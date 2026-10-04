#!/usr/bin/env python3
"""Repeatable synthetic benchmarks. No Herdr connection or real transcripts.

Run each revision in a separate process. Timings are CPU time except the PTY
wall measurement; output includes raw sample medians and p95, not assertions.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import sys
import tempfile
import time
import tracemalloc


def samples(fn, repeats=40):
    for _ in range(3):
        fn()
    values = []
    for _ in range(repeats):
        start = time.process_time_ns()
        fn()
        values.append((time.process_time_ns() - start) / 1_000_000)
    values.sort()
    return {"median_ms": statistics.median(values), "p95_ms": values[min(len(values) - 1, int(len(values) * .95))], "samples": len(values)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="measurement")
    parser.add_argument("--messages", type=int, default=4000)
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source / "src"))
    package = "herdr_agent_grid" if (source / "src/herdr_agent_grid").exists() else "herdr_grid"
    demo = importlib.import_module(package + ".demo")
    view_module = importlib.import_module(package + ".view")
    telemetry = importlib.import_module(package + ".telemetry")
    refresh = importlib.import_module(package + ".refresh")
    app = importlib.import_module(package + ".app")
    results = {"label": args.label, "python": platform.python_version(), "platform": platform.platform(),
               "fixture": {"terminal": [140, 38], "messages": args.messages, "synthetic": True}, "measurements": {}}
    state = demo.demo_state()
    results["fixture"]["demo_children"] = sum(len(getattr(m, "subagents", ())) for m in state.metrics.values())
    original_agents, original_metrics = state.agents, state.metrics
    for count in (6, 24, 100, 1000):
        if hasattr(state, "revision"):
            state.revision = count
        state.agents = [replace(original_agents[i % 6], pane_id=f"w1:p{i}") for i in range(count)]
        state.metrics = {a.pane_id: replace(original_metrics[original_agents[i % 6].pane_id]) for i, a in enumerate(state.agents)}
        view = view_module.View(icons="unicode")
        view.arrange(state, 140, 38)
        results["measurements"][f"draw_{count}_agents"] = samples(lambda: view.draw(state, 140, 38, animation_time=1.3))
        tick = 0
        def moving_frame():
            nonlocal tick
            tick += 1
            view.draw(state, 140, 38, animation_time=tick / 10)
        results["measurements"][f"draw_moving_{count}_agents"] = samples(moving_frame)
        # A later full page catches linear selected-index/card-number scans.
        view.selected = view.items[max(0, len(view.items) - 2)].pane_id
        view.arrange(state, 140, 38)
        results["measurements"][f"draw_later_page_{count}_agents"] = samples(lambda: view.draw(state, 140, 38, animation_time=1.3))
        def navigate():
            view.move(-1)
            view.arrange(state, 140, 38)
            view.move(1)
            view.arrange(state, 140, 38)
        results["measurements"][f"navigate_{count}_agents"] = samples(navigate)
        refresher = refresh.Refresher(None)
        refresher.state = state
        previous_state = None
        def frame_prepare():
            nonlocal previous_state
            frame_state = refresher.get(previous_state) if hasattr(app, "state_key") else refresher.get()
            previous_state = frame_state
            view.arrange(frame_state, 140, 38)
            if hasattr(app, "state_key"):
                app.state_key(frame_state)
            else:
                repr(frame_state)
        results["measurements"][f"prepare_{count}_agents"] = samples(frame_prepare)
        results["measurements"][f"copy_snapshot_{count}_agents"] = samples(refresher.get)
    with tempfile.TemporaryDirectory(prefix="agent-grid-bench-") as directory:
        path = Path(directory) / "session.jsonl"
        with path.open("w") as f:
            for i in range(args.messages):
                f.write(json.dumps({"type": "assistant", "timestamp": "2026-10-04T10:00:00Z", "effort": "high",
                     "message": {"id": f"msg-{i}", "model": "claude-opus-5-5", "usage": {
                     "input_tokens": 10, "cache_read_input_tokens": 10_000, "cache_creation_input_tokens": 100,
                     "output_tokens": 100, "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 0}},
                     "content": [{"type": "text", "text": "Synthetic status update: checking the next implementation step."}]}}) + "\n")
        results["fixture"]["transcript_bytes"] = path.stat().st_size
        def cold_read():
            cursor = telemetry.Cursor(path)
            telemetry.Telemetry.update(cursor, "claude")
            return cursor
        results["measurements"]["cold_transcript"] = samples(cold_read, repeats=7)
        cursor = cold_read()
        results["measurements"]["unchanged_transcript_poll"] = samples(lambda: telemetry.Telemetry.update(cursor, "claude"), repeats=100)
        tracemalloc.start()
        cold_read()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results["measurements"]["cold_transcript_peak_bytes"] = peak
        # Appending and consuming a single message is the steady-state path.
        next_record = json.dumps({"type": "assistant", "message": {"id": "append", "model": "claude-opus-5-5",
                       "usage": {"input_tokens": 10, "output_tokens": 100}}}) + "\n"
        append_times = []
        for i in range(50):
            with path.open("a") as f:
                f.write(next_record.replace('"append"', f'"append-{i}"'))
            start = time.process_time_ns()
            telemetry.Telemetry.update(cursor, "claude")
            append_times.append((time.process_time_ns() - start) / 1_000_000)
        results["measurements"]["append_one_message"] = {"median_ms": statistics.median(append_times), "p95_ms": sorted(append_times)[47], "samples": 50}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
