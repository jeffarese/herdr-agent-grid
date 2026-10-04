#!/usr/bin/env python3
"""Generate the native-release report from checked-in raw measurements."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/"benchmarks/native-release.json").read_text())
micro=json.loads((ROOT/"benchmarks/native-micro.json").read_text())["micro"]
micro_rows=[]
for key,label in (("render_6","Six-card frame"),("render_1000","1,000-agent inventory frame"),("cold_transcript","Cold 4,000-message Claude transcript"),("unchanged_poll","Unchanged transcript poll")):
    value=micro[key]
    micro_rows.append(f"| {label} | {value['python']['median']:.4f} ms | {value['rust']['median']:.4f} ms | {value['paired_speedup_median']:.2f}× |")
rows=[]
for name,scenario in data["scenarios"].items():
    p,r=scenario["python"],scenario["rust"]
    label=name.removeprefix("live_").replace("_6","").replace("_"," ").title()
    for metric,key,scale,unit in (("Key-to-paint p95","key_to_paint_ms",1,"ms"),("Initial frame","first_frame_ms",1,"ms"),("All session metrics ready","ready_frame_ms",1,"ms"),("CPU, one core","one_core_cpu_percent",1,"%"),("Peak memory","peak_rss_bytes",1/1048576,"MiB")):
        if key not in p:continue
        stat="p95" if key=="key_to_paint_ms" else "median"
        rows.append(f"| {label} | {metric} | {p[key][stat]*scale:.2f} {unit} | {r[key][stat]*scale:.2f} {unit} |")
text=f'''# Native Rust release: full-application comparison

Python **1.1.1** reference versus the complete Rust **2.0.0** application.
The same synthetic Herdr socket, provider stores, child logs and keyboard events
feed both implementations. These results cover live transport, exact session
matching, Claude/Codex parsing, child discovery, background refresh and rendering.

![Native application performance](native-release.png)

| Workload | Measurement | Python | Rust |
| --- | --- | ---: | ---: |
{chr(10).join(rows)}

All values are medians across paired runs except the input-latency p95, which
is pooled across every recorded key. Each workload has {data['rounds']} alternating
Python/Rust pairs, {data['seconds']:g} seconds per process, and 70 key events per run.
The terminal is 140×38. All scenarios include six parent sessions and 18 children.

The active/reduced-motion scenarios have six working parents. The settled
scenario marks parents done. The same scripted navigation, zoom and search run
in every case, so “settled” does not mean the process is idle.

Initial frame means the first painted dashboard, which can still be loading.
“All session metrics ready” means a painted frame after every parent's telemetry
(including child discovery) has been read. No fixture is preloaded into the Rust
UI. Peak memory and CPU include startup and the cold reads; CPU is the average
percentage of one core over the entire short process, **not steady-state idle
CPU**. Key timing runs after 500 ms of warmup and may overlap remaining startup
work. It measures PTY key submission to the post-paint marker, not display-panel
or terminal-compositor latency. Identical post-paint observers add small overhead
to both runtimes.

The socket server is a separate process/thread in the harness and its CPU is
excluded for both applications. Logs and OS caches can be warm. The test runs
on a shared, working machine, so scheduler load and frequency changes affect
results; raw samples and ranges should be consulted rather than treating these
figures as guarantees. Current machine: `{data['platform']}`; Python
`{data['python']}`; optimized Rust release build.

Each Claude parent uses the same 4,000-message transcript; each Codex parent
uses 4,000 cumulative usage records. Every run starts a fresh application and
parses identical, unchanged bytes. Native session-store caches start empty.
The harness checks that both runtimes produce identical selection, query and
zoom states after all 70 inputs. Synthetic timestamps/messages/costs are used
throughout; no live user sessions or screenshots appear in this report.

## CPU microbenchmarks

The production renderer/parser also run through the controlled comparison
adapter with identical fixtures, fixed displayed time and matched output.
These are CPU milliseconds per operation, not terminal input latency.

| Operation | Python | Rust | Median paired speedup |
| --- | ---: | ---: | ---: |
{chr(10).join(micro_rows)}

[Raw microbenchmark samples](native-micro.json) include five alternating pairs.
The final rendering optimization avoids allocating sanitized copies of already
clean Unicode UI text. More invasive cache changes were left out once frame CPU
was well below one millisecond; measured input and data readiness were the
remaining useful targets. The worker wakeup addresses the latter directly.

## Correctness gates

- 176 matching draw-command frames across 6/24/100/1,000 agents, sizes, filters,
  zoom levels, Unicode text, child scrolling and reduced motion.
- Incremental file checkpoints plus replay of provider events from the existing
  telemetry, pricing and subagent regression suite.
- Same-model pricing cases covering cached tokens, long context, missing usage,
  unknown models and provider modifiers.
- Exact-store/parent matching, external symlink exclusion, long Codex headers,
  partial tails, streaming deduplication, file replacement and child resumes.
- Real PTY keyboard/mouse focus, persistent errors, clean exit and installation
  backups/idempotence; native Unix-socket and atomic-config checks.

The full local suite passes 93 Python-driven regression tests and eight native
Rust integration tests. CI repeats production checks on both architectures of
macOS and Linux; minimum Rust 1.88 and Python 3.11 compatibility are checked
separately. Timing thresholds are deliberately excluded from CI.

## Reproduce

```sh
cargo build --release --locked --examples --bin herdr-agent-grid
cargo build --release --locked --manifest-path experiments/rust-grid/Cargo.toml
python3 -m unittest discover -s tests -v
python3 benchmarks/compare_native.py --rounds 5 --seconds 4
python3 benchmarks/compare_rust.py --skip-pty --output dist/current-micro.json
python3 benchmarks/render_native_report.py
```

The report renderer uses matplotlib; the benchmark itself needs only Python's
standard library. [Full raw results](native-release.json) contain individual
runs, key latencies, source hashes and fixture hashes. [Pre-wakeup measurements](native-release-before.json) are retained. They exposed
a 250 ms UI polling delay: active-session readiness was about 263 ms before the
worker wakeup and 86 ms afterward. Those Rust revisions were measured in
separate runs, so that before/after difference is not an interleaved comparison.
Historical [prototype results](rust-comparison.md) remain unchanged and have a
narrower scope. The old prototype's much smaller memory number excludes live
session indexes and must not be attributed to the full application.
'''
(ROOT/"benchmarks/native-release.md").write_text(text)
try:import matplotlib.pyplot as plt
except ImportError:raise SystemExit("Report written. Install matplotlib to regenerate the chart.")
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'text.color':'#e5e7ef','axes.labelcolor':'#bbc2d4','xtick.color':'#bbc2d4','ytick.color':'#bbc2d4','axes.edgecolor':'#343b50','savefig.facecolor':'#111523'})
fig,axes=plt.subplots(1,3,figsize=(14,4.5),facecolor='#111523')
labels=['Active','Settled','Reduced motion'];colors=['#a8b2ca','#fb923c']
for ax,(metric,title,scale) in zip(axes,[('key_to_paint_ms','Input latency · p95 (ms)',1),('ready_frame_ms','All metrics ready · median (ms)',1),('one_core_cpu_percent','Whole-run CPU · median (%)',1)]):
    ax.set_facecolor('#111523')
    for j,runtime in enumerate(('python','rust')):
        values=[s[runtime][metric]['p95' if metric=='key_to_paint_ms' else 'median']*scale for s in data['scenarios'].values()]
        positions=[i+(j-.5)*.34 for i in range(len(values))]
        bars=ax.bar(positions,values,width=.3,color=colors[j],label='Python 1.1.1' if j==0 else 'Rust 2.0.0',zorder=3)
        for bar,value in zip(bars,values):ax.text(bar.get_x()+bar.get_width()/2,bar.get_height()+max(values)*.04,f'{value:.1f}',ha='center',va='bottom',fontsize=10,color=colors[j])
    ax.set_xticks(range(len(data['scenarios'])),labels[:len(data['scenarios'])]);ax.set_title(title,color='#e5e7ef',pad=18,loc='left',fontsize=12)
    ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',color='#293044',alpha=.55,zorder=0);ax.set_ylim(0,ax.get_ylim()[1]*1.22)
fig.suptitle('herdr-agent-grid 2.0  /  Native Rust',x=.055,y=.965,ha='left',fontsize=21,fontweight='bold')
fig.text(.055,.87,'Complete applications · identical synthetic Herdr data · 5 alternating pairs',color='#9da5ba',fontsize=11)
handles,legend=axes[0].get_legend_handles_labels();fig.legend(handles,legend,loc='lower center',ncol=2,frameon=False,bbox_to_anchor=(.5,.005))
fig.subplots_adjust(left=.055,right=.98,top=.75,bottom=.2,wspace=.28)
fig.savefig(ROOT/'benchmarks/native-release.png',dpi=170)
fig.savefig(ROOT/'benchmarks/native-release.svg')
