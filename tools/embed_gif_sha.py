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

⇒ 修法：把 sha 写进 GIF 本身（GIF89a comment extension 块
   `0x21 0xFE <len> <data> 0x00`，PIL 读成 `info['comment']`）。

   ⚠⚠ **但这只是把「旁路文本」换成「产物内的字节」，不是存证**（红队第六/七轮）：
   它是**流程门**。证明的命题只有「有人把这 16 个字符写进了这个 GIF」，
   **不**证明画面是用当前语料录的 —— 拿旧 GIF 只跑本脚本、或用 PIL 造张黑图嵌上 sha，
   门都会绿。我第六轮写过的「手写文本这条路不存在 / 换 GIF 就换掉了证据」**是错的**，
   已在这里改正。

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


def frame_count(data: bytes) -> int:
    """从字节流数图像描述符（0x2C）—— 用来断言「嵌入没破坏帧结构」。"""
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return getattr(im, "n_frames", 1)
    except Exception:
        return -1


def _after_screen_descriptor(data: bytes) -> int:
    """返回「逻辑屏幕描述符（+ 全局调色板）之后」的偏移 = 可以插块的位置。"""
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise SystemExit(f"不是 GIF（header={data[:6]!r}）")
    if len(data) < 13:
        raise SystemExit("GIF 太短，读不到逻辑屏幕描述符")
    flags = data[10]
    gct = 3 * (2 ** ((flags & 0x07) + 1)) if flags & 0x80 else 0
    return 13 + gct


def iter_blocks(data: bytes):
    """**真正的 GIF 块解析器**：按块走，不按字节找。

    ⚠ 第六轮红队抓到的真 bug：上一版是 `while` 里逐字节找 `0x21 0xFE`，
      碰巧图形控制扩展（GCE）的**延时字节**是 `0xFE21` 时，就会从延时的高字节
      开始「找到」一个假 comment 起点，然后按子块长度跳 ⇒ **静默删掉 3 个字节**，
      实测把 **3 帧的 GIF 变成 2 帧，而 CLI 仍然打印 ✅ exit 0**
      （因为它读回只验 comment，不验帧数）。
    ⇒ 正确做法是从逻辑屏幕描述符之后开始**逐块解析**：
      0x21 = 扩展（下一个字节是标签），0x2C = 图像描述符，0x3B = 文件结束。
      只有标签为 0xFE 的扩展才是 comment。

    yield (kind, start, end)；kind ∈ {"comment", "other"}；解析到第一张图像就停。
    """
    pos = _after_screen_descriptor(data)
    n = len(data)
    while pos < n:
        b = data[pos]
        if b == 0x3B:  # trailer
            return
        if b == 0x2C:  # 图像描述符之后的内容一律不动
            return
        if b != 0x21:
            # 不该出现的字节：保守停手（不猜）
            return
        if pos + 1 >= n:
            return
        label = data[pos + 1]
        start = pos
        pos += 2
        if label == 0xF9 and pos < n:
            # GCE：块大小 + 数据 + 终止符（大小是自描述的，不猜）
            size = data[pos]
            pos += 1 + size
            if pos < n and data[pos] == 0x00:
                pos += 1
        else:
            # 其余扩展（含 comment 0xFE、应用 0xFF 等）都是「长度 + 数据」子块串
            while pos < n and data[pos] != 0x00:
                pos += 1 + data[pos]
            pos += 1  # 跳过终止 0x00
        if label == 0xFE:
            yield ("comment", start, min(pos, n))


def strip_comments(data: bytes) -> bytes:
    """剥掉已有的 comment extension 块（幂等：重复嵌入不会越积越多）。

    ⚠ **只剥 comment**，其他块原样保留；解析到第一张图像描述符就停手。
    """
    prune = [(s, e) for kind, s, e in iter_blocks(data) if kind == "comment"]
    if not prune:
        return data
    out = bytearray()
    last = 0
    for s, e in prune:
        out += data[last:s]
        last = e
    out += data[last:]
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
    from PIL import Image

    ap = argparse.ArgumentParser(description="把语料 sha 写进 GIF 的 comment 块")
    ap.add_argument("gif", type=Path)
    ap.add_argument("--sha", default=None, help="默认取当前语料 sha256[:16]")
    args = ap.parse_args()
    sha = args.sha or hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:16]
    if not args.gif.exists():
        print(f"❌ 没有这个文件：{args.gif}", file=sys.stderr)
        return 78
    # ⚠ 红队第七轮：文件被弄坏时原来是 traceback（exit 1），走不到 `❌ 78` ——
    #   fail-loud 但不体面。⇒ 统一成 78。
    try:
        with Image.open(args.gif) as _probe:
            getattr(_probe, "n_frames", 1)
    except Exception as e:
        print(f"❌ 不是可读的 GIF（{type(e).__name__}）⇒ 不嵌入", file=sys.stderr)
        return 78
    before = args.gif.stat().st_size
    before_bytes = args.gif.read_bytes()
    embed(args.gif, sha)
    after = args.gif.stat().st_size

    # 立刻读回来验证（不信任自己写的）
    from PIL import Image

    # ⚠ 第六轮：**帧数必须不变**（上一版把 3 帧改成 2 帧还报 ✅）
    before_frames = frame_count(before_bytes)
    with Image.open(args.gif) as im:
        after_frames = getattr(im, "n_frames", 1)
    if before_frames != after_frames:
        print(
            f"❌ 帧数变了：{before_frames} → {after_frames}（嵌 comment 破坏了文件）",
            file=sys.stderr,
        )
        return 78

    got = (Image.open(args.gif).info or {}).get("comment")
    got = got.decode("ascii", "replace") if isinstance(got, bytes) else got
    if got != sha:
        print(f"❌ 嵌入后读回是 {got!r}，期望 {sha!r}", file=sys.stderr)
        return 78
    print(f"✅ 已把 {sha} 写进 GIF comment（{before} → {after} B，差 {after - before} B）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
