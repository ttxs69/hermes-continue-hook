#!/usr/bin/env python3
"""报告「兜底层会打码什么」—— **可读性守护**，不是安全守护。

## 角色变了（红队第八轮之后）

第一版这个工具是**黑名单检查器**：枚举「像代号」的形态，看还有哪些没被规则覆盖。
红队自造 6 种真代号，它**只抓到 2/6** —— 因为它自己用 `\b`（正是它要防的那个 bug）、
限 8 字符、只收大写开头 ⇒ 粘着汉字 / 长 ID / 小写代号 / Unicode 下标
四类**连 token 都提不出来**，然后报绿。

那次失败说明：**黑名单在原理上不可能完备**（正则只能枚举已知形态）。

⇒ 安全性现在由 **`mask_unknown_tokens()` 兜底层**保证：凡「同时含字母和数字」的
  短 token，**不在白名单里就一律打码**。代号必然是字母+数字混合 ⇒ 结构上漏不掉。
  这条路不需要「覆盖率」这个概念。

⇒ 所以这个工具改成回答另一个问题：**白名单是不是漏了哪些正常技术词**
  （那些词会被误打码，损害语料可读性）。

    python3 tools/coverage_check.py <原始语料.jsonl>            # 看会被打码的清单
    python3 tools/coverage_check.py --self-test                 # CI：只验工具与白名单自洽
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sanitize_corpus as sc


def _self_test() -> int:
    """CI 用：验白名单结构 + 三处同源（替换/兜底/扫描 用的是同一个 dict）。"""
    names = list(sc.ALLOWED_TOKENS)
    if len(names) != len(set(names)):
        print("❌ 白名单有重复键", file=sys.stderr)
        return 78
    no_reason = [k for k, v in sc.ALLOWED_TOKENS.items() if not v or len(v) < 2]
    if no_reason:
        print(f"❌ 白名单里这些项没写理由: {no_reason}", file=sys.stderr)
        return 78
    # 白名单项必须真的被保留（否则说明三处没同源）
    kept = [k for k in sc.ALLOWED_TOKENS if sc.sanitize(k) != k]
    if kept:
        print(
            f"❌ 这些项在白名单里，但 sanitize() 仍然改动了它们（三处不同源）: {kept[:10]}",
            file=sys.stderr,
        )
        return 78
    print(f"✅ 白名单 {len(sc.ALLOWED_TOKENS)} 项：无重复、有理由、三处同源")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="看看兜底层会打码什么（可读性守护）")
    ap.add_argument("raw", type=Path, nargs="?", help="原始（未脱敏）语料")
    ap.add_argument("--self-test", action="store_true", help="只验工具与白名单自洽（CI 用）")
    ap.add_argument("--list", action="store_true", help="打印全部会被打码的 token")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if not args.raw:
        print("❌ 需要原始语料路径（或用 --self-test）", file=sys.stderr)
        return 2

    text = args.raw.read_text(encoding="utf-8")
    tok = Counter(sc.TOKEN_RE.findall(text))
    masked: dict[str, int] = {}
    for k, n in tok.items():
        if not (sc.HAS_DIGIT.search(k) and sc.HAS_ALPHA.search(k)):
            continue  # 纯字母 / 纯数字 ⇒ 不归兜底层管
        if sc._is_allowed_token(k):
            continue
        masked[k] = n

    print(
        f"原始语料里「字母+数字混合」的短 token: {len(masked)} 种 / {sum(masked.values())} 次会被打码"
    )
    print(f"白名单已登记（原样保留）: {len(sc.ALLOWED_TOKENS)} 项")
    print("\n⚠ 下面这些会被打码 —— **逐个确认它们不是正常技术词**（是代号就没事）：")
    for k, n in sorted(masked.items(), key=lambda x: -x[1])[:40]:
        print(f"   {k:16} ×{n}")
    if args.list:
        print("\n（全量）")
        for k, n in sorted(masked.items(), key=lambda x: -x[1]):
            print(f"   {k:16} ×{n}")
    print("\nℹ 这只是**可读性**报告，不阻断：安全由兜底层保证（未登记的一律打码）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
