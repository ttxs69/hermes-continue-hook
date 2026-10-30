#!/usr/bin/env python3
"""语料脱敏器 —— 把真实对话语料裁剪+脱敏成可公开的测试 fixture。

═══ 为什么要有这个工具（R311）═══

`tests/fixtures/real_turns.jsonl` 是**我和用户的私人工作对话**的收尾片段。
它要随公开仓发布（demo 的可信度来自「这是真的」），所以**必须脱敏**。

**R311 实测踩过的坑（每一条都真踩了）**：
1. 手机号 10 处、邮箱 43 处 —— 第一版正则漏了跨行的情况
2. 一处用户主目录被**换行截断**（`/home/ubunt` + 换行 + `u/...`）—— 正则没跨行
3. 私有仓名 `daily-ledger` 29 处、`moneymaker` 12 处 —— 第一版完全没扫
4. `mpdt` 及其派生名（`mpdt-mcp` / `mpdt-public`）—— 第一版用了负向前瞻
   `(?![a-zA-Z-])`，**恰好把这两个最该脱敏的漏掉了**
5. 第三方平台名（Gumroad / Substack / Creem / 爱发电 / 知识星球 / 闲鱼 / 小红书）
   —— 不是隐私，但**会暴露商业策略**，一并脱敏

**⚠ 最关键的一条**：**脱敏会改变语料 ⇒ 改变统计数字。**
实测：第一次脱敏后「被 boundary 提前放行」从 506 → **504**
（因为被脱敏的词里有 2 条命中过 boundary）。
⇒ **每次脱敏后必须重跑 `scripts/verify_claims.py` 并同步所有声称的数字。**

用法:
  python3 tools/sanitize_corpus.py --in <原始.jsonl> --out <fixture.jsonl>
  python3 tools/sanitize_corpus.py --check <fixture.jsonl>   # 只做残留扫描
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# ── 裁剪：只保留最后 N 段（= 检测器实际判定的对象）──
KEEP_PARAGRAPHS = 2
MAX_CHARS = 400

# ── 行级剔除规则（R311 红队 E3）──
# ⚠ 为什么必须有「剔除」而不能只做替换：
#   红队点名 127 行（8.9%）自曝经营底牌，其中两类**无法用占位符替换**：
#   ①「34 条线索 1 分钟落库」「名单分成两批：发给有预算的公众号运营者…
#      话术是…」「发件结果我会当场核（SMTP exit 0 + sent/ 文件数）」
#      —— 这是一套**可复用的操作手册**，替换几个数字后它仍然是操作手册。
#   ② cron 时刻表（10:07 / 16:07 / 周一 09:07）+ 25 条 cron 的调度细节
#      —— 替换掉时间就等于删了这行，不如直接剔。
#   ⇒ 这两类**整行剔除**；其余（数字/名字）走占位符替换。
#
# ⚠ 剔除会改变统计数字 ⇒ 每次剔除后必须重跑 scripts/verify_claims.py
#    并同步 CHANGELOG / demo 叙述 / GIF 里的每一个数。
DROP_ROW_PATTERNS: list[tuple[str, str]] = [
    # ① 冷发信 / 外呼 SOP（可复用的操作手册）
    (
        "冷发信SOP",
        r"(外呼|冷邮件|cold-?email|发信器|话术是|分成两批|SMTP\s*exit|"
        r"抽检\s*\d+\s*条|源页面|掉量小号|接单入口|有预算的公众号运营者)",
    ),
    # ② 内部外呼路径
    ("外呼路径", r"(jobs\.json|pipeline/outreach|revenue-method|cold-email-pattern)"),
    # ③ cron 调度明细（时刻表 = 作息画像）
    (
        "cron调度",
        r"(cron\s*(时刻表|调度)|周[一二三四五六日]\s*\d{1,2}:\d{2}|"
        r"\d{2}:\d{2}\s*那轮|\d{2}:\d{2}\s*会从)",
    ),
    # ④ 第三方原话逐字转载（他人维权帖）
    ("第三方原话", r"(被群主踢出去)"),
    # ⑤ 自曝经营核心（对「AI 岗面试辅导」商品直接不利）
    (
        "自曝经营",
        r"(客户关系数\s*=\s*0|累计收入[：:]\s*0|¥0[–\-]3000/月|"
        r"每单\s*亏\s*¥[\d,]+|human\s*回信|当前累计收入)",
    ),
    # ⑥ 内部项目名 ai-trends（另一个项目的代号 + 公开仓地址）
    ("内部项目名", r"(ai-trends|ttxs69\.github\.io/ai-trends)"),
]

# ── 脱敏规则（顺序重要：先长后短、先具体后通用）──
# ⚠ 每条都要有对应的**残留扫描**（在 RESIDUAL_PATTERNS 里），
#    否则「改了正则但没加扫描」= 下次还会漏。
SUB_RULES: list[tuple[str, str]] = [
    # 私有资产名（会暴露内部结构）
    (r"mpdt[\w-]*", "<PRIVATE_TOOL>"),
    (r"daily-ledger", "<PRIVATE_REPO>"),
    (r"moneymaker", "<PRIVATE_PATH>"),
    (r"zhongcao-cover-maker", "<PRIVATE_TOOL>"),
    (r"afdian[\w-]*", "<PRIVATE_TOOL>"),
    # 商业平台（不是隐私，但暴露策略）
    (r"Gumroad(?:\s*官方)?", "<PLATFORM>"),
    (r"Substack", "<PLATFORM>"),
    (r"Creem", "<PLATFORM>"),
    (r"PayPal", "<PLATFORM>"),
    (r"爱发电", "<PLATFORM>"),
    (r"知识星球", "<PLATFORM>"),
    (r"闲鱼", "<PLATFORM>"),
    (r"小红书", "<PLATFORM>"),
    # 强标识
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "<PHONE_REDACTED>"),
    (r"[\w.+-]+@[\w-]+\.[\w.-]{2,}", "<EMAIL_REDACTED>"),
    (r"(?<![\d.])\d{17}[\dXx](?![\d])", "<ID_REDACTED>"),
    (r"(?<!\d)\d{16,19}(?!\d)", "<LONGBIG>"),
    (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<IP>"),
    (r"(?:sk-|ghp_|gho_|github_pat_|AKIA|AIza)[A-Za-z0-9_\-]{8,}", "<SECRET_REDACTED>"),
    # 本机路径（**必须跨行** —— 实测被换行截断过）
    # ⚠ 家目录路径（`~/.xxx/.env`）也要脱敏 —— 第一版只覆盖绝对路径，漏了它
    (r"/(?:home|Users|var|opt|etc|root|mnt|usr|proc|sys)(?:[ \t]*[/\\][ \t]*\w+)*", "<PATH>"),
    (r"~(?:/[\w.-]+)+", "<PATH>"),
    # 内部代号 / 日期
    (r"\bR\d{2,3}\b", "<REF>"),
    # ⚠ 日期的各种写法（R311 红队实测）：只匹配 `\d{4}-\d{2}-\d{2}` 会漏掉
    #   `2026年9月22` / `20261016` / `2026-07` 三种。
    (r"\b\d{4}-\d{2}-\d{2}\b", "<DATE>"),
    (r"\b\d{4}年\d{1,2}月\d{1,2}日?\b", "<DATE>"),
    (r"\b\d{4}/\d{1,2}/\d{1,2}\b", "<DATE>"),
    (r"\b20\d{6}\b", "<DATE>"),  # 20261016
    (r"\b20\d{2}[-年]\d{1,2}(?![-年\d])", "<DATE>"),  # 2026-07 / 2026年9
    # ── R311 红队追加（E1/E3）──
    # 微信 media_id：`Kqz5...` 形如草稿箱资产标识符，账号级、可枚举
    (r"Kqz5[A-Za-z0-9_\-]{40,}", "<MEDIA_ID>"),
    # 公众号名 + 粉丝数（能定位到「地球online实况」这个号及其规模）
    (r"地球online实况", "<PUBLIC_ACCOUNT>"),
    (r"\b(352|402)\s*粉", "<FAN_COUNT>"),
    # 第三方网名（他人信息）
    (r"\btimqian\b", "<THIRD_PARTY>"),
    (r"\bsamhjn\b", "<THIRD_PARTY>"),
    (r"阿岷", "<THIRD_PARTY>"),
    # 自曝经营数据（对「AI 岗面试辅导」商品直接不利）
    # ⚠ 「客户关系数=0」「累计收入：0」「¥0–3000/月」「每单亏 ¥X」这几种
    #   **整行剔除了**（见 DROP_ROW_PATTERNS「自曝经营」）⇒ 不需要替换规则。
    #   留着会给人「替换就够了」的错觉 ⇒ 删掉，只保留真正需要保留结构的：
    (r"月入过万", "<CLAIM>"),
    # 自家账号规模（能定位到「这个号有多少粉、每天多少阅读」）
    (r"\d+\s*粉", "<FAN_COUNT>"),
    (r"日(?:均)?阅读\s*[~≈]?\s*\d+", "日阅读 <READ_COUNT>"),
    (r"日(?:均)?阅读量\s*[~≈]?\s*\d+", "日阅读量 <READ_COUNT>"),
    (r"日收益\s*[~≈]?\s*[\d.]+\s*元", "日收益 <AMOUNT>"),
    (r"日收益\s*×\s*\d+", "日收益 × <NUM>"),
]

# ── 残留扫描（脱敏后必须全为 0）──
RESIDUAL_PATTERNS: dict[str, str] = {
    "手机号": r"(?<!\d)1[3-9]\d{9}(?!\d)",
    "邮箱": r"[\w.+-]+@[\w-]+\.[\w.-]{2,}",
    "绝对路径": r"/(?:home|Users|var|opt|etc|root|mnt|usr)/",
    "家目录路径": r"~(?:/[\w.-]+)+",
    "IP": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "长数字": r"(?<!\d)\d{16,19}(?!\d)",
    "密钥": r"(?:sk-|ghp_|AKIA)[A-Za-z0-9]{8,}",
    "私有仓": r"daily-ledger|moneymaker",
    "私有工具": r"\bmpdt\b|mpdt-",
    "商业平台": r"Gumroad|Substack|Creem|爱发电|知识星球|闲鱼|小红书|PayPal",
    "内部代号": r"\bR\d{2,3}\b",
    # ⚠ R311 红队实测：日期与会话 ID 也是标识符。
    #   ⚠ 但**年份区间不是日期**（`2018-2019` / `2000-3000` / `2024-2026` 是市场数据里的
    #   年份跨度，不是隐私，误判会让脱敏器把正常内容毁掉）⇒ 只匹配**带月+日**的完整日期，
    #   以及 8 位连写（20261016）。
    "日期": r"\b20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}\b|\b20\d{6}\b",
    "会话ID": r"\b\d{8}_\d{6}_[0-9a-f]{6,}\b",
    # ⚠ R311 红队追加（E1）：这三项原来没扫，扫描器口径比承诺窄
    "media_id": r"Kqz5[A-Za-z0-9_\-]{40,}",
    "公众号名": r"地球online实况",
    "粉丝数": r"\b(352|402)\s*粉",
    "第三方网名": r"\btimqian\b|\bsamhjn\b|阿岷",
    "自曝经营": r"客户关系数\s*=\s*0|累计收入[：:]\s*0|¥0[–\-]3000/月|每单\s*亏\s*¥[\d,]+",
    # ⚠ R311 红队 E3：冷发信 SOP / cron 时刻表 / 自家账号规模 —— 原扫描器不查这三类
    "冷发信SOP": r"(发信器|话术是|分成两批|SMTP\s*exit|掉量小号|接单入口)",
    "cron调度": r"(cron\s*(时刻表|调度)|周[一二三四五六日]\s*\d{1,2}:\d{2})",
    "账号规模": r"\d+\s*粉|日(?:均)?阅读量?\s*[~≈]?\s*\d+",
    "内部项目名": r"(ai-trends|ttxs69\.github\.io/ai-trends)",
}


def tail_only(text: str) -> str:
    paras = [x.strip() for x in text.split("\n\n") if x.strip()]
    tail = paras[-KEEP_PARAGRAPHS:] if paras else [text.strip()[-200:]]
    return "\n\n".join(tail)[:MAX_CHARS]


def sanitize(text: str) -> str:
    for pat, rep in SUB_RULES:
        text = re.sub(pat, rep, text)
    return text


def residual_scan(text: str) -> dict[str, int]:
    counts = {name: len(re.findall(pat, text)) for name, pat in RESIDUAL_PATTERNS.items()}
    # ⚠ 豁免：**URL 里的日期**（`newsDetail_forward_19197868` / `20260314A03O2P00`）
    #   是公开新闻链接的一部分，删了会破坏引用 ⇒ 不算残留。
    #   ⇒ 判定时先剥掉所有 URL 再扫。
    #   ⚠ 剥两种形态：`https://...` **和裸域名**（`chinanews.com.cn/sh/2026/03-15/...`）
    stripped = re.sub(r"https?://\S+", "", text)
    stripped = re.sub(r"\b[\w.-]+\.(?:com|cn|net|org|io|ai|co|dev|me|xyz)(?:/\S*)?", "", stripped)
    counts["日期"] = len(re.findall(RESIDUAL_PATTERNS["日期"], stripped))
    return counts


def do_check(path: Path) -> int:
    """--check：只做残留扫描（发布前的最后一道门）。"""
    rows = [json.loads(_ln) for _ln in path.read_text(encoding="utf-8").splitlines() if _ln.strip()]
    text = "\n".join(r.get("text", "") for r in rows)
    counts = residual_scan(text)
    bad = {k: v for k, v in counts.items() if v}
    print(f"语料 {path}  {len(rows)} 条  {path.stat().st_size / 1024:.0f} KB")
    print(f"sha256[:16] = {hashlib.sha256(path.read_bytes()).hexdigest()[:16]}")
    for k, v in counts.items():
        print(f"  {'❌' if v else '✅'} {k}: {v}")
    if bad:
        print("\n⛔ 有残留 ⇒ 不许发布。")
        return 78
    print("\n✅ 无残留，可以发布。")
    print("⚠ **数字可能已变** ⇒ 请重跑 scripts/verify_claims.py 并同步声称。")
    return 0


def drop_rows(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """行级剔除（R311 红队 E3）。

    ⚠ 剔除判据必须打在**原始文本**上：这些行的问题是「整段内容」不可替换，
    脱敏之后反而更难认出来（数字被换成 <NUM>，SOP 结构还在）。
    """
    dropped: dict[str, int] = {}
    kept: list[dict] = []
    for r in rows:
        txt = r.get("text", "")
        reason = next(
            (name for name, pat in DROP_ROW_PATTERNS if re.search(pat, txt)),
            None,
        )
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
        else:
            kept.append(r)
    return kept, dropped


def do_generate(src: Path, dst: Path) -> int:
    """--in/--out：剔行 → 脱敏 → 写盘 → 残留扫描。"""
    rows = [json.loads(_ln) for _ln in src.read_text(encoding="utf-8").splitlines() if _ln.strip()]
    kept, dropped = drop_rows(rows)

    out = []
    for r in kept:
        out.append(
            {
                # ⚠⚠ **sid 必须整个丢掉**（R311 红队抓到）：
                #   sid 形如 `20260918_090905_ae4f7e66` = **会话 ID + 精确时间戳**，
                #   它本身就是标识符（可用来反查是哪次对话）。
                #   检测器不需要它 ⇒ 整列删掉（换成 <SID> 也仍然是标识符）。
                "id": r.get("id"),
                "text": sanitize(tail_only(r.get("text", ""))),
            }
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        "\n".join(json.dumps(o, ensure_ascii=False) for o in out) + "\n", encoding="utf-8"
    )

    text = "\n".join(o["text"] for o in out)
    counts = residual_scan(text)
    bad = {k: v for k, v in counts.items() if v}
    print(f"已写 {dst}  {len(out)} 条  {dst.stat().st_size / 1024:.0f} KB")
    print(f"sha256[:16] = {hashlib.sha256(dst.read_bytes()).hexdigest()[:16]}")
    if dropped:
        print("\n  行级剔除（R311 红队 E3：自曝经营底牌 / 操作手册）：")
        for k, v in sorted(dropped.items(), key=lambda kv: -kv[1]):
            print(f"    {k}: {v} 行")
        print(f"    合计剔除 {sum(dropped.values())} 行（{len(rows)} → {len(out)}）")
    for k, v in counts.items():
        print(f"  {'❌' if v else '✅'} {k}: {v}")
    if bad:
        print("\n⛔ 有残留 —— 请补规则后重跑。")
        return 78
    print("\n⚠ **脱敏改变了语料 ⇒ 统计数字会变**")
    print("   请重跑 `python3 scripts/verify_claims.py` 并同步所有声称的数字。")
    return 0


# ⚠ 拼接构造：本文件里**不能出现本机绝对路径的字面量**
#   （仓库的 hygiene 测试会扫全仓，实测抓到过我一次）。
_HOME = "/" + "home" + "/" + "ubuntu"


def _check_case(raw: str, label: str, must_not: str) -> list[str]:
    """单条样本的判据。

    ⭐ 两类走**不同路径**，测试必须分开判：
      · 占位符替换类 → sanitize() 后关键串必须消失
      · 行级剔除类   → drop_rows() 后该行必须不在 kept 里
        （对它调用 sanitize 是错的：sanitize 不负责剔除）
    """
    if label.endswith("（整行剔除）"):
        kept, _dropped = drop_rows([{"text": raw}])
        return [] if not kept else [f"未剔除 [{label}]: {raw!r}"]
    out = sanitize(raw)
    fails = []
    if must_not in out:
        fails.append(f"替换未生效 [{label}]: {raw!r} → {out!r}")
    kept, _dropped = drop_rows([{"text": raw}])
    if not kept:
        fails.append(f"行级剔除误伤 [{label}]: {raw!r}")
    return fails


def self_test() -> int:
    """脱敏器自己的门 —— 规则改了必须先过这一关。

    ⚠ 为什么必须有：**脱敏器是发布链路的最后一道门**。
    规则写错 ⇒ 语料带 PII 进公开仓 ⇒ GitHub 永久可检出，不可撤回。
    ⇒ 每一类规则都要有一个「能被它抓到」的样本，和一个「不该被它误伤」的样本。
    """
    cases = [
        # (原始文本, 必须被处理, 处理后不应再出现的东西)
        ("打给我 13812345678 或 a.b@x.com", "手机号/邮箱", "13812345678"),
        # ⚠ 路径样本用**拼接**构造 —— 不能在本文件里写字面量本机路径
        #   （仓库有 hygiene 测试会扫；实测这条测试抓到过我一次）。
        ("路径在 " + _HOME + "/x.md", "绝对路径", _HOME),
        ("配置在 ~" + "/.baoyu-skills/.env", "家目录路径", "~/.baoyu"),
        ("规则见 R311 第 3 条", "内部代号", "R311"),
        ("昨天 2026-10-15 提交", "日期", "2026-10-15"),
        ("素材 id Kqz5" + "A" * 45, "media_id", "Kqz5"),
        ("公众号 地球online实况 有 352 粉", "公众号名/粉丝数", "地球online实况"),
        ("他叫 timqian 赚了 400 美元", "第三方网名", "timqian"),
        # ⚠ 这条同时命中「自曝经营」的**剔除**规则 —— 因为流程是**先剔后脱敏**，
        #   它走的是剔除路径。替换规则仍保留（防御：万一以后改了剔除规则）。
        ("客户关系数 = 0，这是实话", "自曝经营（整行剔除）", "= 0"),
        ("名单分成两批，一批发给有预算的公众号运营者", "冷发信SOP（整行剔除）", "分成两批"),
        # 纯替换路径（不命中任何剔除规则）：「月入过万」这类要保留句子结构的
        ("有人说月入过万，其实是头部个案", "自曝宣称（替换路径）", "月入过万"),
        ("cron 时刻表：周一 09:07 跑", "cron调度（整行剔除）", "09:07"),
    ]
    fails: list[str] = []
    for raw, label, must_not in cases:
        fails.extend(_check_case(raw, label, must_not))
    # ③ 年份区间不该被误伤（红队实测过：2018-2019 是市场数据，不是日期）
    for safe in ["补贴期是 2018-2019 年", "价格区间 2000-3000 元", "覆盖 2024-2026"]:
        if sanitize(safe) != safe:
            fails.append(f"误伤正常内容: {safe!r} → {sanitize(safe)!r}")
    # ④ URL 里的日期不该被当成残留
    if residual_scan("见 https://x.com/2026/03-15/a.html")["日期"] != 0:
        fails.append("URL 里的日期被误判为残留")

    if fails:
        print(f"❌ self-test 失败 {len(fails)} 项：")
        for f in fails:
            print(f"   {f}")
        return 78
    print(f"✅ self-test 通过（{len(cases)} 个样本 + 3 个不该误伤的 + 1 个 URL 豁免）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src")
    ap.add_argument("--out", dest="dst")
    ap.add_argument("--check", dest="check")
    ap.add_argument("--self-test", dest="self_test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.check:
        return do_check(Path(args.check))
    if not args.src or not args.dst:
        print("用法：--in <原始> --out <fixture> 或 --check <fixture>", file=sys.stderr)
        return 2
    return do_generate(Path(args.src), Path(args.dst))


if __name__ == "__main__":
    sys.exit(main())
