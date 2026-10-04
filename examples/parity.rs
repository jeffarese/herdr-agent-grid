//! Test-only JSON adapter. It is never included in release archives.
use herdr_agent_grid::{
    install::config_with_shortcut,
    model::agents_from,
    sessions::Telemetry,
    telemetry::{Cursor, estimate, rates},
};
use serde_json::{Value, json};
use std::io::{self, BufRead};
fn main() {
    let mut telemetry = Telemetry::default();
    for line in io::stdin().lock().lines() {
        let input: Value = serde_json::from_str(&line.unwrap()).unwrap();
        let result = match input["op"].as_str().unwrap_or("consume") {
            "consume" => {
                let mut c = Cursor::default();
                let mut output = vec![];
                for step in input["steps"].as_array().unwrap() {
                    c.subagent = step["subagent"].as_bool().unwrap_or(false);
                    if step.get("child_hints").is_some() {
                        c.child_hints =
                            serde_json::from_value(step["child_hints"].clone()).unwrap();
                    }
                    c.consume_provider(
                        &step["record"],
                        step["provider"].as_str().unwrap(),
                        rates(),
                    );
                    output.push(json!({"metrics":c.metrics,"session_id":c.session_id,"finished_at":c.finished_at,"lifecycle":if c.lifecycle.is_empty(){"unknown"}else{&c.lifecycle},"lifecycle_at":c.lifecycle_at,"child_hints":c.child_hints,"spawn_calls":c.spawn_calls}));
                }
                json!(output)
            }
            "read" => {
                let a = serde_json::from_value(input["agent"].clone()).unwrap();
                json!(telemetry.read(&a))
            }
            "inventory" => json!(agents_from(&input["snapshot"])),
            "install" => match config_with_shortcut(input["text"].as_str().unwrap()) {
                Ok((text, _)) => json!({"text":text}),
                Err(e) => json!({"error":e}),
            },
            "estimate" => {
                let (cost, note) = estimate(
                    input["provider"].as_str().unwrap(),
                    input["model"].as_str().unwrap(),
                    &input["usage"],
                    input["context"].as_f64(),
                    rates(),
                );
                json!([cost, note])
            }
            _ => panic!("Unknown operation"),
        };
        println!("{}", result);
    }
}
