#!/usr/bin/env bash
# One command that runs everything CI runs (except the dsh typecheck, which
# needs the npm registry).
set -euo pipefail
cd "$(dirname "$0")/.."

# The toolchain must be the one the workflows use. A gate that runs on a
# different node/npm than CI is a gate that does not predict CI: the publish
# job used to run a different npm than every other job, and the difference
# showed up as a failing release rather than as a failing test.
# Compare the whole version. `.nvmrc` used to hold a major ("24"), which was
# not the same thing: setup-node resolved "24" to 24.21.0 in CI while this
# machine ran 24.22.1. A patch-level difference is the shape of the bug that
# cost six release attempts, so the gate now refuses to run on anything but
# the exact version it names.
PINNED="$(tr -d '[:space:]' < .nvmrc 2>/dev/null)"
if [ -n "${PINNED}" ]; then
  CURRENT="$(node --version 2>/dev/null | sed 's/^v//')"
  if [ -n "${CURRENT}" ] && [ "${CURRENT}" != "${PINNED}" ]; then
    echo "!! node ${CURRENT} is running; .nvmrc pins ${PINNED}." >&2
    echo "!! Use that exact version (nvm use) so this gate matches CI." >&2
    exit 1
  fi
fi

echo "== ruff check"
${RUFF:-ruff} check .

echo "== ruff format --check"
${RUFF:-ruff} format --check .

echo "== mypy"
${MYPY:-mypy}

echo "== patch-mode is in step with the engine"
python3 scripts/build-patch-mode.py --check

echo "== python tests"
python3 -m unittest discover -s tests

echo "== pytest (same suite, with the coverage floor)"
${PYTEST:-python3 -m pytest} --cov --cov-report=term-missing

echo "== eslint"
npx --no-install eslint .

echo "== prettier"
npx --no-install prettier --check '**/*.{js,mjs,ts,json,yml}'

echo "== tsc (Pi extension, strict)"
npx --no-install tsc -p tsconfig.json

echo "== tsc (JavaScript, checkJs)"
npx --no-install tsc -p tsconfig.checkjs.json

echo "== vitest (with the coverage floor)"
# The dsh suite is excluded here on purpose: it installs the real
# @deepseek-ai/dsh-* packages from the npm registry to compile against, so it
# needs the network. This gate has to work offline and reproducibly. CI runs
# that suite in its own job ("dsh plugin (typecheck + runtime)"), where the
# install is the point of the job.
npx --no-install vitest run --coverage --exclude "tests/test_dsh.test.mjs"

echo "== npm pack (contents)"
npm pack --dry-run > /dev/null

echo
echo "all checks passed"
