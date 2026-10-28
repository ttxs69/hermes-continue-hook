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

    def test_the_english_boundaries_are_as_wide_as_the_chinese_ones(self):
        """Every one of these is user-only: money, credentials, destruction,
        publishing, or sending something to a third party.

        They used to nudge, which told an English-speaking agent that asking
        for a subscription, deleting production logs or emailing a customer
        was its own call. The nudge text still asks the model to self-check,
        but the exclusion layer is what the README promises.
        """
        for text in (
            "This requires a paid subscription. Should I upgrade?",
            "Should I subscribe to the paid tier?",
            "Should I spend $200 on the ad campaign?",
            "Should I use the production API key?",
            "Can you give me the AWS secret access key?",
            "Do you want me to rotate the production credentials?",
            "Do you want me to revoke the API token?",
            "Should I delete the old logs?",
            "Should I overwrite the config file?",
            "Should I force-push to master?",
            "Should I delete the production database?",
            "Want me to publish the release notes?",
            "Should I deploy this to production?",
            "Want me to email the customer the invoice?",
            "Shall I send it to the client?",
            "Want me to forward this to the team?",
            "Should I push this to main?",
            "Should I merge the PR?",
        ):
            self.case(text, False)

    def test_english_triggers_are_case_insensitive(self):
        """`should i` is lowercase in the pattern, and "Should I…" starts the
        sentence — so the offer trigger never fired on the most common form and
        only the trailing "?" caught it."""
        got, reason = detect("Should I also update the changelog?", 0, self.CFG)
        self.assertTrue(got)
        self.assertNotEqual(reason, "trigger:question_mark",
                            "the offer trigger should match before the fallback")

    def test_a_normal_english_offer_still_nudges(self):
        for text in (
            "Want me to add the tests as well?",
            "Should I add a unit test for the edge case?",
            "Want me to refactor that helper?",
            "Should I rename this variable for clarity?",
        ):
            self.case(text, True)


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



    def test_url_with_query_string_is_not_a_question(self):
        """A "?" inside a link is not a permission-seeking ending."""
        cfg = default_config()
        for text in (
            "全部改好了。文档地址 https://example.com/docs?foo=bar",
            "见 [文档](https://example.com/x?y=1) 这份",
            "看这里 https://example.com/a?",
        ):
            should, reason = detect(text, 0, cfg)
            self.assertFalse(should, f"{text!r} should not trigger ({reason})")

    def test_question_after_a_url_still_triggers(self):
        """Stripping the URL must not swallow a real question after it."""
        cfg = default_config()
        should, reason = detect("文档 https://example.com/a?x=1 还需要我做什么？", 0, cfg)
        self.assertTrue(should)
        self.assertTrue(reason.startswith("trigger:"))


    def test_a_question_mark_in_code_is_not_a_question(self):
        """Ternaries and optional chaining are syntax, not questions."""
        cfg = default_config()
        for text in (
            "改好了：\n```js\nconst x = a ? b : c;\n```",
            "改成 `x ? y : z` 就行",
        ):
            should, reason = detect(text, 0, cfg)
            self.assertFalse(should, f"{text!r} should not trigger ({reason})")

    def test_english_boundaries_are_excluded(self):
        """The English exclusion list has to cover the same boundaries as the
        Chinese one — otherwise an English agent can talk itself into a
        payment, a deletion or a publish."""
        cfg = default_config()
        for text in (
            "This will cost $120 — I need your approval first.",
            "I need your API key to continue.",
            "Should I delete the branch and the old files?",
            "Should I publish this to main?",
        ):
            should, reason = detect(text, 0, cfg)
            self.assertFalse(should, f"{text!r} should not trigger ({reason})")

    def test_english_permission_seeking_still_triggers(self):
        cfg = default_config()
        should, reason = detect("Should I also update the README?", 0, cfg)
        self.assertTrue(should)
        self.assertTrue(reason.startswith("trigger:"))

if __name__ == "__main__":
    unittest.main()
