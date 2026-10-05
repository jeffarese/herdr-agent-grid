use herdr_agent_grid::{model::Agent, sessions::Telemetry};
use serde_json::{Value, json};
use std::{collections::HashMap, fs, io::Write, os::unix::fs::symlink, path::Path};
fn write(path: &Path, records: &[Value], append: bool) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    let mut file = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(!append)
        .append(append)
        .open(path)
        .unwrap();
    for r in records {
        writeln!(file, "{r}").unwrap();
    }
}
fn message(id: &str) -> Value {
    json!({"type":"assistant","isSidechain":true,"timestamp":"2026-10-04T10:00:00Z","effort":"high","message":{"id":id,"model":"claude-opus-5-5","content":[],"usage":{"input_tokens":1000,"output_tokens":100}}})
}
#[test]
fn exact_parent_discovery_children_completion_resume_and_rotation() {
    for provider in ["claude", "codex"] {
        let dir = tempfile::tempdir().unwrap();
        let store = dir.path().join("store");
        let id = "11111111-1111-1111-1111-111111111111";
        let path = store.join(format!("{id}.jsonl"));
        let child;
        if provider == "claude" {
            write(
                &path,
                &[json!({"type":"cost-state","totalCostUSD":1.25})],
                false,
            );
            child = path.with_extension("").join("subagents/agent-child.jsonl");
            write(&child, &[message("m1")], false);
            fs::write(
                child.with_extension("meta.json"),
                "{\"description\":\"Synthetic reviewer\"}",
            )
            .unwrap();
            let outside = dir.path().join("outside.jsonl");
            write(&outside, &[message("outside")], false);
            symlink(outside, child.parent().unwrap().join("agent-escape.jsonl")).unwrap();
        } else {
            write(
                &path,
                &[json!({"type":"session_meta","payload":{"id":id}})],
                false,
            );
            child = store.join("child.jsonl");
            write(
                &child,
                &[
                    json!({"type":"session_meta","payload":{"id":"child","parent_thread_id":id,"agent_nickname":"Synthetic reviewer"}}),
                    json!({"type":"turn_context","payload":{"model":"gpt-6.1-sol","effort":"high"}}),
                    json!({"type":"token_usage_record","payload":{"thread_token_usage":{"total_tokens":1100,"input_tokens":1000,"output_tokens":100}}}),
                ],
                false,
            );
            write(
                &store.join("unrelated.jsonl"),
                &[
                    json!({"type":"session_meta","payload":{"id":"unrelated","parent_thread_id":"other"}}),
                ],
                false,
            );
            write(
                &store.join("guardian.jsonl"),
                &[
                    json!({"type":"session_meta","payload":{"id":"guardian","parent_thread_id":id,"source":{"subagent":{"other":"guardian"}}}}),
                ],
                false,
            );
        }
        let mut telemetry = Telemetry::with_roots(HashMap::from([(provider.into(), vec![store])]));
        let a = Agent {
            pane_id: "p1".into(),
            kind: provider.into(),
            status: "working".into(),
            session_kind: "id".into(),
            session_ref: id.into(),
            ..Default::default()
        };
        let first = telemetry.read(&a);
        assert_eq!(first.subagents.len(), 1);
        assert!(first.subagents[0].estimated_cost.is_some());
        let finish = if provider == "claude" {
            let mut m = message("m2");
            m["timestamp"] = json!("2026-10-04T10:02:00Z");
            m["message"]["stop_reason"] = json!("end_turn");
            m
        } else {
            json!({"type":"event_msg","timestamp":"2026-10-04T10:02:00Z","payload":{"type":"task_complete"}})
        };
        write(&child, &[finish], true);
        assert_eq!(telemetry.read(&a).subagents[0].status, "done");
        let resume = if provider == "claude" {
            json!({"type":"user","timestamp":"2026-10-04T10:03:00Z","message":{"content":"Synthetic resume"}})
        } else {
            json!({"type":"event_msg","timestamp":"2026-10-04T10:03:00Z","payload":{"type":"task_started"}})
        };
        write(&child, &[resume], true);
        assert_eq!(telemetry.read(&a).subagents[0].status, "working");
        assert_eq!(
            first.subagents[0].status,
            if provider == "claude" {
                "working"
            } else {
                "unknown"
            }
        );
        write(&child, &[json!({"type":"ignored"})], false);
        assert!(telemetry.read(&a).subagents[0].estimated_cost.is_none());
        let external = Agent {
            session_kind: "path".into(),
            session_ref: dir.path().join("outside.jsonl").display().to_string(),
            ..a.clone()
        };
        assert!(telemetry.read(&external).cost.is_none());
        telemetry.forget(&[]);
        assert_eq!(telemetry.read(&a).subagents.len(), 1);
    }
}
#[test]
fn codex_large_header_and_truncated_tail_keep_explicit_children() {
    let dir = tempfile::tempdir().unwrap();
    let store = dir.path().join("sessions");
    let id = "11111111-1111-1111-1111-111111111111";
    let path = store.join(format!("{id}.jsonl"));
    write(
        &path,
        &[
            json!({"type":"session_meta","payload":{"id":id,"base_instructions":"Synthetic ".repeat(5000)}}),
        ],
        false,
    );
    let ignored = json!({"type":"ignored","padding":"x".repeat(1000)});
    write(&path, &vec![ignored; 2200], true);
    write(
        &path,
        &[
            json!({"type":"turn_context","payload":{"model":"gpt-6.1-sol","effort":"high"}}),
            json!({"type":"token_usage_record","payload":{"thread_token_usage":{"input_tokens":2000,"output_tokens":100,"total_tokens":2100},"usage":{"input_tokens":1000,"output_tokens":50}}}),
        ],
        true,
    );
    write(
        &store.join("child.jsonl"),
        &[json!({"type":"session_meta","payload":{"id":"child","parent_thread_id":id}})],
        false,
    );
    let mut telemetry = Telemetry::with_roots(HashMap::from([("codex".into(), vec![store])]));
    let a = Agent {
        pane_id: "p1".into(),
        kind: "codex".into(),
        session_kind: "path".into(),
        session_ref: path.display().to_string(),
        ..Default::default()
    };
    let metrics = telemetry.read(&a);
    assert!(metrics.estimate_partial);
    assert_eq!(metrics.tokens, Some(2100));
    assert_eq!(metrics.subagents.len(), 1);
}

#[test]
fn nested_codex_costs_and_transcript_aliases_are_counted_once() {
    use herdr_agent_grid::costs;
    let dir = tempfile::tempdir().unwrap();
    let id = "11111111-1111-1111-1111-111111111111";
    let store = dir.path();
    for (name, parent) in [(id, ""), ("child", id), ("grandchild", "child")] {
        write(
            &store.join(format!("{name}.jsonl")),
            &[
                json!({"type":"session_meta","payload":{"id":name,"parent_thread_id":parent}}),
                json!({"type":"turn_context","payload":{"model":"gpt-6.1-sol"}}),
                json!({"type":"token_usage_record","payload":{"thread_token_usage":{"input_tokens":1000,"output_tokens":100,"total_tokens":1100}}}),
            ],
            false,
        );
    }
    let mut telemetry =
        Telemetry::with_roots(HashMap::from([("codex".into(), vec![store.into()])]));
    let a = Agent {
        kind: "codex".into(),
        pane_id: "root".into(),
        session_kind: "id".into(),
        session_ref: id.into(),
        ..Default::default()
    };
    let root = telemetry.read(&a);
    assert_eq!(root.subagents.len(), 2);
    let own = root.estimated_cost.unwrap();
    assert!((costs::total(&root).amount.unwrap() - 3.0 * own).abs() < 1e-8);
    let alias = Agent {
        session_kind: "path".into(),
        session_ref: store.join(format!("{id}.jsonl")).display().to_string(),
        ..a.clone()
    };
    assert_eq!(root.session_key, telemetry.read(&alias).session_key);
    let child = Agent {
        pane_id: "child".into(),
        session_kind: "path".into(),
        session_ref: store.join("child.jsonl").display().to_string(),
        ..a
    };
    let child_metrics = telemetry.read(&child);
    assert_eq!(child_metrics.subagents.len(), 1);
    let sub = root.subagents.iter().find(|c| c.id == "child").unwrap();
    assert_eq!(sub.session_key, child_metrics.session_key);
    assert_eq!(
        sub.descendant_keys,
        vec![child_metrics.subagents[0].session_key.clone()]
    );
    let all = costs::overview(&[
        (root.session_key.clone(), Some(&root)),
        (child_metrics.session_key.clone(), Some(&child_metrics)),
    ])
    .0;
    assert!((all.amount.unwrap() - 3.0 * own).abs() < 1e-8);
    // Re-reading cached cursors must not add costs a second time.
    assert_eq!(costs::total(&telemetry.read(&alias)), costs::total(&root));
}
