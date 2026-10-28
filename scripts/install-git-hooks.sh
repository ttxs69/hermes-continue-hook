#!/usr/bin/env bash
# Install the git hooks (husky), if husky is installed.
#
# This runs on `npm install` (that is the point) and also on `npm pack` and
# `npm publish`, because npm runs the `prepare` lifecycle script for those
# too. Two things follow:
#
#   * In CI, some jobs pack the package without installing the dev
#     dependencies — husky is then not there at all. That must not fail the
#     pack: `npm error code 127, sh: 1: husky: not found` is not a reason
#     to refuse to build a package.
#   * When husky *is* there but fails, say so. A hook that silently did not
#     install is worse than no hook, because it looks installed.
#
# Husky itself is a no-op outside a git repository and when HUSKY=0.
set -u
cd "$(dirname "$0")/.."

if [ -x node_modules/.bin/husky ]; then
  node_modules/.bin/husky || echo "continue-hook: husky could not install the git hooks" >&2
else
  echo "continue-hook: husky is not installed — no git hooks (normal in CI)" >&2
fi

exit 0
