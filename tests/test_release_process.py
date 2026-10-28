"""The release path has invariants, and they are not obvious from the files.

Every check here exists because the thing it guards has actually broken, or
because a guard that silently stops guarding is worse than no guard at all.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from typing import ClassVar

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

    def test_it_does_not_upgrade_npm(self) -> None:
        """The node version pins the npm version.

        This used to assert the opposite — that the workflow runs
        `npm install -g npm@latest`. That upgrade is what made the publish
        job behave differently from every other job in the repository: npm 12
        installs a different layout, and the dsh runtime test failed on a
        missing tsc while CI was green. "latest" is not a version, so a gate
        that depends on it cannot be reproduced.

        node 24 ships npm 11.19.x, which meets both of npm's requirements
        (trusted publishing 11.5.1+, `npm stage publish` 11.15.0+).
        """
        self.assertNotIn("npm install -g npm@latest", self.text)
        self.assertIn("npm --version", self.text)


class TestTheWorkflowsReadTheNodeVersionFromOneFile(unittest.TestCase):
    """`.nvmrc` must be the only pin, in the repository and in CI alike.

    This is the failure that cost five release attempts: the local pin and
    the CI pin were two separate literals, so "it works here" said nothing
    about "it works there". A literal `node-version:` in a workflow is a
    second pin, and a second pin drifts.

    Exceptions are deliberate and named here: the `node` job runs a matrix
    of older versions on purpose (a library must work on more than the
    version its maintainer uses), and the `node18-cli` job proves the
    shipped CLI still runs on node 18. Both are *tests of other versions*,
    not the project's own toolchain.
    """

    #: Jobs allowed to name a node version literally, and why.
    ALLOWED_LITERAL_JOBS: ClassVar[dict[str, str]] = {
        "node18-cli": "proves the shipped CLI still runs on node 18",
        "node": "the compatibility matrix, by design",
    }

    def _jobs(self, text: str) -> dict[str, list[str]]:
        """Group a workflow's lines by job, using indentation only."""
        jobs: dict[str, list[str]] = {}
        current = None
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.startswith("  ") and not line.startswith("    "):
                stripped = line.strip()
                if stripped.endswith(":") and ":" not in stripped[:-1]:
                    current = stripped[:-1]
                    jobs[current] = []
                    continue
            if current is not None:
                jobs[current].append(line)
        return jobs

    def test_no_workflow_pins_a_node_version_of_its_own(self) -> None:
        offenders = []
        for workflow in sorted((REPO / ".github" / "workflows").glob("*.yml")):
            for job, lines in self._jobs(workflow.read_text(encoding="utf-8")).items():
                if job in self.ALLOWED_LITERAL_JOBS:
                    continue
                for line in lines:
                    stripped = line.strip()
                    if not stripped.startswith("node-version:"):
                        continue
                    value = stripped.split(":", 1)[1].strip()
                    # A matrix reference is not a pin — it names the matrix.
                    if value.startswith("${{"):
                        continue
                    offenders.append(f"{workflow.name}: job '{job}' pins {value}")
        self.assertEqual(
            offenders,
            [],
            "these workflows carry their own node pin instead of reading "
            ".nvmrc:\n  " + "\n  ".join(offenders),
        )

    def test_every_workflow_that_installs_node_reads_the_file(self) -> None:
        for workflow in sorted((REPO / ".github" / "workflows").glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            for job, lines in self._jobs(text).items():
                if job in self.ALLOWED_LITERAL_JOBS:
                    continue
                body = "\n".join(lines)
                if "actions/setup-node" not in body:
                    continue
                self.assertIn(
                    'node-version-file: ".nvmrc"',
                    body,
                    f"{workflow.name}: job '{job}' sets up node without .nvmrc",
                )

    def test_the_file_pins_an_exact_version(self) -> None:
        """A major prefix is not a pin.

        `.nvmrc` held "24" and both sides claimed to agree — until the day
        setup-node resolved it to 24.21.0 while the development machine ran
        24.22.1. That is not a hypothetical: it is the same class of drift
        that cost six release attempts, and it was still there after the
        first version of this test, which only asserted a major.

        An exact version has one cost — a security release is a one-line
        commit rather than an automatic pickup. That is the trade this
        project wants: a bump you can see in the log, not a difference you
        discover from a failed release.
        """
        value = (REPO / ".nvmrc").read_text(encoding="utf-8").strip()
        parts = value.split(".")
        self.assertEqual(
            len(parts),
            3,
            f".nvmrc must pin an exact version (x.y.z), got {value!r}",
        )
        self.assertTrue(all(p.isdigit() for p in parts), f"not a version: {value!r}")

    def test_the_gate_compares_the_whole_version(self) -> None:
        """The gate must reject a different *patch*, not just a different major."""
        gate = (REPO / "scripts" / "check.sh").read_text(encoding="utf-8")
        self.assertIn('PINNED="$(tr -d', gate)
        self.assertIn('[ "${CURRENT}" != "${PINNED}" ]', gate)
        # The old shape compared only the first component. Keep it out.
        self.assertNotIn("cut -d. -f1", gate)

    def test_the_pre_commit_hook_compares_the_whole_version(self) -> None:
        hook = (REPO / ".husky" / "pre-commit").read_text(encoding="utf-8")
        self.assertIn('[ "${CURRENT}" != "${PINNED}" ]', hook)
        self.assertNotIn("cut -d. -f1", hook)


class TestGitHooks(unittest.TestCase):
    """The hooks are the local copy of the CI gate.

    A commit that passes locally but fails in CI is the failure mode these
    hooks exist to catch, so they have to actually run — and the published
    package must not carry them.
    """

    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent.parent

    def test_the_hooks_exist_and_are_executable(self) -> None:
        for name in ("pre-commit", "pre-push"):
            hook = self.root / ".husky" / name
            self.assertTrue(hook.exists(), f".husky/{name} is missing")
            self.assertTrue(os.access(hook, os.X_OK), f".husky/{name} is not executable")

    def test_the_pre_commit_hook_runs_only_the_fast_checks(self) -> None:
        """The full gate takes a minute; a commit hook cannot run that."""
        text = _read(self.root / ".husky" / "pre-commit")
        self.assertIn("ruff", text)
        self.assertNotIn("check.sh", text)
        self.assertNotIn("vitest", text)

    def test_the_pre_push_hook_runs_the_whole_gate(self) -> None:
        text = _read(self.root / ".husky" / "pre-push")
        self.assertIn("check.sh", text)

    def test_the_hooks_refuse_the_wrong_node(self) -> None:
        """A gate on a different node than CI's does not predict CI."""
        text = _read(self.root / ".husky" / "pre-commit")
        self.assertIn(".nvmrc", text)

    def test_the_published_package_carries_no_hooks(self) -> None:
        """A published package must not ship git hooks."""
        package = json.loads(_read(self.root / "package.json"))
        files = package.get("files", [])
        self.assertFalse(
            any(entry.startswith(".husky") for entry in files),
            f"the package would ship the git hooks: {files}",
        )

    def test_husky_is_installed_by_the_prepare_script(self) -> None:
        """Otherwise the hooks exist only for whoever set them up by hand."""
        package = json.loads(_read(self.root / "package.json"))
        prepare = package["scripts"].get("prepare", "")
        self.assertIn("install-git-hooks", prepare)
        self.assertIn("husky", package.get("devDependencies", {}))

    def test_the_prepare_script_survives_a_pack_without_dev_dependencies(self) -> None:
        """npm runs `prepare` for `npm pack` too.

        CI has a job that packs the package without installing the dev
        dependencies, and there husky is not there at all. The script has to
        notice that and carry on — "sh: 1: husky: not found" is not a reason
        to refuse to build a package.
        """
        script = _read(self.root / "scripts" / "install-git-hooks.sh")
        self.assertIn("node_modules/.bin/husky", script)
        self.assertIn("exit 0", script)


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
