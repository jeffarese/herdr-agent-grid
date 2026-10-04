# Release process

Production version: **2.0.0**, in `Cargo.toml` and `herdr-plugin.toml`.
The Python reference intentionally retains its historical version.

1. Run `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`,
   `cargo test --locked`, and release builds of the application, parity example
   and comparison adapter. Run `python3 -m unittest discover -s tests -v`.
2. Benchmark identical synthetic inputs with `benchmarks/compare_native.py`.
   Keep raw samples, fixture/source hashes, scope and limitations in the report.
   Preserve the historical prototype report instead of overwriting it.
3. Run `./install.sh` from an existing Herdr environment. Verify plugin linking,
   config preservation/reload, popup opening, status/child rendering and closing.
   Keep any live screenshots and session data outside the repository.
4. Push the reviewed commit. All four native platform jobs and the Rust 1.88 /
   Python 3.11 compatibility job must pass. Native jobs package and smoke-test
   archives before uploading artifacts; never publish a failed build.
5. Download the `release-*` CI artifacts. Build the source archive with
   `python3 scripts/package_release.py`. Combine the individual `.sha256` files
   into `SHA256SUMS`, then independently verify all archive hashes and contents.
6. Tag the tested commit, create the GitHub release and attach the four native
   archives, source archive and `SHA256SUMS`. Include measured gains, upgrade
   instructions, platform requirements and any limitations in the notes.
7. Download a published native archive and verify its checksum, `--version`,
   `--demo --render`, and install layout. Check the release URL and asset list.

Native archives contain one executable, the manifest, shell launcher/installer,
README, changelog, project license and dependency notices. They include no
provider logs, user configuration, Python runtime, build cache or test fixtures.
Source archives include only Git-tracked inputs. All demo/benchmark data is
synthetic. Binary assets are ordinary unsigned CLI programs; no signing or
notarization claim is made. Linux binaries target glibc 2.35+; macOS targets 11+.

The benchmark adapter in `experiments/rust-grid` imports the production library.
The retained Python reference and historical source archives are used only for
regression tests and reproducibility, never by the installed plugin.
