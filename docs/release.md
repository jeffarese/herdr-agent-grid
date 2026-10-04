# Releasing herdr-agent-grid

The repository is [jeffarese/herdr-agent-grid](https://github.com/jeffarese/herdr-agent-grid).
The current version is **1.1.1**.

## Verify locally

```sh
python3 -m unittest discover -s tests -v
python3 run.py --version
python3 run.py --demo
python3 benchmarks/measure.py --output /tmp/agent-grid-bench.json
python3 benchmarks/pty_cpu.py --seconds 12 --output /tmp/agent-grid-pty.json
```

From a real Herdr terminal, run `python3 install.py --open` and verify Cmd+G,
Enter-to-focus, filtering, subagent details and a completed agent’s green status.
The installer migrates old `herdr-grid.open` shortcuts.

## Tag a release

Keep the version in `herdr-plugin.toml` and `src/herdr_agent_grid/__init__.py`
in sync, and describe changes in `CHANGELOG.md`. After verification, commit
the release and tag it:

```sh
git add .
git commit -m "Release herdr-agent-grid 1.1.1"
git tag -a v1.1.1 -m "herdr-agent-grid 1.1.1"
git push origin main
git push origin v1.1.1
```

Use the description and topics in [the launch kit](launch.md).
Upload `media/demo.mp4` in the release or announcement so viewers can play it
inline; a README link to the checked-in MP4 may show GitHub’s download view.

Verify a live Herdr upgrade before each release. CI runs the test suite on
macOS and Linux; local development verification has covered macOS.

## Packaged source

```sh
python3 scripts/package_release.py
```

This produces a versioned source archive and SHA-256 checksum under `dist/`.
It includes the implementation, documentation, demo assets, tests and raw
benchmark results; it excludes local caches, environments and Git metadata.
No session logs, credentials or real agent messages are included.
