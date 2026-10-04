use herdr_agent_grid::{
    install::config_with_shortcut,
    model::*,
    telemetry::{Cursor, estimate, rates},
    view::View,
};
use serde_json::{Value, json};

fn equivalent(expected: &Value, actual: &Value, path: &str) {
    match (expected, actual) {
        (Value::Object(a), Value::Object(b)) => {
            assert_eq!(
                a.keys().collect::<Vec<_>>(),
                b.keys().collect::<Vec<_>>(),
                "{path}"
            );
            for (key, value) in a {
                equivalent(value, &b[key], &format!("{path}.{key}"));
            }
        }
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(a.len(), b.len(), "{path}");
            for (i, (x, y)) in a.iter().zip(b).enumerate() {
                equivalent(x, y, &format!("{path}[{i}]"));
            }
        }
        (Value::Number(a), Value::Number(b)) => assert!(
            (a.as_f64().unwrap() - b.as_f64().unwrap()).abs() < 1e-8,
            "{path}: {a} != {b}"
        ),
        _ => assert_eq!(expected, actual, "{path}"),
    }
}

#[test]
fn provider_event_sequences() {
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/provider-events.json")).unwrap();
    let mut count = 0;
    for (i, case) in cases.iter().enumerate() {
        let mut cursor = Cursor::default();
        for (j, (step, expected)) in case["input"]["steps"]
            .as_array()
            .unwrap()
            .iter()
            .zip(case["expected"].as_array().unwrap())
            .enumerate()
        {
            cursor.subagent = step["subagent"].as_bool().unwrap();
            cursor.child_hints = serde_json::from_value(step["child_hints"].clone()).unwrap();
            cursor.consume_provider(&step["record"], step["provider"].as_str().unwrap(), rates());
            let actual = json!({"metrics":cursor.metrics,"session_id":cursor.session_id,"finished_at":cursor.finished_at,"lifecycle":if cursor.lifecycle.is_empty(){"unknown"}else{&cursor.lifecycle},"lifecycle_at":cursor.lifecycle_at,"child_hints":cursor.child_hints,"spawn_calls":cursor.spawn_calls});
            equivalent(expected, &actual, &format!("sequence {i}, event {j}"));
            count += 1;
        }
    }
    assert_eq!(count, 64);
}

#[test]
fn model_pricing_cache_tiers_and_invalid_usage() {
    let cases: Vec<Value> = serde_json::from_str(include_str!("fixtures/pricing.json")).unwrap();
    for (i, case) in cases.iter().enumerate() {
        let input = &case["input"];
        let actual = estimate(
            input["provider"].as_str().unwrap(),
            input["model"].as_str().unwrap(),
            &input["usage"],
            input["context"].as_f64(),
            rates(),
        );
        equivalent(&case["expected"], &json!(actual), &format!("pricing {i}"));
    }
    assert_eq!(cases.len(), 280);
    let usage = json!({"input_tokens":1_000_000,"cached_input_tokens":700_000,"cache_write_input_tokens":100_000,"output_tokens":100_000,"reasoning_output_tokens":80_000});
    for (context, expected) in [(200_000.0, 1.72), (300_000.0, 2.94)] {
        assert!(
            (estimate("codex", "gpt-6.1-sol", &usage, Some(context), rates())
                .0
                .unwrap()
                - expected)
                .abs()
                < 1e-8
        );
    }
}

#[test]
fn configuration_preserves_comments_bindings_and_idempotency() {
    let before = "# keep this\n[[keys.command]]\nkey = [\"alt+a\"]\ntype = \"plugin_action\"\ncommand = \"herdr-grid.open\" # custom\n";
    let after = config_with_shortcut(before).unwrap().0;
    assert_eq!(
        after,
        before.replace("herdr-grid.open", "herdr-agent-grid.open")
    );
    assert_eq!(config_with_shortcut(&after).unwrap().0, after);
    for text in [
        "[keys]\ngoto=\"cmd+g\"\n",
        "[[keys.command]]\nkey=[\"ctrl+alt+g\"]\ncommand=\"other\"\n",
    ] {
        assert!(config_with_shortcut(text).unwrap_err().contains("already"));
    }
    assert!(config_with_shortcut("not valid toml [").is_err());
}

#[test]
fn selection_filters_and_lifecycle_order_survive_new_snapshots() {
    let mut state = demo_state();
    let mut view = View::new(false);
    view.arrange(&state, 160, 44);
    view.move_by(&state, 3, true);
    let selected = view.selected.clone();
    state.agents.reverse();
    state.revision += 1;
    view.arrange(&state, 160, 44);
    assert_eq!(view.selected, selected);
    state.agents.retain(|a| a.pane_id != selected);
    state.revision += 1;
    view.arrange(&state, 160, 44);
    assert!(state.agents.iter().any(|a| a.pane_id == view.selected));
    view.query = "blocked".into();
    view.arrange(&state, 160, 44);
    assert!(
        view.items
            .iter()
            .all(|i| state.agents[*i].status == "blocked")
    );
    view.query = "no-such-agent".into();
    view.arrange(&state, 160, 44);
    assert!(view.visible.is_empty());
    assert!(herdr_agent_grid::app::targets(&view, &state).is_empty());
}

#[test]
fn layout_never_overlaps_and_handles_empty_inventory() {
    for w in [1, 12, 40, 80, 140, 240] {
        for h in [1, 8, 24, 38, 64] {
            for count in [0, 1, 6, 35, 1000] {
                let geometry = layout(w, h, count);
                let mut cells = std::collections::HashSet::new();
                for r in geometry.rects {
                    for x in r.x..r.x + r.width {
                        for y in r.y..r.y + r.height {
                            assert!(x < w && y < h);
                            assert!(cells.insert((x, y)));
                        }
                    }
                }
            }
        }
    }
}

#[test]
fn inventory_rejects_duplicates_and_retains_workspace_labels() {
    let agents = agents_from(
        &json!({"workspaces":[{"workspace_id":"w1","label":"project"}],"tabs":[{"tab_id":"w1:t1","label":"task"}],"agents":[{"pane_id":"w1:p1","workspace_id":"w1","tab_id":"w1:t1","agent":"codex","name":null,"agent_status":"idle"},{"pane_id":"w1:p1","agent":"codex"},{"pane_id":"w1:p2","agent":null}]}),
    );
    assert_eq!(agents.len(), 1);
    assert_eq!(
        (&*agents[0].title, &*agents[0].workspace),
        ("task", "project")
    );
}

#[test]
fn frames_match_reviewed_terminal_fixtures() {
    let state: State = serde_json::from_str(include_str!("../assets/demo.json")).unwrap();
    let cases: Vec<Value> = serde_json::from_str(include_str!("fixtures/frames.json")).unwrap();
    for case in cases {
        let mut view = View::new(false);
        view.icons = "unicode".into();
        view.zoom = case["zoom"].as_bool().unwrap();
        let actual = view.draw(
            &state,
            case["width"].as_u64().unwrap() as usize,
            case["height"].as_u64().unwrap() as usize,
            1800000000.0,
            0.0,
        );
        equivalent(&case["expected"], &json!(actual), "frame");
    }
}

#[test]
fn animation_is_bounded_and_stops_for_settled_or_reduced_motion() {
    use herdr_agent_grid::visuals::{core_runs, icon, phase};
    for state in [
        "working", "thinking", "writing", "tool", "done", "idle", "blocked", "unknown",
    ] {
        for (w, h) in [(1, 1), (15, 1), (42, 2)] {
            let frame = core_runs(state, w, h, 1.0, "pane", true);
            for (x, y, text, _) in &frame {
                assert!(x + width(text) <= w && *y < h);
            }
            if ["done", "idle", "blocked", "unknown"].contains(&state) {
                assert_eq!(frame, core_runs(state, w, h, 9.0, "pane", true));
            }
            assert_eq!(
                core_runs(state, w, h, 1.0, "pane", false),
                core_runs(state, w, h, 9.0, "pane", false)
            );
        }
    }
    let metrics = Metrics {
        phase: "thinking".into(),
        ..Default::default()
    };
    for state in ["done", "idle", "blocked"] {
        assert_eq!(phase(state, &metrics), state);
    }
    assert_eq!(phase("working", &metrics), "thinking");
    assert_eq!(icon("claude", "unicode"), "✻");
    assert_eq!(icon("codex", "ascii"), "X");
}

#[test]
fn streaming_tool_calls_deduplicate_and_hide_private_reasoning() {
    let mut cursor = Cursor::default();
    let record = json!({"type":"assistant","timestamp":"2026-10-04T12:00:00Z","message":{"content":[{"type":"tool_use","id":"call1","name":"Bash"}]}});
    cursor.consume_provider(&record, "claude", rates());
    cursor.consume_provider(&record, "claude", rates());
    assert_eq!(cursor.metrics.trail.len(), 1);
    cursor.consume_provider(&json!({"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"call1","is_error":true}]}}),"claude",rates());
    assert!(cursor.metrics.trail[0].done && cursor.metrics.trail[0].error);
    cursor.consume_provider(&json!({"type":"assistant","message":{"content":[{"type":"thinking","thinking":"private text"}]}}),"claude",rates());
    assert_eq!(cursor.metrics.phase, "thinking");
    assert!(
        !serde_json::to_string(&cursor.metrics)
            .unwrap()
            .contains("private")
    );
}

#[test]
fn unicode_details_wrap_without_losing_message_content() {
    let mut state = demo_state();
    let mut view = View::new(false);
    view.zoom = true;
    view.selected = state.agents[0].pane_id.clone();
    state.metrics.get_mut(&view.selected).unwrap().last_message = "漢字テスト".repeat(20);
    let frame = herdr_agent_grid::app::plain_frame(&view.draw(&state, 80, 44, now(), 0.0), 80, 44);
    assert!(frame.contains("Tool target      src/styles/dashboard.css"));
    let body = frame.split("LATEST ASSISTANT MESSAGE").nth(1).unwrap();
    assert_eq!(body.matches('漢').count(), 20);
    assert_eq!(body.matches('字').count(), 20);
    assert_eq!(clean("\x1b[31mred\x1b[0m\x1b]52;c;secret\x07\0"), "red");
    assert_eq!(clip("界界x", 3, true), "界…");
}
