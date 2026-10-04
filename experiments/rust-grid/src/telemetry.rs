//! Bounded, incremental Claude transcript path for the comparative prototype.
//! This is deliberately not a live provider/session discovery implementation.
use crate::model::{Metrics, Rates, ToolCall};
use chrono::DateTime;
use regex::Regex;
use serde_json::Value;
use std::{
    collections::HashMap,
    fs::File,
    io::{Read, Seek, SeekFrom},
    os::unix::fs::MetadataExt,
    path::Path,
    sync::LazyLock,
};

const MAX_READ: u64 = 2 * 1024 * 1024;
fn number(v: &Value) -> Option<f64> {
    v.as_f64().filter(|n| n.is_finite() && *n >= 0.0)
}
fn timestamp(v: &Value) -> Option<f64> {
    if let Some(n) = v.as_f64() {
        return if n.is_finite() && n > 0.0 {
            Some(if n > 100_000_000_000.0 { n / 1000.0 } else { n })
        } else {
            None
        };
    }
    DateTime::parse_from_rfc3339(v.as_str()?)
        .ok()
        .map(|d| d.timestamp_micros() as f64 / 1_000_000.0)
        .filter(|n| *n > 0.0)
}
fn line(v: &str, limit: usize) -> String {
    static CONTROLS: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[ -/]*[@-~]")
            .unwrap()
    });
    CONTROLS
        .replace_all(v, "")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(limit)
        .collect()
}
fn pricing(model: &str, u: &Value, rates: &HashMap<String, Rates>) -> (Option<f64>, String) {
    let model = model.strip_prefix("anthropic/").unwrap_or(model);
    let model = model.strip_prefix("openai/").unwrap_or(model);
    let model = if model.len() > 9
        && model.as_bytes()[model.len() - 9] == b'-'
        && model.as_bytes()[model.len() - 8..]
            .iter()
            .all(u8::is_ascii_digit)
    {
        &model[..model.len() - 9]
    } else {
        model
    };
    let Some(r) = rates.get(model) else {
        return (
            None,
            format!(
                "Pricing unavailable for {}",
                if model.is_empty() {
                    "unreported model"
                } else {
                    model
                }
            ),
        );
    };
    let (Some(incoming), Some(outgoing)) =
        (number(&u["input_tokens"]), number(&u["output_tokens"]))
    else {
        return (None, "Input/output token breakdown not reported".into());
    };
    let zero = Value::from(0);
    let read = number(u.get("cache_read_input_tokens").unwrap_or(&zero));
    let write = number(u.get("cache_creation_input_tokens").unwrap_or(&zero));
    let split = &u["cache_creation"];
    let hour = number(split.get("ephemeral_1h_input_tokens").unwrap_or(&zero));
    let short = number(
        split
            .get("ephemeral_5m_input_tokens")
            .unwrap_or(u.get("cache_creation_input_tokens").unwrap_or(&zero)),
    );
    let (Some(read), Some(write), Some(hour), Some(short)) = (read, write, hour, short) else {
        return (None, "Invalid cache token breakdown".into());
    };
    if hour + short != write {
        return (None, "Invalid cache token breakdown".into());
    }
    let mut notes = vec![];
    if write > 0.0 && split.as_object().is_none_or(|s| s.is_empty()) {
        notes.push("5m cache writes assumed");
    }
    let mut multiplier = 1.0;
    if u["speed"] == "fast" {
        if !matches!(
            model,
            "claude-opus-5-5" | "claude-opus-5" | "claude-opus-4-8"
        ) {
            return (None, "Fast-mode pricing unavailable for model".into());
        }
        multiplier *= 2.0;
        notes.push("Fast mode");
    }
    if u["inference_geo"] == "us" {
        multiplier *= 1.1;
        notes.push("US inference");
    }
    (
        Some(
            (incoming * r.input
                + outgoing * r.output
                + read * r.read
                + short * r.write
                + hour * r.write_hour)
                * multiplier
                / 1_000_000.0,
        ),
        if notes.is_empty() {
            "Standard API token rates".into()
        } else {
            notes.join("; ")
        },
    )
}
#[derive(Default)]
pub struct Cursor {
    pub metrics: Metrics,
    pub offset: u64,
    identity: Option<(u64, u64)>,
    boundary: Vec<u8>,
    usages: HashMap<String, f64>,
    estimates: HashMap<String, (Option<f64>, String)>,
    token_sum: f64,
    estimate_sum: f64,
    priced_count: usize,
    notes: Vec<(String, usize)>,
    cumulative_tokens: bool,
    truncated: bool,
    call_id: String,
}
impl Cursor {
    fn set_estimate(&mut self, id: &str, value: (Option<f64>, String)) {
        if let Some((cost, note)) = self.estimates.get(id) {
            if let Some(c) = cost {
                self.estimate_sum -= c;
                self.priced_count -= 1;
            }
            if let Some(pos) = self.notes.iter().position(|(n, _)| n == note) {
                self.notes[pos].1 -= 1;
                if self.notes[pos].1 == 0 {
                    self.notes.remove(pos);
                }
            }
        }
        if let Some(c) = value.0 {
            self.estimate_sum += c;
            self.priced_count += 1;
        }
        if let Some((_, count)) = self.notes.iter_mut().find(|(n, _)| n == &value.1) {
            *count += 1;
        } else {
            self.notes.push((value.1.clone(), 1));
        }
        self.estimates.insert(id.into(), value);
        self.update_estimate();
    }
    fn update_estimate(&mut self) {
        self.metrics.estimated_cost = if self.priced_count > 0 {
            Some(self.estimate_sum.max(0.0))
        } else {
            None
        };
        self.metrics.estimate_partial = self.truncated || self.priced_count < self.estimates.len();
        self.metrics.estimate_note = self
            .notes
            .iter()
            .take(3)
            .map(|(n, _)| n.as_str())
            .collect::<Vec<_>>()
            .join("; ")
            + " · rates 2026-10-04";
    }
    pub fn consume(&mut self, r: &Value, rates: &HashMap<String, Rates>) {
        if r["isSidechain"] == true {
            return;
        }
        let at = timestamp(&r["timestamp"]);
        if let Some(at) = at
            && self.metrics.started_at.is_none_or(|s| at < s)
        {
            self.metrics.started_at = Some(at);
        }
        let msg = &r["message"];
        match r["type"].as_str().unwrap_or("") {
            "assistant" => {
                if let Some(model) = msg["model"].as_str() {
                    self.metrics.model = line(model, 80);
                }
                if let Some(effort) = r
                    .get("perTurnEffort")
                    .filter(|v| v.as_str().is_some_and(|s| !s.is_empty()))
                    .unwrap_or(&r["effort"])
                    .as_str()
                {
                    self.metrics.effort = line(effort, 24);
                }
                let usage = &msg["usage"];
                if let Some(id) = msg["id"]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .or_else(|| r["uuid"].as_str())
                    && usage.is_object()
                {
                    if !self.cumulative_tokens {
                        let total = [
                            "input_tokens",
                            "output_tokens",
                            "cache_read_input_tokens",
                            "cache_creation_input_tokens",
                        ]
                        .iter()
                        .map(|k| number(&usage[k]).unwrap_or(0.0))
                        .sum::<f64>();
                        self.token_sum += total - self.usages.get(id).copied().unwrap_or(0.0);
                        self.usages.insert(id.into(), total);
                        self.metrics.tokens = Some(self.token_sum as u64);
                        self.metrics.tokens_partial = self.truncated;
                    }
                    self.set_estimate(
                        id,
                        pricing(msg["model"].as_str().unwrap_or(""), usage, rates),
                    );
                }
                if let Some(content) = msg["content"].as_array() {
                    let text = content
                        .iter()
                        .filter(|b| matches!(b["type"].as_str(), Some("text" | "output_text")))
                        .filter_map(|b| b["text"].as_str())
                        .collect::<Vec<_>>()
                        .join(" ");
                    let text = line(&text, 1200);
                    if !text.is_empty() {
                        self.metrics.last_message = text;
                        self.metrics.message_at = at;
                    }
                    for b in content {
                        match b["type"].as_str().unwrap_or("") {
                            "thinking" | "redacted_thinking" => {
                                self.metrics.phase = "thinking".into()
                            }
                            "text" => self.metrics.phase = "writing".into(),
                            "tool_use" => {
                                if let Some(name) = b["name"].as_str().filter(|s| !s.is_empty()) {
                                    let id = b["id"]
                                        .as_str()
                                        .or_else(|| r["uuid"].as_str())
                                        .map(str::to_owned)
                                        .unwrap_or_else(|| format!("{name}:{at:?}"));
                                    let previous = self.metrics.trail.iter().find(|c| c.id == id);
                                    self.metrics.last_call = line(name, 80);
                                    self.metrics.call_at = previous.map(|c| c.at).unwrap_or(at);
                                    self.metrics.call_done = Some(previous.is_some_and(|c| c.done));
                                    self.metrics.call_source = "transcript".into();
                                    self.metrics.call_detail = ["description", "file_path", "path"]
                                        .iter()
                                        .find_map(|k| b["input"][k].as_str())
                                        .map(|s| line(s, 160))
                                        .unwrap_or_default();
                                    if previous.is_none() {
                                        if self.metrics.trail.len() >= 24 {
                                            self.metrics.trail.remove(0);
                                        }
                                        self.metrics.trail.push(ToolCall {
                                            id: id.clone(),
                                            name: self.metrics.last_call.clone(),
                                            at,
                                            done: false,
                                            error: false,
                                        });
                                    }
                                    self.call_id = id;
                                    self.metrics.phase = "tool".into();
                                }
                            }
                            _ => {}
                        }
                    }
                }
            }
            "user" => {
                if let Some(content) = msg["content"].as_array() {
                    for b in content {
                        if b["type"] == "tool_result" && !self.call_id.is_empty() {
                            let id = b["tool_use_id"].as_str().unwrap_or("");
                            for c in &mut self.metrics.trail {
                                if c.id == id {
                                    c.done = true;
                                    c.error = b["is_error"].as_bool().unwrap_or(false);
                                }
                            }
                            if id == self.call_id {
                                self.metrics.call_done = Some(true);
                            }
                            if self.metrics.phase == "tool"
                                && self.metrics.trail.iter().all(|c| c.done)
                            {
                                self.metrics.phase = "working".into();
                            }
                        }
                    }
                }
            }
            "cost-state" => {
                if let Some(c) = number(&r["totalCostUSD"]) {
                    self.metrics.cost = Some(c);
                    self.metrics.cost_partial = r["hasUnknownModelCost"].as_bool().unwrap_or(false);
                }
                if let Some(t) = timestamp(&r["startTime"]) {
                    self.metrics.started_at = Some(t);
                }
                if let Some(models) = r["modelUsage"]
                    .as_object()
                    .filter(|m| !m.is_empty() && m.values().all(Value::is_object))
                {
                    self.metrics.tokens = Some(
                        models
                            .values()
                            .map(|u| {
                                [
                                    "inputTokens",
                                    "outputTokens",
                                    "cacheReadInputTokens",
                                    "cacheCreationInputTokens",
                                ]
                                .iter()
                                .map(|k| number(&u[k]).unwrap_or(0.0))
                                .sum::<f64>()
                            })
                            .sum::<f64>() as u64,
                    );
                    self.metrics.tokens_partial = false;
                    self.cumulative_tokens = true;
                    self.usages.clear();
                    self.token_sum = 0.0;
                }
            }
            "result" => {
                if let Some(c) = number(&r["total_cost_usd"]) {
                    self.metrics.cost = Some(c);
                }
            }
            _ => {}
        }
    }
    pub fn update(&mut self, path: &Path, rates: &HashMap<String, Rates>) -> std::io::Result<()> {
        let mut stream = File::open(path)?;
        let stat = stream.metadata()?;
        let identity = (stat.dev(), stat.ino());
        stream.seek(SeekFrom::Start(self.offset.saturating_sub(64)))?;
        let mut boundary = vec![];
        (&mut stream)
            .take(self.offset.min(64))
            .read_to_end(&mut boundary)?;
        if self.identity != Some(identity) || stat.len() < self.offset || boundary != self.boundary
        {
            *self = Self::default();
            self.identity = Some(identity);
        }
        let start = self.offset.max(stat.len().saturating_sub(MAX_READ));
        if self.offset == 0 && start > 0 {
            stream.seek(SeekFrom::Start(0))?;
            let mut header = vec![];
            (&mut stream).take(32768).read_to_end(&mut header)?;
            for line in header.split(|c| *c == b'\n').take(32) {
                if let Ok(record) = serde_json::from_slice::<Value>(line)
                    && record.is_object()
                {
                    self.consume(&record, rates);
                }
            }
            self.usages.clear();
            self.estimates.clear();
            self.token_sum = 0.0;
            self.estimate_sum = 0.0;
            self.priced_count = 0;
            self.notes.clear();
            self.metrics.estimated_cost = None;
            self.truncated = true;
        } else if start > self.offset {
            self.truncated = true;
        }
        stream.seek(SeekFrom::Start(start))?;
        let mut data = Vec::with_capacity(stat.len().saturating_sub(start).min(MAX_READ) as usize);
        (&mut stream).take(MAX_READ).read_to_end(&mut data)?;
        let Some(end) = data.iter().rposition(|b| *b == b'\n') else {
            return Ok(());
        };
        for (i, line) in data[..end].split(|c| *c == b'\n').enumerate() {
            if i == 0 && start > self.offset {
                continue;
            }
            if let Ok(record) = serde_json::from_slice::<Value>(line)
                && record.is_object()
            {
                self.consume(&record, rates);
            }
        }
        self.offset = start + end as u64 + 1;
        stream.seek(SeekFrom::Start(self.offset.saturating_sub(64)))?;
        self.boundary = vec![0; self.offset.min(64) as usize];
        stream.read_exact(&mut self.boundary)?;
        if !self.estimates.is_empty() {
            self.update_estimate();
        }
        Ok(())
    }
}
