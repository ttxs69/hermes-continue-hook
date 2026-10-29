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
            if (
                "node_modules" in path.parts
                or "__pycache__" in path.parts
                or ".venv" in path.parts
                or "venv" in path.parts
            ):
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


class TestToolchainIsPinned(unittest.TestCase):
    """One node version, one npm, everywhere.

    The publish workflow used to run `npm install -g npm@latest` while every
    other job ran the npm that ships with the node it was given. "latest" is
    not a version, so the publish job drifted away from CI: npm 12 installs a
    different layout (no `.bin` shim for some packages), and the dsh runtime
    test failed on a missing tsc — a symptom of the environments differing,
    not of the plugin being broken.

    These tests make the alignment checkable instead of a convention.
    """

    def test_nvmrc_is_the_single_source_of_truth(self) -> None:
        nvmrc = REPO / ".nvmrc"
        self.assertTrue(nvmrc.exists(), "no .nvmrc — the node version is not pinned")
        version = nvmrc.read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+(\.\d+)*$", f"unexpected .nvmrc content: {version!r}")

    def test_workflows_use_the_pinned_node_major(self) -> None:
        pinned = (REPO / ".nvmrc").read_text(encoding="utf-8").strip().split(".")[0]
        for workflow in sorted((REPO / ".github" / "workflows").glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            for line in text.splitlines():
                if "node-version:" not in line:
                    continue
                value = line.split(":", 1)[1].strip().strip('"')
                # A matrix (`["22", "24"]`) and an expression are both fine:
                # the matrix job exists to prove the shipped CLI runs on
                # several node versions, and it does not run the release.
                if value.startswith("[") or value.startswith("${{"):
                    continue
                # node 18 is the compatibility floor and is deliberately older.
                if value == "18":
                    continue
                self.assertEqual(
                    value,
                    pinned,
                    f"{workflow.name} uses node {value}, but .nvmrc pins {pinned}. "
                    "The publish job must run the same npm as every other job.",
                )

    def test_npm_floor_lives_in_the_release_workflow_not_in_engines(self) -> None:
        """npm 版本是**发布工具**的约束，不是**使用者**的约束。

        2026-10-29 实测事故：engines 里写着 `"npm": ">=11.15.0"`（来自
        `npm stage publish` 的要求）。结果每个用默认 npm 跑
        `npx @ttxs69/hermes-continue-hook --help` 的人都会看到

            npm warn EBADENGINE  required: { npm: '>=11.15.0' }
                                 current:  { npm: '10.9.8' }

        包本身 rc=0 跑得起来，但对一个「陌生人 1 分钟试用」的 CLI，
        这行警告足以让人关掉页面 —— 约束放错了位置就成了交付缺陷。

        正确位置：发布 workflow（只有它知道自己要 stage publish）+ .nvmrc 单一事实源。

        ⚠ 本测试**不能**靠断言 publish.yml 注释里出现了 "11.15.0" 这几个字
        来「锁住」版本要求 —— 注释会被人改写，那种断言是一扇只会红、
        不会挡的门（2026-10-29 独立红队指出）。这里断言的是两件
        **机器可执行**的事实：
          ① publish.yml 里 `npm stage publish` 是**真正会跑的命令**（不是注释行）；
          ② .nvmrc 钉的 node 主版本 ≥ 24 —— 因为 npm 11 只随 node 24 发布
             （实测：node 22.23.2 → npm 10.9.8；node 24.22.1 → npm 11.19.1），
             所以「node 主版本够高」才是「npm 够新」的可执行代理。
        """
        import json

        package = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
        engines = package.get("engines", {})

        self.assertNotIn(
            "npm",
            engines,
            "engines 里不许写 npm：那是发布流程的要求，写进 engines 会让每个用默认 "
            "npm 的使用者看到 EBADENGINE 警告。发布侧要求请留在 publish.yml。",
        )
        self.assertIn("node", engines, "engines.node 必须显式声明运行时下界")

        # ① 发布路径必须真的在跑 stage publish（注释行不算）
        publish_lines = (
            (REPO / ".github" / "workflows" / "publish.yml")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        executable = [
            ln.strip()
            for ln in publish_lines
            if "npm stage publish" in ln and not ln.strip().startswith("#")
        ]
        self.assertTrue(
            executable,
            "publish.yml 里找不到可执行的 `npm stage publish` 行（只有注释不算）。"
            "staging 是免 2FA 的那条发布路径，它没了就没有无人值守的发布。",
        )

        # ② node 主版本必须够新（npm 11 随 node 24 发布）
        pinned = (REPO / ".nvmrc").read_text(encoding="utf-8").strip()
        major = int(pinned.split(".")[0])
        self.assertGreaterEqual(
            major,
            24,
            f".nvmrc 钉的是 node {pinned}。npm 11（trusted publishing 需 11.5.1+、"
            "`npm stage publish` 需 11.15.0+）只随 node 24 发布：实测 node 22.23.2 "
            "带的是 npm 10.9.8，node 24.22.1 才是 11.19.1。",
        )

    def test_no_workflow_upgrades_npm(self) -> None:
        for workflow in sorted((REPO / ".github" / "workflows").glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            self.assertNotIn(
                "npm install -g npm@latest",
                text,
                f"{workflow.name} upgrades npm to 'latest' — that is not a version, "
                "and it made the publish job behave differently from CI.",
            )


class TestTheVersionBumpKeepsTheFormatting(unittest.TestCase):
    """`npm version` must not leave the tree failing the gate.

    The version hook rewrote the plugin manifests with `JSON.stringify`,
    which puts every array element on its own line. Prettier collapses a
    short array onto one line, so the bump produced files that
    `prettier --check` rejected — and the release gate runs that check, so
    the release failed on its own version bump.
    """

    def test_the_manifests_are_prettier_clean(self) -> None:
        import subprocess

        prettier = REPO / "node_modules" / ".bin" / "prettier"
        if not prettier.exists():
            self.skipTest("prettier is not installed")
        manifests = sorted(
            str(p.relative_to(REPO))
            for p in REPO.rglob("*.json")
            if "node_modules" not in p.parts
            and "__pycache__" not in p.parts
            and ".venv" not in p.parts
            and p.name != "package-lock.json"
        )
        result = subprocess.run(
            [str(prettier), "--check", *manifests],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        self.assertEqual(
            result.returncode,
            0,
            "these manifests are not prettier-clean:\n" + result.stdout,
        )


class TestProjectHygiene(unittest.TestCase):
    """A published package must not carry local paths or credentials."""

    def test_no_local_absolute_paths(self):
        offenders = []
        for path in REPO.rglob("*"):
            if not path.is_file() or path.suffix in {".png", ".jpg", ".pyc"}:
                continue
            if (
                "__pycache__" in path.parts
                or "node_modules" in path.parts
                or ".venv" in path.parts
                or "venv" in path.parts
            ):
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
