#!/usr/bin/env python3
"""Measure the real curses process through a drained PTY, with synthetic data."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import pty
import resource
import select
import struct
import subprocess
import sys
import termios
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agents", type=int, choices=(6, 24, 100, 1000), default=6)
    parser.add_argument("--scenarios", nargs="+", choices=("active", "settled", "reduced_motion"),
                        default=("active", "settled", "reduced_motion"))
    args = parser.parse_args()
    package = "herdr_agent_grid" if (args.source / "src/herdr_agent_grid").exists() else "herdr_grid"
    results = {"python": platform.python_version(), "seconds": args.seconds, "terminal": [140, 38], "agents": args.agents, "versioned_snapshots": True, "synthetic": True, "scenarios": {}}
    for mode in args.scenarios:
        script = f'''import sys
sys.path.insert(0, {str(args.source.resolve() / 'src')!r})
from dataclasses import replace
from {package} import cli
from {package}.demo import demo_state
state = demo_state()
agents, metrics = state.agents, state.metrics
state.agents = [replace(agents[i % 6], pane_id=f"p{{i}}") for i in range({args.agents})]
state.metrics = {{a.pane_id: replace(metrics[agents[i % 6].pane_id]) for i, a in enumerate(state.agents)}}
if hasattr(state, "revision"): state.revision = 1
if {mode!r} == "settled": state.agents = [replace(a, status="done") for a in state.agents]
cli.demo_state = lambda: state
raise SystemExit(cli.main(["--demo", "--icons", "unicode"] + (["--no-motion"] if {mode!r} == "reduced_motion" else [])))
'''
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 38, 140, 0, 0))
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        process = subprocess.Popen([sys.executable, "-c", script], stdin=slave, stdout=slave, stderr=slave,
                                   env=dict(os.environ, TERM="xterm-256color", HERDR_SOCKET_PATH="", HERDR_ENV="0"))
        os.close(slave)
        output_bytes, tail = 0, bytearray()
        start = time.monotonic()
        while time.monotonic() - start < args.seconds and process.poll() is None:
            if select.select([master], [], [], .05)[0]:
                try:
                    chunk = os.read(master, 65536)
                    output_bytes += len(chunk)
                    tail.extend(chunk)
                    if len(tail) > 4000: del tail[:-4000]
                except OSError:
                    break
        key_at = time.monotonic()
        if process.poll() is None:
            os.write(master, b"q")
        while process.poll() is None and time.monotonic() - key_at < 3:
            if select.select([master], [], [], .01)[0]:
                try: os.read(master, 65536)
                except OSError: break
        process.wait(timeout=3)
        exit_ms = (time.monotonic() - key_at) * 1000
        elapsed = time.monotonic() - start
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
        os.close(master)
        if process.returncode:
            raise RuntimeError(tail.decode(errors="replace"))
        results["scenarios"][mode] = {"cpu_seconds": cpu, "wall_seconds": elapsed, "one_core_cpu_percent": cpu / elapsed * 100,
                                        "terminal_bytes_per_second": output_bytes / elapsed,
                                        "close_latency_ms": exit_ms, "peak_rss_bytes": after.ru_maxrss * (1 if sys.platform == "darwin" else 1024)}
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__": main()
