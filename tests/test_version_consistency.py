"""Version consistency — one version, four places.

The project publishes itself as an npm package, a Hermes plugin and a Python
package, and each of those carries its own version field. They drifted before
(the plugin said 1.0.0 while the package said 1.3.0), so the check is a test
rather than a convention.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Built from parts so this file does not itself contain the literal it hunts.
LOCAL_HOME_MARKER = "/home/" + "ubuntu"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestVersionConsistency(unittest.TestCase):
    def setUp(self):
        self.package = json.loads(_read(REPO / "package.json"))
        self.version = self.package["version"]

    def test_package_version_is_semver(self):
        self.assertRegex(self.version, r"^\d+\.\d+\.\d+$")

    def test_pyproject_matches_package(self):
        text = _read(REPO / "pyproject.toml")
        match = re.search(r'^version = "([^"]+)"', text, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), self.version)

    def test_plugin_yaml_matches_package(self):
        text = _read(REPO / "plugin.yaml")
        match = re.search(r"^version:\s*[\"']?([^\"'\n]+)", text, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), self.version)

    def test_plugin_module_matches_package(self):
        text = _read(REPO / "__init__.py")
        match = re.search(r'^__version__ = "([^"]+)"', text, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), self.version)

    def test_dsh_plugin_package_matches_package(self):
        path = REPO / "adapters" / "dsh" / "package.json"
        if not path.exists():
            self.skipTest("dsh plugin package not present")
        data = json.loads(_read(path))
        self.assertEqual(data["version"], self.version)

    def test_every_manifest_with_a_version_matches(self):
        """Any manifest carrying a version must carry *this* version.

        The four checked by name above used to be the whole list, and seven
        more manifests drifted out of step without the suite noticing: the
        per-harness plugin manifests (Claude Code, Codex, Cursor, Kimi,
        Devin, Antigravity) and the Gemini extension file. They are found
        here by walking the tree instead of by being listed, so a new
        manifest is covered the moment it is added.
        """
        offenders = []
        checked = 0
        for path in sorted(REPO.rglob("*.json")):
            if "node_modules" in path.parts or "__pycache__" in path.parts:
                continue
            if path.name in {"package-lock.json", "pack.json"}:
                continue
            try:
                data = json.loads(_read(path))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            version = data.get("version")
            if not isinstance(version, str) or not re.match(r"^\d+\.\d+\.\d+$", version):
                continue
            checked += 1
            if version != self.version:
                offenders.append(f"{path.relative_to(REPO)} says {version}")
        # The npm package itself is one of them.
        self.assertGreaterEqual(checked, 8, "the manifest walk found suspiciously few files")
        self.assertEqual(offenders, [], f"version drift: {offenders}")

    def test_changelog_mentions_the_current_version(self):
        text = _read(REPO / "CHANGELOG.md")
        self.assertIn(self.version, text)


class TestProjectHygiene(unittest.TestCase):
    """A published package must not carry local paths or credentials."""

    def test_no_local_absolute_paths(self):
        offenders = []
        for path in REPO.rglob("*"):
            if not path.is_file() or path.suffix in {".png", ".jpg", ".pyc"}:
                continue
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            try:
                text = _read(path)
            except (UnicodeDecodeError, OSError):
                continue
            if LOCAL_HOME_MARKER in text:
                offenders.append(str(path.relative_to(REPO)))
        self.assertEqual(offenders, [], f"local paths leaked into: {offenders}")

    def test_no_credential_shaped_strings(self):
        import re

        pattern = re.compile(r"sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|npm_[A-Za-z0-9]{20,}")
        offenders = []
        for path in REPO.rglob("*"):
            if not path.is_file() or path.suffix in {".png", ".jpg", ".pyc"}:
                continue
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            try:
                text = _read(path)
            except (UnicodeDecodeError, OSError):
                continue
            if pattern.search(text):
                offenders.append(str(path.relative_to(REPO)))
        self.assertEqual(offenders, [], f"credential-shaped strings in: {offenders}")

    def test_host_context_files_are_identical(self):
        """AGENTS.md / CLAUDE.md / GEMINI.md / QWEN.md carry the same rule.

        They are four copies of one text because four hosts read different
        filenames. If one is edited and the others are not, the hosts
        disagree — so the equality is enforced here rather than trusted.
        """
        names = ["AGENTS.md", "CLAUDE.md", "GEMINI.md", "QWEN.md"]
        contents = {name: _read(REPO / name) for name in names}
        distinct = set(contents.values())
        self.assertEqual(len(distinct), 1, "host context files have drifted apart")

    def test_no_duplicate_copy_of_an_adapter(self):
        """Each adapter implementation lives in exactly one place.

        A second copy is a copy that drifts: the project already had a stale
        duplicate of the OpenCode plugin that still called the old entry
        point. Duplicates are now a test failure, not a review comment.
        """
        import hashlib

        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        canonical = {}
        for path in (REPO / "adapters").rglob("*"):
            if path.is_file() and path.suffix in {".py", ".js", ".ts"}:
                canonical[digest(path)] = path.relative_to(REPO)

        duplicates = []
        for path in REPO.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".js", ".ts"}:
                continue
            rel = path.relative_to(REPO)
            if rel.parts[0] == "adapters" or rel.parts[0] == "tests":
                continue
            if "__pycache__" in rel.parts:
                continue
            key = digest(path)
            if key in canonical:
                duplicates.append((str(rel), str(canonical[key])))
        self.assertEqual(duplicates, [], f"duplicated adapter files: {duplicates}")

    def test_npm_files_manifest_covers_the_runtime(self):
        package = json.loads(_read(REPO / "package.json"))
        files = package.get("files", [])
        for required in ("bin/", "adapters/", "skills/", "detector.py", "hookctl.py"):
            self.assertIn(required, files)


if __name__ == "__main__":
    unittest.main()
