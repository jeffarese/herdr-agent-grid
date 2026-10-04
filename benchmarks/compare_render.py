#!/usr/bin/env python3
"""Alternate before/after batches in one process to limit CPU-frequency drift."""
from __future__ import annotations
import argparse
from dataclasses import replace
import importlib
import importlib.util
import json
from pathlib import Path
import platform
import statistics
import sys
import time


def load(source, alias):
    package = source / "src/herdr_agent_grid"
    if not package.exists(): package = source / "src/herdr_grid"
    spec = importlib.util.spec_from_file_location(alias, package / "__init__.py", submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return importlib.import_module(alias + ".demo"), importlib.import_module(alias + ".view")


def fixture(modules, count, later=False):
    demo, view_module = modules
    state = demo.demo_state()
    agents, metrics = state.agents, state.metrics
    state.agents = [replace(agents[i % 6], pane_id=f"p{i}") for i in range(count)]
    state.metrics = {a.pane_id: replace(metrics[agents[i % 6].pane_id]) for i, a in enumerate(state.agents)}
    if hasattr(state, "revision"): state.revision = 1
    view = view_module.View(icons="unicode")
    view.arrange(state, 140, 38)
    if later: view.selected = view.items[max(0, len(view.items) - 2)].pane_id
    view.arrange(state, 140, 38)
    return state, view


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--batch", type=int, default=50)
    args = parser.parse_args()
    versions = {"before": load(args.before.resolve(), "_grid_before"), "after": load(args.after.resolve(), "_grid_after")}
    results = {"python": platform.python_version(), "platform": platform.platform(), "synthetic": True,
               "terminal": [140, 38], "rounds": args.rounds, "batch": args.batch,
               "method": "Alternating paired CPU-time batches; medians of batch means", "measurements": {}}
    for label, count, later, moving, navigation in (
        ("draw_6", 6, False, False, False), ("draw_moving_6", 6, False, True, False),
        ("draw_1000", 1000, False, False, False), ("draw_later_page_1000", 1000, True, False, False),
        ("navigate_1000", 1000, True, False, True)):
        fixtures = {name: fixture(modules, count, later) for name, modules in versions.items()}
        times = {name: [] for name in versions}
        def run(name, offset):
            state, view = fixtures[name]
            state.updated = time.monotonic()
            for i in range(args.batch):
                if navigation:
                    view.move(-1); view.arrange(state, 140, 38)
                    view.move(1); view.arrange(state, 140, 38)
                else:
                    view.draw(state, 140, 38, animation_time=(offset + i) / 10 if moving else 1.3)
        for name in versions: run(name, 0)
        for round in range(args.rounds):
            order = ("before", "after") if round % 2 == 0 else ("after", "before")
            for name in order:
                start = time.process_time_ns()
                run(name, round * args.batch)
                times[name].append((time.process_time_ns() - start) / 1_000_000 / args.batch)
        result = {name: {"median_ms": statistics.median(values), "samples_ms": values} for name, values in times.items()}
        result["paired_speedup_median"] = statistics.median(b / a for b, a in zip(times["before"], times["after"]))
        results["measurements"][label] = result
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({k: {"before_ms": v["before"]["median_ms"], "after_ms": v["after"]["median_ms"], "paired_speedup": v["paired_speedup_median"]} for k,v in results["measurements"].items()}, indent=2))

if __name__ == "__main__": main()
