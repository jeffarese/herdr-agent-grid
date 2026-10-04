#!/usr/bin/env python3
"""Link the plugin and add a backed-up, idempotent shortcut to Herdr config."""
from __future__ import annotations

import argparse
from datetime import datetime
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parent
ACTION = "herdr-agent-grid.open"
LEGACY_ACTION = "herdr-grid.open"
KEYS = ["cmd+g", "prefix+a", "ctrl+alt+g"]
BLOCK = '''

# Agent Grid: full-panel agent status cards
[[keys.command]]
key = ["cmd+g", "prefix+a", "ctrl+alt+g"]
type = "plugin_action"
command = "herdr-agent-grid.open"
description = "agent grid"
'''


def config_with_shortcut(text: str) -> str:
    config = tomllib.loads(text)
    commands = config.get("keys", {}).get("command", [])
    legacy = any(c.get("type") == "plugin_action" and c.get("command") == LEGACY_ACTION for c in commands)
    if legacy:
        # Preserve the user's bindings, comments and formatting during rename.
        # Restrict substitution to parsed plugin-action command entries.
        sections = re.split(r'(?m)(?=^\s*\[\[keys\.command\]\])', text)
        for i, section in enumerate(sections):
            if re.match(r'\s*\[\[keys\.command\]\]', section):
                command = tomllib.loads(section).get("keys", {}).get("command", [{}])[0]
                if command.get("type") == "plugin_action" and command.get("command") == LEGACY_ACTION:
                    sections[i] = re.sub(r'''(?m)^(\s*command\s*=\s*)(["'])herdr-grid\.open\2''',
                                         lambda m: m[1] + m[2] + ACTION + m[2], section)
        result = "".join(sections)
        if any(c.get("command") == LEGACY_ACTION for c in tomllib.loads(result).get("keys", {}).get("command", [])):
            raise ValueError("Legacy shortcut uses unsupported formatting; change herdr-grid.open to herdr-agent-grid.open")
        return result
    for command in commands:
        if command.get("type") == "plugin_action" and command.get("command") == ACTION:
            return text
    for name, value in config.get("keys", {}).items():
        if name != "command" and isinstance(value, str) and value in KEYS:
            raise ValueError(f"Shortcut {value} is already bound to {name}")
    for command in commands:
        bindings = command.get("key", [])
        if isinstance(bindings, str):
            bindings = [bindings]
        collisions = set(KEYS) & set(bindings)
        if collisions:
            raise ValueError(f"Shortcut {', '.join(sorted(collisions))} is already in use")
    result = text.rstrip() + BLOCK
    tomllib.loads(result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install Herdr Agent Grid and its shortcuts")
    parser.add_argument("--config", type=Path, default=Path(os.environ.get(
        "HERDR_CONFIG_PATH", str(Path.home() / ".config/herdr/config.toml"))))
    parser.add_argument("--open", action="store_true", help="open the grid after installing")
    args = parser.parse_args(argv)
    config = args.config.expanduser().resolve()
    binary = os.environ.get("HERDR_BIN_PATH")
    if not binary or not os.access(binary, os.X_OK):
        binary = shutil.which("herdr")
    if not binary:
        print("Herdr is not on PATH", file=sys.stderr)
        return 1
    env = dict(os.environ, HERDR_CONFIG_PATH=str(config))

    def herdr(*parts):
        subprocess.run([binary, *parts], env=env, check=True)

    try:
        before = config.read_text() if config.exists() else ""
        after = config_with_shortcut(before)
        # Validate the shortcut before changing the plugin registry.
        herdr("plugin", "link", str(ROOT), "--enabled")
        if after != before:
            config.parent.mkdir(parents=True, exist_ok=True)
            if config.exists():
                backup = config.with_name(config.name + ".bak-grid-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
                shutil.copy2(config, backup)
                print(f"Config backup: {backup}")
            fd, temp = tempfile.mkstemp(prefix=".grid-config-", dir=config.parent)
            try:
                with os.fdopen(fd, "w") as out:
                    out.write(after)
                    out.flush()
                    os.fsync(out.fileno())
                if config.exists():
                    os.chmod(temp, config.stat().st_mode & 0o777)
                # Refuse to replace a config edited concurrently.
                current = config.read_text() if config.exists() else ""
                if current != before:
                    raise ValueError("Herdr config changed during installation; rerun the installer")
                os.replace(temp, config)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        try:
            herdr("server", "reload-config")
        except subprocess.CalledProcessError:
            print("Plugin linked and shortcut saved. Live reload failed; reload Herdr config in the app.", file=sys.stderr)
            return 1
        print("Agent Grid installed: Cmd+G, prefix then A, or Ctrl+Alt+G")
        if args.open:
            herdr("plugin", "action", "invoke", ACTION)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Install failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
