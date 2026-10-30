"""`check_published_sha.py` 与 `embed_gif_sha.py` 的回归测试。

红队第六轮指出：这两个脚本**零测试覆盖**，而它们恰好是发布前最后一道门。
本文件把红队用过的那几招固化成测试 —— 门必须「该红时红、该绿时绿」。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GIF = ROOT / "deliverables" / "demos" / "demo-all.gif"
FIXTURE = ROOT / "tests" / "fixtures" / "real_turns.jsonl"
EMBED = ROOT / "tools" / "embed_gif_sha.py"


def cur_sha() -> str:
    return hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:16]


class TestEmbed(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.gif = self.tmp / "x.gif"
        shutil.copy2(GIF, self.gif)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(EMBED), str(self.gif)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def test_embeds_and_reads_back(self) -> None:
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        with Image.open(self.gif) as im:
            got = im.info.get("comment").decode()
            frames = getattr(im, "n_frames", 1)
            # comment 格式是 `sha:frames`（帧数一起记，防「切到前 N 帧」）
            self.assertEqual(got, f"{cur_sha()}:{frames}")

    def test_pixels_unchanged(self) -> None:
        """只插块，不碰像素 —— 逐帧比对必须全同。"""
        with Image.open(self.gif) as a:
            before = [
                a.seek(i) or a.convert("RGB").copy() for i in range(getattr(a, "n_frames", 1))
            ]
        self._run()
        with Image.open(self.gif) as b:
            after = [b.seek(i) or b.convert("RGB").copy() for i in range(getattr(b, "n_frames", 1))]
        self.assertEqual(len(before), len(after))
        for x, y in zip(before, after, strict=True):
            self.assertIsNone(
                __import__("PIL.ImageChops", fromlist=["x"]).difference(x, y).getbbox(),
                "嵌入 comment 改变了画面",
            )

    def test_idempotent(self) -> None:
        self._run()
        size1 = self.gif.stat().st_size
        self._run()
        self.assertEqual(size1, self.gif.stat().st_size, "重复嵌入改变了文件大小")

    def test_gce_delay_false_positive(self) -> None:
        """红队第六轮的真 bug：GCE 延时字节恰为 `0x21 0xFE` 时，逐字节找标记会删错字节。

        上一版实测把 477785 B 的 GIF 删成 474109 B（少 3676 B）⇒ 整个文件读不出来。
        """
        data = bytearray(self.gif.read_bytes())
        k = data.find(b"\x21\xf9\x04")
        self.assertGreater(k, 0)
        data[k + 4] = 0x21
        data[k + 5] = 0xFE  # delay = 0xFE21 ⇒ 字节流里出现 "21 FE"
        self.gif.write_bytes(bytes(data))
        with Image.open(self.gif) as im:
            frames = getattr(im, "n_frames", 1)

        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        with Image.open(self.gif) as im:
            self.assertEqual(getattr(im, "n_frames", 1), frames, "嵌 comment 破坏了帧结构")


class TestGate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{ROOT}", str(self.repo)],
            capture_output=True,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _gate(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "scripts/check_published_sha.py"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

    def test_green_in_fresh_clone(self) -> None:
        """红队第五轮阻断项：mtime 在 clone 里等于 index 序 ⇒ 旧版恒红。"""
        if not (self.repo / "scripts" / "check_published_sha.py").exists():
            self.skipTest("clone 失败")
        r = self._gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_red_on_empty_gif(self) -> None:
        gif = self.repo / "deliverables" / "demos" / "demo-all.gif"
        gif.write_bytes(b"")
        self.assertEqual(self._gate().returncode, 78)

    def test_red_when_comment_stripped(self) -> None:
        gif = self.repo / "deliverables" / "demos" / "demo-all.gif"
        sys.path.insert(0, str(ROOT / "tools"))
        import embed_gif_sha as eg

        gif.write_bytes(eg.strip_comments(gif.read_bytes()))
        self.assertEqual(self._gate().returncode, 78)

    def test_red_when_truncated_to_two_frames(self) -> None:
        """红队第八轮：按块结构切到「刚好前 2 帧 + 0x3B」的文件曾能过门（2 帧）。

        ⇒ comment 里记了录制时的帧数（`sha:frames`），帧数不符 ⇒ 红。
        """
        gif = self.repo / "deliverables" / "demos" / "demo-all.gif"
        data = gif.read_bytes()
        # 找到第三个图像描述符（0x2C）的位置，切掉它及之后，换成 trailer
        pos = 13 + (3 * (2 ** ((data[10] & 0x07) + 1)) if data[10] & 0x80 else 0)
        seen = 0
        while pos < len(data):
            if data[pos] == 0x2C:
                seen += 1
                if seen == 3:  # 第 3 帧开始 ⇒ 切到这里
                    break
            pos += 1
        gif.write_bytes(data[:pos] + b"\x3b")
        self.assertEqual(self._gate().returncode, 78)

    def test_red_when_corpus_changes(self) -> None:
        f = self.repo / "tests" / "fixtures" / "real_turns.jsonl"
        f.write_bytes(f.read_bytes() + b'\n{"id": 9999, "text": "x"}\n')
        self.assertEqual(self._gate().returncode, 78)


if __name__ == "__main__":
    unittest.main()
