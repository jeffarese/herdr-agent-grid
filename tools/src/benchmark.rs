use grid_tools::{Result, checked, root, terminal::Terminal};
use herdr_agent_grid::{
    model::*,
    telemetry::{Cursor, rates},
    view::View,
};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    io::{BufRead, BufReader, Write},
    os::unix::net::UnixListener,
    process::Command,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
fn stats(mut values: Vec<f64>) -> Value {
    values.sort_by(f64::total_cmp);
    let n = values.len();
    json!({"median":if n.is_multiple_of(2){(values[n/2-1]+values[n/2])/2.0}else{values[n/2]},"p95":values[((n as f64*0.95).ceil() as usize-1).min(n-1)],"min":values[0],"max":values[n-1],"samples":values})
}
fn measure(mut f: impl FnMut(), batch: usize, rounds: usize) -> Value {
    for _ in 0..3 {
        f()
    }
    stats(
        (0..rounds)
            .map(|_| {
                let start = Instant::now();
                for _ in 0..batch {
                    f()
                }
                start.elapsed().as_secs_f64() * 1000.0 / batch as f64
            })
            .collect(),
    )
}
struct Server {
    stop: Arc<AtomicBool>,
    handle: Option<std::thread::JoinHandle<()>>,
}
impl Server {
    fn start(path: &std::path::Path, snapshot: Value) -> Result<Self> {
        let listener = UnixListener::bind(path)?;
        listener.set_nonblocking(true)?;
        let stop = Arc::new(AtomicBool::new(false));
        let done = stop.clone();
        let handle = std::thread::spawn(move || {
            while !done.load(Ordering::Relaxed) {
                match listener.accept() {
                    Ok((mut socket, _)) => {
                        socket
                            .set_read_timeout(Some(Duration::from_secs(1)))
                            .unwrap();
                        let mut line = String::new();
                        if BufReader::new(socket.try_clone().unwrap())
                            .read_line(&mut line)
                            .is_err()
                        {
                            continue;
                        }
                        let Ok(request) = serde_json::from_str::<Value>(&line) else {
                            continue;
                        };
                        let result = match request["method"].as_str() {
                            Some("session.snapshot") => json!({"snapshot":snapshot}),
                            Some("pane.read") => json!({"read":{"text":"● Bash(synthetic check)"}}),
                            _ => json!({"error":"read-only fixture"}),
                        };
                        let _ = writeln!(socket, "{}", json!({"result":result}));
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(1))
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(Self {
            stop,
            handle: Some(handle),
        })
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

fn live(binary: &std::path::Path, dir: &std::path::Path, rounds: usize) -> Result<Value> {
    let store = dir.join("claude/projects/synthetic");
    fs::create_dir_all(&store)?;
    let mut agents = vec![];
    for i in 0..6 {
        let id = format!("00000000-0000-0000-0000-{i:012}");
        let mut log = fs::File::create(store.join(format!("{id}.jsonl")))?;
        for n in 0..4000 {
            writeln!(
                log,
                "{}",
                json!({"type":"assistant","timestamp":"2026-10-04T10:00:00Z","message":{"id":format!("m{n}"),"model":"claude-opus-5-5","usage":{"input_tokens":1000,"output_tokens":100},"content":[{"type":"tool_use","id":format!("t{n}"),"name":"Read"}]}})
            )?;
        }
        let children = store.join(&id).join("subagents");
        fs::create_dir_all(&children)?;
        for n in 0..3 {
            fs::write(
                children.join(format!("agent-{n}.jsonl")),
                format!(
                    "{}\n",
                    json!({"type":"assistant","isSidechain":true,"effort":"high","message":{"id":"child","model":"claude-opus-5-5","usage":{"input_tokens":1000,"output_tokens":100}}})
                ),
            )?;
        }
        agents.push(json!({"pane_id":format!("p{i:04}"),"name":format!("worker{i}"),"agent":"claude","agent_status":"working","agent_session":{"kind":"id","value":id}}));
    }
    let mut all = serde_json::Map::new();
    for (scenario, settled, motion) in [
        ("active", false, true),
        ("settled", true, true),
        ("reduced_motion", false, false),
    ] {
        for a in &mut agents {
            a["agent_status"] = json!(if settled { "done" } else { "working" });
        }
        let socket = dir.join(format!("{scenario}.sock"));
        let _server = Server::start(&socket, json!({"agents":agents}))?;
        let mut first = vec![];
        let mut ready = vec![];
        let mut latency = vec![];
        let mut bandwidth = vec![];
        for _ in 0..rounds {
            let mut cmd = Command::new(binary);
            cmd.env("HERDR_ENV", "1")
                .env("HERDR_SOCKET_PATH", &socket)
                .env("CLAUDE_CONFIG_DIR", dir.join("claude"))
                .env("HERDR_AGENT_GRID_CLAUDE_DIRS", "")
                .env("HERDR_AGENT_GRID_TRACE", "1")
                .env("HERDR_AGENT_GRID_ICONS", "unicode")
                .env("HERDR_AGENT_GRID_MOTION", if motion { "on" } else { "off" });
            let start = Instant::now();
            let mut term = Terminal::spawn(&mut cmd, 140, 38)?;
            term.wait_for(Duration::from_secs(10), |t| !t.traces().is_empty())?;
            first.push(start.elapsed().as_secs_f64() * 1000.0);
            term.wait_for(Duration::from_secs(10), |t| {
                t.traces().last().is_some_and(|v| v["ready"] == true)
            })?;
            ready.push(start.elapsed().as_secs_f64() * 1000.0);
            for (index, key) in [
                b"\t".as_slice(),
                b"z",
                b"z",
                b"/",
                b"w",
                b"\x1b",
                b"\x1b[C",
                b"\x1b[D",
            ]
            .into_iter()
            .enumerate()
            {
                let stamp = Instant::now();
                term.send(key)?;
                term.wait_for(Duration::from_secs(2), |t| {
                    t.traces()
                        .last()
                        .is_some_and(|v| v["input"].as_u64().unwrap_or(0) > index as u64)
                })?;
                latency.push(stamp.elapsed().as_secs_f64() * 1000.0);
                let trace = term.traces().pop().unwrap();
                if index == 1 && trace["zoom"] != true {
                    return Err("Details key did not open details".into());
                }
                if index == 4 && trace["query"] != "w" {
                    return Err("Filter input was lost".into());
                }
            }
            let hold = Instant::now();
            while hold.elapsed() < Duration::from_millis(500) {
                term.read(Duration::from_millis(20))?;
            }
            term.send(b"q")?;
            term.wait_for(Duration::from_secs(3), |t| {
                t.child.try_wait().unwrap().is_some()
            })?;
            if !term.child.wait()?.success() {
                return Err("Benchmark application failed".into());
            }
            bandwidth.push(term.output.len() as f64 / start.elapsed().as_secs_f64());
        }
        all.insert(scenario.into(),json!({"first_frame_ms":stats(first),"metrics_ready_ms":stats(ready),"key_to_paint_ms":stats(latency),"terminal_bytes_per_second":stats(bandwidth)}));
    }
    Ok(Value::Object(all))
}
pub fn run(args: &[String]) -> Result<()> {
    let mut binary = root().join("target/release/herdr-agent-grid");
    let mut output = root().join("benchmarks/results.json");
    let mut rounds = 5;
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--binary" => binary = it.next().ok_or("Missing binary")?.into(),
            "--output" => output = it.next().ok_or("Missing output")?.into(),
            "--rounds" => rounds = it.next().ok_or("Missing rounds")?.parse()?,
            _ => return Err(format!("Unknown bench argument: {arg}").into()),
        }
    }
    if rounds == 0 || rounds > 100 {
        return Err("Rounds must be between 1 and 100".into());
    }
    if cfg!(debug_assertions) {
        return Err("Benchmark tooling must be built with --release (see --help)".into());
    }
    let mut micro = BTreeMap::new();
    for count in [6, 60, 1000] {
        let base = demo_state();
        let mut state = base.clone();
        state.agents.clear();
        state.metrics.clear();
        for i in 0..count {
            let mut a = base.agents[i % 6].clone();
            let m = base.metrics[&a.pane_id].clone();
            a.pane_id = format!("p{i:04}");
            state.metrics.insert(a.pane_id.clone(), m);
            state.agents.push(a);
        }
        let mut view = View::new(true);
        view.icons = "unicode".into();
        let mut tick = 0.0;
        micro.insert(
            format!("render_{count}_ms"),
            measure(
                || {
                    tick += 0.1;
                    std::hint::black_box(view.draw(&state, 140, 38, 1800000000.0, tick));
                },
                100,
                rounds,
            ),
        );
        micro.insert(
            format!("navigate_{count}_ms"),
            measure(
                || {
                    view.move_by(&state, 1, true);
                    std::hint::black_box(view.draw(&state, 140, 38, 1800000000.0, tick));
                },
                100,
                rounds,
            ),
        );
    }
    let dir = tempfile::Builder::new()
        .prefix("grid-bench-")
        .tempdir_in("/tmp")?;
    let transcript = dir.path().join("transcript.jsonl");
    let mut file = fs::File::create(&transcript)?;
    for i in 0..4000 {
        writeln!(
            file,
            "{}",
            json!({"type":"assistant","message":{"id":format!("m{i}"),"model":"claude-opus-5-5","usage":{"input_tokens":1000,"output_tokens":100}}})
        )?;
    }
    micro.insert(
        "cold_transcript_ms".into(),
        measure(
            || {
                let mut cursor = Cursor::default();
                cursor.update(&transcript, rates()).unwrap();
                std::hint::black_box(cursor.metrics);
            },
            10,
            rounds,
        ),
    );
    let mut cursor = Cursor::default();
    cursor.update(&transcript, rates())?;
    micro.insert(
        "unchanged_transcript_ms".into(),
        measure(
            || {
                cursor.update(&transcript, rates()).unwrap();
                std::hint::black_box(&cursor.metrics);
            },
            100,
            rounds,
        ),
    );
    let scenarios = live(&binary, dir.path(), rounds)?;
    let mut sources = BTreeMap::new();
    for folder in ["src", "tools/src", "assets"] {
        for e in walkdir::WalkDir::new(root().join(folder))
            .into_iter()
            .filter_map(std::result::Result::ok)
            .filter(|e| e.file_type().is_file())
        {
            sources.insert(
                e.path().strip_prefix(root())?.display().to_string(),
                crate::package::hash(&fs::read(e.path())?),
            );
        }
    }
    sources.insert(
        "Cargo.lock".into(),
        crate::package::hash(&fs::read(root().join("Cargo.lock"))?),
    );
    let rustc = std::env::var_os("RUSTC").unwrap_or("rustc".into());
    let result = json!({"schema":1,"synthetic":true,"version":herdr_agent_grid::VERSION,"platform":format!("{}-{}",std::env::consts::OS,std::env::consts::ARCH),"rustc":String::from_utf8_lossy(&checked(Command::new(rustc).arg("--version"))?.stdout).trim(),"rounds":rounds,"terminal":[140,38],"binary_sha256":crate::package::hash(&fs::read(binary)?),"sources":sources,"micro":micro,"scenarios":scenarios,"scope":"Wall-clock microbenchmarks and fresh-process PTY timings. Includes polling and scheduling overhead. Six synthetic Claude sessions, 4000 records each, 18 children. No live account data. Results are machine-specific; no cross-language comparison or CPU/RSS claims."});
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&output, serde_json::to_string_pretty(&result)? + "\n")?;
    let mut report = format!(
        "# Performance measurements\n\nSynthetic workloads on {} with {}. {} rounds; optimized builds.\n\n| Scenario | First frame median | Metrics ready median | Key-to-paint p95 |\n| --- | ---: | ---: | ---: |\n",
        result["platform"].as_str().unwrap(),
        result["rustc"].as_str().unwrap(),
        rounds
    );
    for (name, data) in result["scenarios"].as_object().unwrap() {
        report += &format!(
            "| {name} | {:.2} ms | {:.2} ms | {:.2} ms |\n",
            data["first_frame_ms"]["median"].as_f64().unwrap(),
            data["metrics_ready_ms"]["median"].as_f64().unwrap(),
            data["key_to_paint_ms"]["p95"].as_f64().unwrap()
        );
    }
    report += &format!(
        "\n{}\n\n[Raw samples]({}) · [Methodology](README.md)\n",
        result["scope"].as_str().unwrap(),
        output.file_name().unwrap().to_string_lossy()
    );
    fs::write(output.with_extension("md"), report)?;
    println!("{}", output.display());
    Ok(())
}
