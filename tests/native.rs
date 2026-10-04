use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use herdr_agent_grid::{
    app::{Action, key},
    client::Client,
    install::write_config,
    model::*,
    telemetry::{Cursor, rates},
    view::View,
};
use serde_json::json;
use std::{
    fs,
    io::{BufRead, BufReader, Write},
    os::unix::{fs::PermissionsExt, net::UnixListener},
    time::Duration,
};

#[test]
fn tiny_terminals_unicode_and_huge_inventories_stay_in_bounds() {
    let mut state = demo_state();
    for a in &mut state.agents {
        a.title = "評審 e\u{301}\x1b]52;c;secret\x07".repeat(20);
    }
    for (w, h) in [
        (1, 1),
        (2, 2),
        (12, 8),
        (20, 20),
        (80, 24),
        (140, 38),
        (240, 64),
    ] {
        for zoom in [false, true] {
            let mut v = View::new(true);
            v.zoom = zoom;
            for c in v.draw(&state, w, h, now(), 1.0) {
                assert!(c.x + width(&c.text) <= w);
                assert!(c.y < h);
                assert!(!c.text.contains('\x1b'));
            }
        }
    }
    assert_eq!(casefold("Straße Σς"), "strasse σσ");
    let a = state.agents[0].clone();
    state.agents = (0..1000)
        .map(|i| Agent {
            pane_id: format!("p{i}"),
            ..a.clone()
        })
        .collect();
    let mut v = View::new(true);
    v.arrange(&state, 80, 24);
    let mut seen = std::collections::HashSet::new();
    loop {
        seen.extend(v.visible.iter().copied());
        if v.page + 1 == v.page_count {
            break;
        }
        v.move_by(&state, v.geometry.capacity as isize, false);
        v.arrange(&state, 80, 24);
    }
    assert_eq!(seen.len(), 1000);
}
#[test]
fn search_zoom_and_escape_preserve_navigation_contract() {
    let s = demo_state();
    let mut v = View::new(false);
    v.arrange(&s, 140, 38);
    let send = |v: &mut View, k| key(v, &s, KeyEvent::new(k, KeyModifiers::NONE));
    send(&mut v, KeyCode::Char('/'));
    send(&mut v, KeyCode::Char('q'));
    assert_eq!(v.query, "q");
    send(&mut v, KeyCode::Esc);
    assert!(!v.searching);
    assert!(v.query.is_empty());
    send(&mut v, KeyCode::Char('z'));
    assert!(v.zoom);
    assert!(matches!(send(&mut v, KeyCode::Esc), Action::Continue));
    assert!(!v.zoom);
    assert!(matches!(send(&mut v, KeyCode::Esc), Action::Quit));
}
#[test]
fn truncated_claude_tail_partial_append_and_inode_replacement() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("log.jsonl");
    let mut data = "{\"type\":\"ignored\"}\n".repeat(130000);
    data.push_str(&json!({"type":"assistant","message":{"id":"last","model":"claude-opus-5-5","usage":{"input_tokens":5,"output_tokens":2}}}).to_string());
    data.push('\n');
    fs::write(&path, data).unwrap();
    let mut c = Cursor::default();
    c.update(&path, rates()).unwrap();
    assert_eq!(c.metrics.tokens, Some(7));
    assert!(c.metrics.tokens_partial);
    assert!(c.metrics.estimate_partial);
    let mut f = fs::OpenOptions::new().append(true).open(&path).unwrap();
    write!(f, "{{\"type\":\"cost-state\",\"totalCostUSD\":2}}").unwrap();
    c.update(&path, rates()).unwrap();
    assert_eq!(c.metrics.cost, None);
    writeln!(f).unwrap();
    c.update(&path, rates()).unwrap();
    assert_eq!(c.metrics.cost, Some(2.0));
    let next = dir.path().join("new");
    fs::write(&next, "{\"type\":\"ignored\"}\n").unwrap();
    fs::rename(next, &path).unwrap();
    c.update(&path, rates()).unwrap();
    assert_eq!(c.metrics.cost, None);
    assert_eq!(c.metrics.tokens, None);
}
#[test]
fn socket_focus_is_delivered_once_even_when_reply_is_lost() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("herdr.sock");
    let listener = UnixListener::bind(&path).unwrap();
    let server = std::thread::spawn(move || {
        let (stream, _) = listener.accept().unwrap();
        let mut line = String::new();
        BufReader::new(stream).read_line(&mut line).unwrap();
        let r: serde_json::Value = serde_json::from_str(&line).unwrap();
        assert_eq!(r["method"], "agent.focus");
        assert_eq!(r["params"]["target"], "p1");
    });
    let c = Client {
        socket_path: Some(path),
        bin: "must-never-execute".into(),
        timeout: Duration::from_millis(200),
    };
    assert!(c.focus("p1").unwrap_err().contains("closed"));
    server.join().unwrap();
}
#[test]
fn socket_timeout_is_bounded_and_server_errors_surface() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("herdr.sock");
    let listener = UnixListener::bind(&path).unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut line = String::new();
        BufReader::new(stream.try_clone().unwrap())
            .read_line(&mut line)
            .unwrap();
        writeln!(stream, "{{\"error\":{{\"message\":\"synthetic error\"}}}}").unwrap();
    });
    let c = Client {
        socket_path: Some(path),
        bin: "unused".into(),
        timeout: Duration::from_millis(100),
    };
    assert_eq!(c.snapshot().unwrap_err(), "synthetic error");
    server.join().unwrap();
}
#[test]
fn atomic_config_preserves_permissions_and_rejects_concurrent_edit() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("config.toml");
    fs::write(&path, "a=1\n").unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
    write_config(&path, "a=1\n", "a=2\n").unwrap();
    assert_eq!(fs::read_to_string(&path).unwrap(), "a=2\n");
    assert_eq!(
        fs::metadata(&path).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert!(write_config(&path, "a=1\n", "a=3\n").is_err());
    assert_eq!(fs::read_to_string(&path).unwrap(), "a=2\n");
    assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 2);
}

#[test]
fn background_publication_wakes_consumers_and_preserves_snapshots() {
    use herdr_agent_grid::refresh::Refresher;
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("herdr.sock");
    let listener = UnixListener::bind(&path).unwrap();
    let server = std::thread::spawn(move || {
        for status in ["working", "done"] {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = String::new();
            BufReader::new(stream.try_clone().unwrap())
                .read_line(&mut request)
                .unwrap();
            writeln!(stream,"{}",json!({"result":{"snapshot":{"agents":[{"pane_id":"p1","agent":"gemini","agent_status":status}]}}})).unwrap();
        }
    });
    let (tx, rx) = std::sync::mpsc::channel();
    let worker = Refresher::start_with_wakeup(
        Client {
            socket_path: Some(path),
            bin: "unused".into(),
            timeout: Duration::from_secs(1),
        },
        move || {
            let _ = tx.send(());
        },
    );
    rx.recv_timeout(Duration::from_secs(2)).unwrap();
    let first = worker.get();
    assert!(
        !first.agents.is_empty(),
        "initial refresh error: {}",
        first.error
    );
    assert_eq!(first.agents[0].status, "working");
    worker.request(Default::default(), true);
    let deadline = std::time::Instant::now() + Duration::from_secs(3);
    loop {
        assert!(
            std::time::Instant::now() < deadline,
            "{}",
            serde_json::to_string(&*worker.get()).unwrap()
        );
        rx.recv_timeout(Duration::from_secs(2)).unwrap();
        if worker.get().agents[0].status == "done" {
            break;
        }
    }
    assert_eq!(first.agents[0].status, "working");
    drop(worker);
    server.join().unwrap();
}

#[test]
fn unresponsive_socket_has_a_total_deadline() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("herdr.sock");
    let listener = UnixListener::bind(&path).unwrap();
    let (tx, rx) = std::sync::mpsc::channel();
    let server = std::thread::spawn(move || {
        let (stream, _) = listener.accept().unwrap();
        let mut request = String::new();
        BufReader::new(stream.try_clone().unwrap())
            .read_line(&mut request)
            .unwrap();
        let _ = rx.recv_timeout(Duration::from_secs(2));
        drop(stream);
    });
    let c = Client {
        socket_path: Some(path),
        bin: "unused".into(),
        timeout: Duration::from_millis(30),
    };
    let start = std::time::Instant::now();
    assert_eq!(c.snapshot().unwrap_err(), "Herdr request timed out");
    assert!(start.elapsed() < Duration::from_secs(1));
    tx.send(()).unwrap();
    server.join().unwrap();
}
