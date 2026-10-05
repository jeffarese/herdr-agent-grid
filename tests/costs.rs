use herdr_agent_grid::{app::plain_frame, costs, model::*, view::View};

fn child(id: &str, amount: Option<f64>) -> Subagent {
    Subagent {
        id: id.into(),
        session_key: format!("claude:{id}"),
        estimated_cost: amount,
        ..Default::default()
    }
}
fn parent() -> Metrics {
    Metrics {
        session_key: "claude:parent".into(),
        estimated_cost: Some(13.27),
        estimate_partial: true,
        subagents: [
            3.00, 5.97, 4.17, 6.75, 3.51, 3.33, 2.59, 2.91, 1.07, 0.83, 1.23, 0.50, 0.91, 0.92,
            1.14, 0.51, 4.75,
        ]
        .into_iter()
        .enumerate()
        .map(|(i, c)| child(&i.to_string(), Some(c)))
        .collect(),
        ..Default::default()
    }
}
#[test]
fn screenshot_costs_include_all_seventeen_children_in_card_and_overview() {
    let m = parent();
    assert_eq!(costs::children(&m).label(), "~$44.09");
    assert_eq!(cost_label(&m), "≥~$57.36");
    let a = Agent {
        pane_id: "p1".into(),
        kind: "claude".into(),
        status: "working".into(),
        ..Default::default()
    };
    let state = State {
        agents: vec![a.clone()],
        metrics: [(a.pane_id.clone(), m)].into(),
        ..Default::default()
    };
    let mut view = View::new(false);
    view.zoom = true;
    let frame = plain_frame(&view.draw(&state, 160, 50, 0.0, 0.0), 160, 50);
    assert!(frame.contains("API cost ≥~$57.36 (1/1 covered)"));
    assert!(frame.contains("COMBINED COST"));
    assert!(frame.contains("Own ≥~$13.27 + Subagents ~$44.09 = Combined ≥~$57.36"));
}
#[test]
fn missing_usage_partial_children_and_zero_are_preserved() {
    let mut m = Metrics {
        estimated_cost: Some(1.0),
        subagents: vec![child("a", None)],
        ..Default::default()
    };
    assert_eq!(cost_label(&m), "≥~$1.00");
    m.estimated_cost = None;
    assert_eq!(cost_label(&m), "—");
    m.subagents[0].estimated_cost = Some(2.0);
    assert_eq!(cost_label(&m), "≥~$2.00");
    m.estimated_cost = Some(0.0);
    assert_eq!(cost_label(&m), "~$2.00");
    m.subagents[0].estimate_partial = true;
    assert_eq!(cost_label(&m), "≥~$2.00");
    m.subagents[0].estimated_cost = Some(0.0);
    m.subagents[0].estimate_partial = false;
    assert_eq!(cost_label(&m), "~$0.00");
}
#[test]
fn reported_parent_is_not_added_to_potentially_included_children() {
    for amount in [0.0, 100.0] {
        let m = Metrics {
            cost: Some(amount),
            ..parent()
        };
        assert_eq!(costs::total(&m).amount, Some(amount));
        assert!(!costs::total(&m).estimated);
        assert!(costs::total(&m).partial);
        assert!(costs::breakdown(&m).contains("child inclusion unverified; not added"));
        let c = Metrics {
            session_key: m.subagents[0].session_key.clone(),
            estimated_cost: Some(3.0),
            ..Default::default()
        };
        let (all, _) = costs::overview(&[
            (m.session_key.clone(), Some(&m)),
            (c.session_key.clone(), Some(&c)),
        ]);
        assert_eq!(all, costs::total(&m));
    }
}
#[test]
fn overview_deduplicates_children_also_shown_as_tiles_and_nested_children() {
    let mut m = parent();
    let c = Metrics {
        session_key: m.subagents[0].session_key.clone(),
        estimated_cost: Some(3.0),
        subagents: vec![child("grandchild", Some(2.0))],
        ..Default::default()
    };
    m.subagents.push(c.subagents[0].clone());
    let sessions = vec![
        (m.session_key.clone(), Some(&m)),
        (c.session_key.clone(), Some(&c)),
    ];
    let (all, covered) = costs::overview(&sessions);
    assert_eq!(all.label(), "≥~$59.36");
    assert_eq!(covered, 2);
    let mut reversed = sessions.clone();
    reversed.reverse();
    assert_eq!(costs::overview(&reversed).0.label(), all.label());
    m.subagents.push(m.subagents[0].clone());
    assert_eq!(cost_label(&m), "≥~$59.36");
}
#[test]
fn reported_child_does_not_double_count_its_descendants() {
    let mut c = child("child", None);
    c.cost = Some(10.0);
    c.descendant_keys = vec!["claude:grandchild".into()];
    let m = Metrics {
        estimated_cost: Some(1.0),
        subagents: vec![c, child("grandchild", Some(2.0))],
        ..Default::default()
    };
    assert_eq!(cost_label(&m), "≥~$11.00");
    assert_eq!(
        costs::overview(&[("parent".into(), Some(&m))]).0.label(),
        "≥~$11.00"
    );
}
#[test]
fn overview_missing_sessions_are_lower_bounds_and_aliases_are_deduplicated() {
    let m = parent();
    let id = Agent {
        kind: "claude".into(),
        session_kind: "id".into(),
        session_ref: "id".into(),
        ..Default::default()
    };
    let path = Agent {
        session_kind: "path".into(),
        session_ref: "/log.jsonl".into(),
        ..id.clone()
    };
    assert_eq!(
        costs::session_key(&id, Some(&m)),
        costs::session_key(&path, Some(&m))
    );
    let known = Metrics {
        cost: Some(1.0),
        ..Default::default()
    };
    assert_eq!(
        costs::overview(&[("known".into(), Some(&known)), ("missing".into(), None)])
            .0
            .label(),
        "≥$1.00"
    );
}
