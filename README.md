<p align="center">
  <img src="docs/media/hero.png" alt="herdr-agent-grid — Your agents. One command center." width="900">
</p>

<p align="center">
  <strong>A full-panel command center for the agents running in Herdr.</strong><br>
  Working agents first. Harness icons. Subagents. Latest messages. Clear API cost.
</p>

<p align="center">
  <a href="#get-started">Get started</a> ·
  <a href="docs/media/demo.mp4">Watch the 30-second demo</a> ·
  <a href="docs/metrics.md">Metric sources</a> ·
  <a href="benchmarks/README.md">Performance</a>
</p>

<p align="center">
  <img alt="Native Rust" src="https://img.shields.io/badge/native-Rust-fb923c?style=flat-square">
  <img alt="Herdr 0.9+" src="https://img.shields.io/badge/Herdr-0.9%2B-b39dff?style=flat-square">
  <img alt="macOS and Linux" src="https://img.shields.io/badge/macOS%20%7C%20Linux-supported-34d399?style=flat-square">
  <img alt="MIT License" src="https://img.shields.io/badge/license-MIT-9da5ba?style=flat-square">
</p>

![Agent Grid with synthetic agents, orange working cards and green completed cards](docs/media/preview.gif)

*All demo agents, messages and costs are synthetic. The video renders the same
TUI draw commands as the plugin, with scripted updates and navigation.*

## See the whole team

Trigger one panel to see agents across every workspace in your current Herdr
session. Each card tells you what the agent is doing, what it said most
recently, how long it has been running, and its token usage and API cost.
Select a card and press Enter to jump straight to its terminal.

| At a glance | In the panel |
| --- | --- |
| **Active work first** | A prominent `ACTIVE / TOTAL` count shows working agents across the inventory. Stable ordering puts working agents first. |
| **A readable visual language** | Orange for working, green for done, explicit text labels for every state. Small violet, cyan and amber cores indicate thinking, writing and tool activity. |
| **Harness + model** | A harness icon followed by `Model@Effort`, with model versions preserved. |
| **Delegated work** | Parent cards fill their spare rows with children, working first, with separate working/total counts. Child rows show name, `Model@Effort`, API cost and time. Press `z`, then PgUp/PgDn, to reach every child. |
| **The latest update** | Recent tool call, call age, assistant message and a compact tool trail. Expand with `z` for message context and the tool’s file target or description. |
| **Honest costs** | Reported API totals take precedence. Estimates show `~`; partial coverage shows `≥`; missing data stays unavailable. |
| **Keyboard flow** | Arrows, Vim keys, Tab, filtering, pagination and mouse selection. Responsive cards with a persistent key legend. |
| **Clear selection** | A double border and filled `SELECTED` title bar make arrow navigation easy to follow, while preserving status colors. |

The visual direction builds on [agent-swarm](https://github.com/jeffarese/agent-swarm).
Phase animation indicates observed activity; it is not a throughput chart.

## Get started

Version **2.0 runs entirely in Rust**. Requires Herdr 0.9+ on macOS 11+ or
Linux with glibc 2.35+. Native releases support Apple Silicon, Intel Macs, and
Linux x86_64/ARM64. **No Python or Rust toolchain is needed for release bundles.**

Download the archive for your platform from [Releases](https://github.com/jeffarese/herdr-agent-grid/releases/latest),
extract it somewhere permanent, and run `./install.sh --open` inside it.
Checksums are published in `SHA256SUMS`.

Herdr's [GitHub plugin installer](https://herdr.dev/docs/cli-reference/#plugins)
also runs the manifest build hook to prepare the native executable:

```sh
herdr plugin install jeffarese/herdr-agent-grid --ref v2.0.0
herdr plugin action invoke herdr-agent-grid.open
```

Use the release/source installer above to add keyboard shortcuts automatically.
Herdr refuses to replace a locally linked plugin through GitHub install; update
that checkout with `git pull --ff-only && ./install.sh` instead.

Or build from source in a Herdr terminal (Rust 1.88+):

```sh
git clone https://github.com/jeffarese/herdr-agent-grid.git
cd herdr-agent-grid
./install.sh --open
```

The installer links the plugin, backs up your configuration, adds **Cmd+G**,
**prefix then A**, and **Ctrl+Alt+G**, and reloads Herdr. Re-running it is
idempotent. Existing `herdr-grid.open` bindings migrate to
`herdr-agent-grid.open`, preserving your custom keys and comments.

Use `--config /path/to/config.toml` for a custom configuration. For an existing
source installation, run `git pull --ff-only && ./install.sh`. The plugin ID
and shortcuts stay the same. Without a Rust toolchain, the source installer
downloads the matching version’s native binary and verifies its checksum.

<details>
<summary>Manual installation and upgrading from herdr-grid</summary>

```sh
herdr plugin link "$PWD" --enabled
herdr plugin action invoke herdr-agent-grid.open
```

For a manual upgrade, change any old shortcut command from `herdr-grid.open`
to `herdr-agent-grid.open`, then run `herdr server reload-config`.

To remove the plugin, run `herdr plugin unlink herdr-agent-grid`, remove its
`[[keys.command]]` shortcut block, and reload the configuration.

</details>

## Make it yours

Harness logos use an existing **Herdr Agent Icons Max** font when available.
Unicode and ASCII fallbacks work without it. The plugin does not install
fonts or change your terminal settings.

```sh
./run.sh --demo                     # Try the panel without Herdr
./run.sh --demo --icons unicode     # Portable harness marks
./run.sh --demo --icons ascii --no-motion
```

| Setting | Values |
| --- | --- |
| `HERDR_AGENT_GRID_ICONS` | `auto` (default), `font`, `unicode`, `ascii` |
| `HERDR_AGENT_GRID_MOTION` | `on` (default), `off` |
| `HERDR_AGENT_GRID_THEME` | `light` for darker accents on a light terminal |
| `HERDR_AGENT_GRID_CLAUDE_DIRS` | Additional Claude config roots, separated by `:` |

Legacy `HERDR_GRID_*` settings remain supported. `font` mode is useful when
viewing through a terminal with the icon font while the plugin runs remotely.
Colors inherit your terminal background; textual status labels remain visible
on terminals without color support.

## Controls

| Input | Action |
| --- | --- |
| Arrows or `h/j/k/l` | Select a card |
| Tab / Shift+Tab | Next / previous agent |
| Enter or click | Focus the agent and close the panel |
| `z` | Expand or collapse details |
| `/` | Filter by name, task, workspace, status or tool |
| PgUp / PgDn or `[` / `]` | Change page; scroll subagents in expanded details |
| `r` | Refresh now |
| Escape / `q` | Exit details, clear a filter, or close |
| Ctrl+C | Close immediately |

In demo mode, Enter/click opens details. Cards paginate when the terminal
cannot fit every agent legibly. In expanded details with subagents,
PgUp/PgDn and `[`/`]` scroll the child list; left/right select another parent.

## Fast enough to stay out of your way

Inventory refreshes about every 750 ms on a background thread. Transcript
reads are incremental; terminal reads are skipped when a matched transcript
already supplies the tool call. Animation runs at most 10 fps and stops for
settled agents and compact cards. Unchanged snapshots, fleet totals, filtered
inventories, layouts and terminal rows are reused. Faster panes publish tool
updates immediately even while another pane is still being read.

The complete Rust application is benchmarked against the Python 1.1.1 reference
using **the same synthetic Herdr socket responses, Claude/Codex transcripts,
18 child logs, terminal dimensions and keyboard events**. Release builds run
in alternating pairs; tests check matching navigation before comparing speed.

[Native release measurements and methodology](benchmarks/native-release.md) ·
[Earlier renderer/parser comparison](benchmarks/rust-comparison.md)

On the measured Mac, the full six-agent workload improves:

| Measurement | Python 1.1.1 | Rust 2.0.0 |
| --- | ---: | ---: |
| Key-to-paint p95 | 3.02 ms | **1.25 ms** |
| All session metrics ready | 373 ms | **86 ms** |
| Whole-run CPU, one core | 12.6% | **4.3%** |

Five alternating pairs use identical data. CPU includes cold startup and scripted
interaction; it is not idle CPU. Memory drops modestly (35.4 to 32.7 MiB) because
both full applications retain live session indexes. All measurements are
synthetic and machine-specific, with raw samples and limitations in the report.

## Where the numbers come from

Herdr supplies agent lifecycle status. Claude and Codex metrics come from
local logs matched by the agent's **exact session ID or path**. Other harnesses
still get status cards; transcript-specific fields may be unavailable.

Estimated costs use bundled model rates and observed token usage. They are
API-equivalent token costs, not subscription charges or invoices. Reported
zero is a valid value. Unknown models and missing usage display `Unavailable`.

The overview deduplicates shared sessions and shows coverage. Expanded details
explain the source and partial coverage. Latest-message previews exclude
reasoning, user prompts, tool output and Claude sidechains.

Claude subagents come from the matched parent's subagent logs; Codex children
use explicit parent thread IDs. Child costs use the same `~`, `≥` and `—`
labels, and stay separate from overview totals because parent reports may
already include them. Missing child effort shows `?`. Completed child timers
stop when completion is reported. Tiles use their available space for multiple
children; compact tiles show the visible range. Expanded details put children
first and scroll when needed, with a persistent row range and key hint.
Working children are orange and completed children green; unreported status
stays unknown. [See a nine-child synthetic example](docs/media/subagents.png).

[Read the full metrics and pricing notes](docs/metrics.md).

## Develop and verify

```sh
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo build --release --locked --examples --bin herdr-agent-grid
cargo build --release --locked --manifest-path experiments/rust-grid/Cargo.toml
python3 -m unittest discover -s tests -v
./run.sh --demo --render --width 140 --height 38
./run.sh --doctor
./run.sh --list
```

Production code lives in `native/`. The Python 1.1.1 implementation is retained
in `src/herdr_agent_grid` only as a differential-test and benchmark reference;
it is excluded from binary release bundles. Python 3.11+ is needed for the
development suite. The `run.py` and `install.py` entrypoints remain compatibility
wrappers for existing scripts.

CI builds and tests natively on macOS and Linux, each on ARM64 and x86_64.
The checks cover frame equivalence, provider accounting and lifecycle events,
exact session discovery, file rotation, partial reads, keyboard/mouse focus,
child scrolling, error persistence and safe configuration upgrades.

Media export uses synthetic fixtures and requires Pillow/ffmpeg during development.
See [release instructions](docs/release.md) and [launch copy](docs/launch.md).

[MIT licensed](LICENSE) · Built for [Herdr](https://herdr.dev)
