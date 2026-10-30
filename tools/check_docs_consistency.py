#!/usr/bin/env python3
"""**文档里写的每个数字，都必须与代码实跑一致。**

## 为什么要有这道门（红队第十一轮 B2）

第十一轮红队扫出 **16 处**文档数字与实跑不符，且**三处互斥**：
`191 tests`（实 356）、`68`（实 67）、`8 adapters`（实 9，同文件表格写 9）、
`79%`/`82%`/实 `83.89%`、`__init__ 76%`(实 91)、`_common 69%`(实 88)。

**根因**：文档里的数字全是**手抄**的。改了代码忘了改文档 ⇒ 没人发现。

⇒ 这道门把「文档里出现的、可识别的数字模式」逐个抓出来，
  与代码实跑的值比对。

⚠ **诚实边界（红队第十二轮指出）**：本门**做不到**「抓不到模式就报红」——
  它只能校验写进 `PATTERNS` 的那些写法；**没登记的模式（覆盖率、语料条数、
  英文 `9 adapters`、表格里的数字）它完全看不见**。别把它当"文档全对"的证明。

    python3 tools/check_docs_consistency.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = [
    "README.md",
    "README.zh-CN.md",
    "CHANGELOG.md",
    "tests/fixtures/README.md",
    "pyproject.toml",
]

# ⚠ **历史段落豁免**（红队第十一轮：CHANGELOG 里的「339 tests」是**历史记录**，
#   描述的是那一版发布时的测试数 —— 它就该是旧值，改了反而是篡改历史）。
#   ⇒ 只校验「当前口径」相关段落；历史条目不参与。
#   判据：CHANGELOG 里位于 `<!-- BEGIN GENERATED: metrics -->` 之后、
#   或标题含「当前」/「now」的段落。
HISTORY_MARKERS = ("<!-- BEGIN GENERATED: metrics -->",)

# ── 实跑取值（每个都是「怎么算出来的」写清楚，便于复核）──


def corpus_sha() -> str:
    import hashlib

    f = ROOT / "tests" / "fixtures" / "real_turns.jsonl"
    return hashlib.sha256(f.read_bytes()).hexdigest()[:16]


def test_count() -> int:
    r = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    m = re.search(r"Ran (\d+) tests?", r.stderr + r.stdout)
    return int(m.group(1)) if m else -1


def host_facts() -> dict:
    r = subprocess.run(
        [sys.executable, "scripts/check_hosts.py", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    j = json.loads(r.stdout)
    return {
        "adapters": j["adapters"],
        "enforce_hosts": j.get("enforce_hosts", 0),
        "implementation_copies": j.get("implementation_copies", 0),
        "adapter_lines": j["adapter_layer_lines"]["total"],
        "shared_lines": j["adapter_layer_lines"]["shared_module_lines"],
    }


def rule_counts() -> dict:
    import importlib.util

    spec = importlib.util.spec_from_file_location("sc", ROOT / "tools" / "sanitize_corpus.py")
    # mypy: spec_from_file_location 的返回类型是 Optional ⇒ 显式断言，
    # 顺便在路径写错时给出明确报错（而不是 NoneType 堆栈）。
    assert spec is not None and spec.loader is not None, "取不到 sanitize_corpus.py"
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return {
        "rules": len(m.SUB_RULES),
        "scan_classes": len(m.RESIDUAL_PATTERNS),
        "allowlist": len(m.ALLOWED_TOKENS),
    }


# ── 模式 → 实跑值 ──
#   每条：(正则, 取值函数, 说明)  —— 正则必须**只匹配该事实**，
#   否则会误伤别的数字。匹配到但值对不上 ⇒ 报红。
PATTERNS = [
    # ⚠ 「N tests」**不在**这里：它必须按套件分派，见 check_test_counts()。
    #   红队第十二轮 B2：原来一条 `\b(\d+) tests?\b` 把 vitest 的 74 和
    #   unittest 的 356 当成同一个事实 ⇒ 门**拒绝真值**、**锁死错数**。
    (r"(\d+) 个 enforce 宿主", lambda: host_facts()["enforce_hosts"], "enforce 宿主数"),
    (r"(\d+) 个 adapter", lambda: host_facts()["adapters"], "adapter 数"),
    (r"(\d+) 份(?: detector|规则|实现)", lambda: host_facts()["implementation_copies"], "实现份数"),
    (r"\b(\d+) 条规则\b", lambda: rule_counts()["rules"], "脱敏规则数"),
    (r"白名单\s*(\d+)\s*项", lambda: rule_counts()["allowlist"], "白名单项数"),
    (r"(\d+) 类 \+ 应剔除却仍在", lambda: rule_counts()["scan_classes"], "扫描类别数"),
    (r"残留扫描[^\n]*?(\d+) 类", lambda: rule_counts()["scan_classes"], "扫描类别数"),
]


def check_numbers(facts: dict) -> tuple[list[str], int]:
    """数字类事实：测试数 / 宿主数 / 规则数 / 白名单项数 ……"""
    bad = []
    checked = 0
    for rel in DOCS:
        f = ROOT / rel
        if not f.exists():
            continue
        text = _current_section(rel, f.read_text(encoding="utf-8"))
        for pat, getter, label in PATTERNS:
            for m in re.finditer(pat, text):
                checked += 1
                want, got = getter(), int(m.group(1))
                if got != want:
                    line = text[: m.start()].count("\n") + 1
                    bad.append(
                        f"{rel}:{line} 「{label}」写的是 {got}，实跑是 {want}"
                        f" —— 「{m.group(0)[:40]}」"
                    )
    return bad, checked


def check_test_counts(rel: str, text: str) -> tuple[list[str], int]:
    """「N tests」按**同一行里的命令**分派到对应套件。

    判据（红队第十二轮 B2 后）：
    | 这一行含 | 期望值 |
    |---|---|
    | `unittest` / `pytest` | python 套件实跑数 |
    | `vitest` / `npm test` | **不许写绝对条数** —— CI 里跑不了 node 套件，
    |                       写死就是"发布一个无法校验的数"⇒ 报红 |
    | 都没有 | 歧义 ⇒ 报红（让它写清是哪个套件） |
    """
    bad, checked = [], 0
    py = test_count()
    for ln_no, line in enumerate(text.splitlines(), 1):
        if not re.search(r"\d+\s*tests?\b", line, re.I):
            continue
        low = line.lower()
        if "vitest" in low or "npm test" in low or "npm run test" in low:
            bad.append(
                f"{rel}:{ln_no} node 套件的条数**不许写死**（CI 里无法校验）："
                f"「{line.strip()[:70]}」 ⇒ 改成「条数以 npm test 输出为准」"
            )
            continue
        if "unittest" in low or "pytest" in low:
            for m in re.finditer(r"(\d+)\s*tests?\b", line, re.I):
                checked += 1
                if int(m.group(1)) != py:
                    bad.append(f"{rel}:{ln_no} python 套件写的是 {m.group(1)}，实跑是 {py}")
            continue
        bad.append(
            f"{rel}:{ln_no} 写了测试数但看不出是哪个套件（歧义即红）：「{line.strip()[:70]}」"
        )
    return bad, checked


def check_shas(facts: dict) -> list[str]:
    """语料 sha 的引用必须等于当前语料。"""
    bad = []
    sha = facts["corpus_sha"]
    for rel in DOCS:
        f = ROOT / rel
        if not f.exists():
            continue
        scan = _current_section(rel, f.read_text(encoding="utf-8"))
        for m in re.finditer(r"\b([0-9a-f]{16})\b", scan):
            if m.group(1) != sha:
                bad.append(f"{rel} sha {m.group(1)} ≠ 当前语料 {sha}")
    return bad


def _current_section(rel: str, text: str) -> str:
    """CHANGELOG 只看**最新一节**（历史版本的旧数字是该留的历史，不是错）。

    ⚠ 红队第十二轮抓到：原来用 `rfind` ⇒ 取到的是**最后一个** `## ` 段 =
      **最旧**那一节（CHANGELOG 是倒序，最新在上）⇒ 最新一节里的错数
      和悬空 sha 全被豁免，门看到的是"历史上某一版"。
      ⇒ 用 `find` 取第一节，并截到下一节之前。
    """
    if rel != "CHANGELOG.md":
        return text
    start = text.find("\n## ")
    if start < 0:
        return text
    nxt = text.find("\n## ", start + 1)
    return text[start:nxt] if nxt > 0 else text[start:]


def main() -> int:
    facts = {
        "corpus_sha": corpus_sha(),
        "tests": test_count(),
        **{f"host_{k}": v for k, v in host_facts().items()},
        **{f"rule_{k}": v for k, v in rule_counts().items()},
    }
    print("实跑事实：", json.dumps(facts, ensure_ascii=False))

    bad, checked = check_numbers(facts)
    for rel in DOCS:
        f = ROOT / rel
        if not f.exists():
            continue
        _b, _c = check_test_counts(rel, _current_section(rel, f.read_text(encoding="utf-8")))
        bad += _b
        checked += _c
    bad += check_shas(facts)
    print(f"\n检查了 {checked} 处数字 + 全部 sha 引用")
    if bad:
        print("❌ 文档数字与代码实跑不一致：")
        for b in bad:
            print("   -", b)
        return 78
    print("✅ 文档里所有可识别的数字与代码实跑一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
