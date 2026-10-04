"""Production Rust parity, including fixtures from the complete Python regression suite."""
import contextlib
from dataclasses import asdict
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
BINARY = ROOT / "target/release/herdr-agent-grid"
ADAPTER = ROOT / "target/release/examples/parity"
from herdr_agent_grid import telemetry as reference
from herdr_agent_grid.pricing import estimate, RATES


def adapter(inputs, env=None):
    result = subprocess.run([str(ADAPTER)], input="".join(json.dumps(x) + "\n" for x in inputs),
                            text=True, capture_output=True, check=True, env=env)
    return [json.loads(line) for line in result.stdout.splitlines()]


def equivalent(test, left, right, path=""):
    if isinstance(left, dict):
        test.assertEqual(left.keys(), right.keys(), path)
        for key in left:
            equivalent(test, left[key], right[key], path + "." + key)
    elif isinstance(left, list):
        test.assertEqual(len(left), len(right), path)
        for i, (a, b) in enumerate(zip(left, right)):
            equivalent(test, a, b, f"{path}[{i}]")
    elif isinstance(left, float):
        test.assertIsNotNone(right, path)
        test.assertAlmostEqual(left, right, places=8, msg=path)
    else:
        test.assertEqual(left, right, path)


@unittest.skipUnless(ADAPTER.exists(), "cargo build --release --examples first")
class NativeParityTests(unittest.TestCase):
    def test_all_provider_regression_events_match_reference(self):
        # Replay actual records exercised by the existing tests, including
        # streaming revisions, cache/tier pricing, resumes and notifications.
        modules = [importlib.import_module(name) for name in
                   ("test_telemetry", "test_subagents", "test_pricing", "test_performance_regressions")]
        original = reference.consume
        groups, retained = {}, []
        def observe(cursor, record, provider):
            key = id(cursor.metrics)
            retained.append(cursor.metrics)  # Prevent id reuse across file resets.
            step = dict(record=record, provider=provider, subagent=cursor.subagent,
                        child_hints={k: asdict(v) for k, v in cursor.child_hints.items()})
            step = json.loads(json.dumps(step))
            # Truncated readers deliberately seed/clear accounting separately;
            # those are tested through file replay, not this consume adapter.
            original(cursor, record, provider)
            if cursor.truncated:
                groups.pop(key, None)
                return
            pair = groups.setdefault(key, ([], []))
            pair[0].append(step)
            pair[1].append(json.loads(json.dumps(dict(metrics=asdict(cursor.metrics),
                session_id=cursor.session_id, finished_at=cursor.finished_at,
                lifecycle=cursor.lifecycle, lifecycle_at=cursor.lifecycle_at,
                child_hints={k: asdict(v) for k, v in cursor.child_hints.items()},
                spawn_calls={k: asdict(v) for k, v in cursor.spawn_calls.items()}))))
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(reference, "consume", observe))
            for module in modules:
                if hasattr(module, "consume"):
                    stack.enter_context(patch.object(module, "consume", observe))
            suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(m) for m in modules)
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
            self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        actual = adapter([dict(steps=steps) for steps, _ in groups.values()])
        count = 0
        for i, ((steps, expected), observed) in enumerate(zip(groups.values(), actual)):
            equivalent(self, expected, observed, f"sequence{i}")
            count += len(steps)
        self.assertGreater(count, 50)

    def test_all_model_rates_cache_and_long_context_cases_match(self):
        cases = []
        for model in [*RATES, "unknown", "", "anthropic/claude-opus-5-5", "gpt-6.1-sol-20261004"]:
            for usage in ({}, {"input_tokens": 1000, "output_tokens": 100},
                          {"input_tokens": 300000, "output_tokens": 123, "cached_input_tokens": 2000},
                          {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 20,
                           "cache_creation_input_tokens": 50, "cache_creation": {"ephemeral_1h_input_tokens": 30,"ephemeral_5m_input_tokens":20}},
                          {"input_tokens": 100, "output_tokens": 10, "speed": "fast", "inference_geo": "us"},
                          {"input_tokens": True, "output_tokens": 10},
                          {"input_tokens": 1, "output_tokens": 10, "cached_input_tokens": 100}):
                for provider in ("claude", "codex"):
                    cases.append(dict(op="estimate", provider=provider, model=model, usage=usage))
        for case, actual in zip(cases, adapter(cases)):
            expected = list(estimate(case["provider"], case["model"], case["usage"]))
            equivalent(self, expected, actual, str(case))

    def test_native_config_migration_and_collision_rules(self):
        before = '# keep this\n[[keys.command]]\nkey = ["alt+a"]\ntype = "plugin_action"\ncommand = "herdr-grid.open" # custom\n'
        result = adapter([dict(op="install", text=before)])[0]["text"]
        self.assertEqual(result, before.replace("herdr-grid.open", "herdr-agent-grid.open"))
        self.assertEqual(adapter([dict(op="install", text=result)])[0]["text"], result)
        for text in ('[keys]\ngoto = "cmd+g"\n', '[[keys.command]]\nkey=["ctrl+alt+g"]\ncommand="other"\n'):
            self.assertIn("already", adapter([dict(op="install",text=text)])[0]["error"])
        text = adapter([dict(op="install", text='[keys]\nnext_tab="ctrl+tab"\n')])[0]["text"]
        for key in ("cmd+g", "prefix+a", "ctrl+alt+g"):
            self.assertIn(key, text)


import test_terminal
@unittest.skipUnless(BINARY.exists(), "cargo build --release first")
class NativeTerminalTests(test_terminal.TerminalTests):
    def start(self, python=sys.executable):
        # Reuse the complete existing live Herdr PTY regression suite.
        original = subprocess.Popen
        def native(command, *args, **kwargs):
            if isinstance(command, list) and str(ROOT / "run.py") in command:
                command = [str(BINARY)]
            return original(command, *args, **kwargs)
        with patch("subprocess.Popen", native):
            super().start(python)

    def test_slow_preview_does_not_block_fast_preview_or_keyboard(self):
        script = self.fake.read_text().replace('elif args[:2] == ["pane", "read"]:',
            'elif args[:2] == ["pane", "read"]:\n    if args[2] == "w1:p1":\n        import time\n        time.sleep(1.5)\n        (root / "slow-finished").touch()')
        self.fake.write_text(script)
        self.start()
        self.await_condition(lambda: b"Bash" in self.output)
        self.assertFalse((self.root / "slow-finished").exists())
        os.write(self.master, b"q")
        self.await_condition(lambda: self.process.poll() is not None)
        self.assertEqual(self.process.returncode, 0)
        self.assertFalse((self.root / "slow-finished").exists())

    def test_installer_preserves_dotfiles_symlinks(self):
        config=self.root / "actual.toml"
        config.write_text('[keys]\nnext_tab="ctrl+tab"\n')
        link=self.root / "config.toml";link.symlink_to(config)
        subprocess.run([str(BINARY),"install","--config",str(link)],env=self.env,capture_output=True,check=True)
        self.assertTrue(link.is_symlink())
        self.assertIn("herdr-agent-grid.open",config.read_text())
        self.assertEqual(len(list(self.root.glob("actual.toml.bak-grid-*"))),1)

    def test_installer_links_backs_up_reloads_and_is_idempotent(self):
        original = subprocess.run
        def native(command, *args, **kwargs):
            if isinstance(command, list) and str(ROOT / "install.py") in command:
                command = [str(BINARY), "install", *command[2:]]
            return original(command, *args, **kwargs)
        with patch("subprocess.run", native):
            super().test_installer_links_backs_up_reloads_and_is_idempotent()

@unittest.skipUnless(ADAPTER.exists(), "cargo build --release --examples first")
class NativeSessionTests(unittest.TestCase):
    def test_exact_discovery_children_rotation_and_incremental_snapshots(self):
        from herdr_agent_grid.model import Agent
        from test_subagents import claude_message, codex_meta, write
        from dataclasses import replace
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                config = root / provider
                store = config / ("projects/project" if provider == "claude" else "sessions/date")
                sid = "11111111-1111-1111-1111-111111111111"
                path = store / (sid + ".jsonl")
                if provider == "claude":
                    write(path, {"type":"cost-state","totalCostUSD":1.25})
                    child = path.with_suffix("") / "subagents/agent-child.jsonl"
                    write(child, claude_message())
                    child.with_suffix(".meta.json").write_text('{"description":"Synthetic reviewer"}')
                    # These exact-parent checks must reject external files.
                    outside = root / "outside.jsonl"
                    write(outside, claude_message())
                    (child.parent / "agent-escape.jsonl").symlink_to(outside)
                    record = claude_message("m2")
                    record["timestamp"] = "2026-10-04T10:02:00Z"
                    record["message"]["stop_reason"] = "end_turn"
                else:
                    write(path, {"type":"session_meta","timestamp":"2026-10-04T10:00:00Z","payload":{"id":sid}})
                    child = store / "child.jsonl"
                    write(child, codex_meta("child", parent=sid),
                          {"type":"turn_context","payload":{"model":"gpt-6.1-sol","effort":"high"}},
                          {"type":"token_usage_record","payload":{"thread_token_usage":{"total_tokens":1100,"input_tokens":1000,"output_tokens":100}}})
                    write(store / "unrelated.jsonl", codex_meta("unrelated",parent="other"))
                    write(store / "guardian.jsonl", codex_meta("guardian",parent=sid,guardian=True))
                    record = {"type":"event_msg","timestamp":"2026-10-04T10:02:00Z","payload":{"type":"task_complete"}}
                env = dict(os.environ, HOME=folder, CLAUDE_CONFIG_DIR=str(config), CODEX_HOME=str(config),
                           HERDR_AGENT_GRID_CLAUDE_DIRS="", HERDR_GRID_CLAUDE_DIRS="")
                a = Agent("p1", provider, "parent", "Task", "Synthetic", "working", provider, "id", sid)
                ref = reference.Telemetry()
                process = subprocess.Popen([str(ADAPTER)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=env)
                def check(agent=a):
                    process.stdin.write(json.dumps(dict(op="read",agent=asdict(agent)))+"\n");process.stdin.flush()
                    actual=json.loads(process.stdout.readline())
                    with patch.dict(os.environ,env):
                        expected=json.loads(json.dumps(asdict(ref.read(agent))))
                    for m in (actual,expected):
                        m.pop("seen_at");m.pop("status_since")
                    equivalent(self,expected,actual,provider)
                    return actual
                try:
                    first=check();self.assertEqual(len(first["subagents"]),1)
                    write(child,record,append=True)
                    finished=check();self.assertEqual(finished["subagents"][0]["status"],"done")
                    if provider == "claude":
                        write(child,{"type":"user","timestamp":"2026-10-04T10:03:00Z","message":{"content":"Synthetic resume"}},append=True)
                    else:
                        write(child,{"type":"event_msg","timestamp":"2026-10-04T10:03:00Z","payload":{"type":"task_started"}},append=True)
                    self.assertEqual(check()["subagents"][0]["status"],"working")
                    self.assertEqual(first["subagents"][0]["status"],"working" if provider == "claude" else "unknown")
                    # Replacement on the same path must reset accounting and state.
                    write(child,{"type":"ignored"})
                    self.assertIsNone(check()["subagents"][0]["estimated_cost"])
                    self.assertIsNone(check(replace(a,session_kind="path",session_ref=str(root / "outside.jsonl")))["cost"])
                finally:
                    process.stdin.close();process.wait(timeout=5);process.stdout.close()
                    self.assertEqual(process.returncode,0)

    def test_codex_large_header_and_partial_tail_keep_explicit_children(self):
        from herdr_agent_grid.model import Agent
        from test_subagents import codex_meta,write
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=root/"codex/sessions";sid="11111111-1111-1111-1111-111111111111"
            path=store/(sid+".jsonl")
            write(path,{"type":"session_meta","payload":{"id":sid,"base_instructions":"Synthetic "*5000}},
                  *[{"type":"ignored","padding":"x"*1000} for _ in range(2200)],
                  {"type":"turn_context","payload":{"model":"gpt-6.1-sol","effort":"high"}},
                  {"type":"token_usage_record","payload":{"thread_token_usage":{"input_tokens":2000,"output_tokens":100,"total_tokens":2100},"usage":{"input_tokens":1000,"output_tokens":50}}})
            write(store/"child.jsonl",codex_meta("child",parent=sid))
            env=dict(os.environ,HOME=folder,CODEX_HOME=str(root/"codex"))
            a=Agent("p1","codex","Parent","Task","Synthetic","working","codex","path",str(path))
            with patch.dict(os.environ,env): expected=json.loads(json.dumps(asdict(reference.Telemetry().read(a))))
            actual=adapter([dict(op="read",agent=asdict(a))],env)[0]
            for m in (actual,expected):m.pop("seen_at");m.pop("status_since")
            equivalent(self,expected,actual)
            self.assertTrue(actual["estimate_partial"])
            self.assertEqual(len(actual["subagents"]),1)
