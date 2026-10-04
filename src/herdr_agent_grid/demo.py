"""Synthetic terminals for a preview without access to a live Herdr socket."""
import time

from .model import Agent
from .refresh import State
from .telemetry import Metrics, Subagent, ToolCall


def demo_state() -> State:
    examples = [
        ("claude", "frontend", "Polish dashboard layout", "demo-platform", "working",
         "⏺ Reading src/components/Dashboard.tsx\n  Found 3 layout breakpoints.\n\n⏺ Updating the tablet grid…\n  Edit src/styles/dashboard.css\n  + grid-template-columns: repeat(2, 1fr);\n\n✻ Working…"),
        ("codex", "grid", "Build the agent grid plugin", "herdr-agent-grid", "working",
         "• Added the full-panel plugin entrypoint.\n• Preview refresh runs in the background.\n\n  Testing responsive tile layouts…\n  80×24   160×48   240×64\n\n• Checking keyboard and mouse navigation."),
        ("claude", "api", "Add retry handling", "demoapi", "blocked",
         "⏺ Bash(python3 -m pytest tests/test_retry.py)\n\n  This command requires approval.\n\n  1. Yes\n  2. Yes, and don't ask again\n  3. No\n\n❯ Waiting for your decision"),
        ("codex", "tests", "Verify permission checks", "demo-platform", "done",
         "• Validation complete.\n\n  38 tests passed in 2.41s\n  No lint errors.\n\n• Role checks now cover team membership.\n  Changes are ready for review.\n\n› Ask for a follow-up"),
        ("gemini", "docs", "Update API examples", "demo-docs", "idle",
         "Updated README.md with the current API.\n\n  + session.snapshot\n  + pane.read --source visible\n  + agent.focus <pane-id>\n\nReady for the next task.\n\n> "),
        ("claude", "review", "Review the query cache", "demo-workspace", "working",
         "⏺ Read src/cache/query.ts\n⏺ Read tests/cache/query.test.ts\n\n  Tracing invalidation after a mutation…\n  Found 2 subscribers to the cache key.\n\n✻ Reviewing edge cases…"),
    ]
    agents, previews = [], {}
    for i, (kind, name, title, space, status, text) in enumerate(examples, 1):
        pid = f"w{i}:p1"
        agents.append(Agent(pid, kind, name, title, space, status))
        previews[pid] = text
    now = time.time()
    tools = ["Edit", "exec_command", "Bash", "apply_patch", "—", "Read"]
    details = ["src/styles/dashboard.css", "Check responsive card layouts", "Run the retry test suite",
               "tests/test_permissions.py", "", "src/cache/query.ts"]
    ages = [8, 3, 22, 74, 180, 2]
    session_ages = [1848, 782, 2437, 3760, 1215, 527]
    costs = [1.24, None, 2.68, None, None, .77]
    tokens = [184200, 93200, 247500, 112700, None, 62800]
    metrics = {}
    phases = ["writing", "tool", "tool", "writing", "", "thinking"]
    models = ["claude-sonnet", "gpt-demo", "claude-opus", "gpt-demo", "", "claude-opus"]
    efforts = ["medium", "xhigh", "high", "high", "", "max"]
    messages = ["I’m adjusting the spacing and checking the tablet layout.",
                "The grid is ready; I’m checking keyboard navigation.",
                "The retry tests need your approval to run.",
                "All 38 tests passed. The permission checks are ready for review.",
                "", "I found two cache subscribers and am tracing invalidation."]
    trails = [ ["Read", "Grep", "Read", "Bash", "Edit"], ["Read", "exec_command", "apply_patch", "exec_command"],
               ["Read", "Bash", "Edit", "Bash"], ["Read", "apply_patch", "exec_command", "apply_patch"],
               [], ["Glob", "Grep", "Read", "Read"] ]
    for i, a in enumerate(agents):
        known = tools[i] != "—"
        metrics[a.pane_id] = Metrics(
            last_call=tools[i] if known else "", call_at=now - ages[i] if known else None,
            call_done=i not in (1, 2) if known else None, call_detail=details[i],
            call_source="transcript" if known else "", started_at=now - session_ages[i] if known else None,
            seen_at=now - 90, status_since=now - 38, tokens=tokens[i], cost=costs[i],
            model=models[i], effort=efforts[i], source=a.kind + " session log" if known else "Herdr status", phase=phases[i],
            estimated_cost=.43 if i == 1 else .58 if i == 3 else None,
            estimate_note="Standard API token rates · synthetic demo",
            last_message=messages[i], message_at=now - ages[i] if messages[i] else None,
            trail=tuple(ToolCall(f"demo-{i}-{j}", name, now - ages[i] - (len(trails[i]) - 1 - j) * 5,
                                 done=j < len(trails[i]) - 1 or i not in (1, 2),
                                 error=i == 2 and j == 1) for j, name in enumerate(trails[i])))
    metrics["w1:p1"].subagents = (
        Subagent("demo-layout", "Layout audit", "claude-sonnet-5-5", "high",
                 estimated_cost=.09, started_at=now - 81, status="working"),)
    metrics["w2:p1"].subagents = (
        Subagent("demo-keyboard", "Keyboard tests", "gpt-6.1-sol", "high",
                 estimated_cost=.06, started_at=now - 54, status="working"),
        Subagent("demo-cost", "Cost review", "gpt-6.1-sol", "xhigh",
                 estimated_cost=.04, started_at=now - 45, finished_at=now - 10, duration_s=35, status="done"),)
    return State(agents, previews, updated=time.monotonic(), metrics=metrics)
