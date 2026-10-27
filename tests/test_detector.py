"""Detector unit tests. Zero third-party deps; runs via `python tests/test_detector.py`
or `python -m unittest discover -s tests`."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detector import (
    DEFAULTS,
    ConfigStore,
    build_nudge,
    default_config,
    detect,
)


class TestDetectZh(unittest.TestCase):
    CFG = default_config()

    def case(self, text, expect, note=""):
        got, reason = detect(text, 0, self.CFG)
        self.assertEqual(got, expect, f"{note} {text!r} -> {reason}")

    def test_offer_with_question_mark(self):
        self.case("这套要不要我沉淀进 skill？说一声就写。", True, "the motivating incident")

    def test_bare_question_mark(self):
        self.case("这个方案可以吗？", True)

    def test_info_question_still_fires_agent_decides(self):
        self.case("你要用哪个邮箱注册，给我一个？", True)

    def test_boundary_money(self):
        self.case("这会花 ¥69，需要你批准后我才买。", False)

    def test_boundary_identity(self):
        self.case("卡在实名认证，需要你先完成。", False)

    def test_declarative_done(self):
        self.case("已完成，验证通过。", False)

    def test_normal_report(self):
        self.case("交付：A 已上线，B 待数据。剩余风险：C。", False)

    def test_hand_off_without_mark(self):
        self.case("听你的，我停在这。", True)

    def test_hand_off_wait_approval(self):
        self.case("等你拍板。", True)


class TestDetectEn(unittest.TestCase):
    CFG = default_config()

    def case(self, text, expect):
        got, _ = detect(text, 0, self.CFG)
        self.assertEqual(got, expect, text)

    def test_should_i(self):
        self.case("Should I go ahead and refactor this?", True)

    def test_want_me_to(self):
        self.case("I can write the README for you, want me to?", True)

    def test_boundary_cost(self):
        self.case("This will cost $69 — do you approve?", False)

    def test_boundary_publish(self):
        self.case("Should I go ahead and publish this?", False)

    def test_done(self):
        self.case("Done. All tests pass.", False)


class TestQuotedMentions(unittest.TestCase):
    """Regression: quoting the trigger patterns in a report must not fire
    (found in live dogfooding — a status report quoted them and got nudged)."""

    CFG = default_config()

    def test_quoted_patterns_report(self):
        report = ("我在任何会话以「要不要我…/你定/听你的/？…」收尾且不涉独占边界时，"
                  "会被当场拦下。这条回复以句号结尾。")
        got, reason = detect(report, 0, self.CFG)
        self.assertFalse(got, reason)

    def test_real_ask_still_fires_after_quote(self):
        got, reason = detect("先不提「要不要我」这句话了。要不要我继续？", 0, self.CFG)
        self.assertTrue(got, reason)


class TestGuards(unittest.TestCase):
    CFG = default_config()

    def test_max_nudges(self):
        got, reason = detect("要不要我继续吗？", attempt=2, cfg=self.CFG)
        self.assertFalse(got)
        self.assertEqual(reason, "max_nudges")

    def test_empty(self):
        got, reason = detect("   ", 0, self.CFG)
        self.assertFalse(got)
        self.assertEqual(reason, "empty")


class TestConfigStore(unittest.TestCase):
    def test_defaults_when_missing(self):
        cs = ConfigStore(Path("/nonexistent/continue_hook.json"))
        self.assertEqual(cs.load(), DEFAULTS)

    def test_bad_json_falls_back(self):
        p = Path("/tmp/ch_bad.json")
        p.write_text("{not json", encoding="utf-8")
        self.assertEqual(ConfigStore(p).load(), DEFAULTS)

    def test_override_and_hot_reload(self):
        import time
        p = Path("/tmp/ch_hot.json")
        p.write_text('{"triggers": {"question_mark": false}}', encoding="utf-8")
        cs = ConfigStore(p)
        cfg = cs.load()
        self.assertFalse(cfg["triggers"]["question_mark"])
        got, _ = detect("这个方案可以吗？", 0, cfg)
        self.assertFalse(got)
        # hot reload on mtime change
        time.sleep(0.02)
        p.write_text('{"triggers": {"question_mark": true}}', encoding="utf-8")
        got, _ = detect("这个方案可以吗？", 0, cs.load())
        self.assertTrue(got)

    def test_dry_run_flag(self):
        p = Path("/tmp/ch_dry.json")
        p.write_text('{"dry_run": true}', encoding="utf-8")
        self.assertTrue(ConfigStore(p).load()["dry_run"])


class TestNudge(unittest.TestCase):
    def test_attempt_numbering(self):
        self.assertIn("#1", build_nudge(0))
        self.assertIn("#2", build_nudge(1))


if __name__ == "__main__":
    unittest.main()
