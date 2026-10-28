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

    def test_it_loads_and_nudges(self):
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
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
            module = self._load(home)

            class Agent:
                session_id = "s2"
                platform = "cli"
                _continue_hook_nudges = 0

            self.assertIsNone(module.continue_hook_nudge(Agent(), "已完成，42 项测试全绿。"))

    def test_it_has_no_private_paths(self):
        """The old copy wrote into ~/moneymaker/state/… — not a public path."""
        source = GENERATED.read_text(encoding="utf-8")
        self.assertNotIn("moneymaker", source)
        self.assertNotIn("/home/", source)


if __name__ == "__main__":
    unittest.main()
