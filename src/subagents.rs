//! The subagents view: each subagent becomes a virtual agent card, so it is
//! drawn, selected and filtered by the same code as the agent grid.

use crate::model::{Agent, State};

/// Separates the parent pane from the subagent id in a virtual pane id.
const JOIN: char = '#';

pub fn virtual_id(parent: &str, child: &str) -> String {
    format!("{parent}{JOIN}{child}")
}

/// The real Herdr pane that owns a virtual subagent card.
pub fn parent_pane(id: &str) -> &str {
    id.rsplit_once(JOIN).map_or(id, |(parent, _)| parent)
}

/// Subagent lifecycle in the grid's status vocabulary; `blocked` styles a
/// failed subagent red and the subagents header labels it "failed".
fn status(child: &str) -> &'static str {
    match child {
        "working" => "working",
        "done" => "done",
        "failed" => "blocked",
        _ => "unknown",
    }
}

/// Subagents of `parent`, or of every agent when `parent` is empty.
pub fn state(source: &State, parent: &str) -> State {
    let mut out = State {
        error: source.error.clone(),
        updated: source.updated,
        revision: source.revision,
        ..State::default()
    };
    for a in &source.agents {
        if !parent.is_empty() && a.pane_id != parent {
            continue;
        }
        let Some(m) = source.metrics.get(&a.pane_id) else {
            continue;
        };
        let owner = if a.title.is_empty() {
            &a.name
        } else {
            &a.title
        };
        for c in &m.subagents {
            let id = virtual_id(&a.pane_id, &c.id);
            let mut metrics = c.metrics.as_deref().cloned().unwrap_or_default();
            metrics.session_key = c.session_key.clone();
            metrics.subagents.clear();
            if !c.model.is_empty() {
                metrics.model = c.model.clone();
            }
            if !c.effort.is_empty() {
                metrics.effort = c.effort.clone();
            }
            metrics.cost = c.cost;
            metrics.cost_partial = c.cost_partial;
            metrics.estimated_cost = c.estimated_cost;
            metrics.estimate_partial = c.estimate_partial;
            metrics.started_at = c.started_at.or(metrics.started_at);
            metrics.ended_at = c
                .finished_at
                .or_else(|| Some(c.started_at? + c.duration_s?))
                .filter(|_| matches!(c.status.as_str(), "done" | "failed"));
            if metrics.source.is_empty() {
                metrics.source = "Subagent transcript".into();
            }
            out.agents.push(Agent {
                pane_id: id.clone(),
                kind: a.kind.clone(),
                name: c.name.clone(),
                title: format!("↳ {owner}"),
                workspace: a.workspace.clone(),
                status: status(&c.status).into(),
                provider: a.provider.clone(),
                cwd: a.cwd.clone(),
                ..Agent::default()
            });
            out.metrics.insert(id, metrics);
        }
    }
    out
}

/// How many subagents an agent has, for the parent card and footer hints.
pub fn count(source: &State, parent: &str) -> usize {
    source.metrics.get(parent).map_or(0, |m| m.subagents.len())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Metrics, Subagent};

    #[test]
    fn subagents_become_cards_that_point_at_their_parent() {
        let mut source = State {
            revision: 3,
            ..State::default()
        };
        for pane in ["w:1", "w:2"] {
            source.agents.push(Agent {
                pane_id: pane.into(),
                kind: "claude".into(),
                name: "claude".into(),
                title: format!("Lead {pane}"),
                workspace: "team".into(),
                ..Agent::default()
            });
            source.metrics.insert(
                pane.into(),
                Metrics {
                    subagents: vec![
                        Subagent {
                            id: "a1".into(),
                            name: "p1-ts".into(),
                            status: "working".into(),
                            model: "claude-opus-5-5".into(),
                            metrics: Some(Box::new(Metrics {
                                last_call: "Bash".into(),
                                ..Metrics::default()
                            })),
                            ..Subagent::default()
                        },
                        Subagent {
                            id: "a2".into(),
                            name: "p2-inbox".into(),
                            status: "failed".into(),
                            started_at: Some(10.0),
                            finished_at: Some(70.0),
                            ..Subagent::default()
                        },
                    ],
                    ..Metrics::default()
                },
            );
        }
        let all = state(&source, "");
        assert_eq!(all.agents.len(), 4);
        assert_eq!(all.revision, 3);
        let one = state(&source, "w:2");
        assert_eq!(one.agents.len(), 2);
        let card = &one.agents[0];
        assert_eq!(card.name, "p1-ts");
        assert_eq!(card.title, "↳ Lead w:2");
        assert_eq!(parent_pane(&card.pane_id), "w:2");
        assert_eq!(parent_pane("w:2"), "w:2");
        let m = &one.metrics[&card.pane_id];
        assert_eq!(
            (m.last_call.as_str(), m.model.as_str()),
            ("Bash", "claude-opus-5-5")
        );
        assert_eq!(one.agents[1].status, "blocked");
        assert_eq!(one.metrics[&one.agents[1].pane_id].ended_at, Some(70.0));
        assert_eq!(count(&source, "w:1"), 2);
    }

    #[test]
    fn a_failed_subagent_card_says_failed_not_needs_input() {
        let source = State {
            agents: vec![Agent {
                pane_id: "w:1".into(),
                name: "claude".into(),
                status: "working".into(),
                ..Agent::default()
            }],
            metrics: [(
                "w:1".to_string(),
                Metrics {
                    subagents: vec![Subagent {
                        id: "x".into(),
                        name: "broken".into(),
                        status: "failed".into(),
                        ..Subagent::default()
                    }],
                    ..Metrics::default()
                },
            )]
            .into(),
            ..State::default()
        };
        let mut view = crate::view::View::new(false);
        view.selected = "w:1".into();
        view.enter_subagents(&source);
        let shown = state(&source, &view.scope);
        let text: String = view
            .draw(&shown, 120, 40, 0.0, 0.0)
            .iter()
            .map(|d| d.text.clone())
            .collect();
        assert!(
            text.contains("FAILED") && text.contains("1 failed"),
            "{text}"
        );
        assert!(!text.to_lowercase().contains("input"), "{text}");
    }
}
