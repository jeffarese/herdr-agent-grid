# Performance measurements

Synthetic workloads on macos-aarch64 with rustc 1.99.0 (b940084d7 2026-09-28). 5 rounds; optimized builds.

| Scenario | First frame median | Metrics ready median | Key-to-paint p95 |
| --- | ---: | ---: | ---: |
| active | 7.51 ms | 87.06 ms | 1.19 ms |
| reduced_motion | 7.73 ms | 91.80 ms | 1.61 ms |
| settled | 7.41 ms | 79.56 ms | 1.71 ms |

Wall-clock microbenchmarks and fresh-process PTY timings. Includes polling and scheduling overhead. Six synthetic Claude sessions, 4000 records each, 18 children. No live account data. Results are machine-specific; no cross-language comparison or CPU/RSS claims.

[Raw samples](results.json) · [Methodology](README.md)
