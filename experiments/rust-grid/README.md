# Rust performance prototype

An experimental Rust implementation of Agent Grid's card renderer, keyboard
navigation and bounded Claude transcript reader. It exists to compare the
current Python app with Rust on **the same data and visible work**.

The installed Herdr plugin continues to use Python. This prototype has no live
Herdr transport, focus operation, session matching, Codex transcript parser or
subagent discovery. Children and metrics for other harnesses come from the
shared synthetic fixture. It is not a production replacement.

## Run

Install stable Rust with [rustup](https://rustup.rs/), then from the repo root:

```sh
. "$HOME/.cargo/env"
cargo build --release --locked --manifest-path experiments/rust-grid/Cargo.toml
python3 benchmarks/compare_rust.py --validate-only
python3 benchmarks/compare_rust.py
```

The harness generates deterministic synthetic fixtures in ignored
`dist/rust-comparison/`. Open the Rust panel with:

```sh
experiments/rust-grid/target/release/herdr-grid-bench \
  --fixture dist/rust-comparison/agents-6.json --mode pty
```

Arrow keys select, Tab wraps, `z` opens details, `/` filters, Page Up/Down navigate
pages or children, and `q` exits. The benchmark panel emits invisible timing
markers after painting; it does not connect to any live agent.

## What is compared

- The existing Python `View.draw`, `app.run`, `app.paint` and `Telemetry.update`.
  The app adds an optional post-paint observer; the normal launcher leaves it off.
- A translation of the same layout, colors, animation, working-first order,
  messages, costs, child rows and details, rendered through Ratatui/Crossterm.
- Identical 6/24/100/1,000-agent inventories and JSONL bytes. The 1,000-agent
  inventory remains paginated; only visible cards are drawn in both versions.
- Fixed displayed ages, identical dimensions and motion settings, identical
  input sequences, matched incremental parser output, and release Rust builds.

See [the comparison methodology and results](../../benchmarks/rust-comparison.md).
The lockfile is committed. CI checks formatting, lint, frame/parser equivalence
and real PTY input behavior on Linux and macOS, without timing thresholds.
