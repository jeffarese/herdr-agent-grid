use crate::costs;
use crate::model::*;
use crate::visuals::*;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
pub struct Draw {
    pub x: usize,
    pub y: usize,
    pub text: String,
    pub style: String,
}
struct Painter {
    width: usize,
    height: usize,
    commands: Vec<Draw>,
}
impl Painter {
    fn put(
        &mut self,
        x: usize,
        y: usize,
        text: impl AsRef<str>,
        style: impl AsRef<str>,
        limit: usize,
    ) {
        if x < self.width && y < self.height {
            self.commands.push(Draw {
                x,
                y,
                text: clip(text.as_ref(), (self.width - x).min(limit), true),
                style: style.as_ref().into(),
            });
        }
    }
    fn inside(&mut self, r: Rect, row: usize, text: impl AsRef<str>, style: impl AsRef<str>) {
        if row > 0 && row < r.height - 1 {
            self.put(r.x + 2, r.y + row, text, style, r.width - 4);
        }
    }
}
fn status(s: &str) -> &str {
    if matches!(s, "working" | "blocked" | "done" | "idle" | "unknown") {
        s
    } else {
        "unknown"
    }
}
pub fn children_summary(children: &[Subagent]) -> String {
    let working = children.iter().filter(|c| c.status == "working").count();
    let unknown = children.iter().filter(|c| c.status == "unknown").count();
    format!(
        "{working} working / {} total{}",
        children.len(),
        if unknown > 0 {
            format!(" · {unknown} unknown")
        } else {
            String::new()
        }
    )
}
fn child_style(child: &Subagent) -> (&str, &str) {
    match child.status.as_str() {
        "working" => ("●", "working"),
        "done" => ("✓", "done"),
        "failed" => ("!", "failed"),
        _ => ("?", "muted"),
    }
}
fn completed_or_stale(status: &str) -> bool {
    matches!(status, "done" | "completed" | "idle" | "stale")
}
fn child_row(values: [&str; 4], width: usize) -> String {
    let rest = width.saturating_sub(24).max(2);
    let model = (rest / 2).clamp(1, 26);
    let sizes = [rest - model, model, 10, 8];
    values
        .iter()
        .zip(sizes)
        .map(|(v, n)| padded(v, n))
        .collect::<Vec<_>>()
        .join("  ")
}
pub fn children_lines(
    children: &[Subagent],
    width: usize,
    space: usize,
    now: f64,
    mut offset: usize,
    scrolling: bool,
) -> (Vec<(String, String)>, usize, usize) {
    if space == 0 {
        return (vec![], 0, 0);
    }
    if width < 64 && space == 2 && !children.is_empty() {
        offset = if scrolling {
            offset.min(children.len() - 1)
        } else {
            0
        };
        let child = &children[offset];
        let (glyph, style) = child_style(child);
        let count = format!(" · subagent {}/{}", offset + 1, children.len());
        let name = format!(
            "{glyph} {}{count}",
            clip(
                &child.name,
                width.saturating_sub(2 + count.chars().count()).max(1),
                true
            )
        );
        let suffix = format!(
            " · {} · {}",
            cost(
                child.cost,
                child.estimated_cost,
                child.cost_partial,
                child.estimate_partial
            ),
            child_time(child, now)
        );
        let model = format!(
            "  {}",
            clip(
                &model_effort(&child.model, &child.effort),
                width.saturating_sub(2 + suffix.chars().count()).max(1),
                true
            )
        );
        return (
            vec![(name, style.into()), (model + &suffix, "muted".into())],
            1,
            offset,
        );
    }
    let wide = width >= 64;
    let headers = if wide && space >= 4 { 2 } else { 1 };
    let per_child = if wide || space <= 3 { 1 } else { 2 };
    let mut capacity = space.saturating_sub(headers) / per_child;
    let overflow = children.len() > capacity;
    let footer = overflow && space > headers + per_child;
    if footer {
        capacity = (space - headers - 1) / per_child;
        capacity = capacity.max(1);
    }
    offset = if scrolling {
        offset.min(children.len().saturating_sub(capacity))
    } else {
        0
    };
    let shown = &children[offset..(offset + capacity).min(children.len())];
    let header = if overflow && !footer {
        format!("SUBAGENTS {}/{} · z details", shown.len(), children.len())
    } else {
        format!("SUBAGENTS  {}", children_summary(children))
    };
    let mut lines = vec![(header, "accent".into())];
    if headers == 2 {
        lines.push((
            child_row(["NAME", "MODEL@EFFORT", "API COST", "TIME"], width),
            "muted".into(),
        ));
    }
    for c in shown {
        let (glyph, style) = child_style(c);
        let name = format!("{glyph} {}", c.name);
        let model = model_effort(&c.model, &c.effort);
        let cost = cost(c.cost, c.estimated_cost, c.cost_partial, c.estimate_partial);
        let elapsed = child_time(c, now);
        if per_child == 1 {
            lines.push((
                child_row([&name, &model, &cost, &elapsed], width),
                style.into(),
            ));
        } else {
            lines.push((name, style.into()));
            let suffix = format!(" · {cost} · {elapsed}");
            lines.push((
                format!(
                    "  {}{suffix}",
                    clip(
                        &model,
                        width.saturating_sub(2 + suffix.chars().count()).max(1),
                        true
                    )
                ),
                "muted".into(),
            ));
        }
    }
    if footer {
        lines.push((
            if scrolling {
                format!(
                    "{}–{} / {} · PgUp/PgDn scroll",
                    offset + 1,
                    offset + shown.len(),
                    children.len()
                )
            } else {
                format!("+{} more · z details", children.len() - shown.len())
            },
            "muted".into(),
        ));
    }
    (lines, capacity, offset)
}
fn wrap_cells(text: &str, width: usize) -> Vec<String> {
    let width = width.max(1);
    let mut lines = vec![];
    let mut line = String::new();
    let mut used = 0;
    for word in text.split_whitespace() {
        if !line.is_empty() {
            if used + 1 + crate::model::width(word) > width {
                lines.push(std::mem::take(&mut line));
                used = 0;
            } else {
                line.push(' ');
                used += 1;
            }
        }
        for c in word.chars() {
            let size = crate::model::width(&c.to_string());
            let (c, size) = if size > width { ('…', 1) } else { (c, size) };
            if used + size > width {
                lines.push(std::mem::take(&mut line));
                used = 0;
            }
            if size > 0 || !line.is_empty() {
                line.push(c);
                used += size;
            }
        }
    }
    if !line.is_empty() {
        lines.push(line);
    }
    lines
}

#[derive(Default)]
pub struct View {
    pub selected: String,
    pub query: String,
    pub searching: bool,
    pub zoom: bool,
    pub hide_completed: bool,
    pub completed_button: Option<Rect>,
    pub message: String,
    pub items: Vec<usize>,
    pub visible: Vec<usize>,
    pub geometry: Layout,
    pub page: usize,
    pub page_count: usize,
    pub top: usize,
    pub motion: bool,
    pub icons: String,
    pub child_offsets: HashMap<String, usize>,
    pub child_capacity: usize,
    pub child_count: usize,
    indices: HashMap<String, usize>,
    inventory_key: Option<(u64, String, bool)>,
    arrangement_key: Option<(u64, usize, usize, String, String, bool, bool)>,
    overview_revision: Option<u64>,
    overview_value: (HashMap<String, usize>, String),
}
impl View {
    pub fn new(motion: bool) -> Self {
        Self {
            motion,
            ..Self::default()
        }
    }
    pub fn index(&self) -> usize {
        *self.indices.get(&self.selected).unwrap_or(&0)
    }
    pub fn move_by(&mut self, state: &State, delta: isize, wrap: bool) {
        if self.items.is_empty() {
            return;
        }
        let raw = self.index() as isize + delta;
        let index = if wrap {
            raw.rem_euclid(self.items.len() as isize)
        } else {
            raw.clamp(0, self.items.len() as isize - 1)
        } as usize;
        self.selected = state.agents[self.items[index]].pane_id.clone();
    }
    pub fn child_offset(&self) -> usize {
        *self.child_offsets.get(&self.selected).unwrap_or(&0)
    }
    pub fn toggle_completed(&mut self) {
        self.hide_completed = !self.hide_completed;
        self.child_offsets.clear();
    }
    pub fn click_controls(&mut self, x: usize, y: usize) -> bool {
        if self
            .completed_button
            .is_some_and(|r| x >= r.x && x < r.x + r.width && y == r.y)
        {
            self.toggle_completed();
            true
        } else {
            false
        }
    }
    pub fn scroll_children(&mut self, delta: isize) -> bool {
        if !self.zoom || self.child_count == 0 || self.child_capacity == 0 {
            return false;
        }
        let index = (self.child_offset() as isize + delta).clamp(
            0,
            self.child_count.saturating_sub(self.child_capacity) as isize,
        );
        self.child_offsets
            .insert(self.selected.clone(), index as usize);
        true
    }
    pub fn arrange(&mut self, state: &State, width: usize, height: usize) {
        let key = (
            state.revision,
            width,
            height,
            self.selected.clone(),
            self.query.clone(),
            self.zoom,
            self.hide_completed,
        );
        if state.revision != 0 && self.arrangement_key.as_ref() == Some(&key) {
            return;
        }
        let query = casefold(&self.query);
        let inv = (state.revision, query.clone(), self.hide_completed);
        if state.revision == 0 || self.inventory_key.as_ref() != Some(&inv) {
            self.items = state
                .agents
                .iter()
                .enumerate()
                .filter(|(_, a)| {
                    !self.hide_completed
                        || !completed_or_stale(&a.status)
                        || state.metrics.get(&a.pane_id).is_some_and(|m| {
                            m.subagents.iter().any(|c| !completed_or_stale(&c.status))
                        })
                })
                .filter(|(_, a)| {
                    query.is_empty()
                        || casefold(&format!(
                            "{} {} {} {} {} {}",
                            a.name,
                            a.kind,
                            a.title,
                            a.workspace,
                            a.status,
                            state
                                .metrics
                                .get(&a.pane_id)
                                .map(|m| m.last_call.as_str())
                                .unwrap_or("")
                        ))
                        .contains(&query)
                })
                .map(|(i, _)| i)
                .collect();
            self.items
                .sort_by_key(|i| state.agents[*i].status != "working");
            self.indices = self
                .items
                .iter()
                .enumerate()
                .map(|(i, n)| (state.agents[*n].pane_id.clone(), i))
                .collect();
            self.inventory_key = Some(inv);
        }
        if !self.indices.contains_key(&self.selected) {
            self.selected = self
                .items
                .first()
                .map(|n| state.agents[*n].pane_id.clone())
                .unwrap_or_default();
        }
        self.top = if height >= 18 { 5 } else { 2 };
        let index = self.index();
        self.geometry = layout(
            width,
            height.saturating_sub(self.top + 2).max(1),
            if self.zoom && !self.items.is_empty() {
                1
            } else {
                self.items.len()
            },
        );
        self.page = index / self.geometry.capacity;
        self.page_count = self.items.len().div_ceil(self.geometry.capacity).max(1);
        let start = if self.zoom {
            index
        } else {
            self.page * self.geometry.capacity
        };
        let end =
            (start + if self.zoom { 1 } else { self.geometry.capacity }).min(self.items.len());
        self.visible = self.items[start..end].to_vec();
        self.arrangement_key = Some((
            state.revision,
            width,
            height,
            self.selected.clone(),
            self.query.clone(),
            self.zoom,
            self.hide_completed,
        ));
    }
    pub fn animating(&self, state: &State) -> bool {
        if self.zoom
            && self.geometry.rects.first().is_some_and(|r| r.height < 17)
            && state
                .metrics
                .get(&self.selected)
                .is_some_and(|m| !m.subagents.is_empty())
        {
            return false;
        }
        self.motion
            && self.visible.iter().zip(&self.geometry.rects).any(|(i, r)| {
                state.agents[*i].status == "working" && r.height >= 12 && r.width >= 4
            })
    }
    fn overview(&mut self, state: &State) -> (HashMap<String, usize>, String) {
        if state.revision != 0 && self.overview_revision == Some(state.revision) {
            return self.overview_value.clone();
        }
        let mut counts = HashMap::from([
            ("working".into(), 0),
            ("blocked".into(), 0),
            ("done".into(), 0),
            ("idle".into(), 0),
            ("unknown".into(), 0),
        ]);
        let mut seen = HashSet::new();
        let mut spaces = HashSet::new();
        let mut reported = vec![];
        for a in &state.agents {
            *counts.get_mut(status(&a.status)).unwrap() += 1;
            spaces.insert(&a.workspace);
            let metrics = state.metrics.get(&a.pane_id);
            let key = costs::session_key(a, metrics);
            if seen.insert(key.clone()) {
                reported.push((key, metrics));
            }
        }
        let (total_cost, covered) = costs::overview(&reported);
        let ts: Vec<_> = reported
            .iter()
            .filter_map(|(_, m)| *m)
            .filter(|m| m.tokens.is_some())
            .collect();
        let aggregate = Metrics {
            tokens: if ts.is_empty() {
                None
            } else {
                Some(ts.iter().map(|m| m.tokens.unwrap()).sum())
            },
            tokens_partial: ts.iter().any(|m| m.tokens_partial),
            ..Metrics::default()
        };
        let summary = format!(
            "{} agents · {} workspaces   API cost {} ({}/{} {})   Tokens {} ({}/{} reported)",
            state.agents.len(),
            spaces.len(),
            if total_cost.amount.is_none() {
                "unavailable".into()
            } else {
                total_cost.label()
            },
            covered,
            reported.len(),
            "covered",
            tokens(&aggregate),
            ts.len(),
            reported.len()
        );
        self.overview_revision = Some(state.revision);
        self.overview_value = (counts.clone(), summary.clone());
        (counts, summary)
    }
    pub fn draw(
        &mut self,
        state: &State,
        width: usize,
        height: usize,
        now: f64,
        tick: f64,
    ) -> Vec<Draw> {
        self.arrange(state, width, height);
        self.child_capacity = 0;
        self.child_count = 0;
        self.completed_button = None;
        let mut p = Painter {
            width,
            height,
            commands: Vec::with_capacity(250),
        };
        let (counts, summary) = self.overview(state);
        let context = if !self.query.is_empty() {
            format!("/ {} of {}", self.items.len(), state.agents.len())
        } else if self.zoom {
            "/ agent details".into()
        } else if self.page_count > 1 {
            format!("/ page {} of {}", self.page + 1, self.page_count)
        } else {
            String::new()
        };
        p.put(
            0,
            0,
            if width >= 55 {
                "  ◆ AGENT GRID"
            } else {
                " ◆ GRID"
            },
            "brand",
            if width >= 55 { 18 } else { 9 },
        );
        let connection = if !state.error.is_empty() {
            "OFFLINE"
        } else if state.updated != 0.0 {
            "LIVE"
        } else {
            "CONNECTING"
        };
        if width >= 55 {
            p.put(
                width - connection.len() - 3,
                0,
                format!("● {connection}"),
                if !state.error.is_empty() {
                    "blocked"
                } else if state.updated != 0.0 {
                    "done"
                } else {
                    "muted"
                },
                width,
            );
        }
        let active = counts["working"];
        let bx = if width >= 55 { 19 } else { 10 };
        let bw = width.saturating_sub(bx + if width >= 55 { connection.len() + 6 } else { 1 });
        let active_badge = if width >= 55 {
            format!(" {active} ACTIVE / {} TOTAL ", state.agents.len())
        } else {
            format!(" {active} ACTIVE ")
        };
        p.put(
            bx,
            0,
            &active_badge,
            if active > 0 {
                "selection:working"
            } else {
                "selection:idle"
            },
            bw,
        );
        if !context.is_empty() && width >= 90 {
            let cx = bx + active_badge.len() + 2;
            p.put(
                cx,
                0,
                context,
                "muted",
                width.saturating_sub(cx + connection.len() + 6),
            );
        }
        let mut x = 2;
        for (s, label) in [
            ("working", "working"),
            ("blocked", "need input"),
            ("done", "done"),
            ("idle", "idle"),
            ("unknown", "unknown"),
        ] {
            if counts[s] > 0 {
                let v = format!("{} {label}", counts[s]);
                p.put(x, 1, &v, s, width);
                x += v.len() + 4;
            }
        }
        if state.agents.is_empty() {
            p.put(
                2,
                1,
                if state.updated != 0.0 {
                    "No active agents"
                } else {
                    "Waiting for Herdr…"
                },
                "muted",
                width,
            );
        }
        if self.top == 5 {
            p.put(2, 2, summary, "muted", width);
            p.put(
                2,
                3,
                if state.error.is_empty() {
                    "API token cost  ·  ~ estimated  ·  ≥ partial  ·  — unavailable".into()
                } else {
                    format!("Connection unavailable · {}", state.error)
                },
                if state.error.is_empty() {
                    "muted"
                } else {
                    "blocked"
                },
                width,
            );
            let label = if self.hide_completed {
                "[Show completed/stale]"
            } else {
                "[Hide completed/stale]"
            };
            if width >= label.len() + 4 {
                p.put(2, 4, label, "selection:unknown", label.len());
                self.completed_button = Some(Rect {
                    x: 2,
                    y: 4,
                    width: label.len(),
                    height: 1,
                });
                p.put(
                    label.len() + 4,
                    4,
                    format!(
                        "{} / {} agents shown · d toggle",
                        self.items.len(),
                        state.agents.len()
                    ),
                    "muted",
                    width.saturating_sub(label.len() + 4),
                );
            }
        }
        if width < 12 || height < 7 {
            p.put(
                0,
                2.min(height.saturating_sub(1)),
                "Enlarge terminal",
                "muted",
                width,
            );
            return p.commands;
        }
        if self.items.is_empty() {
            p.put(
                2,
                (self.top + 1).max(height / 2),
                if self.hide_completed && self.query.is_empty() {
                    "No active agents · Show completed/stale to restore cards"
                } else if self.query.is_empty() {
                    "Waiting for agents in this session"
                } else {
                    "No agents match this filter"
                },
                "muted",
                width,
            );
        }
        let empty = Metrics::default();
        for (i, original) in self.visible.iter().zip(&self.geometry.rects) {
            let a = &state.agents[*i];
            let r = Rect {
                y: original.y + self.top,
                ..*original
            };
            let (x, y, w, h) = (r.x, r.y, r.width, r.height);
            if w < 4 || h < 4 {
                continue;
            }
            let selected = a.pane_id == self.selected;
            let m = state.metrics.get(&a.pane_id).unwrap_or(&empty);
            let shown_children: std::borrow::Cow<'_, [Subagent]> = if self.hide_completed {
                std::borrow::Cow::Owned(
                    m.subagents
                        .iter()
                        .filter(|c| !completed_or_stale(&c.status))
                        .cloned()
                        .collect(),
                )
            } else {
                std::borrow::Cow::Borrowed(&m.subagents)
            };
            let phase = phase(&a.status, m);
            let badge = badge(phase);
            let ss = status(&a.status);
            let border = if selected {
                format!("focus:{ss}")
            } else if matches!(a.status.as_str(), "working" | "blocked" | "done") {
                a.status.clone()
            } else {
                "border".into()
            };
            let settled = matches!(a.status.as_str(), "done" | "idle") && !selected;
            let (tl, hor, tr, bl, br, ver) = if selected {
                ("╔", "═", "╗", "╚", "╝", "║")
            } else {
                ("╭", "─", "╮", "╰", "╯", "│")
            };
            p.put(x, y, format!("{tl}{}{tr}", hor.repeat(w - 2)), &border, w);
            p.put(
                x,
                y + h - 1,
                format!("{bl}{}{br}", hor.repeat(w - 2)),
                &border,
                w,
            );
            for row in 1..h - 1 {
                p.put(x, y + row, ver, &border, 1);
                p.put(x + w - 1, y + row, ver, &border, 1);
            }
            let number = self.indices[&a.pane_id] + 1;
            if selected {
                let tag = if w >= 30 { " SELECTED " } else { "" };
                let title =
                    padded(&format!(" ▶ {number:02}  {} ", a.name), w - 4 - tag.len()) + tag;
                p.put(x + 2, y, title, format!("selection:{ss}"), w - 4);
            } else {
                p.put(
                    x + 2,
                    y,
                    format!("   {number:02}  {} ", a.name),
                    if matches!(a.status.as_str(), "working" | "blocked" | "done") {
                        &border
                    } else {
                        "title"
                    },
                    w - 5,
                );
            }
            let harness = harness(&a.kind);
            p.put(
                x + 2,
                y + 1,
                crate::visuals::icon(&harness, &self.icons),
                format!("harness:{harness}"),
                1,
            );
            p.put(
                x + 4,
                y + 1,
                model_effort(&m.model, &m.effort),
                "title",
                w - 6,
            );
            p.inside(r, 2, &a.title, if settled { "settled" } else { "title" });
            let age = duration(m.started_at.map(|t| now - t));
            let last_age = duration(m.call_at.map(|t| now - t));
            let mut call = if m.last_call.is_empty() {
                "Not available".into()
            } else {
                m.last_call.clone()
            };
            if let Some(done) = m.call_done {
                call += if done {
                    "  ·  returned"
                } else {
                    "  ·  called"
                };
            }
            let msg = if m.last_message.is_empty() {
                "Message unavailable".into()
            } else {
                format!("» {}", m.last_message)
            };
            if self.zoom && !m.subagents.is_empty() && h < 17 {
                p.inside(
                    r,
                    3,
                    format!("{age} · {} · {} tokens", cost_label(m), tokens(m)),
                    "muted",
                );
                p.inside(r, 4, format!("{badge} · {}", a.workspace), ss);
            } else if h >= 12 {
                let blen = badge.chars().count();
                p.put(
                    x + 2,
                    y + 3,
                    &a.workspace,
                    "muted",
                    w.saturating_sub(blen + 6).max(1),
                );
                if w > blen + 8 {
                    p.put(x + w - blen - 2, y + 3, badge, ss, blen);
                }
                for (cx, cy, text, style) in core_runs(
                    phase,
                    w - 4,
                    if h >= 14 { 2 } else { 1 },
                    tick,
                    &a.pane_id,
                    self.motion,
                ) {
                    p.commands.push(Draw {
                        x: x + 2 + cx,
                        y: y + 4 + cy,
                        text,
                        style,
                    });
                }
                let cr = if h >= 14 { 6 } else { 5 };
                let latest = if m.last_call.is_empty() {
                    "Latest call —".into()
                } else {
                    format!("▸ {call}")
                };
                let right = if m.call_at.is_none() {
                    String::new()
                } else if m.call_source == "screen" {
                    format!("seen {last_age}")
                } else {
                    format!("{last_age} ago")
                };
                let len = right.chars().count();
                p.put(
                    x + 2,
                    y + cr,
                    latest,
                    if settled {
                        "settled"
                    } else if !m.last_call.is_empty() {
                        "accent"
                    } else {
                        "muted"
                    },
                    w.saturating_sub(4 + len + if right.is_empty() { 0 } else { 2 }),
                );
                if w >= 40 {
                    p.put(x + w - len - 2, y + cr, right, "muted", len);
                } else if h < 14 {
                    p.inside(
                        r,
                        6,
                        if m.call_at.is_some() {
                            format!("Last call {last_age}")
                        } else {
                            String::new()
                        },
                        "muted",
                    );
                }
                p.inside(r, 7, &msg, if settled { "settled" } else { "normal" });
                if !m.subagents.is_empty() && h < 14 {
                    p.inside(
                        r,
                        6,
                        format!(
                            "↳ {} subagent{} · z details",
                            m.subagents.len(),
                            if m.subagents.len() != 1 { "s" } else { "" }
                        ),
                        "accent",
                    );
                }
                p.put(x + 1, y + 8, "─".repeat(w - 2), "border", w - 2);
                let column = (w - 4) / 3;
                for (off, label, value) in [
                    (0, "SESSION", age.clone()),
                    (
                        column,
                        if m.subagents.is_empty() || column < 15 {
                            "TOKENS"
                        } else {
                            "SESSION TOKENS"
                        },
                        tokens(m),
                    ),
                    (
                        column * 2,
                        if !m.subagents.is_empty() {
                            if m.cost.is_some() {
                                "REPORTED COST"
                            } else {
                                "COMBINED COST"
                            }
                        } else if costs::total(m).estimated {
                            "EST. COST"
                        } else {
                            "API COST"
                        },
                        if costs::total(m).amount.is_some() {
                            cost_label(m)
                        } else {
                            "Unavailable".into()
                        },
                    ),
                ] {
                    p.put(x + 2 + off, y + 9, label, "muted", column);
                    p.put(
                        x + 2 + off,
                        y + 10,
                        value,
                        if settled { "settled" } else { "metric" },
                        column,
                    );
                }
                if h >= 14 {
                    if !m.subagents.is_empty() {
                        p.inside(r, 11, costs::breakdown(m), "muted");
                        if !self.zoom || h < 17 {
                            let (lines, _, _) = children_lines(
                                &shown_children,
                                w - 4,
                                h.saturating_sub(13 + usize::from(!m.issue.is_empty())),
                                now,
                                0,
                                false,
                            );
                            for (row, (text, style)) in lines.iter().enumerate() {
                                p.inside(r, row + 12, text, style);
                            }
                        }
                    } else {
                        p.inside(r, 12, "TRAIL", "muted");
                        if m.trail.is_empty() {
                            p.put(x + 8, y + 12, "—", "muted", 1);
                        } else {
                            let take = ((w.saturating_sub(12)) / 2).max(1);
                            for (off, entry) in m
                                .trail
                                .iter()
                                .skip(m.trail.len().saturating_sub(take))
                                .enumerate()
                            {
                                let (glyph, style) = tool_glyph(&entry.name);
                                p.put(
                                    x + 8 + off * 2,
                                    y + 12,
                                    glyph,
                                    if entry.error { "failed" } else { style },
                                    1,
                                );
                            }
                        }
                    }
                    if !m.issue.is_empty() {
                        p.inside(r, h - 2, format!("! {}", m.issue), "blocked");
                    }
                }
            } else {
                p.inside(
                    r,
                    3,
                    format!("Last  {call}"),
                    if m.last_call.is_empty() {
                        "muted"
                    } else {
                        "accent"
                    },
                );
                p.inside(
                    r,
                    4,
                    format!(
                        "Time  {age}  ·  {} {last_age}",
                        if m.call_source == "screen" {
                            "Seen"
                        } else {
                            "Call"
                        }
                    ),
                    "muted",
                );
                p.inside(
                    r,
                    5,
                    format!(
                        "API   {}  ·  {} tokens",
                        if costs::total(m).amount.is_some() {
                            cost_label(m)
                        } else {
                            "Unavailable".into()
                        },
                        tokens(m)
                    ),
                    "metric",
                );
                let blen = badge.chars().count();
                p.put(x + 2, y + 6, badge, ss, blen);
                if w > blen + 8 {
                    let detail = if !m.subagents.is_empty() {
                        format!(
                            "↳ {} subagent{} · z",
                            m.subagents.len(),
                            if m.subagents.len() != 1 { "s" } else { "" }
                        )
                    } else if h >= 9 {
                        a.workspace.clone()
                    } else {
                        msg.clone()
                    };
                    p.put(x + blen + 4, y + 6, detail, "muted", w - blen - 6);
                }
                p.inside(r, 7, &msg, "muted");
            }
            if self.zoom && !m.subagents.is_empty() && h >= 8 {
                let last = h - 2 - usize::from(!m.issue.is_empty());
                let reserve = if h >= 22 { 3 } else { 0 };
                let start = if h >= 17 { 12 } else { 5 };
                let (lines, capacity, offset) = children_lines(
                    &shown_children,
                    w - 4,
                    (last - reserve + 1).saturating_sub(start),
                    now,
                    self.child_offset(),
                    true,
                );
                self.child_offsets.insert(a.pane_id.clone(), offset);
                self.child_capacity = capacity;
                self.child_count = shown_children.len();
                for (row, (text, style)) in lines.iter().enumerate() {
                    p.inside(r, start + row, text, style);
                }
                let mut row = start + lines.len();
                let mut details = vec![
                    ("DETAILS".into(), "muted"),
                    (
                        format!(
                            "Metrics source   {}",
                            if m.source.is_empty() {
                                "Herdr status"
                            } else {
                                &m.source
                            }
                        ),
                        "muted",
                    ),
                ];
                if !m.call_detail.is_empty() {
                    details.push((format!("Tool target      {}", m.call_detail), "normal"));
                }
                details.extend([
                    (format!("API cost         {}", cost_detail(m)), "muted"),
                    (
                        if m.tokens_partial {
                            "Token coverage   Partial transcript · lower bound"
                        } else {
                            "Token count      Includes input, output and cached input"
                        }
                        .into(),
                        "muted",
                    ),
                    (
                        if costs::total(m).partial && m.cost.is_none() {
                            "Cost coverage    Partial usage/model history · lower bound".into()
                        } else {
                            format!(
                                "Model / effort   {}@{}",
                                if m.model.is_empty() {
                                    "Not reported"
                                } else {
                                    &m.model
                                },
                                if m.effort.is_empty() { "?" } else { &m.effort }
                            )
                        },
                        "muted",
                    ),
                    (
                        format!(
                            "Latest call      {}",
                            if m.call_source == "screen" {
                                "Terminal observation"
                            } else if !m.call_source.is_empty() {
                                "Session transcript"
                            } else {
                                "Not reported"
                            }
                        ),
                        "muted",
                    ),
                ]);
                for (text, style) in details
                    .iter()
                    .take((last - reserve + 1).saturating_sub(row))
                {
                    p.inside(r, row, text, *style);
                    row += 1;
                }
                if reserve > 0 {
                    p.inside(
                        r,
                        row,
                        format!(
                            "LATEST ASSISTANT MESSAGE  {}",
                            m.message_at
                                .map(|t| format!("{} ago", duration(Some(now - t))))
                                .unwrap_or_default()
                        ),
                        "muted",
                    );
                    let lines = wrap_cells(
                        if m.last_message.is_empty() {
                            "Message unavailable"
                        } else {
                            &m.last_message
                        },
                        w - 4,
                    );
                    let available = 6.min(last.saturating_sub(row));
                    for (idx, line) in lines.iter().take(available).enumerate() {
                        p.inside(
                            r,
                            row + 1 + idx,
                            if idx == available - 1 && lines.len() > available {
                                clip(line, w - 5, false) + "…"
                            } else {
                                line.clone()
                            },
                            "normal",
                        );
                    }
                }
            } else if self.zoom && h >= 23 {
                p.inside(r, 14, "DETAILS", "muted");
                let observed = if m.status_since != 0.0 {
                    duration(Some(now - m.status_since))
                } else {
                    "—".into()
                };
                p.inside(
                    r,
                    15,
                    format!("Status observed  ≥{observed}   ·   {}", a.pane_id),
                    "normal",
                );
                p.inside(
                    r,
                    16,
                    format!(
                        "Model / effort   {}@{}",
                        if m.model.is_empty() {
                            "Not reported"
                        } else {
                            &m.model
                        },
                        if m.effort.is_empty() { "?" } else { &m.effort }
                    ),
                    "normal",
                );
                p.inside(
                    r,
                    17,
                    format!(
                        "Metrics source   {}",
                        if m.source.is_empty() {
                            "Herdr status"
                        } else {
                            &m.source
                        }
                    ),
                    "normal",
                );
                p.inside(
                    r,
                    18,
                    format!(
                        "Latest call      {}",
                        if m.call_source == "screen" {
                            "Terminal observation"
                        } else if !m.call_source.is_empty() {
                            "Session transcript"
                        } else {
                            "Not reported"
                        }
                    ),
                    "normal",
                );
                let extra = usize::from(!m.call_detail.is_empty() && h >= 28);
                if extra > 0 {
                    p.inside(
                        r,
                        19,
                        format!("Tool target      {}", m.call_detail),
                        "normal",
                    );
                }
                p.inside(
                    r,
                    19 + extra,
                    format!("API cost         {}", cost_detail(m)),
                    "muted",
                );
                p.inside(
                    r,
                    20 + extra,
                    "Token count      Includes input, output and cached input",
                    "muted",
                );
                if m.tokens_partial {
                    p.inside(
                        r,
                        21 + extra,
                        "Token coverage   Partial transcript · shown as a lower bound",
                        "muted",
                    );
                } else if m.estimate_partial && m.cost.is_none() {
                    p.inside(
                        r,
                        21 + extra,
                        "Cost coverage    Partial usage/model history · shown as a lower bound",
                        "muted",
                    );
                }
                if h >= 27 {
                    let mr = 22 + extra;
                    p.inside(
                        r,
                        mr,
                        format!(
                            "LATEST ASSISTANT MESSAGE  {}",
                            m.message_at
                                .map(|t| format!("{} ago", duration(Some(now - t))))
                                .unwrap_or_default()
                        ),
                        "muted",
                    );
                    let lines = wrap_cells(
                        if m.last_message.is_empty() {
                            "Message unavailable"
                        } else {
                            &m.last_message
                        },
                        w - 4,
                    );
                    let available = 6.min(h.saturating_sub(mr + 2));
                    for (row, line) in lines.iter().take(available).enumerate() {
                        p.inside(
                            r,
                            mr + 1 + row,
                            if row == available - 1 && lines.len() > available {
                                clip(line, w - 5, false) + "…"
                            } else {
                                line.clone()
                            },
                            "normal",
                        );
                    }
                }
            }
        }
        if self.searching || !self.query.is_empty() {
            p.put(
                0,
                height - 2,
                format!(
                    "  / {}{}",
                    self.query,
                    if self.searching { "▏" } else { "" }
                ),
                "selected",
                width,
            );
        } else if !self.message.is_empty() {
            p.put(
                0,
                height - 2,
                format!("  {}", self.message),
                "blocked",
                width,
            );
        } else if let Some(i) = self.items.get(self.index()) {
            let a = &state.agents[*i];
            let m = state.metrics.get(&a.pane_id).unwrap_or(&empty);
            let detail = if !m.issue.is_empty() {
                &m.issue
            } else if state.errors.contains_key(&a.pane_id) {
                "Activity read unavailable"
            } else if !m.source.is_empty() {
                &m.source
            } else {
                "Herdr status"
            };
            let children = if m.subagents.is_empty() {
                String::new()
            } else {
                format!(
                    " · subagents {} · z details",
                    children_summary(&m.subagents)
                )
            };
            p.put(
                0,
                height - 2,
                format!("  {}{children} · {}  /  {detail}", a.name, a.workspace),
                "muted",
                width,
            );
        }
        let mut legend =
            "  ↑↓←→ select   Enter/click open   z details   / filter   r refresh   Esc close";
        if self.page_count > 1 && !self.zoom {
            legend = "  Arrows select  Enter/click open  PgUp/PgDn pages  z details  / filter  Esc close";
        }
        if self.searching {
            legend = "  Type to filter   Enter finish   Esc clear filter";
        } else if self.zoom && self.child_count > 0 {
            legend = "  PgUp/PgDn scroll subagents   ←→ agent   Enter open   z grid   / filter   Esc back";
        }
        p.put(0, height - 1, legend, "muted", width);
        p.commands
    }
}
