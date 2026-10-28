"""The release path has invariants, and they are not obvious from the files.

Every check here exists because the thing it guards has actually broken, or
because a guard that silently stops guarding is worse than no guard at all.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PUBLISH = REPO / ".github" / "workflows" / "publish.yml"
PACKAGE = REPO / "package.json"
SCRIPTS = REPO / "scripts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _permissions_of(text: str) -> dict[str, str] | None:
    """The workflow-level `permissions:` block, as a dict.

    Parsed by hand rather than with PyYAML: this test suite runs on the
    standard library alone, and a dev dependency would make it fail on a
    machine that has only Python. The block is a flat mapping of scalars,
    which is all this needs to read.
    """
    match = re.search(r"^permissions:\n((?:[ \t]+.*\n)+)", text, re.M)
    if not match:
        return None
    declared: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.strip().partition(":")
        # A trailing comment is not part of the value.
        value = value.split("#", 1)[0].strip().strip("'\"")
        declared[key.strip()] = value
    return declared


class TestTheReleaseScript(unittest.TestCase):
    """`scripts/publish-npm.sh` is the thing a human runs."""

    def setUp(self) -> None:
        self.script = _read(SCRIPTS / "publish-npm.sh")

    def test_it_refuses_to_publish_from_a_dirty_tree(self) -> None:
        self.assertIn("git status --porcelain", self.script)

    def test_it_refuses_to_publish_from_a_branch_other_than_main(self) -> None:
        self.assertIn('!= "main"', self.script)

    def test_it_runs_the_gate_before_publishing(self) -> None:
        self.assertIn("./scripts/check.sh", self.script)
        # The gate must run *before* the version bump, not after.
        self.assertLess(self.script.index("./scripts/check.sh"), self.script.index("npm version"))

    def test_the_publish_is_triggered_by_a_tag_not_by_a_merge(self) -> None:
        """OpenJS: publish only after an explicit request, never on a merge."""
        self.assertIn('git tag -a "$TAG"', self.script)
        self.assertIn('git push origin "$TAG"', self.script)

    def test_it_says_which_kind_of_release_it_is(self) -> None:
        for bump in ("patch", "minor", "major"):
            self.assertIn(bump, self.script)


class TestTheVersionHook(unittest.TestCase):
    """`npm version` must update every file that carries a version."""

    def setUp(self) -> None:
        self.script = _read(SCRIPTS / "sync-version.mjs")

    def test_it_is_wired_as_the_version_hook(self) -> None:
        scripts = json.loads(_read(PACKAGE))["scripts"]
        self.assertEqual(scripts["version"], "node scripts/sync-version.mjs")

    def test_it_creates_the_changelog_section(self) -> None:
        """A release with no changelog entry is a release nobody can find."""
        self.assertIn("CHANGELOG.md", self.script)
        self.assertIn("updateChangelog", self.script)

    def test_it_fails_when_a_manifest_is_not_listed(self) -> None:
        """A new manifest must not be able to drift silently."""
        self.assertIn("findUnlisted", self.script)
        self.assertIn("process.exit(1)", self.script)


class TestThePackageLifecycleHooks(unittest.TestCase):
    """OpenJS: the checks belong in package.json, so nobody can forget them."""

    def setUp(self) -> None:
        self.scripts = json.loads(_read(PACKAGE))["scripts"]

    def test_prepublish_only_runs_the_gate(self) -> None:
        self.assertEqual(self.scripts.get("prepublishOnly"), "npm run check")

    def test_prepack_cleans_the_tarball(self) -> None:
        self.assertIn("clean-pack", self.scripts.get("prepack", ""))

    def test_the_check_script_exists_and_is_executable(self) -> None:
        path = SCRIPTS / "check.sh"
        self.assertTrue(path.exists())
        self.assertTrue(path.stat().st_mode & 0o111, "check.sh is not executable")


class TestThePublishWorkflow(unittest.TestCase):
    """The workflow is the trust boundary; its permissions are the point."""

    def setUp(self) -> None:
        self.text = _read(PUBLISH)

    def test_it_has_exactly_the_permissions_it_needs(self) -> None:
        # id-token: write for the OIDC exchange, contents: read for the
        # checkout, and nothing else. A wildcard here would hand every job in
        # the workflow a write token.
        declared = _permissions_of(self.text)
        self.assertIsNotNone(declared, "the workflow declares no permissions")
        self.assertEqual(declared.get("id-token"), "write")
        self.assertEqual(declared.get("contents"), "read")
        self.assertEqual(set(declared), {"id-token", "contents"})

    def test_no_workflow_asks_for_more_than_it_uses(self) -> None:
        """A wildcard permission hands every job in the workflow a write token."""
        for path in (REPO / ".github" / "workflows").glob("*.yml"):
            declared = _permissions_of(path.read_text(encoding="utf-8"))
            if declared is None:
                continue
            for key, value in declared.items():
                self.assertNotEqual(value, "write-all", f"{path.name} asks for write-all")
                self.assertNotIn("*", value, f"{path.name} has a wildcard permission: {key}")

    def test_it_publishes_against_npmjs_org_explicitly(self) -> None:
        """A machine-level .npmrc pointing at a mirror would publish there."""
        self.assertIn("--registry=https://registry.npmjs.org/", self.text)

    def test_it_asks_for_provenance(self) -> None:
        self.assertIn("--provenance", self.text)

    def test_the_version_gate_refuses_a_mismatched_tag(self) -> None:
        self.assertIn("does not match package.json version", self.text)

    def test_it_does_not_cache_in_the_release_build(self) -> None:
        """A poisoned cache entry in a release build would be published."""
        self.assertIn("package-manager-cache: false", self.text)

    def test_it_uses_a_current_npm(self) -> None:
        self.assertIn("npm install -g npm@latest", self.text)


class TestNoSecretIsNeeded(unittest.TestCase):
    """The repository must hold no npm credential.

    A secret in a release path is a secret that leaks. The publish
    authenticates with the CI provider's OIDC identity instead.
    """

    def test_the_publish_workflow_reads_no_npm_secret(self) -> None:
        text = _read(PUBLISH)
        # NODE_AUTH_TOKEN may appear as an (empty) environment variable — npm
        # reads it — but it must not be *filled from a secret*.
        self.assertNotIn("secrets.NPM_TOKEN", text)

    def test_the_release_script_does_not_insist_on_a_token(self) -> None:
        text = _read(SCRIPTS / "publish-npm.sh")
        self.assertNotIn("NPM_TOKEN", text)


if __name__ == "__main__":
    unittest.main()
