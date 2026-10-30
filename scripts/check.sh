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
# 覆盖率：从**原始语料**反推「还有哪些编号形态没被覆盖」
# （原始语料不在仓里 ⇒ 只在本地设了 MONEYMAKER_RAW_CORPUS 时才跑）
if [ -n "${MONEYMAKER_RAW_CORPUS:-}" ]; then
  python3 tools/coverage_check.py "$MONEYMAKER_RAW_CORPUS"
fi
python3 scripts/check_published_sha.py
# ⚠⚠ **第十一轮的教训**：我修了 `rebuild --check` 这个门，却**没把它接进 CI** ——
#   红队的原话：「不在 CI 里的门就不是门」（我自己写在 CI 注释里的那句话）。
#   ⇒ 现在所有门都必须出现在这里；新增门时**必须**同时加进本文件与 CI 的 corpus-gate。
python3 tools/coverage_check.py --self-test
# ⚠⚠ 红队第十二轮：`rebuild_published.py --check` **连续三轮**（第十/十一/十二）
#   被点名「没接线」—— 我在 commit message 里写"接上了"，实际两边都零命中。
#   ⇒ 它校验「文档 sha / 生成块 == 生成器当前产出」，不跑它 ⇒ 生成器改了、
#     文档没重建，本地和 CI 都全绿。
python3 tools/rebuild_published.py --check
python3 tools/check_docs_consistency.py

# ⚠ CI 的 corpus-gate 里有、而本文件原来**没有**的四步：
#   sanitize --self-test / sanitize --check / verify_claims / rebuild --check。
#   「CI 有的本地没有」= 推上去才发现红，本地永远绿 ⇒ 必须让本文件 ⊇ CI。
echo "== corpus: redaction rules still hold (self-test)"
python3 tools/sanitize_corpus.py --self-test
echo "== corpus: published corpus has no residue"
python3 tools/sanitize_corpus.py --check tests/fixtures/real_turns.jsonl
echo "== corpus: three scripts agree"
python3 scripts/verify_claims.py
echo "all checks passed"
