#!/bin/bash
# continue-hook — patch-mode installer (same-turn continuation, no upstream PR).
#
#   ./install.sh /path/to/hermes-agent      install
#   ./install.sh --check /path/to/hermes-agent   report only
#   ./install.sh --remove /path/to/hermes-agent  uninstall
#
# This mode edits the Hermes core source tree. It is pinned to one core
# revision (see BASE_COMMIT) and refuses to install on anything it cannot
# apply cleanly — the plugin mode is the portable option.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PATCH="$HERE/core-gate.patch"
MODE="install"
ROOT=""
for arg in "$@"; do
  case "$arg" in
    --check|--remove) MODE="${arg#--}" ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) ROOT="$arg" ;;
  esac
done
if [ -z "$ROOT" ] || [ ! -d "$ROOT/agent" ]; then
  echo "usage: $0 [--check|--remove] /path/to/hermes-agent" >&2
  exit 1
fi
cd "$ROOT"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "✗ $ROOT is not a git checkout — patch mode needs one so it can be undone." >&2
  exit 1
fi

PIN="$(cat "$HERE/BASE_COMMIT" 2>/dev/null || echo unknown)"
HEAD_SHORT="$(git rev-parse --short HEAD)"
if [ "$PIN" != "unknown" ] && [ "${HEAD_SHORT:0:${#PIN}}" != "$PIN" ]; then
  echo "! this core is at ${HEAD_SHORT}, the patch was generated against ${PIN}"
fi

if [ "$MODE" = "check" ]; then
  if git apply --check "$PATCH" 2>/dev/null; then
    echo "✓ the patch applies cleanly to $(git rev-parse --short HEAD)"
  elif git apply --reverse --check "$PATCH" 2>/dev/null; then
    echo "• already installed"
  else
    echo "✗ the patch does not apply — this core has moved on. Use the plugin mode." >&2
    exit 3
  fi
  exit 0
fi

if [ "$MODE" = "remove" ]; then
  if ! git apply --reverse --check "$PATCH" 2>/dev/null; then
    echo "✗ this tree does not have the patch applied — nothing to remove." >&2
    exit 3
  fi
  git apply --reverse "$PATCH"
  rm -f agent/continue_hook.py
  echo "✓ removed (restart the Hermes gateway to take effect)"
  exit 0
fi

# The patch is the authority on whether this tree is supported. A loose grep
# on a symbol was not: every recent core has it, so the guard passed on trees
# the patch could never apply to.
if git apply --reverse --check "$PATCH" 2>/dev/null; then
  echo "• already installed; nothing to do"
  exit 0
fi
if ! git apply --check "$PATCH" 2>/dev/null; then
  echo "✗ this patch was generated for core ${PIN} and does not apply to ${HEAD_SHORT}." >&2
  echo "  Use the plugin mode instead — it needs no core changes:" >&2
  echo "    npx hermes-continue-hook install --host claude-code" >&2
  echo "  (patch mode is pinned on purpose: it rewrites core files and cannot" >&2
  echo "   follow a moving target safely.)" >&2
  exit 3
fi

git apply "$PATCH"
# The patch carries agent/continue_hook.py itself (it is the generated build of
# the shared engine). Copying a second file over it would put two different
# implementations on top of each other — that is exactly the drift this mode
# used to have.

cat <<'EOF'
✓ installed. Two steps to switch it on:
    1) hermes config set agent.continue_hook_enabled true
    2) restart the Hermes gateway process
  Undo:  ./install.sh --remove /path/to/hermes-agent
EOF
