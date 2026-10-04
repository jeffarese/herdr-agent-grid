# Changelog

## 1.1.1 — 2026-10-04

- Fill each tile's available space with child rows instead of a fixed first-child
  preview; show explicit ranges/overflow on compact tiles.
- Put working children first, distinguish working/total counts, and color
  completed children green. Read Claude end-turn signals and queued/delivered
  async completion notices, including completions from nested child transcripts.
- Freeze completed timers; reopen resumed children and ignore delayed notices
  belonging to an earlier invocation of the same child.
- Prioritize the child list in expanded details and add PgUp/PgDn scrolling,
  making every child reachable even on short terminals.
- Exclude background shell task notifications from child discovery.
- Cover nine-child tiles, twenty-child terminal scrolling and completion/resume
  handling with regression tests; update synthetic media and source notes.

## 1.1.0 — 2026-10-04

- Put the working-agent count and total inventory in a prominent header badge.
  Mark selection with a double border, inverted title bar and `SELECTED` label,
  retaining orange working and green completed colors.
- Show subagent counts and a compact child preview on parent cards. Expanded
  details list child name, `Model@Effort`, API cost and elapsed time.
- Match Claude children through the exact parent's subagent directory and
  spawn metadata; match Codex children by explicit parent thread IDs.
- Read child usage incrementally with the same reported/estimated/partial cost
  labels. Completed child timers stop; resumed Codex timers continue.
- Keep child costs separate from parent/overview totals to avoid double counting.
- Cache Codex metadata headers and refresh child discovery every three seconds;
  retain immutable child summaries in published snapshots.
- Update the synthetic demo and metric-source documentation.

## 1.0.1 — 2026-10-04

- Cache fleet totals per published snapshot and retain filtered inventories
  while navigating; index selection and card numbering by pane ID.
- Batch animation glyphs into same-color runs, preserving every cell and color.
- Avoid animation ticks for compact cards and reduce settled/reduced-motion
  polling while keeping keyboard input immediate.
- Publish terminal fallback results as they complete, so a slow pane does not
  hold back faster panes.
- Show the latest tool target in expanded details and wrap assistant messages
  by terminal cell width, retaining wide and combining characters.
- Add moving-frame, later-page, navigation and alternating paired benchmarks.

## 1.0.0 — 2026-10-04

- Rename the plugin and Python package to `herdr-agent-grid`.
- Show working agents first while retaining selection across status changes.
- Full-panel tiled agent cards with harness icons and `Model@Effort` headers.
- Orange working states, green completed states, phase cores and tool trails.
- Latest assistant messages with expanded context and message age.
- Reported API totals and labeled per-model estimates with coverage details.
- Incremental token/cost aggregation, snapshot/layout reuse, safe bounded text
  caches and changed-row terminal painting.
- Migrate legacy shortcut commands while preserving custom bindings.
- Add repeatable synthetic benchmarks, a 1080p demo film and launch materials.
