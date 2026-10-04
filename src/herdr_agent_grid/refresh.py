"""One background refresh at a time; UI input never waits on Herdr."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import threading
import time

from .client import Client, HerdrError
from .model import Agent, agents_from, clean
from .telemetry import Metrics, Telemetry, screen_call
from dataclasses import replace


@dataclass
class State:
    agents: list[Agent] = field(default_factory=list)
    previews: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    error: str = ""
    updated: float = 0.0
    metrics: dict[str, Metrics] = field(default_factory=dict)
    revision: int = 0


class Refresher:
    def __init__(self, client: Client, interval: float = 0.75):
        self.client, self.interval = client, interval
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.targets: dict[str, int] = {}
        self.state = State()
        self.telemetry = Telemetry()
        self.thread = threading.Thread(target=self.run, daemon=True, name="grid-refresh")

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        self.wake.set()

    def request(self, targets: dict[str, int], force: bool = False) -> None:
        with self.lock:
            changed = targets != self.targets
            self.targets = dict(targets)
        if changed or force:
            self.wake.set()

    def get(self, previous: State | None = None) -> State:
        with self.lock:
            s = self.state
            if previous is not None and previous.revision == s.revision:
                return previous
            return State(list(s.agents), dict(s.previews), dict(s.errors), s.error, s.updated,
                         {pid: replace(m) for pid, m in s.metrics.items()}, s.revision)

    def run(self) -> None:
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="grid-preview") as pool:
            while not self.stop.is_set():
                self.wake.clear()
                try:
                    agents = agents_from(self.client.snapshot())
                    self.telemetry.forget(agents)
                    live = {a.pane_id for a in agents}
                    with self.lock:
                        previous_agents = {a.pane_id: a.identity for a in self.state.agents}
                        unchanged = {a.pane_id for a in agents if previous_agents.get(a.pane_id) == a.identity}
                        targets = {pid: lines for pid, lines in self.targets.items() if pid in live}
                        self.state.agents = agents
                        self.state.error = ""
                        self.state.updated = time.monotonic()
                        self.state.previews = {p: text for p, text in self.state.previews.items() if p in unchanged}
                        self.state.errors = {p: text for p, text in self.state.errors.items() if p in unchanged}
                        self.state.metrics = {p: m for p, m in self.state.metrics.items() if p in unchanged}
                        self.state.revision += 1
                    for agent in agents:
                        if self.stop.is_set():
                            break
                        metrics = self.telemetry.read(agent)
                        with self.lock:
                            previous = self.state.metrics.get(agent.pane_id)
                            if not metrics.last_call and previous and previous.call_source == "screen":
                                metrics.last_call = previous.last_call
                                metrics.call_at = previous.call_at
                                metrics.call_source = "screen"
                            self.state.metrics[agent.pane_id] = metrics
                            cleared = metrics.call_source == "transcript" and self.state.errors.pop(agent.pane_id, None) is not None
                            if metrics != previous or cleared:
                                self.state.revision += 1
                    # Exact transcript calls already supply the card's tool.
                    # Terminal reads are needed only for the fallback path.
                    with self.lock:
                        targets = {pid: lines for pid, lines in targets.items()
                                   if self.state.metrics.get(pid, Metrics()).call_source != "transcript"}
                    reads = {pool.submit(self.client.read, pid, lines): pid
                             for pid, lines in targets.items()}
                    for future in as_completed(reads):
                        pid = reads[future]
                        if self.stop.is_set():
                            for pending in reads:
                                pending.cancel()
                            break
                        try:
                            text = clean(future.result())
                            with self.lock:
                                self.state.previews[pid] = text
                                self.state.errors.pop(pid, None)
                                metrics = self.state.metrics.get(pid)
                                if metrics and metrics.call_source != "transcript":
                                    name = screen_call(text)
                                    if name:
                                        if metrics.last_call != name:
                                            metrics.call_at = time.time()
                                        metrics.last_call, metrics.call_source = name, "screen"
                                self.state.revision += 1
                        except HerdrError as error:
                            with self.lock:
                                self.state.errors[pid] = str(error)
                                self.state.revision += 1
                except HerdrError as error:
                    with self.lock:
                        self.state.error = str(error)
                        self.state.revision += 1
                self.wake.wait(self.interval)
