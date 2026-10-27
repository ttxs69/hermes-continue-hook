#!/usr/bin/env bash
# One command that runs everything CI runs (except the dsh typecheck, which
# needs the npm registry).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== ruff"
ruff check .

echo "== mypy"
mypy

echo "== python tests"
python3 -m unittest discover -s tests

echo "== node tests"
node tests/test_opencode.mjs
node tests/test_installer.mjs

echo "== npm pack (contents)"
npm pack --dry-run > /dev/null

echo
echo "all checks passed"
