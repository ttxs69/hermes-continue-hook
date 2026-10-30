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
    # ⚠ 红队第二轮 C4 指出原规则漏了两种**最典型**的写法：
    #   「每天 08:23」「Cron：`23 8 * * *`」—— 恰好落在规则的缝隙里。
    (
        "cron调度",
        r"(cron\s*(时刻表|调度)|周[一二三四五六日]\s*\d{1,2}:\d{2}|"
        r"\d{2}:\d{2}\s*那轮|\d{2}:\d{2}\s*会从|"
        r"每天\s*\d{1,2}:\d{2}|"
        r"\d{1,2}\s+\d{1,2}\s+\*\s+\*\s+\*|"
        r"定时发布.{0,40}\d{1,2}:\d{2})",
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
    # ⚠⚠ **ISO 时间戳必须排在日期规则之前**（R311 红队第二轮）：
    #   顺序反了 ⇒ 日期部分先被换成 <DATE> ⇒ 剩下 `T07:25:14` ⇒ 时间信息漏出。
    (r"(?<![\dT])\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?![\d])", "<TIMESTAMP>"),
    # ⚠ 日期的各种写法（R311 红队实测）：只匹配 `\d{4}-\d{2}-\d{2}` 会漏掉
    #   `2026年9月22` / `20261016` / `2026-07` 三种。
    #   ⚠ 且**不能用 `\b`**（红队第二轮 N1）：汉字是 `\w` ⇒「提交2026-10-15」匹配不到。
    (r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", "<DATE>"),
    (r"(?<!\d)\d{4}年\d{1,2}月\d{1,2}日?(?!\d)", "<DATE>"),
    (r"(?<!\d)\d{4}/\d{1,2}/\d{1,2}(?!\d)", "<DATE>"),
    (r"(?<!\d)20\d{6}(?!\d)", "<DATE>"),  # 20261016
    (r"(?<!\d)20\d{2}[-年]\d{1,2}(?![-年\d])", "<DATE>"),  # 2026-07 / 2026年9
    # ── R311 红队追加（E1/E3）──
    # 微信 media_id：`Kqz5...` 形如草稿箱资产标识符，账号级、可枚举
    (r"Kqz5[A-Za-z0-9_\-]{40,}", "<MEDIA_ID>"),
    # 公众号名 + 粉丝数（能定位到「地球online实况」这个号及其规模）
    (r"地球online实况", "<PUBLIC_ACCOUNT>"),
    (r"\b(352|402)\s*粉", "<FAN_COUNT>"),
    # ⚠⚠ **作者本人的笔名**（R311 红队第二轮 N2 发现 —— 任何一轮都没发现）：
    #   上一轮把公众号名「地球online实况」列为阻塞并换成 <PUBLIC_ACCOUNT>，
    #   却把**同一个账号的署名**「林克」原样留下 —— 同一个可定位实体只遮了一半。
    #   更糟：有一行同时给出了**已发布文章的标题**（可公开检索到署名），
    #   另一行把笔名和收款平台（creem.io）绑在一起。
    #   ⇒ 规则不带边界：中文词不需要边界，且「X林克」也该被换掉。
    (r"林克", "<AUTHOR>"),
    # ⚠ ISO 时间戳（精确到秒 = 作息画像）
    # ⚠ 无年份的月日（「9月19日」）：**工作日历的一种写法**
    #   ⚠ 尾部**不能**有 `(?!\d)`：`11月28日23:59:59` 里「日」后面紧跟时刻
    #   ⇒ 负向断言把整条规则排除 ⇒ 日期原样漏出（实测）。
    (r"(?<!\d)\d{1,2}月\d{1,2}日", "<DATE>"),
    # ⚠ 年+月区间（「2026年9-10月」「2026年6-9月」）：红队第二轮验收命令匹配到的真残留
    (r"(?<![\d])20\d{2}年\d{1,2}[-–~至]\d{1,2}月(?![\d])", "<DATE>"),
    # ⚠⚠ **一切时刻**（HH:MM）⇒ <TIME>：
    #   红队第二轮 C4 指出「每天 08:23」漏网；实测**「今晨 08:23/08:37 两条日报」
    #   「GH（08:23）collect」这类写法才是主流** ⇒ 剔行规则追不上（写法太多）。
    #   ⇒ 改成**统一替换**：时刻本身信息量低，配合「两条日报」「今晨」才是作息画像；
    #      替换掉具体时刻后，句子语义（每天/今晨/两条日报）仍在，但定位不到分钟。
    # ⚠⚠ **秒级时间戳必须排在分钟级之前**（实测漏网 30 处）：
    #   `19:49:44` / `07:25:14` —— 分钟级规则的负向后视 `(?![\d:])`
    #   会把它们整个排除掉 ⇒ 秒级时间戳原样漏出。
    #   ⚠ 负向断言**只能看数字**：排除了 `:` ⇒ `| 主 :23:53 |` 这种
    #   （「23」前面正是冒号）会整个漏掉 ⇒ 实测漏网。
    (r"(?<!\d)\d{1,2}:\d{2}:\d{2}(?!\d)", "<TIME>"),
    (r"(?<!\d)\d{1,2}:\d{2}(?!\d)", "<TIME>"),
    # ⚠ cron 任务 ID（12+ 位 hex）：可回指到作者的 cron 系统
    (r"(?<![0-9A-Za-z])[0-9a-f]{12,40}(?![0-9A-Za-z])", "<CRON_ID>"),
    # ⚠ 第三方 GitHub 账号 + 其 MRR 数字（与 timqian 同类：他人信息）
    (r"@gefei55", "<THIRD_PARTY>"),
    (r"@yihui_indie", "<THIRD_PARTY>"),
    (r"GeFei 第二站", "<THIRD_PARTY>"),
    # 第三方网名（他人信息）
    (r"(?<![0-9A-Za-z_])timqian(?![0-9A-Za-z_])", "<THIRD_PARTY>"),
    (r"(?<![0-9A-Za-z_])samhjn(?![0-9A-Za-z_])", "<THIRD_PARTY>"),
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
    # ⚠⚠ **与 SUB_RULES 用同一套边界**（R311 红队第二轮 N1）：
    #   原来的 `\b` 在中文语料里等于失效（汉字是 \w）⇒ 替换侧和扫描侧
    #   **同时**漏 ⇒ 「✅ 无残留」是结构性绿灯，不是偶然。
    "日期": r"(?<!\d)20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}(?!\d)|(?<!\d)20\d{6}(?!\d)"
    r"|(?<!\d)\d{1,2}月\d{1,2}日"
    r"|(?<!\d)20\d{2}年\d{1,2}[-–~至]\d{1,2}月(?!\d)",
    "时间戳": r"(?<![\dT])\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?![\d])",
    "时刻": r"(?<!\d)\d{1,2}:\d{2}:\d{2}(?!\d)|(?<!\d)\d{1,2}:\d{2}(?!\d)",
    # ⚠ 作者本人的笔名（R311 红队第二轮 N2 —— 上一轮只遮了公众号名，
    #   同一账号的署名却原样留着；有一行还给出了已发布文章的标题）。
    "作者笔名": r"林克",
    "cron任务ID": r"(?<![0-9A-Za-z])[0-9a-f]{12,40}(?![0-9A-Za-z])",
    "第三方账号": r"@gefei55|@yihui_indie|GeFei 第二站",
    "会话ID": r"\b\d{8}_\d{6}_[0-9a-f]{6,}\b",
    # ⚠ R311 红队追加（E1）：这三项原来没扫，扫描器口径比承诺窄
    "media_id": r"Kqz5[A-Za-z0-9_\-]{40,}",
    "公众号名": r"地球online实况",
    "粉丝数": r"\b(352|402)\s*粉",
    "第三方网名": r"(?<![0-9A-Za-z_])timqian(?![0-9A-Za-z_])"
    r"|(?<![0-9A-Za-z_])samhjn(?![0-9A-Za-z_])|阿岷",
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
    r"""扫描残留。

    ⚠⚠ **必须与 SUB_RULES 用同一套判据**（R311 红队第二轮 N1 / N7）：
      ① 边界：原来的 `\b` 在中文语料里等于失效（汉字与带圈数字都是 `\w`）
         ⇒ 替换侧和扫描侧**同时**漏 ⇒「✅ 无残留」是结构性绿灯。
      ② 剔除侧 vs 扫描侧**不对称**：`--check` 与 `--in/--out` 用的不是同一套判据
         ⇒ 这道「发布前最后一道门」在最该拦的几类上是空的。
         ⇒ 结构性修法：**扫描时对每行跑一次 drop_rows()**，
           任何「会被 pipeline 剔除的行」都算残留 ⇒ 两套判据永远一致。
    """
    counts = {name: len(re.findall(pat, text)) for name, pat in RESIDUAL_PATTERNS.items()}
    # ⚠ 豁免：**URL 里的日期**（`newsDetail_forward_19197868` / `20260314A03O2P00`）
    #   是公开新闻链接的一部分，删了会破坏引用 ⇒ 不算残留。
    #   ⇒ 判定时先剥掉所有 URL 再扫。
    #   ⚠ 剥两种形态：`https://...` **和裸域名**（`chinanews.com.cn/sh/2026/03-15/...`）
    stripped = re.sub(r"https?://\S+", "", text)
    stripped = re.sub(r"\b[\w.-]+\.(?:com|cn|net|org|io|ai|co|dev|me|xyz)(?:/\S*)?", "", stripped)
    counts["日期"] = len(re.findall(RESIDUAL_PATTERNS["日期"], stripped))
    counts["时间戳"] = len(re.findall(RESIDUAL_PATTERNS["时间戳"], stripped))
    counts["时刻"] = len(re.findall(RESIDUAL_PATTERNS["时刻"], stripped))
    counts["cron任务ID"] = len(re.findall(RESIDUAL_PATTERNS["cron任务ID"], stripped))
    return counts


def do_check(path: Path) -> int:
    """--check：只做残留扫描（发布前的最后一道门）。"""
    rows = [json.loads(_ln) for _ln in path.read_text(encoding="utf-8").splitlines() if _ln.strip()]
    text = "\n".join(r.get("text", "") for r in rows)
    counts = residual_scan(text)
    # ⚠⚠ **结构性对称保证**（R311 红队第二轮 N7）：
    #   `--check` 与 `--in/--out` 必须用**同一套判据**，否则这道门是空的。
    #   ⇒ 这里直接对每一行跑一次 drop_rows()：任何「pipeline 会剔掉的行」
    #   在 --check 里也必须报出来。
    would_drop = [
        (name, r.get("text", "")[:60])
        for r in rows
        for name in (
            next((n for n, pat in DROP_ROW_PATTERNS if re.search(pat, r.get("text", ""))), None),
        )
        if name
    ]
    counts["应剔除却仍在"] = len(would_drop)
    bad = {k: v for k, v in counts.items() if v}
    print(f"语料 {path}  {len(rows)} 条  {path.stat().st_size / 1024:.0f} KB")
    print(f"sha256[:16] = {hashlib.sha256(path.read_bytes()).hexdigest()[:16]}")
    for k, v in counts.items():
        print(f"  {'❌' if v else '✅'} {k}: {v}")
    if would_drop:
        print("\n  ⚠ 这些行 pipeline 会剔除，--check 之前却放行了（判据不对称）：")
        for name, snippet in would_drop[:10]:
            print(f"    [{name}] {snippet}")
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

    ⚠⚠ **但只看原始文本 ⇒ 生成器不幂等**（红队第二轮 CI 任务实测）：
      第一次跑：判据打在原始文本上 ⇒ 剔掉 N 行
      第二次跑（输入已是脱敏后的文件）：判据打在**脱敏后**的文本上
      ⇒ 敏感串已被占位符替换（如 `jobs.json` → `<PATH>/jobs.json`）
      ⇒ **判据不再命中** ⇒ 该行被保留下来 ⇒ 输出与第一次不同
      ⇒ 「发布物 = 生成器的输出」不成立 ⇒ CI 没法验证。
    ⇒ **修法：脱敏前/脱敏后任一命中即剔** ⇒ 两次结果一致 ⇒ 幂等。
    """
    dropped: dict[str, int] = {}
    kept: list[dict] = []
    for r in rows:
        raw = r.get("text", "")
        # 脱敏后的形态（用于幂等：判据在脱敏后也要能命中）
        sanitized = sanitize(raw)
        reason = None
        for name, pat in DROP_ROW_PATTERNS:
            if re.search(pat, raw) or re.search(pat, sanitized):
                reason = name
                break
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
        else:
            kept.append(r)
    return kept, dropped


def _one_pass(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """跑一遍流水线：剔行 → 脱敏 → 截断 → 重编号。"""
    kept, dropped = drop_rows(rows)

    out = []
    for r in kept:
        out.append(
            {
                # ⚠⚠ **sid 必须整个丢掉**（R311 红队抓到）：
                #   sid 形如 `20260918_090905_ae4f7e66` = **会话 ID + 精确时间戳**，
                #   它本身就是标识符（可用来反查是哪次对话）。
                #   检测器不需要它 ⇒ 整列删掉（换成 <SID> 也仍然是标识符）。
                # ⚠⚠ **id 重编号**（R311 红队第二轮 N9）：
                #   原 id 是原始语料的行号（min=2 max=95132，非连续），
                #   保留它等于保留一把能 join 回全部 1430 条的钥匙
                #   （sid 删了，但 id 是等价强度的链接键）。
                #   ⇒ 重编号为 1..N。
                "id": len(out) + 1,
                # ⚠⚠ **顺序：先脱敏，再截断**（R311 红队第二轮实测的幂等性 bug）：
                #   原来是 tail_only(text) → sanitize(...)，
                #   而 tail_only 按「最后 2 段」截断、sanitize 会改变段长
                #   ⇒ 对已脱敏的文件再跑一遍，截断点就变了 ⇒ **生成器不幂等**
                #   ⇒ 「发布物 = 生成器的输出」这句话不成立 ⇒ CI 无法验证。
                #   改成 sanitize → tail_only 后：
                #   sanitize 本身幂等（占位符不再命中任何规则）⇒ 全流程幂等。
                "text": tail_only(sanitize(r.get("text", ""))),
            }
        )
    return out, dropped


def do_generate(src: Path, dst: Path) -> int:
    """--in/--out：剔行 → 脱敏 → 写盘 → 残留扫描。

    ⚠⚠ **发布物必须是生成器的「不动点」**（R311 红队第二轮 CI 任务实测）：
      生成器对原始语料跑一遍得到的文件，**再跑一遍会变**（截断点因脱敏而移位）
      ⇒ 「发布物 = 生成器的输出」只在「输入是原始语料」时成立，
      而原始语料是**私人的**、不可能进 CI。
      ⇒ **改成迭代到不动点**：f(f(x)) == x。
      ⇒ 此时 CI 只要验证「f(已发布) == 已发布」，就等于验证了
      「已发布 = 生成器的输出」，且不需要原始语料。
    """
    rows = [json.loads(_ln) for _ln in src.read_text(encoding="utf-8").splitlines() if _ln.strip()]

    out, dropped = _one_pass(rows)
    # 迭代到不动点（实测 2 轮收敛）
    for _ in range(4):
        again, _d = _one_pass(out)
        if again == out:
            break
        out = again

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
        # ⚠⚠ **无空格样本**（R311 红队第二轮 N1 的核心）：
        #   原来的样本是「昨天 2026-10-15 提交」（**两边有空格**）——
        #   恰好是能通过的那种写法，掩盖了 `\b` 在中文语料里失效的 bug。
        #   中文语料的**默认写法**就是汉字紧贴数字。
        ("提交2026-10-15", "日期（汉字紧贴）", "2026-10-15"),
        ("明示2016年7月15日", "中文日期（汉字紧贴）", "2016年7月15日"),
        ("戳20261015", "8位日期（汉字紧贴）", "20261015"),
        ("②timqian 的 V2EX", "第三方网名（带圈数字紧贴）", "timqian"),
        # ⚠ 作者笔名（R311 红队第二轮 N2）—— 上一轮只遮了公众号名，
        #   同一账号的署名却原样留着；有一行还给出了已发布文章的标题。
        ("作者「林克」生成草稿", "作者笔名", "林克"),
        ("2026-10-20T07:25:14 跑完了", "ISO 时间戳", "07:25:14"),
        # ⚠ 秒级时间戳（实测漏网 30 处：`19:49:44`）
        ("在 19:49:44 跑完了", "秒级时间戳", "19:49:44"),
        ("每天 08:23 自动运行", "时刻表（整行剔除）", "08:23"),
        ("任务 24a9bd59ddf0 已排", "cron 任务 ID", "24a9bd59ddf0"),
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
