The production application is entirely Rust. `main.rs` and `cli.rs` launch it;
`app.rs` owns terminal input and rendering. `refresh.rs` owns background Herdr
I/O, `sessions.rs` discovers exact provider and child links, and `telemetry.rs`
reads bounded incremental transcripts. Assets are embedded in the binary.

The Python code in `src/herdr_agent_grid` is a frozen compatibility reference
for differential tests, historical benchmarks and media export. Release bundles
contain only the native binary, shell launch/install scripts, manifest and docs.
