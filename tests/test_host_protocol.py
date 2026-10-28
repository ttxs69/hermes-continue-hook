"""The host-protocol layer: transcript shapes, session keys, loop caps.

Why this file exists separately from test_adapters.py: the functions below are
the *boundary* between ten hosts that spell everything differently and one
shared decision service. When a host ships a new payload shape, the bug shows
up here — not in the detector, and not in the agent. So these get tested
directly, with the shapes seen in the wild rather than the shape we wish for.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters import _common


class TestTextFromContent(unittest.TestCase):
    """A message body arrives in whatever shape the host felt like."""

    def test_plain_string(self) -> None:
        self.assertEqual(_common._text_from_content("hello"), "hello")

    def test_content_block_list(self) -> None:
        body = [
            {"type": "text", "text": "first"},
            {"type": "image", "source": {}},  # not text — dropped
            {"type": "text", "text": "second"},
        ]
        self.assertEqual(_common._text_from_content(body), "first\nsecond")

    def test_output_text_block(self) -> None:
        body = [{"type": "output_text", "text": "from the responses API"}]
        self.assertEqual(_common._text_from_content(body), "from the responses API")

    def test_dict_with_text(self) -> None:
        self.assertEqual(_common._text_from_content({"text": "body"}), "body")

    def test_dict_without_text(self) -> None:
        self.assertEqual(_common._text_from_content({"other": 1}), "")

    def test_unknown_type(self) -> None:
        self.assertEqual(_common._text_from_content(42), "")


class TestRecordToText(unittest.TestCase):
    def test_claude_style(self) -> None:
        record = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
        self.assertEqual(_common._record_to_text(record), "hi")

    def test_openai_style(self) -> None:
        record = {"role": "assistant", "content": "hi"}
        self.assertEqual(_common._record_to_text(record), "hi")

    def test_user_record_is_not_assistant_text(self) -> None:
        self.assertEqual(_common._record_to_text({"role": "user", "content": "hi"}), "")

    def test_tool_record_is_not_assistant_text(self) -> None:
        self.assertEqual(_common._record_to_text({"type": "tool_use", "content": "hi"}), "")

    def test_not_a_dict(self) -> None:
        self.assertEqual(_common._record_to_text("nope"), "")


class TestParseTranscript(unittest.TestCase):
    """Three shapes seen in the wild; anything else yields "" (fail open)."""

    def test_jsonl_claude_style(self) -> None:
        raw = "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "do it"}}),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "done"}]},
                    }
                ),
            ]
        )
        self.assertEqual(_common.parse_transcript(raw), "done")

    def test_jsonl_openai_style(self) -> None:
        raw = json.dumps({"role": "assistant", "content": "done"})
        self.assertEqual(_common.parse_transcript(raw), "done")

    def test_json_array(self) -> None:
        raw = json.dumps([{"role": "assistant", "content": "done"}])
        self.assertEqual(_common.parse_transcript(raw), "done")

    def test_newest_assistant_wins(self) -> None:
        raw = "\n".join(
            [
                json.dumps({"role": "assistant", "content": "first"}),
                json.dumps({"role": "user", "content": "more"}),
                json.dumps({"role": "assistant", "content": "second"}),
            ]
        )
        self.assertEqual(_common.parse_transcript(raw), "second")

    def test_empty(self) -> None:
        self.assertEqual(_common.parse_transcript(""), "")
        self.assertEqual(_common.parse_transcript("   \n "), "")

    def test_unparseable_fails_open(self) -> None:
        self.assertEqual(_common.parse_transcript("not json at all"), "")

    def test_json_object_with_no_assistant_record(self) -> None:
        self.assertEqual(_common.parse_transcript(json.dumps({"role": "user", "content": "x"})), "")


class TestSessionKey(unittest.TestCase):
    def test_prefers_session_id(self) -> None:
        self.assertEqual(
            _common.session_key({"session_id": "s1", "conversation_id": "c1"}, "fb"), "s1"
        )

    def test_accepts_camel_case(self) -> None:
        self.assertEqual(_common.session_key({"sessionId": "s2"}, "fb"), "s2")

    def test_thread_id(self) -> None:
        self.assertEqual(_common.session_key({"thread_id": "t1"}, "fb"), "t1")

    def test_falls_back(self) -> None:
        self.assertEqual(_common.session_key({}, "fallback"), "fallback")

    def test_truncated_to_64(self) -> None:
        self.assertEqual(len(_common.session_key({"session_id": "x" * 200}, "fb")), 64)


class TestHostContinued(unittest.TestCase):
    def test_stop_hook_active(self) -> None:
        self.assertTrue(_common.host_continued({"stop_hook_active": True}))

    def test_camel_case(self) -> None:
        self.assertTrue(_common.host_continued({"stopHookActive": True}))

    def test_loop_active(self) -> None:
        self.assertTrue(_common.host_continued({"loop_active": True}))

    def test_gemini_continue_false_means_not_continued(self) -> None:
        """Gemini's {"continue": false} means STOP. Reading it as "already
        continuing" would make the first nudge of a session never fire."""
        self.assertFalse(_common.host_continued({"continue": False}))

    def test_absent(self) -> None:
        self.assertFalse(_common.host_continued({}))


class TestHostLoop(unittest.TestCase):
    def test_count(self) -> None:
        self.assertEqual(_common.host_loop_count({"loop_count": 3}), 3)
        self.assertEqual(_common.host_loop_count({"loopCount": 4}), 4)

    def test_count_default(self) -> None:
        self.assertEqual(_common.host_loop_count({}), 0)

    def test_non_int_ignored(self) -> None:
        self.assertEqual(_common.host_loop_count({"loop_count": "3"}), 0)

    def test_exceeded(self) -> None:
        self.assertTrue(_common.host_loop_exceeded({"loop_count": 5}, 5))
        self.assertFalse(_common.host_loop_exceeded({"loop_count": 4}, 5))

    def test_zero_cap_disables_the_check(self) -> None:
        self.assertFalse(_common.host_loop_exceeded({"loop_count": 99}, 0))


class TestReadTranscript(unittest.TestCase):
    def test_reads_the_file(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write(json.dumps({"role": "assistant", "content": "hello"}))
            path = handle.name
        try:
            self.assertIn("hello", _common.read_transcript(path))
        finally:
            Path(path).unlink()

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(_common.read_transcript("/nonexistent/path.jsonl"), "")


class TestReadFirstTranscript(unittest.TestCase):
    """It returns the transcript TEXT from the first field that yields any."""

    def _write(self, text: str) -> str:
        import tempfile

        path = Path(tempfile.mkstemp(suffix=".jsonl")[1])
        self.addCleanup(path.unlink, True)
        path.write_text(json.dumps({"role": "assistant", "content": text}), encoding="utf-8")
        return str(path)

    def test_first_field_that_yields_text_wins(self) -> None:
        first = self._write("from first")
        second = self._write("from second")
        payload = {"transcript_path": first, "other_path": second}
        self.assertEqual(
            _common.read_first_transcript(payload, "transcript_path", "other_path"), "from first"
        )

    def test_falls_to_the_next_field_when_the_first_is_empty(self) -> None:
        second = self._write("from second")
        payload = {"transcript_path": "/nonexistent.jsonl", "other_path": second}
        self.assertEqual(
            _common.read_first_transcript(payload, "transcript_path", "other_path"), "from second"
        )

    def test_no_field_present(self) -> None:
        self.assertEqual(_common.read_first_transcript({}, "transcript_path"), "")

    def test_camel_case_spelling(self) -> None:
        path = self._write("camel")
        self.assertEqual(
            _common.read_first_transcript(
                {"transcriptPath": path}, "transcript_path", "transcriptPath"
            ),
            "camel",
        )


class TestReadPayload(unittest.TestCase):
    def test_reads_stdin(self) -> None:
        import io

        original = sys.stdin
        sys.stdin = io.StringIO(json.dumps({"session_id": "s"}))
        try:
            self.assertEqual(_common.read_payload(), {"session_id": "s"})
        finally:
            sys.stdin = original

    def test_invalid_stdin_is_empty(self) -> None:
        import io

        original = sys.stdin
        sys.stdin = io.StringIO("{not json")
        try:
            self.assertEqual(_common.read_payload(), {})
        finally:
            sys.stdin = original


class TestDisabled(unittest.TestCase):
    def test_env_switch(self) -> None:
        import os

        original = os.environ.get("HERMES_CONTINUE_HOOK")
        try:
            os.environ["HERMES_CONTINUE_HOOK"] = "0"
            self.assertTrue(_common.disabled())
            os.environ["HERMES_CONTINUE_HOOK"] = "1"
            self.assertFalse(_common.disabled())
        finally:
            if original is None:
                os.environ.pop("HERMES_CONTINUE_HOOK", None)
            else:
                os.environ["HERMES_CONTINUE_HOOK"] = original


if __name__ == "__main__":
    unittest.main()
