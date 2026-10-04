"""Small phase animations inspired by agent-swarm, plus observed tool trails.

The core is a phase indicator, not a throughput chart. Finished and blocked
states are still; reduced-motion mode renders a label for every phase.
"""
from __future__ import annotations

import math
import re

from .telemetry import Metrics

PHASES = {"thinking": ("✻ THINK", "thinking"), "writing": ("✎ WRITE", "writing"),
          "tool": ("▸ TOOL", "tool"), "working": ("● WORK", "thinking"),
          "blocked": ("! NEEDS INPUT", "blocked"), "done": ("✓ DONE", "done"),
          "idle": ("○ IDLE", "idle"), "unknown": ("? UNKNOWN", "unknown")}
TOOLS = {"Read": ("R", "tool:read"), "Grep": ("G", "tool:read"), "Glob": ("g", "tool:read"),
         "Bash": ("$", "tool"), "exec_command": ("$", "tool"), "shell_command": ("$", "tool"),
         "Edit": ("E", "done"), "apply_patch": ("E", "done"), "Write": ("W", "done"),
         "WebFetch": ("F", "tool:web"), "WebSearch": ("S", "tool:web"),
         "Agent": ("A", "tool:agent"), "spawn_agent": ("A", "tool:agent"),
         "Skill": ("K", "writing"), "TodoWrite": ("T", "muted"), "LSP": ("L", "writing")}


def phase_for(status: str, metrics: Metrics) -> str:
    if status != "working":
        return status if status in PHASES else "unknown"
    return metrics.phase if metrics.phase in ("thinking", "writing", "tool") else "working"


def tool_glyph(name: str) -> tuple[str, str]:
    short = name.rsplit(".", 1)[-1]
    if short.startswith("mcp__") or name.startswith("mcp__"):
        return "m", "accent"
    return TOOLS.get(short, (short[:1].upper() or "·", "muted"))


def model_label(model: str) -> str:
    short = model.removeprefix("openai/").removeprefix("anthropic/")
    match = re.fullmatch(r"claude-(opus|sonnet|haiku)(?:-(\d+)-(\d+))?(?:-\d{8})?", short)
    if match:
        family, major, minor = match.groups()
        return family.title() + (f" {major}.{minor}" if major and minor else "")
    return short or "?"


def model_effort(model: str, effort: str) -> str:
    return model_label(model) + "@" + (effort or "?")


def core_runs(phase: str, width: int, rows: int, tick: float, identity: str,
              motion: bool = True) -> list[tuple[int, int, str, str]]:
    """Batch adjacent cells of the same color without changing the image."""
    lines = [[] for _ in range(max(1, rows))]
    for x, y, char, style in core(phase, width, rows, tick, identity, motion):
        line = lines[y]
        if line and line[-1][0] + len(line[-1][2]) == x and line[-1][3] == style:
            start, _, text, _ = line[-1]
            line[-1] = (start, y, text + char, style)
        else:
            line.append((x, y, char, style))
    return [run for line in lines for run in line]


def core(phase: str, width: int, rows: int, tick: float, identity: str,
         motion: bool = True) -> list[tuple[int, int, str, str]]:
    width, rows = max(1, width), max(1, rows)
    cells = []
    tone = PHASES.get(phase, PHASES["unknown"])[1]
    if not motion or phase not in ("thinking", "writing", "tool", "working"):
        label = {"done": "✓ complete", "blocked": "! waiting for input", "idle": "○ ready",
                 "unknown": "? status unavailable"}.get(phase, PHASES[phase][0].lower())
        label = label[:width]
        start = max(0, (width - len(label)) // 2)
        for x in range(width):
            char = label[x - start] if start <= x < start + len(label) else "─"
            cells.append((x, rows // 2, char, tone if start <= x < start + len(label) else "border"))
        return cells
    seed = sum(ord(c) for c in identity) % 97
    if phase == "tool":
        center = (math.sin(tick * 1.2 + seed) + 1) * (width - 1) / 2
        for x in range(width):
            intensity = max(0, 1 - abs(x - center) / 5)
            char = "█" if intensity > .8 else "▓" if intensity > .55 else "▒" if intensity > .25 else "░" if intensity else "─"
            cells.append((x, rows - 1, char, f"tool:{max(0, min(3, int(intensity * 4)))}"))
            if rows > 1 and intensity > .5:
                cells.append((x, 0, "·" if intensity < .85 else "•", "tool:2"))
    elif phase == "writing":
        bars = " ▁▂▃▄▅▆▇█"
        for x in range(width):
            wave = .5 + .3 * math.sin(x * .55 + tick * 2.2 + seed) + .2 * math.sin(x * .17 - tick * 1.5)
            filled = int(max(0, min(rows * 8, wave * rows * 8)))
            for row in range(rows):
                amount = max(0, min(8, filled - (rows - row - 1) * 8))
                cells.append((x, row, bars[amount], f"writing:{min(3, amount // 2)}"))
    else:
        # Two-column, four-row Braille dot cells carry a quiet plasma texture.
        dots = ((0, 0, 1), (0, 1, 2), (0, 2, 4), (0, 3, 64),
                (1, 0, 8), (1, 1, 16), (1, 2, 32), (1, 3, 128))
        for row in range(rows):
            for x in range(width):
                bits = 0
                for dx, dy, bit in dots:
                    px, py = x * 2 + dx, row * 4 + dy
                    v = (math.sin(px * .19 + tick * 1.4 + seed) +
                         math.sin(py * .7 - tick * 1.2) + math.sin((px + py) * .14 + tick * .8)) / 3
                    if v > .2:
                        bits |= bit
                cells.append((x, row, chr(0x2800 + bits) if bits else " ",
                              f"thinking:{min(3, bin(bits).count('1') // 2)}"))
    return cells
