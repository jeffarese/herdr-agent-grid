#!/usr/bin/env python3
"""Synthetic child discovery and polling costs, without a live provider store."""
import argparse
import json
from pathlib import Path
import platform
import sys
import tempfile
from unittest.mock import patch

from measure import samples

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from herdr_agent_grid.model import Agent
from herdr_agent_grid.subagents import Subagents
from herdr_agent_grid.telemetry import Cursor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = {"python": platform.python_version(), "platform": platform.platform(),
               "fixture": {"sessions": 1000, "children": 10, "synthetic": True}, "measurements": {}}
    with tempfile.TemporaryDirectory(prefix="grid-child-bench-") as directory:
        root = Path(directory)
        for i in range(1000):
            child = i < 10
            payload = {"id": f"session-{i}"}
            if child:
                payload.update(parent_thread_id="parent", agent_nickname=f"Child {i}")
            records = [{"type": "session_meta", "timestamp": "2026-10-04T10:00:00Z", "payload": payload},
                       {"type": "turn_context", "payload": {"model": "gpt-6.1-sol", "effort": "high"}},
                       {"type": "token_usage_record", "payload": {"thread_token_usage": {
                           "input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100}}}]
            (root / f"session-{i}.jsonl").write_text("".join(json.dumps(record) + "\n" for record in records))
        agent = Agent("w1:p1", "codex", "Parent", "Task", "Project", "working")
        parent = Cursor(root / "parent.jsonl", session_id="parent")
        with patch("herdr_agent_grid.subagents.roots", return_value=[root]):
            results["measurements"]["cold_index_1000_headers"] = samples(lambda: Subagents().index_codex(), repeats=10)
            reader = Subagents()
            reader.read(agent, parent)
            def warm_index():
                reader.scan_after = 0
                reader.index_codex()
            results["measurements"]["warm_index_1000_cached_headers"] = samples(warm_index, repeats=20)
            # No store scan is due: this isolates incremental child polling.
            results["measurements"]["unchanged_10_children_poll"] = samples(lambda: reader.read(agent, parent), repeats=100)
            assert len(reader.read(agent, parent)) == 10
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
