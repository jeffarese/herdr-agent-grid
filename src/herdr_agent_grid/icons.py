"""Optional harness marks from the user's existing Herdr Agent Icons Max font."""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import sys

VENDORS = ("claude", "codex", "opencode", "omp", "cline", "mastracode", "kimi", "kilo",
           "maki", "pi", "hermes", "cursor", "copilot", "deepseek", "gemini", "gpt",
           "qwen", "grok", "agy", "kiro", "amp", "devin", "qodercli", "glm",
           "kimchi", "muse", "crush")
LOGOS = {name: chr(0xE1A0 + i) for i, name in enumerate(VENDORS)}
FALLBACK = {"claude": "✻", "codex": "◈", "gemini": "✦", "pi": "π", "hermes": "◇",
            "opencode": "◧", "cursor": "▹", "copilot": "⌘", "crush": "♥"}


def normalize(value: str) -> str:
    kind = value.lower().strip().split(":", 1)[0]
    return {"claude code": "claude", "cursor-agent": "cursor", "open-code": "opencode",
            "antigravity": "agy"}.get(kind, kind)


@lru_cache(maxsize=1)
def font_path() -> Path | None:
    home = Path.home()
    directories = [home / "Library/Fonts", Path("/Library/Fonts")] if sys.platform == "darwin" else [
        Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share"))) / "fonts",
        home / ".fonts", Path("/usr/local/share/fonts"), Path("/usr/share/fonts")]
    for directory in directories:
        found = next(directory.glob("HerdrAgentIconsMax*.ttf"), None)
        if found:
            return found
    return None


def logo_for(kind: str, mode: str = "auto") -> str:
    kind = normalize(kind)
    if mode == "ascii":
        return {"claude": "C", "codex": "X", "gemini": "G"}.get(kind, kind[:1].upper() or "?")
    if mode == "font" or (mode == "auto" and font_path()):
        return LOGOS.get(kind, "◇")
    return FALLBACK.get(kind, "◇")
