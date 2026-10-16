use grid_tools::checked;
#[cfg(unix)]
use grid_tools::terminal::Terminal;
use serde_json::{Value, json};
#[cfg(unix)]
use std::time::Duration;
use std::{fs, path::PathBuf, process::Command};
#[cfg(unix)]
const WAIT: Duration = Duration::from_secs(8);
fn app() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_xtask"))
        .parent()
        .unwrap()
        .join(format!("herdr-agent-grid{}", std::env::consts::EXE_SUFFIX))
}
struct Fixture {
    dir: tempfile::TempDir,
}
impl Fixture {
    fn new() -> Self {
        let mut dir = tempfile::Builder::new();
        dir.prefix("grid-test-");
        // Short Unix paths keep socket and PTY names within platform limits.
        #[cfg(unix)]
        let dir = dir.tempdir_in("/tmp").unwrap();
        #[cfg(not(unix))]
        let dir = dir.tempdir().unwrap();
        fs::write(dir.path().join("snapshot.json"),json!({"agents":[{"pane_id":"w1:p1","agent":"claude","name":"first","agent_status":"working"},{"pane_id":"w1:p2","agent":"codex","name":"second","agent_status":"blocked"}]}).to_string()).unwrap();
        Self { dir }
    }
    fn command(&self) -> Command {
        let mut c = Command::new(app());
        c.env("HERDR_ENV", "1")
            .env("HERDR_SOCKET_PATH", "")
            .env("HERDR_BIN_PATH", env!("CARGO_BIN_EXE_xtask"))
            .env("GRID_FIXTURE_ROOT", self.dir.path())
            .env("HERDR_AGENT_GRID_TRACE", "1")
            .env("HERDR_AGENT_GRID_ICONS", "unicode")
            .env("CLAUDE_CONFIG_DIR", self.dir.path().join("claude"))
            .env("HERDR_AGENT_GRID_CLAUDE_DIRS", "");
        c
    }
    #[cfg(unix)]
    fn start(&self) -> Terminal {
        Terminal::spawn(&mut self.command(), 140, 38).unwrap()
    }
    fn calls(&self) -> Vec<Value> {
        fs::read_to_string(self.dir.path().join("calls.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|s| serde_json::from_str(s).unwrap())
            .collect()
    }
    #[cfg(unix)]
    fn children(&self, count: usize) {
        let id = "11111111-1111-1111-1111-111111111111";
        let store = self.dir.path().join("claude/projects/synthetic");
        let children = store.join(id).join("subagents");
        fs::create_dir_all(&children).unwrap();
        fs::write(
            store.join(format!("{id}.jsonl")),
            "{\"type\":\"cost-state\",\"totalCostUSD\":1.23}\n",
        )
        .unwrap();
        for i in 0..count {
            let name = if i == 0 {
                "API tests".into()
            } else if i == 1 {
                "Cost review".into()
            } else if i == 19 {
                "ZZZZ last child".into()
            } else {
                format!("Done{i:02}")
            };
            fs::write(
                children.join(format!("agent-{i}.meta.json")),
                json!({"description":name}).to_string(),
            )
            .unwrap();
            let mut record = json!({"type":"assistant","isSidechain":true,"timestamp":"2026-10-04T10:00:00Z","effort":if i==1{"medium"}else{"high"},"message":{"id":format!("child{i}"),"model":"claude-sonnet-4-6","content":[],"usage":{"input_tokens":1000,"output_tokens":1000}}});
            if i >= 3 {
                record["message"]["stop_reason"] = json!("end_turn")
            };
            fs::write(
                children.join(format!("agent-{i}.jsonl")),
                format!("{record}\n"),
            )
            .unwrap();
        }
        let path = self.dir.path().join("snapshot.json");
        let mut snapshot: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        snapshot["agents"][0]["agent_session"] = json!({"kind":"id","value":id});
        fs::write(path, snapshot.to_string()).unwrap();
    }
}
#[cfg(unix)]
fn finish(t: &mut Terminal) {
    t.wait_for(WAIT, |t| t.child.try_wait().unwrap().is_some())
        .unwrap();
    assert!(t.child.wait().unwrap().success());
    assert!(t.contains("\x1b[?1049l"));
}
#[test]
fn concurrent_mock_calls_keep_complete_log_records() {
    const WORKERS: usize = 32;
    const CALLS: usize = 4;
    let f = Fixture::new();
    let barrier = std::sync::Barrier::new(WORKERS);
    std::thread::scope(|scope| {
        for worker in 0..WORKERS {
            let root = f.dir.path();
            let barrier = &barrier;
            scope.spawn(move || {
                barrier.wait();
                for call in 0..CALLS {
                    checked(
                        Command::new(env!("CARGO_BIN_EXE_xtask"))
                            .env("GRID_FIXTURE_ROOT", root)
                            .args(["agent", "focus", &format!("worker-{worker}-call-{call}")]),
                    )
                    .unwrap();
                }
            });
        }
    });
    let calls = f.calls();
    assert_eq!(calls.len(), WORKERS * CALLS);
    for worker in 0..WORKERS {
        for call in 0..CALLS {
            assert!(calls.contains(&json!([
                "agent",
                "focus",
                format!("worker-{worker}-call-{call}")
            ])));
        }
    }
}
#[cfg(unix)]
#[test]
fn keyboard_focus_and_clean_terminal_exit() {
    let f = Fixture::new();
    let mut t = f.start();
    t.wait_for(WAIT, |t| t.contains("Bash")).unwrap();
    assert!(!t.contains("CURRENT PROMPT"));
    assert!(t.contains("first") && t.contains("second"));
    t.send(b"\t\r").unwrap();
    finish(&mut t);
    assert!(f.calls().contains(&json!(["agent", "focus", "w1:p2"])));
}
#[cfg(unix)]
#[test]
fn mouse_focus_and_escape_do_not_send_agent_input() {
    for mouse in [true, false] {
        let f = Fixture::new();
        let mut t = f.start();
        t.wait_for(WAIT, |t| t.contains("Bash")).unwrap();
        t.send(if mouse {
            b"\x1b[<0;4;8M\x1b[<0;4;8m"
        } else {
            b"\x1b"
        })
        .unwrap();
        finish(&mut t);
        let calls = f.calls();
        let focus: Vec<_> = calls.iter().filter(|c| c[0] == "agent").collect();
        assert_eq!(focus.len(), usize::from(mouse));
        if mouse {
            assert_eq!(focus[0], &json!(["agent", "focus", "w1:p1"]));
        }
        assert!(
            calls
                .iter()
                .all(|c| ["api", "pane", "agent"].contains(&c[0].as_str().unwrap()))
        );
    }
}
#[cfg(unix)]
#[test]
fn details_scroll_reaches_every_child() {
    let f = Fixture::new();
    f.children(20);
    let mut t = f.start();
    t.wait_for(WAIT, |t| t.contains("3 working / 20 total"))
        .unwrap();
    assert!(!t.contains("ZZZZ"));
    t.send(b"z").unwrap();
    t.wait_for(WAIT, |t| t.contains("PgUp/PgDn scroll"))
        .unwrap();
    assert!(t.contains("Cost review"));
    t.send(b"\x1b[6~").unwrap();
    t.wait_for(WAIT, |t| t.contains("ZZZZ")).unwrap();
    t.resize(80, 24).unwrap();
    t.send(b"\x1b[5~").unwrap();
    t.send(b"qq").unwrap();
    finish(&mut t);
    assert!(!f.calls().iter().any(|c| c[0] == "agent"));
}
#[cfg(unix)]
#[test]
fn fast_preview_and_keyboard_do_not_wait_for_slow_pane() {
    let f = Fixture::new();
    fs::write(f.dir.path().join("slow"), "").unwrap();
    let mut t = f.start();
    t.wait_for(WAIT, |t| t.contains("Bash")).unwrap();
    assert!(!f.dir.path().join("slow-finished").exists());
    t.send(b"q").unwrap();
    finish(&mut t);
    assert!(!f.dir.path().join("slow-finished").exists());
}
#[cfg(unix)]
#[test]
fn startup_failure_remains_visible_until_dismissed() {
    let f = Fixture::new();
    let mut t = Terminal::spawn(f.command().env("HERDR_ENV", "0"), 140, 38).unwrap();
    t.wait_for(WAIT, |t| t.contains("Press Enter to close"))
        .unwrap();
    assert!(t.child.try_wait().unwrap().is_none());
    assert!(t.contains("run from a Herdr pane"));
    t.send(b"\n").unwrap();
    t.wait_for(WAIT, |t| t.child.try_wait().unwrap().is_some())
        .unwrap();
    assert_eq!(t.child.wait().unwrap().code(), Some(2));
}
#[test]
fn installer_links_backs_up_reloads_and_preserves_symlinks() {
    let f = Fixture::new();
    let actual = f.dir.path().join("actual.toml");
    let before = "[keys]\nnext_tab=\"ctrl+tab\"\n";
    fs::write(&actual, before).unwrap();
    #[cfg(unix)]
    let link = {
        let link = f.dir.path().join("config.toml");
        std::os::unix::fs::symlink(&actual, &link).unwrap();
        link
    };
    // Windows symlinks need Developer Mode or elevation; install into the file directly.
    #[cfg(not(unix))]
    let link = actual.clone();
    let install = || {
        checked(
            f.command()
                .args(["install", "--config"])
                .arg(&link)
                .arg("--open"),
        )
        .unwrap()
    };
    install();
    let after = fs::read_to_string(&actual).unwrap();
    install();
    #[cfg(unix)]
    assert!(fs::symlink_metadata(&link).unwrap().is_symlink());
    assert_eq!(fs::read_to_string(actual).unwrap(), after);
    let backups: Vec<_> = fs::read_dir(f.dir.path())
        .unwrap()
        .flatten()
        .filter(|e| e.file_name().to_string_lossy().contains("bak-grid"))
        .collect();
    assert_eq!(backups.len(), 1);
    assert_eq!(fs::read_to_string(backups[0].path()).unwrap(), before);
    let calls = f.calls();
    let linked = calls
        .iter()
        .find(|c| c[0] == "plugin" && c[1] == "link")
        .unwrap();
    // Herdr receives a plain path, never a Windows verbatim (\\?\) path.
    let root = PathBuf::from(linked[2].as_str().unwrap());
    assert!(root.join("herdr-plugin.toml").is_file());
    assert!(!linked[2].as_str().unwrap().starts_with(r"\\?\"));
    assert!(calls.contains(&json!(["server", "reload-config"])));
    assert!(calls.contains(&json!([
        "plugin",
        "action",
        "invoke",
        "herdr-agent-grid.open"
    ])));
}
#[test]
fn cli_json_render_dimensions_and_errors() {
    let f = Fixture::new();
    let out = checked(f.command().args(["--demo", "--list"])).unwrap();
    let agents: Vec<Value> = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(agents.len(), 6);
    let out = checked(
        f.command()
            .args(["--demo", "--render", "--width", "80", "--height", "24"]),
    )
    .unwrap();
    assert_eq!(String::from_utf8(out.stdout).unwrap().lines().count(), 24);
    for args in [["--demo", "--width", "0"], ["--demo", "--icons", "bad"]] {
        assert!(!f.command().args(args).output().unwrap().status.success());
    }
}

#[cfg(unix)]
#[test]
fn completed_filter_button_keeps_dashboard_open() {
    let f = Fixture::new();
    let mut t = f.start();
    t.wait_for(WAIT, |t| t.contains("Hide completed/stale"))
        .unwrap();
    t.output.clear();
    t.send(b"\x1b[<0;3;5M\x1b[<0;3;5m").unwrap();
    t.wait_for(WAIT, |t| t.contains("Show")).unwrap();
    assert!(t.child.try_wait().unwrap().is_none());
    t.output.clear();
    t.send(b"d").unwrap();
    t.wait_for(WAIT, |t| t.contains("Hide")).unwrap();
    t.send(b"q").unwrap();
    finish(&mut t);
    assert!(!f.calls().iter().any(|c| c[0] == "agent"));
}

#[test]
fn cli_transport_lists_agents_and_reports_doctor() {
    let f = Fixture::new();
    let mut list = f.command();
    list.arg("--list");
    // Windows exposes a named pipe that the grid leaves to the Herdr CLI.
    if cfg!(windows) {
        list.env("HERDR_SOCKET_PATH", r"\\.\pipe\herdr-grid-missing");
    }
    let agents: Vec<Value> = serde_json::from_slice(&checked(&mut list).unwrap().stdout).unwrap();
    assert_eq!(agents.len(), 2);
    let doctor: Value =
        serde_json::from_slice(&checked(f.command().arg("--doctor")).unwrap().stdout).unwrap();
    assert_eq!(doctor["agents"], 2);
    assert_eq!(doctor["transport"], "cli");
    assert_eq!(
        f.calls().iter().filter(|c| c[0] == "api").count(),
        2,
        "{:?}",
        f.calls()
    );
}

#[test]
fn open_action_asks_herdr_for_the_grid_popup() {
    let f = Fixture::new();
    checked(f.command().arg("open")).unwrap();
    assert_eq!(
        f.calls(),
        [json!([
            "plugin",
            "pane",
            "open",
            "--plugin",
            "herdr-agent-grid",
            "--entrypoint",
            "grid",
            "--focus"
        ])]
    );
    assert!(
        !f.command()
            .args(["open", "extra"])
            .status()
            .unwrap()
            .success()
    );
}
