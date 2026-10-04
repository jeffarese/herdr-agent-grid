"""Small, immutable child summaries discovered from explicit provider links.

All disk work runs in the telemetry worker. Child transcripts use the same
bounded, incremental reader and cost accounting as their parent. The renderer
never scans session stores, and child costs are not added to parent totals.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import time

from .model import Agent
from .telemetry import ChildHint, Cursor, MAX_READ, Subagent, Telemetry, one_line, roots


def label(value, limit=80) -> str:
    return one_line(value, limit) if isinstance(value, str) else ""


def read_object(path: Path, limit: int, first_line=False) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.readline(limit + 1) if first_line else stream.read(limit + 1)
        if len(raw) > limit:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


@dataclass(frozen=True)
class Link:
    id: str
    parent: str
    name: str
    path: Path


class Subagents:
    def __init__(self):
        self.cursors: dict[tuple, dict[str, Cursor]] = {}
        self.metadata: dict[Path, tuple[tuple, dict]] = {}
        self.headers: dict[Path, tuple[tuple, Link | None]] = {}
        self.links: dict[str, tuple[Link, ...]] = {}
        self.scan_after = 0.0
        self.stores: tuple[Path, ...] = ()

    def forget(self, live: set[tuple]) -> None:
        self.cursors = {key: value for key, value in self.cursors.items() if key in live}
        used = {c.path.with_suffix(".meta.json") for group in self.cursors.values() for c in group.values()}
        self.metadata = {path: value for path, value in self.metadata.items() if path in used}

    def read(self, agent: Agent, parent: Cursor) -> tuple[Subagent, ...]:
        provider = agent.provider or agent.kind
        if provider not in ("claude", "codex"):
            return ()
        group = self.cursors.setdefault(agent.identity, {})
        try:
            if provider == "claude":
                children = self.claude(parent)
            else:
                if not parent.session_id:
                    if agent.session_kind == "id":
                        parent.session_id = agent.session_ref
                    elif not parent.session_header_checked:
                        # The main reader's small header may cut a long metadata
                        # line when opening a large session by its exact path.
                        record = read_object(parent.path, MAX_READ, first_line=True)
                        payload = record.get("payload")
                        if record.get("type") == "session_meta" and isinstance(payload, dict):
                            parent.session_id = label(payload.get("id") or payload.get("session_id"), 128)
                        parent.session_header_checked = True
                self.index_codex()
                children = [(link.id, link.name, link.path, ChildHint())
                            for link in self.links.get(parent.session_id, ())]
        except OSError:
            return ()
        # Read all children before resolving state: nested Claude children may
        # have their completion result in another child's transcript.
        for child_id, name, path, hint in children:
            cursor = group.get(child_id)
            if path is not None:
                if cursor is None or cursor.path != path:
                    cursor = group[child_id] = Cursor(path, subagent=True)
                try:
                    Telemetry.update(cursor, provider)
                except (OSError, ValueError):
                    # A child file can disappear during a refresh. Retain known
                    # measurements instead of taking down the parent's panel.
                    pass
        hints = dict(parent.child_hints)
        if provider == "claude":
            for cursor in group.values():
                for child_id, hint in cursor.child_hints.items():
                    previous = hints.get(child_id)
                    if previous is None or (hint.event_at or 0) > (previous.event_at or 0):
                        hints[child_id] = hint
        result = []
        for child_id, name, path, hint in children:
            cursor = group.get(child_id)
            observed = hints.get(child_id)
            if observed and (observed.event_at or 0) >= (hint.event_at or 0):
                hint = replace(observed, name=observed.name or hint.name,
                               model=observed.model or hint.model, effort=observed.effort or hint.effort,
                               started_at=observed.started_at or hint.started_at)
            m = cursor.metrics if cursor else None
            status = cursor.lifecycle if cursor else "unknown"
            finished = cursor.finished_at if cursor else None
            if provider == "claude" and hint.status != "unknown" and (hint.event_at or 0) >= ((cursor.lifecycle_at or 0) if cursor else 0):
                status, finished = hint.status, hint.finished_at
            if hint.duration_s is not None and status == "unknown":
                status = "done"
            result.append(Subagent(
                id=child_id, name=hint.name or name or child_id,
                model=(m.model if m else "") or hint.model,
                effort=(m.effort if m else "") or hint.effort,
                cost=m.cost if m else None,
                estimated_cost=m.estimated_cost if m else None,
                cost_partial=m.cost_partial if m else False,
                estimate_partial=m.estimate_partial if m else False,
                started_at=hint.started_at or (m.started_at if m else None),
                finished_at=finished,
                duration_s=hint.duration_s if status in ("done", "failed") else None,
                status=status,
            ))
        live = {child.id for child in result}
        self.cursors[agent.identity] = {key: value for key, value in group.items() if key in live}
        # Working children are always visible first. Unknown state is explicit;
        # older finished work stays reachable after live/recent work.
        ranks = {"working": 0, "unknown": 1, "failed": 2, "done": 3}
        return tuple(sorted(result, key=lambda child: (ranks.get(child.status, 1),
                           -(child.finished_at or child.started_at or 0), child.name.casefold(), child.id)))

    def claude(self, parent: Cursor) -> list[tuple]:
        folder = parent.path.resolve().with_suffix("") / "subagents"
        # A symlinked session directory must not attach another parent's files.
        if folder.resolve() != folder.absolute():
            return []
        files = {}
        for path in sorted(folder.glob("agent-*.jsonl")):
            if not path.resolve().is_relative_to(folder) or not path.is_file():
                continue
            child_id = path.stem[len("agent-"):]
            files[child_id] = path
        children = []
        for child_id in dict.fromkeys((*files, *parent.child_hints)):
            path = files.get(child_id)
            meta = self.meta(path.with_suffix(".meta.json"), folder) if path else {}
            hint = parent.child_hints.get(child_id) or parent.spawn_calls.get(meta.get("toolUseId")) or ChildHint()
            name = label(meta.get("name")) or hint.name or label(meta.get("description")) or label(meta.get("agentType"))
            hint = replace(hint, name=name, model=hint.model or label(meta.get("model")),
                           effort=hint.effort or label(meta.get("effort"), 24))
            children.append((child_id, name, path, hint))
        return children

    def meta(self, path: Path, folder: Path) -> dict:
        try:
            if not path.resolve().is_relative_to(folder):
                return {}
            stat = path.stat()
            identity = stat.st_ino, stat.st_mtime_ns, stat.st_size
            old = self.metadata.get(path)
            if old and old[0] == identity:
                return old[1]
            value = read_object(path, 32768)
            # Retain display metadata only; never prompts or transcript content.
            value = {key: value[key] for key in ("name", "description", "agentType", "toolUseId", "model", "effort")
                     if isinstance(value.get(key), str)}
            self.metadata[path] = identity, value
            return value
        except OSError:
            return {}

    def index_codex(self) -> None:
        now = time.monotonic()
        stores = tuple(path.resolve() for path in roots("codex"))
        if stores == self.stores and now < self.scan_after:
            return
        if stores != self.stores:
            self.headers.clear()
        self.stores, self.scan_after = stores, now + 3
        found, links = {}, {}
        for root in stores:
            for path in root.glob("**/*.jsonl"):
                try:
                    if not path.resolve().is_relative_to(root):
                        continue
                    stat = path.stat()
                    old = self.headers.get(path)
                    # Appended bodies cannot change immutable session metadata.
                    if old and old[0][:2] == (stat.st_dev, stat.st_ino) and stat.st_size >= old[0][2]:
                        link = old[1]
                        cacheable = True
                    else:
                        record = read_object(path, MAX_READ, first_line=True)
                        payload = record.get("payload")
                        cacheable = record.get("type") == "session_meta" and isinstance(payload, dict)
                        link = self.codex_link(path, payload) if cacheable else None
                    # Retry incomplete headers after a writer finishes its line.
                    if cacheable:
                        found[path] = ((stat.st_dev, stat.st_ino, stat.st_size), link)
                    if link:
                        links.setdefault(link.parent, {})[link.id] = link
                except OSError:
                    continue
        self.headers = found
        self.links = {parent: tuple(sorted(group.values(), key=lambda link: (link.name.casefold(), link.id)))
                      for parent, group in links.items()}

    @staticmethod
    def codex_link(path: Path, payload: dict) -> Link | None:
        source = payload.get("source")
        sub = source.get("subagent") if isinstance(source, dict) else None
        if isinstance(sub, dict) and "other" in sub:
            return None  # Provider-internal guardians are not delegated tasks.
        spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
        spawn = spawn if isinstance(spawn, dict) else {}
        parent = payload.get("parent_thread_id") or spawn.get("parent_thread_id")
        child_id = payload.get("id") or payload.get("session_id")
        if not isinstance(parent, str) or not isinstance(child_id, str) or not parent or not child_id or parent == child_id:
            return None
        name = label(payload.get("agent_nickname")) or label(spawn.get("agent_nickname"))
        agent_path = spawn.get("agent_path")
        if not name and isinstance(agent_path, str):
            name = label(agent_path.rsplit("/", 1)[-1])
        return Link(child_id, parent, name or child_id[:12], path)
