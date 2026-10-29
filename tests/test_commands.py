"""The two user-facing commands: /ch-stats and /ch-test.

These are the only things a user can type to see what the hook is doing. If
they lie — a stats panel that reports zero evaluations when the log has
entries, or a detector test that says "no trigger" for a text that triggers —
the user has no way to tell the plugin is broken. So both get tested against
a real event log on disk, not a mock.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location("continue_hook_plugin_cmds", REPO / "__init__.py")
PLUGIN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PLUGIN)


class _HomeCase(unittest.TestCase):
    """HOME points at a temp dir, so the event log and config are per-test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmp.name
        self.addCleanup(self._restore_home)
        self.addCleanup(self._tmp.cleanup)

    def _restore_home(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home

    def write_events(self, rows: list[dict]) -> None:
        path = Path(self._tmp.name) / ".hermes" / "continue-hook-events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )


class TestReadEvents(_HomeCase):
    def test_missing_log_is_empty(self) -> None:
        self.assertEqual(PLUGIN._read_events(), [])

    def test_reads_the_log(self) -> None:
        self.write_events([{"decision": "nudge", "reason": "trigger:offer"}])
        rows = PLUGIN._read_events()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "nudge")

    def test_blank_lines_are_skipped(self) -> None:
        path = Path(self._tmp.name) / ".hermes" / "continue-hook-events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n{"decision": "skip"}\n\n{"decision": "nudge"}\n', encoding="utf-8")
        self.assertEqual(len(PLUGIN._read_events()), 2)

    def test_corrupt_log_does_not_raise(self) -> None:
        path = Path(self._tmp.name) / ".hermes" / "continue-hook-events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"decision": "nudge"}\nnot json\n', encoding="utf-8")
        self.assertEqual(PLUGIN._read_events(), [])


class TestStatsCommand(_HomeCase):
    def test_no_events(self) -> None:
        out = PLUGIN._cmd_stats()
        self.assertIn("0 evaluations", out)
        self.assertIn("n/a", out)

    def test_counts_nudges_and_evaluations(self) -> None:
        self.write_events(
            [
                {"decision": "nudge", "reason": "trigger:offer"},
                {"decision": "nudge", "reason": "trigger:question_mark"},
                {"decision": "skip", "reason": "no_trigger"},
                {"decision": "skip", "reason": "boundary"},
            ]
        )
        out = PLUGIN._cmd_stats()
        self.assertIn("4 evaluations", out)
        self.assertIn("nudges 2", out)
        self.assertIn("50%", out)
        self.assertIn("trigger:offer", out)

    def test_reports_the_config_path(self) -> None:
        out = PLUGIN._cmd_stats()
        self.assertIn(str(Path(self._tmp.name) / ".hermes" / "continue_hook.json"), out)

    def test_reports_dry_run_state(self) -> None:
        out = PLUGIN._cmd_stats()
        self.assertIn("dry_run=", out)


class TestTestCommand(_HomeCase):
    def test_a_permission_ending_triggers(self) -> None:
        out = PLUGIN._cmd_test("好的，已经改完了。\n\n要不要我顺便把示例也补上？")
        self.assertIn("detect=True", out)
        self.assertIn("trigger:", out)

    def test_a_clean_delivery_does_not(self) -> None:
        out = PLUGIN._cmd_test("已完成 README 优化。验证：lint 通过。")
        self.assertIn("detect=False", out)

    def test_a_user_approval_request_is_excluded(self) -> None:
        """「需要你批准并提供 key」⇒ user_must 放行。

        2026-10-29：boundary 词表删除（用户明令：「我从来没说过要加 boundary 词表」）。
        那张表实测把 1430 条真实收尾里的 510 条（36%）误放行。
        护栏改由 nudge 文本的无条件清单承担；这条收尾本身是第二人称请求，
        由 user_must 判。
        """
        out = PLUGIN._cmd_test("这一步要调用付费 API，需要你批准并提供 key。要我继续吗？")
        self.assertIn("detect=False", out)
        self.assertIn("user_must", out)

    def test_empty_text(self) -> None:
        self.assertIn("detect=False", PLUGIN._cmd_test(""))


if __name__ == "__main__":
    unittest.main()
