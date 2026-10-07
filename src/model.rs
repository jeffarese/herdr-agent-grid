use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use unicode_width::UnicodeWidthChar;

#[derive(Clone, Default, PartialEq, Deserialize, Serialize)]
#[serde(default)]
pub struct Agent {
    pub pane_id: String,
    pub kind: String,
    pub name: String,
    pub title: String,
    pub workspace: String,
    pub status: String,
    pub provider: String,
    pub session_kind: String,
    pub session_ref: String,
    pub cwd: String,
    pub terminal_id: String,
    pub state_seq: u64,
}

#[derive(Clone, Default, PartialEq, Deserialize, Serialize)]
#[serde(default)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub at: Option<f64>,
    pub done: bool,
    pub error: bool,
}

#[derive(Clone, Default, PartialEq, Deserialize, Serialize)]
#[serde(default)]
pub struct Subagent {
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub session_key: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub descendant_keys: Vec<String>,
    pub id: String,
    pub name: String,
    pub model: String,
    pub effort: String,
    pub cost: Option<f64>,
    pub estimated_cost: Option<f64>,
    pub cost_partial: bool,
    pub estimate_partial: bool,
    pub started_at: Option<f64>,
    pub finished_at: Option<f64>,
    pub duration_s: Option<f64>,
    pub status: String,
    /// The child's own transcript metrics, for the subagent cards view.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metrics: Option<Box<Metrics>>,
}

#[derive(Clone, Default, PartialEq, Deserialize, Serialize)]
#[serde(default)]
pub struct Metrics {
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub session_key: String,
    pub last_call: String,
    pub call_at: Option<f64>,
    pub call_done: Option<bool>,
    pub call_detail: String,
    pub call_source: String,
    pub started_at: Option<f64>,
    /// When a finished subagent stopped, so its card's clock stops too.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ended_at: Option<f64>,
    pub seen_at: f64,
    pub status_since: f64,
    pub tokens: Option<u64>,
    pub tokens_partial: bool,
    pub cost: Option<f64>,
    pub cost_partial: bool,
    pub estimated_cost: Option<f64>,
    pub estimate_partial: bool,
    pub estimate_note: String,
    pub model: String,
    pub effort: String,
    pub cost_reason: String,
    pub source: String,
    pub issue: String,
    pub phase: String,
    pub trail: Vec<ToolCall>,
    pub last_message: String,
    pub message_at: Option<f64>,
    pub subagents: Vec<Subagent>,
}

#[derive(Clone, Default, PartialEq, Deserialize, Serialize)]
#[serde(default)]
pub struct State {
    pub agents: Vec<Agent>,
    pub previews: HashMap<String, String>,
    pub errors: HashMap<String, String>,
    pub error: String,
    pub updated: f64,
    pub metrics: HashMap<String, Metrics>,
    pub revision: u64,
}

#[derive(Clone, Deserialize, Serialize)]
pub struct Rates {
    pub input: f64,
    pub output: f64,
    pub read: f64,
    pub write: f64,
    pub write_hour: f64,
    pub long_context: bool,
}

pub fn width(text: &str) -> usize {
    text.chars().map(|c| c.width().unwrap_or(0)).sum()
}

pub fn clip(text: &str, limit: usize, ellipsis: bool) -> String {
    if limit == 0 {
        return String::new();
    }
    if text.is_ascii() && text.bytes().all(|c| (32..127).contains(&c)) {
        return if text.len() <= limit {
            text.into()
        } else if ellipsis {
            format!("{}…", &text[..limit - 1])
        } else {
            text[..limit].into()
        };
    }
    // Most UI strings are already clean. Borrow Unicode text instead of
    // allocating three sanitized copies for every animated draw command.
    let expanded = if text.chars().any(char::is_control) {
        std::borrow::Cow::Owned(clean(text).replace('\n', " ").replace('\t', "    "))
    } else {
        std::borrow::Cow::Borrowed(text)
    };
    let mut result = String::new();
    let mut used = 0;
    for c in expanded.chars().filter(|c| !c.is_control()) {
        let size = c.width().unwrap_or(0);
        if used + size > limit {
            if ellipsis {
                return format!("{}…", clip(&result, limit - 1, false));
            }
            break;
        }
        if result.is_empty() && size == 0 {
            continue;
        }
        result.push(c);
        used += size;
    }
    result
}

pub fn padded(text: &str, limit: usize) -> String {
    let clipped = clip(text, limit, true);
    format!(
        "{}{}",
        clipped,
        " ".repeat(limit.saturating_sub(width(&clipped)))
    )
}

#[derive(Clone, Copy, Default, Deserialize, Serialize)]
pub struct Rect {
    pub x: usize,
    pub y: usize,
    pub width: usize,
    pub height: usize,
}
#[derive(Clone, Default)]
pub struct Layout {
    pub rects: Vec<Rect>,
    pub columns: usize,
    pub capacity: usize,
}

pub fn layout(width: usize, height: usize, count: usize) -> Layout {
    let (width, height) = (width.max(1), height.max(1));
    if count == 0 {
        return Layout {
            rects: vec![],
            columns: 1,
            capacity: 1,
        };
    }
    let max_cols = ((width + 1) / 37).max(1);
    let max_rows = ((height + 1) / 9).max(1);
    let visible = count.min(max_cols * max_rows);
    let mut best = (f64::INFINITY, 1, 1);
    for cols in 1..=max_cols.min(visible) {
        let rows = visible.div_ceil(cols);
        if rows > max_rows {
            continue;
        }
        let tile_w = (width + 1 - cols) as f64 / cols as f64;
        let tile_h = (height + 1 - rows) as f64 / rows as f64;
        let score = ((tile_w / tile_h.max(1.0)).max(0.1) / 4.0).ln().abs()
            + 0.6 * (cols * rows - visible) as f64 / visible as f64;
        if score < best.0 {
            best = (score, cols, rows);
        }
    }
    let (_, columns, rows) = best;
    let rects = (0..visible)
        .map(|i| {
            let (col, row) = (i % columns, i / columns);
            let (left, right) = (
                col * (width + 1) / columns,
                (col + 1) * (width + 1) / columns - 1,
            );
            let (top, bottom) = (
                row * (height + 1) / rows,
                (row + 1) * (height + 1) / rows - 1,
            );
            Rect {
                x: left,
                y: top,
                width: right.saturating_sub(left).max(1),
                height: bottom.saturating_sub(top).max(1),
            }
        })
        .collect();
    Layout {
        rects,
        columns,
        capacity: visible,
    }
}

pub fn duration(seconds: Option<f64>) -> String {
    let Some(s) = seconds else {
        return "—".into();
    };
    let s = s.max(0.0) as u64;
    if s < 60 {
        format!("{s}s")
    } else if s < 3600 {
        format!("{}m {:02}s", s / 60, s % 60)
    } else if s < 86400 {
        format!("{}h {:02}m", s / 3600, s % 3600 / 60)
    } else {
        format!("{}d {}h", s / 86400, s % 86400 / 3600)
    }
}
pub fn tokens(m: &Metrics) -> String {
    let Some(t) = m.tokens else {
        return "—".into();
    };
    let value = if t >= 1_000_000 {
        format!("{:.1}m", t as f64 / 1_000_000.0)
    } else if t >= 1000 {
        format!("{:.1}k", t as f64 / 1000.0)
    } else {
        t.to_string()
    };
    format!("{}{value}", if m.tokens_partial { "≥" } else { "" })
}
pub fn cost(
    reported: Option<f64>,
    estimated: Option<f64>,
    partial: bool,
    estimate_partial: bool,
) -> String {
    let Some(c) = reported.or(estimated) else {
        return "—".into();
    };
    let value = if c == 0.0 {
        "$0.00".into()
    } else if c < 0.005 {
        "<$0.01".into()
    } else {
        format!("${c:.2}")
    };
    format!(
        "{}{}{value}",
        if if reported.is_some() {
            partial
        } else {
            estimate_partial
        } {
            "≥"
        } else {
            ""
        },
        if reported.is_none() { "~" } else { "" }
    )
}
pub fn cost_label(m: &Metrics) -> String {
    crate::costs::total(m).label()
}
pub fn cost_detail(m: &Metrics) -> String {
    if m.cost.is_some() && !m.subagents.is_empty() {
        "Reported session total; child inclusion unverified; child costs not added".into()
    } else if m.cost.is_some() {
        "Reported session total; not an invoice".into()
    } else if !m.subagents.is_empty() && crate::costs::total(m).amount.is_some() {
        format!(
            "Combined own + subagent API token cost · {}",
            m.estimate_note
        )
    } else if m.estimated_cost.is_some() {
        format!("Estimated API token cost · {}", m.estimate_note)
    } else if !m.cost_reason.is_empty() {
        m.cost_reason.clone()
    } else {
        "Token usage or pricing unavailable".into()
    }
}
pub fn child_time(c: &Subagent, now: f64) -> String {
    duration(
        c.duration_s
            .or_else(|| c.started_at.map(|s| c.finished_at.unwrap_or(now) - s)),
    )
}

pub fn clean(text: &str) -> String {
    use regex::Regex;
    use std::sync::LazyLock;
    static ANSI: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[ -/]*[@-~]")
            .unwrap()
    });
    ANSI.replace_all(text, "")
        .chars()
        .filter(|c| !c.is_control() || matches!(c, '\n' | '\t'))
        .collect()
}
pub fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
impl Agent {
    pub fn provider(&self) -> &str {
        if self.provider.is_empty() {
            &self.kind
        } else {
            &self.provider
        }
    }
    pub fn identity(&self) -> String {
        serde_json::to_string(&(
            &self.pane_id,
            &self.terminal_id,
            self.provider(),
            &self.session_kind,
            &self.session_ref,
        ))
        .unwrap()
    }
}
pub fn agents_from(snapshot: &serde_json::Value) -> Vec<Agent> {
    use serde_json::Value;
    let empty = Vec::new();
    let array = |key| snapshot[key].as_array().unwrap_or(&empty);
    let spaces: HashMap<_, _> = array("workspaces")
        .iter()
        .filter_map(|v| Some((v["workspace_id"].as_str()?, v)))
        .collect();
    let tabs: HashMap<_, _> = array("tabs")
        .iter()
        .filter_map(|v| Some((v["tab_id"].as_str()?, v)))
        .collect();
    let panes: HashMap<_, _> = array("panes")
        .iter()
        .filter_map(|v| Some((v["pane_id"].as_str()?, v)))
        .collect();
    let mut seen = std::collections::HashSet::new();
    let first = |values: &[&Value]| {
        values
            .iter()
            .filter_map(|v| v.as_str())
            .find(|s| !s.is_empty())
            .unwrap_or("")
            .to_owned()
    };
    array("agents")
        .iter()
        .filter_map(|a| {
            let pid = a["pane_id"].as_str().filter(|s| !s.is_empty())?;
            if !seen.insert(pid) {
                return None;
            }
            let p = panes.get(pid).copied().unwrap_or(&Value::Null);
            let kind = first(&[&a["display_agent"], &a["agent"], &p["agent"]]);
            if kind.is_empty() {
                return None;
            }
            let space = spaces
                .get(a["workspace_id"].as_str().unwrap_or(""))
                .copied()
                .unwrap_or(&Value::Null);
            let tab = tabs
                .get(a["tab_id"].as_str().unwrap_or(""))
                .copied()
                .unwrap_or(&Value::Null);
            let session = a
                .get("agent_session")
                .filter(|v| v.is_object() && !v.as_object().unwrap().is_empty())
                .unwrap_or(&p["agent_session"]);
            Some(Agent {
                pane_id: pid.into(),
                name: clean(&first(&[&a["name"], &Value::String(kind.clone())])),
                title: clean(&first(&[
                    &tab["label"],
                    &a["terminal_title_stripped"],
                    &p["terminal_title_stripped"],
                    &a["title"],
                    &Value::String(pid.into()),
                ])),
                workspace: clean(&first(&[&space["label"], &a["workspace_id"]])),
                status: a["agent_status"].as_str().unwrap_or("unknown").into(),
                provider: first(&[&a["agent"], &p["agent"], &Value::String(kind.clone())]),
                kind: clean(&kind),
                session_kind: session["kind"].as_str().unwrap_or("").into(),
                session_ref: session["value"].as_str().unwrap_or("").into(),
                cwd: first(&[&a["foreground_cwd"], &a["cwd"], &p["cwd"]]),
                terminal_id: first(&[&a["terminal_id"], &p["terminal_id"]]),
                state_seq: a["state_change_seq"].as_u64().unwrap_or(0),
            })
        })
        .collect()
}
pub fn demo_state() -> State {
    let mut s: State = serde_json::from_str(include_str!("../assets/demo.json")).unwrap();
    let delta = now() - 1800000000.0;
    for m in s.metrics.values_mut() {
        for v in [&mut m.call_at, &mut m.started_at, &mut m.message_at]
            .into_iter()
            .flatten()
        {
            *v += delta;
        }
        m.seen_at += delta;
        m.status_since += delta;
        for c in &mut m.trail {
            if let Some(t) = &mut c.at {
                *t += delta;
            }
        }
        for c in &mut m.subagents {
            for t in [&mut c.started_at, &mut c.finished_at]
                .into_iter()
                .flatten()
            {
                *t += delta;
            }
        }
    }
    s
}

pub fn casefold(s: &str) -> String {
    static SPECIAL: std::sync::LazyLock<HashMap<char, String>> = std::sync::LazyLock::new(|| {
        serde_json::from_str(include_str!("../assets/casefold.json")).unwrap()
    });
    let mut result = String::with_capacity(s.len());
    for c in s.chars() {
        if let Some(s) = SPECIAL.get(&c) {
            result.push_str(s);
        } else {
            result.extend(c.to_lowercase());
        }
    }
    result
}
