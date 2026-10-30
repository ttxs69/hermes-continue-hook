#!/usr/bin/env bash
# Demo 串讲 —— AI 岗面试辅导的可录屏交付物（4 段，一条命令）
#
# ⭐ 这四段对应的是「面试官会问的最狠追问」，
#    每一个都能当场复算 —— 不是 PPT，是能跑的脚本。
#
#   Demo 1 「你怎么知道你的判断是对的」 → scripts/replay_real.py
#   Demo 2 「误伤了怎么办？你测过吗」   → scripts/replay_regression.py
#   Demo 3 「10 个宿主怎么保证规则一致」 → scripts/check_hosts.py
#   Demo 2b「你怎么保证这些数没被你写错」 → scripts/verify_claims.py（独立复算）
#
# 用法:
#   bash deliverables/demos/demo-all.sh
#
# 录屏:
#   ffmpeg -f x11grab ... （或 asciinema rec）
set -uo pipefail

# ⚠ 仓库路径必须**自动定位** —— 硬编码本机绝对路径，别人 clone 下来跑不了。
# 优先级：① 环境变量 REPO ② 从本脚本位置往上找 ③ 当前目录
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${REPO:-}" ]; then
  cd "$REPO"
elif [ -d "$_here/../../products/hermes-continue-hook" ]; then
  cd "$_here/../../products/hermes-continue-hook"
elif [ -d "$_here/../hermes-continue-hook" ]; then
  cd "$_here/../hermes-continue-hook"
elif [ -f "$_here/../../detector.py" ]; then
  cd "$_here/../.."          # ← demo 在本仓 deliverables/demos/ 里
elif [ -f "detector.py" ]; then
  :   # 已经在仓库根
else
  echo "找不到 hermes-continue-hook 仓库。设 REPO=/path/to/repo 后重试。" >&2
  exit 2
fi
# ⚠ 不要打印绝对路径 —— 这行会进 GIF，GIF 是要公开的。
#   红队 R311 实测抓到过（GIF 首帧出现本机绝对路径）。
echo "  仓库：$(basename "$PWD")"
export PATH="$PWD/.venv/bin:$PATH"

FRAME() { printf "\n@@FRAME@@\n"; }
bar() { printf '\n\033[1;36m%s\033[0m\n' "$(printf '═%.0s' {1..72})"; }
sub() { printf '\033[1;33m%s\033[0m\n' "$1"; }

bar
echo "  AI 岗面试辅导 · 交付 Demo（4 段，全部可当场复算）"
echo "  项目：hermes-continue-hook（npm 1.5.1）"
bar

# ⚠⚠ **总结段的数字必须从脚本输出里取，不能写死**（R311 红队 E2）。
#   写死的后果：脚本算出一个数、总结写另一个 ⇒ 屏幕上两套数字，
#   面试官当场就能问「这两个哪个对」。
#   ⇒ 这里先静默取一次 --json，后面总结段用变量插值 ⇒ 永远一致。
#   ⚠ 变量名与打印顺序必须一一对应（写错过一次：D_TOTAL 拿到了 nudge 的值，
#   屏幕上出现「40 条真实 agent 收尾」）。
read -r D_TOTAL D_NUDGED D_OLD_B D_TRULY < <(
  python3 scripts/replay_real.py --json 2>/dev/null | python3 -c "
import json,sys
j=json.load(sys.stdin)
print(j['corpus_size'], j['nudged'], j['old']['boundary_released'], j['old']['truly_missed'])
" 2>/dev/null || echo "0 0 0 0"
)
# ⚠⚠ 宿主侧的数也必须插值（红队第二轮 N4）：
#   原来「9 个 adapter」「84 项」「1420 行」「285 行」全是写死的
#   ⇒ 给 adapters/codex 加 100 行，脚本输出 1520 而总结段仍写 1420
#   ⇒ **屏幕上两套数字并存**（与 E2 同型的失败，只是漏了 demo 脚本本身）。
read -r H_ADAPTERS H_ROUTED H_LINES H_SHARED H_CONTRACT H_ENFORCE H_IMPL H_MISMATCH < <(
  python3 scripts/check_hosts.py --json 2>/dev/null | python3 -c "
import json,sys
j=json.load(sys.stdin)
ct = j.get('contract_tests', {})
print(j['adapters'], j['routed_to_canonical'],
      j['adapter_layer_lines']['total'], j['adapter_layer_lines']['shared_module_lines'],
      sum(int(v.get('ran') or 0) for v in ct.values()),
      j.get('enforce_hosts', 0), j.get('implementation_copies', 0),
      1 if j.get('enforce_hosts_mismatch') else 0)
" 2>/dev/null || echo "0 0 0 0 0 0 0 0"
)

FRAME
sub "Demo 1 ·「你怎么知道你的判断是对的」"
echo "  → $D_TOTAL 条真实 agent 收尾的触发/放行分布"
python3 scripts/replay_real.py || echo "  [demo1 失败 exit=$?]"

FRAME
sub "Demo 2 ·「误伤了怎么办？你测过吗」"
echo "  → 同一批语料，旧版（boundary 词表）vs 新版（删表）"
python3 scripts/replay_regression.py || echo "  [demo2 失败 exit=$?]"

FRAME
sub "Demo 3 ·「10 个宿主，你怎么保证规则一致」"
if [ "$H_MISMATCH" = "1" ]; then
  echo "  → $H_ADAPTERS 个 adapter（$H_ROUTED/$H_ADAPTERS 全路由到同一入口）+ $H_CONTRACT 项契约测试"
  echo "  ⚠ enforce 口径不一致：README 表 = $H_ENFORCE，adapter 数 +1 = $((H_ADAPTERS + 1)) ⇒ 请同步 README"
else
  echo "  → $H_ADAPTERS 个 adapter（$H_ROUTED/$H_ADAPTERS 全路由到同一入口）+ $H_CONTRACT 项契约测试（$H_ADAPTERS 个 adapter + Hermes 本仓 core patch mode = $H_ENFORCE 个 enforce 宿主）"
fi
python3 scripts/check_hosts.py || echo "  [demo3 失败 exit=$?]"

FRAME
sub "Demo 2b ·「你怎么保证这些数字没被你写错」"
echo "  → 用一个**独立重写**的脚本复算，不信任上面那个脚本的输出"
python3 scripts/verify_claims.py || echo "  [verify_claims 失败 exit=$?]"

FRAME
bar
echo "  三句话总结（面试时直接说）"
bar
# ⚠⚠ 预计算百分比：**heredoc 里不能塞命令替换**（写错过一次：
#   `<<'EOF'` 不展开变量，命令替换也会被当字面量输出）。
D_PCT="$(python3 -c "print(f'{$D_OLD_B/$D_TOTAL*100:.1f}')" 2>/dev/null || echo '?')"
# ⚠ 用**不带引号**的 heredoc 才会展开 $D_*；正文里的 $ 需转义为 \$
cat <<EOF

  1「我的检测器只判形状，语义我交还给模型自己 ——
     所以召回率有上限，我把这个上限公开写进 changelog，
     因为我更想知道它在哪失效，而不是让人以为它 100%。」

  2「同一批 $D_TOTAL 条真实收尾，我旧版提前放行 $D_OLD_B 条（$D_PCT%），其中 $D_TRULY 条本来该 nudge，
     新版是 0。我不是把词表调大，是把那张表删了 ——
     因为词表判断的是『tail 里有没有这个词』，
     而语义是『这一条在请求不可逆动作』，这是两件事。」

  3「$H_ENFORCE 个 enforce 宿主、$H_IMPL 份 detector、薄适配层 $H_LINES 行（含共享模块 $H_SHARED 行）。
     历史上我手写过第二份 patch 实现，跟插件版漂移了，
     机器上同时跑两套却没人发现 —— 后来改成共享引擎生成，
     『规则只有一处』现在是可以验的，不是口号。」

  4「这些数字我用一个**独立重写**的脚本再算了一遍 ——
     而它当场抓出了我自己写的另一个脚本的 bug：
     我把新版的配置喂给了旧版，旧版拿不到词表，
     跑出『误放行 0 条』这种看起来很干净的假数字。
     所以我现在报的是**两个独立实现互相验证过的数**，
     不是『脚本跑出来是多少』。」
EOF
bar
