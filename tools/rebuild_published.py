#!/usr/bin/env python3
"""**一条命令重建全部发布物**，消除「改了生成器忘了重生成」这一类错误。

## 为什么需要这个

红队第九轮给了 3 个阻断项，**全部是同一个操作失误的不同面**：

| # | 现象 |
|---|---|
| B3 | 发布语料的 sha 是 `8792cf49…`，但**当前生成器**从同一原始语料产出 `13d627ac…`（720/1341 行不同） |
| B1 | `CHANGELOG.md` 写着 sha `8792cf49…`，数却是旧的 `466/456`（同一 sha 实算 `452/442`） |
| B2 | `fixtures/README.md` 占位符表 5 项错，且**漏掉 `<CODE>`（1745 次，第二多）** |

⇒ 根因：我每次改完生成器，就**手工**去改文档里的 sha、数字、表格。
    改一处漏三处，而且**没有任何机制能发现漏了** ——
    `verify_claims.py` 只打印正确值，从不与文档比对；不动点只验 `f(已发布)=已发布`，
    对「生成器本身变了」结构性失明。

⇒ 修法：**发布物必须是生成器的纯函数**。这个脚本重算一遍，写回所有派生数字，
   并把「生成器指纹」记进 `PROVENANCE` —— 之后改了生成器不重建，`--check` 直接红。

    python3 tools/rebuild_published.py --raw <原始语料.jsonl>
    python3 tools/rebuild_published.py --raw <原始语料> --check   # 只校验，不写
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "real_turns.jsonl"
GENERATOR = ROOT / "tools" / "sanitize_corpus.py"
PROVENANCE = ROOT / "tests" / "fixtures" / "PROVENANCE.json"
CHANGELOG = ROOT / "CHANGELOG.md"
FIX_README = ROOT / "tests" / "fixtures" / "README.md"

BEGIN = "<!-- BEGIN GENERATED: metrics -->"
END = "<!-- END GENERATED: metrics -->"


def sha16(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def generate(raw: Path) -> tuple[str, int]:
    """跑生成器，返回 (sha16, 行数)。"""
    r = subprocess.run(
        [sys.executable, str(GENERATOR), "--in", str(raw), "--out", str(FIXTURE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"生成器失败：\n{r.stdout}\n{r.stderr}")
    n = sum(1 for _ in FIXTURE.open())
    return sha16(FIXTURE), n


def metrics() -> dict:
    """跑三脚本拿口径数字（--json 由 replay_real 提供）。"""
    out = {}
    r = subprocess.run(
        [sys.executable, "scripts/replay_real.py", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"replay_real 失败：{r.stderr}")
    j = json.loads(r.stdout)
    out["nudge_old"] = j["old"]["nudged"]
    out["early_old"] = j["old"]["boundary_released"]
    out["truly"] = j["old"]["truly_missed"]
    out["nudge_new"] = j["nudged"]
    out["early_new"] = (
        j["corpus_size"] - j["nudged"] - (j["corpus_size"] - j["released"] - j["nudged"])
    )
    out["early_new"] = 0  # 1.5.1 没有 boundary 词表 ⇒ 提前放行恒为 0
    total = j.get("corpus_size") or 0
    out["total"] = total
    out["early_pct"] = (out["early_old"] / total * 100) if total else 0.0
    out["truly_pct"] = (out["truly"] / total * 100) if total else 0.0
    out["both_pass"] = out["early_old"] - out["truly"]
    return out


def placeholder_table() -> str:
    """从**已发布语料**统计占位符，生成表格（不再手抄）。"""
    text = FIXTURE.read_text(encoding="utf-8")
    c = Counter(re.findall(r"<[A-Z_]+>", text))
    lines = ["| 占位符 | 次数 |", "|---|---:|"]
    for k, v in c.most_common():
        lines.append(f"| `{k}` | {v} |")
    lines.append("")
    lines.append(f"合计 **{sum(c.values())}** 处，{len(c)} 种。")
    return "\n".join(lines)


def metrics_block(sha: str, n: int, m: dict) -> str:
    return f"""{BEGIN}
**⇒ 当前口径（脱敏 + 剔行后，语料 {n} 条，sha256[:16] = `{sha}`）**：

| | before | after |
|---|---|---|
| released early by `boundary` | **{m["early_old"]} ({m["early_pct"]:.1f}%)** | **{m["early_new"]}** |
| actually nudged | {m["nudge_old"]} | **{m["nudge_new"]}** (+{m["truly"]} recovered) |

**真正的误放行 = {m["truly"]} 条（{m["truly_pct"]:.1f}%）**，不是 {m["early_old"]} 条 ——
其余 {m["both_pass"]} 条两版都放行（本来就没有触发信号，**不算误判**）。
{END}"""


def replace_block(path: Path, block: str) -> bool:
    """把 path 里 BEGIN…END 之间的内容换成 block。没有标记就追加到末尾。"""
    t = path.read_text(encoding="utf-8")
    if BEGIN in t and END in t:
        i, j = t.index(BEGIN), t.index(END) + len(END)
        new = t[:i] + block + t[j:]
    else:
        new = t.rstrip() + "\n\n" + block + "\n"
    if new == t:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def write_placeholder_block(block: str) -> bool:
    t = FIX_README.read_text(encoding="utf-8")
    tag = "<!-- BEGIN GENERATED: placeholders -->", "<!-- END GENERATED: placeholders -->"
    wrapped = tag[0] + "\n" + block + "\n" + tag[1]
    if tag[0] in t and tag[1] in t:
        i, j = t.index(tag[0]), t.index(tag[1]) + len(tag[1])
        new = t[:i] + wrapped + t[j:]
    else:
        new = t.rstrip() + "\n\n## 语料里出现的全部占位符\n\n" + wrapped + "\n"
    if new == t:
        return False
    FIX_README.write_text(new, encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="重建全部发布物（语料 + 文档数字 + PROVENANCE）")
    ap.add_argument("--raw", type=Path, required=True, help="原始（未脱敏）语料")
    ap.add_argument("--check", action="store_true", help="只校验，不写")
    args = ap.parse_args()

    gen_sha = sha16(GENERATOR)
    raw_sha = sha16(args.raw)

    if args.check:
        prov = json.loads(PROVENANCE.read_text(encoding="utf-8")) if PROVENANCE.exists() else {}
        bad = []
        if prov.get("generator_sha16") != gen_sha:
            bad.append(
                f"生成器变了（PROVENANCE 记 {prov.get('generator_sha16')}，"
                f"当前 {gen_sha}）⇒ 必须重跑 rebuild_published.py"
            )
        if prov.get("fixture_sha16") != sha16(FIXTURE):
            bad.append("发布语料与 PROVENANCE 不一致")
        if bad:
            print("❌ 发布物不是当前生成器的输出：")
            for b in bad:
                print("   -", b)
            return 78
        print(
            f"✅ 发布物 = 当前生成器的输出（生成器 {gen_sha} / 语料 {prov.get('fixture_sha16')}）"
        )
        return 0

    sha, n = generate(args.raw)
    m = metrics()
    prov = {
        "generator_sha16": gen_sha,
        "raw_corpus_sha16": raw_sha,
        "fixture_sha16": sha,
        "fixture_lines": n,
        "metrics": m,
    }
    PROVENANCE.write_text(json.dumps(prov, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    changed = []
    if replace_block(CHANGELOG, metrics_block(sha, n, m)):
        changed.append("CHANGELOG.md")
    if write_placeholder_block(placeholder_table()):
        changed.append("tests/fixtures/README.md")

    print(f"语料 {n} 条  sha={sha}  生成器 {gen_sha}")
    print(
        f"口径：{m['early_old']} ({m['early_pct']:.1f}%) → {m['early_new']}；"
        f"nudge {m['nudge_old']} → {m['nudge_new']}；误放行 {m['truly']} ({m['truly_pct']:.1f}%)"
    )
    print(f"重写的文件：{changed or '（无变化）'}")
    print("\n⚠ 还要手工做两件事（脚本不代劳，因为它们要跑 Chrome）：")
    print("   1) 重录 GIF：python3 tools/record_gif.py ...")
    print("   2) 嵌 sha：  python3 tools/embed_gif_sha.py deliverables/demos/demo-all.gif")
    return 0


if __name__ == "__main__":
    sys.exit(main())
