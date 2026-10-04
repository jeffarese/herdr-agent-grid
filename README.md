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
  <img alt="Python 3.9+" src="https://img.shields.io/badge/Python-3.9%2B-93c5fd?style=flat-square">
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

Requires Herdr 0.9+ and Python 3.9+ on macOS or Linux. There are **no runtime
packages** to install. The shortcut installer requires **Python 3.11+**.

From a Herdr terminal:

```sh
git clone https://github.com/jeffarese/herdr-agent-grid.git
cd herdr-agent-grid
python3 install.py --open
```

The installer links the plugin, backs up your configuration, adds **Cmd+G**,
**prefix then A**, and **Ctrl+Alt+G**, and reloads Herdr. Re-running it is
idempotent. Existing `herdr-grid.open` bindings migrate to
`herdr-agent-grid.open`, preserving your custom keys and comments.

Use `--config /path/to/config.toml` for a custom configuration.

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
python3 run.py --demo                     # Try the panel without Herdr
python3 run.py --demo --icons unicode     # Portable harness marks
python3 run.py --demo --icons ascii --no-motion
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

An alternating paired comparison against version 1.0.0 measures **1.22× faster
moving six-card frames** and **9.65× faster later-page frames** for a 1,000-agent
inventory. The second pass removes repeated inventory scans during navigation. A cold 4,000-message transcript
still takes roughly **50–70 ms** on the measured macOS ARM64 machine.
These are synthetic measurements, not guarantees for every machine or
live Herdr workload. [Raw results, before/after comparisons and methodology](benchmarks/README.md)
are included.

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
python3 -m unittest discover -s tests -v
python3 run.py --demo --render --width 140 --height 38
python3 run.py --doctor
python3 run.py --list
```

Diagnostics require a Herdr-managed environment. Tests exercise real curses
processes through a PTY, including macOS system Python, keyboard/mouse focus,
clean exit and persistent startup errors. Performance regression tests cover
working-first ordering, snapshot isolation, row repainting, Unicode wrapping,
independent pane refreshes and rename migration.

Media export requires Pillow and ffmpeg only during development:

```sh
python3 scripts/render_demo.py
```

See [release instructions](docs/release.md) and [launch copy](docs/launch.md).

[MIT licensed](LICENSE) · Built for [Herdr](https://herdr.dev)
