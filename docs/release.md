# Release process

Production version: **2.0.2**, in `Cargo.toml` and `herdr-plugin.toml`.

1. Run the complete Rust checks:
   ```sh
   cargo fmt --all --check
   cargo clippy --workspace --locked --all-targets -- -D warnings
   cargo test --workspace --locked
   cargo build --release --locked --workspace
   ```
2. Run `cargo run --release --locked -p grid-tools --bin xtask -- bench`.
   Review the raw samples, source hashes, workload and limitations. Regenerate
   demonstration assets with `cargo xtask media` (system fonts and ffmpeg).
3. Verify installation in Herdr: linking, config preservation/reload, shortcuts,
   popup opening, status/child rendering and closing. Keep live screenshots and
   session data outside the repository.
4. Push the reviewed commit. All six platform jobs (macOS, Linux and Windows,
   each on ARM64 and x86_64) and the Rust 1.88 job must pass. CI packages and
   smoke-tests each native archive before uploading it; Windows jobs also test
   `install.ps1` against a local release server and load the plugin in Herdr.
5. Download the `release-*` artifacts. Create the source archive from the tested,
   committed checkout with `cargo xtask package`. Combine the `.sha256` files
   into `SHA256SUMS`, then independently verify archive hashes and contents.
6. Tag the tested commit and attach the six native archives, source archive,
   and `SHA256SUMS` to the release. Include installation steps, platform
   requirements, validated measurements and limitations in the release notes.
7. Download a published archive and verify its checksum, `--version`,
   `--demo --render`, and installation layout.

## Package commands

```sh
cargo xtask package --target aarch64-apple-darwin
cargo xtask package --target x86_64-unknown-linux-gnu --binary target/release/herdr-agent-grid
cargo xtask package --target x86_64-pc-windows-msvc
cargo xtask package
```

Native archives contain one executable, manifest, platform installer
(`install.sh`, or `install.ps1` on Windows),
README, changelog, project license and notices for runtime dependencies. They
exclude development tools, provider logs, user configuration, caches and test
fixtures. Archives use stable file order, timestamps and permissions, with a
SHA-256 sidecar. Source archives include only Git-tracked regular files.

Media export renders production draw commands in Rust. FFmpeg encodes the
animated GIF and H.264 film; `cargo xtask media --stills-only` needs no encoder.
All demonstration and benchmark data is synthetic.

Binaries are unsigned CLI programs. Linux targets glibc 2.35+; macOS targets 11+;
Windows targets 10 (1803+, for the inbox `tar.exe`) and 11.
