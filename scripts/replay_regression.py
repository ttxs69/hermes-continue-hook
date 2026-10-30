#!/usr/bin/env python3
"""误伤对比复算 —— 同一批语料，旧版（boundary 词表）vs 新版（1.5.1）。

**为什么有这个脚本（面试向 · 最强的一段）**

面试官问「误伤了怎么办？你测过吗」时，**最强的回答不是「我测过」，
是当场把两版跑在同一批语料上，让数字自己说话。**

这个脚本做三件事：

1. 从 git 历史取 **v1.4.1** 的 detector（带 boundary 词表）
2. 在**同一批真实收尾**上分别跑两版
3. 打印「提前放行」条数与差异，并区分**提前放行**与**真正的误放行**

⚠ **数字口径**（R311 红队第二轮 N6 —— 这里曾写着写死的旧数）：
   本脚本**打印的数字全部来自本次运行**（不写死任何结论值），
   并在末尾打印 `语料 sha256[:16]`。
   ⇒ **要报什么数字，请以本脚本/verify_claims.py 的输出为准，并带上 sha。**
   ⚠ 历史上出现过两组数（未裁剪全量 510 / 36% / 43；裁剪脱敏中间版 506 / 35.4% / 42），
   **它们不对应当前语料**（当前流水线的实测值见 verify_claims.py 输出）。
   ⇒ **不要引用历史数**。

**这证明的能力**：不是「我做对了」，是「**我有能力发现自己错，并量化它**」。
AI Agent 岗每三个月变一次，做不对是必然的，
**能不能快速发现并修正才是本事**。

用法:
  python3 scripts/replay_regression.py           # 需要 git 历史
  python3 scripts/replay_regression.py --json
"""

from __future__ import annotations

import argparse
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


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 {path}")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def corpus_sha() -> str:
    """语料指纹 —— **每个报数的地方都必须带上它**（R311 红队 E2）。

    为什么：语料一改（裁剪/脱敏/剔行），统计值就变
    （实测 1430→1348 条时，506→472、42→40、11→10）。
    ⇒ 不带 sha 的数字 = 无法追溯 = 等于没报。
    """
    import hashlib

    return hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:16]


def fetch_old_detector() -> Path | None:
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


def load_texts() -> list[str]:
    if not FIXTURE.exists():
        return []
    return [
        json.loads(line)["text"]
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run(det, texts) -> tuple[int, Counter]:
    cfg = det.default_config()
    c = Counter(det.detect(t, 0, cfg) for t in texts)
    return sum(v for k, v in c.items() if k[0]), c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    texts = load_texts()
    if not texts:
        print(f"找不到语料：{FIXTURE}", file=sys.stderr)
        return 2

    old_path = fetch_old_detector()
    if old_path is None:
        print(f"取不到 {OLD_TAG} 的 detector（需要 git 历史）", file=sys.stderr)
        return 2

    old_det = load_module(old_path, "det_old")
    new_det = load_module(ROOT / "detector.py", "det_new")

    old_n, old_c = run(old_det, texts)
    new_n, new_c = run(new_det, texts)
    total = len(texts)

    # 旧版因 boundary 放行的条数 = 命��� boundary 的条数
    old_boundary = old_c.get((False, "boundary"), 0) + old_c.get((False, "boundary_words"), 0)
    new_boundary = new_c.get((False, "boundary"), 0) + new_c.get((False, "boundary_words"), 0)

    # ⭐ 真正的误放行 = 新版多触发的条数（不是旧版放行的总数！）
    truly = new_n - old_n

    result = {
        "corpus_size": total,
        "corpus_sha256_16": corpus_sha(),
        "old": {"tag": OLD_TAG, "nudged": old_n, "boundary_released": old_boundary},
        "new": {"tag": "1.5.0", "nudged": new_n, "boundary_released": new_boundary},
        "recovered": new_n - old_n,
        "true_false_negatives": new_n - old_n,
        "boundary_released_but_fine": old_boundary - (new_n - old_n),
        # ⚠⚠ **这些数字必须是计算值，不能写字面量**（R311 红队 E2）
        #   实测：语料换一次脱敏规则，数字就在 504 ↔ 506 之间跳。
        #   写死 ⇒ 脚本会**静默印出过期数字**，与另一个脚本当场矛盾。
        "note": (
            f"{old_boundary} = 被旧版**提前放行**的总数；其中只有 {truly} 条本来该 nudge ⇒ "
            f"真正的误放行 = {truly} 条（{truly / total * 100:.1f}%），"
            f"其余 {old_boundary - truly} 条两版都放行（不算误判）。"
        ),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print("=" * 70)
    print(f"  误伤对比 —— 同一批 {total} 条真实 agent 收尾")
    print("=" * 70)
    print()
    print(f"  {OLD_TAG:>10}（带 boundary 词表）")
    print(f"  {'真的该 nudge':>16} : {old_n}")
    print(f"  {'被 boundary 放行':>16} : {old_boundary}  ({old_boundary / total * 100:.1f}%)")
    print()
    print(f"  {'1.5.0（删表）':>16}")
    print(f"  {'真的该 nudge':>16} : {new_n}")
    print(f"  {'被 boundary 放行':>16} : {new_boundary}  ({new_boundary / total * 100:.1f}%)")
    print()
    # ⭐ 措辞必须精确（红队 R311 质疑过，纠正）：
    #   「506 条被旧版提前放行」≠「506 条误放行」。
    #   其中**只有 11 条**在新版下真的该 nudge ⇒ 真正的误放行是 **11 条（0.8%）**。
    #   另外 495 条**两版都放行**（那些本来就没有触发信号，不算误判）。
    print(f"  ⇒ 旧版靠词表**提前放行 {old_boundary} 条**，其中：")
    print(
        f"       · **{truly} 条**本来该 nudge（新版多触发的）⇒ **真正的误放行 = {truly} 条（{truly / total * 100:.1f}%）**"
    )
    print(f"       · {old_boundary - truly} 条两版都放行（本来就没有触发信号，**不算误判**）")
    print()
    print("  ⛔ 旧版的问题不是「词表不够大」，是**用词表判断语义**：")
    print("     它判的是「tail 里有没有这个词」，而语义是")
    print("     「这一条收尾在请求不可逆动作」——两件事被混为一谈。")
    print("     ⇒ 修法是删表 + 把护栏交给 agent 自己核，不是加词。")
    print(
        f"     ⚠ **口径**：误放行是 {truly} 条，不是 {old_boundary} 条。"
        f"{old_boundary} 是「被提前放行的总数」。"
    )
    print()
    print(f"  语料 sha256[:16] = {corpus_sha()}")
    print()
    print("=" * 70)
    print("  复算：python3 scripts/replay_regression.py")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
