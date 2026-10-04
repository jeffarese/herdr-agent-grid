"""Shared role colors for terminal rendering and the exported preview."""
import os

RAMPS = {
    "thinking": ("#241a4d", "#45309a", "#7c5cff", "#b39dff"),
    "writing": ("#0a3442", "#0e6e85", "#14b3d1", "#5fe3f7"),
    "tool": ("#3a2405", "#7a4a06", "#d98a0b", "#ffb733"),
}
COLORS = {"normal": "#d6d9e6", "title": "#f0f2f9", "muted": "#9da5ba",
          "border": "#48506a", "selected": "#8fd9eb", "working": "#fb923c",
          "blocked": "#ffb733", "done": "#34d399", "idle": "#94a3b8",
          "unknown": "#c5a0ed", "accent": "#c5a0ed", "metric": "#f0f2f9",
          "brand": "#b39dff", "settled": "#aab2c5", "failed": "#f87171", "tool:read": "#93c5fd",
          "tool:web": "#f472b6", "tool:agent": "#fb923c"}
for phase, ramp in RAMPS.items():
    COLORS[phase] = ramp[3]
    for i, color in enumerate(ramp):
        COLORS[f"{phase}:{i}"] = color
for phase in ("working", "thinking", "writing", "tool", "blocked", "done", "idle", "unknown"):
    COLORS["focus:" + phase] = COLORS[phase]
for harness, color in {
    "claude": "#d97757", "codex": "#e9e9f0", "gemini": "#4285f4", "opencode": "#a8abbd",
    "cursor": "#5fe3f7", "deepseek": "#4d6bfe", "qwen": "#b39dff", "kimi": "#1783ff",
    "kiro": "#a770ff", "amp": "#ff8d65", "copilot": "#86c5a2", "pi": "#ffb733",
    "hermes": "#b39dff", "crush": "#ff388b", "glm": "#93c5fd", "muse": "#0082fb",
}.items():
    COLORS["harness:" + harness] = color


def terminal_colors() -> dict[str, str]:
    colors = dict(COLORS)
    if os.environ.get("HERDR_AGENT_GRID_THEME", os.environ.get("HERDR_GRID_THEME")) == "light":
        colors.update(muted="#646d82", border="#aab2c3", brand="#6742ae", selected="#147a92",
                      working="#b45309", done="#15704d", blocked="#985800", failed="#b82d3a", accent="#6742ae")
        for phase, ramp in RAMPS.items():
            colors[phase] = ramp[1]
            colors["focus:" + phase] = ramp[1]
            for i, color in enumerate(reversed(ramp)):
                colors[f"{phase}:{i}"] = color
        colors["harness:codex"] = "#343b4e"
        for status in ("working", "done", "blocked", "idle", "unknown"):
            colors["focus:" + status] = colors[status]
    return colors


def xterm_color(color: str) -> int:
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    levels = (0, 95, 135, 175, 215, 255)
    positions = [min(range(6), key=lambda i: abs(levels[i] - channel)) for channel in rgb]
    cube = 16 + 36 * positions[0] + 6 * positions[1] + positions[2]
    cube_error = sum((rgb[i] - levels[positions[i]]) ** 2 for i in range(3))
    gray = min(range(24), key=lambda i: sum((c - (8 + 10 * i)) ** 2 for c in rgb))
    gray_error = sum((c - (8 + 10 * gray)) ** 2 for c in rgb)
    return 232 + gray if gray_error < cube_error else cube
