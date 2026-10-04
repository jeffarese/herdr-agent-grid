//! Bounded incremental provider transcripts and explicit child lifecycle signals.
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

pub const MAX_READ: u64 = 2 * 1024 * 1024;
pub fn number(v: &Value) -> Option<f64> {
    v.as_f64().filter(|n| n.is_finite() && *n >= 0.0)
}
pub fn timestamp(v: &Value) -> Option<f64> {
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
pub fn line(v: &str, limit: usize) -> String {
    crate::model::clean(v)
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(limit)
        .collect()
}
pub fn pricing(model: &str, u: &Value, rates: &HashMap<String, Rates>) -> (Option<f64>, String) {
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
    pub subagent: bool,
    pub session_id: String,
    pub session_header_checked: bool,
    pub finished_at: Option<f64>,
    pub lifecycle: String,
    pub lifecycle_at: Option<f64>,
    pub finished_message_id: String,
    pub spawn_calls: HashMap<String, ChildHint>,
    pub child_hints: HashMap<String, ChildHint>,
    codex_totals: Option<[f64; 4]>,
    estimate_incomplete: bool,
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
        self.metrics.estimate_partial =
            self.truncated || self.estimate_incomplete || self.priced_count < self.estimates.len();
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
        if r["isSidechain"] == true && !self.subagent {
            return;
        }
        let at = timestamp(&r["timestamp"]);
        if let Some(at) = at
            && self.metrics.started_at.is_none_or(|s| at < s)
        {
            self.metrics.started_at = Some(at);
        }
        self.claude_lifecycle(r, at);
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
        self.update_provider(path, "claude", rates)
    }
    pub fn update_provider(
        &mut self,
        path: &Path,
        provider: &str,
        rates: &HashMap<String, Rates>,
    ) -> std::io::Result<()> {
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
            *self = Self {
                subagent: self.subagent,
                ..Self::default()
            };
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
                    self.consume_provider(&record, provider, rates);
                }
            }
            self.usages.clear();
            self.estimates.clear();
            self.token_sum = 0.0;
            self.estimate_sum = 0.0;
            self.priced_count = 0;
            self.notes.clear();
            self.metrics.estimated_cost = None;
            self.codex_totals = None;
            if provider == "codex" {
                self.metrics.model.clear();
            }
            self.truncated = true;
        } else if start > self.offset {
            self.truncated = true;
            self.codex_totals = None;
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
                self.consume_provider(&record, provider, rates);
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

#[derive(Clone, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct ChildHint {
    pub name: String,
    pub model: String,
    pub effort: String,
    pub started_at: Option<f64>,
    pub finished_at: Option<f64>,
    pub duration_s: Option<f64>,
    pub status: String,
    pub event_at: Option<f64>,
    pub call_id: String,
}
impl Default for ChildHint {
    fn default() -> Self {
        Self {
            name: String::new(),
            model: String::new(),
            effort: String::new(),
            started_at: None,
            finished_at: None,
            duration_s: None,
            status: "unknown".into(),
            event_at: None,
            call_id: String::new(),
        }
    }
}
pub fn rates() -> &'static HashMap<String, Rates> {
    static RATES: LazyLock<HashMap<String, Rates>> =
        LazyLock::new(|| serde_json::from_str(include_str!("../assets/rates.json")).unwrap());
    &RATES
}
pub fn label(v: &Value, limit: usize) -> String {
    line(v.as_str().unwrap_or(""), limit)
}
pub fn first<'a>(values: &[&'a Value]) -> &'a Value {
    values
        .iter()
        .copied()
        .find(|v| v.as_str().is_some_and(|s| !s.is_empty()))
        .unwrap_or(&Value::Null)
}
pub fn screen_call(text: &str) -> String {
    static TOOL: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"^\s*[⏺●•]\s+([A-Z][\w.-]{1,32})\s*(?:\(|:)").unwrap());
    crate::model::clean(text)
        .lines()
        .rev()
        .find_map(|s| TOOL.captures(s).map(|c| c[1].into()))
        .unwrap_or_default()
}
pub fn estimate(
    provider: &str,
    model: &str,
    u: &Value,
    context: Option<f64>,
    rates: &HashMap<String, Rates>,
) -> (Option<f64>, String) {
    if provider == "claude" {
        return pricing(model, u, rates);
    }
    let m = model.strip_prefix("anthropic/").unwrap_or(model);
    let m = m.strip_prefix("openai/").unwrap_or(m);
    static DATE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"-\d{8}$").unwrap());
    let m = DATE.replace(m, "");
    let Some(r) = rates.get(m.as_ref()) else {
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
    let (Some(input), Some(output)) = (number(&u["input_tokens"]), number(&u["output_tokens"]))
    else {
        return (None, "Input/output token breakdown not reported".into());
    };
    let zero = Value::from(0);
    let read = number(u.get("cached_input_tokens").unwrap_or(&zero));
    let write = number(u.get("cache_write_input_tokens").unwrap_or(&zero));
    let (Some(read), Some(write)) = (read, write) else {
        return (None, "Invalid or unsupported cache token breakdown".into());
    };
    if read + write > input || write > 0.0 && r.write == 0.0 {
        return (None, "Invalid or unsupported cache token breakdown".into());
    }
    let long = r.long_context && context.unwrap_or(input) > 272000.0;
    (
        Some(
            (((input - read - write) * r.input + read * r.read + write * r.write)
                * if long { 2.0 } else { 1.0 }
                + output * r.output * if long { 1.5 } else { 1.0 })
                / 1000000.0,
        ),
        if long {
            "long-context rates"
        } else {
            "Standard API token rates"
        }
        .into(),
    )
}
impl Cursor {
    pub fn unpriced_reason(&self) -> Option<String> {
        self.estimates
            .values()
            .find(|(c, _)| c.is_none())
            .map(|(_, n)| n.clone())
    }
    pub fn consume_provider(&mut self, r: &Value, provider: &str, rates: &HashMap<String, Rates>) {
        if provider == "claude" {
            self.consume(r, rates);
            return;
        }
        if provider != "codex" {
            return;
        }
        let at = timestamp(&r["timestamp"]);
        if let Some(t) = at
            && self.metrics.started_at.is_none_or(|s| t < s)
        {
            self.metrics.started_at = at;
        }
        let p = &r["payload"];
        match r["type"].as_str().unwrap_or("") {
            "session_meta" => {
                self.metrics.started_at = timestamp(&p["timestamp"]).or(at).or(self.metrics.started_at);
                self.session_id = label(first(&[&p["id"],&p["session_id"]]),128);
            }
            "turn_context" => {
                if p["model"].is_string() {self.metrics.model=label(&p["model"],80);}
                for k in ["effort","reasoning_effort","model_reasoning_effort"] {if p.get(k).is_some() {self.metrics.effort=label(&p[k],24);break;}}
            }
            "response_item" => match p["type"].as_str().unwrap_or("") {
                "reasoning" => self.metrics.phase="thinking".into(),
                "message" if p["role"]=="assistant" => {
                    self.metrics.phase="writing".into();
                    if let Some(blocks)=p["content"].as_array() {
                        let text=line(&blocks.iter().filter(|b| matches!(b["type"].as_str(),Some("text"|"output_text"))).filter_map(|b|b["text"].as_str()).collect::<Vec<_>>().join(" "),1200);
                        if !text.is_empty() {self.metrics.last_message=text;self.metrics.message_at=at;}
                    }
                }
                "function_call"|"custom_tool_call" => {
                    let args=p.get("arguments").unwrap_or(&p["input"]);
                    let args=if let Some(s)=args.as_str() {serde_json::from_str(s).unwrap_or(Value::Null)} else {args.clone()};
                    self.consume_tool_record(&serde_json::json!({"type":"assistant","uuid":r["uuid"],"timestamp":r["timestamp"],"message":{"content":[{"type":"tool_use","name":p["name"],"id":p["call_id"],"input":args}]}}),rates);
                }
                "function_call_output"|"custom_tool_call_output" => self.consume_tool_record(&serde_json::json!({"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":p["call_id"]}]}}),rates),
                _=>{}
            },
            "event_msg" if matches!(p["type"].as_str(),Some("task_started"|"task_complete"|"task_aborted")) => {
                self.metrics.phase="working".into();
                let started=p["type"]=="task_started";
                self.finished_at=if started {None} else {timestamp(&p["completed_at"]).or_else(||timestamp(&p["completed_at_ms"])).or(at)};
                self.lifecycle=if started {"working"} else if p["type"]=="task_aborted" {"failed"} else {"done"}.into(); self.lifecycle_at=at;
            }
            _=>{}
        }
        let (u, last) = if r["type"] == "event_msg" && p["type"] == "token_count" {
            (
                &p["info"]["total_token_usage"],
                &p["info"]["last_token_usage"],
            )
        } else if r["type"] == "token_usage_record" {
            (&p["thread_token_usage"], &p["usage"])
        } else {
            return;
        };
        let Some(total) = number(&u["total_tokens"]) else {
            return;
        };
        self.metrics.tokens = Some(total as u64);
        self.metrics.tokens_partial = false;
        let fields = [
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
        ];
        let zero = Value::from(0);
        let totals: Option<Vec<_>> = fields
            .iter()
            .map(|k| number(u.get(*k).unwrap_or(&zero)))
            .collect();
        let Some(totals) =
            totals.filter(|_| u.get("input_tokens").is_some() && u.get("output_tokens").is_some())
        else {
            self.estimate_incomplete = true;
            self.metrics.estimate_note = "Input/output token breakdown not reported".into();
            return;
        };
        let totals: [f64; 4] = totals.try_into().unwrap();
        if self.codex_totals == Some(totals) {
            return;
        }
        let mut delta = serde_json::Map::new();
        let usage = if let Some(prev) = self
            .codex_totals
            .filter(|prev| totals.iter().zip(prev).all(|(n, p)| n >= p))
        {
            for (i, k) in fields.iter().enumerate() {
                delta.insert((*k).into(), Value::from(totals[i] - prev[i]));
            }
            Value::Object(delta)
        } else if self.truncated || self.codex_totals.is_some() {
            self.estimate_incomplete = true;
            last.clone()
        } else {
            for (i, k) in fields.iter().enumerate() {
                delta.insert((*k).into(), Value::from(totals[i]));
            }
            Value::Object(delta)
        };
        self.set_estimate(
            &format!("codex:{}", self.estimates.len()),
            estimate(
                "codex",
                &self.metrics.model,
                &usage,
                number(&last["input_tokens"]),
                rates,
            ),
        );
        self.codex_totals = Some(totals);
    }
    fn consume_tool_record(&mut self, r: &Value, rates: &HashMap<String, Rates>) {
        let child = self.subagent;
        self.subagent = false;
        self.consume(r, rates);
        self.subagent = child;
    }
    fn claude_lifecycle(&mut self, r: &Value, at: Option<f64>) {
        let kind = r["type"].as_str().unwrap_or("");
        let msg = &r["message"];
        let empty = vec![];
        let content = msg["content"].as_array().unwrap_or(&empty);
        let mut texts: Vec<&str> = content
            .iter()
            .filter(|b| b["type"] == "text")
            .filter_map(|b| b["text"].as_str())
            .collect();
        if let Some(s) = msg["content"].as_str() {
            texts.push(s);
        }
        if kind == "queue-operation"
            && r["operation"] == "enqueue"
            && let Some(s) = r["content"].as_str()
        {
            texts.push(s);
        }
        let notification = matches!(kind, "user" | "queue-operation")
            && texts.iter().any(|s| s.contains("<task-notification>"));
        let recent =
            self.lifecycle_at.is_none() || at.is_some_and(|t| t >= self.lifecycle_at.unwrap());
        if self.subagent
            && matches!(kind, "assistant" | "user")
            && !notification
            && recent
            && (kind == "user"
                || msg["id"]
                    .as_str()
                    .is_none_or(|id| id.is_empty() || id != self.finished_message_id))
        {
            self.lifecycle = "working".into();
            self.lifecycle_at = at;
            self.finished_at = None;
            self.finished_message_id.clear();
        }
        if notification {
            static NOTICE: LazyLock<Regex> = LazyLock::new(|| {
                Regex::new(r"(?s)<task-notification>(.*?)</task-notification>").unwrap()
            });
            for text in texts {
                for cap in NOTICE.captures_iter(text) {
                    let tag = |name: &str| {
                        let open = format!("<{name}>");
                        let close = format!("</{name}>");
                        cap[1]
                            .split_once(&open)
                            .and_then(|(_, s)| s.split_once(&close))
                            .map(|(s, _)| s.trim())
                            .filter(|s| !s.contains('<'))
                            .unwrap_or("")
                            .to_owned()
                    };
                    let id = tag("task-id");
                    let status = tag("status");
                    let tid = tag("tool-use-id");
                    if id.is_empty()
                        || !matches!(
                            status.as_str(),
                            "completed" | "failed" | "killed" | "cancelled" | "stopped"
                        )
                    {
                        continue;
                    }
                    if let Some(h) = self
                        .child_hints
                        .get(&id)
                        .or_else(|| self.spawn_calls.get(&tid))
                        && h.finished_at.is_none()
                        && (h.call_id.is_empty() || tid.is_empty() || h.call_id == tid)
                    {
                        let mut h = h.clone();
                        h.finished_at = at;
                        h.status = if status == "completed" {
                            "done"
                        } else {
                            "failed"
                        }
                        .into();
                        h.event_at = at;
                        self.child_hints.insert(id, h);
                    }
                }
            }
        }
        if kind == "assistant" {
            for b in content {
                if b["type"] == "tool_use"
                    && matches!(b["name"].as_str(), Some("Agent" | "Task"))
                    && b["input"].is_object()
                {
                    let args = &b["input"];
                    let h = ChildHint {
                        name: label(
                            first(&[&args["name"], &args["description"], &args["subagent_type"]]),
                            80,
                        ),
                        model: label(&args["model"], 80),
                        effort: label(&args["effort"], 24),
                        started_at: at,
                        status: "working".into(),
                        event_at: at,
                        call_id: label(&b["id"], 128),
                        ..ChildHint::default()
                    };
                    if let Some(id) = b["id"].as_str() {
                        self.spawn_calls.insert(id.into(), h.clone());
                    }
                    if let Some(id) = args["resume"].as_str() {
                        self.child_hints.insert(id.into(), h);
                    }
                }
            }
            if self.subagent && msg["stop_reason"] == "end_turn" && recent {
                self.lifecycle = "done".into();
                self.lifecycle_at = at;
                self.finished_at = at;
                self.finished_message_id = label(&msg["id"], 256);
            }
        } else if kind == "user"
            && let Some(id) = r["toolUseResult"]["agentId"].as_str()
        {
            let result = &r["toolUseResult"];
            let tid = content
                .iter()
                .find(|b| b["type"] == "tool_result")
                .and_then(|b| b["tool_use_id"].as_str())
                .unwrap_or("");
            let mut h = self.spawn_calls.get(tid).cloned().unwrap_or_default();
            if h.name.is_empty() {
                h.name = label(first(&[&result["description"], &result["agentType"]]), 80);
            }
            if result["resolvedModel"].is_string() {
                h.model = label(&result["resolvedModel"], 80);
            }
            let done = matches!(
                result["status"].as_str(),
                Some("completed" | "failed" | "aborted")
            );
            h.finished_at = if done { at } else { None };
            h.duration_s = if done {
                number(&result["totalDurationMs"]).map(|n| n / 1000.0)
            } else {
                None
            };
            h.status = if matches!(result["status"].as_str(), Some("failed" | "aborted")) {
                "failed"
            } else if done {
                "done"
            } else {
                "working"
            }
            .into();
            h.event_at = at;
            self.child_hints.insert(id.into(), h);
        }
    }
}
