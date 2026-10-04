#!/usr/bin/env python3
"""Same-data Python/Rust comparison with parity gates and alternating paired runs.

No real sessions or Herdr IPC. Rust is an experimental renderer + bounded Claude
parser, not a replacement for the live plugin. See rust-comparison.md for scope.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import pty
import re
import select
import shutil
import statistics
import struct
import subprocess
import sys
import sysconfig
import termios
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from herdr_agent_grid.demo import demo_state
from herdr_agent_grid.pricing import RATES
from herdr_agent_grid.theme import terminal_colors, xterm_color
from herdr_agent_grid.view import plain_frame, Draw

RUST = ROOT / "experiments/rust-grid/target/release/herdr-grid-bench"
PYTHON = [sys.executable, str(ROOT / "benchmarks/rust_compare_worker.py")]
MARKER = re.compile(rb"\x1b\]777;(.*?)\x07")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return path


def fixture(directory, count, settled=False):
    # Capture the existing demo once, then shift every timestamp by the same
    # amount. Fixed displayed time prevents timing samples changing age text.
    state = demo_state()
    now = 1_791_115_200.0  # 2026-10-04 12:00 UTC
    original_now = time.time()
    delta = now - original_now
    originals, original_metrics = state.agents, state.metrics
    state.agents = [replace(originals[i % 6], pane_id=f"p{i:04}",
                            status="done" if settled else originals[i % 6].status) for i in range(count)]
    state.metrics = {}
    for i, agent in enumerate(state.agents):
        m = replace(original_metrics[originals[i % 6].pane_id])
        for name in ("call_at", "started_at", "seen_at", "status_since", "message_at"):
            value = getattr(m, name)
            if value is not None:
                setattr(m, name, float(round(value + delta)))
        m.trail = tuple(replace(c, at=float(round(c.at + delta)) if c.at else None) for c in m.trail)
        m.subagents = tuple(replace(c, started_at=float(round(c.started_at + delta)) if c.started_at else None,
                                   finished_at=float(round(c.finished_at + delta)) if c.finished_at else None) for c in m.subagents)
        state.metrics[agent.pane_id] = m
    state.previews, state.updated, state.revision = {}, 1.0, 1
    styles = {name: {"color": None, "bold": name in ("title", "metric"), "reverse": False}
              for name in ("normal", "title", "metric")}
    for name, color in terminal_colors().items():
        if name not in styles:
            styles[name] = {"color": xterm_color(color),
                            "bold": name in ("brand", "selected") or name.startswith(("focus:", "harness:")), "reverse": False}
    for status in ("working", "done", "blocked", "idle", "unknown"):
        styles["selection:" + status] = dict(styles[status], bold=True, reverse=True)
    return write_json(directory / f"agents-{count}{'-settled' if settled else ''}.json",
                      {"schema": 1, "now": now, "state": asdict(state), "styles": styles,
                       "rates": {name: asdict(rate) for name, rate in RATES.items()}})


def transcript(directory, messages=4000):
    path = directory / "session.jsonl"
    with path.open("w") as stream:
        for i in range(messages):
            stream.write(json.dumps({"type": "assistant", "timestamp": "2026-10-04T10:00:00Z", "effort": "high",
                "message": {"id": f"msg-{i}", "model": "claude-opus-5-5", "usage": {
                    "input_tokens": 10, "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 100,
                    "output_tokens": 100, "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 0}},
                    "content": [{"type": "text", "text": "Synthetic status update: checking the next implementation step."}]}}) + "\n")
    return path


def command(runtime, fixture_path, *args):
    return (PYTHON if runtime == "python" else [str(RUST)]) + ["--fixture", str(fixture_path), *map(str, args)]


def run_json(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{cmd[0]} failed ({result.returncode}): {result.stderr[-4000:]}")
    return json.loads(result.stdout)


def close_enough(a, b, path="root"):
    if isinstance(a, dict) and isinstance(b, dict):
        assert a.keys() == b.keys(), (path, a.keys(), b.keys())
        for key in a:
            close_enough(a[key], b[key], f"{path}.{key}")
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        assert len(a) == len(b), (path, len(a), len(b))
        for i, (left, right) in enumerate(zip(a, b)):
            close_enough(left, right, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9), (path, a, b)
    else:
        assert a == b, (path, a, b)


def validate(directory, fixtures, log):
    frames = 0
    for count, path in fixtures.items():
        cases = []
        for w, h in ((80, 24), (140, 38), (200, 60), (10, 5)):
            for motion in (True, False):
                for tick in (.1, 1.3, 5.7):
                    cases.append({"width": w, "height": h, "tick": tick, "motion": motion})
        for selected in ("p0000", "p0001", "p0002", f"p{count - 1:04}"):
            cases.extend([{"width": 140, "height": 38, "selected": selected},
                          {"width": 140, "height": 38, "selected": selected, "zoom": True},
                          {"width": 80, "height": 24, "selected": selected, "zoom": True},
                          {"width": 140, "height": 17, "selected": selected, "zoom": True}])
        for query in ("grid", "working", "no such agent", ""):
            cases.append({"width": 140, "height": 38, "query": query, "searching": True})
        case_file = write_json(directory / "frames.json", cases)
        left = run_json(command("python", path, "--mode", "frames", "--cases", case_file))
        right = run_json(command("rust", path, "--mode", "frames", "--cases", case_file))
        for i, (a, b) in enumerate(zip(left, right)):
            try:
                close_enough(a, b)
            except AssertionError as error:
                write_json(directory / "frame-mismatch.json", {"case": cases[i], "python": a, "rust": b})
                raise AssertionError(f"{count} agents, case {i} {cases[i]}: {error}") from error
        frames += len(cases)
        if count == 6:
            (directory / "python-frame.txt").write_text(plain_frame([Draw(**c) for c in left[0]], 80, 24))
            (directory / "rust-frame.txt").write_text(plain_frame([Draw(**c) for c in right[0]], 80, 24))
    record = {"type": "assistant", "timestamp": "2026-10-04T10:00:00Z", "effort": "high",
              "message": {"id": "stream", "model": "claude-opus-5-5", "usage": {"input_tokens": 100, "output_tokens": 10},
                          "content": [{"type": "text", "text": "Synthetic café: wide 界 and combining é."}]}}
    encoded = lambda r: json.dumps(r, ensure_ascii=False) + "\n"
    duplicate = json.loads(json.dumps(record))
    duplicate["message"]["usage"]["output_tokens"] = 30
    duplicate["message"]["content"].append({"type": "tool_use", "id": "call", "name": "Read", "input": {"file_path": "src/demo.rs"}})
    result = {"type": "user", "timestamp": "2026-10-04T10:00:01Z", "message": {"content": [{"type": "tool_result", "tool_use_id": "call", "is_error": True}]}}
    unknown = json.loads(json.dumps(record)); unknown["message"].update(id="unknown", model="unpriced-model")
    partial = encoded(record).replace('"stream"', '"partial"')
    cases = [{"write": encoded(record) + "malformed json\n"}, {"append": encoded(duplicate)},
             {"append": encoded(result) + encoded(unknown)}, {"append": partial[:len(partial)//2]},
             {"append": partial[len(partial)//2:]}, {}, {"write": encoded(record)},
             {"append": encoded({"type": "cost-state", "totalCostUSD": .5, "modelUsage": {"demo": {"inputTokens": 123, "outputTokens": 456}}})},
             {"append": encoded(duplicate)}, {"write": log.read_text()}, {},
             {"write": log.read_text() * 2}, {}]
    case_file = write_json(directory / "parse-cases.json", cases)
    left = run_json(command("python", fixtures[6], "--mode", "parse-sequence", "--cases", case_file, "--transcript", directory / "python-parity.jsonl"))
    right = run_json(command("rust", fixtures[6], "--mode", "parse-sequence", "--cases", case_file, "--transcript", directory / "rust-parity.jsonl"))
    close_enough(left, right)
    return {"draw_command_frames": frames, "incremental_parser_states": len(cases), "passed": True}


def stats(values):
    ordered = sorted(values)
    return {"median": statistics.median(ordered), "p95": ordered[max(0, math.ceil(len(ordered) * .95) - 1)],
            "max": ordered[-1], "samples": len(ordered)}


def paired_micro(directory, fixtures, log, rounds):
    measurements = {}
    append_records = directory / "append-records.jsonl"
    append_records.write_text("".join(json.dumps({"type": "assistant", "message": {"id": f"append-{i}",
        "model": "claude-opus-5-5", "usage": {"input_tokens": 10, "output_tokens": 100}}}) + "\n" for i in range(50)))
    for count, path in fixtures.items():
        for op in ("render", "navigate", "filter"):
            pairs = []
            for r in range(rounds):
                pair = {}
                for runtime in (("python", "rust") if r % 2 == 0 else ("rust", "python")):
                    pair[runtime] = run_json(command(runtime, path, "--mode", "micro", "--operation", op, "--batch", 100, "--samples", 5))
                assert pair["python"]["checksum"] == pair["rust"]["checksum"], (count, op, "work mismatch")
                pairs.append(pair)
            measurements[f"{op}_{count}"] = summarize_micro(pairs)
    for op, batch in (("cold_transcript", 1), ("unchanged_poll", 100), ("append_message", 10)):
        pairs = []
        for r in range(rounds):
            pair = {}
            for runtime in (("python", "rust") if r % 2 == 0 else ("rust", "python")):
                fresh = directory / f"{runtime}-micro.jsonl"
                shutil.copyfile(log, fresh)
                pair[runtime] = run_json(command(runtime, fixtures[6], "--mode", "micro", "--operation", op,
                                                "--transcript", fresh, "--append-records", append_records,
                                                "--batch", batch, "--samples", 5))
            assert pair["python"]["checksum"] == pair["rust"]["checksum"], (op, "work mismatch")
            pairs.append(pair)
        measurements[op] = summarize_micro(pairs)
    return measurements


def summarize_micro(pairs):
    ratios = [statistics.median(p["python"]["samples_ms"]) / statistics.median(p["rust"]["samples_ms"]) for p in pairs]
    return {"unit": "ms CPU per operation", "python": stats([v for p in pairs for v in p["python"]["samples_ms"]]),
            "rust": stats([v for p in pairs for v in p["rust"]["samples_ms"]]),
            "paired_speedup_median": statistics.median(ratios), "raw_pairs": pairs}


def terminal_run(cmd, seconds, keys):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 38, 140, 0, 0))
    env = dict(os.environ, TERM="xterm-256color", HERDR_ENV="0", HERDR_SOCKET_PATH="", HERDR_AGENT_GRID_THEME="dark")
    env.pop("COLUMNS", None); env.pop("LINES", None)
    start = time.perf_counter_ns()
    process = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=ROOT)
    os.close(slave)
    buffer, tail, output_bytes, trace_bytes = bytearray(), bytearray(), 0, 0
    markers = []
    max_revision = 0
    reaped = False
    def drain(timeout):
        nonlocal output_bytes, trace_bytes, max_revision
        if select.select([master], [], [], timeout)[0]:
            try: chunk = os.read(master, 65536)
            except OSError: chunk = b""
            output_bytes += len(chunk); buffer.extend(chunk); tail.extend(chunk)
            if len(tail) > 4000: del tail[:-4000]
            for match in MARKER.finditer(buffer):
                marker = json.loads(match[1])
                markers.append((time.perf_counter_ns(), marker))
                max_revision = max(max_revision, marker["revision"])
                trace_bytes += match.end() - match.start()
            if markers:
                last = list(MARKER.finditer(buffer))
                if last: del buffer[:last[-1].end()]
            if len(buffer) > 65536: del buffer[:-65536]
    def await_input(number, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for at, marker in markers:
                if marker["input"] == number: return at, marker
            drain(.01)
        raise RuntimeError(f"No painted acknowledgement {number}: {tail.decode(errors='replace')}")
    try:
        first_at, first_marker = await_input(0)
        first_frame_ms = (first_at - start) / 1_000_000
        # Exclude cold startup/cache warmup from input latency. Keep it as its own metric.
        warm_until = time.monotonic() + .5
        while time.monotonic() < warm_until: drain(.01)
        latencies, states = [], []
        for i, key in enumerate(keys, 1):
            markers.clear()
            sent = time.perf_counter_ns()
            os.write(master, key)
            painted, marker = await_input(i)
            latencies.append((painted - sent) / 1_000_000)
            states.append({k: marker[k] for k in ("input", "selected", "query", "zoom")})
            # Small, equal pacing interval, after acknowledgement; not a timer poll.
            deadline = time.monotonic() + .01
            while time.monotonic() < deadline: drain(.001)
        until = max(time.monotonic(), (start / 1e9) + seconds)
        while time.monotonic() < until: drain(.01)
        sent = time.perf_counter_ns(); os.write(master, b"q")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            drain(.001)
            pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid:
                reaped = True; process.returncode = os.waitstatus_to_exitcode(status); break
        if not reaped: raise RuntimeError("process did not exit")
        exit_ms = (time.perf_counter_ns() - sent) / 1_000_000
        elapsed = (time.perf_counter_ns() - start) / 1e9
        if process.returncode: raise RuntimeError(tail.decode(errors="replace"))
        return {"first_frame_ms": first_frame_ms, "key_to_paint_ms": stats(latencies), "raw_key_ms": latencies,
                "key_states": states, "exit_ms": exit_ms, "cpu_seconds": usage.ru_utime + usage.ru_stime,
                "wall_seconds": elapsed, "one_core_cpu_percent": (usage.ru_utime + usage.ru_stime) / elapsed * 100,
                "published_snapshots_observed": max_revision,
                "peak_rss_bytes": usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                "terminal_bytes_per_second": (output_bytes - trace_bytes) / elapsed}
    finally:
        if not reaped:
            process.kill(); process.wait()
        os.close(master)


def paired_terminal(fixtures, settled, log, rounds, seconds):
    # Full arrows, wrap/page navigation, details, and incremental search. End on
    # unfiltered grid so q exits both programs immediately.
    keys = ([b"\x1bOC", b"\x1bOD", b"\t", b"z", b"z", b"\x1bOD"] * 12
            + [b"/", b"g", b"r", b"i", b"d", b"\x7f", b"\x7f", b"\x7f", b"\x7f", b"\r"]
            + [b"]", b"[", b"\x1bOB", b"\x1bOA"] * 5)
    results = {}
    for name, path, extra in (("active_6", fixtures[6], []), ("settled_6", settled, []),
                              ("reduced_motion_6", fixtures[6], ["--no-motion"]),
                              ("compact_1000", fixtures[1000], []),
                              ("background_refresh_6", fixtures[6], ["--background-transcript", str(log), "--replay-ms", "750"]),
                              ("background_burst_6", fixtures[6], ["--background-transcript", str(log), "--replay-ms", "100"])):
        print(f"  {name}: {rounds} pairs", flush=True)
        pairs = []
        for r in range(rounds):
            pair = {}
            for runtime in (("python", "rust") if r % 2 == 0 else ("rust", "python")):
                pair[runtime] = terminal_run(command(runtime, path, "--mode", "pty", *extra), seconds, keys)
            close_enough(pair["python"]["key_states"], pair["rust"]["key_states"])
            pairs.append(pair)
        results[name] = {"python": {}, "rust": {}, "raw_pairs": pairs}
        for runtime in ("python", "rust"):
            values = [p[runtime] for p in pairs]
            results[name][runtime] = {"first_frame_ms": stats([v["first_frame_ms"] for v in values]),
                "key_to_paint_ms": stats([t for v in values for t in v["raw_key_ms"]]),
                "one_core_cpu_percent": stats([v["one_core_cpu_percent"] for v in values]),
                "peak_rss_bytes": stats([v["peak_rss_bytes"] for v in values]),
                "terminal_bytes_per_second": stats([v["terminal_bytes_per_second"] for v in values])}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks/rust-comparison.json")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "dist/rust-comparison")
    parser.add_argument("--micro-rounds", type=int, default=9)
    parser.add_argument("--pty-rounds", type=int, default=5)
    parser.add_argument("--seconds", type=float, default=6)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--skip-pty", action="store_true")
    args = parser.parse_args()
    if args.micro_rounds < 1 or args.pty_rounds < 1 or args.seconds <= 0:
        parser.error("round counts and seconds must be positive")
    if not RUST.exists(): parser.error("Build release Rust first: cargo build --release --locked --manifest-path experiments/rust-grid/Cargo.toml")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HERDR_AGENT_GRID_THEME"] = "dark"
    fixtures = {n: fixture(args.work_dir, n) for n in (6, 24, 100, 1000)}
    settled = fixture(args.work_dir, 6, settled=True)
    log = transcript(args.work_dir)
    print("Checking frame and incremental-parser equivalence…", flush=True)
    parity = validate(args.work_dir, fixtures, log)
    if args.validate_only:
        print(json.dumps(parity, indent=2)); return
    rustc = shutil.which("rustc") or str(Path.home()/".cargo/bin/rustc")
    sources = [*sorted((ROOT / "src/herdr_agent_grid").glob("*.py")),
               *sorted((ROOT / "experiments/rust-grid/src").glob("*.rs")),
               ROOT / "experiments/rust-grid/Cargo.toml", ROOT / "experiments/rust-grid/Cargo.lock",
               Path(__file__), ROOT / "benchmarks/rust_compare_worker.py"]
    result = {"schema": 1, "synthetic": True, "python": platform.python_version(), "platform": platform.platform(),
              "python_free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
              "rustc": subprocess.check_output([rustc, "--version"], text=True).strip(),
              "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
              "python_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "terminal": [140, 38], "micro_rounds": args.micro_rounds, "pty_rounds": args.pty_rounds,
              "seconds_per_pty_run": args.seconds, "parity": parity,
              "fixtures": {str(n): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size} for n,p in fixtures.items()},
              "transcript": {"sha256": hashlib.sha256(log.read_bytes()).hexdigest(), "bytes": log.stat().st_size, "messages": 4000}}
    print("Alternating paired CPU microbenchmarks…", flush=True)
    result["micro"] = paired_micro(args.work_dir, fixtures, log, args.micro_rounds)
    write_json(args.output, result)
    if not args.skip_pty:
        print("Alternating paired terminal/input measurements…", flush=True)
        result["pty"] = paired_terminal(fixtures, settled, log, args.pty_rounds, args.seconds)
        write_json(args.output, result)
    print(f"Wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
