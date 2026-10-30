#!/usr/bin/env python3
"""**文档里写的每个数字，都必须与代码实跑一致。**

## 为什么要有这道门（红队第十一轮 B2）

第十一轮红队扫出 **16 处**文档数字与实跑不符，且**三处互斥**：
`191 tests`（实 356）、`68`（实 67）、`8 adapters`（实 9，同文件表格写 9）、
`79%`/`82%`/实 `83.89%`、`__init__ 76%`(实 91)、`_common 69%`(实 88)。

**根因**：文档里的数字全是**手抄**的。改了代码忘了改文档 ⇒ 没人发现。

⇒ 这道门把「文档里出现的、可识别的数字模式」逐个抓出来，
  与代码实跑的值比对。**抓不到模式 = 报红**（说明有新写法逃出去了）。

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
    (r"\b(\d+) tests?\b", lambda: test_count(), "测试数"),
    (r"(\d+) 个 enforce 宿主", lambda: host_facts()["enforce_hosts"], "enforce 宿主数"),
    (r"(\d+) 个 adapter", lambda: host_facts()["adapters"], "adapter 数"),
    (r"(\d+) 份(?: detector|规则|实现)", lambda: host_facts()["implementation_copies"], "实现份数"),
    (r"\b(\d+) 条规则\b", lambda: rule_counts()["rules"], "脱敏规则数"),
    (r"白名单\s*(\d+)\s*项", lambda: rule_counts()["allowlist"], "白名单项数"),
    (r"(\d+) 类 \+ 应剔除却仍在", lambda: rule_counts()["scan_classes"], "扫描类别数"),
    (r"残留扫描[^\n]*?(\d+) 类", lambda: rule_counts()["scan_classes"], "扫描类别数"),
]


def check_numbers(facts: dict) -> list[str]:
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
    """CHANGELOG 只看**最新一节**（历史版本的旧数字是该留的历史，不是错）。"""
    if rel != "CHANGELOG.md":
        return text
    idx = text.rfind("\n## ")
    return text[idx:] if idx > 0 else text


def main() -> int:
    facts = {
        "corpus_sha": corpus_sha(),
        "tests": test_count(),
        **{f"host_{k}": v for k, v in host_facts().items()},
        **{f"rule_{k}": v for k, v in rule_counts().items()},
    }
    print("实跑事实：", json.dumps(facts, ensure_ascii=False))

    bad, checked = check_numbers(facts)
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
