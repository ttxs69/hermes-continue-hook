#!/bin/bash
# continue-hook 补丁模式安装器：同 turn 无缝续跑（无需等上游 PR）
# 用法: ./install.sh /path/to/hermes-agent   （卸载: ./install.sh --remove /path）
set -e
MODE=""; ROOT=""
if [ "${1:0:2}" = "--" ]; then MODE=$1; ROOT=${2:-}; else ROOT=${1:-}; fi
if [ -z "$ROOT" ] || [ ! -d "$ROOT/agent" ]; then
  echo "用法: $0 [--remove] /path/to/hermes-agent"; exit 1; fi
cd "$ROOT"
if [ "$MODE" = "--remove" ]; then
  git apply -R "$(dirname "$0")/core-gate.patch" && rm -f agent/continue_hook.py
  echo "已移除（记得重启 Hermes 网关进程）"; exit 0
fi
# 版本护栏：确认目标文件含停止闸链（对不上就拒装，防错版本）
grep -q "_kanban_stop_nudge" agent/turn_stop_gates.py || { echo "目标版本不含停止闸链，拒绝打补丁（请用插件模式）"; exit 2; }
git apply --check "$(dirname "$0")/core-gate.patch" || { echo "补丁不干净（可能已打过或版本不符）"; exit 3; }
git apply "$(dirname "$0")/core-gate.patch"
cp "$(dirname "$0")/continue_hook.py" agent/continue_hook.py
# 开关默认关，装完自行开启
echo "已安装。开启两步: 1) hermes config set agent.continue_hook_enabled true  2) 重启 Hermes 网关进程"
