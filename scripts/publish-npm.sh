#!/usr/bin/env bash
# Publish the current version to npm through CI.
#
#   ./scripts/publish-npm.sh            # tag + push → CI publishes
#   ./scripts/publish-npm.sh --dry-run  # ask CI to pack only
#
# What has to be true before this works:
#   1. The npm package exists — npm's trusted publishing (OIDC) refuses to
#      publish a package that does not exist yet, so the FIRST release needs
#      an automation token in the repository secret NPM_TOKEN.
#      After that one release, trusted publishing takes over and the secret
#      can be removed.
#   2. The tag matches package.json — the workflow's version gate refuses a
#      tag that does not.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(node -p "require('./package.json').version")
TAG="v${VERSION}"

if [ "${1:-}" = "--dry-run" ]; then
  echo "→ asking CI for a dry run of ${VERSION}"
  gh workflow run publish.yml -f dry-run=true
  echo "✓ watch it: gh run list --workflow publish.yml"
  exit 0
fi

if ! gh secret list | grep -q '^NPM_TOKEN'; then
  echo "✗ the repository has no NPM_TOKEN secret."
  echo
  echo "  The first npm release cannot use OIDC (npm requires the package to"
  echo "  exist first), so it needs a token:"
  echo
  echo "    1. create an npm account:      https://www.npmjs.com/signup"
  echo "    2. turn on 2FA (auth-and-writes)"
  echo "    3. create a token:             https://www.npmjs.com/settings/ttxs69/access-tokens"
  echo "       npm removed legacy automation tokens in Nov 2025 — create a"
  echo "       Granular Access Token with:"
  echo "         Packages and scopes → Read and write (publish and stage)"
  echo "         Select Packages        → All Packages (the package does not"
  echo "                                   exist yet, so it cannot be picked)"
  echo "         Bypass 2FA             → on"
  echo "         Expiration             → as short as possible"
  echo "    4. store it:                    gh secret set NPM_TOKEN"
  echo
  echo "  Then re-run this script. For later releases, configure trusted"
  echo "  publishing once and drop the secret (needs npm >= 11.15.0):"
  echo
  echo "    npm trust github hermes-continue-hook \\"
  echo "      --repo ttxs69/hermes-continue-hook --file publish.yml --env npm \\"
  echo "      --allow-publish --registry=https://registry.npmjs.org/"
  echo
  echo "  Note: a token with bypass-2FA may NOT configure a trusted publisher —"
  echo "  that call needs an interactive 2FA challenge."
  exit 2
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "✗ tag $TAG already exists locally — move it first:"
  echo "    git tag -d $TAG && git push --delete origin $TAG"
  exit 3
fi

echo "→ tagging $TAG"
git tag -a "$TAG" -m "continue-hook ${VERSION}"
git push origin "$TAG"
echo "✓ pushed $TAG — CI will publish it: gh run list --workflow publish.yml"
