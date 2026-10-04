use crossterm::{
    cursor::{Hide, Show},
    event::{self, Event, KeyCode, KeyEventKind},
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use herdr_grid_bench::{
    model::*,
    telemetry::Cursor,
    view::{Draw, View},
};
use ratatui::{
    Terminal,
    backend::CrosstermBackend,
    style::{Color, Modifier, Style},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

fn cpu_ns() -> u64 {
    let mut t = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    unsafe {
        assert_eq!(
            libc::clock_gettime(libc::CLOCK_PROCESS_CPUTIME_ID, &mut t),
            0
        );
    }
    t.tv_sec as u64 * 1_000_000_000 + t.tv_nsec as u64
}
fn load(path: &Path) -> Fixture {
    serde_json::from_slice(&fs::read(path).expect("fixture read")).expect("fixture decode")
}
fn option(args: &[String], name: &str) -> Option<String> {
    args.windows(2).find(|p| p[0] == name).map(|p| p[1].clone())
}
fn usize_arg(args: &[String], name: &str, default: usize) -> usize {
    option(args, name)
        .map(|v| v.parse().expect("integer option"))
        .unwrap_or(default)
}
fn flag(args: &[String], name: &str) -> bool {
    args.iter().any(|a| a == name)
}

#[derive(Deserialize)]
#[serde(default)]
struct Case {
    width: usize,
    height: usize,
    selected: String,
    query: String,
    zoom: bool,
    searching: bool,
    tick: f64,
    motion: bool,
    child_offset: usize,
}
impl Default for Case {
    fn default() -> Self {
        Self {
            width: 140,
            height: 38,
            selected: String::new(),
            query: String::new(),
            zoom: false,
            searching: false,
            tick: 1.3,
            motion: true,
            child_offset: 0,
        }
    }
}
fn frames(f: &Fixture, path: &Path) {
    let cases: Vec<Case> = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
    let result: Vec<_> = cases
        .into_iter()
        .map(|c| {
            let mut v = View::new(c.motion);
            v.selected = c.selected;
            v.query = c.query;
            v.zoom = c.zoom;
            v.searching = c.searching;
            if !v.selected.is_empty() {
                v.child_offsets.insert(v.selected.clone(), c.child_offset);
            }
            v.draw(&f.state, c.width, c.height, f.now, c.tick)
        })
        .collect();
    println!("{}", serde_json::to_string(&result).unwrap());
}
fn measure(mut function: impl FnMut(), batch: usize, samples: usize) -> Vec<f64> {
    for _ in 0..3 {
        function();
    }
    (0..samples)
        .map(|_| {
            let start = cpu_ns();
            for _ in 0..batch {
                function();
            }
            (cpu_ns() - start) as f64 / 1_000_000.0 / batch as f64
        })
        .collect()
}
fn micro(f: &Fixture, args: &[String]) {
    let op = option(args, "--operation").unwrap_or("render".into());
    let batch = usize_arg(args, "--batch", 100);
    let samples = usize_arg(args, "--samples", 10);
    let path = option(args, "--transcript").map(PathBuf::from);
    let mut checksum = 0u64;
    let values = match op.as_str() {
        "render" | "navigate" | "filter" => {
            let mut v = View::new(true);
            v.arrange(&f.state, 140, 38);
            let mut tick = 0;
            measure(
                || {
                    if op == "navigate" {
                        v.move_by(&f.state, 1, true);
                        v.arrange(&f.state, 140, 38);
                    }
                    if op == "filter" {
                        v.query = if v.query.is_empty() {
                            "grid".into()
                        } else {
                            String::new()
                        };
                    }
                    tick += 1;
                    let commands = v.draw(&f.state, 140, 38, f.now, tick as f64 / 10.0);
                    checksum += std::hint::black_box(commands.len()) as u64;
                },
                batch,
                samples,
            )
        }
        "cold_transcript" => measure(
            || {
                let mut c = Cursor::default();
                c.update(path.as_ref().unwrap(), &f.rates).unwrap();
                checksum += std::hint::black_box(c.metrics.tokens.unwrap_or(0));
            },
            batch,
            samples,
        ),
        "unchanged_poll" => {
            let mut c = Cursor::default();
            c.update(path.as_ref().unwrap(), &f.rates).unwrap();
            measure(
                || {
                    c.update(path.as_ref().unwrap(), &f.rates).unwrap();
                    checksum += std::hint::black_box(c.metrics.tokens.unwrap_or(0));
                },
                batch,
                samples,
            )
        }
        "append_message" => {
            let mut c = Cursor::default();
            let path = path.as_ref().unwrap();
            c.update(path, &f.rates).unwrap();
            let mut writer = fs::OpenOptions::new().append(true).open(path).unwrap();
            let mut values = vec![];
            let records = fs::read_to_string(
                option(args, "--append-records").expect("--append-records required"),
            )
            .unwrap();
            let records: Vec<_> = records.split_inclusive('\n').collect();
            assert!(records.len() >= samples * batch);
            for record in records.iter().take(samples * batch) {
                write!(writer, "{record}").unwrap();
                writer.flush().unwrap();
                let start = cpu_ns();
                c.update(path, &f.rates).unwrap();
                values.push((cpu_ns() - start) as f64 / 1_000_000.0);
                checksum += std::hint::black_box(c.metrics.tokens.unwrap_or(0));
            }
            values
        }
        _ => panic!("unknown operation"),
    };
    println!("{}", json!({"samples_ms":values,"checksum":checksum}));
}
fn parse_sequence(f: &Fixture, args: &[String]) {
    let operations: Vec<Value> =
        serde_json::from_slice(&fs::read(option(args, "--cases").unwrap()).unwrap()).unwrap();
    let path = PathBuf::from(option(args, "--transcript").unwrap());
    let mut c = Cursor::default();
    let mut results = vec![];
    for op in operations {
        if let Some(text) = op["write"].as_str() {
            fs::write(&path, text).unwrap();
        }
        if let Some(text) = op["append"].as_str() {
            write!(
                fs::OpenOptions::new().append(true).open(&path).unwrap(),
                "{text}"
            )
            .unwrap();
        }
        c.update(&path, &f.rates).unwrap();
        results.push(json!({"metrics":c.metrics,"offset":c.offset}));
    }
    println!("{}", json!(results));
}
fn styles(f: &Fixture) -> HashMap<String, Style> {
    f.styles
        .iter()
        .map(|(name, s)| {
            let mut style = Style::default();
            if let Some(c) = s.color {
                style = style.fg(Color::Indexed(c));
            }
            if s.bold {
                style = style.add_modifier(Modifier::BOLD);
            }
            if s.reverse {
                style = style.add_modifier(Modifier::REVERSED);
            }
            (name.clone(), style)
        })
        .collect()
}
fn trace(input: u64, view: &View, state: &State) {
    print!(
        "\x1b]777;{}\x07",
        json!({"input":input,"selected":view.selected,"query":view.query,"zoom":view.zoom,"revision":state.revision})
    );
    io::stdout().flush().unwrap();
}
fn paint(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    commands: &[Draw],
    styles: &HashMap<String, Style>,
) -> io::Result<()> {
    terminal.draw(|frame| {
        let buf = frame.buffer_mut();
        for c in commands {
            buf.set_string(
                c.x as u16,
                c.y as u16,
                &c.text,
                *styles.get(&c.style).unwrap_or(&Style::default()),
            );
        }
    })?;
    Ok(())
}
struct Restore;
impl Drop for Restore {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        let _ = execute!(io::stdout(), Show, LeaveAlternateScreen);
    }
}
fn pty(f: Fixture, args: &[String]) -> io::Result<()> {
    let motion = !flag(args, "--no-motion");
    let fixed = f.now;
    let styles = styles(&f);
    let shared = Arc::new(Mutex::new(f.state));
    let stop = Arc::new(AtomicBool::new(false));
    let replay_ms = usize_arg(args, "--replay-ms", 100) as u64;
    let worker = option(args, "--background-transcript").map(|p| {
        let state = shared.clone();
        let stop = stop.clone();
        let rates = f.rates.clone();
        thread::spawn(move || {
            let path = PathBuf::from(p);
            while !stop.load(Ordering::Relaxed) {
                let start = Instant::now();
                let mut c = Cursor::default();
                c.update(&path, &rates).unwrap();
                {
                    let mut s = state.lock().unwrap();
                    let id = s.agents.first().unwrap().pane_id.clone();
                    s.metrics.insert(id, c.metrics);
                    s.revision += 1;
                }
                while start.elapsed() < Duration::from_millis(replay_ms)
                    && !stop.load(Ordering::Relaxed)
                {
                    thread::sleep(Duration::from_millis(2));
                }
            }
        })
    });
    enable_raw_mode()?;
    execute!(io::stdout(), EnterAlternateScreen, Hide)?;
    let _restore = Restore;
    let mut terminal = Terminal::new(CrosstermBackend::new(io::stdout()))?;
    let start = Instant::now();
    let mut view = View::new(motion);
    let mut input = 0u64;
    let mut dirty = true;
    let mut state = shared.lock().unwrap().clone();
    let mut previous_tick = u64::MAX;
    loop {
        {
            let s = shared.lock().unwrap();
            if s.revision != state.revision {
                state = s.clone();
                dirty = true;
            }
        }
        let size = terminal.size()?;
        let (w, h) = (size.width as usize, size.height as usize);
        view.arrange(&state, w, h);
        let animating = view.animating(&state);
        let timeout = if animating { 100 } else { 250 };
        let tick = (start.elapsed().as_secs_f64() * if animating { 10.0 } else { 1.0 }) as u64;
        if dirty || tick != previous_tick {
            let commands = view.draw(&state, w, h, fixed, start.elapsed().as_secs_f64());
            paint(&mut terminal, &commands, &styles)?;
            trace(input, &view, &state);
            dirty = false;
            previous_tick = tick;
        }
        if !event::poll(Duration::from_millis(timeout))? {
            continue;
        }
        match event::read()? {
            Event::Resize(_, _) => dirty = true,
            Event::Key(key) if key.kind == KeyEventKind::Press => {
                input += 1;
                dirty = true;
                if view.searching {
                    match key.code {
                        KeyCode::Esc => {
                            view.searching = false;
                            view.query.clear();
                        }
                        KeyCode::Enter => view.searching = false,
                        KeyCode::Backspace => {
                            view.query.pop();
                        }
                        KeyCode::Char(c) if view.query.chars().count() < 200 => {
                            view.query.push(c);
                        }
                        _ => {}
                    }
                    continue;
                }
                match key.code {
                    KeyCode::Char('q') | KeyCode::Esc => {
                        if view.zoom {
                            view.zoom = false;
                        } else if !view.query.is_empty() {
                            view.query.clear();
                        } else {
                            break;
                        }
                    }
                    KeyCode::Char('/') => view.searching = true,
                    KeyCode::Char('z') | KeyCode::Enter => view.zoom = !view.zoom,
                    KeyCode::Right | KeyCode::Char('l') => view.move_by(&state, 1, false),
                    KeyCode::Tab => view.move_by(&state, 1, true),
                    KeyCode::Left | KeyCode::Char('h') => view.move_by(&state, -1, false),
                    KeyCode::BackTab => view.move_by(&state, -1, true),
                    KeyCode::Down | KeyCode::Char('j') => view.move_by(
                        &state,
                        if view.zoom {
                            1
                        } else {
                            view.geometry.columns as isize
                        },
                        false,
                    ),
                    KeyCode::Up | KeyCode::Char('k') => view.move_by(
                        &state,
                        if view.zoom {
                            -1
                        } else {
                            -(view.geometry.columns as isize)
                        },
                        false,
                    ),
                    KeyCode::PageDown | KeyCode::Char(']') => {
                        if !view.scroll_children(view.child_capacity as isize) {
                            view.move_by(&state, view.geometry.capacity as isize, false);
                        }
                    }
                    KeyCode::PageUp | KeyCode::Char('[') => {
                        if !view.scroll_children(-(view.child_capacity as isize)) {
                            view.move_by(&state, -(view.geometry.capacity as isize), false);
                        }
                    }
                    KeyCode::Home => view.move_by(&state, -(view.items.len() as isize), false),
                    KeyCode::End => view.move_by(&state, view.items.len() as isize, false),
                    _ => {}
                }
            }
            _ => {}
        }
    }
    stop.store(true, Ordering::Relaxed);
    if let Some(worker) = worker {
        worker.join().unwrap();
    }
    Ok(())
}
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let f = load(Path::new(
        &option(&args, "--fixture").expect("--fixture required"),
    ));
    assert_eq!(f.schema, 1);
    match option(&args, "--mode").as_deref().unwrap_or("pty") {
        "frames" => frames(
            &f,
            Path::new(&option(&args, "--cases").expect("--cases required")),
        ),
        "micro" => micro(&f, &args),
        "parse-sequence" => parse_sequence(&f, &args),
        "pty" => pty(f, &args).expect("terminal session"),
        _ => panic!("unknown mode"),
    }
}
