#!/usr/bin/env bash
# Typecheck the DeepSeek Harness plugin against the REAL dsh packages.
#
# The dsh plugin uses `ctx.on('agent/turn-stopping')`, `agent.session.deriveMessages()`
# and `agent.steer(createUserMessage(...))`. Those APIs are only guaranteed by
# dsh's own type declarations, so this script installs them and runs `tsc`
# against them — a compile error means the plugin no longer matches dsh.
#
#   ./adapters/dsh/typecheck.sh          # install into a temp dir and check
#
# Needs network access to the npm registry. Skip it in CI when offline; the
# contract tests in tests/test_dsh_contract.py still run.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${TMPDIR:-/tmp}/ch-dsh-typecheck"
DSH_VERSIONS="${DSH_VERSIONS:-@deepseek-ai/dsh-agent @deepseek-ai/dsh-llm @deepseek-ai/dsh-session @deepseek-ai/dsh-tools @deepseek-ai/cordis}"

rm -rf "$WORK"
mkdir -p "$WORK/src"
cp "$HERE/src/index.ts" "$WORK/src/index.ts"
cp "$HERE/tsconfig.json" "$WORK/tsconfig.json"
cat > "$WORK/package.json" <<'EOF'
{"name": "continue-hook-dsh-typecheck", "type": "module", "private": true}
EOF

cd "$WORK"
npm i --no-save --no-audit --no-fund $DSH_VERSIONS typescript @types/node >/dev/null 2>&1
./node_modules/.bin/tsc -p tsconfig.json
echo "dsh plugin typecheck: OK (against $(node -e "console.log(require('./node_modules/@deepseek-ai/dsh-agent/package.json').version)"))"
