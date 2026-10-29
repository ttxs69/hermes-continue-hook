"""workflow 里的 shell 块必须是有效 bash —— 而且要能抓到「少一个引号」。

═══ 为什么有这条测试（2026-10-29 实测事故）═══

在 publish.yml 里加 npm 版本校验时，最后一行写成了：

    echo "npm $NPM_VER satisfies the 11.15.0 floor for 'npm stage publish'.

结尾少了一个双引号（被 Python 三引号字符串吃掉了）。

**所有既有检查都是绿的**：
- YAML 解析成功（它在块标量 `run: |` 里，YAML 不管 shell 语法）
- prettier --check 通过（prettier 只管 YAML 排版，不管 shell）
- ruff / mypy / 单测全过
- `./scripts/check.sh` rc=0

**但 CI 一跑就会炸**：bash 报 `unexpected EOF while looking for matching '"'`，
发布 job 直接失败 —— 而且是在 tag 已经推上去之后。

⇒ 这类 bug 的特征是「发布流水线自己的 gate 抓不到它」。
⇒ 所以必须有一道门直接把每个 `run:` 块喂给 `bash -n`。

判据：
    ① 每个 workflow 的每个 `run:` 块必须通过 `bash -n`（只查语法，不执行）
    ② 负对照：故意写一个少引号的块 ⇒ 这道门必须报错
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"


def _run_blocks(text: str) -> list[tuple[str, str]]:
    """粗提取 YAML 里 `run: |` / `run: >` 块的内容。

    不引入 yaml 依赖（本仓库的测试环境未必装了 pyyaml），
    用行扫描：找到 `run: |` 后，把后续缩进更深的行都算进这个块。
    """
    blocks: list[tuple[str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)-?\s*run:\s*[|>][-+]?\s*$", lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        body: list[str] = []
        j = i + 1
        while j < len(lines):
            ln = lines[j]
            if not ln.strip():
                body.append("")
                j += 1
                continue
            cur = len(ln) - len(ln.lstrip())
            if cur <= indent:
                break
            body.append(ln[indent + 2 :] if ln[indent:].startswith("  ") else ln.lstrip())
            j += 1
        blocks.append((f"line {i + 1}", "\n".join(body)))
        i = j
    return blocks


class TestWorkflowShellBlocksAreValidBash(unittest.TestCase):
    def test_every_run_block_parses_as_bash(self) -> None:
        failures: list[str] = []
        checked = 0
        for wf in sorted(WORKFLOWS.glob("*.yml")):
            for where, block in _run_blocks(wf.read_text(encoding="utf-8")):
                if not block.strip():
                    continue
                checked += 1
                proc = subprocess.run(
                    ["bash", "-n"], input=block, capture_output=True, text=True, timeout=30
                )
                if proc.returncode != 0:
                    failures.append(
                        f"{wf.name} {where}: bash -n says {proc.stderr.strip()[:200]}\n"
                        f"--- block ---\n{block[:400]}"
                    )
        self.assertTrue(checked > 0, "一个 run 块都没提取到 —— 提取逻辑坏了，不是 workflow 干净")
        self.assertEqual(
            failures, [], "这些 shell 块有语法错误，CI 里会炸：\n\n" + "\n\n".join(failures)
        )

    def test_the_extractor_actually_finds_blocks(self) -> None:
        """负对照：提取器必须真的找到块，否则上面那条是空跑。"""
        sample = "jobs:\n  x:\n    steps:\n      - run: |\n          echo one\n          echo two\n"
        blocks = _run_blocks(sample)
        self.assertEqual(len(blocks), 1)
        self.assertIn("echo one", blocks[0][1])
        self.assertIn("echo two", blocks[0][1])

    def test_a_block_with_an_unclosed_quote_is_caught(self) -> None:
        """负对照：少一个引号的块必须被判失败 —— 否则这道门不会关。"""
        bad = 'echo "unterminated string\n'
        proc = subprocess.run(["bash", "-n"], input=bad, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(proc.returncode, 0, "bash -n 竟然没抓到未闭合引号")
        good = 'echo "closed"\n'
        proc2 = subprocess.run(
            ["bash", "-n"], input=good, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc2.returncode, 0, "bash -n 对合法块报错")


if __name__ == "__main__":
    unittest.main(verbosity=2)
