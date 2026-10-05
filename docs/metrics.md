# Metrics and sources

| Metric | Source |
| --- | --- |
| Status | Herdr's current agent lifecycle state |
| Active / total | Working parent agents / all parent agents in Herdr's current inventory, across pages and filters |
| Latest call | Matching Claude or Codex session log; recognized terminal tool line as fallback |
| Session age | Session log timestamp or Claude's reported start time |
| Call age | Transcript timestamp; terminal fallbacks are labeled `seen` |
| Tokens | Codex cumulative usage; Claude cumulative model usage or deduplicated message usage |
| API cost | Reported session total when available; otherwise an estimate from observed per-model token usage |
| Latest message | Most recent observed assistant text in the matching session log |
| Subagents | Claude's exact parent session/subagents directory and spawn metadata; Codex's explicit parent thread IDs |

`—` means unavailable. `≥` marks partial token/cost coverage or an observed
lower bound. Expanded details show how long the dashboard has observed the
current status; this clock starts when the dashboard first sees that status,
so it is a lower bound rather than the agent's actual transition time.
Token totals include input, output and cached input, without adding reasoning
tokens a second time. Claude message usage is deduplicated by message id to
avoid counting streaming blocks multiple times.

Reported dollar totals take precedence, including a reported zero. Otherwise,
`EST. COST` and `~$` label an estimated API token cost; `≥~$` marks partial
usage/model coverage. The overview combines reported and estimated costs,
marks an estimated total with `~`, shows coverage, and counts a shared session
only once. These amounts are API-equivalent token costs, not subscription
charges or invoices. Synthetic demo values are never used for real agents.

Rates were verified on **2026-10-04** against
[Anthropic's pricing](https://platform.claude.com/docs/en/about-claude/pricing),
[OpenAI's pricing](https://developers.openai.com/api/docs/pricing), and the
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and
[GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol) model pages.
The bundled table covers current Claude Opus, Sonnet, Haiku and Fable models,
GPT-6 models, GPT-5.6 Sol and GPT-5.3 Codex. Unknown model IDs or absent token
breakdowns display `Unavailable`, with the reason in expanded details.

Claude estimates deduplicate streaming message IDs and price each message's
own model. They distinguish cache reads, 5-minute writes and 1-hour writes,
and use reported Fast mode and US inference modifiers. Missing write duration
uses 5-minute pricing, disclosed in details. Codex estimates price cumulative
usage deltas with the observed model, deduplicate repeated usage records,
discount cached input, and include cache writes and long-context rates.
Reasoning tokens are already included in output and are not charged twice.
Codex uses Standard API rates because its logs generally omit processing tier.
Separate tool fees, taxes and account-specific discounts are excluded.

Each card shows a shortened latest assistant message. `z` reveals more text
and its timestamp, plus the latest tool’s file target or description when
reported. Message wrapping uses terminal cell widths so wide and combining
characters remain readable. Reasoning blocks, user prompts, tool outputs and Claude
sidechain messages are excluded; missing messages display `Message unavailable`.

Use `z` to expand or collapse subagent details. Card clicks open the agent.
The `Hide completed/stale` button (`d`) hides completed and idle parent cards
and child rows, retaining parents with active or unresolved children. Cost totals
include hidden sessions. The filter combines with text search; click
`Show completed/stale` to restore the hidden rows.

Subagents have a simple name, `Model@Effort`, API cost and time row. Tiles fill
their spare rows with children, working first, and distinguish working from total
counts. Compact tiles show an explicit visible range. In `z` details, children
take priority over verbose parent provenance; PgUp/PgDn (or `[`/`]`) scroll the
list so every row remains reachable. Claude
names use reported spawn names/descriptions or child metadata. Codex names use
the child nickname or task path; provider-internal guardian threads are excluded.
The child's own transcript model and effort take precedence over spawn hints.
An unreported effort stays `?`, rather than inheriting the parent's setting.

Child usage is incrementally read and priced independently. **Estimated card
and overview costs include discovered descendants.** Cards show `COMBINED COST`
and an own/subagent/combined breakdown; session tokens still describe the parent's
log. Unknown costs and partial child usage make the combined amount a lower bound,
including when only child costs are available. Reported zero remains valid.

Provider-reported totals do not identify whether children are included. These
retain precedence and display `REPORTED COST`; child costs are shown separately
with an explicit inclusion-unverified note and are not added. The overview marks
these amounts partial when children exist. The same rule applies to a reported
child with known descendants. This prevents counting overlapping charges as if
they were independent, without claiming complete coverage.

The overview deduplicates canonical transcript identities across parent cards,
child rows, ID/path aliases and children that also appear as standalone tiles.
Repeated refreshes do not add prior costs again. Claude descendants use the exact
parent's subagent directory; Codex discovery follows explicit parent thread IDs
recursively, with cycle protection. These are totals of discovered usage, not a
guarantee that every delegated session or historical token record is available.
Child time uses a reported duration or session start through
confirmed completion; it continues while no completion has been reported.
Claude status comes from launch/resume records, explicit assistant `end_turn`
signals and parent task completion notifications, including notifications queued
before delivery. Child transcripts also supply nested child completion results.
Background shell tasks are excluded. Codex status uses explicit task lifecycle
events. Unknown status is labeled rather than inferred from inactivity; logs
that omit a completion signal can leave the last observed working state in place.
Resuming a child clears its completion time; delayed notifications for an earlier
Claude invocation do not complete a newer run. Inaccessible child logs leave
unknown fields unavailable without preventing parent status/metrics from loading.

Claude discovery stays inside the matched parent's subagent directory (including
descendants stored there). Codex reads bounded metadata headers from session and
archived-session stores, links direct children by parent ID, and caches headers
across appends. New Codex children are discovered within about three seconds.
All discovery and child log reads run on the background telemetry worker.

Session logs are matched by Herdr's exact `agent_session` id or path. The
dashboard never guesses by selecting the most recent log in a directory. It
reads local Claude project logs and Codex session/archived-session logs,
respecting `CLAUDE_CONFIG_DIR` and `CODEX_HOME`. Additional Claude config roots
can be supplied through `HERDR_AGENT_GRID_CLAUDE_DIRS` (colon-separated directories);
existing `~/.claude-*` project stores are also checked. Session paths must stay
inside a provider's session store. Missing/inaccessible logs leave metrics
unavailable while Herdr status remains usable.

Inventory refreshes about every 750 ms. Transcript reads are incremental and
bounded; an initial tail read can yield partial Claude token totals when no
cumulative usage record exists. All filesystem and terminal reads run off the
UI thread. At most six terminal reads run concurrently, for the visible cards'
last-call fallback. Each result publishes as soon as it completes; slower panes
do not hold back faster ones. The dashboard never sends prompts or keystrokes to agents
and does not resize or scroll their terminals. Only Enter/click changes focus.
