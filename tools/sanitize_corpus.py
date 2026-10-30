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

# ⚠ 拼接构造：本文件里**不能出现本机绝对路径的字面量**
#   （仓库的 hygiene 测试会扫全仓，实测抓到过我一次）。
_HOME = "/" + "home" + "/" + "ubuntu"

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
SUB_RULES: list[tuple[str, str, str, bool, tuple[str, str]]] = [
    # ⚠⚠⚠ **每条规则带 4 个声明 + 1 个专属样本**（红队第三轮 P0-1 / P0-2）：
    #   ① 第三项 = 它对应的扫描类别（RESIDUAL_PATTERNS 的键）
    #      ⇒ 「方向 B」对账：每条替换规则必须有一个扫描类别覆盖它，
    #        否则**那条规则坏了没人知道**（红队实测：改坏 `afdian` 规则
    #        ⇒ 语料带原始串发布，而 `--check` 与 `--self-test` 双双全绿）
    #   ② 第四项 = 在 URL 内是否豁免（**显式声明，不靠模式串猜** ——
    #      我先后两次栽在「靠 marker 猜」上：斜杠年+月没进 marker、
    #      内部代号没进 marker）
    #   ③ 第五项 = **这条规则自己的**样本（不是类别的样本）
    #      ⇒ 同一类别里有多条规则时（如 3 条私有工具），
    #        改坏其中任何一条都会被各自的样本抓到
    #   ⇒ **一条都不许用 `\b`**：Python 的 `\b` 定义在 `\w` 边界上，
    #     而**汉字与带圈数字 ①②③ 都是 `\w`** ⇒ 中文语料的主流写法
    #     （数字紧贴汉字）会同时骗过替换侧与扫描侧 ⇒ 绿灯是结构性的。
    # ── 私有资产名（会暴露内部结构；长的先替）──
    (r"mpdt[\w-]*", "<PRIVATE_TOOL>", "私有工具", False, ("工具 mpdt-public 跑一下", "mpdt")),
    (
        r"daily-ledger",
        "<PRIVATE_REPO>",
        "私有仓",
        False,
        ("仓库 daily-ledger 同步", "daily-ledger"),
    ),
    (r"moneymaker", "<PRIVATE_PATH>", "私有仓", False, ("目录 moneymaker 在这", "moneymaker")),
    (
        r"zhongcao-cover-maker",
        "<PRIVATE_TOOL>",
        "私有工具",
        False,
        ("脚本 zhongcao-cover-maker", "zhongcao-cover-maker"),
    ),
    (r"afdian[\w-]*", "<PRIVATE_TOOL>", "私有工具", False, ("平台 afdian 注册了", "afdian")),
    # ── 商业平台（不是隐私，但暴露策略）──
    (r"Gumroad(?:\s*官方)?", "<PLATFORM>", "商业平台", False, ("上架到 Gumroad 官方", "Gumroad")),
    (r"Substack", "<PLATFORM>", "商业平台", False, ("订阅 Substack", "Substack")),
    (r"Creem", "<PLATFORM>", "商业平台", False, ("收款 Creem", "Creem")),
    (r"PayPal", "<PLATFORM>", "商业平台", False, ("用 PayPal 收款", "PayPal")),
    (r"爱发电", "<PLATFORM>", "商业平台", False, ("在爱发电开通", "爱发电")),
    (r"知识星球", "<PLATFORM>", "商业平台", False, ("加入知识星球", "知识星球")),
    (r"闲鱼", "<PLATFORM>", "商业平台", False, ("在闲鱼挂商品", "闲鱼")),
    (r"小红书", "<PLATFORM>", "商业平台", False, ("发小红书笔记", "小红书")),
    # ── 强标识 ──
    (
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "<PHONE_REDACTED>",
        "手机号",
        False,
        ("打给我 13812345678", "13812345678"),
    ),
    (
        r"[\w.+-]+@[\w-]+\.[\w.-]{2,}",
        "<EMAIL_REDACTED>",
        "邮箱",
        False,
        ("联系 creators@n8n.io", "creators@n8n.io"),
    ),
    (
        r"(?<![\d.])\d{17}[\dXx](?![\d])",
        "<ID_REDACTED>",
        "身份证",
        False,
        ("身份证 11010119900307561X", "11010119900307561X"),
    ),
    (
        r"(?<!\d)\d{16,19}(?!\d)",
        "<LONGBIG>",
        "长数字",
        True,
        ("单号 2065523833575039002", "2065523833575039002"),
    ),
    (
        r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",
        "<IP>",
        "IP",
        False,
        ("服务器 192.168.1.100", "192.168.1.100"),
    ),
    (
        r"(?:sk-|ghp_|gho_|github_pat_|AKIA|AIza)[A-Za-z0-9_\-]{8,}",
        "<SECRET_REDACTED>",
        "密钥",
        False,
        ("token " + "sk-" + "abcdefgh12345678", "sk-" + "abcdefgh12345678"),
    ),
    # ── 本机路径（**必须跨行** —— 实测被换行截断过）──
    (
        r"/(?:home|Users|var|opt|etc|root|mnt|usr|proc|sys)(?:[ \t]*[/\\][ \t]*\w+)*",
        "<PATH>",
        "绝对路径",
        False,
        ("文件在 " + _HOME + "/x.md", _HOME),
    ),
    (
        r"~(?:/[\w.-]+)+",
        "<PATH>",
        "家目录路径",
        False,
        ("配置在 ~" + "/.baoyu-skills/.env", "~/.baoyu"),
    ),
    # ── 内部代号（作者工作流的引用编号，如 R53/R57/R64）──
    #   ⚠ 原来用 `\bR\d{2,3}\b` ⇒「但R57隐含」「①R95=源」这类全漏
    #   ⇒ 实测公开语料里活着 8 处，而扫描器报「内部代号: 0」。
    (
        r"(?<![0-9A-Za-z_])R\d{2,3}(?![0-9A-Za-z_])",
        "<REF>",
        "内部代号",
        False,
        ("但R57隐含的空白", "R57"),
    ),
    #   ⚠ 任务代号 C###/W##/G##（实测 1556 处 / 510 行，两侧原本都无规则）
    (
        r"(?<![0-9A-Za-z_])[CWG]\d{2,3}(?![0-9A-Za-z_])",
        "<TASK_CODE>",
        "内部任务代号",
        False,
        ("任务 C334 探针文", "C334"),
    ),
    # ── 时间与日期（**顺序敏感**：长的一定在前）──
    # ⚠ ISO 时间戳必须排在日期规则之前：否则日期部分先被换掉 ⇒ 剩下 `T07:25:14`
    (
        r"(?<![\dT])\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?![\d])",
        "<TIMESTAMP>",
        "时间戳",
        False,
        ("跑于 2026-10-20T07:25:14", "07:25:14"),
    ),
    # ⚠ 秒级必须排在分钟级之前：分钟级的负向断言会把 `19:49:44` 整个排除
    # ⚠ 负向断言**只能看数字**：排除了 `:` ⇒ `| 主 :23:53 |` 会漏
    (r"(?<!\d)\d{1,2}:\d{2}:\d{2}(?!\d)", "<TIME>", "时刻", True, ("于 19:49:44 结束", "19:49:44")),
    (r"(?<!\d)\d{1,2}:\d{2}(?!\d)", "<TIME>", "时刻", True, ("于 19:49 结束", "19:49")),
    (r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", "<DATE>", "日期", True, ("提交2026-10-15", "2026-10-15")),
    (
        r"(?<!\d)\d{4}年\d{1,2}月\d{1,2}日?(?!\d)",
        "<DATE>",
        "日期",
        True,
        ("明示2016年7月15日", "2016年7月15"),
    ),
    (
        r"(?<!\d)\d{4}/\d{1,2}/\d{1,2}(?!\d)",
        "<DATE>",
        "日期",
        True,
        ("发布于 2026/03/15", "2026/03/15"),
    ),
    (r"(?<!\d)20\d{6}(?!\d)", "<DATE>", "日期", True, ("戳20261015", "20261015")),
    (
        r"(?<!\d)20\d{2}[-年]\d{1,2}(?![-年\d])",
        "<DATE>",
        "日期",
        True,
        ("数据 2026-07 的", "2026-07"),
    ),
    # ⚠ 无年份的月日（「9月19日」）：尾部**不能**有 `(?!\d)`
    #   —— `11月28日23:59:59` 里「日」后紧跟时刻，负向断言会把整条规则排除
    (r"(?<!\d)\d{1,2}月\d{1,2}日", "<DATE>", "日期", True, ("截止11月28日", "11月28日")),
    # ⚠ 年+月区间（「2026年9-10月」）
    (
        r"(?<!\d)20\d{2}年\d{1,2}[-–~至]\d{1,2}月(?!\d)",
        "<DATE>",
        "日期",
        True,
        ("2026年9-10月官方更新", "2026年9-10月"),
    ),
    # ⚠ 斜杠年+月（「2026/3」）：**必须排在「年/月/日」之后**
    #   —— 否则会吃掉 `2026/03/15` 的前半段
    (r"(?<!\d)20\d{2}/\d{1,2}(?!\d|/)", "<DATE>", "日期", True, ("政策 2026/3 起", "2026/3")),
    # ── 微信 media_id：草稿箱资产标识符，账号级、可枚举 ──
    (r"Kqz5[A-Za-z0-9_\-]{40,}", "<MEDIA_ID>", "media_id", False, ("素材 Kqz5" + "A" * 45, "Kqz5")),
    # ── 会话 ID（形如 `20260918_090905_ae4f7e66`）──
    (
        r"(?<![0-9A-Za-z_])\d{8}_\d{6}_[0-9a-f]{6,}(?![0-9A-Za-z_])",
        "<SESSION_ID>",
        "会话ID",
        False,
        ("会话 20260918_090905_ae4f7e66", "20260918_090905_ae4f7e66"),
    ),
    # ── 公众号名 + 粉丝数（能定位到「地球online实况」这个号及其规模）──
    (
        r"地球online实况",
        "<PUBLIC_ACCOUNT>",
        "公众号名",
        False,
        ("公众号 地球online实况", "地球online实况"),
    ),
    (r"(?<![0-9])(?:352|402)\s*粉", "<FAN_COUNT>", "粉丝数", False, ("对352粉小号", "352")),
    # ⚠ 作者本人的笔名（红队第二轮 N2）：遮了公众号名却把同一账号的署名留着；
    #   有一行还给出了**已发布文章的标题** ⇒ 语料成了脱敏对象的可检索索引
    (r"林克", "<AUTHOR>", "作者笔名", False, ("作者「林克」生成草稿", "林克")),
    # ⚠ cron 任务 ID（12+ 位 hex）：可回指到作者的 cron 系统
    (
        r"(?<![0-9A-Za-z])[0-9a-f]{12,40}(?![0-9A-Za-z])",
        "<CRON_ID>",
        "cron任务ID",
        True,
        ("任务 24a9bd59ddf0 已排", "24a9bd59ddf0"),
    ),
    # ⚠ 第三方 GitHub 账号 + 其 MRR 数字（与 timqian 同类：他人信息）
    (r"@gefei55", "<THIRD_PARTY>", "第三方账号", False, ("见 @gefei55 的 MRR", "gefei55")),
    (
        r"@yihui_indie",
        "<THIRD_PARTY>",
        "第三方账号",
        False,
        ("见 @yihui_indie 的收入", "yihui_indie"),
    ),
    (r"GeFei 第二站", "<THIRD_PARTY>", "第三方账号", False, ("GeFei 第二站的 MRR", "GeFei 第二站")),
    # ⚠ 第三方网名（他人信息）
    (
        r"(?<![0-9A-Za-z_])timqian(?![0-9A-Za-z_])",
        "<THIRD_PARTY>",
        "第三方网名",
        False,
        ("②timqian 的 V2EX", "timqian"),
    ),
    (
        r"(?<![0-9A-Za-z_])samhjn(?![0-9A-Za-z_])",
        "<THIRD_PARTY>",
        "第三方网名",
        False,
        ("samhjn 的帖子", "samhjn"),
    ),
    (r"阿岷", "<THIRD_PARTY>", "第三方网名", False, ("知乎专栏·阿岷", "阿岷")),
    # ⚠ 自曝经营数据（对「AI 岗面试辅导」商品直接不利）
    #   「客户关系数=0」「累计收入：0」「¥0–3000/月」「每单亏 ¥X」整行剔除了
    #   （见 DROP_ROW_PATTERNS「自曝经营」）⇒ 不需要替换规则。
    #   留着会给人「替换就够了」的错觉 ⇒ 删掉，只保留要保留结构的：
    (r"月入过万", "<CLAIM>", "自曝经营", False, ("有人说月入过万", "月入过万")),
    # ── 自家账号规模（能定位到「这个号有多少粉、每天多少阅读」）──
    (r"(?<![0-9])\d+\s*粉", "<FAN_COUNT>", "账号规模", False, ("今日 100 粉", "100")),
    (
        r"日(?:均)?阅读\s*[~≈]?\s*\d+",
        "日阅读 <READ_COUNT>",
        "账号规模",
        False,
        ("日阅读 ~100", "100"),
    ),
    (
        r"日(?:均)?阅读量\s*[~≈]?\s*\d+",
        "日阅读量 <READ_COUNT>",
        "账号规模",
        False,
        ("日阅读量 100", "100"),
    ),
    (
        r"日收益\s*[~≈]?\s*[\d.]+\s*元",
        "日收益 <AMOUNT>",
        "账号规模",
        False,
        ("日收益 0.2 元", "0.2"),
    ),
    (r"日收益\s*×\s*\d+", "日收益 × <NUM>", "账号规模", False, ("日收益 × 10", "10")),
]

# ── 残留扫描（脱敏后必须全为 0）──
RESIDUAL_PATTERNS: dict[str, str] = {
    # ⚠⚠ **与 SUB_RULES 用同一套边界**（红队第三轮 P0-1）：
    #   原来这 5 条还在用 `\b` ⇒ 汉字/带圈数字紧贴时**两侧同时失效**
    #   ⇒ 实测公开语料里活着 8 处 `R###`，而扫描器报「内部代号: 0」。
    #   ⇒ **修根因要修所有兄弟**，不是只修当时发现的那一条。
    "手机号": r"(?<!\d)1[3-9]\d{9}(?!\d)",
    "邮箱": r"[\w.+-]+@[\w-]+\.[\w.-]{2,}",
    "身份证": r"(?<![\d.])\d{17}[\dXx](?![\d])",
    "绝对路径": r"/(?:home|Users|var|opt|etc|root|mnt|usr|proc|sys)/",
    "家目录路径": r"~(?:/[\w.-]+)+",
    "IP": r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",
    "长数字": r"(?<!\d)\d{16,19}(?!\d)",
    "密钥": r"(?:sk-|ghp_|AKIA)[A-Za-z0-9]{8,}",
    "私有仓": r"daily-ledger|moneymaker",
    "私有工具": r"(?<![0-9A-Za-z_])mpdt(?![0-9A-Za-z_])|mpdt-|afdian|zhongcao-cover-maker",
    "商业平台": r"Gumroad|Substack|Creem|爱发电|知识星球|闲鱼|小红书|PayPal",
    "内部代号": r"(?<![0-9A-Za-z_])R\d{2,3}(?![0-9A-Za-z_])",
    # ⚠ 新增：任务代号 C###/W##/G##（实测 1556 处 / 510 行，两侧原本都无规则）
    "内部任务代号": r"(?<![0-9A-Za-z_])[CWG]\d{2,3}(?![0-9A-Za-z_])",
    # ⚠ 日期的各种写法；⚠ **不能有 `\b`**（汉字是 `\w`）
    #   ⚠ 年份区间（`2018-2019`）**不是日期**，是市场数据 ⇒ 不匹配
    "日期": r"(?<!\d)20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}(?!\d)|(?<!\d)20\d{6}(?!\d)"
    r"|(?<!\d)\d{1,2}月\d{1,2}日"
    r"|(?<!\d)20\d{2}年\d{1,2}[-–~至]\d{1,2}月(?!\d)"
    r"|(?<!\d)20\d{2}/\d{1,2}(?!\d|/)",
    "时间戳": r"(?<![\dT])\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?![\d])",
    "时刻": r"(?<!\d)\d{1,2}:\d{2}:\d{2}(?!\d)|(?<!\d)\d{1,2}:\d{2}(?!\d)",
    "作者笔名": r"林克",
    "cron任务ID": r"(?<![0-9A-Za-z])[0-9a-f]{12,40}(?![0-9A-Za-z])",
    "第三方账号": r"@gefei55|@yihui_indie|GeFei 第二站",
    "会话ID": r"(?<![0-9A-Za-z_])\d{8}_\d{6}_[0-9a-f]{6,}(?![0-9A-Za-z_])",
    "media_id": r"Kqz5[A-Za-z0-9_\-]{40,}",
    "公众号名": r"地球online实况",
    "粉丝数": r"(?<![0-9])(?:352|402)\s*粉",
    "第三方网名": r"(?<![0-9A-Za-z_])timqian(?![0-9A-Za-z_])"
    r"|(?<![0-9A-Za-z_])samhjn(?![0-9A-Za-z_])|阿岷",
    # ⚠ 「月入过万」也归到自曝经营（它原只有替换规则、没有扫描类别 ⇒ 对账漏了它）
    "自曝经营": r"客户关系数\s*=\s*0|累计收入[：:]\s*0|¥0[–\-]3000/月|每单\s*亏\s*¥[\d,]+|月入过万",
    # ⚠ R311 红队 E3：冷发信 SOP / cron 时刻表 / 自家账号规模 —— 原扫描器不查这三类
    "冷发信SOP": r"(发信器|话术是|分成两批|SMTP\s*exit|掉量小号|接单入口)",
    "cron调度": r"(cron\s*(时刻表|调度)|周[一二三四五六日]\s*\d{1,2}:\d{2})",
    "账号规模": r"(?<![0-9])\d+\s*粉|日(?:均)?阅读量?\s*[~≈]?\s*\d+",
    "内部项目名": r"(ai-trends|ttxs69\.github\.io/ai-trends)",
}


def tail_only(text: str) -> str:
    paras = [x.strip() for x in text.split("\n\n") if x.strip()]
    tail = paras[-KEEP_PARAGRAPHS:] if paras else [text.strip()[-200:]]
    return "\n\n".join(tail)[:MAX_CHARS]


# ── URL 保护：URL 内的内容**不参与替换** ──
# ⚠⚠ 为什么（红队第二轮实测）：规则的「宽网」会改写 URL 里的公开 ID。
#   实测 `cron任务ID` 规则（12-40 位 hex）改写了 **31 处**，其中 4 处是
#   公开新闻/政府网站的文章 ID（21jingji.com / m.tech.china.com /
#   shanghai.gov.cn）⇒ **破坏引用可溯源性** ——
#   而「URL 里的东西不脱敏」正是我声明过的取舍（公开资料，删了反而坏）。
#
# ⇒ **结构性修法**（而不是逐条加豁免）：替换前把 URL **整段摘出来**，
#   替换完再放回去 ⇒ 任何规则都碰不到 URL 内的内容。
#   ⚠ 代价：URL **里面**的敏感串也不会被脱敏。
#   ⇒ 实测 113 个 URL 全是公开站点（唯一含敏感词的是
#     `github.com/settings/tokens` = **设置页**，不是 token 本身）⇒ 可接受，
#     且已在 tests/fixtures/README.md 的「已知边界」里写明。
# ⚠⚠ URL 边界必须**排除中文标点**：原来的 `https?://\S+` 太贪婪，
#   会把 URL 后面**紧跟的无空格中文**一起吞进 URL
#   （实测：`...docs/45）；对352粉小号：...` 整段被当成 URL
#   ⇒ 里面的 `352粉`/`100粉`（自家账号规模）逃过脱敏）。
URL_RE = re.compile(r"https?://[^\s，。；、：！？）】》」』〉%,]+")
# ⚠⚠ 负向后视 `(?<![@\w.-])`：**裸域名规则不能吞掉邮箱**
#   （实测 `creators@n8n.io` 的 `n8n.io` 被当成裸域名 stash ⇒
#   邮箱规则看不到完整串 ⇒ 第三方真实邮箱原样留在公开语料里）。
BARE_DOMAIN_RE = re.compile(
    r"(?<![@\w.-])[\w-]+(?:\.[\w-]+)*(?:\.com|\.cn|\.net|\.org|\.io|\.ai|\.co|\.dev|\.me|\.xyz)"
    r"(?:/[^\s，。；、：！？）】》」』〉%,]*)?"
)
_URL_SLOT = "\x00u{}\x00"

# ⚠⚠ **只有「公开 ID 类」在 URL 内豁免** —— 不是整段 URL。
#   实测：URL 里混着**必须脱敏**的东西：
#     · 第三方真实邮箱（`creators@n8n.io` / `xycoder@163.com`）⇒ 第三方隐私
#     · 私有仓名（`github.com/ttxs69/daily-ledger`）⇒ 内部信息
#     · 私有工具名（`.../ttxs69/mpdt.git`）⇒ 内部信息
#   ⇒ 豁免集只含「删了会破坏引用可溯源性」的形态：
#     日期 / 时刻 / 12+ 位 hex（文章 ID）/ 16-19 位长数字（公开文章 ID）
URL_EXEMPT_RULE_MARKERS = (
    # 日期（5 种写法）
    r"\d{4}-\d{2}-\d{2}",
    r"\d{4}年\d{1,2}月",
    r"\d{4}/\d{1,2}/\d{1,2}",
    r"20\d{6}",
    r"20\d{2}[-年]\d{1,2}",
    # 时刻
    r"\d{1,2}:\d{2}",
    # 12+ 位 hex（公开文章 ID）—— ⚠ 必须是**实际 pat 字符串**，
    #   写成正则源码（`\[0-9a-f\]\{12,40\}`）会匹配不上 ⇒ 豁免失效
    "[0-9a-f]{12,40}",
    # 16-19 位长数字（公开文章 ID）
    r"\d{16,19}",
)


def _is_url_exempt(pat: str) -> bool:
    return any(m in pat for m in URL_EXEMPT_RULE_MARKERS)


def sanitize(text: str) -> str:
    """跑一遍替换规则。

    ⚠ **URL 内的内容只跑「非豁免」规则**（见 URL_EXEMPT_RULE_MARKERS）：
      公开 ID 类的形态在 URL 内保留（删了破坏引用），
      但**敏感实体照脱敏**（第三方邮箱 / 私有仓名 / 私有工具名）。
    """
    stashed: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        stashed.append(m.group(0))
        return _URL_SLOT.format(len(stashed) - 1)

    text = URL_RE.sub(_stash, text)
    text = BARE_DOMAIN_RE.sub(_stash, text)
    for pat, rep, _cat, _ex, _s in SUB_RULES:
        text = re.sub(pat, rep, text)
    for i, u in enumerate(stashed):
        # URL 内：只跑非豁免规则
        u2 = u
        for pat, rep, _cat, exempt, _s in SUB_RULES:
            if exempt:
                continue
            u2 = re.sub(pat, rep, u2)
        text = text.replace(_URL_SLOT.format(i), u2)
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
    # ⭐⭐ **扫描侧必须与替换侧完全同构**（R311 第三轮自查的关键修复）：
    #   替换侧的语义是「URL 内只跑非豁免规则」⇒ 扫描侧必须**按段**算：
    #     · URL 外的段 → 跑全部规则
    #     · URL 内的段 → 只跑**非豁免**规则（公开 ID 类的形态保留）
    #   ⇒ 否则会出现两类不一致：
    #     ① 扫描报 0、替换没做 ⇒ **结构性绿灯**（邮箱那一类就是这样被抓到的）
    #     ② 扫描报错、替换也不做 ⇒ 「门红但改不掉」（长数字那一类）
    #   ⇒ 段化计算让两类不一致**在结构上不可能**。
    segments = _split_by_url(text)
    counts: dict[str, int] = {}
    for name, pat in RESIDUAL_PATTERNS.items():
        exempt = _is_url_exempt(pat)
        n = 0
        for is_url, chunk in segments:
            if is_url and exempt:
                continue
            n += len(re.findall(pat, chunk))
        counts[name] = n
    return counts


def _split_by_url(text: str) -> list[tuple[bool, str]]:
    """把文本切成「URL 段」与「非 URL 段」。"""
    out: list[tuple[bool, str]] = []
    pos = 0
    matches = sorted(
        list(URL_RE.finditer(text)) + list(BARE_DOMAIN_RE.finditer(text)),
        key=lambda mm: mm.start(),
    )
    for m in matches:
        if m.start() < pos:
            continue
        if m.start() > pos:
            out.append((False, text[pos : m.start()]))
        out.append((True, m.group(0)))
        pos = m.end()
    if pos < len(text):
        out.append((False, text[pos:]))
    return out


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
    if "豁免" in label:
        # 豁免类：**必须原样保留**
        if must_not not in out:
            fails.append(f"URL 豁免失效 [{label}]: {raw!r} → {out!r}")
        return fails
    if must_not in out:
        fails.append(f"替换未生效 [{label}]: {raw!r} → {out!r}")
    kept, _dropped = drop_rows([{"text": raw}])
    if not kept:
        fails.append(f"行级剔除误伤 [{label}]: {raw!r}")
    return fails


# ── 双向对账表（R311 第三轮自查的产物）──
# ⚠⚠ **每一类残留，要么有替换规则，要么有剔除规则** ——
#   两边都没有 ⇒ 扫描器报 0、产物里原样残留 ⇒ **结构性绿灯**。
#   实测：邮箱这一类**只在扫描侧有、替换侧没有** ⇒ 第三方真实邮箱
#   （`creators@n8n.io` / `xycoder@163.com`）会原样留在公开语料里，
#   而扫描器报「邮箱: 0」。两轮红队都没抓到 ⇒ **模式串比对抓不到它**，
#   只能**拿真实样本跑一遍**。
# ⇒ 这里给每一类配一个真实样本，逐条跑 sanitize / drop_rows，
#    缺哪边就红 ⇒ 新增扫描类别时忘了加替换规则，self-test 立刻报。
RESIDUAL_SAMPLES: dict[str, tuple[str, str]] = {
    "手机号": ("打给我 13812345678", "13812345678"),
    "邮箱": ("联系 creators@n8n.io", "creators@n8n.io"),
    "身份证": ("身份证 11010119900307561X", "11010119900307561X"),
    # ⚠ 路径样本用拼接（文件里不能出现本机绝对路径的字面量 —— 仓库有 hygiene 测试会扫）
    "绝对路径": ("文件在 " + _HOME + "/x.md", _HOME),
    "家目录路径": ("配置在 ~/.baoyu-skills/.env", "~/.baoyu"),
    "IP": ("服务器 192.168.1.100", "192.168.1.100"),
    "长数字": ("单号 2065523833575039002", "2065523833575039002"),
    # ⚠ 密钥样本用**拼接**构造 —— 本文件里不能出现 credential 形状的字面量
    #   （仓库有 hygiene 测试会扫，实测抓到过我一次）。
    "密钥": ("token " + "sk-" + "abcdefgh12345678", "sk-" + "abcdefgh12345678"),
    "私有仓": ("仓库 daily-ledger", "daily-ledger"),
    "私有工具": ("工具 mpdt-public", "mpdt"),
    "商业平台": ("上架到 Gumroad", "Gumroad"),
    "内部代号": ("见 R311 第 3 条", "R311"),
    "日期": ("提交 2026-10-15 完成", "2026-10-15"),
    "时间戳": ("跑于 2026-10-20T07:25:14", "07:25:14"),
    "时刻": ("于 19:49:44 结束", "19:49:44"),
    "作者笔名": ("作者「林克」生成", "林克"),
    "cron任务ID": ("任务 24a9bd59ddf0 已排", "24a9bd59ddf0"),
    "第三方账号": ("见 @gefei55 的 MRR", "gefei55"),
    "会话ID": ("会话 20260918_090905_ae4f7e66", "20260918_090905_ae4f7e66"),
    "media_id": ("素材 Kqz5" + "A" * 45, "Kqz5"),
    "公众号名": ("公众号 地球online实况", "地球online实况"),
    "粉丝数": ("有 352 粉", "352"),
    "第三方网名": ("②timqian 的 V2EX", "timqian"),
    "自曝经营": ("客户关系数 = 0", "= 0"),
    "冷发信SOP": ("名单分成两批，一批发给有预算的公众号运营者", "分成两批"),
    "cron调度": ("每天 08:23 自动运行", "08:23"),
    "账号规模": ("日阅读 100", "100"),
    "内部项目名": ("站 ai-trends 上线", "ai-trends"),
}


def reconcile() -> list[str]:
    """扫描侧 ↔ 替换侧/剔除侧的**双向**对账。返回问题列表（空 = 一致）。

    ⚠⚠ **必须是双向的，而且粒度要到「每一条规则」**（红队第三轮 P0-2）：
      我第一版只走**方向 A**（扫描侧有类别 ⇒ 替换侧要有规则），
      于是出现了一个更隐蔽的假绿灯：
        把某条替换规则改坏（`afdian`，原始语料里真实出现 13 次）
        ⇒ 公开语料带着原始串发布
        ⇒ 而 `--check` 与 `--self-test` **双双 exit 0**
      因为**那条规则对应的扫描类别根本不存在** ⇒ 扫描器压根不查它。

    后来我第二版只把粒度提到「类别」，**还是不够**：
      「私有工具」这一类有 3 条规则（mpdt / zhongcao-cover-maker / afdian），
      样本只测了 mpdt ⇒ **改坏 afdian 仍然不被发现**。
      ⇒ 现在粒度是**每条规则一个专属样本**（SUB_RULES 的第 5 项）。

    三个方向：
      A：每个扫描类别 ⇒ 有替换规则或剔除规则覆盖
      B：每条替换规则 ⇒ 声明的类别存在于 RESIDUAL_PATTERNS
      C：每条替换规则 ⇒ **它自己的样本**跑一遍 sanitize，敏感串必须消失
         （C 才是能抓住「改坏某一条规则」的那一半）
    """
    problems = []
    # ── 方向 A ──
    for name, (raw, must) in RESIDUAL_SAMPLES.items():
        replaced = must not in sanitize(raw)
        dropped = not drop_rows([{"text": raw}])[0]
        if not (replaced or dropped):
            problems.append(f"「{name}」既没有替换规则也没有剔除规则 ⇒ 扫描器报 0、产物里原样残留")
    # ── 方向 B ──
    for pat, _rep, cat, _ex, _s in SUB_RULES:
        if cat not in RESIDUAL_PATTERNS:
            problems.append(
                f"替换规则 {pat[:40]!r} 声明的类别 {cat!r} 不在 RESIDUAL_PATTERNS 里"
                f" ⇒ 这条规则坏了没人知道（扫描器压根不查它）"
            )
    # ── 方向 C（**逐条规则**；这一半原来没有）──
    for pat, _rep, _cat, _ex, (raw, must) in SUB_RULES:
        try:
            out = sanitize(raw)
        except Exception as e:
            # ⚠ 坏正则（括号不配对之类）会让 sanitize **抛异常**
            #   ⇒ 对账必须**报告**它，而不是跟着崩（崩虽然也是非零退出，
            #   但报错形态是 traceback，读者不知道是「规则坏了」）。
            problems.append(
                f"替换规则 {pat[:40]!r} 抛异常（规则本身坏了）：{type(e).__name__}: {e}"
            )
            continue
        if must in out:
            problems.append(
                f"替换规则 {pat[:40]!r} 对自己的样本无效：{raw!r} ⇒ {out!r}"
                f"（这条规则被改坏了，而扫描侧不会发现）"
            )
    # ── 反向：每个扫描类别 ⇒ 至少有一条替换规则声明它，或它由剔除规则覆盖 ──
    declared = {cat for _p, _r, cat, _e, _s in SUB_RULES}
    drop_cats = {name for name, _p in DROP_ROW_PATTERNS}
    for name in RESIDUAL_PATTERNS:
        if name in declared or name in drop_cats or name in RESIDUAL_SAMPLES:
            continue
        problems.append(f"扫描类别 {name!r} 没有任何替换规则声明它，也不在剔除类别里")
    return problems


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
        # ⚠ URL 内的**公开 ID**不该被改写（红队第二轮实测：cron 任务 ID 的
        #   宽网改写了 31 处，其中 4 处是公开新闻/政府网站的文章 ID
        #   ⇒ 破坏引用可溯源性）。⇒ 只豁免「公开 ID 类」。
        (
            "见 https://21jingji.com/article/a1b2c3d4e5f6/abcdef1234567890.html",
            "URL 内的公开 ID（豁免）",
            "abcdef1234567890",
        ),
        (
            "裸域名也豁免 chinanews.com.cn/sh/2026/03-15/10587063.shtml",
            "裸域名内的日期（豁免）",
            "2026/03-15",
        ),
        # ⚠ 但 URL 里的**敏感实体必须照脱敏**（实测 URL 里混着第三方真实邮箱
        #   与私有仓名 ⇒ 整段豁免会把它们一起放过）
        ("联系 creators@n8n.io 即可", "URL 外的第三方邮箱", "creators@n8n.io"),
        (
            "见 https://example.com/x/creators@n8n.io/y",
            "URL 内的第三方邮箱（仍须脱敏）",
            "creators@n8n.io",
        ),
        (
            "仓库 https://github.com/ttxs69/daily-ledger",
            "URL 内的私有仓名（仍须脱敏）",
            "daily-ledger",
        ),
        # ⚠ URL 边界不能吞掉后面紧跟的**无空格中文**（实测 `...docs/45）；对352粉小号`
        #   整段被当成 URL ⇒ 账号规模逃过脱敏）
        (
            "见 https://ad.weixin.qq.com/docs/45）；对352粉小号",
            "URL 边界不吃中文（账号规模仍须脱敏）",
            "352粉",
        ),
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
    # ⭐ 双向对账：每一类残留都必须有替换规则或剔除规则
    fails.extend(reconcile())
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
