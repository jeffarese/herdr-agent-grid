"""Agent inventory and responsive, bounded tile geometry."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import re
import unicodedata


# Remove terminal controls even when an old server fails to strip them.
CONTROLS = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|"
                      r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[ -/]*[@-~]")


def clean(text: str) -> str:
    text = CONTROLS.sub("", text).replace("\r", "")
    return "".join(c for c in text if c in "\n\t" or
                   unicodedata.category(c) == "Co" or
                   not unicodedata.category(c).startswith("C"))


@lru_cache(maxsize=1024)
def cell_width(char: str) -> int:
    if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def clip(text: str, width: int, ellipsis: bool = False) -> str:
    text = str(text)
    if width <= 0:
        return ""
    if text.isascii() and text.isprintable():
        if len(text) <= width:
            return text
        return text[:width - 1] + "…" if ellipsis else text[:width]
    return _clip(text, width, ellipsis) if len(text) <= 1200 else _clip.__wrapped__(text, width, ellipsis)


@lru_cache(maxsize=1024)
def _clip(text: str, width: int, ellipsis: bool = False) -> str:
    text = clean(str(text)).replace("\n", " ").expandtabs(4)
    if width <= 0:
        return ""
    result, used = [], 0
    for char in text:
        size = cell_width(char)
        if used + size > width:
            if ellipsis:
                return clip("".join(result), width - 1) + "…"
            break
        if not result and size == 0:
            continue
        result.append(char)
        used += size
    return "".join(result)


def wrap_cells(text: str, width: int) -> list[str]:
    """Wrap words by terminal cells, retaining wide and combining characters."""
    width = max(1, width)
    lines, line, used = [], [], 0
    for word in clean(str(text)).split():
        if line:
            if used + 1 + sum(cell_width(c) for c in word) > width:
                lines.append("".join(line))
                line, used = [], 0
            else:
                line.append(" ")
                used += 1
        for char in word:
            size = cell_width(char)
            if size > width:
                char, size = "…", 1
            if used + size > width:
                lines.append("".join(line))
                line, used = [], 0
            if size or line:
                line.append(char)
                used += size
    if line:
        lines.append("".join(line))
    return lines


@dataclass(frozen=True)
class Agent:
    pane_id: str
    kind: str
    name: str
    title: str
    workspace: str
    status: str
    provider: str = ""
    session_kind: str = ""
    session_ref: str = ""
    cwd: str = ""
    terminal_id: str = ""
    state_seq: int = 0

    @property
    def identity(self) -> tuple:
        return self.pane_id, self.terminal_id, self.provider or self.kind, self.session_kind, self.session_ref


def agents_from(snapshot: dict) -> list[Agent]:
    spaces = {w["workspace_id"]: w for w in snapshot.get("workspaces", [])}
    tabs = {t["tab_id"]: t for t in snapshot.get("tabs", [])}
    panes = {p["pane_id"]: p for p in snapshot.get("panes", [])}
    seen, result = set(), []
    for a in snapshot.get("agents", []):
        pid = a.get("pane_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        pane = panes.get(pid, {})
        kind = a.get("display_agent") or a.get("agent") or pane.get("agent")
        if not kind:
            continue
        space = spaces.get(a.get("workspace_id"), {})
        tab = tabs.get(a.get("tab_id"), {})
        title = (tab.get("label") or a.get("terminal_title_stripped") or
                 pane.get("terminal_title_stripped") or a.get("title") or pid)
        session = a.get("agent_session") or pane.get("agent_session") or {}
        result.append(Agent(pid, clean(kind), clean(a.get("name") or kind),
                            clean(title), clean(space.get("label") or
                                                a.get("workspace_id") or ""),
                            a.get("agent_status", "unknown"),
                            a.get("agent") or pane.get("agent") or kind,
                            session.get("kind", ""), session.get("value", ""),
                            a.get("foreground_cwd") or a.get("cwd") or pane.get("cwd") or "",
                            a.get("terminal_id") or pane.get("terminal_id") or "",
                            a.get("state_change_seq", 0)))
    return result


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass(frozen=True)
class Layout:
    rects: tuple[Rect, ...]
    columns: int
    capacity: int


@lru_cache(maxsize=128)
def layout(width: int, height: int, count: int) -> Layout:
    """Fit every agent when readable; paginate only past physical capacity."""
    width, height = max(1, width), max(1, height)
    if count <= 0:
        return Layout((), 1, 1)
    max_cols = max(1, (width + 1) // 37)
    max_rows = max(1, (height + 1) // 9)
    capacity = max_cols * max_rows
    visible = min(count, capacity)
    candidates = []
    for cols in range(1, min(max_cols, visible) + 1):
        rows = math.ceil(visible / cols)
        if rows > max_rows:
            continue
        tile_w = (width - cols + 1) / cols
        tile_h = (height - rows + 1) / rows
        score = abs(math.log(max(0.1, tile_w / max(1, tile_h)) / 4.0))
        score += 0.6 * (cols * rows - visible) / visible
        candidates.append((score, cols, rows))
    _, cols, rows = min(candidates)
    rects = []
    for i in range(visible):
        col, row = i % cols, i // cols
        left = col * (width + 1) // cols
        right = (col + 1) * (width + 1) // cols - 1
        top = row * (height + 1) // rows
        bottom = (row + 1) * (height + 1) // rows - 1
        rects.append(Rect(left, top, max(1, right - left), max(1, bottom - top)))
    return Layout(tuple(rects), cols, visible)
