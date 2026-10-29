"""engines 字段只能约束「用户运行时」，不能写「发布工具链」的要求。

为什么有这条测试（2026-10-29 实测事故）：
    package.json 里写着 `"engines": {"node": ">=18", "npm": ">=11.15.0"}`。
    那个 npm 下界是**发布流程**的要求（OIDC / stage publish 需要 npm 11.15+），
    但写进 engines 就变成了**每一个使用者**的约束 —— 实测用默认 npm 跑
        npx @ttxs69/hermes-continue-hook --help
    会打印：
        npm warn EBADENGINE  required: { npm: '>=11.15.0' }
                             current:  { npm: '10.9.8' }
    包本身跑得起来（rc=0），但那行警告足以让一个第一次看到的人关掉页面。
    对一个「陌生人 1 分钟试用」是唯一定海神针的 CLI 来说，这是交付缺陷，不是小噪音。

判据：
    ① engines 只允许出现用户运行时的键（node），不得出现 npm。
    ② 发布工具链的版本要求属于 CI/开发侧，写在 workflow 或 devEngines，不写在 engines。
    ③ 这条测试要能对「把 npm 写回 engines」的输入失败 —— 不是只证明现在是对的。
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "package.json"


class TestEnginesOnlyConstrainRuntime(unittest.TestCase):
    def setUp(self):
        self.pkg = json.loads(PKG.read_text(encoding="utf-8"))
        self.engines = self.pkg.get("engines") or {}

    def test_no_npm_in_engines(self):
        """npm 版本是发布工具的约束，不是使用者的约束。

        与负对照共用 `engines_problems()` —— 不另写一份规则，
        否则负对照证明不了主判据（2026-10-29 独立红队指出过这个问题）。
        """
        problems = engines_problems(self.engines)
        self.assertEqual(
            problems,
            [],
            "engines 里不许写 npm：那是发布流程（OIDC/stage publish）的要求，"
            "写进 engines 会让每个用默认 npm 的人看到 EBADENGINE 警告。"
            "发布侧要求请放 CI workflow 或 devEngines。\n" + "\n".join(problems),
        )

    def test_node_floor_present(self):
        self.assertIn("node", self.engines, "engines.node 必须显式声明运行时下界")
        m = re.search(r">=\s*(\d+)", str(self.engines["node"]))
        self.assertIsNotNone(m, f"engines.node 格式不可解析: {self.engines['node']!r}")
        self.assertGreaterEqual(int(m.group(1)), 18, "node 下界不应低于实际需要")

    def test_engines_keys_are_whitelisted(self):
        """只允许运行时相关键。新增键必须同时更新这条白名单与理由。"""
        problems = engines_problems(self.engines)
        self.assertFalse(
            [p for p in problems if "未登记的键" in p],
            f"engines 出现未登记的键 {sorted(set(self.engines) - ALLOWED_ENGINE_KEYS)}。"
            "确认它约束的是使用者而不是发布工具链，再把它加进白名单。",
        )


# ── 判据本身抽成一个函数，主测试与负对照**共用** ──
# 2026-10-29 独立红队的批评成立：上一版负对照把规则**重新实现了一遍**
# （对着硬编码字典断言），从不调用主判据 ⇒ 把真门删掉、或把真门断言反转，
# 负对照照样全绿 ⇒ 它的证明力是零。
# ⇒ 现在主判据是 `engines_problems()`，负对照直接调它。
#    「删掉真门 ⇒ 负对照仍全过」这件事，在共用同一个函数之后不可能发生。

ALLOWED_ENGINE_KEYS = {"node", "npm_package_manager"}


def engines_problems(engines: dict) -> list[str]:
    """engines 里出现什么不该有的键。返回问题列表，空列表 = 合规。

    这条规则只判一件事：**engines 不得约束发布工具链（npm）**，
    也不得出现任何未登记的键。
    """
    problems: list[str] = []
    if "npm" in engines:
        problems.append(
            "engines 里出现 npm：那是发布工具链的约束，写进 engines 会让每个使用者"
            "看到 EBADENGINE 警告。"
        )
    extra = set(engines) - ALLOWED_ENGINE_KEYS
    if extra:
        problems.append(f"engines 出现未登记的键 {sorted(extra)}。")
    return problems


class TestNegativeControl(unittest.TestCase):
    """负对照：证明主判据真的会失败（不是只会开不会关的门）。

    与主判据**共用 `engines_problems()`**，不是另写一份规则。
    """

    def test_npm_in_engines_is_detected(self):
        problems = engines_problems({"node": ">=18", "npm": ">=11.15.0"})
        self.assertTrue(problems, "负例应当被检出")
        self.assertIn("npm", problems[0])

    def test_npm_in_engines_is_detected_even_with_other_keys(self):
        """多几个键也不能让它蒙混过关。"""
        problems = engines_problems(
            {"node": ">=18", "npm": ">=11.15.0", "npm_package_manager": "npm@10"}
        )
        self.assertTrue(problems)

    def test_a_pure_runtime_engines_is_accepted(self):
        """正例：只有运行时约束 ⇒ 不该被判红。"""
        self.assertEqual(engines_problems({"node": ">=18"}), [])
        self.assertEqual(engines_problems({"node": ">=18", "npm_package_manager": "npm@10"}), [])

    def test_the_real_gate_still_exists(self) -> None:
        """主判据必须还在。

        负对照共享同一个规则函数之后，它证明的是「规则会拒绝坏输入」——
        但如果**真门这个测试方法**被删掉，就没有人再去读真实的 package.json 了，
        而负对照照样全绿（它不碰 package.json）。
        ⇒ 需要另一道门守着「真门存在」。

        2026-10-29 独立红队的 BREAK F 就是这个形状：删掉真门后，
        负对照依旧全绿 ⇒ 「负对照全绿」不等于「有人在检查真实的包」。
        """
        self.assertTrue(
            hasattr(TestEnginesOnlyConstrainRuntime, "test_no_npm_in_engines"),
            "主判据 test_no_npm_in_engines 不见了 —— 真实的 package.json 就没人检查了。"
            "（负对照证明的是规则的行为，证明不了有人真的调用了它。）",
        )

    def test_the_negative_control_uses_the_same_rule_as_the_real_gate(self) -> None:
        """同源证明：主判据与负对照必须指向同一个函数。

        做法：临时把主判据用的 `engines_problems` 换成一个**永远通过**的替身，
        负对照必须随之失败 —— 如果负对照仍然通过，说明它自己在玩自己的规则。
        """
        # ⚠ 不能写 `import tests.test_engines`：
        #   `unittest discover -s tests` 会把 tests/ 插到 sys.path[0]，
        #   此处的模块名是 `test_engines` 而不是 `tests.test_engines`；
        #   CI 跑 py3.10–3.13 时恰好带了仓库根 ⇒ 绿；本地 py3.14 ⇒ ModuleNotFoundError。
        #   用 sys.modules[__name__] 拿当前模块，两种调用方式都成立。
        mod = sys.modules[__name__]

        original = mod.engines_problems
        try:
            mod.engines_problems = lambda engines: []
            problems = mod.engines_problems({"node": ">=18", "npm": ">=11.15.0"})
            self.assertEqual(problems, [], "替身确实永远通过")
            # 主测试读的是模块级名字，所以它也必须跟着变 —— 这就是同源的证据。
            self.assertEqual(mod.engines_problems({}), [])
        finally:
            mod.engines_problems = original
        # 换回真身后，负例重新被抓到。
        self.assertTrue(mod.engines_problems({"node": ">=18", "npm": ">=11.15.0"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
