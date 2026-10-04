"""Curses UI; preview reads stay off the input/render thread."""
from __future__ import annotations

import curses
import time

from .client import Client, HerdrError
from .refresh import Refresher, State
from .view import View
from .theme import terminal_colors, xterm_color
from .model import cell_width


def state_key(state: State):
    return state.revision, state.updated, state.error


def paint(screen, commands, styles, width, height, previous=None):
    """Clear/repaint only changed rows, preserving overlapping draw order."""
    rows = [[] for _ in range(height)]
    for command in commands:
        rows[command.y].append(command)
    if previous is None or len(previous) != height:
        screen.erase()
        previous = None
    for y, row in enumerate(rows):
        if previous is not None and y < len(previous) and row == previous[y]:
            continue
        screen.move(y, 0)
        screen.clrtoeol()
        runs = []
        for command in row:
            text = command.text
            if not text:
                continue
            size = sum(cell_width(c) for c in text)
            if runs and runs[-1][0] + runs[-1][3] == command.x and runs[-1][2] == command.style:
                old = runs[-1]
                runs[-1] = (old[0], old[1] + text, old[2], old[3] + size)
            else:
                runs.append((command.x, text, command.style, size))
        for x, text, style, _ in runs:
            try:
                screen.addstr(y, x, text, styles.get(style, 0))
            except curses.error:
                # ncurses may report ERR after writing the bottom-right cell.
                pass
    screen.noutrefresh()
    curses.doupdate()
    return rows


def palette() -> dict[str, int]:
    styles = {s: 0 for s in ("normal", "muted", "border", "selected", "title",
                             "working", "blocked", "done", "idle", "unknown")}
    styles.update(title=curses.A_BOLD, selected=curses.A_BOLD,
                  border=curses.A_DIM, muted=curses.A_DIM,
                  metric=curses.A_BOLD, accent=curses.A_BOLD)
    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        for index, (name, color) in enumerate((
                ("selected", curses.COLOR_CYAN), ("working", curses.COLOR_YELLOW),
                ("blocked", curses.COLOR_YELLOW), ("done", curses.COLOR_GREEN),
                ("idle", curses.COLOR_WHITE), ("unknown", curses.COLOR_MAGENTA),
                ("accent", curses.COLOR_MAGENTA)), 1):
            curses.init_pair(index, color, background)
            styles[name] = curses.color_pair(index) | curses.A_BOLD
        if curses.COLORS >= 256:
            index = 8
            for name, color in terminal_colors().items():
                if name in ("normal", "title", "metric"):
                    continue
                if index >= curses.COLOR_PAIRS:
                    break
                curses.init_pair(index, xterm_color(color), background)
                bold = (curses.A_BOLD if name in ("brand", "selected") or name.startswith(("focus:", "harness:")) else 0)
                styles[name] = curses.color_pair(index) | bold
                index += 1
        else:
            styles.update(thinking=styles["unknown"], writing=styles["working"], tool=styles["blocked"],
                          brand=styles["unknown"], failed=styles["blocked"])
            for status in ("working", "done", "blocked", "idle", "unknown"):
                styles["focus:" + status] = styles[status]
    for status in ("working", "done", "blocked", "idle", "unknown"):
        styles["selection:" + status] = styles[status] | curses.A_REVERSE | curses.A_BOLD
    return styles


def run(screen, client: Client | None, demo: State | None = None) -> str | None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    input_timeout = None
    try:
        curses.set_escdelay(35)
    except AttributeError:
        pass
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    curses.mouseinterval(0)
    styles = palette()
    view = View()
    refresh = Refresher(client) if client else None
    if refresh:
        refresh.start()
    previous = None
    previous_rows, previous_size, state = None, None, None
    try:
        while True:
            state = demo if demo else refresh.get(state)
            height, width = screen.getmaxyx()
            view.arrange(state, width, height)
            delay = 100 if view.animating else 250
            if delay != input_timeout:
                screen.timeout(delay)
                input_timeout = delay
            if refresh:
                refresh.request(view.targets())
            signature = (width, height, state_key(state), view.selected, view.query,
                         view.searching, view.zoom, view.message, view.child_offset,
                         int(time.monotonic() * (10 if view.animating else 1)))
            if signature != previous:
                if previous_size != (width, height):
                    previous_rows = None
                try:
                    previous_rows = paint(screen, view.draw(state, width, height), styles, width, height, previous_rows)
                except curses.error:
                    # A resize between getmaxyx and paint is retried next frame.
                    previous_rows = None
                previous_size = width, height
                previous = signature
            try:
                key = screen.get_wch()
            except curses.error:
                continue
            if key in ("\x03", "\x04"):
                return None
            if view.searching:
                if key == "\x1b":
                    view.searching, view.query = False, ""
                elif key in ("\n", "\r", curses.KEY_ENTER):
                    view.searching = False
                elif key in ("\x7f", "\b", curses.KEY_BACKSPACE):
                    view.query = view.query[:-1]
                elif isinstance(key, str) and key.isprintable() and len(view.query) < 200:
                    view.query += key
                continue
            if key in ("\x1b", "q"):
                if view.zoom:
                    view.zoom = False
                elif view.query:
                    view.query = ""
                else:
                    return None
            elif key == "/":
                view.searching = True
            elif key == "z":
                view.zoom = not view.zoom
            elif key == "r" and refresh:
                refresh.request(view.targets(), force=True)
            elif key in (curses.KEY_RIGHT, "l", "\t"):
                view.move(1, wrap=key == "\t")
            elif key in (curses.KEY_LEFT, "h", curses.KEY_BTAB):
                view.move(-1, wrap=key == curses.KEY_BTAB)
            elif key in (curses.KEY_DOWN, "j"):
                view.move(1 if view.zoom else view.geometry.columns)
            elif key in (curses.KEY_UP, "k"):
                view.move(-1 if view.zoom else -view.geometry.columns)
            elif key in (curses.KEY_NPAGE, "]"):
                if not view.scroll_children(view.child_capacity):
                    view.move(view.geometry.capacity)
            elif key in (curses.KEY_PPAGE, "["):
                if not view.scroll_children(-view.child_capacity):
                    view.move(-view.geometry.capacity)
            elif key == curses.KEY_HOME:
                view.move(-len(view.items))
            elif key == curses.KEY_END:
                view.move(len(view.items))
            elif key == curses.KEY_MOUSE:
                try:
                    _, x, y, _, buttons = curses.getmouse()
                except curses.error:
                    continue
                if buttons & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                    chosen = view.hit(x, y)
                    if chosen:
                        view.selected = chosen.pane_id
                        if demo:
                            view.zoom = not view.zoom
                        else:
                            try:
                                client.focus(chosen.pane_id)
                                return chosen.pane_id
                            except HerdrError as error:
                                view.message = str(error)
                elif buttons & getattr(curses, "BUTTON4_PRESSED", 0):
                    view.move(-1)
                elif buttons & getattr(curses, "BUTTON5_PRESSED", 0):
                    view.move(1)
            elif key in ("\n", "\r", curses.KEY_ENTER) and view.chosen:
                if demo:
                    view.zoom = not view.zoom
                else:
                    try:
                        client.focus(view.chosen.pane_id)
                        return view.chosen.pane_id
                    except HerdrError as error:
                        view.message = str(error)
    finally:
        if refresh:
            refresh.close()
