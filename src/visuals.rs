use crate::model::Metrics;
use regex::Regex;
use std::sync::LazyLock;

pub fn phase<'a>(status: &'a str, m: &'a Metrics) -> &'a str {
    if status != "working" {
        return if matches!(status, "blocked" | "done" | "idle" | "unknown") {
            status
        } else {
            "unknown"
        };
    }
    if matches!(m.phase.as_str(), "thinking" | "writing" | "tool") {
        &m.phase
    } else {
        "working"
    }
}
pub fn badge(phase: &str) -> &str {
    match phase {
        "thinking" => "✻ THINK",
        "writing" => "✎ WRITE",
        "tool" => "▸ TOOL",
        "working" => "● WORK",
        "blocked" => "! NEEDS INPUT",
        "done" => "✓ DONE",
        "idle" => "○ IDLE",
        _ => "? UNKNOWN",
    }
}
pub fn tone(phase: &str) -> &str {
    if phase == "working" {
        "thinking"
    } else {
        phase
    }
}
pub fn tool_glyph(name: &str) -> (String, &str) {
    let short = name.rsplit('.').next().unwrap_or(name);
    if short.starts_with("mcp__") || name.starts_with("mcp__") {
        return ("m".into(), "accent");
    }
    let (glyph, style) = match short {
        "Read" => ("R", "tool:read"),
        "Grep" => ("G", "tool:read"),
        "Glob" => ("g", "tool:read"),
        "Bash" | "exec_command" | "shell_command" => ("$", "tool"),
        "Edit" | "apply_patch" => ("E", "done"),
        "Write" => ("W", "done"),
        "WebFetch" => ("F", "tool:web"),
        "WebSearch" => ("S", "tool:web"),
        "Agent" | "spawn_agent" => ("A", "tool:agent"),
        "Skill" => ("K", "writing"),
        "TodoWrite" => ("T", "muted"),
        "LSP" => ("L", "writing"),
        _ => {
            return (
                short
                    .chars()
                    .next()
                    .map(|c| c.to_uppercase().to_string())
                    .unwrap_or("·".into()),
                "muted",
            );
        }
    };
    (glyph.into(), style)
}
pub fn model_effort(model: &str, effort: &str) -> String {
    static LABEL: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"^claude-(opus|sonnet|haiku)(?:-(\d+)-(\d+))?(?:-\d{8})?$").unwrap()
    });
    let short = model.strip_prefix("openai/").unwrap_or(model);
    let short = short.strip_prefix("anthropic/").unwrap_or(short);
    let label = if let Some(c) = LABEL.captures(short) {
        let family = &c[1];
        let titled = format!("{}{}", family[..1].to_uppercase(), &family[1..]);
        if let (Some(major), Some(minor)) = (c.get(2), c.get(3)) {
            format!("{} {}.{}", titled, major.as_str(), minor.as_str())
        } else {
            titled
        }
    } else if short.is_empty() {
        "?".into()
    } else {
        short.into()
    };
    format!("{label}@{}", if effort.is_empty() { "?" } else { effort })
}
pub fn harness(kind: &str) -> String {
    let value = kind.trim().to_lowercase();
    match value.split(':').next().unwrap_or("") {
        "claude code" => "claude",
        "cursor-agent" => "cursor",
        "open-code" => "opencode",
        "antigravity" => "agy",
        other => other,
    }
    .into()
}
pub fn logo(kind: &str) -> &str {
    match kind {
        "claude" => "✻",
        "codex" => "◈",
        "gemini" => "✦",
        "pi" => "π",
        "hermes" => "◇",
        "opencode" => "◧",
        "cursor" => "▹",
        "copilot" => "⌘",
        "crush" => "♥",
        _ => "◇",
    }
}

pub fn core_runs(
    phase: &str,
    width: usize,
    rows: usize,
    tick: f64,
    identity: &str,
    motion: bool,
) -> Vec<(usize, usize, String, String)> {
    let (width, rows) = (width.max(1), rows.max(1));
    let mut lines: Vec<Vec<(usize, usize, String, String)>> = vec![vec![]; rows];
    let mut cell = |x: usize, y: usize, c: char, style: String| {
        let line = &mut lines[y];
        if let Some(last) = line.last_mut()
            && last.0 + last.2.chars().count() == x
            && last.3 == style
        {
            last.2.push(c);
            return;
        }
        line.push((x, y, c.to_string(), style));
    };
    if !motion || !matches!(phase, "thinking" | "writing" | "tool" | "working") {
        let label = match phase {
            "done" => "✓ complete".into(),
            "blocked" => "! waiting for input".into(),
            "idle" => "○ ready".into(),
            "unknown" => "? status unavailable".into(),
            _ => badge(phase).to_lowercase(),
        };
        let chars: Vec<char> = label.chars().take(width).collect();
        let start = (width - chars.len()) / 2;
        for x in 0..width {
            cell(
                x,
                rows / 2,
                if x >= start && x < start + chars.len() {
                    chars[x - start]
                } else {
                    '─'
                },
                if x >= start && x < start + chars.len() {
                    tone(phase)
                } else {
                    "border"
                }
                .into(),
            );
        }
    } else {
        let seed = identity.chars().map(|c| c as u32).sum::<u32>() as f64 % 97.0;
        if phase == "tool" {
            let center = ((tick * 1.2 + seed).sin() + 1.0) * (width - 1) as f64 / 2.0;
            for x in 0..width {
                let intensity = (1.0 - (x as f64 - center).abs() / 5.0).max(0.0);
                let c = if intensity > 0.8 {
                    '█'
                } else if intensity > 0.55 {
                    '▓'
                } else if intensity > 0.25 {
                    '▒'
                } else if intensity > 0.0 {
                    '░'
                } else {
                    '─'
                };
                cell(
                    x,
                    rows - 1,
                    c,
                    format!("tool:{}", ((intensity * 4.0) as usize).min(3)),
                );
                if rows > 1 && intensity > 0.5 {
                    cell(
                        x,
                        0,
                        if intensity < 0.85 { '·' } else { '•' },
                        "tool:2".into(),
                    );
                }
            }
        } else if phase == "writing" {
            let bars: Vec<char> = " ▁▂▃▄▅▆▇█".chars().collect();
            for x in 0..width {
                let wave = 0.5
                    + 0.3 * (x as f64 * 0.55 + tick * 2.2 + seed).sin()
                    + 0.2 * (x as f64 * 0.17 - tick * 1.5).sin();
                let filled = (wave * rows as f64 * 8.0).clamp(0.0, (rows * 8) as f64) as usize;
                for row in 0..rows {
                    let amount = filled.saturating_sub((rows - row - 1) * 8).min(8);
                    cell(
                        x,
                        row,
                        bars[amount],
                        format!("writing:{}", (amount / 2).min(3)),
                    );
                }
            }
        } else {
            let dots = [
                (0, 0, 1u32),
                (0, 1, 2),
                (0, 2, 4),
                (0, 3, 64),
                (1, 0, 8),
                (1, 1, 16),
                (1, 2, 32),
                (1, 3, 128),
            ];
            for row in 0..rows {
                for x in 0..width {
                    let mut bits = 0u32;
                    for (dx, dy, bit) in dots {
                        let (px, py) = ((x * 2 + dx) as f64, (row * 4 + dy) as f64);
                        let v = ((px * 0.19 + tick * 1.4 + seed).sin()
                            + (py * 0.7 - tick * 1.2).sin()
                            + ((px + py) * 0.14 + tick * 0.8).sin())
                            / 3.0;
                        if v > 0.2 {
                            bits |= bit;
                        }
                    }
                    cell(
                        x,
                        row,
                        if bits > 0 {
                            char::from_u32(0x2800 + bits).unwrap()
                        } else {
                            ' '
                        },
                        format!("thinking:{}", (bits.count_ones() / 2).min(3)),
                    );
                }
            }
        }
    }
    lines.into_iter().flatten().collect()
}

pub fn icon(kind: &str, mode: &str) -> String {
    const VENDORS: &[&str] = &[
        "claude",
        "codex",
        "opencode",
        "omp",
        "cline",
        "mastracode",
        "kimi",
        "kilo",
        "maki",
        "pi",
        "hermes",
        "cursor",
        "copilot",
        "deepseek",
        "gemini",
        "gpt",
        "qwen",
        "grok",
        "agy",
        "kiro",
        "amp",
        "devin",
        "qodercli",
        "glm",
        "kimchi",
        "muse",
        "crush",
    ];
    if mode == "ascii" {
        return match kind {
            "claude" => "C".into(),
            "codex" => "X".into(),
            "gemini" => "G".into(),
            _ => kind
                .chars()
                .next()
                .map(|c| c.to_uppercase().to_string())
                .unwrap_or("?".into()),
        };
    }
    static FONT: LazyLock<bool> = LazyLock::new(|| {
        let home = std::env::var("HOME").unwrap_or_default();
        let dirs = if cfg!(target_os = "macos") {
            vec![format!("{home}/Library/Fonts"), "/Library/Fonts".into()]
        } else {
            vec![
                format!(
                    "{}/fonts",
                    std::env::var("XDG_DATA_HOME").unwrap_or(format!("{home}/.local/share"))
                ),
                format!("{home}/.fonts"),
                "/usr/local/share/fonts".into(),
                "/usr/share/fonts".into(),
            ]
        };
        dirs.iter().any(|p| {
            std::fs::read_dir(p).is_ok_and(|entries| {
                entries.flatten().any(|e| {
                    let n = e.file_name();
                    let n = n.to_string_lossy();
                    n.starts_with("HerdrAgentIconsMax") && n.ends_with(".ttf")
                })
            })
        })
    });
    if mode == "font" || mode == "auto" && *FONT {
        return VENDORS
            .iter()
            .position(|v| *v == kind)
            .and_then(|i| char::from_u32(0xE1A0 + i as u32))
            .unwrap_or('◇')
            .to_string();
    }
    logo(kind).into()
}
