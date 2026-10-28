#!/usr/bin/env bash
# Release a version to npm, through CI.
#
#   ./scripts/publish-npm.sh patch        # 1.3.1 → 1.3.2
#   ./scripts/publish-npm.sh minor        # 1.3.2 → 1.4.0
#   ./scripts/publish-npm.sh major        # 1.4.0 → 2.0.0
#   ./scripts/publish-npm.sh --version X.Y.Z
#   ./scripts/publish-npm.sh --dry-run    # ask CI to pack only
#
# The order follows the OpenJS Foundation's secure-release guidance:
#
#   1. safety checks   — a clean tree, the whole gate green
#   2. prepare         — the version bump, the changelog entry
#   3. publish         — a human says which version; CI does the publishing
#
# Step 3 is the important one: the publish happens only after an explicit
# request (a pushed semver tag), never automatically, and never on a merge to
# the default branch.
#
# Nothing here needs a credential. The workflow authenticates with the CI
# provider's OIDC identity (npm trusted publishing), so the repository holds
# no npm token and the release carries a provenance attestation.
set -euo pipefail
cd "$(dirname "$0")/.."

BUMP=""
EXPLICIT=""
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    patch|minor|major) BUMP="$arg" ;;
    --version) shift; EXPLICIT="${1:-}" ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
  shift || true
done

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
die() { printf '\n\033[31m!! %s\033[0m\n' "$1" >&2; exit 1; }

CURRENT=$(node -p "require('./package.json').version")

# ── 1. safety checks ────────────────────────────────────────────────────────
say "1. safety checks"

if [ -n "$(git status --porcelain)" ]; then
  die "the working tree is not clean. Commit or stash first:
$(git status --short)"
fi
if [ "$(git rev-parse --abbrev-ref HEAD)" != "main" ]; then
  die "releases are cut from main, not $(git rev-parse --abbrev-ref HEAD)"
fi
git fetch --quiet origin main
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  die "main is not in sync with origin/main. Push first."
fi
echo "   tree clean, on main, in sync with origin"

# The gate that decides whether the code is fit to ship. It is the same gate
# CI runs, and it is also wired as `prepublishOnly`, so a package published by
# any other route is checked too.
say "1b. the full gate"
./scripts/check.sh
echo "   gate green"

# ── 2. prepare ──────────────────────────────────────────────────────────────
if [ "$DRY_RUN" = "1" ]; then
  say "2. dry run — asking CI to pack ${CURRENT} without publishing"
  gh workflow run publish.yml -f dry-run=true
  echo "✓ watch it: gh run list --workflow publish.yml"
  exit 0
fi

say "2. bumping the version"
if [ -n "$EXPLICIT" ]; then
  npm version "$EXPLICIT" --no-git-tag-version
elif [ -n "$BUMP" ]; then
  npm version "$BUMP" --no-git-tag-version
else
  die "say what kind of release this is: patch, minor, major, or --version X.Y.Z"
fi

VERSION=$(node -p "require('./package.json').version")
TAG="v${VERSION}"
echo "   ${CURRENT} → ${VERSION}"

if git rev-parse "$TAG" >/dev/null 2>&1; then
  git checkout -- . 2>/dev/null || true
  die "tag $TAG already exists — pick another version"
fi

# The changelog entry is created by the version hook; it is a placeholder, so
# it has to be written before the commit. The release is not ready until the
# entry says what changed.
say "2b. the changelog entry"
if grep -q "^## ${VERSION}" CHANGELOG.md; then
  echo "   a section for ${VERSION} exists — edit it to say what changed:"
  echo
  sed -n "/^## ${VERSION}/,/^## /p" CHANGELOG.md | head -20
  echo
  read -r -p "   press Enter to accept, or Ctrl-C to stop: " _
fi

git add -A
git commit -q -m "release: ${VERSION}

$(sed -n "/^## ${VERSION}/,/^## /p" CHANGELOG.md | sed '1d;$d' | sed '/^$/d' | head -20)"

# ── 3. publish ──────────────────────────────────────────────────────────────
say "3. tagging and pushing"
echo "   → tagging ${TAG}"
git tag -a "$TAG" -m "continue-hook ${VERSION}"
git push origin main
git push origin "$TAG"
echo
echo "✓ pushed ${TAG} — CI will publish it."
echo "  watch:  gh run list --workflow publish.yml"
echo "  verify: npm view @ttxs69/hermes-continue-hook versions --json"
