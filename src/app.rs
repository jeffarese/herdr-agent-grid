use crate::{
    client::{Client, Result},
    model::{State, now},
    refresh::Refresher,
    subagents::parent_pane,
    view::{Draw, View},
};
use crossterm::{
    cursor::{Hide, Show},
    event::{
        self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind,
        KeyModifiers, MouseButton, MouseEventKind,
    },
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::{
    Terminal,
    backend::CrosstermBackend,
    style::{Color, Modifier, Style},
};
use std::{
    collections::HashMap,
    io::{self, Write},
    sync::Arc,
    time::{Duration, Instant},
};
pub fn styles() -> HashMap<String, Style> {
    let light = std::env::var("HERDR_AGENT_GRID_THEME")
        .or_else(|_| std::env::var("HERDR_GRID_THEME"))
        .is_ok_and(|s| s == "light");
    let colors: HashMap<String, String> = serde_json::from_str(if light {
        include_str!("../assets/light.json")
    } else {
        include_str!("../assets/dark.json")
    })
    .unwrap();
    let mut styles = HashMap::new();
    for (name, color) in colors {
        let mut style = Style::default();
        // Let the terminal choose foreground/background for readable native themes.
        if !matches!(name.as_str(), "normal" | "title" | "metric") {
            let rgb = u32::from_str_radix(&color[1..], 16).unwrap();
            style = style.fg(Color::Rgb((rgb >> 16) as u8, (rgb >> 8) as u8, rgb as u8));
        }
        if matches!(name.as_str(), "title" | "metric" | "brand" | "selected")
            || name.starts_with("focus:")
            || name.starts_with("harness:")
        {
            style = style.add_modifier(Modifier::BOLD);
        }
        styles.insert(name, style);
    }
    for status in ["working", "done", "blocked", "idle", "unknown"] {
        styles.insert(
            format!("selection:{status}"),
            styles[status].add_modifier(Modifier::REVERSED | Modifier::BOLD),
        );
    }
    styles
}
pub fn paint(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    commands: &[Draw],
    styles: &HashMap<String, Style>,
) -> io::Result<()> {
    terminal.draw(|f| {
        let b = f.buffer_mut();
        for c in commands {
            if c.x < b.area.width as usize && c.y < b.area.height as usize {
                b.set_stringn(
                    c.x as u16,
                    c.y as u16,
                    &c.text,
                    (b.area.width as usize).saturating_sub(c.x),
                    *styles.get(&c.style).unwrap_or(&Style::default()),
                );
            }
        }
    })?;
    Ok(())
}
struct Restore;
impl Drop for Restore {
    fn drop(&mut self) {
        restore();
    }
}
pub fn restore() {
    let _ = disable_raw_mode();
    let _ = execute!(
        io::stdout(),
        DisableMouseCapture,
        Show,
        LeaveAlternateScreen
    );
}
pub fn targets(view: &View, state: &State) -> HashMap<String, usize> {
    // Subagent cards are read through their parent's pane.
    view.visible
        .iter()
        .map(|i| (parent_pane(&state.agents[*i].pane_id).to_owned(), 40))
        .collect()
}
pub enum Action {
    Continue,
    Quit,
    Focus,
    Refresh,
    /// Enter or leave the subagents view.
    Subagents,
    /// Switch the subagents view between one agent and all agents.
    Scope,
}
pub fn key(view: &mut View, state: &State, event: KeyEvent) -> Action {
    let code = event.code;
    if event.modifiers.contains(KeyModifiers::CONTROL) && matches!(code, KeyCode::Char('c' | 'd')) {
        return Action::Quit;
    }
    if view.searching {
        match code {
            KeyCode::Esc => {
                view.searching = false;
                view.query.clear();
            }
            KeyCode::Enter => view.searching = false,
            KeyCode::Backspace => {
                view.query.pop();
            }
            KeyCode::Char(c) if !c.is_control() && view.query.chars().count() < 200 => {
                view.query.push(c)
            }
            _ => {}
        }
        return Action::Continue;
    }
    let delta = match code {
        KeyCode::Esc | KeyCode::Char('q') => {
            if view.zoom {
                view.zoom = false;
            } else if !view.query.is_empty() {
                view.query.clear();
            } else if view.subagents {
                return Action::Subagents;
            } else {
                return Action::Quit;
            }
            0
        }
        KeyCode::Char('/') => {
            view.searching = true;
            0
        }
        KeyCode::Char('z') => {
            view.zoom = !view.zoom;
            0
        }
        KeyCode::Char('d') => {
            view.toggle_completed();
            0
        }
        KeyCode::Char('r') => return Action::Refresh,
        KeyCode::Char('s') => return Action::Subagents,
        KeyCode::Char('a') if view.subagents => return Action::Scope,
        KeyCode::Enter if !view.selected.is_empty() => return Action::Focus,
        KeyCode::Right | KeyCode::Char('l') | KeyCode::Tab => 1,
        KeyCode::Left | KeyCode::Char('h') | KeyCode::BackTab => -1,
        KeyCode::Down | KeyCode::Char('j') => {
            if view.zoom {
                1
            } else {
                view.geometry.columns as isize
            }
        }
        KeyCode::Up | KeyCode::Char('k') => {
            if view.zoom {
                -1
            } else {
                -(view.geometry.columns as isize)
            }
        }
        KeyCode::PageDown | KeyCode::Char(']') => {
            if view.scroll_children(view.child_capacity as isize) {
                0
            } else {
                view.geometry.capacity as isize
            }
        }
        KeyCode::PageUp | KeyCode::Char('[') => {
            if view.scroll_children(-(view.child_capacity as isize)) {
                0
            } else {
                -(view.geometry.capacity as isize)
            }
        }
        KeyCode::Home => -(view.items.len() as isize),
        KeyCode::End => view.items.len() as isize,
        _ => 0,
    };
    if delta != 0 {
        view.move_by(
            state,
            delta,
            matches!(code, KeyCode::Tab | KeyCode::BackTab),
        );
    }
    Action::Continue
}
pub fn plain_frame(commands: &[Draw], width: usize, height: usize) -> String {
    let mut b = ratatui::buffer::Buffer::empty(ratatui::layout::Rect::new(
        0,
        0,
        width as u16,
        height as u16,
    ));
    for c in commands {
        if c.x < width && c.y < height {
            b.set_stringn(
                c.x as u16,
                c.y as u16,
                &c.text,
                width - c.x,
                Style::default(),
            );
        }
    }
    (0..height)
        .map(|y| {
            let mut row = String::new();
            let mut x = 0;
            while x < width {
                let s = b[(x as u16, y as u16)].symbol();
                row.push_str(s);
                x += crate::model::width(s).max(1);
            }
            row
        })
        .collect::<Vec<_>>()
        .join("\n")
}
enum UiEvent {
    Input(Event),
    Refresh,
    Error(String),
}
struct StopInput(Arc<std::sync::atomic::AtomicBool>);
impl Drop for StopInput {
    fn drop(&mut self) {
        self.0.store(true, std::sync::atomic::Ordering::Relaxed);
    }
}
pub fn run(client: Option<Client>, demo: Option<State>, motion: bool, icons: String) -> Result<()> {
    let (tx, rx) = std::sync::mpsc::sync_channel(128);
    let refresh = client.clone().map(|c| {
        let tx = tx.clone();
        Refresher::start_with_wakeup(c, move || {
            let _ = tx.try_send(UiEvent::Refresh);
        })
    });
    let mut state = Arc::new(demo.unwrap_or_default());
    let styles = styles();
    let panic = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        restore();
        panic(info);
    }));
    enable_raw_mode().map_err(|e| e.to_string())?;
    let _restore = Restore;
    execute!(io::stdout(), EnterAlternateScreen, EnableMouseCapture, Hide)
        .map_err(|e| e.to_string())?;
    let mut terminal =
        Terminal::new(CrosstermBackend::new(io::stdout())).map_err(|e| e.to_string())?;
    // One input reader; both keyboard events and published telemetry wake the UI.
    let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let _stop = StopInput(stop.clone());
    std::thread::spawn(move || {
        while !stop.load(std::sync::atomic::Ordering::Relaxed) {
            match event::poll(Duration::from_millis(100)) {
                Ok(false) => continue,
                Ok(true) => match event::read() {
                    Ok(e) => {
                        if tx.send(UiEvent::Input(e)).is_err() {
                            return;
                        }
                    }
                    Err(e) => {
                        let _ = tx.send(UiEvent::Error(e.to_string()));
                        return;
                    }
                },
                Err(e) => {
                    let _ = tx.send(UiEvent::Error(e.to_string()));
                    return;
                }
            }
        }
    });
    let mut view = View::new(motion);
    view.icons = icons;
    // What the view draws: the agent inventory, or its subagents as cards.
    let mut shown = state.clone();
    let mut shown_for: Option<Option<String>> = None;
    let start = Instant::now();
    let mut dirty = true;
    let mut previous_tick = u64::MAX;
    let trace = std::env::var_os("HERDR_AGENT_GRID_TRACE").is_some();
    let mut input = 0u64;
    loop {
        if let Some(r) = &refresh {
            let next = r.get();
            if !Arc::ptr_eq(&next, &state) {
                state = next;
                dirty = true;
                shown_for = None;
            }
        }
        let wanted = view.subagents.then(|| view.scope.clone());
        if shown_for.as_ref() != Some(&wanted) {
            shown = if view.subagents {
                Arc::new(crate::subagents::state(&state, &view.scope))
            } else {
                state.clone()
            };
            shown_for = Some(wanted);
        }
        let size = terminal.size().map_err(|e| e.to_string())?;
        let (w, h) = (size.width as usize, size.height as usize);
        view.arrange(&shown, w, h);
        if let Some(r) = &refresh {
            r.request(targets(&view, &shown), false);
        }
        let animating = view.animating(&shown);
        let tick = (start.elapsed().as_secs_f64() * if animating { 10.0 } else { 1.0 }) as u64;
        if dirty || tick != previous_tick {
            paint(
                &mut terminal,
                &view.draw(&shown, w, h, now(), start.elapsed().as_secs_f64()),
                &styles,
            )
            .map_err(|e| e.to_string())?;
            if trace {
                print!(
                    "\x1b]777;{}\x07",
                    serde_json::json!({"input":input,"selected":view.selected,"query":view.query,"zoom":view.zoom,"revision":state.revision,"ready":!state.agents.is_empty() && state.metrics.len()==state.agents.len()})
                );
                io::stdout().flush().map_err(|e| e.to_string())?;
            }
            dirty = false;
            previous_tick = tick;
        }
        let event = match rx.recv_timeout(Duration::from_millis(if animating { 100 } else { 250 }))
        {
            Ok(UiEvent::Input(e)) => e,
            Ok(UiEvent::Error(e)) => return Err(e),
            Ok(UiEvent::Refresh) | Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                return Err("Terminal input disconnected".into());
            }
        };
        dirty = true;
        let action = match event {
            Event::Key(k) if k.kind != KeyEventKind::Release => {
                input += 1;
                key(&mut view, &shown, k)
            }
            Event::Mouse(m) => match m.kind {
                MouseEventKind::Down(MouseButton::Left) => {
                    if view.click_controls(m.column as usize, m.row as usize) {
                        continue;
                    }
                    let hit = view
                        .visible
                        .iter()
                        .zip(&view.geometry.rects)
                        .find(|(_, r)| {
                            m.column as usize >= r.x
                                && (m.column as usize) < r.x + r.width
                                && m.row as usize >= r.y + view.top
                                && (m.row as usize) < r.y + view.top + r.height
                        })
                        .map(|(i, _)| *i);
                    if let Some(i) = hit {
                        view.selected = shown.agents[i].pane_id.clone();
                        Action::Focus
                    } else {
                        Action::Continue
                    }
                }
                MouseEventKind::ScrollUp => {
                    view.move_by(&shown, -1, false);
                    Action::Continue
                }
                MouseEventKind::ScrollDown => {
                    view.move_by(&shown, 1, false);
                    Action::Continue
                }
                _ => Action::Continue,
            },
            _ => Action::Continue,
        };
        match action {
            Action::Quit => break,
            Action::Refresh => {
                if let Some(r) = &refresh {
                    r.request(targets(&view, &shown), true);
                }
            }
            Action::Subagents => {
                if view.subagents {
                    view.leave_subagents();
                } else {
                    view.enter_subagents(&state);
                }
            }
            Action::Scope => view.toggle_scope(&state),
            Action::Focus => {
                if let Some(c) = &client {
                    match c.focus(parent_pane(&view.selected)) {
                        Ok(()) => break,
                        Err(e) => view.message = crate::model::clean(&e),
                    }
                } else {
                    view.zoom = !view.zoom;
                }
            }
            Action::Continue => {}
        }
    }
    Ok(())
}
