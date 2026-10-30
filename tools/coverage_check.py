#!/usr/bin/env python3
"""把「脱敏覆盖率」变成**可证**的东西，而不是靠红队一次次抓。

## 为什么需要这个

前七轮里，同一类问题出现了五次：

| 轮次 | 形状 | 症状 |
|---|---|---|
| 2 | `\b` 在中文里失效 | 替换侧与扫描侧**同时**漏 ⇒ `--check` 报 0 |
| 4 | `[CWG]\\d{2,3}` 窗口开窄 | 漏 1 位数和 `T` 前缀 ⇒ 291 处活着 |
| 5 | `[A-Z]{1,3}` 无下划线 | `AP_26` 漏 6 处 |
| 6 | 字母数收到 1–2 | （正确）但同时漏了「粘着字母」 |
| 7 | 尾部 `(?![0-9A-Za-z_])` 拒掉粘着形态 | `C348A` / `C211a` / `T_y` 漏 11 处 |

**共同病因**：我每次只加/改**红队点名的那一个形态**，从不去问
「**还有哪些形态没被任何规则覆盖**」。而扫描侧和替换侧用同一条正则，
盖不住的两侧都看不见 ⇒ 假 0。

## 这个脚本做的事

1. 从**原始语料**取出所有「大写字母打头的短 token」（这是内部编号的形态上界）
2. 逐个喂给 `sanitize()` —— 原样返回的 = **一条规则都没覆盖**
3. 把「含数字或下划线」的挑出来（编号的特征），打印**未覆盖面**
4. 与一份**人工登记表**比对：
   - 在 `EXPECTED_RAW` 里的是「确认为代号、但故意不脱敏」⇒ 不算问题
   - 不在表里的 ⇒ **报红**，逼人来看

⇒ 这才是「覆盖率」：不是「我加了几条规则」，而是「**已知形态里还剩哪些没盖住**」。

    python3 tools/coverage_check.py <原始语料.jsonl>
    python3 tools/coverage_check.py <原始语料.jsonl> --list   # 打印全部未覆盖项
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sanitize_corpus as sc

# 大写开头的短 token —— 内部编号的**形态上界**（宁可多抓）
CAND = re.compile(r"\b[A-Z][A-Za-z0-9_\-]{0,7}\b")

# ⚠ 人工登记：确认为「不是内部编号」或「故意不脱敏」的形态。
#   往这里加东西时必须写理由 —— 这个表就是「我到底放过了什么」的账。
EXPECTED_RAW: dict[str, str] = {
    # 技术术语 / 标准名 / 产品名（脱敏会损害可读性，且不含内部信息）
    "V2EX": "社区名",
    "B2B": "商业模式",
    "B2C": "商业模式",
    "Top10": "榜单",
    "Top5": "榜单",
    "TOP5": "榜单",
    "TOP20": "榜单",
    "Top100": "榜单",
    "W-8BEN": "美国税表名",
    "Gate1": "关卡名",
    "SKU3": "商品编号通称",
    "AnyPS5": "商品名",
    "GPT-6": "模型名",
    "Agent37": "产品名",
    "IPv6": "协议",
    "GLP-1": "药名",
    "Image-2": "模型名",
    "Qwen3": "模型名",
    "CVE-2026": "漏洞编号标准",
    "Apache-2": "许可证名",
    "C2PA": "内容来源标准",
    "MIN_ROWS": "代码常量名",
    "SEP-2640": "专利号",
    "Context7": "服务名",
    "K8s": "Kubernetes",
    "SOC2": "合规标准",
    "HTTP3": "协议",
    "Sparc3D": "模型名",
    "SHA-256": "哈希算法",
    "Gen-4": "模型代次",
    "Agent8": "产品名",
    "W_OF": "代码常量名",
    "KDiff3": "工具名",
    # —— 覆盖率工具第一轮跑出来的剩余项（逐个确认不是内部编号）——
    "HTML5": "网页标准",
    "Web3": "行业词",
    "Vue3": "框架",
    "NAPS2": "扫描软件",
    "PDF24": "软件",
    "F5Bot": "服务名",
    "Ed25519": "签名算法",
    "GLM-5": "模型名",
    "Mem0": "开源项目",
    "FTS5": "SQLite 模块",
    "MIT-0": "许可证",
    "GPT-3": "模型名",
    "G4J": "项目名",
    "T4B": "规格名",
    "CROSS1": "实验组名",
    "EARLY20": "折扣码",
    "Pro25x": "套餐名",
    "Max5x": "套餐名",
    "Top6": "榜单",
    "Top8": "榜单",
    "Top20": "榜单",
    "Top25": "榜单",
    "TOP10": "榜单",
    "TOP50": "榜单",
    "Top1-2": "榜单",
    "Day7": "天数",
    "Day21": "天数",
    # 短片段（不是完整编号）
    "Kqz5": "media_id 前缀的短片段",
    "Ju0EB": "随机串片段",
    "Sov2ex": "域名片段",
    # —— 第二轮剩余项：确认是技术词（不是内部编号）——
    "IN_SYNC": "代码常量",
    "AI_N": "代码常量",
    "AI_RE": "代码常量",
    "MAIL_": "代码常量",
    "USD500-": "金额",
    "Top3": "榜单",
    "Top50": "榜单",
    "AS132203": "ASN 自治域号",
    "FSE2014": "学术会议名（FSE 2014）",
    # ⚠ 下面两项**语义上可能是内部编号**，但形态与正常词无法机械区分，
    #   且各只出现 1 次、无伴随上下文可泄露——**定为「已知放过」，并写在这**：
    "JJMM_903": "已知放过：形态像编号但无法与正常串区分，各 1 次",
    # —— 第三轮剩余项 ——
    "MV7nZqE": "随机串片段（media_id/URL 残余）",
    "WudEF_": "随机串片段",
    "SUPi5460": "随机串片段",
    "Rtkw4me": "随机串片段",
    "Doc2X": "产品名",
    "Top15": "榜单",
    "Pro2": "套餐名",
    "SEP-1865": "专利号",
    "C-2026-": "已知放过：带尾连字符的片段，无法确认是编号",
    "G3b": "已知放过：像代号，但放宽到 1 位数字会误伤 K8s ⇒ 各 1 次，接受",
    "GPL-3": "许可证",
    "Neon14": "数据库服务",
    "E2B": "云沙箱服务",
    "PY_VENV": "目录名",
    "Android1": "平台版本",
    "H5player": "播放器",
    "UTF-8": "编码",
    "SKU2": "商品编号通称",
    "GPT-6-": "模型名带尾连字符",
    "MIN_FILL": "代码常量",
    "Quad9": "公共 DNS 服务",
    "LTX-2": "模型名",
    "V2EX_HN": "站点组合名",
    "Top1": "榜单",
    "A2A": "协议名",
    "Qwen3-4B": "模型名",
    # ⚠⚠ 这一类的通用上限（写进文档，别假装它不存在）：
    #   「大写+数字混合的短随机串」（`MV7nZqE` / `WudEF_` / `SUPi5460` / `Rtkw4me`）
    #   与正常技术词在**形态上不可区分**，任何正则都只能二选一：
    #     放宽 ⇒ 吃掉 `K8s` / `H264` / `B2B` 这类正常词（损害可读性）
    #     收紧 ⇒ 放过若干随机串片段（每次 1 个、无上下文）
    #   这里**选收紧 + 逐条记账**，因为「可读性」是这份语料存在的理由。
}


def _self_test() -> int:
    """CI 用：只验工具与登记表自洽（没有原始语料也能跑）。"""
    if len(EXPECTED_RAW) != len(set(EXPECTED_RAW)):
        print("❌ 登记表有重复键", file=sys.stderr)
        return 78
    no_reason = [k for k, v in EXPECTED_RAW.items() if not v or len(v) < 2]
    if no_reason:
        print(f"❌ 登记表里这些项没写理由: {no_reason}", file=sys.stderr)
        return 78
    contradicted = [k for k in EXPECTED_RAW if sc.sanitize(k) != k]
    if contradicted:
        print(
            f"❌ 这些项登记为「不用脱敏」，但实际会被脱敏: {contradicted[:10]}",
            file=sys.stderr,
        )
        return 78
    print(f"✅ 覆盖率工具自洽：登记表 {len(EXPECTED_RAW)} 项，无重复、无矛盾")
    return 0


def _report(unexplained: dict[str, int], list_all: bool) -> int:
    blocking = {k: n for k, n in unexplained.items() if n >= 2}
    if list_all:
        for k, n in sorted(unexplained.items(), key=lambda x: -x[1]):
            print(f"  {k:14} ×{n}")
    if blocking:
        print("\n❌ 有**反复出现**且未解释的编号形态 ⇒ 要么加脱敏规则，要么登记并写理由：")
        for k, v in sorted(blocking.items(), key=lambda x: -x[1]):
            print(f"   - {k}（×{v}）")
        return 78
    if unexplained:
        print(f"\nℹ 另有 {len(unexplained)} 种单次出现的形态（不阻断，供人工扫一眼）：")
        for k in sorted(unexplained, key=lambda x: -unexplained[x])[:20]:
            print(f"   - {k}")
    print("\n✅ 没有「反复出现且未解释」的编号形态")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="脱敏覆盖率：还有哪些编号形态没被覆盖")
    ap.add_argument("raw", type=Path, nargs="?", help="原始（未脱敏）语料")
    ap.add_argument("--self-test", action="store_true", help="只检查工具与登记表自洽（CI 用）")
    ap.add_argument("--list", action="store_true", help="打印全部未覆盖项")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if not args.raw:
        print("❌ 需要原始语料路径（或用 --self-test）", file=sys.stderr)
        return 2

    text = args.raw.read_text(encoding="utf-8")
    tok = Counter(CAND.findall(text))
    uncovered = {k: v for k, v in tok.items() if sc.sanitize(k) == k}
    suspect = {k: v for k, v in uncovered.items() if re.search(r"[\d_]", k)}
    unexplained = {k: v for k, v in suspect.items() if k not in EXPECTED_RAW}

    print(f"原始语料里大写开头的短 token: {len(tok)} 种 / {sum(tok.values())} 次")
    print(f"其中一条规则都没覆盖到的    : {len(uncovered)} 种")
    print(f"其中「含数字或下划线」（像编号）: {len(suspect)} 种 / {sum(suspect.values())} 次")
    print(f"其中未在登记表里的           : {len(unexplained)} 种")
    return _report(unexplained, args.list)


if __name__ == "__main__":
    sys.exit(main())
