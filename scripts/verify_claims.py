#!/usr/bin/env python3
"""独立复算 —— 不信任脚本的输出，自己算一遍。

**为什么需要这个文件**：demo 报的数字是要在面试现场被追问的。
**如果这些数字只能由那个脚本自己算出来，就等于没验证** ——
脚本里的常量、过滤条件、版本选择只要有一处漂移，报出去的数字就是假的。

这个文件**从零重写**一遍逻辑，不 import 上面那两个脚本 ⇒ 它算出来的数
才独立。两者对不上 ⇒ 有一处错了。

**⚠⚠ R311 实测踩过的坑（这个文件的存在理由）**

1. **写死的期望值会随语料变化而失效。**
   实测：语料从 1430 条裁剪脱敏到 1348 条后，
   「被 boundary 提前放行」506 → 472、nudge 42 → 40、真正误放行 11 → 10。
   ⇒ **数字由语料决定，不由我写死。**
   ⇒ 本文件**不写死任何期望值**：它算当前语料的实测值，并检查
     ① 「不丢失」硬红线 ② 与另外两个脚本的输出逐项一致 ③ 打印语料 sha256。
   ⇒ **要报什么数字，请以本文件的输出为准**（它打印了当前语料 sha256）。

2. **交叉喂错配置会产出「看起来很干净的假数字」。**
   实测：把**新版**的 cfg 传给**旧版** detector ⇒ 新版 cfg 的 exclusions 里
   没有 boundary 键 ⇒ 旧版拿不到词表 ⇒ 跑出「nudge 42 / boundary 放行 0」。
   ⇒ **每版必须用它自己的 `default_config()`。**

用法：
  python3 scripts/verify_claims.py
  python3 scripts/verify_claims.py --json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "real_turns.jsonl"
OLD_TAG = "v1.4.1"
NEW_TAG = "1.5.0"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def corpus_sha() -> str:
    return hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not FIXTURE.exists():
        print(f"缺语料 {FIXTURE}", file=sys.stderr)
        return 2

    sha = corpus_sha()
    texts = [
        json.loads(_ln)["text"]
        for _ln in FIXTURE.read_text(encoding="utf-8").splitlines()
        if _ln.strip()
    ]
    total = len(texts)

    # ── 新版（用它自己的 cfg）──
    new = load(ROOT / "detector.py", "det_new")
    cfg = new.default_config()
    cn = Counter(new.detect(t, 0, cfg) for t in texts)
    new_nudged = sum(v for k, v in cn.items() if k[0])
    new_boundary = cn.get((False, "boundary"), 0) + cn.get((False, "boundary_words"), 0)

    # ── 旧版（**必须用它自己的 cfg**）──
    src = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{OLD_TAG}:detector.py"],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    if not src.strip():
        print(f"取不到 {OLD_TAG}", file=sys.stderr)
        return 2
    tmp = Path(tempfile.mkdtemp()) / "old.py"
    tmp.write_text(src, encoding="utf-8")
    old = load(tmp, "det_old")
    old_cfg = old.default_config()  # ⚠⚠ 不能用新版的 cfg（见文件头第 2 条）
    co = Counter(old.detect(t, 0, old_cfg) for t in texts)
    old_nudged = sum(v for k, v in co.items() if k[0])
    old_boundary = co.get((False, "boundary"), 0) + co.get((False, "boundary_words"), 0)

    recovered = new_nudged - old_nudged

    # ── 硬红线：不丢失（旧版触发的，新版也必须触发）──
    lost = sum(1 for t in texts if old.detect(t, 0, old_cfg)[0] and not new.detect(t, 0, cfg)[0])

    # ── 三脚本交叉核对（E2）──
    #   replay_real.py / replay_regression.py / verify_claims.py 各自独立实现。
    #   红队 E2 实测：语料一换，写死字面量的脚本会静默印出过期数字，
    #   而另一个脚本算出另一个数 ⇒ **当场互相矛盾**。
    #   ⇒ 这里把三者钉在一起，任何一个对不上就 exit 78。
    cross_scripts: list[tuple[str, bool, str]] = []
    for script in ("replay_real.py", "replay_regression.py"):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), "--json"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(ROOT),
        )
        try:
            j = json.loads(r.stdout)
            if script == "replay_real.py":
                ok = (
                    j["corpus_size"] == total
                    and j["nudged"] == new_nudged
                    and j["released"] == total - new_nudged
                    and j["old"]["boundary_released"] == old_boundary
                    and j["old"]["truly_missed"] == recovered
                    and j.get("corpus_sha256_16") == sha
                )
                detail = (
                    f"nudged={j['nudged']} released={j['released']} "
                    f"old_boundary={j['old']['boundary_released']} "
                    f"truly_missed={j['old']['truly_missed']}"
                )
            else:
                ok = (
                    j["corpus_size"] == total
                    and j["old"]["nudged"] == old_nudged
                    and j["old"]["boundary_released"] == old_boundary
                    and j["new"]["nudged"] == new_nudged
                    and j["new"]["boundary_released"] == new_boundary
                    and j["true_false_negatives"] == recovered
                    and j.get("corpus_sha256_16") == sha
                )
                detail = (
                    f"old={j['old']['nudged']}/{j['old']['boundary_released']} "
                    f"new={j['new']['nudged']}/{j['new']['boundary_released']}"
                )
            cross_scripts.append((script, ok, detail))
        except Exception as e:
            cross_scripts.append((script, False, f"解析失败: {e}"))

    all_ok = lost == 0 and all(ok for _, ok, _ in cross_scripts)

    result = {
        "corpus_sha256_16": sha,
        "corpus_size": total,
        "old": {"tag": OLD_TAG, "nudged": old_nudged, "boundary_released": old_boundary},
        "new": {"tag": NEW_TAG, "nudged": new_nudged, "boundary_released": new_boundary},
        "recovered": recovered,
        "true_false_negatives": recovered,
        "lost": lost,
        "both_released_and_fine": old_boundary - recovered,
        "cross_scripts": [{"script": s, "match": ok, "detail": d} for s, ok, d in cross_scripts],
        "note": (
            f"{old_boundary} = 被旧版**提前放行**的总数；其中只有 {recovered} 条本来该 nudge ⇒ "
            f"真正的误放行 = {recovered} 条（{recovered / total * 100:.1f}%）；"
            f"其余 {old_boundary - recovered} 条两版都放行（不算误判）。"
        ),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all_ok else 78

    print("=" * 72)
    print("  独立复算 —— 不信任其它脚本，自己算一遍")
    print("=" * 72)
    print(f"  语料 {total} 条   sha256[:16] = {sha}")
    print()
    print(f"  {OLD_TAG:>10}（带 boundary 词表）")
    print(f"  {'真的该 nudge':>16} : {old_nudged}")
    print(f"  {'被提前放行':>16} : {old_boundary}  ({old_boundary / total * 100:.1f}%)")
    print()
    print(f"  {NEW_TAG:>10}（删表）")
    print(f"  {'真的该 nudge':>16} : {new_nudged}")
    print(f"  {'被提前放行':>16} : {new_boundary}  ({new_boundary / total * 100:.1f}%)")
    print()
    print(f"  ⇒ 找回 {recovered} 条；不丢失 {lost} 条")
    print(f"  ⇒ **真正的误放行 = {recovered} 条（{recovered / total * 100:.1f}%）**")
    print(f"     其余 {old_boundary - recovered} 条**两版都放行**（本来就没有触发信号，不算误判）")
    print()
    print(f"  {'✅' if lost == 0 else '❌'} 硬红线「不丢失」：{lost} 条")
    for name, ok_, detail in cross_scripts:
        print(f"  {'✅' if ok_ else '❌'} 三脚本一致 [{name}]: {detail}")
    print()
    print("  ⚠ 数字由**当前语料**决定。语料一改（裁剪/脱敏/剔行），数字就变")
    print("    （实测：1430→1348 条 ⇒ 506→472、42→40、11→10）")
    print("    ⇒ **报数请以本文件输出为准，并把 sha256 一起报出来**")
    print()
    print("=" * 72)
    print("  复算：python3 scripts/verify_claims.py")
    print("=" * 72)
    return 0 if all_ok else 78


if __name__ == "__main__":
    sys.exit(main())
