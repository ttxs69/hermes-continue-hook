"""Patch mode must stay in step with the shared engine.

Patch mode drops a single self-contained file into the Hermes core tree, so
that file cannot import the engine — it has to *contain* it. The first
version was hand-maintained and became a second implementation with its own
patterns, its own key names and a hardcoded private log path.

It is generated now. This test fails when the generated file drifts from the
engine, which is exactly the failure mode we are guarding against.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

REPO = Path(__file__).resolve().parent.parent
GENERATED = REPO / "patch-mode" / "continue_hook.py"
BUILDER = REPO / "scripts" / "build-patch-mode.py"


class TestPatchModeIsGenerated(unittest.TestCase):
    def test_generated_file_is_up_to_date(self):
        result = subprocess.run(
            [sys.executable, str(BUILDER), "--check"], capture_output=True, text=True, cwd=REPO
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_generator_is_wired_into_the_checks(self):
        """The check script and CI must both run the generator's check."""
        check = (REPO / "scripts" / "check.sh").read_text(encoding="utf-8")
        self.assertIn("build-patch-mode.py", check)


class TestPatchModeStandalone(unittest.TestCase):
    """The generated file must work on its own, outside this repository."""

    def _load(self, home: str):
        spec = importlib.util.spec_from_file_location("continue_hook", GENERATED)
        module = importlib.util.module_from_spec(spec)
        # Deliberately NOT registered in sys.modules — that is how the core
        # imports it, and it is what a @dataclass in the file would break.
        spec.loader.exec_module(module)
        return module

    def _fake_hermes_config(self, cfg: dict) -> None:
        """Pin what the core "config" says, so the test does not depend on the
        machine it runs on (a dev box has a real Hermes config; CI has none)."""
        import types

        pkg = types.ModuleType("hermes_cli")
        pkg.__path__ = []  # type: ignore[attr-defined]
        cfgmod = types.ModuleType("hermes_cli.config")
        cfgmod.load_config = lambda: cfg  # type: ignore[attr-defined]
        saved = {k: sys.modules.get(k) for k in ("hermes_cli", "hermes_cli.config")}
        sys.modules["hermes_cli"] = pkg
        sys.modules["hermes_cli.config"] = cfgmod

        def restore():
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.addCleanup(restore)

    def test_it_loads_and_nudges(self):
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
            # The gate is off unless it is switched on, and this test used to
            # pass locally only because the dev machine's Hermes config had it
            # enabled — CI has no hermes_cli at all, so it failed there.
            self._fake_hermes_config({"agent": {"continue_hook_enabled": True}})
            module = self._load(home)

            class Agent:
                session_id = "s1"
                platform = "cli"
                _continue_hook_nudges = 0

            nudge = module.continue_hook_nudge(Agent(), "要不要我顺便也把示例补上？")
            self.assertIsNotNone(nudge)
            self.assertIn("continue-hook", nudge)

    def test_it_stays_silent_on_a_clean_reply(self):
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
            self._fake_hermes_config({"agent": {"continue_hook_enabled": True}})
            module = self._load(home)

            class Agent:
                session_id = "s2"
                platform = "cli"
                _continue_hook_nudges = 0

            self.assertIsNone(module.continue_hook_nudge(Agent(), "已完成，42 项测试全绿。"))

    def test_a_clean_environment_nudges_nothing(self):
        """Installing the patch must not start nudging by itself."""
        self._fake_hermes_config({})
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
            os.environ.pop("HERMES_CONTINUE_HOOK", None)
            module = self._load(home)

            class Agent:
                session_id = "s3"
                platform = "cli"
                _continue_hook_nudges = 0

            self.assertIsNone(module.continue_hook_nudge(Agent(), "要不要我顺便也把示例补上？"))

    def test_it_has_no_private_paths(self):
        """The old copy wrote into ~/moneymaker/state/… — not a public path."""
        source = GENERATED.read_text(encoding="utf-8")
        self.assertNotIn("moneymaker", source)
        self.assertNotIn("/home/", source)


class TestTheGateIsOffUntilAsked(unittest.TestCase):
    """Installing the patch must not start nudging turns by itself.

    The switch is `agent.continue_hook_enabled` and it defaults to off; the old
    fallback read the shared config's `enabled` (default true), so the
    documented switch did nothing and the gate ran the moment it was installed.
    """

    def _enabled(self, hermes_cfg, env=None):
        code = f"""
import sys, types
pkg = types.ModuleType("hermes_cli"); pkg.__path__ = []
cfg = types.ModuleType("hermes_cli.config")
cfg.load_config = lambda: {hermes_cfg!r}
sys.modules["hermes_cli"] = pkg
sys.modules["hermes_cli.config"] = cfg
sys.path.insert(0, {str(self.root)!r})
import continue_hook
from typing import ClassVar
print(continue_hook.continue_hook_enabled())
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": self.home, **(env or {})},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip() == "True"

    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / "patch-mode"
        self._td = tempfile.TemporaryDirectory()
        self.home = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def test_no_configuration_means_off(self):
        self.assertFalse(self._enabled({}))

    def test_the_documented_switch_is_authoritative(self):
        self.assertTrue(self._enabled({"agent": {"continue_hook_enabled": True}}))
        self.assertFalse(self._enabled({"agent": {"continue_hook_enabled": False}}))

    def test_the_environment_can_opt_in_when_nothing_is_configured(self):
        self.assertTrue(self._enabled({}, {"HERMES_CONTINUE_HOOK": "1"}))

    def test_a_configured_off_beats_the_environment(self):
        self.assertFalse(
            self._enabled(
                {"agent": {"continue_hook_enabled": False}}, {"HERMES_CONTINUE_HOOK": "1"}
            )
        )


class TestThePatchCarriesNoCopyOfTheEngine(unittest.TestCase):
    """The patch must not embed a second copy of the generated module.

    It used to. `core-gate.patch` was maintained by hand while
    `patch-mode/continue_hook.py` was generated from the shared engine, and
    the two drifted apart: the patch carried a detector without
    `strip_code_and_links` — the fix for a reply that ends in a code block —
    and with an older trigger list. Patch mode was then running different
    detection rules from plugin mode on the same machine.

    The generator was introduced to prevent exactly that, and it only
    prevented half of it, because nothing checked the copy inside the patch.
    These tests close the remaining half.
    """

    def test_the_patch_does_not_embed_the_generated_file(self):
        patch = (REPO / "patch-mode" / "core-gate.patch").read_text(encoding="utf-8")
        self.assertNotIn(
            "diff --git a/agent/continue_hook.py",
            patch,
            "the patch carries its own copy of the generated module — it will "
            "drift from the engine again",
        )

    def test_the_installer_copies_the_generated_file(self):
        installer = (REPO / "patch-mode" / "install.sh").read_text(encoding="utf-8")
        self.assertIn(
            'cp "$HERE/continue_hook.py"',
            installer,
            "the installer must copy the generated module into the core tree",
        )

    def test_the_patch_only_touches_the_core_gate_files(self):
        patch = (REPO / "patch-mode" / "core-gate.patch").read_text(encoding="utf-8")
        files = {
            line.split(" b/")[-1].strip()
            for line in patch.splitlines()
            if line.startswith("diff --git")
        }
        self.assertEqual(
            files,
            {
                "agent/session_persistence.py",
                "agent/turn_finalizer.py",
                "agent/turn_stop_gates.py",
            },
            f"the patch touches unexpected files: {files}",
        )


class TestPatchModeDetectsExactlyLikeTheRepo(unittest.TestCase):
    """The generated module must behave like the shared engine.

    The copy in the patch had drifted: it was missing `strip_code_and_links`
    (so a reply ending in a code block was treated as a question) and it
    carried an older trigger list. Patch mode and plugin mode then disagreed
    on the same machine.

    Comparing the two modules on real sentences is the check that would have
    caught it — the file comparison did not, because both were "up to date"
    with respect to their own generator.
    """

    CASES: ClassVar[list[str]] = [
        "好的，改完了。要不要我顺便补上示例？",
        "已完成。验证：lint 通过。",
        "这一步要调用付费 API，需要你批准并提供 key。要我继续吗？",
        "看这段代码：\n```python\nx = 1  # 这个行尾有问号？\n```",
        "Would you like me to also update the CHANGELOG?",
        "报告里说「要不要我继续？」——这是引用不是提问",
        "顺手把 CHANGELOG 也更新了。",
    ]

    def _load(self, path, name):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_two_agree_on_every_sentence(self):
        core = self._load(GENERATED, "generated_continue_hook")
        repo = self._load(REPO / "detector.py", "repo_detector")
        for text in self.CASES:
            with self.subTest(text=text[:30]):
                self.assertEqual(
                    core.detect(text, 0, core.default_config()),
                    repo.detect(text, 0, repo.default_config()),
                    "the generated module and the shared engine disagree",
                )


if __name__ == "__main__":
    unittest.main()
