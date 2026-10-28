#!/usr/bin/env bash
# One command that runs everything CI runs (except the dsh typecheck, which
# needs the npm registry).
set -euo pipefail
cd "$(dirname "$0")/.."

# The toolchain must be the one the workflows use. A gate that runs on a
# different node/npm than CI is a gate that does not predict CI: the publish
# job used to run a different npm than every other job, and the difference
# showed up as a failing release rather than as a failing test.
PINNED_MAJOR="$(cat .nvmrc 2>/dev/null | cut -d. -f1)"
if [ -n "${PINNED_MAJOR}" ]; then
  CURRENT_MAJOR="$(node --version 2>/dev/null | sed 's/^v//' | cut -d. -f1)"
  if [ -n "${CURRENT_MAJOR}" ] && [ "${CURRENT_MAJOR}" != "${PINNED_MAJOR}" ]; then
    echo "!! node ${CURRENT_MAJOR} is running; .nvmrc pins ${PINNED_MAJOR}." >&2
    echo "!! Use the pinned version (nvm use) so this gate matches CI." >&2
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
npx --no-install vitest run --coverage

echo "== npm pack (contents)"
npm pack --dry-run > /dev/null

echo
echo "all checks passed"
