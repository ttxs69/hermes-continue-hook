"""Detector unit tests. Zero third-party deps; runs via `python tests/test_detector.py`
or `python -m unittest discover -s tests`."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detector import (
    DEFAULTS,
    ConfigStore,
    build_nudge,
    default_config,
    detect,
)


class TestPromise(unittest.TestCase):
    """陈述式承诺：「下一步我去 X」然后交回合。

    2026-10-29 实测事故：agent 在回复末尾写「下一步我去查 Replume 有没有定价页」，
    然后直接结束了回合 —— 四类原有触发（offer / hand_off / confirm_seek /
    question_mark）**一个都没命中**，因为它既没有问号、也没把决定推给用户，
    而是自己承认还有活，却把回合交了。这是 continue-hook 要防的事的**子集**，
    但原来完全漏检。
    """

    CFG = default_config()

    def case(self, text, expect, note=""):
        got, reason = detect(text, 0, self.CFG)
        self.assertEqual(got, expect, f"{note} {text!r} -> {reason}")
        return reason

    def test_the_actual_incident(self):
        """真实原句：含「付费证据」这个名词，不能让 boundary 放行。"""
        reason = self.case(
            "下一步我去查 Replume 有没有定价页和真实客户证言"
            "——那是该品类的付费证据，也是我唯一还能从这次撞车拿到的东西。",
            True,
            "the incident that motivated this class",
        )
        self.assertEqual(reason, "trigger:promise")

    def test_short_promise(self):
        self.case("下一步我去查 Replume 定价。", True)

    def test_colloquial_variants(self):
        for s in ("接下来我会验证这个假设。", "我这就去拉数据。", "下面我会先跑一遍测试。"):
            self.case(s, True, "colloquial promise")

    def test_english_promise(self):
        for s in (
            "Next I'll check their pricing page.",
            "I will run the verification now.",
            "Then I'll pull the numbers.",
        ):
            self.case(s, True, "english promise")

    def test_promise_beats_boundary_noun(self):
        """boundary 里的「付费」会被「付费证据」这个名词误命中 ⇒ promise 先判。"""
        self.case("下一步我去看它的付费数据。", True, "noun must not mask a promise")

    def test_promise_touching_a_hard_boundary_is_released(self):
        """⚠ 红线：承诺本身是**不可逆/对外**动作 ⇒ 必须放行，不能 nudge。

        2026-10-29 红队阻断项 S1：上一版让 promise 抢在 boundary 之前，
        于是「下一步我去付款开通」「下一步我去把生产库的旧表删掉」从 1.3.5 的
        代码级放行变成被 nudge。那份 nudge 文案的豁免写的是「需要用户独有的东西」
        （持有物判据），而 agent 手里有 DB 凭据、有 npm token 时这条为假 ——
        它完全可以顺势删库/发版。**用散文替代硬门 = 护栏回退。**
        """
        for s in (
            "下一步我要付款开通，需要你批准。",
            "下一步我去把生产库的旧表删掉。",
            "下一步我会发布到 npm。",
            "下一步我去部署到生产环境。",
            "Next step: I'll deploy to production and rotate the keys.",
            "I'll delete the stale snapshots now.",
        ):
            got, reason = detect(s, 0, self.CFG)
            self.assertFalse(got, f"hard boundary must be released, got {reason}: {s!r}")
            self.assertEqual(reason, "boundary", s)

    def test_real_blocker_is_not_a_promise(self):
        """真的需要用户提供东西 ⇒ 不 nudge（这里是 boundary/user_must 的活）。"""
        self.case("下一步需要你提供 API key，我没有凭据。", False, "genuine blocker")

    def test_narrative_next_step_does_not_fire(self):
        """正文里叙述「下一步是…」且已完成 ⇒ 不该拦。"""
        self.case("这个方案下一步是把它部署到 staging，我已经部署完了。", False)

    def test_finished_report_does_not_fire(self):
        self.case("本次排查完成：读了 41 条标题，分类统计如下。结论已写进 GOAL.md R296。", False)
        self.case("报告写完了。整个流程：collect → render → publish，三步都跑通了。", False)

    def test_promise_nudge_text_is_specific(self):
        """promise 有自己的文案：说明「你说了要做，现在就做」。"""
        txt = build_nudge(0, "trigger:promise")
        self.assertIn("promise about what you would do next", txt)
        self.assertIn("do it now", txt)
        # offer 的文案不能串到 promise 上
        self.assertNotIn("ended by asking the user a question", txt)


class TestPromiseFalsePositiveBudget(unittest.TestCase):
    """误伤预算：正常回复被退回给 agent 是这个包最贵的失败模式。

    2026-10-29 第一版 promise 触发在 33 条真实形态的回复上误伤了 6 条（18.2%），
    其中三条是同一个 bug（代码块里的 `# TODO:` 被当成承诺）、两条是
    `\bnext step\b` 没要求第一人称、一条是已兑现。差点就这样发出去。
    ⇒ 把「误伤率」本身写成断言，而不是靠每次手动试。
    """

    CFG: ClassVar[dict] = default_config()
    BUDGET: ClassVar[float] = 0.10  # >10% 不放行

    # 全部是「不该被拦」的真实回复形态（中英文、长短、代码、数据、引用、已完成）
    NEGATIVE: ClassVar[list[str]] = [
        "## Summary\nThe migration completed. 12 tables updated.\n\n## Next steps\n1. Monitor error rates\n2. Update the runbook",
        "Done. Next steps: review the diff and merge.",
        "The next step needs your decision on the pricing tier.",
        "Implemented all three items.\n\nTODO (for you):\n- approve the deploy\n- rotate the key",
        "下一步是把它部署到 staging。我已经部署完了，日志显示 healthy。",
        "下一步计划已经写进 design.md 第 3 节，本轮到此结束。",
        "这次充值花了 200 元，账单截图在附件里。",
        "v1.2.0 已经发布到 npm，registry 读回版本号一致。",
        "删除了 43 个过期快照，磁盘从 91% 降到 61%。",
        "统计结果：41 篇文章，阅读中位 36，分享率 7.2%，判断型 9 篇。",
        "改了 3 个文件：detector.py 新增 promise 触发，hookctl.py 透传 reason。325 测试全绿。",
        "结论：这个方向不做，因为竞品已经覆盖 5 项能力中的全部。",
        "文档里写着「下一步我们会上线 CLI」，那是作者的原话。",
        'The README says "Next I will add tests" - that is the author\'s roadmap.',
        "```python\n# TODO: refactor this\nx = 1\n```\n改完了，测试通过。",
        "```\n// TODO: handle null\n```\nFixed and verified.",
        "完整流程：采集 → 渲染 → 发布。三步都跑通了。",
        "我这就跑完了，输出 exit 0，用时 3.2 秒。",
        "你说「接下来我会先看数据」，我就按这个做了。",
        'The plan you wrote says "then I\'ll deploy". I only did the build.',
        "好，收到。",
        "跑完了，结果如上。",
        "Done - all 325 tests pass.",
        "下一步需要你决定用哪个方案，A 还是 B。",
        "下一步要 API key，你把 key 发我我就跑。",
        "本次排查完成。根因是 ruff 的 exclude 对显式文件路径不生效。已修 hook。",
        "Root cause found: the formatter skips the exclude list for explicit paths.",
        "步骤：1. 读数据 2. 分类 3. 写结论。三个都做完了。",
        "I checked the next release notes; nothing breaking.",
        "下一步当时是把目录复制过去，已经做完了。",
        "I will note that the run took 3 minutes. It did.",
        "我说过下一步去查定价，查完了：没有公开定价页。",
        # ── 以下来自独立红队的**预注册**语料（2026-10-29），作者自己的语料里没有这些形态 ──
        "Build green. My next step is blocked on the DNS record only you can add",
        "检查完了：注册流程下一步会跳到实名页，不是 bug",
        "剩下的是你的活。待办：1. 审批这个 PR",
        "遗留 TODO：把 3 个 flaky 测试改成 deterministic（已记进 issue #41）",
        "Shipped. Next, I'd suggest a different approach - that's your call",
        "All 41 classified. Next step: the taxonomy needs your sign-off",
        "待办：C406 / C407 交棒给下一轮",
        "下一步是等 CI 跑完，不过已经跑过了，全绿",
    ]

    def test_false_positive_rate_is_within_budget(self):
        fired = [(x, detect(x, 0, self.CFG)) for x in self.NEGATIVE]
        bad = [(x, r) for x, (got, r) in fired if got]
        rate = len(bad) / len(self.NEGATIVE)
        self.assertLess(
            rate,
            self.BUDGET,
            f"false-positive rate {rate:.1%} exceeds {self.BUDGET:.0%}; fired on: "
            + "; ".join(f"{r}::{x[:60]!r}" for x, r in bad),
        )

    def test_code_blocks_never_fire(self):
        """单独钉住那个 bug：代码块里的 TODO 不是承诺。"""
        for s in (
            "```python\n# TODO: refactor\n```\n改完了。",
            "```\n// TODO: handle null\n```\nFixed.",
            "```\n# 下一步我来改这里\n```\n已经改完了。",
        ):
            got, reason = detect(s, 0, self.CFG)
            self.assertFalse(got, f"{reason} fired on a code block: {s!r}")

    def test_promise_is_not_double_judged(self):
        """`promise` 不能在 _first_trigger_hit 里被原文 tail 再判一次。"""
        import inspect

        from detector import _first_trigger_hit

        src = inspect.getsource(_first_trigger_hit)
        self.assertNotIn(
            '"promise"',
            src,
            "promise must be judged only in detect(), on the code-stripped window",
        )

    def test_recall_of_punctuation_and_mixed_forms(self):
        """红队实测：12 条变体里原先漏 7 条 —— 标点与混排必须召回。"""
        for s in (
            "下一步：我去查定价页。",
            "下一步，我会验证这个假设。",
            "接下来，我会先跑测试。",
            "下一步 I'll check their pricing page.",
            "我稍后去查一下。",
            "下面我跑一下测试。",
            "Next up: I'll audit the logs.",
            "Next steps: I'll verify and report.",
        ):
            got, reason = detect(s, 0, self.CFG)
            self.assertTrue(got, f"missed a punctuation/mixed form: {s!r}")
            self.assertEqual(reason, "trigger:promise", s)

    def test_recall_is_intact(self):
        """收紧正则不能把真承诺漏掉。"""
        for s in (
            "下一步我去查 Replume 定价。",
            "接下来我会验证这个假设。",
            "我这就去拉数据。",
            "下面我会先跑一遍测试。",
            "Next I'll check their pricing page.",
            "I will run the verification now.",
            "My next step is to audit the logs.",
        ):
            got, reason = detect(s, 0, self.CFG)
            self.assertTrue(got, f"missed a real promise: {s!r}")
            self.assertEqual(reason, "trigger:promise")


class TestWiringCoverage(unittest.TestCase):
    """两类「改了配置但没生效」的漏，2026-10-29 红队各抓到一次，都没有测试能抓。

    S2: 只改了 6 个 shell adapter，`hookctl.py` / `__init__.py` / patch-mode
        三个主力入口还是旧的 `build_nudge(decision.attempt)` —— Hermes 插件
        和 patch-mode 会把 offer 的文案（"你刚才在问用户问题"）发给 promise 场景。
    S6: trigger 注册散在 3 处（遍历元组 + 两个独立分支），加第 6 个键会静默 no-op。
    """

    def test_every_build_nudge_call_passes_the_reason(self):
        """源码级：任何 build_nudge(...) 调用都必须传 reason。"""
        import re
        from pathlib import Path as _P

        root = _P(__file__).resolve().parent.parent
        offenders = []
        for f in list(root.rglob("*.py")) + list(root.rglob("*.js")):
            if any(x in f.parts for x in (".venv", "node_modules", "tests", "patch-mode")):
                continue
            src = f.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r"build_nudge\(([^)]*)\)", src):
                args = m.group(1)
                if (
                    "reason" not in args
                    and args.strip()
                    and "def" not in src[max(0, m.start() - 12) : m.start()]
                ):
                    offenders.append(f"{f.relative_to(root)}: build_nudge({args})")
        self.assertEqual(offenders, [], "build_nudge call without reason: " + "; ".join(offenders))

    def test_patch_mode_mirrors_the_reason_plumbing(self):
        from pathlib import Path as _P

        root = _P(__file__).resolve().parent.parent
        src = (root / "patch-mode" / "continue_hook.py").read_text(encoding="utf-8")
        self.assertIn(
            "build_nudge(decision.attempt, decision.reason)",
            src,
            "patch-mode must pass the reason, else it sends the offer text for promises",
        )

    def test_every_configured_trigger_is_dispatched(self):
        """配置里有的键，必须都在 DISPATCHED_TRIGGERS 里。"""
        from detector import DISPATCHED_TRIGGERS

        cfg = default_config()
        configured = set(cfg["triggers"])
        self.assertEqual(
            configured,
            set(DISPATCHED_TRIGGERS),
            f"triggers config and dispatch table disagree: only-in-config="
            f"{configured - set(DISPATCHED_TRIGGERS)}, only-in-dispatch="
            f"{set(DISPATCHED_TRIGGERS) - configured}",
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
        self.assertNotEqual(
            reason, "trigger:question_mark", "the offer trigger should match before the fallback"
        )

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
        report = (
            "我在任何会话以「要不要我…/你定/听你的/？…」收尾且不涉独占边界时，"
            "会被当场拦下。这条回复以句号结尾。"
        )
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


class TestBadConfigNeverBreaksATurn(unittest.TestCase):
    """A malformed value must fall back, not raise.

    ``cooldown_seconds: 5m`` used to raise inside the hook, which the shell
    adapters turned into a traceback and a non-zero exit — the opposite of the
    documented "degrades to no nudges".
    """

    def test_numeric_strings_and_garbage(self):
        cfg = dict(default_config())
        for value in ("two", "abc", None, True, [], {}):
            cfg["max_nudges_per_turn"] = value
            got, reason = detect("要不要我顺便补个测试？", 0, cfg)
            self.assertTrue(got, f"{value!r} should fall back to the default, got {reason}")

    def test_a_suffix_is_understood(self):
        cfg = dict(default_config())
        cfg["tail_chars"] = "100"
        got, _ = detect("要不要我顺便补个测试？", 0, cfg)
        self.assertTrue(got)

    def test_a_string_pattern_list_is_not_split_into_characters(self):
        # `"offer": "要不要我"` used to be iterated per character, so the
        # pattern matched almost any Chinese sentence.
        cfg = dict(default_config())
        cfg["triggers"] = {"offer": "要不要我"}
        self.assertFalse(detect("今天天气不错，我改完了。", 0, cfg)[0])
        self.assertTrue(detect("要不要我顺便补个测试？", 0, cfg)[0])


class TestMoneyIsAlwaysABoundary(unittest.TestCase):
    """The nudge must never carry the agent past spending money."""

    def test_chinese_and_english_money(self):
        cfg = default_config()
        for text in (
            "要不要我买这个订阅？",
            "要不要我付钱给他？",
            "要不要我把钱转给他？",
            "要不要我给他打钱？",
            "要不要我下单买下来？",
            "要不要我签合同？",
            "Should I pay the contractor?",
            "Should I wire the money?",
            "Should I transfer the funds?",
            "Should I hire the contractor?",
            "Should I sign the contract?",
        ):
            got, reason = detect(text, 0, cfg)
            self.assertFalse(got, f"{text} → {reason}")


class TestMoneyBoundariesDoNotEatRealWork(unittest.TestCase):
    """The money list must not turn ordinary engineering talk into a boundary.

    Widening it to bare pay/buy/transfer/wire/hire blocked 47% of realistic
    endings ("pay attention to the lint warnings", "transfer the helper into
    its own module").
    """

    def test_these_are_still_nudged(self):
        cfg = default_config()
        for text in (
            "Should I pay attention to the lint warnings?",
            "Should I transfer the helper into its own module?",
            "Should I contract the two helpers into one?",
            "Should I wire this callback to the store?",
            "Should I hire a stub for the test?",
            "Should I buy into the event-sourcing pattern?",
            "要不要我买账这个方案？",
        ):
            got, reason = detect(text, 0, cfg)
            self.assertTrue(got, f"{text} → {reason}")


class TestPublishingIsABoundary(unittest.TestCase):
    def test_publishing_and_shipping_outward(self):
        cfg = default_config()
        for text in (
            "要不要我发布到 npm？",
            "要不要我发布到 PyPI？",
            "要不要我推送到线上？",
            "要不要我导出客户数据？",
            "要不要我发到公众号？",
            "Should I publish it to npm?",
            "Should I deploy to production?",
            "Should I push to main?",
        ):
            got, reason = detect(text, 0, cfg)
            self.assertFalse(got, f"{text} → {reason}")


class TestQuotedAndCodeSpans(unittest.TestCase):
    """A quoted phrase is a mention, and code is not prose.

    Both rules existed, but each had a hole that made the reply look like a
    question: ASCII double quotes were not treated as quotes, and a fenced block
    whose closing ``` fell outside the 240-character window was not stripped.
    """

    def case(self, text, expect):
        got, reason = detect(text, 0, default_config())
        self.assertEqual(got, expect, f"{text!r} → {reason}")

    def test_ascii_double_quotes_are_a_mention(self):
        self.case('The log said "Should I continue?" three times.', False)
        self.case('He asked "要不要我顺便补个测试？" and I said no.', False)

    def test_a_real_question_still_counts(self):
        self.case("Should I continue?", True)
        self.case("要不要我顺便补个测试？", True)

    def test_a_code_block_that_ends_the_reply(self):
        self.case("```python\na = b ? c : d\n```", False)

    def test_a_code_block_longer_than_the_window(self):
        block = "\n".join(f"x{i} = a ? b : c" for i in range(40))
        self.case(f"```python\n{block}\n```", False)

    def test_an_unclosed_fence_is_still_code(self):
        self.case("```python\na = b ? c : d\n", False)

    def test_inline_code_is_not_prose(self):
        self.case("已改好 `a ? b : c` 的写法。", False)

    def test_a_question_outside_the_code_still_counts(self):
        self.case("要不要我顺手也改一下 `a ? b : c`？", True)


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
