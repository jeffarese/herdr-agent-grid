# Performance measurements

Synthetic workloads on macos-aarch64 with rustc 1.99.0 (b940084d7 2026-09-28). 5 rounds; optimized builds.

| Scenario | First frame median | Metrics ready median | Key-to-paint p95 |
| --- | ---: | ---: | ---: |
| active | 12.89 ms | 69.66 ms | 0.58 ms |
| reduced_motion | 12.52 ms | 83.50 ms | 0.60 ms |
| settled | 15.03 ms | 79.28 ms | 0.49 ms |

Wall-clock microbenchmarks and fresh-process PTY timings. Includes polling and scheduling overhead. Six synthetic Claude sessions, 4000 records each, 18 children. No live account data. Results are machine-specific; no cross-language comparison or CPU/RSS claims.

[Raw samples](results.json) · [Methodology](README.md)
