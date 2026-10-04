#!/usr/bin/env python3
"""Python side of the shared-fixture comparison; uses the shipped app/view/parser."""
from __future__ import annotations

import argparse
import curses
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from herdr_agent_grid import app
from herdr_agent_grid.model import Agent
from herdr_agent_grid.refresh import Refresher, State
from herdr_agent_grid.telemetry import Cursor, Metrics, Subagent, Telemetry, ToolCall
from herdr_agent_grid.view import View


def load(path):
    fixture = json.loads(Path(path).read_text())
    assert fixture["schema"] == 1
    raw = fixture["state"]
    metrics = {}
    for pid, item in raw["metrics"].items():
        item = dict(item)
        item["trail"] = tuple(ToolCall(**c) for c in item["trail"])
        item["subagents"] = tuple(Subagent(**c) for c in item["subagents"])
        metrics[pid] = Metrics(**item)
    state = State(**dict(raw, agents=[Agent(**a) for a in raw["agents"]], metrics=metrics))
    # Freeze displayed ages, retain real monotonic/process clocks for timing.
    time.time = lambda: fixture["now"]
    state.updated = time.monotonic() + 3600
    return fixture, state


def measure(fn, batch, samples):
    for _ in range(3):
        fn()
    values = []
    for _ in range(samples):
        start = time.process_time_ns()
        for _ in range(batch):
            fn()
        values.append((time.process_time_ns() - start) / 1_000_000 / batch)
    return values


def micro(state, args):
    checksum = 0
    op = args.operation
    if op in ("render", "navigate", "filter"):
        view = View(icons="unicode")
        view.arrange(state, 140, 38)
        tick = 0
        def operation():
            nonlocal tick, checksum
            if op == "navigate":
                view.move(1, wrap=True)
                view.arrange(state, 140, 38)
            if op == "filter":
                view.query = "grid" if not view.query else ""
            tick += 1
            checksum += len(view.draw(state, 140, 38, animation_time=tick / 10))
    elif op == "cold_transcript":
        def operation():
            nonlocal checksum
            cursor = Cursor(Path(args.transcript))
            Telemetry.update(cursor, "claude")
            checksum += cursor.metrics.tokens or 0
    elif op == "unchanged_poll":
        cursor = Cursor(Path(args.transcript))
        Telemetry.update(cursor, "claude")
        def operation():
            nonlocal checksum
            Telemetry.update(cursor, "claude")
            checksum += cursor.metrics.tokens or 0
    elif op == "append_message":
        cursor = Cursor(Path(args.transcript))
        Telemetry.update(cursor, "claude")
        values = []
        records = Path(args.append_records).read_text().splitlines(keepends=True)
        with Path(args.transcript).open("a") as stream:
            for i in range(args.samples * args.batch):
                stream.write(records[i])
                stream.flush()
                start = time.process_time_ns()
                Telemetry.update(cursor, "claude")
                values.append((time.process_time_ns() - start) / 1_000_000)
                checksum += cursor.metrics.tokens or 0
        return {"samples_ms": values, "checksum": checksum}
    else:
        raise ValueError(op)
    values = measure(operation, args.batch, args.samples)
    return {"samples_ms": values, "checksum": checksum}


class CountingScreen:
    def __init__(self, screen):
        self.screen, self.inputs = screen, 0
    def __getattr__(self, name):
        return getattr(self.screen, name)
    def get_wch(self):
        key = self.screen.get_wch()
        self.inputs += 1
        return key


def terminal(state, args):
    os.environ["HERDR_AGENT_GRID_ICONS"] = "unicode"
    os.environ["HERDR_AGENT_GRID_MOTION"] = "off" if args.no_motion else "on"
    # Reuse the production snapshot path when the replay worker is enabled.
    stop = threading.Event()
    publisher = Refresher(None)
    publisher.state = state
    if args.background_transcript:
        def replay():
            while not stop.is_set():
                start = time.monotonic()
                cursor = Cursor(Path(args.background_transcript))
                Telemetry.update(cursor, "claude")
                with publisher.lock:
                    publisher.state.metrics[state.agents[0].pane_id] = cursor.metrics
                    publisher.state.revision += 1
                stop.wait(max(0, args.replay_ms / 1000 - (time.monotonic() - start)))
        worker = threading.Thread(target=replay, name="benchmark-replay", daemon=True)
        worker.start()
        # app.run owns this object, but its worker is already started above.
        publisher.start = lambda: None
        publisher.request = lambda *a, **k: None
        publisher.close = stop.set
        app.Refresher = lambda _: publisher
    def run(screen):
        counted = CountingScreen(screen)
        def observed(view, current):
            marker = {"input": counted.inputs, "selected": view.selected,
                      "query": view.query, "zoom": view.zoom, "revision": current.revision}
            sys.stdout.write("\x1b]777;" + json.dumps(marker, separators=(",", ":")) + "\x07")
            sys.stdout.flush()
        return app.run(counted, object() if args.background_transcript else None,
                       None if args.background_transcript else state, frame_observer=observed)
    try:
        curses.wrapper(run)
    finally:
        stop.set()
        if args.background_transcript:
            worker.join(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--mode", choices=("frames", "micro", "pty", "parse-sequence"), default="pty")
    parser.add_argument("--cases")
    parser.add_argument("--operation", default="render")
    parser.add_argument("--batch", type=int, default=100)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--transcript")
    parser.add_argument("--append-records")
    parser.add_argument("--no-motion", action="store_true")
    parser.add_argument("--background-transcript")
    parser.add_argument("--replay-ms", type=int, default=100)
    args = parser.parse_args()
    _, state = load(args.fixture)
    if args.mode == "micro":
        result = micro(state, args)
    elif args.mode == "frames":
        result = []
        for case in json.loads(Path(args.cases).read_text()):
            view = View(icons="unicode", motion=case.get("motion", True))
            for key in ("selected", "query", "zoom", "searching"):
                setattr(view, key, case.get(key, getattr(view, key)))
            if view.selected:
                view.child_offsets[view.selected] = case.get("child_offset", 0)
            result.append([asdict(c) for c in view.draw(state, case["width"], case["height"], case.get("tick", 1.3))])
    elif args.mode == "parse-sequence":
        result = []
        cursor = Cursor(Path(args.transcript))
        for op in json.loads(Path(args.cases).read_text()):
            if "write" in op:
                cursor.path.write_text(op["write"])
            if "append" in op:
                with cursor.path.open("a") as stream:
                    stream.write(op["append"])
            Telemetry.update(cursor, "claude")
            result.append({"metrics": asdict(cursor.metrics), "offset": cursor.offset})
    else:
        terminal(state, args)
        return
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
