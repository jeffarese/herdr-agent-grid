# Performance measurements

Measured on **2026-10-04**, macOS 26.7.1 ARM64, Python 3.14.7.
Every agent, transcript and message is synthetic. No live Herdr connection is
used by these benchmarks. Values describe this machine and these fixtures.

## Subagent feature and visibility fix: 1.1.1

The updated six-card fixture includes three synthetic children. Moving frames
with the active-count badge and stronger selection take **0.362 ms** median CPU
time (40 samples). Ten unchanged child logs take
**0.232 ms** per poll (100 samples). A 1,000-session Codex store takes **51.4 ms**
for a cold header index and **24.5 ms** for a directory scan with cached headers
(10 and 20 samples). Discovery scans run at most every three seconds on the
background worker; ordinary child reads are incremental. These observations
are feature checks, not paired speedup claims against the earlier fixtures.

```sh
python3 benchmarks/measure.py --output /tmp/grid-subagents-render.json --label 1.1.1
python3 benchmarks/measure_subagents.py --output /tmp/grid-subagents-discovery.json
```

[Render measurements](subagents-render.json) ·
[Child discovery and polling](subagents-discovery.json)

## Second optimization pass: 1.0.1

The comparison below measures the **already optimized 1.0.0 implementation**
against 1.0.1. It alternates before/after CPU-time batches in the same process,
swapping their order each round to limit CPU-speed drift. Fifteen paired rounds
of 50 operations are measured after warm-up. Times are medians of batch means;
speedups are medians of the paired ratios. These are synthetic render/navigation
measurements, not live Herdr latency guarantees. The moving-frame result is
about 18% less CPU time per generated frame (a 1.22× speedup).

| Operation | 1.0.0 | 1.0.1 | Paired speedup |
| --- | ---: | ---: | ---: |
| Six cards, fixed animation frame | 0.429 ms | 0.350 ms | 1.22× |
| Six cards, moving animation | 0.451 ms | 0.369 ms | 1.22× |
| 1,000-agent inventory, first page | 0.853 ms | 0.131 ms | 6.50× |
| 1,000-agent inventory, later full page | 1.356 ms | 0.142 ms | 9.65× |
| Navigate back and forth, 1,000 agents | 0.214 ms | <0.01 ms | Constant-time pane lookup |

The gains come from caching fleet counts/cost coverage by snapshot revision,
retaining filtered inventories across selection changes, and indexing pane IDs
for selection and card numbering. Animation cells now batch into same-color
runs; generated, bounded glyphs avoid redundant sanitization. Core math and
animation colors remain unchanged. Tests compare every batched cell and style
and validate cache invalidation when inventory, metrics or filters change.

The benchmark now exercises **moving frames and later pages**. The first pass
only covered a fixed frame on the first page, missing the navigation/overview
bottleneck. Raw per-batch samples are in [round2-paired.json](round2-paired.json).
Exploratory standalone runs are retained separately; their changing absolute
CPU times are why the paired comparison is used for these improvement claims.
No further transcript parsing or memory improvement is claimed in this pass.

Compact cards have no visible animated core and now use the still-frame cadence.
Settled/reduced-motion panels wait up to 250 ms for background updates, while
keyboard/mouse input wakes the curses call immediately. Terminal fallback results
publish in completion order so one slow pane cannot hold back a faster pane.
These behaviors have dedicated regression tests.

Reproduce the second-pass comparison:

```sh
mkdir -p /tmp/grid-round2-before
tar -xzf benchmarks/round2-before-source.tar.gz -C /tmp/grid-round2-before
python3 benchmarks/compare_render.py --before /tmp/grid-round2-before --output /tmp/grid-paired.json
python3 benchmarks/pty_cpu.py --seconds 12 --output /tmp/grid-round2-pty.json
python3 benchmarks/pty_cpu.py --seconds 12 --agents 1000 --scenarios active --output /tmp/grid-1000-pty.json
```

The preserved second-pass source is 1.0.0, including all optimizations from the
first pass. New PTY runs use versioned snapshots to model the production cache
path. The first-pass PTY runs used mutable demo fixtures, so compare versions
within a pass rather than combining their CPU percentages.

### Second-pass PTY observations

The 12-second runs below include startup. CPU is a percentage of one core;
these are single process observations, separate from the paired render test.

| Scenario | 1.0.0 | 1.0.1 |
| --- | ---: | ---: |
| Six cards, three working | 1.72% | 1.18% |
| Six cards, all completed | 0.89% | 0.67% |
| Six cards, reduced motion | 1.03% | 0.77% |
| 1,000-agent inventory, compact cards | 1.95% | 0.59% |

Absolute results vary with machine load and CPU speed. No render bandwidth
reduction is claimed: output stayed about 1.02 kB/s for the compact-card scenario.
The last case benefits from both aggregate reuse and stopping invisible
animation ticks. The synthetic PTY has no live Herdr inventory or transcript I/O.

[Before, six cards](round2-before-pty.json) ·
[Final, six cards](round2-final-pty.json) ·
[Before, 1,000 agents](round2-before-1000-pty.json) ·
[Final, 1,000 agents](round2-final-1000-pty.json)

## First optimization pass: before and after

CPU-time medians from the same benchmark script and fixture dimensions:

| Operation | Baseline | Final | Result |
| --- | ---: | ---: | --- |
| Generate a six-card frame | 1.184 ms | 0.425 ms | 2.8× faster |
| Generate a frame from a 1,000-agent inventory | 2.125 ms | 0.747 ms | 2.8× faster |
| Cold parse: 4,000 messages / 1.80 MB | 343.037 ms | 52.756 ms | 6.5× faster |
| Consume one appended message | 0.323 ms | 0.046 ms | 6.9× faster |
| Prepare an unchanged 1,000-agent frame | 7.525 ms | <0.01 ms | Snapshot + layout reuse |

Generating a frame means producing draw commands; it excludes terminal I/O.
At 140×38, a 1,000-agent inventory is paginated, with nine cards visible.
Unchanged-frame preparation measures the hot snapshot/layout path, not the
cost of refreshing an entire inventory. Copying a fresh 1,000-agent snapshot
still takes about 2.26 ms, versus 2.24 ms before. No copy-speed gain is claimed.
Unchanged transcript polling is effectively unchanged at about 0.016 ms.
Peak traced allocations for a cold transcript are effectively unchanged at
about 5.70 MB. This is a latency/CPU improvement, not a memory reduction claim.

## Actual curses process

Both versions run through a drained PTY for **12 seconds per scenario**, at
140×38. CPU percentages refer to **one CPU core** and include startup/imports.

| Scenario | Baseline CPU | Final CPU | Change |
| --- | ---: | ---: | ---: |
| Six cards, three working | 2.39% | 1.19% | 50% lower |
| All agents completed | 1.10% | 0.70% | 37% lower |
| Reduced motion | 1.11% | 0.66% | 41% lower |

Final quit latency in these runs was approximately 11–13 ms. These timings are
single observations, not latency guarantees. Terminal output is roughly
unchanged; ncurses already suppresses unchanged cells. Row reuse saves Python
preparation and curses calls, rather than claiming a large bandwidth reduction.
`peak_rss_bytes` uses `RUSAGE_CHILDREN.ru_maxrss`: it is a process-run high-water
mark that can include earlier scenarios in the same benchmark invocation. It
should not be used as a per-scenario memory comparison.

Live Herdr inventory/IPC and providers writing real transcripts may add work.
The PTY scenarios deliberately isolate rendering and input handling.

## Optimization log and stopping point

1. **Incremental aggregates and bounded text/layout caches.** Replace repeated
   sums over every message with running token/cost totals. Streaming replacements
   subtract their previous contribution, preserving deduplication. Cache glyph
   widths and short sanitized strings, with an ASCII fast path.
2. **Snapshot revisions and row painting.** Reuse immutable UI snapshots until
   the worker publishes a new revision. Replace whole-state `repr` signatures.
   Repaint changed rows and coalesce adjacent same-style text. Skip terminal
   reads when exact transcripts supply the tool call.
3. **Reuse arrangement.** Cache selection/filter/layout results for versioned
   snapshots; retain stable working-first order. Mutable, unversioned fixtures
   bypass this cache. Avoid filter-string construction for an empty query.
4. **Rejected candidate: precomputed animation sine terms.** The six-card median
   moved from 0.419 ms to 0.4065 ms, only **3.0%**; median-to-p95 intervals overlapped
   (0.459 vs 0.475 ms), and the wider fixture results showed no consistent gain.
   The candidate was reverted. The final implementation retains the simpler
   animation math.

The stopping rule was no further practical gain once ordinary rendering was
well below the 100 ms animation budget and the next candidate's gain was below
5% or within run-to-run spread. This is a practical stopping point, not proof
that no possible optimization exists. Earlier iterations were exploratory;
only `baseline.json` and `final.json` form the first-pass like-for-like table.
Raw iteration results are retained to make the process inspectable.

## Reproduce

From the project root:

```sh
python3 benchmarks/measure.py --output /tmp/grid-final.json --label final
python3 benchmarks/pty_cpu.py --seconds 12 --output /tmp/grid-final-pty.json

mkdir -p /tmp/grid-baseline
tar -xzf benchmarks/baseline-source.tar.gz -C /tmp/grid-baseline
python3 benchmarks/measure.py --source /tmp/grid-baseline --output /tmp/grid-before.json --label baseline
python3 benchmarks/pty_cpu.py --source /tmp/grid-baseline --seconds 12 --output /tmp/grid-before-pty.json
```

Run versions sequentially on the same machine, with unrelated work quiet.
The preserved baseline already includes working-first ordering, so sorting is
held constant across the comparison. Its old package name is intentionally
retained in the archive; the benchmark detects either package automatically.
Archived demo workspace labels are generic, with their original lengths
preserved. Archive owner metadata is omitted; benchmark logic is unchanged.

`measure.py` warms each operation three times, records 40 frame/snapshot CPU
samples, seven cold parses, 100 unchanged polls and 50 single-message appends.
It reports medians and an approximate p95 order statistic, using
`time.process_time_ns` (index `int(n * .95)` for general samples, index 47
for the 50 appends).
Very small preparation values approach timer resolution and are presented as
`<0.01 ms` rather than inflated speedup ratios. `tracemalloc` runs separately
from timing samples. Appending a fixture record happens outside the timer.

`pty_cpu.py` drains output, measures child CPU against elapsed wall time, then
sends `q` and observes process exit. It checks successful exit and records raw
CPU, output, quit latency and RSS metadata. Unix PTY support is required.

- [Baseline timings](baseline.json) · [Final timings](final.json)
- [Baseline PTY](baseline-pty.json) · [Final PTY](final-pty.json)
- [Iteration 1](iteration-1.json) · [Iteration 2](iteration-2.json) ·
  [Iteration 3](iteration-3.json) · [Rejected iteration 4](iteration-4.json)

The regression suite checks ordering, snapshot isolation, row-diff painting,
Unicode/resize behavior, transcript fallback and shortcut migration. Provider
usage tests protect streamed-message replacement and cumulative token pricing.
