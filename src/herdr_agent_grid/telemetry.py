"""Read only a matched agent session; never attach a nearby unrelated log.

Provider transcripts are optional. Herdr remains the source of lifecycle state.
Initial reads are bounded to a tail plus a small header. Cumulative provider
totals remain exact; Claude totals reconstructed from a truncated tail are
explicitly marked partial. Estimated token costs are separate from reported cost.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from collections import Counter
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import time

from .model import Agent, clean
from .pricing import VERIFIED, estimate as price_estimate

MAX_READ = 2 * 1024 * 1024
SESSION_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def timestamp(value) -> float | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result = float(value)
            result = result / 1000 if result > 100_000_000_000 else result
        elif isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        else:
            return None
        return result if math.isfinite(result) and result > 0 else None
    except (ValueError, OverflowError, OSError):
        return None


def number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return value
    return None


def one_line(value: str, limit: int = 160) -> str:
    return " ".join(clean(value).split())[:limit]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    at: float | None = None
    done: bool = False
    error: bool = False


@dataclass(frozen=True)
class Subagent:
    id: str
    name: str
    model: str = ""
    effort: str = ""
    cost: float | None = None
    estimated_cost: float | None = None
    cost_partial: bool = False
    estimate_partial: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    duration_s: float | None = None
    status: str = "unknown"


@dataclass(frozen=True)
class ChildHint:
    name: str = ""
    model: str = ""
    effort: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    duration_s: float | None = None
    status: str = "unknown"
    event_at: float | None = None
    call_id: str = ""


@dataclass
class Metrics:
    last_call: str = ""
    call_at: float | None = None
    call_done: bool | None = None
    call_detail: str = ""
    call_source: str = ""
    started_at: float | None = None
    seen_at: float = 0.0
    status_since: float = 0.0
    tokens: int | None = None
    tokens_partial: bool = False
    cost: float | None = None
    cost_partial: bool = False
    estimated_cost: float | None = None
    estimate_partial: bool = False
    estimate_note: str = ""
    model: str = ""
    effort: str = ""
    cost_reason: str = ""
    source: str = ""
    issue: str = ""
    phase: str = ""
    trail: tuple[ToolCall, ...] = ()
    last_message: str = ""
    message_at: float | None = None
    subagents: tuple[Subagent, ...] = ()


@dataclass
class Cursor:
    path: Path
    offset: int = 0
    identity: tuple = ()
    boundary: bytes = b""
    metrics: Metrics = field(default_factory=Metrics)
    call_id: str = ""
    usages: dict = field(default_factory=dict)
    cumulative_tokens: bool = False
    truncated: bool = False
    estimates: dict = field(default_factory=dict)
    codex_totals: dict | None = None
    estimate_incomplete: bool = False
    token_sum: float = 0
    estimate_sum: float = 0
    priced_count: int = 0
    estimate_notes: Counter = field(default_factory=Counter)
    subagent: bool = False
    session_id: str = ""
    session_header_checked: bool = False
    finished_at: float | None = None
    lifecycle: str = "unknown"
    lifecycle_at: float | None = None
    finished_message_id: str = ""
    spawn_calls: dict[str, ChildHint] = field(default_factory=dict)
    child_hints: dict[str, ChildHint] = field(default_factory=dict)


def set_estimate(cursor: Cursor, key, value) -> None:
    previous = cursor.estimates.get(key)
    if previous is not None:
        cost, note = previous
        if cost is not None:
            cursor.estimate_sum -= cost
            cursor.priced_count -= 1
        cursor.estimate_notes[note] -= 1
        if not cursor.estimate_notes[note]:
            del cursor.estimate_notes[note]
    cursor.estimates[key] = value
    cost, note = value
    if cost is not None:
        cursor.estimate_sum += cost
        cursor.priced_count += 1
    cursor.estimate_notes[note] += 1
    update_estimate(cursor)


def update_estimate(cursor: Cursor) -> None:
    m = cursor.metrics
    m.estimated_cost = max(0, cursor.estimate_sum) if cursor.priced_count else None
    m.estimate_partial = cursor.truncated or cursor.estimate_incomplete or cursor.priced_count < len(cursor.estimates)
    notes = list(cursor.estimate_notes)
    m.estimate_note = "; ".join(notes[:3]) + f" · rates {VERIFIED}"


def roots(provider: str) -> list[Path]:
    home = Path.home()
    if provider == "claude":
        values = [Path(os.environ.get("CLAUDE_CONFIG_DIR", str(home / ".claude"))),
                  *home.glob(".claude-*")]
        values += [Path(p) for p in os.environ.get("HERDR_AGENT_GRID_CLAUDE_DIRS", os.environ.get("HERDR_GRID_CLAUDE_DIRS", "")).split(os.pathsep) if p]
        return list(dict.fromkeys(p.expanduser().resolve() / "projects" for p in values))
    if provider == "codex":
        base = Path(os.environ.get("CODEX_HOME", str(home / ".codex"))).expanduser().resolve()
        return [base / "sessions", base / "archived_sessions"]
    return []


def tool_detail(value) -> str:
    """A descriptive label or file target, without dumping command arguments."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ""
    if not isinstance(value, dict):
        return ""
    for key in ("description", "file_path", "path"):
        if isinstance(value.get(key), str):
            return one_line(value[key])
    return ""


def consume(cursor: Cursor, record: dict, provider: str) -> None:
    if provider == "claude" and record.get("isSidechain") and not cursor.subagent:
        return
    m = cursor.metrics
    at = timestamp(record.get("timestamp"))
    if at is not None and (m.started_at is None or at < m.started_at):
        m.started_at = at
    payload = record.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    message = record.get("message")
    message = message if isinstance(message, dict) else {}
    kind = record.get("type")

    def call(name, call_id, detail=""):
        if isinstance(name, str) and name:
            cid = str(call_id or record.get("uuid") or f"{name}:{at}")
            previous = next((c for c in m.trail if c.id == cid), None)
            m.last_call = one_line(name, 80)
            m.call_at, m.call_done = (previous.at, previous.done) if previous else (at, False)
            m.call_source, m.call_detail = "transcript", detail
            cursor.call_id = cid
            m.phase = "tool"
            if previous is None:
                m.trail = (*m.trail[-23:], ToolCall(cid, m.last_call, at))

    def returned(call_id, error=False):
        m.trail = tuple(replace(c, done=True, error=bool(error)) if c.id == call_id else c for c in m.trail)
        if cursor.call_id and call_id == cursor.call_id:
            m.call_done = True
        if m.phase == "tool" and not any(not c.done for c in m.trail):
            m.phase = "working"

    def assistant_message(content):
        pieces = [b.get("text") for b in content if isinstance(b, dict) and
                  b.get("type") in ("text", "output_text") and isinstance(b.get("text"), str)]
        text = one_line(" ".join(pieces), 1200)
        if text:
            m.last_message, m.message_at = text, at

    if provider == "codex":
        if kind == "session_meta":
            m.started_at = timestamp(payload.get("timestamp")) or at or m.started_at
            value = payload.get("id") or payload.get("session_id")
            if isinstance(value, str):
                cursor.session_id = value
        elif kind == "turn_context":
            if isinstance(payload.get("model"), str):
                m.model = one_line(payload["model"], 80)
            for key in ("effort", "reasoning_effort", "model_reasoning_effort"):
                if key in payload:
                    m.effort = one_line(payload[key], 24) if isinstance(payload[key], str) else ""
                    break
        elif kind == "response_item":
            if payload.get("type") == "reasoning":
                m.phase = "thinking"
            elif payload.get("type") == "message" and payload.get("role") == "assistant":
                m.phase = "writing"
                if isinstance(payload.get("content"), list):
                    assistant_message(payload["content"])
            elif payload.get("type") in ("function_call", "custom_tool_call"):
                call(payload.get("name"), payload.get("call_id"),
                     tool_detail(payload.get("arguments", payload.get("input"))))
            elif payload.get("type") in ("function_call_output", "custom_tool_call_output"):
                returned(payload.get("call_id"))
        elif kind == "event_msg" and payload.get("type") in ("task_started", "task_complete", "task_aborted"):
            m.phase = "working"
            cursor.finished_at = None if payload["type"] == "task_started" else (
                timestamp(payload.get("completed_at")) or timestamp(payload.get("completed_at_ms")) or at)
            cursor.lifecycle = "working" if payload["type"] == "task_started" else "failed" if payload["type"] == "task_aborted" else "done"
            cursor.lifecycle_at = at
        usage, last_usage = None, None
        if kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info") or {}
            if isinstance(info, dict):
                usage = info.get("total_token_usage")
                last_usage = info.get("last_token_usage")
        elif kind == "token_usage_record":
            usage = payload.get("thread_token_usage")
            last_usage = payload.get("usage")
        if isinstance(usage, dict) and number(usage.get("total_tokens")) is not None:
            m.tokens = int(usage["total_tokens"])
            m.tokens_partial = False
            fields = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")
            totals = {k: number(usage.get(k, 0)) for k in fields}
            complete = all(v is not None for v in totals.values()) and all(k in usage for k in ("input_tokens", "output_tokens"))
            previous = cursor.codex_totals
            if not complete:
                cursor.estimate_incomplete = True
                m.estimate_note = "Input/output token breakdown not reported"
            elif totals != previous:
                if previous is not None and all(totals[k] >= previous[k] for k in fields):
                    delta = {k: totals[k] - previous[k] for k in fields}
                elif cursor.truncated or previous is not None:
                    delta = last_usage if isinstance(last_usage, dict) else {}
                    cursor.estimate_incomplete = True
                else:
                    delta = totals
                context_input = last_usage.get("input_tokens") if isinstance(last_usage, dict) else None
                set_estimate(cursor, ("codex", len(cursor.estimates)), price_estimate("codex", m.model, delta, context_input))
                cursor.codex_totals = totals

    elif provider == "claude":
        # A child end_turn is a provider completion signal. Ordinary assistant
        # text alone is not. New work/resume records reopen that child.
        content = message.get("content")
        texts = [content] if isinstance(content, str) else [b["text"] for b in content
                 if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)] if isinstance(content, list) else []
        if kind == "queue-operation" and record.get("operation") == "enqueue" and isinstance(record.get("content"), str):
            texts.append(record["content"])
        notification = kind in ("user", "queue-operation") and any("<task-notification>" in text for text in texts)
        if cursor.subagent and kind in ("assistant", "user") and not notification:
            rid = message.get("id")
            if (cursor.lifecycle_at is None or at is not None and at >= cursor.lifecycle_at) and (
                    kind == "user" or not rid or rid != cursor.finished_message_id):
                cursor.lifecycle, cursor.lifecycle_at = "working", at
                cursor.finished_at, cursor.finished_message_id = None, ""
        if notification:
            for text in texts:
                for notice in re.findall(r"<task-notification>(.*?)</task-notification>", text, re.S):
                    def tag(name):
                        match = re.search(r"<" + name + r">([^<]*)</" + name + r">", notice)
                        return match[1].strip() if match else ""
                    child_id, status, tid = tag("task-id"), tag("status"), tag("tool-use-id")
                    if child_id and status in ("completed", "failed", "killed", "cancelled", "stopped"):
                        if child_id not in cursor.child_hints and tid not in cursor.spawn_calls:
                            continue  # Background shell tasks are not subagents.
                        hint = cursor.child_hints.get(child_id) or cursor.spawn_calls.get(tid) or ChildHint()
                        if hint.call_id and tid and hint.call_id != tid:
                            continue  # A delayed notice from before a resume.
                        # A delivered notification may repeat an enqueued one;
                        # keep the first completion timestamp for that run.
                        if hint.finished_at is None:
                            cursor.child_hints[child_id] = replace(hint, finished_at=at,
                                status="done" if status == "completed" else "failed", event_at=at)
        if kind == "assistant":
            if isinstance(message.get("model"), str):
                m.model = one_line(message["model"], 80)
            effort = record.get("perTurnEffort") or record.get("effort")
            if isinstance(effort, str):
                m.effort = one_line(effort, 24)
            usage = message.get("usage")
            message_id = message.get("id") or record.get("uuid")
            if isinstance(usage, dict) and message_id and not cursor.cumulative_tokens:
                # Streaming blocks repeat message ids. Replace each message's
                # usage; adding records would multiply its input/cache tokens.
                total = sum(number(usage.get(k)) or 0 for k in (
                    "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                cursor.token_sum += total - cursor.usages.get(message_id, 0)
                cursor.usages[message_id] = total
                m.tokens = int(cursor.token_sum)
                m.tokens_partial = cursor.truncated
            if isinstance(usage, dict) and message_id:
                set_estimate(cursor, message_id, price_estimate("claude", message.get("model", ""), usage))
            content = message.get("content")
            if isinstance(content, list):
                assistant_message(content)
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in ("thinking", "redacted_thinking"):
                    m.phase = "thinking"
                elif block.get("type") == "text":
                    m.phase = "writing"
                elif block.get("type") == "tool_use":
                    call(block.get("name"), block.get("id"), tool_detail(block.get("input")))
                    if block.get("name") in ("Agent", "Task") and isinstance(block.get("input"), dict):
                        args = block["input"]
                        def label(value, limit=80):
                            return one_line(value, limit) if isinstance(value, str) else ""
                        hint = ChildHint(label(args.get("name") or args.get("description") or args.get("subagent_type")),
                                         label(args.get("model")), label(args.get("effort"), 24), at,
                                         status="working", event_at=at, call_id=label(block.get("id"), 128))
                        if isinstance(block.get("id"), str):
                            cursor.spawn_calls[block["id"]] = hint
                        if isinstance(args.get("resume"), str):
                            cursor.child_hints[args["resume"]] = hint
            if cursor.subagent and message.get("stop_reason") == "end_turn":
                if cursor.lifecycle_at is None or at is not None and at >= cursor.lifecycle_at:
                    cursor.lifecycle, cursor.lifecycle_at, cursor.finished_at = "done", at, at
                    cursor.finished_message_id = str(message.get("id") or "")
        elif kind == "user":
            content = message.get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result" and cursor.call_id:
                    returned(block.get("tool_use_id"), block.get("is_error", False))
            result = record.get("toolUseResult")
            if isinstance(result, dict) and isinstance(result.get("agentId"), str):
                tid = next((b.get("tool_use_id") for b in content if isinstance(b, dict) and b.get("type") == "tool_result"), None) if isinstance(content, list) else None
                hint = cursor.spawn_calls.get(tid, ChildHint())
                model = result.get("resolvedModel")
                name = result.get("description") or result.get("agentType")
                done = result.get("status") in ("completed", "failed", "aborted")
                milliseconds = number(result.get("totalDurationMs")) if done else None
                cursor.child_hints[result["agentId"]] = replace(hint,
                    name=hint.name or (one_line(name, 80) if isinstance(name, str) else ""),
                    model=one_line(model, 80) if isinstance(model, str) else hint.model,
                    finished_at=at if done else None,
                    duration_s=milliseconds / 1000 if milliseconds is not None else None,
                    status="failed" if result.get("status") in ("failed", "aborted") else "done" if done else "working",
                    event_at=at)
        elif kind == "cost-state":
            if number(record.get("totalCostUSD")) is not None:
                m.cost = float(record["totalCostUSD"])
                m.cost_partial = bool(record.get("hasUnknownModelCost"))
            start = timestamp(record.get("startTime"))
            if start:
                m.started_at = start
            models = record.get("modelUsage")
            if isinstance(models, dict) and models and all(isinstance(u, dict) for u in models.values()):
                m.tokens = int(sum(number(u.get(k)) or 0 for u in models.values()
                                   for k in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")))
                m.tokens_partial = False
                cursor.cumulative_tokens = True
                cursor.usages.clear()
                cursor.token_sum = 0
        elif kind == "result" and number(record.get("total_cost_usd")) is not None:
            m.cost = float(record["total_cost_usd"])


class Telemetry:
    def __init__(self):
        self.cursors: dict[tuple, Cursor] = {}
        self.misses: dict[tuple, float] = {}
        self.observed: dict[tuple, tuple] = {}
        from .subagents import Subagents
        self.children = Subagents()

    def find(self, agent: Agent) -> Path | None:
        provider = agent.provider or agent.kind
        directories = [p.resolve() for p in roots(provider)]
        if agent.session_kind == "path":
            candidate = Path(agent.session_ref).expanduser().resolve()
            # A server-supplied path may not turn this reader into an arbitrary
            # file reader. Only provider JSONL session stores are eligible.
            if candidate.suffix == ".jsonl" and any(candidate.is_relative_to(r) for r in directories):
                return candidate if candidate.is_file() else None
        elif agent.session_kind == "id" and SESSION_ID.fullmatch(agent.session_ref):
            for root in directories:
                pattern = "*/" + agent.session_ref + ".jsonl" if provider == "claude" else "**/*" + agent.session_ref + ".jsonl"
                for found in root.glob(pattern):
                    resolved = found.resolve()
                    if resolved.is_relative_to(root) and resolved.is_file():
                        return resolved
        return None

    def forget(self, agents: list[Agent]) -> None:
        live = {a.identity for a in agents}
        self.cursors = {k: v for k, v in self.cursors.items() if k in live}
        self.misses = {k: v for k, v in self.misses.items() if k in live}
        self.observed = {k: v for k, v in self.observed.items() if k in live}
        self.children.forget(live)

    def read(self, agent: Agent) -> Metrics:
        key, now = agent.identity, time.time()
        state = self.observed.get(key)
        if state is None:
            state = (agent.status, agent.state_seq, now, now)
        elif (agent.status, agent.state_seq) != state[:2]:
            state = (agent.status, agent.state_seq, state[2], now)
        self.observed[key] = state
        cursor = self.cursors.get(key)
        issue = ""
        try:
            if cursor is None and now >= self.misses.get(key, 0):
                found = self.find(agent)
                if found:
                    cursor = self.cursors[key] = Cursor(found)
                else:
                    self.misses[key] = now + 15
            if cursor:
                self.update(cursor, agent.provider or agent.kind)
        except (OSError, ValueError) as error:
            issue = "Session metrics unavailable: " + one_line(str(error))
        m = replace(cursor.metrics) if cursor else Metrics()
        m.seen_at, m.status_since = state[2], state[3]
        m.source = (agent.provider or agent.kind) + " session log" if cursor else "Herdr status"
        m.issue = issue
        if cursor:
            m.subagents = self.children.read(agent, cursor)
        if m.cost is None and m.estimated_cost is None:
            m.cost_reason = (issue if issue else "Dollar total not reported by session" if cursor else
                             "Session reference not reported by Herdr" if not agent.session_ref else
                             "No matching local session log")
            if cursor and cursor.estimates:
                m.cost_reason = next((note for cost, note in cursor.estimates.values() if cost is None), m.cost_reason)
        return m

    @staticmethod
    def update(cursor: Cursor, provider: str) -> None:
        with cursor.path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            stream.seek(max(0, cursor.offset - 64))
            boundary = stream.read(min(cursor.offset, 64))
            if cursor.identity != identity or stat.st_size < cursor.offset or boundary != cursor.boundary:
                cursor.offset, cursor.identity, cursor.boundary = 0, identity, b""
                cursor.metrics, cursor.usages, cursor.call_id = Metrics(), {}, ""
                cursor.cumulative_tokens, cursor.truncated = False, False
                cursor.estimates, cursor.codex_totals, cursor.estimate_incomplete = {}, None, False
                cursor.token_sum, cursor.estimate_sum, cursor.priced_count = 0, 0, 0
                cursor.estimate_notes.clear()
                cursor.session_id, cursor.finished_at = "", None
                cursor.lifecycle, cursor.lifecycle_at, cursor.finished_message_id = "unknown", None, ""
                cursor.session_header_checked = False
                cursor.spawn_calls.clear()
                cursor.child_hints.clear()
            start = max(cursor.offset, stat.st_size - MAX_READ)
            if cursor.offset == 0 and start:
                stream.seek(0)
                # Read timestamps/model/session metadata from a bounded header.
                for line in stream.read(32768).splitlines()[:32]:
                    try:
                        record = json.loads(line)
                        if isinstance(record, dict):
                            consume(cursor, record, provider)
                    except (ValueError, UnicodeError):
                        continue
                cursor.usages.clear()
                cursor.estimates.clear()
                cursor.token_sum, cursor.estimate_sum, cursor.priced_count = 0, 0, 0
                cursor.estimate_notes.clear()
                cursor.codex_totals = None
                cursor.metrics.estimated_cost = None
                cursor.truncated = True
                if provider == "codex":
                    cursor.metrics.model = ""
            elif start > cursor.offset:
                cursor.truncated = True
                cursor.codex_totals = None
            stream.seek(start)
            data = stream.read(MAX_READ)
            end = data.rfind(b"\n")
            if end < 0:
                return
            lines = data[:end].split(b"\n")
            if start > cursor.offset:
                lines = lines[1:]
            for line in lines:
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        consume(cursor, record, provider)
                except (ValueError, UnicodeError, TypeError):
                    continue
            cursor.offset = start + end + 1
            stream.seek(max(0, cursor.offset - 64))
            cursor.boundary = stream.read(min(cursor.offset, 64))
            if cursor.estimates:
                update_estimate(cursor)


def screen_call(text: str) -> str:
    """Recognize tool-shaped lines only, without labeling arbitrary prose a call."""
    for line in reversed(clean(text).splitlines()):
        match = re.match(r"\s*[⏺●•]\s+([A-Z][\w.-]{1,32})\s*(?:\(|:)", line)
        if match:
            return match[1]
    return ""


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600}h"


def token_label(m: Metrics) -> str:
    if m.tokens is None:
        return "—"
    amount = f"{m.tokens / 1_000_000:.1f}m" if m.tokens >= 1_000_000 else f"{m.tokens / 1000:.1f}k" if m.tokens >= 1000 else str(m.tokens)
    return ("≥" if m.tokens_partial else "") + amount


def cost_label(m: Metrics) -> str:
    cost = cost_value(m)
    if cost is None:
        return "—"
    value = "$0.00" if cost == 0 else "<$0.01" if cost < .005 else f"${cost:.2f}"
    partial = m.cost_partial if m.cost is not None else m.estimate_partial
    return ("≥" if partial else "") + ("~" if m.cost is None else "") + value


def cost_value(m: Metrics) -> float | None:
    return m.cost if m.cost is not None else m.estimated_cost


def cost_detail(m: Metrics) -> str:
    if m.cost is not None:
        return "Reported session total; not an invoice"
    if m.estimated_cost is not None:
        return "Estimated API token cost · " + m.estimate_note
    return m.cost_reason or "Token usage or pricing unavailable"


def subagent_time(child: Subagent, now: float) -> str:
    if child.duration_s is not None:
        return duration(child.duration_s)
    return duration((child.finished_at or now) - child.started_at) if child.started_at else "—"
