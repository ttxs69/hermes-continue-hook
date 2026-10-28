#!/usr/bin/env bash
# The staged-publishing path for the first release, in the order npm requires.
#
# Why staged publishing: npm's trusted publishing refuses to publish a package
# that does not exist yet ("Package must exist" — npm trust docs, the registry
# API spec and the OIDC exchange endpoint all say it). Staging creates the
# package as a `0.0.0-stage` placeholder, so the trusted publisher can be
# configured at all.
#
# Why every command names the registry: this machine's ~/.npmrc points at
# https://mirrors.tencentyun.com/npm/. A mirror does not implement the staging
# API, and npm's OIDC exchange derives its audience from the registry host, so
# a mirror breaks both. REGISTRY below is the only registry we ever talk to.
#
# Steps that need the human (you): the 2FA challenge, and the browser login.
# Everything else is checked here.
set -euo pipefail

REGISTRY="https://registry.npmjs.org/"
PKG="@ttxs69/hermes-continue-hook"
REPO="ttxs69/hermes-continue-hook"
WORKFLOW="publish.yml"
ENVIRONMENT="npm"

cd "$(dirname "$0")/.."

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
die() { printf '\n\033[31m!! %s\033[0m\n' "$1" >&2; exit 1; }

# ── 0. preconditions ────────────────────────────────────────────────────────
say "0. checking the toolchain"
command -v npm >/dev/null || die "npm is not on PATH"
npm_version=$(npm -v)
node_version=$(node -v)
echo "   npm  $npm_version"
echo "   node $node_version"

# npm 11.15.0 is where staged publishing became generally available; npm 11.5.1
# is where trusted publishing arrived. Compare on the major.minor.patch.
version_at_least() {
  # $1 = required, $2 = actual
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]
}
version_at_least "11.15.0" "$npm_version" || die "npm >= 11.15.0 is required for 'npm stage' (found $npm_version)"
version_at_least "22.14.0" "$(printf '%s' "$node_version" | sed 's/^v//')" \
  || die "node >= 22.14.0 is required (found $node_version)"

say "0b. checking the identity"
if ! npm whoami --registry="$REGISTRY" >/dev/null 2>&1; then
  die "not logged in. Run:
     npm login --registry=$REGISTRY
   then re-run this script."
fi
echo "   logged in as $(npm whoami --registry="$REGISTRY")"

# ── 1. is the package already there? ────────────────────────────────────────
say "1. checking the registry"
if npm view "$PKG" versions --json --registry="$REGISTRY" >/dev/null 2>&1; then
  echo "   the package already exists:"
  npm view "$PKG" versions --json --registry="$REGISTRY"
  echo
  echo "   If 1.3.0 is not published yet, stage it. Otherwise stop here."
else
  echo "   the package does not exist yet — this is a first release."
fi

# ── 2. stage ────────────────────────────────────────────────────────────────
say "2. staging $PKG@$(node -p "require('./package.json').version")"
echo "   this uploads a tarball to the staging area; nothing is public yet."
npm stage publish --access public --registry="$REGISTRY" "$@"

say "2b. what is pending"
npm stage list --registry="$REGISTRY"

cat <<'EOF'

Next step — this one needs you, with your 2FA:

  npm stage list --registry=https://registry.npmjs.org/
  npm stage approve <stage-id> --registry=https://registry.npmjs.org/

(or approve on https://www.npmjs.com → Staged Packages)

Approving is what publishes 1.3.0. The 0.0.0-stage placeholder that staging
created is what makes the next step possible.

EOF

# ── 3. trusted publisher ────────────────────────────────────────────────────
say "3. the trusted publisher (run this after the approve)"
cat <<EOF
Now that $PKG exists, npm will accept an OIDC publish from this workflow.
The fields are matched exactly and case-sensitively:

  Organization or user : ttxs69
  Repository           : $REPO
  Workflow filename    : $WORKFLOW
  Environment name     : $ENVIRONMENT
  Allowed actions      : npm publish  (and npm stage publish, always allowed)

On npmjs.com:  Packages → $PKG → Settings → Trusted Publisher → GitHub Actions

Or from the CLI (npm >= 11.15.0, 2FA on the account):

  npm trust github $PKG \\
    --file $WORKFLOW \\
    --repo $REPO \\
    --env $ENVIRONMENT \\
    --allow-publish \\
    --registry=$REGISTRY

A trusted publisher that has not published once expires after 2 days, so do
this and the next release close together.

Then the secret is no longer needed:

  gh secret delete NPM_TOKEN --repo $REPO

EOF

say "done"
echo "   The package is staged, not published. Run 'npm stage list' to see it,"
echo "   and approve it with your 2FA to make it live."
