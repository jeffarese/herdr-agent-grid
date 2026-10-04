#!/usr/bin/env python3
"""Render the measured comparison as a report and a standalone SVG chart.

Requires matplotlib only for plotting, never for the plugin or benchmark.
"""
import argparse
import json
from pathlib import Path


def plots(result, target):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "text.color": "#d6d9e6",
                         "axes.labelcolor": "#9da5ba", "xtick.color": "#9da5ba", "ytick.color": "#d6d9e6",
                         "axes.facecolor": "#171d2e", "figure.facecolor": "#101525", "savefig.facecolor": "#101525"})
    fig, axes = plt.subplots(2, 3, figsize=(16, 9))
    colors = {"python": "#b39dff", "rust": "#fb923c"}
    def chart(ax, title, names, data, unit, log=False):
        y = np.arange(len(names))
        for runtime, offset in (("python", -.17), ("rust", .17)):
            values = data[runtime]
            ax.barh(y + offset, values, .29, color=colors[runtime], label=runtime.title())
            for row, value in enumerate(values):
                ax.annotate(f"{value:.3g}", (value, row + offset), xytext=(5, 0), textcoords="offset points",
                            va="center", color=colors[runtime], fontsize=10)
        ax.set_yticks(y, names); ax.invert_yaxis(); ax.set_title(title, loc="left", color="#f0f2f9", weight="bold", pad=14)
        if log: ax.set_xscale("log")
        else: ax.set_xlim(0, max(v for vs in data.values() for v in vs) * 1.25)
        ax.set_xlabel(unit); ax.grid(axis="x", alpha=.1); ax.set_axisbelow(True)
        for spine in ax.spines.values(): spine.set_visible(False)
        ax.tick_params(axis="y", length=0)
    pty = result["pty"]
    profiles = ["active_6", "compact_1000", "background_refresh_6", "background_burst_6"]
    names = ["6 cards, 3 working", "1,000 / paginated", "750 ms replay", "100 ms stress replay"]
    chart(axes[0, 0], "Input responsiveness · p95", names,
          {r: [pty[p][r]["key_to_paint_ms"]["p95"] for p in profiles] for r in colors}, "Key to paint (ms, log scale)", True)
    if "startup_repeat" in result:
        startup = result["startup_repeat"]
        chart(axes[0, 1], f"Startup · {startup['pairs']} additional pairs", ["6 cards, 3 working"],
              {r: [startup[r]["median"]] for r in colors}, "First frame (ms)")
        for annotation in axes[0, 1].texts:
            annotation.set_position((5, 5))
            annotation.set_va("bottom")
        for runtime, offset in (("python", -.17), ("rust", .17)):
            s = startup[runtime]
            axes[0, 1].errorbar(s["median"], offset, xerr=[[s["median"] - s["min"]], [s["max"] - s["median"]]],
                               fmt="none", ecolor="#f0f2f9", capsize=5, alpha=.8)
        axes[0, 1].set_xlim(0, max(startup[r]["max"] for r in colors) * 1.15)
        axes[0, 1].set_xlabel("First frame (ms)\nWhiskers: observed min–max", fontsize=10)
    else:
        chart(axes[0, 1], "First painted frame", ["6 cards, 3 working", "1,000 / paginated"],
              {r: [pty[p][r]["first_frame_ms"]["median"] for p in profiles[:2]] for r in colors}, "First frame (ms)")
    chart(axes[0, 2], "Peak process memory", names,
          {r: [pty[p][r]["peak_rss_bytes"]["median"] / 1024**2 for p in profiles] for r in colors}, "Peak RSS (MiB)")
    chart(axes[1, 0], "Draw command generation", ["6 agents", "24 agents", "100 agents", "1,000 agents"],
          {r: [result["micro"][f"render_{n}"][r]["median"] for n in (6, 24, 100, 1000)] for r in colors}, "CPU time / frame (ms)")
    chart(axes[1, 1], "Cold transcript read", ["4,000 messages / 1.80 MB"],
          {r: [result["micro"]["cold_transcript"][r]["median"]] for r in colors}, "CPU time / cold read (ms)")
    chart(axes[1, 2], "CPU consumption", names,
          {r: [pty[p][r]["one_core_cpu_percent"]["median"] for p in profiles] for r in colors}, "% of one core")
    fig.suptitle("HERDR AGENT GRID    Python × Rust", x=.055, y=.98, ha="left", color="#f0f2f9", fontsize=22, weight="bold")
    fig.text(.055, .93, "Same synthetic data · identical frame commands · paired alternating runs · release Rust", color="#9da5ba", fontsize=12)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", bbox_to_anchor=(.975, .99), ncols=2, frameon=False)
    fig.text(.055, .025, "macOS ARM64 · PTY acknowledgements, not display/compositor latency · experimental Rust scope; see report", color="#9da5ba", fontsize=10)
    fig.tight_layout(rect=(.025, .06, .99, .91), w_pad=2.5, h_pad=2.6)
    fig.savefig(target, metadata={"Date": None, "Creator": "herdr-agent-grid synthetic comparison"})
    fig.savefig(target.with_suffix(".png"), dpi=160, metadata={"Software": "herdr-agent-grid synthetic comparison"})


def report(result, target):
    micro, pty = result["micro"], result["pty"]
    m = lambda name, runtime: micro[name][runtime]["median"]
    lines = ["# Python vs Rust: same-data performance comparison", "",
        "The Rust prototype preserves the tested card output and keyboard behavior while reducing measured CPU work, memory and input latency on this machine. It remains an experimental implementation; the installed plugin uses Python.", "",
        "![Python and Rust benchmark comparison](rust-comparison.svg)", "",
        f"Measured on **2026-10-04**, {result['platform']}, Python {result['python']}, {result['rustc']}. All data is synthetic. Results describe this machine and workload, not every supported terminal.", "",
        "## Responsiveness and process measurements", "",
        "Latency is measured from the harness sending a key to receiving an acknowledgement written **after the resulting frame has been flushed**. This includes input handling, layout, drawing and terminal output. It does not measure a terminal emulator's display/compositor. Every key's selected agent, filter and detail state is checked across implementations.", "",
        "| Workload | Python key p95 | Rust key p95 | Python first frame | Rust first frame | Python peak RSS | Rust peak RSS |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    labels = {"active_6": "Six cards, active", "settled_6": "Six cards, settled", "reduced_motion_6": "Six cards, reduced motion",
              "compact_1000": "1,000-agent inventory, paginated", "background_refresh_6": "Cold replay every 750 ms", "background_burst_6": "Cold replay every 100 ms (stress)"}
    for name, values in pty.items():
        py, rs = values["python"], values["rust"]
        lines.append(f"| {labels[name]} | {py['key_to_paint_ms']['p95']:.2f} ms | {rs['key_to_paint_ms']['p95']:.2f} ms | {py['first_frame_ms']['median']:.1f} ms | {rs['first_frame_ms']['median']:.1f} ms | {py['peak_rss_bytes']['median']/1024**2:.1f} MiB | {rs['peak_rss_bytes']['median']/1024**2:.1f} MiB |")
    lines += ["", "First-frame and RSS columns are medians across fresh process runs. `wait4` supplies each process's own peak RSS, avoiding the cumulative-child high-water issue in the earlier benchmark. Input p95 pools the recorded keystrokes across paired runs; raw samples and per-run summaries are included in the JSON.", "",
        "## CPU work", "", "| Operation | Python median | Rust median | Median paired speedup |", "| --- | ---: | ---: | ---: |"]
    if "startup_repeat" in result:
        s = result["startup_repeat"]
        note = ["", "### Startup variability", "",
            f"The main run showed substantial Python startup variation, so a separate **{s['pairs']}-pair** alternating fresh-process repeat used the exact same six-agent fixture. Python first-frame median was **{s['python']['median']:.1f} ms** (observed range {s['python']['min']:.1f}–{s['python']['max']:.1f} ms); Rust was **{s['rust']['median']:.1f} ms** ({s['rust']['min']:.1f}–{s['rust']['max']:.1f} ms). The chart uses this repeat with min–max whiskers. The original profile measurements above remain available; startup is environment-sensitive and is not treated as a fixed speedup multiplier.", "",
            "[Startup repeat samples](rust-startup-repeat.json). Reproduce with `python3 benchmarks/measure_rust_startup.py` after generating fixtures."]
        pos = lines.index("## CPU work")
        lines[pos:pos] = note + [""]
    names = [(f"render_{n}", f"Draw commands, {n} agents") for n in (6, 24, 100, 1000)] + [
        ("navigate_1000", "Navigate + draw, 1,000 agents"), ("filter_1000", "Change filter + draw, 1,000 agents"),
        ("cold_transcript", "Cold read, 4,000 messages / 1.80 MB"), ("unchanged_poll", "Unchanged transcript poll"), ("append_message", "Consume one appended message")]
    for name, label in names:
        lines.append(f"| {label} | {m(name,'python'):.4f} ms | {m(name,'rust'):.4f} ms | {micro[name]['paired_speedup_median']:.2f}× |")
    lines += ["", "Drawing here means generating commands, excluding terminal I/O. The inventory is paginated at 140×38; the 1,000-agent case does not draw 1,000 simultaneous cards. Ratios are the median of within-round ratios, not a ratio of unrelated historical measurements.", "",
        "| Process workload | Python CPU | Rust CPU |", "| --- | ---: | ---: |"]
    for name, values in pty.items():
        lines.append(f"| {labels[name]} | {values['python']['one_core_cpu_percent']['median']:.2f}% | {values['rust']['one_core_cpu_percent']['median']:.2f}% |")
    lines += ["", "CPU percentages refer to one core and include startup, the same scripted input sequence, animations, and snapshot publication. They are not idle-only measurements and should not be compared directly with the earlier no-input PTY runs. Timing-marker bytes are excluded from terminal bandwidth; marker generation remains in both processes' CPU measurements.", "",
        "## Method and equivalence gates", "",
        f"- **{result['parity']['draw_command_frames']} frames** match every command's coordinate, text and style across sizes, motion settings, pages, zoom and filters.",
        f"- **{result['parity']['incremental_parser_states']} parser checkpoints** match metrics and byte offsets, including duplicate streaming usage, tool returns, Unicode, missing prices, partial lines, unchanged polls, replacement, cumulative cost state and bounded tails.",
        f"- **{result['micro_rounds']} alternating pairs** per microbenchmark; three warmup calls followed by timed CPU batches. Append writes happen outside the measured parse interval. Equal result checksums gate every pair.",
        f"- **{result['pty_rounds']} alternating pairs** per terminal workload, each targeting {result['seconds_per_pty_run']:g} seconds. Displayed ages are fixed. Terminal size, colors, input and data bytes are identical. Input sampling begins after a 500 ms warmup; first-frame startup remains a separate metric.",
        "- Python uses the shipped renderer, curses paint/input loop and parser. The only app instrumentation is an optional observer after paint; the normal plugin leaves it off. Rust is built in release mode with the committed lockfile.",
        "- The JSON records individual samples, fixture digests, source-file digests, compiler/interpreter versions and the Python base commit. Test/build/plot processes are not run alongside the final measurements.", "",
        "## Scope and interpretation", "",
        "The Rust prototype implements card rendering/navigation and the bounded incremental **Claude** parser exercised by these fixtures. It does **not** yet implement live Herdr IPC/focus, exact session discovery, the Codex parser, or subagent discovery/lifecycle; their display data is supplied by the shared fixture. Therefore this is evidence for the performance of these paths, not a completed migration or a whole-plugin production benchmark.", "",
        "The 750 ms replay uses the current refresh interval; the 100 ms replay deliberately stresses the background worker. Each cycle cold-reads the same 1.80 MB transcript, unlike ordinary incremental steady-state polling. Both workers target the same period, but a slow parser can miss that rate; `published_snapshots_observed` records observed progress. Replay is synthetic CPU pressure, not a claim about normal live workload. The unchanged/append microbenchmarks separately measure steady state.", "",
        "Python's GIL can delay UI work while its background thread parses; the observed replay results are consistent with that explanation, without proving it is the only cause. Rust uses a background thread and published snapshots. A Python process worker or free-threaded build could produce different results and was not evaluated here.", "",
        "Synthetic tests exclude live Herdr IPC, agent log writers, real terminal emulator painting, font behavior, and session discovery. Absolute timings vary with machine load and power state. Windows and other machines were not measured. CI verifies functional parity on Linux/macOS without performance thresholds.", "",
        "## Reproduce", "", "```sh", '. "$HOME/.cargo/env"', "cargo build --release --locked --manifest-path experiments/rust-grid/Cargo.toml",
        "python3 benchmarks/compare_rust.py --validate-only", "python3 benchmarks/compare_rust.py", "```", "",
        "Optional chart/report regeneration (requires matplotlib):", "", "```sh", "python3 -m venv dist/benchmark-plot-venv",
        "dist/benchmark-plot-venv/bin/pip install matplotlib", "dist/benchmark-plot-venv/bin/python benchmarks/render_rust_report.py", "```", "",
        "[Raw measurements](rust-comparison.json) · [Rust prototype](../experiments/rust-grid/) · [Harness](compare_rust.py)", ""]
    target.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path(__file__).with_name("rust-comparison.json"))
    args = parser.parse_args()
    result = json.loads(args.input.read_text())
    startup = args.input.with_name("rust-startup-repeat.json")
    if startup.exists():
        result["startup_repeat"] = json.loads(startup.read_text())
        assert result["startup_repeat"]["fixture_sha256"] == result["fixtures"]["6"]["sha256"]
    plots(result, args.input.with_suffix(".svg"))
    report(result, args.input.with_suffix(".md"))


if __name__ == "__main__":
    main()
