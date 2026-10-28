#!/usr/bin/env bash
# One command that runs everything CI runs (except the dsh typecheck, which
# needs the npm registry).
set -euo pipefail
cd "$(dirname "$0")/.."

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

echo "== node tests"
node tests/test_opencode.mjs
node tests/test_installer.mjs
node tests/test_lifecycle.mjs

echo "== eslint"
npx --no-install eslint .

echo "== tsc (Pi extension)"
npx --no-install tsc -p tsconfig.pi.json

echo "== npm pack (contents)"
npm pack --dry-run > /dev/null

echo
echo "all checks passed"
