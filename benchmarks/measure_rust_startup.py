#!/usr/bin/env python3
"""Repeat fresh-process startup separately when long benchmark runs show drift."""
import argparse
import hashlib
import json
from pathlib import Path
import platform

import compare_rust as compare


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=int, default=20)
    parser.add_argument("--fixture", type=Path, default=compare.ROOT / "dist/rust-comparison/agents-6.json")
    parser.add_argument("--output", type=Path, default=compare.ROOT / "benchmarks/rust-startup-repeat.json")
    args = parser.parse_args()
    if args.pairs < 1:
        parser.error("pair count must be positive")
    result = {"synthetic": True, "python": platform.python_version(), "platform": platform.platform(),
              "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
              "pairs": args.pairs, "raw_pairs": []}
    for i in range(args.pairs):
        pair = {}
        for runtime in (("python", "rust") if i % 2 == 0 else ("rust", "python")):
            pair[runtime] = compare.terminal_run(compare.command(runtime, args.fixture, "--mode", "pty"),
                                                1, [b"\x1bOC", b"\x1bOD"])
        compare.close_enough(pair["python"]["key_states"], pair["rust"]["key_states"])
        result["raw_pairs"].append(pair)
    for runtime in ("python", "rust"):
        values = [p[runtime]["first_frame_ms"] for p in result["raw_pairs"]]
        result[runtime] = dict(compare.stats(values), min=min(values))
    compare.write_json(args.output, result)
    print(json.dumps({k: result[k] for k in ("pairs", "python", "rust")}, indent=2))


if __name__ == "__main__":
    main()
