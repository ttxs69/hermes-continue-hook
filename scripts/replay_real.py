#!/usr/bin/env python3
"""真实流量回放复算 —— 让「你的判断有数据支撑」当场可验。

**为什么有这个脚本（面试向）**

面试官问「你怎么知道你的判断是对的」，最能加分的回答不是数字，是
**当场把数字复算一遍**。这个脚本做**两件事**（⚠ 曾经在这里写了三件 —— 第 1、3 件它其实不做，
那是 docstring 与代码不一致，红队 R311 抓出来的，已改正）：

1. 在 **全部真实 turn-final 回复** 上跑当前 detector，给出触发/放行分布
2. **自己加载旧版 detector 复算一遍**，给出对照口径
   （`replay_regression.py` 独立做同一件事 ⇒ 两个脚本互为交叉验证）

⚠ **跑测试请用 `python3 -m unittest discover -s tests`（346 项）**，
   本脚本**不跑测试** —— 它只做流量回放。

**语料来源**：`tests/fixtures/real_turns.jsonl`
（真实 agent 收尾；只保留最后 2 段 = 检测器实际判定的对象；
手机/邮箱/路径/IP/日期/会话 ID/账号标识等 22 类残留扫描全为 0，
生成过程见 `tools/sanitize_corpus.py` —— 可重跑验证。）

⚠ **报数必须带 sha256**：语料一改（裁剪/脱敏/剔行）数字就变，
⇒ 本脚本打印 `语料 sha256[:16]`，照抄即可。**不要引用任何历史快照的数。**

用法:
  python3 scripts/replay_real.py
  python3 scripts/replay_real.py --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "real_turns.jsonl"
DETECTOR = ROOT / "detector.py"
OLD_TAG = "v1.4.1"


def corpus_sha() -> str:
    """语料指纹 —— **每个报数的地方都必须带上它**（R311 红队 E2）。

    语料一改（裁剪/脱敏/剔行），统计值就变。**数字随语料变，以脚本输出为准。**
    ⇒ 不带 sha 的数字 = 无法追溯 = 等于没报。
    """
    import hashlib

    return hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:16]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 {path}")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def fetch_old_detector() -> Path | None:
    """取旧版 detector —— 必须用它自己的 default_config()。

    ⚠ 新版 cfg 的 exclusions 里没有 boundary 键 ⇒ 拿新 cfg 喂旧 detector
    会让旧版拿不到词表 ⇒ 跑出「放行 0」的假数字（R311 实测踩过）。
    """
    import subprocess
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "detector_old.py"
    try:
        r = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{OLD_TAG}:detector.py"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    tmp.write_text(r.stdout, encoding="utf-8")
    return tmp


def load_detector():
    spec = importlib.util.spec_from_file_location("ch_detector", DETECTOR)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 {DETECTOR}")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_texts() -> list[str]:
    if not FIXTURE.exists():
        print(f"找不到语料：{FIXTURE}", file=sys.stderr)
        return []
    return [
        json.loads(line)["text"]
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    det = load_detector()
    cfg = det.default_config()
    texts = load_texts()
    if not texts:
        return 2

    counts = Counter(det.detect(t, 0, cfg) for t in texts)
    total = len(texts)
    nudged = sum(v for k, v in counts.items() if k[0])
    new_boundary = counts.get((False, "boundary"), 0) + counts.get((False, "boundary_words"), 0)

    # ── 旧版对照：自己算，不引用别的脚本的输出 ──
    old_path = fetch_old_detector()
    if old_path is None:
        print(f"取不到 {OLD_TAG} 的 detector（需要 git 历史）", file=sys.stderr)
        return 2
    old_det = load_module(old_path, "det_old_r")
    old_counts = Counter(old_det.detect(t, 0, old_det.default_config()) for t in texts)
    old_n = sum(v for k, v in old_counts.items() if k[0])
    old_boundary = old_counts.get((False, "boundary"), 0) + old_counts.get(
        (False, "boundary_words"), 0
    )
    truly = nudged - old_n

    result = {
        "corpus_size": total,
        "nudged": nudged,
        "released": total - nudged,
        "by_reason": {r: v for (ok, r), v in counts.items() if ok},
        # ⚠⚠ 旧版对比数字必须是**本脚本自己算的**（R311 红队 E2）
        #   原写法把三个数写死 —— 语料一换就静默印出过期数字。
        "corpus_sha256_16": corpus_sha(),
        "old": {
            "tag": OLD_TAG,
            "nudged": old_n,
            "boundary_released": old_boundary,
            "truly_missed": truly,
        },
        "note": (
            f"同一批语料（裁剪版，只留收尾段），{OLD_TAG} 靠 boundary 词表时"
            f"提前放行 {old_boundary} 条（{old_boundary / total * 100:.1f}%），"
            f"其中 {truly} 条本来该 nudge；1.5.0 删表后为 {new_boundary}。"
            f"⇒ 报口径，不只报数字。"
        ),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print("=" * 66)
    print(f"  真实流量回放 —— {total} 条 agent 收尾")
    print("=" * 66)
    print(f"  语料          : {total} 条")
    print(f"  真的该 nudge  : {nudged} 条")
    print(f"  放行          : {total - nudged} 条")
    print()
    print("  触发原因分布：")
    for reason, v in sorted(result["by_reason"].items(), key=lambda x: -x[1]):
        print(f"    {reason:<26} {v:>4}  ({v / total * 100:>5.1f}%)")
    print()
    print("  ⚠ 口径说明：")
    print(
        f"     同一批语料，{OLD_TAG} 靠 boundary 词表时**提前放行 {old_boundary} 条"
        f"（{old_boundary / total * 100:.1f}%），其中 {truly} 条本来该 nudge**；"
    )
    print(f"     1.5.0 删表后为 **{new_boundary}**。")
    print("     ⇒ 所以要报的是**口径**，不是单个数字。")
    print(f"     语料 sha256[:16] = {corpus_sha()}")
    print()
    print("=" * 66)
    print("  复算：python3 scripts/replay_real.py")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
