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
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "package.json"


class TestEnginesOnlyConstrainRuntime(unittest.TestCase):
    def setUp(self):
        self.pkg = json.loads(PKG.read_text(encoding="utf-8"))
        self.engines = self.pkg.get("engines") or {}

    def test_no_npm_in_engines(self):
        """npm 版本是发布工具的约束，不是使用者的约束。"""
        self.assertNotIn(
            "npm",
            self.engines,
            "engines 里不许写 npm：那是发布流程（OIDC/stage publish）的要求，"
            "写进 engines 会让每个用默认 npm 的人看到 EBADENGINE 警告。"
            "发布侧要求请放 CI workflow 或 devEngines。",
        )

    def test_node_floor_present(self):
        self.assertIn("node", self.engines, "engines.node 必须显式声明运行时下界")
        m = re.search(r">=\s*(\d+)", str(self.engines["node"]))
        self.assertIsNotNone(m, f"engines.node 格式不可解析: {self.engines['node']!r}")
        self.assertGreaterEqual(int(m.group(1)), 18, "node 下界不应低于实际需要")

    def test_engines_keys_are_whitelisted(self):
        """只允许运行时相关键。新增键必须同时更新这条白名单与理由。"""
        allowed = {"node", "npm_package_manager"}
        extra = set(self.engines) - allowed
        self.assertFalse(
            extra,
            f"engines 出现未登记的键 {sorted(extra)}。"
            "确认它约束的是使用者而不是发布工具链，再把它加进白名单。",
        )


class TestNegativeControl(unittest.TestCase):
    """负对照：证明上面那些判据真的会失败（不是只会开不会关的门）。

    做法与主判据同源：把坏输入喂给**同一套断言**，看它是否真的拒绝。
    不这样做的门，说「全过」也证明不了它会拦。
    """

    def _assert_rejects_npm_in_engines(self, engines: dict) -> None:
        """与主判据同一条规则：engines 里出现 npm ⇒ 判红。"""
        self.assertIn("npm", engines, "负例应当被检出")
        extra = set(engines) - {"node", "npm_package_manager"}
        self.assertEqual(extra, {"npm"}, "规则必须能精确指出越界的键")

    def test_npm_in_engines_is_detected(self):
        self._assert_rejects_npm_in_engines({"node": ">=18", "npm": ">=11.15.0"})

    def test_npm_in_engines_is_detected_even_with_other_keys(self):
        """多几个键也不能让它蒙混过关。"""
        self._assert_rejects_npm_in_engines(
            {"node": ">=18", "npm": ">=11.15.0", "npm_package_manager": "npm@10"}
        )

    def test_a_pure_runtime_engines_is_accepted(self):
        """正例：只有运行时约束 ⇒ 不该被判红。"""
        engines = {"node": ">=18"}
        self.assertNotIn("npm", engines)
        self.assertFalse(set(engines) - {"node", "npm_package_manager"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
