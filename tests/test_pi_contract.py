"""Contract tests for the Pi extension.

The Pi extension is TypeScript and Pi's runtime is not available in CI, so
the runtime behaviour is covered by two things:

  1. a **contract test** (here) — asserts the extension uses Pi's documented
     actionable boundary and return shape, so a refactor that breaks the
     contract fails loudly instead of silently no-oping;
  2. the **shared detector CLI** — the extension's only decision input, which
     is tested directly in `test_detector.py` and `test_opencode.mjs`.

If Pi's extension API changes, this test is the first thing that should fail.
"""

from __future__ import annotations

import unittest
from pathlib import Path

PI_EXT = Path(__file__).resolve().parent.parent / "adapters" / "pi" / "continue-hook.ts"


class TestPiExtensionContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = PI_EXT.read_text(encoding="utf-8")

    def test_file_exists(self):
        self.assertTrue(PI_EXT.exists())

    def test_registers_agent_before_settle(self):
        """agent_before_settle is Pi's documented final actionable boundary."""
        self.assertIn('pi.on("agent_before_settle"', self.src)

    def test_returns_continue_true(self):
        self.assertRegex(self.src, r"continue:\s*true")

    def test_proposes_custom_message_entry(self):
        self.assertRegex(self.src, r"type:\s*[\"']custom_message[\"']")

    def test_delegates_detection_to_shared_detector(self):
        """The extension must not re-implement the rules; it calls detector.py."""
        self.assertIn("detector.py", self.src)
        self.assertIn("--text", self.src)

    def test_delegates_the_caps_to_the_shared_service(self):
        """The caps live in hookctl.py — one implementation for every host.

        The host plugin must not re-implement policy, or a cap would mean two
        different things in two hosts.
        """
        self.assertIn("hookctl.py", self.src)
        self.assertIn("--nudge-text", self.src)
        self.assertNotIn("max_nudges_per_turn", self.src)

    def test_fails_open(self):
        """A detector error must never throw into Pi's event loop."""
        self.assertRegex(self.src, r"catch\s*\{[\s\S]{0,200}return undefined")

    def test_the_reply_goes_over_stdin_not_argv(self):
        """A reply passed as an argument lands in /proc/<pid>/cmdline, where any
        process on the machine can read it, and a long reply hits E2BIG."""
        self.assertRegex(self.src, r'"--text",\s*"-"')
        self.assertRegex(self.src, r"input:\s*text")

    def test_policy_lives_in_the_shared_engine_only(self):
        """No second state file, no local counter, no local cooldown — one
        decision service, or the hosts disagree about the caps."""
        for symbol in ("STATE_PATH", "EVENTS_PATH", "bumpCounter", "function logEvent"):
            self.assertNotIn(
                symbol, self.src, f"{symbol} belongs in hookctl.py, not in the Pi extension"
            )


class TestDetectorCli(unittest.TestCase):
    """The detector CLI is the contract shared by the JS/TS adapters."""

    @classmethod
    def setUpClass(cls):
        cls.detector = Path(__file__).resolve().parent.parent / "detector.py"

    def _run(self, text: str, attempt: int = 0):
        import json
        import subprocess
        import sys

        r = subprocess.run(
            [sys.executable, str(self.detector), "--text", text, "--attempt", str(attempt)],
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(r.returncode, 0)
        return json.loads(r.stdout.decode().strip())

    def test_permission_seeking_is_detected(self):
        out = self._run("要不要我顺便也把示例补上？")
        self.assertTrue(out["should_nudge"])
        self.assertTrue(out["reason"].startswith("trigger:"))

    def test_declarative_ending_is_not_detected(self):
        out = self._run("已完成，验证通过。")
        self.assertFalse(out["should_nudge"])

    def test_boundary_is_excluded(self):
        out = self._run("这会花 $69，需要你批准后我才买。")
        self.assertFalse(out["should_nudge"])
        self.assertEqual(out["reason"], "boundary")

    def test_max_nudges_returns_max_nudges(self):
        out = self._run("要不要我继续？", attempt=5)
        self.assertFalse(out["should_nudge"])
        self.assertEqual(out["reason"], "max_nudges")

    def test_empty_text(self):
        out = self._run("")
        self.assertFalse(out["should_nudge"])
        self.assertEqual(out["reason"], "empty")


if __name__ == "__main__":
    unittest.main()
