"""Exercise the actual curses process through a PTY, using a fake Herdr CLI."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

FAKE = '''#!{python}
import json, os, sys
from pathlib import Path
root = Path(os.environ["GRID_TEST_DIR"])
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as out:
    out.write(json.dumps(args) + "\\n")
if args == ["api", "snapshot"]:
    snapshot = json.loads((root / "snapshot.json").read_text())
    result = {{"snapshot": snapshot}}
elif args[:2] == ["pane", "read"]:
    result = {{"read": {{"text": "● Bash(test)\\nCURRENT PROMPT"}}}}
elif args[:2] == ["agent", "focus"]:
    result = {{"agent": {{"pane_id": args[2]}}}}
else:
    result = {{"ok": True}}
print(json.dumps({{"result": result}}))
'''


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = self.root / "herdr"
        self.fake.write_text(FAKE.format(python=sys.executable))
        self.fake.chmod(0o755)
        agents = [{"pane_id": "w1:p1", "agent": "claude", "name": "first", "agent_status": "working"},
                  {"pane_id": "w1:p2", "agent": "codex", "name": "second", "agent_status": "blocked"}]
        (self.root / "snapshot.json").write_text(json.dumps({"agents": agents}))
        self.env = dict(os.environ, HERDR_ENV="1", HERDR_SOCKET_PATH="",
                        HERDR_BIN_PATH=str(self.fake), GRID_TEST_DIR=str(self.root), TERM="xterm-256color")
        self.process = None
        self.master = None

    def tearDown(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.master is not None:
            os.close(self.master)
        self.temp.cleanup()

    def calls(self):
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def start(self, python=sys.executable):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 38, 140, 0, 0))
        self.process = subprocess.Popen([python, str(ROOT / "run.py")],
                                        stdin=slave, stdout=slave, stderr=slave,
                                        env=self.env, cwd=ROOT)
        os.close(slave)
        self.output = bytearray()

    def await_condition(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                try:
                    self.output.extend(os.read(self.master, 65536))
                except OSError:
                    pass
            if predicate():
                return
            if self.process.poll() is not None:
                if predicate():
                    return
                break
        self.fail("terminal condition timed out: " + self.output.decode(errors="replace")[-2000:])

    def test_live_previews_keyboard_focus_and_clean_exit(self):
        self.start()
        self.await_condition(lambda: b"Bash" in self.output)
        self.assertNotIn(b"CURRENT PROMPT", self.output)
        self.assertIn(b"first", self.output)
        self.assertIn(b"second", self.output)
        os.write(self.master, b"\t\r")
        self.await_condition(lambda: self.process.poll() is not None)
        self.assertEqual(self.process.returncode, 0)
        self.assertIn(["agent", "focus", "w1:p2"], self.calls())
        self.assertIn(b"\x1b[?1049l", self.output)
        self.assertTrue(all(call[:2] in (["api", "snapshot"], ["pane", "read"], ["agent", "focus"])
                            for call in self.calls()))

    def test_mouse_click_focuses_agent(self):
        self.start()
        self.await_condition(lambda: b"Bash" in self.output)
        # Match the mode advertised by this host's curses/terminfo. macOS's
        # system curses advertises legacy X10; newer ncurses uses SGR.
        if b"\x1b[?1006h" in self.output:
            click = b"\x1b[<0;4;8M\x1b[<0;4;8m"
        else:
            click = b"\x1b[M" + bytes((32, 4 + 32, 8 + 32))
        os.write(self.master, click)
        self.await_condition(lambda: self.process.poll() is not None)
        self.assertEqual(self.process.returncode, 0)
        self.assertIn(["agent", "focus", "w1:p1"], self.calls())

    def test_close_does_not_focus_or_send_agent_input(self):
        self.start()
        self.await_condition(lambda: b"Bash" in self.output)
        os.write(self.master, b"\x1b")
        self.await_condition(lambda: self.process.poll() is not None)
        self.assertEqual(self.process.returncode, 0)
        self.assertFalse(any(call[0] == "agent" for call in self.calls()))

    @unittest.skipUnless(sys.platform == "darwin" and Path("/usr/bin/python3").exists(),
                         "requires macOS system Python")
    def test_macos_system_python_opens_and_animates(self):
        self.start("/usr/bin/python3")
        self.await_condition(lambda: b"Bash" in self.output)
        self.assertIsNone(self.process.poll())
        self.assertNotIn(b"Traceback", self.output)
        os.write(self.master, b"q")
        self.await_condition(lambda: self.process.poll() is not None)
        self.assertEqual(self.process.returncode, 0)

    def subagent_fixture(self, count):
        sid = "11111111-1111-1111-1111-111111111111"
        config = self.root / "claude"
        project = config / "projects" / "synthetic"
        project.mkdir(parents=True)
        parent = project / (sid + ".jsonl")
        parent.write_text(json.dumps({"type": "cost-state", "totalCostUSD": 1.23}) + "\n")
        children = project / sid / "subagents"
        children.mkdir(parents=True)
        for i in range(count):
            child_id = f"child-{i:02}"
            name = ("API tests", "Cost review", "UI checks")[i] if i < 3 else "ZZZZ last child" if i == 19 else f"Done{i:02}"
            effort = "medium" if i == 1 else "high"
            (children / f"agent-{child_id}.meta.json").write_text(json.dumps({"description": name}))
            record = {
                "type": "assistant", "isSidechain": True, "timestamp": "2026-10-04T10:00:00Z", "effort": effort,
                "message": {"id": child_id, "model": "claude-sonnet-4-6", "content": [],
                            "usage": {"input_tokens": 1000, "output_tokens": 1000}}}
            if i >= 3:
                record["message"]["stop_reason"] = "end_turn"
            (children / f"agent-{child_id}.jsonl").write_text(json.dumps(record) + "\n")
        snapshot = json.loads((self.root / "snapshot.json").read_text())
        snapshot["agents"][0]["agent_session"] = {"kind": "id", "value": sid}
        (self.root / "snapshot.json").write_text(json.dumps(snapshot))
        self.env["CLAUDE_CONFIG_DIR"] = str(config)

    def test_live_subagent_preview_and_details_from_exact_parent_logs(self):
        self.subagent_fixture(9)
        self.start()
        self.await_condition(lambda: b"Done08" in self.output and b"3 working / 9 total" in self.output)
        self.assertIn(b"API tests", self.output)
        os.write(self.master, b"z")
        self.await_condition(lambda: b"Cost review" in self.output and b"Sonnet 4.6@medium" in self.output)
        self.assertIn(b"MODEL@EFFORT", self.output)
        self.assertIn(b"Sonnet 4.6@medium", self.output)
        self.assertIn(b"~$0.02", self.output)
        os.write(self.master, b"q")  # Leave details, then close the panel.
        os.write(self.master, b"q")
        self.await_condition(lambda: self.process.poll() is not None)
        self.assertEqual(self.process.returncode, 0)

    def test_page_down_reaches_hidden_subagents_in_actual_curses_details(self):
        self.subagent_fixture(20)
        self.start()
        self.await_condition(lambda: b"3 working / 20 total" in self.output)
        self.assertNotIn(b"ZZZZ", self.output)
        os.write(self.master, b"z")
        self.await_condition(lambda: b"PgUp/PgDn scroll" in self.output)
        os.write(self.master, b"\x1b[6~")
        # ncurses may repaint only a changed numeric suffix. A distinct prefix
        # proves the last row became visible without assuming a full repaint.
        self.await_condition(lambda: b"ZZZZ" in self.output)
        self.assertFalse(any(call[0] == "agent" for call in self.calls()))
        os.write(self.master, b"qq")
        self.await_condition(lambda: self.process.poll() is not None)
        self.assertEqual(self.process.returncode, 0)

    def test_startup_failure_stays_visible_until_dismissed(self):
        self.env["HERDR_ENV"] = "0"
        self.start()
        self.await_condition(lambda: b"Press Enter to close" in self.output)
        self.assertIsNone(self.process.poll())
        self.assertIn(b"run from a Herdr pane", self.output)
        os.write(self.master, b"\n")
        self.await_condition(lambda: self.process.poll() is not None)
        self.assertEqual(self.process.returncode, 2)

    def test_installer_links_backs_up_reloads_and_is_idempotent(self):
        config = self.root / "config.toml"
        original = '[keys]\nnext_tab = "ctrl+tab"\n'
        config.write_text(original)
        command = [sys.executable, str(ROOT / "install.py"), "--config", str(config), "--open"]
        installed = subprocess.run(command, env=self.env, capture_output=True, text=True)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        backups = list(self.root.glob("config.toml.bak-grid-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), original)
        after = config.read_text()
        installed = subprocess.run(command, env=self.env, capture_output=True, text=True)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(config.read_text(), after)
        self.assertEqual(len(list(self.root.glob("config.toml.bak-grid-*"))), 1)
        self.assertIn(["plugin", "link", str(ROOT), "--enabled"], self.calls())
        self.assertIn(["server", "reload-config"], self.calls())
        self.assertIn(["plugin", "action", "invoke", "herdr-agent-grid.open"], self.calls())


if __name__ == "__main__":
    unittest.main()
