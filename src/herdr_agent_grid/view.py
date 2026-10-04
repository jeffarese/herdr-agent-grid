"""Compact agent cards with progressive disclosure and explicit data provenance."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time

from .model import Agent, Layout, Rect, cell_width, clip, layout, wrap_cells
from .refresh import State
from .telemetry import Metrics, cost_detail, cost_label, cost_value, duration, subagent_time, token_label
from .icons import logo_for, normalize
from .visuals import PHASES, core_runs, model_effort, phase_for, tool_glyph
import os


@dataclass(frozen=True)
class Draw:
    x: int
    y: int
    text: str
    style: str = "normal"


STATUS = {"working": ("● WORKING", "Working"), "blocked": ("! NEEDS INPUT", "Needs input"),
          "done": ("✓ DONE", "Done"), "idle": ("○ IDLE", "Idle"),
          "unknown": ("? UNKNOWN", "Unknown")}
EMPTY_METRICS = Metrics()


def padded(text: str, width: int) -> str:
    text = clip(text, width, True)
    return text + " " * max(0, width - sum(cell_width(char) for char in text))


def child_columns(width: int) -> tuple[int, int, int, int]:
    # Keep time and cost visible when a child name/model needs truncating.
    cost, elapsed = 10, 8
    rest = max(2, width - cost - elapsed - 6)
    model = min(26, max(1, rest // 2))
    return rest - model, model, cost, elapsed


def child_row(values: tuple[str, str, str, str], width: int) -> str:
    return "  ".join(padded(value, size) for value, size in zip(values, child_columns(width)))


def children_summary(children) -> str:
    working = sum(child.status == "working" for child in children)
    unknown = sum(child.status == "unknown" for child in children)
    return f"{working} working / {len(children)} total" + (f" · {unknown} unknown" if unknown else "")


def children_lines(children, width: int, space: int, now: float, offset=0, scrolling=False):
    """Fill the card's available rows; every overflow remains explicitly reachable."""
    if space <= 0:
        return [], 0, 0
    if width < 64 and space == 2 and children:
        offset = max(0, min(offset, len(children) - 1)) if scrolling else 0
        child = children[offset]
        glyph, style = {"working": ("●", "working"), "done": ("✓", "done"),
                        "failed": ("!", "failed")}.get(child.status, ("?", "muted"))
        count = f" · subagent {offset + 1}/{len(children)}"
        name = glyph + " " + clip(child.name, max(1, width - 2 - len(count)), True) + count
        suffix = f" · {cost_label(child)} · {subagent_time(child, now)}"
        model = "  " + clip(model_effort(child.model, child.effort), max(1, width - 2 - len(suffix)), True)
        return [(name, style), (model + suffix, "muted")], 1, offset
    wide = width >= 64
    headers = 2 if wide and space >= 4 else 1
    per_child = 1 if wide or space <= 3 else 2
    capacity = max(0, (space - headers) // per_child)
    overflow = len(children) > capacity
    footer = int(overflow and space >= headers + per_child + 1)
    if footer:
        capacity = max(1, (space - headers - 1) // per_child)
    offset = max(0, min(offset, len(children) - capacity)) if scrolling else 0
    shown = children[offset:offset + capacity]
    header = "SUBAGENTS  " + children_summary(children)
    if overflow and not footer:
        header = f"SUBAGENTS {len(shown)}/{len(children)} · z details"
    lines = [(header, "accent")]
    if headers == 2:
        lines.append((child_row(("NAME", "MODEL@EFFORT", "API COST", "TIME"), width), "muted"))
    for child in shown:
        glyph, style = {"working": ("●", "working"), "done": ("✓", "done"),
                        "failed": ("!", "failed")}.get(child.status, ("?", "muted"))
        name = glyph + " " + child.name
        model, cost, elapsed = model_effort(child.model, child.effort), cost_label(child), subagent_time(child, now)
        if per_child == 1:
            lines.append((child_row((name, model, cost, elapsed), width), style))
        else:
            lines.append((name, style))
            suffix = f" · {cost} · {elapsed}"
            lines.append(("  " + clip(model, max(1, width - 2 - len(suffix)), True) + suffix, "muted"))
    if footer:
        remaining = len(children) - len(shown)
        text = (f"{offset + 1}–{offset + len(shown)} / {len(children)} · PgUp/PgDn scroll" if scrolling else
                f"+{remaining} more · z details")
        lines.append((text, "muted"))
    return lines, capacity, offset


class View:
    def __init__(self, icons: str | None = None, motion: bool | None = None):
        self.selected = ""
        self.query = ""
        self.searching = False
        self.zoom = False
        self.message = ""
        self.items: list[Agent] = []
        self.visible: list[Agent] = []
        self.geometry = Layout((), 1, 1)
        self.page = 0
        self.page_count = 1
        self.top = 5
        self.icons = icons or os.environ.get("HERDR_AGENT_GRID_ICONS", os.environ.get("HERDR_GRID_ICONS", "auto"))
        self.motion = motion if motion is not None else os.environ.get("HERDR_AGENT_GRID_MOTION", os.environ.get("HERDR_GRID_MOTION", "on")) != "off"
        self._arrangement_key = None
        self._inventory_state = None
        self._inventory_key = None
        self._indices: dict[str, int] = {}
        self._overview_state = None
        self._overview_revision = None
        self._overview_value = None
        self.child_offsets: dict[str, int] = {}
        self.child_capacity = 0
        self.child_count = 0

    def arrange(self, state: State, width: int, height: int) -> None:
        key = (id(state), state.revision, width, height, self.query, self.zoom, self.selected)
        # Published snapshots have revisions. Unversioned fixtures stay mutable.
        if state.revision and key == self._arrangement_key:
            return
        query = self.query.casefold()
        if not state.revision or self._inventory_state is not state or self._inventory_key != (state.revision, query):
            self.items = list(state.agents) if not query else [a for a in state.agents if query in
                      f"{a.name} {a.kind} {a.title} {a.workspace} {a.status} "
                      f"{state.metrics.get(a.pane_id, EMPTY_METRICS).last_call}".casefold()]
            # Preserve Herdr's order within each status group.
            self.items.sort(key=lambda a: a.status != "working")
            self._indices = {a.pane_id: i for i, a in enumerate(self.items)}
            self._inventory_state = state
            self._inventory_key = state.revision, query
        if self.selected not in self._indices:
            self.selected = self.items[0].pane_id if self.items else ""
        self.top = 5 if height >= 18 else 2
        index = self.index
        self.geometry = layout(width, max(1, height - self.top - 2),
                               1 if self.zoom and self.items else len(self.items))
        self.page = index // self.geometry.capacity
        self.page_count = max(1, math.ceil(len(self.items) / self.geometry.capacity))
        if self.zoom:
            self.visible = self.items[index:index + 1]
        else:
            start = self.page * self.geometry.capacity
            self.visible = self.items[start:start + self.geometry.capacity]
        self._arrangement_key = (id(state), state.revision, width, height, self.query, self.zoom, self.selected)

    @property
    def index(self) -> int:
        return self._indices.get(self.selected, 0)

    def overview(self, state: State) -> tuple[dict[str, int], str]:
        """Aggregate once per published snapshot, independent of navigation."""
        if state.revision and self._overview_state is state and self._overview_revision == state.revision:
            return self._overview_value
        counts = dict.fromkeys(STATUS, 0)
        reported, seen, spaces = [], set(), set()
        for a in state.agents:
            counts[a.status if a.status in STATUS else "unknown"] += 1
            spaces.add(a.workspace)
            key = (a.provider or a.kind, a.session_kind, a.session_ref) if a.session_ref else a.identity
            if key not in seen:
                seen.add(key)
                reported.append(state.metrics.get(a.pane_id, EMPTY_METRICS))
        costs = [m for m in reported if cost_value(m) is not None]
        estimated = any(m.cost is None for m in costs)
        tokens = [m for m in reported if m.tokens is not None]
        amount = sum(cost_value(m) for m in costs) if costs else None
        partial = any(m.cost_partial if m.cost is not None else m.estimate_partial for m in costs)
        aggregate = Metrics(cost=amount if not estimated else None, cost_partial=partial,
                            estimated_cost=amount if estimated else None, estimate_partial=partial,
                            tokens=sum(m.tokens for m in tokens) if tokens else None,
                            tokens_partial=any(m.tokens_partial for m in tokens))
        summary = (f"{len(state.agents)} agents · {len(spaces)} workspaces   "
                   f"API cost {cost_label(aggregate) if costs else 'unavailable'} ({len(costs)}/{len(reported)} {'covered' if estimated else 'reported'})   "
                   f"Tokens {token_label(aggregate)} ({len(tokens)}/{len(reported)} reported)")
        self._overview_state, self._overview_revision = state, state.revision
        self._overview_value = counts, summary
        return self._overview_value

    @property
    def chosen(self) -> Agent | None:
        return self.items[self.index] if self.items else None

    def move(self, delta: int, wrap: bool = False) -> None:
        if self.items:
            index = self.index + delta
            index = index % len(self.items) if wrap else max(0, min(len(self.items) - 1, index))
            self.selected = self.items[index].pane_id

    @property
    def child_offset(self) -> int:
        return self.child_offsets.get(self.selected, 0)

    def scroll_children(self, delta: int) -> bool:
        if not self.zoom or not self.child_count or not self.child_capacity:
            return False
        self.child_offsets[self.selected] = max(0, min(self.child_offset + delta,
                                                     self.child_count - self.child_capacity))
        return True

    def targets(self) -> dict[str, int]:
        # Visible terminal reads provide a last-call fallback for providers
        # without a matched transcript. No transcript text is drawn in cards.
        return {a.pane_id: 40 for a in self.visible}

    @property
    def animating(self) -> bool:
        # Compact cards show labels only, so they need no animation ticks.
        if (self.zoom and self.geometry.rects and self.geometry.rects[0].height < 17 and
                self._inventory_state and self._inventory_state.metrics.get(self.selected, EMPTY_METRICS).subagents):
            return False
        return self.motion and any(a.status == "working" and r.height >= 12 and r.width >= 4
                                   for a, r in zip(self.visible, self.geometry.rects))

    def hit(self, x: int, y: int) -> Agent | None:
        return next((a for a, rect in zip(self.visible, self.geometry.rects)
                     if rect.contains(x, y - self.top)), None)

    def draw(self, state: State, width: int, height: int, animation_time: float | None = None) -> list[Draw]:
        self.arrange(state, width, height)
        commands: list[Draw] = []
        now = time.time()
        animation_time = time.monotonic() if animation_time is None else animation_time
        self.child_capacity, self.child_count = 0, 0

        def put(x: int, y: int, text: str, style: str = "normal", limit: int | None = None):
            if 0 <= y < height and 0 <= x < width:
                commands.append(Draw(x, y, clip(text, min(width - x, limit if limit is not None else width), True), style))

        counts, summary = self.overview(state)
        heading = "  ◆ AGENT GRID"
        context = ""
        if self.query:
            context = f"/ {len(self.items)} of {len(state.agents)}"
        elif self.zoom:
            context = "/ agent details"
        elif self.page_count > 1:
            context = f"/ page {self.page + 1} of {self.page_count}"
        put(0, 0, heading if width >= 55 else " ◆ GRID", "brand", 18 if width >= 55 else 9)
        connection = "OFFLINE" if state.error else "LIVE" if state.updated else "CONNECTING"
        stale = state.updated and time.monotonic() - state.updated > 5
        if stale and not state.error:
            connection = "SYNCING"
        if width >= 55:
            put(width - len(connection) - 3, 0, "● " + connection,
                "blocked" if state.error else "done" if state.updated else "muted")
        active = counts["working"]
        badge_x = 19 if width >= 55 else 10
        badge_width = width - badge_x - (len(connection) + 6 if width >= 55 else 1)
        active_badge = f" {active} ACTIVE / {len(state.agents)} TOTAL " if width >= 55 else f" {active} ACTIVE "
        put(badge_x, 0, active_badge, "selection:working" if active else "selection:idle", badge_width)
        if context and width >= 90:
            context_x = badge_x + len(active_badge) + 2
            put(context_x, 0, context, "muted", width - context_x - len(connection) - 6)
        x = 2
        for status, label in (("working", "working"), ("blocked", "need input"),
                              ("done", "done"), ("idle", "idle"), ("unknown", "unknown")):
            if counts[status]:
                value = f"{counts[status]} {label}"
                put(x, 1, value, status)
                x += len(value) + 4
        if not state.agents:
            put(2, 1, "No active agents" if state.updated else "Waiting for Herdr…", "muted")
        if self.top == 5:
            put(2, 2, summary, "muted")
            if state.error:
                put(2, 3, "Connection unavailable · " + state.error, "blocked")
            elif stale:
                put(2, 3, f"Last inventory {duration(time.monotonic() - state.updated)} ago", "muted")
            else:
                put(2, 3, "API token cost  ·  ~ estimated  ·  ≥ partial  ·  — unavailable", "muted")
        if width < 12 or height < 7:
            put(0, min(2, height - 1), "Enlarge terminal", "muted")
            return commands
        if not self.items:
            message = "No agents match this filter" if self.query else "Waiting for agents in this session"
            put(2, max(self.top + 1, height // 2), message, "muted")

        for agent, original in zip(self.visible, self.geometry.rects):
            rect = Rect(original.x, original.y + self.top, original.width, original.height)
            x, y, w, h = rect.x, rect.y, rect.width, rect.height
            if w < 4 or h < 4:
                continue
            selected = agent.pane_id == self.selected
            m = state.metrics.get(agent.pane_id, EMPTY_METRICS)
            phase = phase_for(agent.status, m)
            badge = PHASES[phase][0]
            status_style = agent.status if agent.status in STATUS else "unknown"
            border = "focus:" + status_style if selected else status_style if agent.status in ("working", "blocked", "done") else "border"
            settled = agent.status in ("done", "idle") and not selected
            corners = ("╔", "═", "╗", "╚", "╝", "║") if selected else ("╭", "─", "╮", "╰", "╯", "│")
            tl, horizontal, tr, bl, br, vertical = corners
            put(x, y, tl + horizontal * (w - 2) + tr, border, w)
            put(x, y + h - 1, bl + horizontal * (w - 2) + br, border, w)
            for line in range(1, h - 1):
                put(x, y + line, vertical, border, 1)
                put(x + w - 1, y + line, vertical, border, 1)
            number = self._indices[agent.pane_id] + 1
            if selected:
                tag = " SELECTED " if w >= 30 else ""
                title = padded(f" ▶ {number:02}  {agent.name} ", w - 4 - len(tag)) + tag
                put(x + 2, y, title, "selection:" + status_style, w - 4)
            else:
                put(x + 2, y, f"   {number:02}  {agent.name} ",
                    border if agent.status in ("working", "blocked", "done") else "title", w - 5)

            def inside(row: int, text: str, style: str = "normal"):
                if 0 < row < h - 1:
                    put(x + 2, y + row, text, style, w - 4)

            harness = normalize(agent.kind)
            put(x + 2, y + 1, logo_for(harness, self.icons), "harness:" + harness, 1)
            put(x + 4, y + 1, model_effort(m.model, m.effort), "title", w - 6)
            inside(2, agent.title, "settled" if settled else "title")
            age = duration(now - m.started_at) if m.started_at else "—"
            last_age = duration(now - m.call_at) if m.call_at else "—"
            qualifier = "seen" if m.call_source == "screen" else "ago"
            call = m.last_call or "Not available"
            if m.call_done is not None:
                call += "  ·  " + ("returned" if m.call_done else "called")
            if self.zoom and m.subagents and h < 17:
                inside(3, f"{age} · {cost_label(m)} · {token_label(m)} tokens", "muted")
                inside(4, badge + " · " + agent.workspace, status_style)
            elif h >= 12:
                put(x + 2, y + 3, agent.workspace, "muted", max(1, w - len(badge) - 6))
                if w > len(badge) + 8:
                    put(x + w - len(badge) - 2, y + 3, badge, status_style, len(badge))
                core_rows = 2 if h >= 14 else 1
                core_top = 4
                for cx, cy, text, style in core_runs(phase, w - 4, core_rows, animation_time,
                                                   agent.pane_id, motion=self.motion):
                    # Generated glyphs are already one-cell and bounded to the
                    # card. Re-sanitizing each changing animation run wastes work.
                    commands.append(Draw(x + 2 + cx, y + core_top + cy, text, style))
                call_row = 6 if h >= 14 else 5
                # Keep the call and its age in separate columns.
                latest = "▸ " + call if m.last_call else "Latest call —"
                right = (f"seen {last_age}" if qualifier == "seen" else f"{last_age} ago") if m.call_at else ""
                call_width = w - 4 - len(right) - (2 if right else 0)
                put(x + 2, y + call_row, latest, "settled" if settled else "accent" if m.last_call else "muted", call_width)
                if w >= 40:
                    put(x + w - len(right) - 2, y + call_row, right, "muted", len(right))
                elif h < 14:
                    inside(6, f"Last call {last_age}" if m.call_at else "", "muted")
                inside(7, "» " + m.last_message if m.last_message else "Message unavailable", "settled" if settled else "normal")
                if m.subagents and h < 14:
                    inside(6, f"↳ {len(m.subagents)} subagent{'s' if len(m.subagents) != 1 else ''} · z details", "accent")
                divider = 8
                labels_row, values_row = 9, 10
                put(x + 1, y + divider, "─" * (w - 2), "border", w - 2)
                column = (w - 4) // 3
                for offset, label, value in ((0, "SESSION", age), (column, "TOKENS", token_label(m)),
                                             (column * 2, "EST. COST" if m.cost is None and m.estimated_cost is not None else "API COST",
                                              cost_label(m) if cost_value(m) is not None else "Unavailable")):
                    put(x + 2 + offset, y + labels_row, label, "muted", column)
                    put(x + 2 + offset, y + values_row, value, "settled" if settled else "metric", column)
                if h >= 14:
                    if m.subagents:
                        if not self.zoom or h < 17:
                            lines, _, _ = children_lines(m.subagents, w - 4, h - 13 - int(bool(m.issue)), now)
                            for row, (text, style) in enumerate(lines, 12):
                                inside(row, text, style)
                    elif m.trail:
                        inside(12, "TRAIL", "muted")
                        for offset, entry in enumerate(m.trail[-max(1, (w - 12) // 2):]):
                            glyph, style = tool_glyph(entry.name)
                            put(x + 8 + offset * 2, y + 12, glyph,
                                "failed" if entry.error else style, 1)
                    else:
                        inside(12, "TRAIL", "muted")
                        put(x + 8, y + 12, "—", "muted", 1)
                    if m.issue:
                        inside(h - 2, "! " + m.issue, "blocked")
            else:
                inside(3, f"Last  {call}", "accent" if m.last_call else "muted")
                call_label = "Seen" if m.call_source == "screen" else "Call"
                inside(4, f"Time  {age}  ·  {call_label} {last_age}", "muted")
                inside(5, f"API   {cost_label(m) if cost_value(m) is not None else 'Unavailable'}  ·  {token_label(m)} tokens", "metric")
                put(x + 2, y + 6, badge, status_style, len(badge))
                if w > len(badge) + 8:
                    compact_detail = (f"↳ {len(m.subagents)} subagent{'s' if len(m.subagents) != 1 else ''} · z" if m.subagents else
                                      agent.workspace if h >= 9 else "» " + m.last_message if m.last_message else "Message unavailable")
                    put(x + len(badge) + 4, y + 6, compact_detail, "muted", w - len(badge) - 6)
                inside(7, "» " + m.last_message if m.last_message else "Message unavailable", "muted")

            if self.zoom and m.subagents and h >= 8:
                # Children get the unused lower half first. Verbose parent
                # provenance follows only when it fits; scrolling reaches all
                # child rows even on a short terminal.
                last = h - 2 - int(bool(m.issue))
                reserve = 3 if h >= 22 else 0
                start = 12 if h >= 17 else 5
                lines, capacity, offset = children_lines(m.subagents, w - 4,
                    last - reserve - start + 1, now, self.child_offset, scrolling=True)
                self.child_offsets[agent.pane_id] = offset
                self.child_capacity, self.child_count = capacity, len(m.subagents)
                for row, (text, style) in enumerate(lines, start):
                    inside(row, text, style)
                row = start + len(lines)
                details = [("DETAILS", "muted"),
                           ("Metrics source   " + (m.source or "Herdr status"), "muted")]
                if m.call_detail:
                    details.append(("Tool target      " + m.call_detail, "normal"))
                details.extend([("API cost         " + cost_detail(m), "muted"),
                                ("Token coverage   Partial transcript · lower bound" if m.tokens_partial else
                                 "Token count      Includes input, output and cached input", "muted"),
                                ("Cost coverage    Partial usage/model history · lower bound" if m.estimate_partial and m.cost is None else
                                 f"Model / effort   {m.model or 'Not reported'}@{m.effort or '?'}", "muted"),
                                ("Latest call      " + ("Terminal observation" if m.call_source == "screen" else "Session transcript" if m.call_source else "Not reported"), "muted")])
                for text, style in details[:max(0, last - reserve - row + 1)]:
                    inside(row, text, style)
                    row += 1
                if reserve:
                    message_age = duration(now - m.message_at) + " ago" if m.message_at else ""
                    inside(row, "LATEST ASSISTANT MESSAGE  " + message_age, "muted")
                    message_lines = wrap_cells(m.last_message or "Message unavailable", w - 4)
                    available = min(6, last - row)
                    for index, line in enumerate(message_lines[:available]):
                        if index == available - 1 and len(message_lines) > available:
                            line = clip(line, w - 5) + "…"
                        inside(row + 1 + index, line)
            elif self.zoom and h >= 23:
                inside(14, "DETAILS", "muted")
                observed = duration(now - m.status_since) if m.status_since else "—"
                inside(15, f"Status observed  ≥{observed}   ·   {agent.pane_id}")
                inside(16, f"Model / effort   {m.model or 'Not reported'}@{m.effort or '?'}")
                inside(17, f"Metrics source   {m.source or 'Herdr status'}")
                inside(18, "Latest call      " + ("Terminal observation" if m.call_source == "screen" else
                                                  "Session transcript" if m.call_source else "Not reported"))
                extra = int(bool(m.call_detail) and h >= 28)
                if extra:
                    inside(19, "Tool target      " + m.call_detail)
                inside(19 + extra, "API cost         " + cost_detail(m), "muted")
                inside(20 + extra, "Token count      Includes input, output and cached input", "muted")
                if m.tokens_partial:
                    inside(21 + extra, "Token coverage   Partial transcript · shown as a lower bound", "muted")
                elif m.estimate_partial and m.cost is None:
                    inside(21 + extra, "Cost coverage    Partial usage/model history · shown as a lower bound", "muted")
                if h >= 27:
                    message_row = 22 + extra
                    message_age = duration(now - m.message_at) + " ago" if m.message_at else ""
                    inside(message_row, "LATEST ASSISTANT MESSAGE  " + message_age, "muted")
                    lines = wrap_cells(m.last_message or "Message unavailable", w - 4)
                    available = max(0, min(6, h - message_row - 2))
                    for row, line in enumerate(lines[:available]):
                        if row == available - 1 and len(lines) > available:
                            line = clip(line, w - 5) + "…"
                        inside(message_row + 1 + row, line)

        if self.searching or self.query:
            put(0, height - 2, "  / " + self.query + ("▏" if self.searching else ""), "selected")
        elif self.message:
            put(0, height - 2, "  " + self.message, "blocked")
        elif self.chosen:
            a = self.chosen
            m = state.metrics.get(a.pane_id, EMPTY_METRICS)
            detail = m.issue or ("Activity read unavailable" if a.pane_id in state.errors else m.source or "Herdr status")
            children = f" · subagents {children_summary(m.subagents)} · z details" if m.subagents else ""
            put(0, height - 2, f"  {a.name}{children} · {a.workspace}  /  {detail}", "muted")
        legend = "  ↑↓←→ select   Enter/click open   z details   / filter   r refresh   Esc close"
        if self.page_count > 1 and not self.zoom:
            legend = "  Arrows select  Enter/click open  PgUp/PgDn pages  z details  / filter  Esc close"
        if self.searching:
            legend = "  Type to filter   Enter finish   Esc clear filter"
        elif self.zoom and self.child_count:
            legend = "  PgUp/PgDn scroll subagents   ←→ agent   Enter open   z grid   / filter   Esc back"
        put(0, height - 1, legend, "muted")
        return commands


def plain_frame(commands: list[Draw], width: int, height: int) -> str:
    """Render commands without terminal controls, for fixtures and inspection."""
    rows = [[" "] * width for _ in range(height)]
    for command in commands:
        x = command.x
        for char in command.text:
            size = cell_width(char)
            if size == 0:
                if x > 0:
                    previous = x - 1
                    while previous > 0 and rows[command.y][previous] == "":
                        previous -= 1
                    rows[command.y][previous] += char
                continue
            if x + size > width:
                break
            rows[command.y][x] = char
            for continuation in range(1, size):
                rows[command.y][x + continuation] = ""
            x += size
    return "\n".join("".join(row) for row in rows)
