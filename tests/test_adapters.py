"""Adapter tests — five hosts, one shared engine.

Each adapter is exercised with the REAL host contract:
  stdin  = the host's hook payload JSON
  stdout = the host's decision JSON (or empty = allow stop)

Hosts covered: Claude Code, Codex, Cursor, GitHub Copilot CLI, Gemini CLI.
No third-party deps; runs via `python tests/test_adapters.py` or
`python -m unittest discover -s tests`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADAPTERS = REPO / "adapters"
HOOKS = {
    "claude-code": ADAPTERS / "claude-code" / "stop_hook.py",
    "codex": ADAPTERS / "codex" / "stop_hook.py",
    "cursor": ADAPTERS / "cursor" / "stop_hook.py",
    "copilot": ADAPTERS / "copilot" / "stop_hook.py",
    "gemini": ADAPTERS / "gemini" / "stop_hook.py",
    "qwen": ADAPTERS / "qwen" / "stop_hook.py",
}
ASK_ZH = "要不要我顺便也把示例补上？"
PLAIN_ZH = "已完成，验证通过。"
BOUNDARY_ZH = "这会花 $69，需要你批准后我才买。"

CLAUDE_STYLE = json.dumps({
    "type": "assistant",
    "message": {"content": [{"type": "text", "text": ASK_ZH}]},
}, ensure_ascii=False) + "\n"
OPENAI_STYLE = json.dumps({"role": "assistant", "content": ASK_ZH}, ensure_ascii=False) + "\n"
ARRAY_STYLE = json.dumps([{"role": "assistant", "content": ASK_ZH}], ensure_ascii=False)


def run_hook(host: str, payload: dict, env: dict | None = None,
             transcript: str | None = None) -> tuple[int, str]:
    """Run a hook with an isolated HOME so the shared per-session state file
    (and the shared config) never leak between tests."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / ".hermes").mkdir(parents=True, exist_ok=True)
        if transcript is not None:
            tp = home / "transcript.jsonl"
            tp.write_text(transcript, encoding="utf-8")
            payload = {**payload, "transcript_path": str(tp)}
        e = dict(os.environ)
        e["HOME"] = str(home)
        e["HERMES_CONTINUE_HOOK"] = e.get("HERMES_CONTINUE_HOOK", "1")
        e.update(env or {})
        r = subprocess.run([sys.executable, str(HOOKS[host])],
                           input=json.dumps(payload).encode(),
                           capture_output=True, env=e, timeout=30)
        return r.returncode, r.stdout.decode().strip()


def claude_payload(**kw):
    base = {"session_id": "cc-1", "transcript_path": "/nonexistent.jsonl",
            "stop_hook_active": False, "last_assistant_message": ""}
    base.update(kw)
    return base


def codex_payload(**kw):
    base = {"session_id": "cx-1", "turn_id": "t1", "stop_hook_active": False,
            "last_assistant_message": ""}
    base.update(kw)
    return base


def cursor_payload(**kw):
    base = {"status": "completed", "loop_count": 0,
            "conversation_id": "cv-1", "transcript_path": "/nonexistent.jsonl"}
    base.update(kw)
    return base


def copilot_payload(**kw):
    base = {"sessionId": "cp-1", "timestamp": 1, "cwd": "/tmp",
            "transcriptPath": "/nonexistent.jsonl", "stopReason": "end_turn"}
    base.update(kw)
    return base


def qwen_payload(**kw):
    base = {"session_id": "qw-1", "stop_hook_active": False,
            "last_assistant_message": "", "transcript_path": "/nonexistent.jsonl"}
    base.update(kw)
    return base


def gemini_payload(**kw):
    base = {"session_id": "gm-1", "prompt": "hi", "prompt_response": "",
            "stop_hook_active": False, "transcript_path": "/nonexistent.jsonl"}
    base.update(kw)
    return base


class TestClaudeCodeHook(unittest.TestCase):
    def test_permission_seeking_blocks(self):
        code, out = run_hook("claude-code", claude_payload(last_assistant_message=ASK_ZH))
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["decision"], "block")
        self.assertIn("continue-hook", data["reason"])

    def test_stop_hook_active_skips(self):
        code, out = run_hook("claude-code",
                             claude_payload(stop_hook_active=True, last_assistant_message=ASK_ZH))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_declarative_allows_stop(self):
        code, out = run_hook("claude-code", claude_payload(last_assistant_message=PLAIN_ZH))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_boundary_allows_stop(self):
        code, out = run_hook("claude-code", claude_payload(last_assistant_message=BOUNDARY_ZH))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_transcript_fallback(self):
        code, out = run_hook("claude-code", claude_payload(), transcript=CLAUDE_STYLE)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_kill_switch(self):
        code, out = run_hook("claude-code", claude_payload(last_assistant_message=ASK_ZH),
                             env={"HERMES_CONTINUE_HOOK": "0"})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


class TestCodexHook(unittest.TestCase):
    def test_permission_seeking_blocks(self):
        code, out = run_hook("codex", codex_payload(last_assistant_message=ASK_ZH))
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["decision"], "block")
        self.assertIn("continue-hook", data["reason"])

    def test_stop_hook_active_skips(self):
        code, out = run_hook("codex",
                             codex_payload(stop_hook_active=True, last_assistant_message=ASK_ZH))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_declarative_allows_stop(self):
        code, out = run_hook("codex", codex_payload(last_assistant_message="Done. Tests pass."))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_missing_message_allows_stop(self):
        code, out = run_hook("codex", codex_payload())
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_kill_switch(self):
        code, out = run_hook("codex", codex_payload(last_assistant_message=ASK_ZH),
                             env={"HERMES_CONTINUE_HOOK": "0"})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


class TestCursorHook(unittest.TestCase):
    def test_permission_seeking_follows_up(self):
        code, out = run_hook("cursor", cursor_payload(), transcript=CLAUDE_STYLE)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("followup_message", data)
        self.assertIn("continue-hook", data["followup_message"])

    def test_loop_count_blocks_further_followups(self):
        # 5 is Cursor's own documented default. It is a loop guard, not the
        # per-turn cap: Cursor counts follow-ups per conversation, so using 2
        # here would end the product's job for the rest of the conversation.
        code, out = run_hook("cursor", cursor_payload(loop_count=5), transcript=CLAUDE_STYLE)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_loop_count_below_the_host_cap_still_nudges(self):
        code, out = run_hook("cursor", cursor_payload(loop_count=2), transcript=CLAUDE_STYLE)
        self.assertEqual(code, 0)
        self.assertIn("followup_message", out)

    def test_aborted_status_skips(self):
        code, out = run_hook("cursor", cursor_payload(status="aborted"), transcript=CLAUDE_STYLE)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_plain_transcript_allows_stop(self):
        code, out = run_hook("cursor", cursor_payload(),
                             transcript=CLAUDE_STYLE.replace(ASK_ZH, PLAIN_ZH))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_kill_switch(self):
        code, out = run_hook("cursor", cursor_payload(), transcript=CLAUDE_STYLE,
                             env={"HERMES_CONTINUE_HOOK": "0"})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


class TestCopilotHook(unittest.TestCase):
    def test_permission_seeking_blocks(self):
        code, out = run_hook("copilot", copilot_payload(), transcript=OPENAI_STYLE)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["decision"], "block")
        self.assertIn("continue-hook", data["reason"])

    def test_snake_case_input_accepted(self):
        code, out = run_hook("copilot", {
            "hook_event_name": "Stop", "session_id": "cp-2",
            "transcript_path": "/nonexistent.jsonl", "stop_reason": "end_turn",
            "stop_hook_active": False,
        }, transcript=OPENAI_STYLE)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_stop_hook_active_skips(self):
        code, out = run_hook("copilot", copilot_payload(stop_hook_active=True),
                             transcript=OPENAI_STYLE)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_kill_switch(self):
        code, out = run_hook("copilot", copilot_payload(), transcript=OPENAI_STYLE,
                             env={"HERMES_CONTINUE_HOOK": "0"})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


class TestGeminiHook(unittest.TestCase):
    def test_permission_seeking_denies(self):
        code, out = run_hook("gemini", gemini_payload(prompt_response=ASK_ZH))
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["decision"], "deny")
        self.assertIn("continue-hook", data["reason"])

    def test_stop_hook_active_skips(self):
        code, out = run_hook("gemini", gemini_payload(stop_hook_active=True, prompt_response=ASK_ZH))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_declarative_allows_stop(self):
        code, out = run_hook("gemini", gemini_payload(prompt_response=PLAIN_ZH))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_missing_response_allows_stop(self):
        code, out = run_hook("gemini", gemini_payload())
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_kill_switch(self):
        code, out = run_hook("gemini", gemini_payload(prompt_response=ASK_ZH),
                             env={"HERMES_CONTINUE_HOOK": "0"})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


class TestQwenHook(unittest.TestCase):
    """Qwen Code's Stop hook is Claude-Code-compatible."""

    def test_permission_seeking_blocks(self):
        code, out = run_hook("qwen", qwen_payload(last_assistant_message=ASK_ZH))
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["decision"], "block")
        self.assertIn("continue-hook", data["reason"])

    def test_stop_hook_active_skips(self):
        code, out = run_hook("qwen", qwen_payload(stop_hook_active=True, last_assistant_message=ASK_ZH))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_declarative_allows_stop(self):
        code, out = run_hook("qwen", qwen_payload(last_assistant_message=PLAIN_ZH))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_kill_switch(self):
        code, out = run_hook("qwen", qwen_payload(last_assistant_message=ASK_ZH),
                             env={"HERMES_CONTINUE_HOOK": "0"})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")


class TestCommonHelpers(unittest.TestCase):
    """The shared plumbing — transcript shapes and the common host schema."""

    @staticmethod
    def _load_common():
        for path in (str(ADAPTERS), str(REPO)):
            if path not in sys.path:
                sys.path.insert(0, path)
        import _common
        return _common

    def test_read_transcript_handles_three_shapes(self):
        common = self._load_common()
        with tempfile.TemporaryDirectory() as td:
            cases = {
                "claude.jsonl": CLAUDE_STYLE,
                "openai.jsonl": OPENAI_STYLE,
                "array.json": ARRAY_STYLE,
            }
            for name, content in cases.items():
                p = Path(td) / name
                p.write_text(content, encoding="utf-8")
                self.assertEqual(common.read_transcript(str(p)), ASK_ZH, name)

    def test_read_transcript_ignores_unknown_shape(self):
        common = self._load_common()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.txt"
            p.write_text("plain log lines\nnothing here", encoding="utf-8")
            self.assertEqual(common.read_transcript(str(p)), "")

    def test_read_transcript_missing_file(self):
        common = self._load_common()
        self.assertEqual(common.read_transcript("/definitely/not/here.jsonl"), "")
        self.assertEqual(common.read_transcript(""), "")

    def test_session_key_prefers_session_id(self):
        common = self._load_common()
        self.assertEqual(common.session_key({"session_id": "a", "conversation_id": "b"}, "x"), "a")
        self.assertEqual(common.session_key({"conversation_id": "b"}, "x"), "b")
        self.assertEqual(common.session_key({}, "x"), "x")

    def test_host_continued_detects_all_spellings(self):
        common = self._load_common()
        for field in ("stop_hook_active", "stopHookActive", "loop_active"):
            self.assertTrue(common.host_continued({field: True}), field)
        self.assertFalse(common.host_continued({"stop_hook_active": False}))

    def test_continue_is_not_a_continuation_signal(self):
        """Gemini's generic field means the opposite of "keep going".

        ``{"continue": false}`` stops the loop. Reading the field as "the host
        already continued" would make the first nudge of a session never fire.
        """
        common = self._load_common()
        self.assertFalse(common.host_continued({"continue": True}))
        self.assertFalse(common.host_continued({"continue": False}))

    def test_host_loop_count(self):
        common = self._load_common()
        self.assertEqual(common.host_loop_count({"loop_count": 3}), 3)
        self.assertEqual(common.host_loop_count({}), 0)


class TestTheChainCloses(unittest.TestCase):
    """A continuation the host performed ends *this* turn's chain.

    Without the close, ``max_nudges_per_turn`` silently became a quota shared
    across user turns: the second turn spent the first turn's allowance and the
    third got nothing at all.
    """

    def _run(self, home: Path, payload: dict) -> str:
        env = {**os.environ, "HOME": str(home), "HERMES_CONTINUE_HOOK": "1"}
        result = subprocess.run(
            [sys.executable, str(HOOKS["claude-code"])],
            input=json.dumps(payload).encode(),
            capture_output=True,
            env=env,
            timeout=30,
        )
        return result.stdout.decode().strip()

    def test_a_host_continuation_resets_the_per_turn_counter(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".hermes").mkdir()

            first = self._run(home, claude_payload(last_assistant_message=ASK_ZH))
            self.assertIn("#1", first, "the first turn should nudge once")

            # the host continues because of our block, then ends cleanly
            closed = self._run(home, claude_payload(
                stop_hook_active=True, last_assistant_message=PLAIN_ZH))
            self.assertEqual(closed, "", "a host-driven continuation must not nudge again")

            # a new user turn starts from zero — not from the previous turn's count
            second = self._run(home, claude_payload(last_assistant_message=ASK_ZH))
            self.assertIn("#1", second,
                          "the per-turn counter leaked into the next user turn")

            # and it keeps working turn after turn (the old bug froze it at #2)
            self._run(home, claude_payload(
                stop_hook_active=True, last_assistant_message=PLAIN_ZH))
            third = self._run(home, claude_payload(last_assistant_message=ASK_ZH))
            self.assertIn("#1", third)

    def test_closing_the_chain_does_not_reset_the_session_total(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".hermes").mkdir()
            for _ in range(3):
                self._run(home, claude_payload(last_assistant_message=ASK_ZH))
                self._run(home, claude_payload(
                    stop_hook_active=True, last_assistant_message=PLAIN_ZH))
            state = json.loads((home / ".hermes" / "continue-hook-state.json").read_text())
            entry = state["sessions"]["cc-1"]
            self.assertEqual(entry["count"], 0, "per-turn count must be closed out")
            self.assertEqual(entry["total"], 3, "the session total must keep accumulating")


if __name__ == "__main__":
    unittest.main()
