"""Plugin launcher and diagnostic commands."""
from __future__ import annotations

import argparse
import curses
from dataclasses import asdict
import json
import os
import sys
import time

from . import __version__
from .app import run
from .client import Client, HerdrError
from .demo import demo_state
from .model import agents_from, clean
from .refresh import State
from .view import View, plain_frame
from .telemetry import Telemetry, screen_call


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A live Herdr agent dashboard with compact status cards")
    parser.add_argument("--version", action="version", version=f"herdr-agent-grid {__version__}")
    parser.add_argument("--demo", action="store_true", help="use six synthetic agent cards")
    parser.add_argument("--render", action="store_true", help="print a plain-text frame")
    parser.add_argument("--list", action="store_true", help="print current agents as JSON")
    parser.add_argument("--doctor", action="store_true", help="check the Herdr connection")
    parser.add_argument("--icons", choices=("auto", "font", "unicode", "ascii"), help="harness icon style")
    parser.add_argument("--no-motion", action="store_true", help="use still phase indicators")
    parser.add_argument("--width", type=int, default=160, help="plain-text frame width")
    parser.add_argument("--height", type=int, default=44, help="plain-text frame height")
    args = parser.parse_args(argv)
    if args.icons:
        os.environ["HERDR_AGENT_GRID_ICONS"] = args.icons
    if args.no_motion:
        os.environ["HERDR_AGENT_GRID_MOTION"] = "off"
    if not 1 <= args.width <= 1000 or not 1 <= args.height <= 300:
        parser.error("frame dimensions must be between 1×1 and 1000×300")
    if not args.demo and os.environ.get("HERDR_ENV") != "1":
        print("herdr-agent-grid: run from a Herdr pane (or use --demo)", file=sys.stderr)
        return 2
    client = None if args.demo else Client()
    try:
        if args.demo:
            state = demo_state()
        elif args.render or args.list or args.doctor:
            state = State(agents_from(client.snapshot()), updated=time.monotonic())
        else:
            state = None
        if args.doctor:
            print(json.dumps({"plugin": __version__, "herdr": "connected" if client else "demo",
                              "agents": len(state.agents), "socket": client.socket_path if client else None}, indent=2))
            return 0
        if args.list:
            print(json.dumps([asdict(a) for a in state.agents], indent=2, ensure_ascii=False))
            return 0
        if args.render:
            view = View()
            view.arrange(state, args.width, args.height)
            if client:
                telemetry = Telemetry()
                state.metrics = {a.pane_id: telemetry.read(a) for a in state.agents}
                for pane_id, lines in view.targets().items():
                    try:
                        state.previews[pane_id] = clean(client.read(pane_id, lines))
                        m = state.metrics[pane_id]
                        if not m.last_call:
                            m.last_call = screen_call(state.previews[pane_id])
                            m.call_source = "screen" if m.last_call else ""
                            m.call_at = time.time() if m.last_call else None
                    except HerdrError as error:
                        state.errors[pane_id] = str(error)
            print(plain_frame(view.draw(state, args.width, args.height), args.width, args.height))
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("herdr-agent-grid: needs a terminal; use the herdr-agent-grid.open plugin action", file=sys.stderr)
            return 2
        curses.wrapper(run, client, state)
        return 0
    except (HerdrError, curses.error) as error:
        print(f"herdr-agent-grid: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
