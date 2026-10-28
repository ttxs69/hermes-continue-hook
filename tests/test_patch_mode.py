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

REPO = Path(__file__).resolve().parent.parent
GENERATED = REPO / "patch-mode" / "continue_hook.py"
BUILDER = REPO / "scripts" / "build-patch-mode.py"


class TestPatchModeIsGenerated(unittest.TestCase):
    def test_generated_file_is_up_to_date(self):
        result = subprocess.run([sys.executable, str(BUILDER), "--check"],
                                capture_output=True, text=True, cwd=REPO)
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)

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
print(continue_hook.continue_hook_enabled())
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True,
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
            self._enabled({"agent": {"continue_hook_enabled": False}}, {"HERMES_CONTINUE_HOOK": "1"})
        )


if __name__ == "__main__":
    unittest.main()
