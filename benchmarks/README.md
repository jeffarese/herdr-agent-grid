# Performance

The benchmark harness is Rust and runs the production library and executable.
Every workload uses authored synthetic data. It never focuses or sends input to
real agents.

```sh
cargo build --release --locked --bin herdr-agent-grid
cargo run --release --locked -p grid-tools --bin xtask -- bench
```

Use `--rounds 5`, `--binary PATH`, or `--output PATH` to override the defaults.
The command writes JSON samples and a Markdown report. See [measurements](results.md)
and [raw results](results.json).

## Workloads and interpretation

- Rendering and navigation at 6, 60, and 1,000 agents on a 140×38 terminal.
- Cold reads of 4,000 synthetic transcript records and unchanged-file reads.
- Fresh production processes in a real pseudoterminal, connected to a local
  mock Herdr socket: six Claude sessions with 4,000 records each and 18 children.
- Active, settled, and reduced-motion scenarios. Scripted keys exercise
  navigation, details, filtering, and dismissal, with trace assertions.

Microbenchmarks use wall-clock time per operation after three warm-up calls.
Full-process measurements report first frame, all parent metrics ready,
key-to-paint latency, and terminal output bandwidth. Samples include operating
system scheduling and harness polling overhead. Startup includes process and
PTY creation. The ready marker does not independently certify child completion.
No CPU utilization or peak-memory estimate is inferred from wall-clock timings.

Results record the platform, compiler, executable hash, source hashes, raw
samples, median and p95. Timings describe that machine and workload, and should
be compared only under similar conditions. Debug builds of the harness are
rejected. The application binary should also be an optimized release build.

The regression suite checks correctness separately with `cargo test --workspace --locked`.
