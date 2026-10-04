# Performance comparison adapter

This directory started as the Rust prototype. Since 2.0.0 its library re-exports
the production crate at the repository root. The executable supplies controlled
fixtures, fixed clocks, timing markers and replay workloads for fair comparison
with the frozen Python 1.1.1 reference.

```sh
cargo build --release --locked --manifest-path experiments/rust-grid/Cargo.toml
python3 benchmarks/compare_rust.py --validate-only
python3 benchmarks/compare_rust.py --output dist/current-comparison.json
python3 benchmarks/compare_native.py
```

`compare_native.py` exercises the complete production executable and a synthetic
Herdr Unix socket, including both provider stores and child discovery. Historical
measurements remain unchanged in `benchmarks/rust-comparison.*`.
