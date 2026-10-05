//! Cost rollups keep raw session metrics intact and deduplicate transcript identities.
use crate::model::{Agent, Metrics, Subagent, cost};
use std::collections::{HashMap, HashSet};

#[derive(Clone, Copy, Default, Debug, PartialEq)]
pub struct Cost {
    pub amount: Option<f64>,
    pub estimated: bool,
    pub partial: bool,
}
impl Cost {
    pub fn label(self) -> String {
        cost(
            self.amount.filter(|_| !self.estimated),
            self.amount.filter(|_| self.estimated),
            self.partial,
            self.partial,
        )
    }
    fn add(&mut self, other: Self) {
        if let Some(value) = other.amount {
            self.amount = Some(self.amount.unwrap_or(0.0) + value);
            self.estimated |= other.estimated;
        }
        self.partial |= other.partial || other.amount.is_none();
    }
}
pub fn own(m: &Metrics) -> Cost {
    Cost {
        amount: m.cost.or(m.estimated_cost),
        estimated: m.cost.is_none() && m.estimated_cost.is_some(),
        partial: if m.cost.is_some() {
            m.cost_partial
        } else {
            m.estimate_partial
        },
    }
}
fn child(c: &Subagent) -> Cost {
    Cost {
        amount: c.cost.or(c.estimated_cost),
        estimated: c.cost.is_none() && c.estimated_cost.is_some(),
        partial: if c.cost.is_some() {
            c.cost_partial || !c.descendant_keys.is_empty()
        } else {
            c.estimate_partial
        },
    }
}
fn child_key(m: &Metrics, c: &Subagent, index: usize) -> String {
    if !c.session_key.is_empty() {
        c.session_key.clone()
    } else if !c.id.is_empty() {
        format!("{}:child:{}", m.session_key, c.id)
    } else {
        format!("{}:child-index:{index}", m.session_key)
    }
}
pub fn children(m: &Metrics) -> Cost {
    let mut result = Cost::default();
    let mut seen = HashSet::new();
    let included: HashSet<_> = m
        .subagents
        .iter()
        .filter(|c| c.cost.is_some())
        .flat_map(|c| c.descendant_keys.iter())
        .collect();
    for (i, c) in m.subagents.iter().enumerate() {
        if !included.contains(&c.session_key) && seen.insert(child_key(m, c, i)) {
            result.add(child(c));
        }
    }
    result
}
pub fn total(m: &Metrics) -> Cost {
    let mut result = own(m);
    if m.cost.is_some() {
        // Provider totals have no reliable child-inclusion marker. Keep the
        // reported amount, never add potentially overlapping child charges,
        // and disclose that coverage of delegated work is unverified.
        result.partial |= !m.subagents.is_empty();
    } else if !m.subagents.is_empty() {
        result.partial |= result.amount.is_none();
        result.add(children(m));
    }
    result
}
pub fn breakdown(m: &Metrics) -> String {
    if m.cost.is_some() {
        format!(
            "Reported {} · Subagents {} · child inclusion unverified; not added",
            own(m).label(),
            children(m).label()
        )
    } else {
        format!(
            "Own {} + Subagents {} = Combined {}",
            own(m).label(),
            children(m).label(),
            total(m).label()
        )
    }
}

pub fn session_key(a: &Agent, m: Option<&Metrics>) -> String {
    if let Some(m) = m.filter(|m| !m.session_key.is_empty()) {
        return m.session_key.clone();
    }
    if a.session_ref.is_empty() {
        format!("pane:{}:{}:{}", a.provider(), a.terminal_id, a.pane_id)
    } else {
        format!("{}:{}:{}", a.provider(), a.session_kind, a.session_ref)
    }
}

/// Sum unique sessions, omitting children underneath reported parent totals.
/// `covered` counts inventory sessions with a known value (including children).
pub fn overview(sessions: &[(String, Option<&Metrics>)]) -> (Cost, usize) {
    let mut entries = HashMap::new();
    let mut covered_by_report = HashSet::new();
    let mut covered = 0;
    for (key, metrics) in sessions {
        if let Some(m) = metrics {
            covered += usize::from(total(m).amount.is_some());
            for (i, c) in m.subagents.iter().enumerate() {
                if c.cost.is_some() {
                    covered_by_report.extend(c.descendant_keys.iter().cloned());
                }
                let ck = if c.session_key.is_empty() {
                    format!("{key}:{}", child_key(m, c, i))
                } else {
                    c.session_key.clone()
                };
                if m.cost.is_some() {
                    covered_by_report.insert(ck.clone());
                }
                entries.entry(ck).or_insert(child(c));
            }
        }
    }
    // Top-level observations take precedence over the same session's child row.
    for (key, metrics) in sessions {
        let value = metrics
            .map(|m| {
                let mut value = own(m);
                value.partial |= m.cost.is_some() && !m.subagents.is_empty();
                value
            })
            .unwrap_or_default();
        if value.amount.is_some() || !entries.contains_key(key) {
            entries.insert(key.clone(), value);
        }
    }
    let mut result = Cost::default();
    for (key, value) in entries {
        if !covered_by_report.contains(&key) {
            result.add(value);
        }
    }
    (result, covered)
}
