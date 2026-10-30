#!/usr/bin/env python3
"""CI 门：**发布物里出现的语料 sha，必须等于当前语料 sha**。

## 为什么是「读 GIF 自己的字节」而不是「读旁路回执 / 比 mtime」

我第一版用的是 `.gif-receipt` 文本 + `gif.mtime > fixture.mtime`。
红队第五轮证明这道门**两头都失效**，而且它在我的机器上是绿的：

1. **该绿不绿（CI 恒红）**：全新 clone 的 mtime **不是编辑顺序，是 git index 路径序**
   （实测 114 个文件零例外；`deliverables/demos/demo-all.gif` 在 index 第 52 位、
   `tests/fixtures/real_turns.jsonl` 在第 90 位）⇒ `gif.mtime < fixture.mtime`
   在 CI 里**恒定成立** ⇒ 3/3 clone 全部 `exit 78`。
2. **该红不红（假绿）**：只读回执文本 + mtime，**从不读 GIF** ⇒ 把 GIF 换成 0 字节、
   或换回上一轮的 GIF（帧里印着旧 sha），再手写回执，它照样打印 ✅。

⇒ 修法：sha 写进 GIF 的 comment extension 块（`tools/embed_gif_sha.py`），
  门用 PIL 读 `info['comment']`，剥旧块用**真正的块解析器**（不是逐字节找 `21 FE`）。

## ⚠⚠ 这道门的**真实强度上限**（红队第六轮要求我说清楚，不许自欺）

它是**流程门，不是存证门**。它证明的命题只有一条：

> 「有人把这 16 个字符写进了这个 GIF 的 comment 块。」

它**不**证明「GIF 的画面是用当前语料录的」。实测反例（红队做的，我复现过）：

- 拿上一轮的 GIF（帧里印着旧 sha），**不重录**，只跑 `embed_gif_sha.py` ⇒ **exit 0**
- 连仓库工具都不用：`python3 -c` 手工插 20 字节 ⇒ 同样 **exit 0**
- 用 PIL 造一个 8×8 单帧黑图，嵌上 sha ⇒ **exit 0**

⇒ 它能可靠拦住的，是**两个自然故障**（这两个是真正会发生的事）：
  ① GIF 被清空 / 截断 / 换成别的东西 ⇒ 读不出 comment ⇒ 红
  ② 改了语料但忘了重录 GIF ⇒ 帧里印的 sha 与语料不符 ⇒ 红

⇒ 它**拦不住**：有人主动跑一下 embed 去「补」证据。要真正做实，得把渲染器 / OCR
  闸进仓库（代价大得多）。**当前选择是「拦住意外，不拦故意」**，并用一条纪律兜底：
  **改语料的同一次提交里必须重录 GIF**，而不是在发布前补一个 comment。

    python3 scripts/check_published_sha.py
    python3 scripts/check_published_sha.py --allow-missing   # 发布物还没生成（本地开发）
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "real_turns.jsonl"
GIF = ROOT / "deliverables" / "demos" / "demo-all.gif"

# 会印给外人看的文档（CHANGELOG 里的历史叙述不归这道门管）
ARTIFACTS = ["README.md", "README.zh-CN.md", "tests/fixtures/README.md"]

SHA_RE = re.compile(r"\b([0-9a-f]{16})\b")


def current_sha() -> str:
    return hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:16]


def gif_sha() -> tuple[str | None, str]:
    """从 GIF **自身**读 comment 块。返回 (sha 或 None, 人类可读的原因)。"""
    if not GIF.exists():
        return None, "GIF 不存在"
    try:
        from PIL import Image

        info = Image.open(GIF).info or {}
    except Exception as e:  # 0 字节 / 截断 / 不是 GIF
        return None, f"GIF 读不出来（{type(e).__name__}）⇒ 它不可能是用当前语料录的"
    raw = info.get("comment")
    if not raw:
        return (
            None,
            "GIF 里没有 sha comment ⇒ 无法证明它是用当前语料录的（跑 tools/embed_gif_sha.py）",
        )
    sha = raw.decode("ascii", "replace") if isinstance(raw, bytes) else str(raw)
    return sha.strip(), ""


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
    got, why = gif_sha()
    if got is None:
        return [why]
    if got != sha:
        return [f"GIF 里印的 sha {got} ≠ 当前语料 {sha}（改了语料但没重录 / 没重新嵌入）"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="校验发布物里印的语料 sha 是否等于当前语料")
    ap.add_argument(
        "--allow-missing",
        action="store_true",
        help="发布物还没生成时跳过 GIF 检查（本地开发用；CI 不带这个参数）",
    )
    args = ap.parse_args()

    if not FIXTURE.exists():
        print("⚠ 语料不存在，跳过", file=sys.stderr)
        return 0

    sha = current_sha()
    bad = check_docs(sha)
    if args.allow_missing and not GIF.exists():
        print("⚠ --allow-missing：GIF 还没生成，跳过")
    else:
        bad += check_gif(sha)

    if bad:
        print("❌ 发布物与语料不同步：")
        for b in bad:
            print("   -", b)
        return 78
    print(f"✅ GIF 的 comment 与语料 sha 一致（{sha}）—— 流程门，不证明画面是用当前语料录的")
    return 0


if __name__ == "__main__":
    sys.exit(main())
