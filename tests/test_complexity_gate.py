"""The complexity gates are only worth something if they are themselves checked.

Two things can go wrong and neither is visible in a green build:

  * the gate is configured with a threshold so high that nothing is ever
    flagged (a limit of 100 is a limit nobody fails);
  * the gate is switched off in a config that nobody reads.

So the configuration is asserted here, and so is the *effect*: a function that
is deliberately too complex has to be reported.

The reference implementations are deliberately ugly — that is the point. They
are never imported by anything else.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
ESLINT_CONFIG = ROOT / "eslint.config.mjs"


def _ruff() -> str:
    """The ruff binary: the one on PATH, or the one in the dev tools dir."""
    from shutil import which

    found = which("ruff")
    if found:
        return found
    for candidate in ("/tmp/devtools/bin/ruff", str(ROOT / ".venv/bin/ruff")):
        if Path(candidate).exists():
            return candidate
    raise unittest.SkipTest("ruff is not installed")


class TestThePythonComplexityGate(unittest.TestCase):
    """ruff's C901, configured in pyproject.toml."""

    def setUp(self) -> None:
        self.config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        self.lint = self.config["tool"]["ruff"]["lint"]

    def test_the_mccabe_rule_is_enabled(self) -> None:
        self.assertIn("C90", self.lint["select"])

    def test_the_ceiling_is_a_number_and_not_a_no_op(self) -> None:
        limit = self.lint["mccabe"]["max-complexity"]
        self.assertIsInstance(limit, int)
        # A threshold nobody can fail is not a gate. Ten is the value this
        # repository measured its own code against.
        self.assertLessEqual(limit, 10)
        self.assertGreaterEqual(limit, 5)

    def test_a_function_over_the_ceiling_is_actually_reported(self) -> None:
        """A deliberately complex function must be flagged."""
        source = ROOT / "tests" / "_complexity_probe.py"
        source.write_text(
            "\n".join(
                [
                    "def probe(value):",
                    *[
                        line
                        for i in range(1, 16)
                        for line in (f"    if value == {i}:", f"        return {i}")
                    ],
                    "    return 0",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [
                    _ruff(),
                    "check",
                    "--select",
                    "C901",
                    "--config",
                    f"lint.mccabe.max-complexity={self.lint['mccabe']['max-complexity']}",
                    "--output-format",
                    "json",
                    str(source),
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            findings = json.loads(result.stdout or "[]")
            self.assertTrue(
                any(f.get("code") == "C901" for f in findings),
                f"a 10-branch function was not reported: {result.stdout}",
            )
        finally:
            source.unlink(missing_ok=True)

    def test_the_shipped_code_is_under_the_ceiling(self) -> None:
        """The real check, on the real files."""
        result = subprocess.run(
            [_ruff(), "check", "--select", "C901", "--output-format", "concise", "."],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(
            result.returncode,
            0,
            "a shipped function is over the complexity ceiling:\n" + result.stdout,
        )


class TestTheTypeScriptComplexityGate(unittest.TestCase):
    """eslint's `complexity` rule, configured in eslint.config.mjs.

    The rule needs eslint, which lives in node_modules. A job that runs only
    the Python suite has no node_modules, and this is not a Python check —
    so the class skips itself there rather than failing a job it does not
    belong to. The JS job (vitest + coverage) and the eslint job both run it.
    """

    def setUp(self) -> None:
        if not (ROOT / "node_modules" / "eslint").exists():
            self.skipTest("eslint is not installed (node_modules missing)")
        self.config = ESLINT_CONFIG.read_text(encoding="utf-8")

    def test_the_rule_is_enabled_with_a_ceiling(self) -> None:
        self.assertIn("complexity", self.config)
        self.assertIn("10", self.config)

    def test_the_ceiling_is_not_a_no_op(self) -> None:
        """The threshold must be low enough to flag something."""
        import re

        match = re.search(r'complexity:\s*\["error",\s*(\d+)\]', self.config)
        self.assertIsNotNone(match, "no numeric ceiling found in eslint.config.mjs")
        limit = int(match.group(1))
        self.assertLessEqual(limit, 10)
        self.assertGreaterEqual(limit, 5)

    def test_a_function_over_the_ceiling_is_actually_reported(self) -> None:
        """A deliberately complex function must be flagged.

        The probe lives under `bin/` because that is where the rule applies —
        the ceiling is on shipped code, not on the test suite.
        """
        probe = ROOT / "bin" / "_complexity_probe.js"
        probe.write_text(
            "\n".join(
                [
                    "export function probe(value) {",
                    *[f"  if (value === {i}) return {i};" for i in range(1, 13)],
                    "  return 0;",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [
                    "npx",
                    "--no-install",
                    "eslint",
                    str(probe),
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            try:
                findings = json.loads(result.stdout or "[]")
            except json.JSONDecodeError:
                self.fail(f"eslint produced no JSON: {result.stdout}\n{result.stderr}")
            messages = [
                m
                for f in findings
                for m in f.get("messages", [])
                if m.get("ruleId") == "complexity"
            ]
            self.assertTrue(
                messages,
                f"a 12-branch function was not reported: {result.stdout}",
            )
        finally:
            probe.unlink(missing_ok=True)

    def test_the_shipped_code_is_under_the_ceiling(self) -> None:
        """The real check, on the real files."""
        result = subprocess.run(
            ["npx", "--no-install", "eslint", ".", "--format", "json"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(
            result.returncode,
            0,
            "a shipped function is over the complexity ceiling:\n" + result.stdout,
        )


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
