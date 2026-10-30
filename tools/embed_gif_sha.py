#!/usr/bin/env python3
"""把语料 sha256[:16] **写进 GIF 本身**（GIF89a comment extension 块）。

## 为什么不用「旁路回执文件」

我先做的是「录完 GIF 写一个 `.gif-receipt` 文本 + 比 mtime」—— 红队第五轮证明
这道门**两头都失效**：

1. **该绿不绿（CI 恒红）**：全新 clone 的 mtime **不是编辑顺序，是 git index 路径序**
   （实测 114 个文件零例外；`deliverables/demos/demo-all.gif` 在 index 第 52 位、
   `tests/fixtures/real_turns.jsonl` 在第 90 位）⇒ `gif.mtime < fixture.mtime`
   在 CI 里**恒定成立** ⇒ 3/3 clone 全部 `exit 78`。我本机因为重录过所以是绿的
   —— 典型的 works on my machine。
2. **该红不红（假绿）**：门只读回执文本 + mtime，**从不读 GIF** ⇒ 把 GIF 换成
   0 字节、或换回上一轮的 GIF（帧里印着旧 sha），再手写回执，它照样打印 ✅。

⇒ 唯一能同时解决两头的做法：**证据必须长在产物里**。
   GIF89a 允许任意 comment extension 块（`0x21 0xFE <len> <data> 0x00`），
   PIL 会把它读成 `info['comment']`。
   写它就必须改 GIF 的字节 ⇒ **手写回执这条路不存在**。

## 为什么不用「PIL 重新保存」

`Image.save()` 会重新编码（实测 477471 B → 400212 B），画面虽同但**字节变了**。
这个仓库的规矩是发布物要能字节级复现 ⇒ 这里**只插一个块，不碰任何像素**。

    python3 tools/embed_gif_sha.py <file.gif>            # 用当前语料 sha
    python3 tools/embed_gif_sha.py <file.gif> --sha xxx   # 指定
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "real_turns.jsonl"

COMMENT_MARK = b"\x21\xfe"


def _after_screen_descriptor(data: bytes) -> int:
    """返回「逻辑屏幕描述符（+ 全局调色板）之后」的偏移 = 可以插块的位置。"""
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise SystemExit(f"不是 GIF（header={data[:6]!r}）")
    if len(data) < 13:
        raise SystemExit("GIF 太短，读不到逻辑屏幕描述符")
    flags = data[10]
    gct = 3 * (2 ** ((flags & 0x07) + 1)) if flags & 0x80 else 0
    return 13 + gct


def strip_comments(data: bytes) -> bytes:
    """剥掉已有的 comment extension 块（幂等：重复嵌入不会越积越多）。

    只在**顶层块流**里剥：图像数据块前面的那些 `0x21 0xFE ... 0x00`。
    碰到第一个图像描述符（0x2C）就停 —— 之后的字节一律不动。
    """
    pos = _after_screen_descriptor(data)
    out = bytearray(data[:pos])
    i = pos
    n = len(data)
    while i < n:
        b = data[i]
        if b == 0x2C:  # 图像描述符：后面全不动
            out += data[i:]
            return bytes(out)
        if data[i : i + 2] == COMMENT_MARK:
            j = i + 2
            while j < n and data[j] != 0x00:  # 子块：长度字节 + 数据
                j += 1 + data[j]
            out += data[j + 1 : j + 1]  # 跳过终止 0x00
            i = j + 1
            continue
        if b == 0x3B:  # trailer
            out += data[i:]
            return bytes(out)
        out.append(b)
        i += 1
    return bytes(out)


def embed(path: Path, sha: str) -> None:
    data = path.read_bytes()
    data = strip_comments(data)
    pos = _after_screen_descriptor(data)
    payload = sha.encode("ascii")
    if len(payload) > 255:
        raise SystemExit("sha 太长")
    block = COMMENT_MARK + bytes([len(payload)]) + payload + b"\x00"
    path.write_bytes(data[:pos] + block + data[pos:])


def main() -> int:
    ap = argparse.ArgumentParser(description="把语料 sha 写进 GIF 的 comment 块")
    ap.add_argument("gif", type=Path)
    ap.add_argument("--sha", default=None, help="默认取当前语料 sha256[:16]")
    args = ap.parse_args()
    sha = args.sha or hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:16]
    before = args.gif.stat().st_size
    embed(args.gif, sha)
    after = args.gif.stat().st_size

    # 立刻读回来验证（不信任自己写的）
    from PIL import Image

    got = (Image.open(args.gif).info or {}).get("comment")
    got = got.decode("ascii", "replace") if isinstance(got, bytes) else got
    if got != sha:
        print(f"❌ 嵌入后读回是 {got!r}，期望 {sha!r}", file=sys.stderr)
        return 78
    print(f"✅ 已把 {sha} 写进 GIF comment（{before} → {after} B，差 {after - before} B）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
