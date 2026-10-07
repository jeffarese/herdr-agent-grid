use crate::{
    VERSION, app,
    client::{Client, Result},
    model::{State, agents_from, demo_state, now},
    sessions::Telemetry,
    view::View,
};
use std::{env, io::IsTerminal};
pub fn main(args: Vec<String>) -> Result<()> {
    if args.iter().any(|a| a == "--version") {
        println!("herdr-agent-grid {VERSION}");
        return Ok(());
    }
    if args.iter().any(|a| matches!(a.as_str(), "--help" | "-h")) {
        println!(
            "Herdr Agent Grid {VERSION}\n\nUsage: herdr-agent-grid [OPTIONS]\n       herdr-agent-grid install [--config PATH] [--open]\n\n  --demo             Six synthetic agents\n  --render           Plain-text frame\n  --subagents        Subagent cards (with --render)\n  --list             Agents as JSON\n  --doctor           Check the Herdr connection\n  --icons MODE       auto, font, unicode, ascii\n  --no-motion        Still phase indicators\n  --width N          Render width (1–1000, default 160)\n  --height N         Render height (1–300, default 44)\n  --version          Print version\n\nKeys: arrows/hjkl, Tab, / filter, z details, s subagents, a this/all agents, PgUp/PgDn children, Enter focus, r refresh, q close"
        );
        return Ok(());
    }
    if args.first().is_some_and(|a| a == "install") {
        return crate::install::run(&args[1..]);
    }
    let (mut demo, mut render, mut list, mut doctor, mut motion) = (
        false,
        false,
        false,
        false,
        env::var("HERDR_AGENT_GRID_MOTION")
            .or_else(|_| env::var("HERDR_GRID_MOTION"))
            .unwrap_or_default()
            != "off",
    );
    let (mut width, mut height) = (160usize, 44usize);
    let mut subagents = false;
    let mut icons = env::var("HERDR_AGENT_GRID_ICONS")
        .or_else(|_| env::var("HERDR_GRID_ICONS"))
        .unwrap_or("auto".into());
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--demo" => demo = true,
            "--render" => render = true,
            "--subagents" => subagents = true,
            "--list" => list = true,
            "--doctor" => doctor = true,
            "--no-motion" => motion = false,
            "--width" | "--height" => {
                let n = iter
                    .next()
                    .ok_or(format!("Missing value for {arg}"))?
                    .parse::<usize>()
                    .map_err(|_| format!("Invalid {arg}"))?;
                if arg == "--width" {
                    width = n;
                } else {
                    height = n;
                }
            }
            "--icons" => icons = iter.next().ok_or("Missing icon mode")?.clone(),
            _ => return Err(format!("Unknown argument: {arg}")),
        }
    }
    if !(1..=1000).contains(&width) || !(1..=300).contains(&height) {
        return Err("Frame dimensions must be between 1×1 and 1000×300".into());
    }
    if !matches!(icons.as_str(), "auto" | "font" | "unicode" | "ascii") {
        return Err("Icon mode must be auto, font, unicode or ascii".into());
    }
    if !demo && env::var("HERDR_ENV").unwrap_or_default() != "1" {
        return Err("run from a Herdr pane (or use --demo)".into());
    }
    let client = if demo { None } else { Some(Client::default()) };
    let mut state = if demo {
        demo_state()
    } else if render || list || doctor {
        State {
            agents: agents_from(&client.as_ref().unwrap().snapshot()?),
            updated: now(),
            ..State::default()
        }
    } else {
        State::default()
    };
    if doctor {
        println!("{}",serde_json::to_string_pretty(&serde_json::json!({"plugin":VERSION,"herdr":if demo{"demo"}else{"connected"},"agents":state.agents.len(),"socket":client.as_ref().and_then(|c|c.socket_path.as_ref())})).unwrap());
        return Ok(());
    }
    if list {
        println!("{}", serde_json::to_string_pretty(&state.agents).unwrap());
        return Ok(());
    }
    if render {
        let mut view = View::new(motion);
        view.icons = icons;
        view.arrange(&state, width, height);
        if let Some(c) = &client {
            let mut telemetry = Telemetry::default();
            for a in &state.agents {
                state.metrics.insert(a.pane_id.clone(), telemetry.read(a));
            }
            for (id, lines) in app::targets(&view, &state) {
                if state
                    .metrics
                    .get(&id)
                    .is_some_and(|m| m.call_source == "transcript")
                {
                    continue;
                }
                match c.read(&id, lines) {
                    Ok(text) => {
                        let text = crate::model::clean(&text);
                        if let Some(m) = state.metrics.get_mut(&id) {
                            m.last_call = crate::telemetry::screen_call(&text);
                            if !m.last_call.is_empty() {
                                m.call_source = "screen".into();
                                m.call_at = Some(now());
                            }
                        }
                        state.previews.insert(id, text);
                    }
                    Err(e) => {
                        state.errors.insert(id, e);
                    }
                }
            }
        }
        if subagents {
            view.enter_subagents(&state);
            state = crate::subagents::state(&state, &view.scope);
        }
        println!(
            "{}",
            app::plain_frame(&view.draw(&state, width, height, now(), 0.0), width, height)
        );
        return Ok(());
    }
    if !std::io::stdin().is_terminal() || !std::io::stdout().is_terminal() {
        return Err("Needs a terminal; use the herdr-agent-grid.open plugin action".into());
    }
    app::run(client, if demo { Some(state) } else { None }, motion, icons)
}
