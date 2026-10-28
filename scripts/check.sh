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
