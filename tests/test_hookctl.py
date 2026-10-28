"""Tests for the decision service itself — the file every host shares.

Coverage of ``hookctl.py`` was 63%: the caps, the lock, the state migration,
the config coercion and the CLI were only exercised indirectly through the
adapters, so their failure modes were untested. These tests drive the service
directly.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import hookctl

ASK = "要不要我顺便也把示例补上？"
PLAIN = "已完成，42 项测试全绿。"


class HomeCase(unittest.TestCase):
    """Every test gets its own HOME: state, config and events live under it."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        os.environ.pop("HERMES_CONTINUE_HOOK", None)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        self._td.cleanup()

    def write_config(self, data: dict) -> Path:
        path = self.home / ".hermes" / "continue_hook.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def events(self) -> list[dict]:
        path = hookctl.default_events_path()
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestDecisionJson(HomeCase):
    def test_to_json_is_the_wire_format(self):
        payload = hookctl.Decision(should_nudge=True, reason="trigger:offer", attempt=1).to_json()
        self.assertEqual(payload, {"should_nudge": True, "reason": "trigger:offer", "attempt": 1})


class TestConfig(HomeCase):
    def test_a_missing_file_falls_back_to_defaults(self):
        cfg = hookctl.load_config()
        self.assertEqual(cfg.get("max_nudges_per_turn"), 2)

    def test_a_broken_file_falls_back_to_defaults(self):
        path = self.home / ".hermes" / "continue_hook.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(hookctl.load_config().get("max_nudges_per_turn"), 2)

    def test_an_unreadable_store_returns_empty(self):
        with mock.patch.object(hookctl, "ConfigStore", side_effect=OSError("nope")):
            self.assertEqual(hookctl.load_config(), {})


class TestState(HomeCase):
    def test_a_missing_state_file_is_empty(self):
        self.assertEqual(hookctl.read_state()["sessions"], {})

    def test_a_broken_state_file_is_empty(self):
        path = hookctl.default_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
        self.assertEqual(hookctl.read_state()["sessions"], {})

    def test_a_non_object_state_file_is_empty(self):
        path = hookctl.default_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(hookctl.read_state()["sessions"], {})

    def test_v1_state_is_migrated(self):
        path = hookctl.default_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"s1": {"count": 2, "last_ts": 123.0}}), encoding="utf-8")
        state = hookctl.read_state()
        self.assertEqual(state["version"], hookctl.STATE_VERSION)
        self.assertEqual(state["sessions"]["s1"]["count"], 2)
        self.assertEqual(state["sessions"]["s1"]["total"], 2)
        self.assertFalse(state["sessions"]["s1"]["chain"])

    def test_writing_the_state_is_owner_only(self):
        path = hookctl.default_state_path()
        hookctl.write_state({"version": 2, "sessions": {}})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_a_write_that_fails_does_not_raise(self):
        hookctl.write_state({"version": 2, "sessions": {}}, path=Path("/proc/nope/state.json"))

    def test_old_sessions_are_pruned(self):
        state = {
            "version": 2,
            "sessions": {
                "fresh": {"count": 0, "total": 1, "last_ts": time.time(), "chain": False},
                "stale": {
                    "count": 2,
                    "total": 9,
                    "last_ts": time.time() - 40 * 86400,
                    "chain": True,
                },
                "broken": "not an entry",
            },
        }
        hookctl._prune_sessions(state)
        self.assertIn("fresh", state["sessions"])
        self.assertNotIn("stale", state["sessions"])
        self.assertNotIn("broken", state["sessions"])


class TestNumberCoercion(HomeCase):
    def test_values_people_actually_write(self):
        cases = [
            (5, 5.0),
            (2.5, 2.5),
            ("7", 7.0),
            ("5m", 300.0),
            ("2h", 7200.0),
            ("30s", 30.0),
            ("1.5m", 90.0),
            (True, 99.0),  # bools are not numbers here
            ("two", 99.0),
            ("", 99.0),
            (None, 99.0),
            ([], 99.0),
            ({}, 99.0),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(hookctl._num(value, 99, integer=False), expected)

    def test_integer_coercion_keeps_zero(self):
        # 0 is a legitimate setting: "no nudges at all".
        self.assertEqual(hookctl._num(0, 5, integer=True), 0.0)


class TestLogEvent(HomeCase):
    def test_a_record_is_appended(self):
        hookctl.log_event({"session": "s", "decision": "skip", "reason": "no_trigger"})
        records = self.events()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["reason"], "no_trigger")
        self.assertIn("ts", records[0])

    def test_the_snippet_can_be_dropped(self):
        hookctl.log_event({"session": "s", "snippet": "secret text"}, snippet=False)
        self.assertNotIn("snippet", self.events()[-1])

    def test_the_log_is_owner_only(self):
        hookctl.log_event({"session": "s"})
        path = hookctl.default_events_path()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_an_unwritable_log_does_not_raise(self):
        hookctl.log_event({"session": "s"}, path=Path("/proc/nope/events.jsonl"))


class TestCloseChain(HomeCase):
    def test_it_resets_the_turn_counter_and_keeps_the_session_total(self):
        sid = "s1"
        self.assertTrue(hookctl.decide(ASK, session_id=sid, host="t").should_nudge)
        before = hookctl.read_state()["sessions"][sid]
        self.assertEqual(before["count"], 1)
        self.assertEqual(before["total"], 1)

        hookctl.close_chain(session_id=sid, host="t")
        after = hookctl.read_state()["sessions"][sid]
        self.assertEqual(after["count"], 0)
        self.assertEqual(after["total"], 1, "the session total must keep accumulating")
        self.assertFalse(after["chain"])
        self.assertEqual(self.events()[-1]["reason"], "chain_closed")


class TestDecide(HomeCase):
    def test_the_environment_kill_switch(self):
        os.environ["HERMES_CONTINUE_HOOK"] = "0"
        decision = hookctl.decide(ASK, session_id="s", host="t")
        self.assertFalse(decision.should_nudge)
        self.assertEqual(decision.reason, "disabled_env")
        self.assertEqual(self.events()[-1]["reason"], "disabled_env")

    def test_the_config_kill_switch(self):
        self.write_config({"enabled": False})
        self.assertEqual(hookctl.decide(ASK, session_id="s", host="t").reason, "disabled")

    def test_empty_text_is_a_skip(self):
        self.assertEqual(hookctl.decide("   ", session_id="s", host="t").reason, "empty")

    def test_a_detector_crash_is_contained(self):
        with mock.patch.object(hookctl, "detect", side_effect=RuntimeError("boom")):
            decision = hookctl.decide(ASK, session_id="s", host="t")
        self.assertFalse(decision.should_nudge)
        self.assertEqual(decision.reason, "error:RuntimeError")

    def test_the_session_cap_stops_it(self):
        self.write_config({"max_nudges_per_session": 1, "max_nudges_per_turn": 5})
        first = hookctl.decide(ASK, session_id="s", host="t")
        self.assertTrue(first.should_nudge)
        hookctl.close_chain(session_id="s", host="t")
        second = hookctl.decide(ASK, session_id="s", host="t")
        self.assertFalse(second.should_nudge)
        self.assertEqual(second.reason, "max_nudges_per_session")

    def test_the_cooldown_holds(self):
        self.write_config({"cooldown_seconds": 60})
        self.assertTrue(hookctl.decide(ASK, session_id="s", host="t").should_nudge)
        hookctl.close_chain(session_id="s", host="t")
        self.assertEqual(hookctl.decide(ASK, session_id="s", host="t").reason, "cooldown")

    def test_dry_run_records_without_nudging(self):
        self.write_config({"dry_run": True})
        decision = hookctl.decide(ASK, session_id="s", host="t")
        self.assertFalse(decision.should_nudge)
        self.assertEqual(self.events()[-1]["decision"], "skip_dryrun")

    def test_the_config_can_redirect_the_log(self):
        target = self.home / "private" / "events.jsonl"
        self.write_config({"events_path": str(target)})
        hookctl.decide(ASK, session_id="s", host="t")
        self.assertTrue(target.exists())
        self.assertFalse(hookctl.default_events_path().exists())

    def test_continued_keeps_the_chain_open(self):
        first = hookctl.decide(ASK, session_id="s", host="t")
        self.assertEqual(first.attempt, 0)
        second = hookctl.decide(ASK, session_id="s", host="t", continued=True)
        self.assertEqual(second.attempt, 1, "a host-driven continuation stays in the same turn")


class TestCli(HomeCase):
    def run_cli(self, *args: str, stdin: str = "") -> dict:
        result = subprocess.run(
            [sys.executable, str(REPO / "hookctl.py"), *args],
            input=stdin.encode(),
            capture_output=True,
            env={**os.environ, "HOME": str(self.home)},
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return json.loads(result.stdout.decode())

    def test_arguments_are_enough(self):
        out = self.run_cli("--text", ASK, "--session", "s", "--host", "t")
        self.assertTrue(out["should_nudge"])

    def test_text_from_stdin(self):
        out = self.run_cli("--text", "-", "--session", "s", "--host", "t", stdin=ASK)
        self.assertTrue(out["should_nudge"])

    def test_nudge_text_flag(self):
        out = self.run_cli("--text", ASK, "--session", "s", "--host", "t", "--nudge-text")
        self.assertIn("continue-hook", out["nudge"])

    def test_a_clean_reply_has_no_nudge_text(self):
        out = self.run_cli("--text", PLAIN, "--session", "s", "--host", "t", "--nudge-text")
        self.assertFalse(out["should_nudge"])
        self.assertNotIn("nudge", out)


class TestTheCliFunction(HomeCase):
    """``_cli`` is what the non-Python hosts run — call it in-process so a
    subprocess boundary does not hide it from coverage."""

    def call(self, *args: str) -> dict:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = hookctl._cli(list(args))
        self.assertEqual(code, 0)
        return json.loads(out.getvalue().strip())

    def test_a_normal_decision(self):
        self.assertTrue(self.call("--text", ASK, "--session", "s", "--host", "t")["should_nudge"])

    def test_text_from_stdin(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("sys.stdin", io.StringIO(ASK)):
            hookctl._cli(["--text", "-", "--session", "s", "--host", "t"])
        self.assertTrue(json.loads(out.getvalue().strip())["should_nudge"])

    def test_a_broken_stdin_does_not_crash(self):
        out = io.StringIO()
        broken = mock.Mock()
        broken.read.side_effect = OSError("no stdin")
        with contextlib.redirect_stdout(out), mock.patch("sys.stdin", broken):
            hookctl._cli(["--text", "-", "--session", "s", "--host", "t"])
        self.assertFalse(json.loads(out.getvalue().strip())["should_nudge"])

    def test_nudge_text_is_attached(self):
        self.assertIn(
            "continue-hook",
            self.call("--text", ASK, "--session", "s", "--host", "t", "--nudge-text")["nudge"],
        )

    def test_a_failing_decision_still_exits_zero(self):
        out = io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            mock.patch.object(hookctl, "decide", side_effect=RuntimeError("boom")),
        ):
            code = hookctl._cli(["--text", ASK, "--session", "s", "--host", "t"])
        self.assertEqual(code, 0, "a decision failure must not break the host")
        self.assertEqual(json.loads(out.getvalue().strip())["reason"], "error:RuntimeError")


class TestEdgeBranches(HomeCase):
    def test_pruning_tolerates_a_state_without_sessions(self):
        state: dict = {"version": 2}
        hookctl._prune_sessions(state)  # must not raise
        self.assertEqual(state, {"version": 2})

    def test_a_state_that_cannot_be_chmodded_is_still_written(self):
        # chmod failing is not a reason to lose the counters.
        with mock.patch.object(Path, "chmod", side_effect=OSError("read-only")):
            hookctl.write_state({"version": 2, "sessions": {}})
        self.assertTrue(hookctl.default_state_path().exists())

    def test_the_lock_is_reentrant_across_processes(self):
        # Two decisions in a row must both land: the lock must be released.
        first = hookctl.decide(ASK, session_id="s", host="t")
        second = hookctl.decide(ASK, session_id="s", host="t")
        self.assertTrue(first.should_nudge and second.should_nudge)
        self.assertEqual(second.attempt, 1)

    def test_a_session_entry_that_is_not_an_object_is_replaced(self):
        path = hookctl.default_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 2, "sessions": {"s": "garbage"}}), encoding="utf-8")
        decision = hookctl.decide(ASK, session_id="s", host="t")
        self.assertTrue(decision.should_nudge)
        self.assertEqual(decision.attempt, 0)


if __name__ == "__main__":
    unittest.main()
