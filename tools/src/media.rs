use grid_tools::{Result, checked, root};
use herdr_agent_grid::{
    model::{State, Subagent, width},
    view::View,
};
use resvg::{tiny_skia, usvg};
use std::{collections::HashMap, fmt::Write as _, fs, process::Command};
const NOW: f64 = 1800000000.0;
fn escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}
fn scene(t: f64) -> (State, View) {
    let mut state: State = serde_json::from_str(include_str!("../../assets/demo.json")).unwrap();
    state.revision = 1 + (t * 10.0) as u64;
    for (agent, model) in state.agents.iter().zip([
        "claude-sonnet-5-5",
        "gpt-6.1-sol",
        "claude-opus-5-5",
        "gpt-6.1-sol",
        "gemini",
        "claude-opus-5-5",
    ]) {
        let metrics = state.metrics.get_mut(&agent.pane_id).unwrap();
        metrics.model = model.into();
        metrics.source = "Synthetic session log".into();
    }
    for m in state.metrics.values_mut() {
        for stamp in [&mut m.started_at, &mut m.call_at, &mut m.message_at]
            .into_iter()
            .flatten()
        {
            *stamp -= t;
        }
        for child in &mut m.subagents {
            if let Some(stamp) = &mut child.started_at {
                *stamp -= t;
            }
        }
    }
    if t >= 4.0 {
        let m = state.metrics.get_mut("w2:p1").unwrap();
        m.last_call = "apply_patch".into();
        m.call_done = Some(true);
        m.phase = "writing".into();
        m.last_message = "Keyboard navigation is verified. I’m polishing the focus states.".into();
    }
    if t >= 10.0 {
        let m = state.metrics.get_mut("w2:p1").unwrap();
        for (i, name) in [
            "API review",
            "UI audit",
            "Cache checks",
            "Data audit",
            "Error paths",
            "Copy review",
            "A11y checks",
        ]
        .iter()
        .enumerate()
        {
            m.subagents.push(Subagent {
                id: format!("demo-extra-{i}"),
                name: (*name).into(),
                model: "gpt-6.1-sol".into(),
                effort: "high".into(),
                estimated_cost: Some(0.02 + i as f64 * 0.01),
                started_at: Some(NOW - 40.0 - i as f64 * 9.0),
                duration_s: if i < 2 {
                    None
                } else {
                    Some(28.0 + i as f64 * 5.0)
                },
                status: if i < 2 { "working" } else { "done" }.into(),
                ..Default::default()
            });
        }
    }
    if t >= 22.0 {
        for a in &mut state.agents {
            if ["w1:p1", "w2:p1"].contains(&a.pane_id.as_str()) {
                a.status = "done".into();
                for c in &mut state.metrics.get_mut(&a.pane_id).unwrap().subagents {
                    c.status = "done".into();
                    c.duration_s = Some(c.duration_s.unwrap_or(82.0));
                }
            }
        }
    }
    let mut view = View::new(true);
    view.icons = "unicode".into();
    if (6.0..10.0).contains(&t) {
        view.selected = "w2:p1".into();
    } else if (10.0..16.0).contains(&t) {
        view.selected = "w2:p1".into();
        view.zoom = true;
    } else if (16.0..21.0).contains(&t) {
        view.query = "working"
            .chars()
            .take(((t - 16.0) * 4.0) as usize + 1)
            .collect();
        view.searching = t < 18.0;
    } else if t >= 21.0 {
        view.selected = "w6:p1".into();
    }
    (state, view)
}
fn caption(t: f64) -> (&'static str, &'static str) {
    if t < 6.0 {
        ("One panel. Every agent. Working agents first.", "Cmd + G")
    } else if t < 10.0 {
        (
            "Harness icons. Model@Effort. The latest message at a glance.",
            "Tab / arrows",
        )
    } else if t < 16.0 {
        (
            "See subagents: name, Model@Effort, API cost and time.",
            "z  details",
        )
    } else if t < 21.0 {
        (
            "Find an agent by status, workspace, task or tool.",
            "/  filter",
        )
    } else if t < 26.0 {
        (
            "Working in orange. Completed in green. Jump straight to an agent.",
            "Enter  focus",
        )
    } else {
        ("Your agents. One command center.", "herdr-agent-grid")
    }
}
fn svg(t: f64) -> String {
    let colors: HashMap<String, String> =
        serde_json::from_str(include_str!("../../assets/dark.json")).unwrap();
    let (state, mut view) = scene(t);
    let (caption, key) = caption(t);
    let mut svg = format!(
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080"><rect width="1920" height="1080" fill="#0b0e18"/><rect x="96" y="138" width="1728" height="862" rx="15" fill="#151925" stroke="#373b50"/><g font-family="DejaVu Sans,Arial" fill="#f0f2f9"><text x="100" y="82" font-size="44" font-weight="bold">herdr-agent-grid</text><text x="1530" y="78" font-size="17" fill="#b39dff">SYNTHETIC DATA / DEMO</text><text x="120" y="1045" font-size="25">{}</text><text x="1540" y="1045" font-size="22" fill="#b39dff">{}</text></g><g font-family="Menlo,DejaVu Sans Mono,monospace" font-size="18">"##,
        escape(caption),
        escape(key)
    );
    for (x, color) in [(123, "#fb7185"), (145, "#fbbf24"), (167, "#34d399")] {
        write!(svg, r#"<circle cx="{x}" cy="160" r="5" fill="{color}"/>"#).unwrap();
    }
    for c in view.draw(&state, 140, 38, NOW, t) {
        let (x, y) = (120 + c.x * 12, 186 + c.y * 21);
        let selection = c.style.strip_prefix("selection:");
        let color = colors
            .get(selection.unwrap_or(&c.style))
            .unwrap_or(&colors["normal"]);
        if selection.is_some() {
            write!(
                svg,
                r#"<rect x="{x}" y="{y}" width="{}" height="21" fill="{color}"/>"#,
                width(&c.text) * 12
            )
            .unwrap();
        }
        let ink = if selection.is_some() {
            "#151925"
        } else {
            color
        };
        let mut offset = 0;
        for ch in c.text.chars() {
            let px = x + offset * 12;
            if ('\u{2800}'..='\u{28ff}').contains(&ch) {
                let bits = ch as u32 - 0x2800;
                for (dx, dy, bit) in [
                    (0, 0, 1),
                    (0, 1, 2),
                    (0, 2, 4),
                    (0, 3, 64),
                    (1, 0, 8),
                    (1, 1, 16),
                    (1, 2, 32),
                    (1, 3, 128),
                ] {
                    if bits & bit != 0 {
                        write!(
                            svg,
                            r#"<circle cx="{}" cy="{}" r="1" fill="{ink}"/>"#,
                            px + 2 + dx * 6,
                            y + 3 + dy * 4
                        )
                        .unwrap();
                    }
                }
            } else if ch != ' ' {
                let pos = if width(&ch.to_string()) == 0 {
                    px.saturating_sub(12)
                } else {
                    px
                };
                write!(
                    svg,
                    r#"<text x="{pos}" y="{}" fill="{ink}">{}</text>"#,
                    y + 17,
                    escape(&ch.to_string())
                )
                .unwrap();
            }
            offset += width(&ch.to_string());
        }
    }
    svg.push_str("</g></svg>");
    svg
}
fn render(svg: &str, options: &usvg::Options) -> Result<tiny_skia::Pixmap> {
    let tree = usvg::Tree::from_str(svg, options)?;
    let size = tree.size().to_int_size();
    let mut pixmap = tiny_skia::Pixmap::new(size.width(), size.height())
        .ok_or("Unable to allocate media frame")?;
    resvg::render(
        &tree,
        tiny_skia::Transform::identity(),
        &mut pixmap.as_mut(),
    );
    Ok(pixmap)
}
pub fn run(args: &[String]) -> Result<()> {
    let mut out = root().join("docs/media");
    let mut stills = false;
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--output" => out = it.next().ok_or("Missing output")?.into(),
            "--stills-only" => stills = true,
            _ => return Err(format!("Unknown media argument: {arg}").into()),
        }
    }
    let mut options = usvg::Options::default();
    options.fontdb_mut().load_system_fonts();
    if options.fontdb.is_empty() {
        return Err("Install a system TrueType font for media export".into());
    }
    fs::create_dir_all(&out)?;
    for (name, t) in [("poster", 3.0), ("subagents", 12.0)] {
        let doc = svg(t);
        fs::write(out.join(format!("{name}.svg")), &doc)?;
        render(&doc, &options)?.save_png(out.join(format!("{name}.png")))?;
    }
    let mut contact =
        String::from(r#"<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1620">"#);
    for (i, t) in [3.0, 7.0, 12.0, 18.0, 23.0, 28.0].iter().enumerate() {
        write!(
            contact,
            r#"<g transform="translate({} {}) scale(0.5)">{}</g>"#,
            i % 2 * 960,
            i / 2 * 540,
            svg(*t)
        )
        .unwrap();
    }
    contact.push_str("</svg>");
    render(&contact, &options)?.save_png(out.join("contact-sheet.png"))?;
    if !stills {
        checked(Command::new("ffmpeg").arg("-version"))?;
        let frames = tempfile::tempdir()?;
        for i in 0..300 {
            render(&svg(i as f64 / 10.0), &options)?
                .save_png(frames.path().join(format!("frame-{i:04}.png")))?;
            if i % 50 == 0 {
                eprintln!("Rendered {i}/300 frames");
            }
        }
        checked(
            Command::new("ffmpeg")
                .args(["-y", "-loglevel", "error", "-framerate", "10", "-i"])
                .arg(frames.path().join("frame-%04d.png"))
                .args([
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-crf",
                    "20",
                    "-movflags",
                    "+faststart",
                ])
                .arg(out.join("demo.mp4")),
        )?;
        checked(Command::new("ffmpeg").args(["-y","-loglevel","error","-ss","2","-t","3","-i"]).arg(out.join("demo.mp4")).args(["-filter_complex","fps=10,scale=1120:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse","-loop","0"]).arg(out.join("preview.gif")))?;
    }
    println!("{}", out.display());
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn authored_scenes_include_details_filter_and_completion() {
        let (s, v) = scene(12.0);
        assert!(v.zoom);
        assert_eq!(s.metrics["w2:p1"].subagents.len(), 9);
        assert_eq!(scene(18.0).1.query, "working");
        assert_eq!(scene(23.0).0.agents[0].status, "done");
        assert!(svg(3.0).contains("SYNTHETIC DATA"));
    }
}
