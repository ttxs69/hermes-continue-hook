#!/usr/bin/env bash
# The first release, with a token.
#
# Why a token at all: npm's trusted publishing (OIDC) refuses to publish a
# package that does not exist yet — the npm trust docs, the registry API spec
# and the OIDC exchange endpoint all say "the package must exist". So the
# first release needs a credential, and only the first one.
#
# Why every command names the registry: this machine's ~/.npmrc points at
# https://mirrors.tencentyun.com/npm/. A mirror does not implement the
# publishing API, and npm's OIDC exchange derives its audience from the
# registry host, so a mirror breaks both. REGISTRY below is the only registry
# we ever talk to.
#
# What needs you: creating the token (an identity action) and pasting it into
# the repository secret. Everything else is checked here.
set -euo pipefail

REGISTRY="https://registry.npmjs.org/"
PKG="hermes-continue-hook"
REPO="ttxs69/hermes-continue-hook"
WORKFLOW="publish.yml"
ENVIRONMENT="npm"

cd "$(dirname "$0")/.."

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
die() { printf '\n\033[31m!! %s\033[0m\n' "$1" >&2; exit 1; }

version_at_least() {
  # $1 = required, $2 = actual
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]
}

# ── 0. preconditions ────────────────────────────────────────────────────────
say "0. checking the toolchain"
command -v npm >/dev/null || die "npm is not on PATH"
npm_version=$(npm -v)
node_version=$(node -v | sed 's/^v//')
echo "   npm  $npm_version"
echo "   node $node_version"
version_at_least "11.5.1" "$npm_version" || die "npm >= 11.5.1 is required for trusted publishing (found $npm_version)"
version_at_least "22.14.0" "$node_version" || die "node >= 22.14.0 is required (found $node_version)"

say "0b. checking the package metadata"
node -e '
  const p = require("./package.json");
  const problems = [];
  if (!p.repository?.url?.includes("ttxs69/hermes-continue-hook")) {
    problems.push("repository.url must point at ttxs69/hermes-continue-hook (npm matches it exactly for the trusted publisher)");
  }
  if (p.publishConfig?.access !== "public") problems.push("publishConfig.access must be \"public\"");
  if (!p.version) problems.push("no version");
  if (problems.length) { console.error(problems.map(s => "   - " + s).join("\n")); process.exit(1); }
  console.log(`   ${p.name}@${p.version}, access=${p.publishConfig.access}`);
'
echo "   version in package.json: $(node -p "require('./package.json').version")"

# ── 1. is the package already on the registry? ──────────────────────────────
say "1. checking the registry"
if npm view "$PKG" versions --json --registry="$REGISTRY" >/dev/null 2>&1; then
  echo "   the package already exists on npm:"
  npm view "$PKG" versions --json --registry="$REGISTRY"
  echo
  echo "   If the current version is not published yet, tag and push:"
  echo "     git tag v$(node -p "require('./package.json').version") && git push origin v$(node -p "require('./package.json').version")"
else
  echo "   the package does not exist yet — this is a first release."
fi

# ── 2. the secret ───────────────────────────────────────────────────────────
say "2. the NPM_TOKEN secret"
if command -v gh >/dev/null 2>&1; then
  if gh secret list --repo "$REPO" 2>/dev/null | grep -q '^NPM_TOKEN'; then
    echo "   NPM_TOKEN is in the repository's secrets."
    echo
    echo "   The token needs to BYPASS 2FA. Measured against the registry: a"
    echo "   token with bypass_2fa off is refused with 401 \"You must provide a"
    echo "   one-time pass\", and CI has nobody to answer the prompt."
    echo "   Check it with:"
    echo
    echo "     curl -sS -H \"Authorization: Bearer \$NPM_TOKEN\" \\"
    echo "       https://registry.npmjs.org/-/npm/v1/tokens | grep bypass_2fa"
  else
    cat <<EOF
   It is not there yet. Create the token first:

     https://www.npmjs.com/settings/ttxs69/access-tokens

     Packages and scopes -> Permissions : Read and write (publish and stage)
     Select Packages                   : All Packages   (the package does not
                                          exist yet, so it cannot be named)
     Bypass two-factor authentication  : ON  <- required, see above
     Expiration                       : the shortest you can

   Then store it:

     gh secret set NPM_TOKEN --repo $REPO

   The token is for ONE publish. Step 4 removes it.
EOF
  fi
else
  echo "   gh is not on PATH — set the secret by hand in the repository's"
  echo "   Settings → Secrets and variables → Actions."
fi

# ── 3. publish ──────────────────────────────────────────────────────────────
say "3. publishing"
cat <<EOF
   Push the tag and the workflow publishes:

     ./scripts/publish-npm.sh

   That tags the current version, pushes the tag, and the workflow runs
   npm publish --access public --provenance. The provenance attestation is
   signed by sigstore with the GitHub OIDC id-token, so it needs the
   id-token: write permission the workflow already has — the npm token only
   has to authorise the publish.

   If the registry refuses the direct publish (a token without bypass 2FA),
   the workflow stages instead and prints the approve command. Staging needs
   no 2FA; only the approval does.
EOF

# ── 4. trusted publisher, then delete the secret ────────────────────────────
say "4. after the first publish"
cat <<EOF
   The package exists now, so npm will accept an OIDC publish from this
   workflow and the secret becomes unnecessary.

   On npmjs.com:  Packages → $PKG → Settings → Trusted Publisher →
   GitHub Actions:

     Organization or user : ttxs69
     Repository           : $REPO
     Workflow filename    : $WORKFLOW
     Environment name     : $ENVIRONMENT
     Allowed actions      : npm publish

   Or from the CLI (npm >= 11.15.0, 2FA on the account):

     npm trust github $PKG \
       --file $WORKFLOW \
       --repo $REPO \
       --env $ENVIRONMENT \
       --allow-publish \
       --registry=$REGISTRY

   Then:

     gh secret delete NPM_TOKEN --repo $REPO

   A trusted publisher that has not published once expires after 2 days, so
   do this and the next release close together.

   After that every release is:

     git tag v1.3.1 && git push origin v1.3.1
EOF

say "done"
echo "   The workflow is tag-driven. Nothing goes out until a tag is pushed."
