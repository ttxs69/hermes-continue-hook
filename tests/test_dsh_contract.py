"""Contract tests for the DeepSeek Harness (dsh) plugin.

The dsh plugin is TypeScript and dsh itself is a developer-preview harness, so
its runtime is not available in CI. What is verified here:

  1. the plugin uses dsh's **documented** turn boundary and continuation API
     (`agent/turn-stopping` → `agent.steer(createUserMessage(...))`), so a
     refactor that breaks the contract fails loudly instead of silently
     no-oping inside a live harness;
  2. the packaging matches dsh's documented plugin layout (bundle manifest,
     cordis patch file, tsconfig);
  3. the shared detector CLI — the plugin's only decision input — behaves.

The authoritative check for API drift is `adapters/dsh/typecheck.sh`, which
compiles the plugin against the real `@deepseek-ai/dsh-*` packages.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DSH = REPO / "adapters" / "dsh"
PLUGIN = DSH / "src" / "index.ts"


class TestDshPluginContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = PLUGIN.read_text(encoding="utf-8")

    def test_plugin_files_exist(self):
        for name in ("src/index.ts", "package.json", "cordis.patch.yml", "tsconfig.json"):
            self.assertTrue((DSH / name).exists(), name)

    def test_exports_cordis_plugin_surface(self):
        """A dsh plugin exports name / inject / apply."""
        self.assertRegex(self.src, r"export const name = 'continue-hook'")
        self.assertRegex(self.src, r"export (?:function|const) apply")
        self.assertRegex(self.src, r"export const inject")

    def test_hooks_the_turn_stopping_event(self):
        """agent/turn-stopping is dsh's documented turn-close boundary."""
        self.assertIn("ctx.on('agent/turn-stopping'", self.src)

    def test_steers_to_continue(self):
        """Steering is how a listener makes the machine run one more step."""
        self.assertIn("agent.steer(", self.src)
        self.assertIn("createUserMessage(", self.src)

    def test_reads_the_reply_from_the_session(self):
        """The reply comes from the session's derived history."""
        self.assertIn("agent.session.deriveMessages()", self.src)

    def test_delegates_detection_to_shared_detector(self):
        self.assertIn("detector.py", self.src)
        self.assertIn("--text", self.src)

    def test_delegates_the_caps_to_the_shared_service(self):
        """The caps live in hookctl.py — one implementation for every host.

        The host plugin must not re-implement policy, or a cap would mean two
        different things in two hosts.
        """
        self.assertIn("hookctl.py", self.src)
        self.assertIn("--nudge-text", self.src)
        self.assertNotIn("max_nudges_per_turn", self.src)

    def test_fails_open(self):
        self.assertRegex(self.src, r"catch\s*\{[\s\S]{0,120}fail open|fail open[\s\S]{0,200}catch")

    def test_plugin_source_is_declared(self):
        """The message source must be a real dsh source variant."""
        self.assertIn("kind: 'plugin'", self.src)


class TestDshPackaging(unittest.TestCase):
    def test_bundle_manifest(self):
        data = json.loads((DSH / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(data["type"], "module")
        self.assertEqual(data["main"], "lib/index.js")
        self.assertIn("dsh", data)
        self.assertEqual(data["dsh"]["bundle"]["patch"], "./cordis.patch.yml")
        self.assertIn("@deepseek-ai/cordis", data["dependencies"])
        self.assertIn("@deepseek-ai/dsh-llm", data["dependencies"])

    def test_cordis_patch_points_at_the_plugin_id(self):
        text = (DSH / "cordis.patch.yml").read_text(encoding="utf-8")
        self.assertIn("- insert:", text)
        self.assertIn("id: continue-hook", text)
        self.assertIn("name: 'continue-hook'", text)

    def test_tsconfig_targets_esm_and_strict(self):
        cfg = json.loads((DSH / "tsconfig.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["compilerOptions"]["module"], "NodeNext")
        self.assertEqual(cfg["compilerOptions"]["moduleResolution"], "NodeNext")
        self.assertTrue(cfg["compilerOptions"]["strict"])
        self.assertEqual(cfg["compilerOptions"]["rootDir"], "src")
        self.assertEqual(cfg["compilerOptions"]["outDir"], "lib")

    def test_typecheck_script_present(self):
        self.assertTrue((DSH / "typecheck.sh").exists())


class TestDetectorCli(unittest.TestCase):
    """The detector CLI is the contract the dsh plugin shares with the others."""

    @classmethod
    def setUpClass(cls):
        cls.detector = REPO / "detector.py"

    def _run(self, text: str, attempt: int = 0):
        r = subprocess.run(
            [sys.executable, str(self.detector), "--text", text, "--attempt", str(attempt)],
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(r.returncode, 0)
        return json.loads(r.stdout.decode().strip())

    def test_permission_seeking_is_detected(self):
        out = self._run("要不要我顺便也把示例补上？")
        self.assertTrue(out["should_nudge"])

    def test_declarative_ending_is_not_detected(self):
        self.assertFalse(self._run("已完成，验证通过。")["should_nudge"])

    def test_boundary_is_excluded(self):
        self.assertEqual(self._run("这会花 $69，需要你批准后我才买。")["reason"], "boundary")


if __name__ == "__main__":
    unittest.main()
