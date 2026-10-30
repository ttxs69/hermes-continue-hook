#!/usr/bin/env python3
"""CI 门：**发布物里出现 sha256 的地方，必须等于当前语料 sha**。

红队第四轮建议第 10 项。为什么需要这道门：
  `GIF` / `README` / `CHANGELOG` 都会引用语料 sha256 的前 16 位。
  我改语料（脱敏规则一改就变）之后**忘了重录 GIF** —— 结果 GIF 印着一个
  已经不存在的语料指纹，而我自己的验收脚本只看 GIF 有没有生成、不看里面印了什么。
  ⇒ 这道门把「改语料」和「重录发布物」绑在一起：**只改语料不重录 ⇒ CI 红**。

    python3 scripts/check_published_sha.py            # 校验
    python3 scripts/check_published_sha.py --allow-missing  # 发布物还没生成时跳过
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "real_turns.jsonl"
GIF = ROOT / "deliverables" / "demos" / "demo-all.gif"

# 会印给外人看的产物（文档允许只出现在「历史叙述」上下文里）
ARTIFACTS = ["README.md", "README.zh-CN.md", "tests/fixtures/README.md"]

SHA_RE = re.compile(r"\b([0-9a-f]{16})\b")


def current_sha() -> str:
    return hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:16]


def gif_text() -> str:
    """GIF 里印的字没法直接读 —— 用 OCR 太重，改为读**生成它的那次运行留下的回执**。

    没有回执就说明它是在「当前语料」之前录的 ⇒ 报不一致（宁可红）。
    """
    receipt = ROOT / "deliverables" / "demos" / ".gif-receipt"
    return receipt.read_text(encoding="utf-8") if receipt.exists() else ""


def check_docs(sha: str) -> list[str]:
    bad: list[str] = []
    for rel in ARTIFACTS:
        f = ROOT / rel
        if not f.exists():
            continue
        for hit in set(SHA_RE.findall(f.read_text(encoding="utf-8"))):
            if hit != sha:
                bad.append(f"{rel}: 出现 sha {hit} ≠ 当前语料 {sha}")
    return bad


def check_gif(sha: str) -> list[str]:
    """回执 + mtime 双重证据。

    回执是文本，**手写也能过** ⇒ 再加一条文件系统证据：
    GIF 必须比语料新。手写回执而不重录 ⇒ mtime 检查仍然红。
    """
    receipt = gif_text().strip()
    if not receipt:
        return (
            ["GIF 存在但没有录制回执 ⇒ 无法证明它是用当前语料录的（重跑 record_gif.py）"]
            if GIF.exists()
            else []
        )
    if receipt != sha:
        return [f"GIF 录制回执 sha {receipt} ≠ 当前语料 {sha}（改了语料但没重录）"]
    if GIF.exists() and GIF.stat().st_mtime < FIXTURE.stat().st_mtime:
        return ["GIF 比语料旧 ⇒ 语料改过但 GIF 没重录（回执可能是手写的）"]
    return []


def main() -> int:
    if not FIXTURE.exists():
        print("⚠ 语料不存在，跳过", file=sys.stderr)
        return 0
    sha = current_sha()
    bad = check_docs(sha) + check_gif(sha)
    if bad:
        print("❌ 发布物与语料不同步：")
        for b in bad:
            print("   -", b)
        return 78
    print(f"✅ 发布物 sha 与语料一致（{sha}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
