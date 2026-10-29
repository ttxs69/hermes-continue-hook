"""Pattern engine for continue-hook.

Transport layer only, zero judgment power: detection decides *whether* to
nudge. Deciding whether the caught question was legitimate is the agent's
job, done via the nudge prompt (see __init__.py).

All patterns are user-configurable via ~/.hermes/continue_hook.json and
hot-reload on file change. A broken config file falls back to defaults —
it must never crash the gateway.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ⚠ 所有会被真正判定的 trigger 键，**集中声明在这一处**。
# 2026-10-29 红队 S6：注册逻辑原来散在 3 个地方（元组 + 两个独立分支），
# 加第 6 个键仍会静默走 no_trigger。测试断言 set(triggers) == DISPATCHED，
# 任何一边漏了都会红。
DISPATCHED_TRIGGERS = frozenset({"promise", "offer", "hand_off", "confirm_seek", "question_mark"})

__all__ = ["DEFAULTS", "ConfigStore", "build_nudge", "default_config", "detect"]

# Quoted spans are *mentions* of a phrase, not *uses* of it. Trigger matching
# runs on the quote-stripped tail (found via live dogfooding: a report that
# quoted the trigger patterns fired the trigger). Exclusion matching still
# scans the RAW tail — the safe direction (skip more, nudge less).
_QUOTE_RE = re.compile(
    r"「[^」]{0,80}」|『[^』]{0,80}』|\u201c[^\u201d]{0,80}\u201d|'[^']{0,60}'|\"[^\"\n]{0,120}\""
)  # ASCII double quotes are the most common way to quote in English

# A question mark inside a URL is not a question. Without this, a reply that
# ends with a link ("docs at https://example.com/x?y=1") looked like a
# permission-seeking ending.
_URL_RE = re.compile(r"https?://[^\s<>）)\]\"']+")
_MARKDOWN_LINK_RE = re.compile(r"\]\([^)]*\)")
# A "?" inside a code block or inline code is syntax, not a question.
# A closing fence may be missing (the reply was truncated, or the model wrote an
# opening fence by hand) — everything after an opening fence is still code.
_CODE_BLOCK_RE = re.compile(r"```.*?```|```[\s\S]*$", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]{1,120}`")

# 「说了下一步但没做」的专用文案。2026-10-29 实测：agent 用陈述式承诺
# （「下一步我去查 X」）收尾时，原四类触发（offer/hand_off/confirm_seek/question_mark）
# 一个都命中不了 —— 因为它没有问号、没把决定推给用户，而是**自己承认还有活就交回合了**。
PROMISE_NUDGE_TEMPLATE = (
    "【continue-hook】Your last reply ended with a promise about what you would do next "
    "(e.g. 「下一步我去查…」/ 「Next I'll…」), and then the turn ended.\n"
    "You said you would do it — so do it now, in this turn.\n"
    "1. If that next step needs something only the user has (credentials, a decision, "
    "money, real-name identity, external publishing, deleting or overwriting data), "
    "ask for that ONE thing in a single line, then stop.\n"
    "2. Otherwise run it now: call the tool, read the result, then report result + "
    "verification + remaining risks.\n"
    "If you already carried that step out, say so in one line and stop — "
    "that is a complete answer, not a repeat.\n"
    "Do not repeat the promise. Either do it, or name the single blocker.\n"
    "(auto nudge #{attempt}; do not mention this nudge in your final reply.)"
)

NUDGE_TEMPLATE = (
    "【continue-hook】Your last reply ended by asking the user a question.\n"
    "Self-check once, per standing discipline:\n"
    "1. Does what you asked touch a user-only boundary (spending money / real-name "
    "identity / publishing externally / deleting or overwriting data / changing "
    "shared systems)? If yes → re-ask in ONE short line (that single decision "
    "only), then stop.\n"
    "2. Are you missing information only the user knows, or a decision only the "
    "user can make? If yes → re-ask in ONE short line, then stop.\n"
    "3. Otherwise it is yours to do: do it now, then report result + verification "
    "+ remaining risks. Do not ask again.\n"
    "(auto nudge #{attempt}; do not mention this nudge in your final reply.)"
)

DEFAULTS: dict = {
    "enabled": True,
    "dry_run": False,  # log-only rollout: record what would fire, never inject
    # Caps. The per-turn counter resets when a turn ends without a nudge, so
    # "per turn" means per turn — not per session (the old implementation never
    # reset it, so two nudges silenced the hook for the rest of the session).
    "max_nudges_per_turn": 2,
    "max_nudges_per_session": 20,  # backstop for hosts without a host-side cap
    # Optional session-level rate limit between two nudges. Disabled by default:
    # the caps above already bound the loop, and a non-zero value silently
    # stops the hook from working for minutes after the first nudge.
    "cooldown_seconds": 0,
    "tail_chars": 240,
    # Triggers: any hit makes the reply a nudge candidate.
    "triggers": {
        # Announced-but-undone: the agent said what it would do next, then ended the
        # turn without doing it. This is worse than an offer — an offer at least asks;
        # this one silently drops the work. Matched on the CLOSING TAIL only, so a
        # narrative "下一步是…" in the middle of a report does not fire.
        # ⚠ 只抓「第一人称 + 动词」的承诺。
        # 2026-10-29 误伤实测（33 条真实回复里 6 条被误拦，18.2%）：
        #   · `\bnext step\b` 太宽 —— "next step is **your call**"（交给用户）
        #     和 "next step **needs your** decision"（要用户决定）都被当成承诺；
        #   · 「下一步**要** API key，你发我」里「要」是"需要"不是"将要"；
        #   · 代码块里的 `# TODO:` 被当成承诺（因为 tail 取的是原文，见 detect()）。
        # ⇒ 收紧成必须出现第一人称，或明确的"我将要做"动词。
        "promise": [
            # 标点/空格变体：红队 2026-10-29 实测，「下一步：我去…」「下一步，我会…」
            # 「下一步I'll…」（无空格）原先全部漏检，12 条变体里漏 7 条。
            # ⚠ 不含「先」：「关于下一步：我**先**做了 A/B 对比」是回顾，不是承诺
            # （红队语料 B3，2026-10-29 实测误伤）。
            r"下一步[：:，,、]?\s*我(会|要|将|去|来|就)",
            r"接下来[：:，,、]?\s*我(会|要|将|去|来|就)",
            # 「稍后/待会」也是承诺的常见说法。
            # ⚠ (去|来|会) **必需**：「我这就跑完了」是已完成，把它当承诺是误伤
            # （红队语料 D1）。宁可漏「我这就跑数据」，也不误拦完成报告。
            r"我(这|现在|马上|立刻|随后|稍后|待会|一会儿)(就)?(去|来|会)"
            r"(查|看|做|写|改|跑|验|测|读|拉|试|补|算|加)",
            r"(下面|接着)[：:，,、]?\s*我(会|要|去|来|跑)",
            # ⚠ 要求同行有第一人称：红队实测「遗留 TODO：把 3 个 flaky 测试改成
            # deterministic（已记进 issue）」和 cron 交棒报告里的「待办：C406 / C407」
            # 都被误拦 —— 那些是 backlog 清单，不是本轮承诺。
            r"(待办|TODO)[:：]\s*(我|I\b)",
            r"\bnext,? i(?:'|’)ll\b",
            r"\bi(?:(?:'|’)ll| will) (?:now |then |go )?(?:check|look|do|write|run|read|verify|pull|try|fix|build)\b",
            r"\bmy next step (?:is|will)\b",
            r"\bnext steps?:? (?:i|we)(?:'|’)?(?:ll| will)\b",
            r"\bnext up[:：]?\s*(?:i|we)(?:'|’)?(?:ll| will)\b",
            r"\bi(?:'|’)m going to (?:check|look|do|run|read|verify|pull)\b",
            r"\bthen i(?:(?:'|’)ll| will)\b",
            r"\blet me (?:do|check|look|run|verify) (?:that|this) next\b",
        ],
        # First-person offers — pushing a self-advancable decision back to the user.
        "offer": [
            "要不要我",
            "要不要顺便",
            "是否需要我",
            "要不要继续",
            "需要我.{0,8}(做|写|建|发|补|加|出|跑|查|弄|搞)",
            "我可以(帮)?(你|您)?.{0,8}(做|写|建|发|补|加|出|跑|查|弄|搞)",
            "我来做还是",
            "等我(先)?(做|写)完(再)?(给你|你)",
            r"should i\b",
            r"do you want me to\b",
            r"shall i\b",
            r"want me to\b",
        ],
        # Hand-offs — deferring the decision without a question mark.
        "hand_off": [
            "(交给|留给)你(来)?(定|决定|判断|选)",
            "你(来)?(定|决定|选)一下",
            "听你的",
            "等你(拍板|指示|点头|回复|确认)",
            r"(up to you|your call|let me know if)",
        ],
        # Confirmation-seeking endings.
        "confirm_seek": [
            "这样(可以|行)吗",
            "没问题吧",
            "确认一下[？?]?$",
            "如果.{1,12}，要不要",
            r"(does this (work|look ok)|thoughts\?)",
        ],
        # Any question mark in the closing tail. True/false is judged by the
        # agent inside the nudge, not here.
        "question_mark": True,
    },
    # Exclusions: any hit short-circuits to skip — legitimate asks and real
    # blockers. Better to miss a nudge than to interrupt a real question.
    "exclusions": {
        # ⚠ `hard_boundary` = **不可逆 / 对外** 的动作。promise 触发**不能**抢在它前面。
        #
        # 2026-10-29 红队阻断：上一版让 promise 先于 boundary 判定，于是
        # 「下一步我去付款开通」「下一步我去把生产库的旧表删掉」从 1.3.5 的
        # **代码级放行** 变成被 nudge —— nudge 文案豁免写的是「需要用户独有的东西」
        # （持有物判据），而 agent 手里有 DB 凭据、有 npm token 时这条为假，
        # 它完全可以顺势删库/发版。**用散文替代硬门 = 护栏回退。**
        #
        # 教训：promise 只该抢在**软名词**前面（「付费证据」「成本」「报价」这类
        # 只是话题词，不是动作）。真正不可逆的动作必须仍然在代码里短路。
        "hard_boundary": [
            r"(付款|支付|充值|下单|购买|转账|打钱|汇款|签约|签合同)",
            r"(删除|删掉|覆盖|清空|格式化|drop\s+table|truncate)",
            r"(发布|上线|部署|push|force-push|npm\s+publish|pip\s+upload|deploy|release)",
            r"(生产库|生产环境|线上环境|主分支|main\s+branch|production|prod\b)",
            r"(实名|认证|授权|签约|开票)",
            # ⚠ `pay attention` / `pay close attention` 不是付款 —— 词表必误伤，
            # 2026-10-29 实测：原有回归测试「Should I pay attention to the lint
            # warnings?」被 hard_boundary 的 `\bpay\b` 放行，属真回归。
            r"\bpay(?!\s+(?:close\s+)?att(?:ention|n))\b(?!\s+(?:heed|tribute))\b"
            r"|\b(purchase|checkout|subscribe|upgrade to (?:pro|paid|premium))\b",
            r"\b(delete|drop|truncate|overwrite|wipe|rm\s+-rf)\b",
            r"\b(publish|deploy|release|force[- ]push)\b",
        ],
        # User-only boundaries: money, identity, credentials, publishing,
        # destructive actions.
        "boundary": [
            "花钱|付费|充值|批准|审批|实名|密码|验证码|付款|掏钱|报销|"
            "买(?!账)|购买|下单|付钱|转账|转钱|打钱|汇款|签约|签合同|雇|把钱|这笔钱|付款|报价",
            "对外发布|发布出去|删掉|删除数据|授权|发(给|到).{0,6}(客户|用户|对方|群|他|她|公众号|子|列表|名单)|"
            "发布(到|至|上)|推(送)?到|上线|导出.{0,4}(数据|客户|用户)|"
            "npm|pypi|npmjs|github release|生产(环境|库|服务器)",
            "生产环境|线上环境|主分支|改数据库|重启服务",
            # Money, credentials, destructive actions and external sending are
            # all user-only. The English list has to be as wide as the Chinese
            # one — an English-speaking agent used to talk itself straight
            # through a payment, a deletion or a send.
            r"\b(paid|paying|prepay|subscription|upgrade to (pro|paid|premium))\b|"
            r"\b(cost|costs|charge|charges|spend|spent|price|priced|budget|fee|fees)\b|"
            # Money verbs need a money object. A bare `pay`/`buy`/`transfer`
            # blocked ordinary work ("pay attention to the lint warnings",
            # "transfer the helper into its own module") — 47% of realistic
            # endings lost their nudge to this.
            r"\b(payment|purchase|renew|refund|invoice|billing|subscription|paid)\b|"
            r"\bpay(?:ing)?\b(?!\s+attention)\s*(?:for\b|the\b|\$|\d|him|her|them|it\b)|"
            r"\bbuy\s+(?:the|a|an|this|that|it|them|more|\$|\d)\b|"
            r"\b(?:wire|transfer|remit)\s+(?:the\s+|my\s+|our\s+)?(?:money|funds|amount|payment|\$|\d)|"
            r"\bhire\s+(?:a\s+|an\s+|the\s+)?(?:contractor|freelancer|developer|dev|person|someone|agency|consultant)\b|"
            r"\bsign\s+(?:the\s+)?(?:contract|agreement|deal|nda|quote)\b|"
            r"\bget(?:ting)?\s+a\s+quote\b|"
            r"[$€£¥]\s*\d|\d\s*(usd|eur|cny|rmb|dollars?)\b|"
            r"\b(api key|api keys|secret|secrets|credential|credentials|token|"
            r"access key|private key|ssh key|passphrase|password|2fa|otp|verification code)\b|"
            r"\b(delete|deleting|deletion|remove|drop|truncate|purge|wipe|erase|"
            r"overwrite|reset --hard|force[- ]push|rm -rf)\b|"
            r"\b(publish|publishing|deploy|deploying|release|announce|tweet|"
            r"post (it|this|that)|make (it|this) public|merge (it|this|the pr))\b|"
            r"\b(send|email|e-?mail|forward|reply to|message|notify|dm|invoice)\b.{0,24}"
            r"\b(client|customer|user|users|them|him|her|subscriber|list|audience|boss|team)\b|"
            r"\b(production|prod|live (environment|server|site)|main branch|master branch)\b|"
            r"\b(to|into|onto) (main|master|prod|production|the main branch|the master branch)\b|"
            r"\bmerge\b.{0,20}\b(pr|pull request|branch)\b|"
            r"\b(rotate|revoke) (the |my |our )?(credentials|keys?|tokens?)\b|"
            r"\b(approve|approval|authorize|authorise|sign(ing)? (in|up) as you)\b",
        ],
        # Second-person requests: only the user can provide / decide this.
        "user_must": [
            "需要你|需要您|你告诉我|你来提供|等你(给|确认|决定之后)",
        ],
    },
}


def default_config() -> dict:
    """Deep copy of DEFAULTS."""
    return json.loads(json.dumps(DEFAULTS))


class ConfigStore:
    """Hot-reloadable config: ``~/.hermes/continue_hook.json`` overrides defaults.

    Cached by mtime; a missing or malformed file silently falls back to
    defaults. Never raises.
    """

    def __init__(self, path: Path):
        self.path = path
        self._mtime: float | None = None
        self._cfg: dict | None = None

    def load(self) -> dict:
        mtime = None
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            pass
        if self._cfg is not None and mtime == self._mtime:
            return self._cfg
        cfg = default_config()
        try:
            user = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception:
            pass  # bad file → defaults (never crash the gateway)
        self._mtime, self._cfg = mtime, cfg
        return cfg


def _compile(patterns) -> re.Pattern:
    # A string is one pattern, not a list of characters: `"offer": "要不要我"`
    # used to be iterated per character and matched almost anything.
    if isinstance(patterns, str):
        patterns = [patterns]
    pats = [p for p in (patterns or []) if isinstance(p, str) and p]
    if not pats:
        return re.compile(r"(?!x)x")  # never-matching pattern
    # Case-insensitive: the English triggers are written lowercase, so without
    # this flag "Should I…" (the most common form — it starts the sentence) did
    # not match `should i` at all and only the trailing "?" caught it. The
    # Chinese patterns are unaffected.
    return re.compile("|".join(f"(?:{p})" for p in pats), re.S | re.IGNORECASE)


def _int(value: Any, fallback: int) -> int:
    """A config value as an int, never raising.

    ``max_nudges_per_turn: two`` must not turn every stop into
    ``error:ValueError`` — the documented behaviour is "fall back to defaults".
    """
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def strip_code_and_links(text: str) -> str:
    """Remove code blocks, inline code, links and URLs. Pure.

    Applied to the WHOLE reply before the closing window is taken. Doing it
    the other way round meant a fenced block whose closing ``` fell outside the
    last 240 characters left its "?" in place — and "the reply ends with a code
    block" is one of the most common shapes.
    """
    for pattern in (_CODE_BLOCK_RE, _INLINE_CODE_RE, _MARKDOWN_LINK_RE, _URL_RE):
        text = pattern.sub(" ", text)
    return text


def _first_trigger_hit(triggers: dict, tail: str) -> str | None:
    """The name of the first trigger pattern that matches, or None. Pure."""
    # ⚠ 这里是**唯一**决定「哪些 trigger 会被检查」的地方 —— 只在 triggers 配置里
    # 加一个键、不在这里登记，那个键永远不会生效（2026-10-29 实测：promise 加了配置
    # 却全量 no_trigger，就是因为漏了这一行）。
    #
    # ⚠ `promise` **故意不在这里**：它在 detect() 里已经用「剥掉代码块/引用后的窗口」
    # 单独判定过一次（并且排在 boundary 之前）。放进这个元组会让它在这里被
    # **原文 tail** 再判一遍 —— 2026-10-29 实测：代码块里的 `# TODO:` 正是这样
    # 二次命中的，明明前面已经用干净窗口排除了。
    for name in ("offer", "hand_off", "confirm_seek"):
        if _compile(triggers.get(name)).search(tail):
            return name
    return None


def detect(text: str, attempt: int, cfg: dict) -> tuple[bool, str]:
    """Return ``(should_nudge, reason_code)``. Pure transport layer.

    The per-turn cap is applied **after** the text is judged: whether a reply
    ends the turn is a property of the text, and the caller has to know it even
    once the cap is reached — otherwise the nudge chain never closes and that
    session stays muted for good.
    """
    if not text or not str(text).strip():
        return False, "empty"
    full = str(text).strip()
    stripped = strip_code_and_links(full)
    tail_chars = _int(cfg.get("tail_chars", 240), 240)
    tail = full[-tail_chars:]
    ex = cfg.get("exclusions", {})
    tr = cfg.get("triggers", {})
    trig_tail = _QUOTE_RE.sub("", tail)  # quoted mentions are not asks
    # ---------------------------------------------------------------------
    # `promise` is checked BEFORE the boundary exclusions, on purpose.
    #
    # 2026-10-29 实测：真实收尾句「下一步我去查…有没有定价页和真实客户证言——
    # 那是该品类的**付费**证据…」被 boundary 的「付费」命中而整句放行。
    # 那是名词（"付费证据"），不是「我要去付款」—— 词表分不出来，加词表只会再漏。
    #
    # 优先级理由：boundary 存在的目的是「别打断用户真正的提问」；
    # 而 promise 是「agent 自己说要去做，却没做就结束回合」—— 它不是提问。
    # 即使那一步真的触及边界，PROMISE_NUDGE_TEMPLATE 的第 1 条已经写明
    # 「若需要用户独有的东西，就用一句话问那一件事然后停」⇒ 分支仍然安全。
    # ---------------------------------------------------------------------
    # ⚠ 必须用**剥掉代码块与引用**的 tail：`tail` 是原文，代码块里的
    # `# TODO: refactor` 会被当成承诺（2026-10-29 误伤实测，中英文各一条）。
    _promise_window = _QUOTE_RE.sub("", stripped[-tail_chars:])
    # 「下一步需要你…」不是承诺，是要用户给东西 —— 明确排除。
    _needs_user = re.search(
        r"下一步(要|需要)(你|您)|next step (needs|requires) (your|the user)|"
        r"next step is your|下一步(是)?(你|您)的?(决|定|选择|call)|"
        r"next step is blocked (on|by)|下一步(卡|阻塞)在",
        _promise_window,
    )
    # ⚠ 顺序：hard_boundary 必须先短路，promise 才能判。
    # 理由见 exclusions.hard_boundary 的注释（2026-10-29 红队阻断项 S1）。
    if _compile(ex.get("hard_boundary")).search(tail):
        return False, "boundary"
    if not _needs_user and _compile(tr.get("promise")).search(_promise_window):
        if _int(attempt, 0) >= _int(cfg.get("max_nudges_per_turn", 2), 2):
            return False, "max_nudges"
        return True, "trigger:promise"
    if _compile(ex.get("boundary")).search(tail):
        return False, "boundary"
    if _compile(ex.get("user_must")).search(tail):
        return False, "user_must"
    # The closing window of the code-stripped text, so the "?" check only sees
    # punctuation that is actually prose.
    no_urls = _QUOTE_RE.sub("", stripped[-tail_chars:])
    name = _first_trigger_hit(tr, trig_tail)
    if name is not None:
        hit = f"trigger:{name}"
    elif tr.get("question_mark", True) and re.search(r"[？?]", no_urls):
        hit = "trigger:question_mark"
    else:
        # The turn ended cleanly — say so even when the cap is already reached.
        return False, "no_trigger"
    if _int(attempt, 0) >= _int(cfg.get("max_nudges_per_turn", 2), 2):
        return False, "max_nudges"
    return True, hit


def build_nudge(attempt: int, reason: str = "") -> str:
    if reason == "trigger:promise":
        return PROMISE_NUDGE_TEMPLATE.replace("{attempt}", str(attempt + 1))
    return NUDGE_TEMPLATE.replace("{attempt}", str(attempt + 1))


def _cli(argv: list[str]) -> int:
    """Tiny CLI so non-Python hosts (OpenCode, Pi, shell scripts) can reuse
    exactly the same detection rules instead of re-implementing them.

    Usage:
      detector.py --text "…reply…" [--config PATH] [--attempt N]

    Prints one JSON object: {"should_nudge": bool, "reason": "…"} and
    exits 0. Always exits 0 — a detection failure must not break the host.
    """
    import argparse

    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--text", default="")
    parser.add_argument("--config", default=None)
    parser.add_argument("--attempt", type=int, default=0)
    args = parser.parse_args(argv)

    cfg_path = Path(args.config) if args.config else Path.home() / ".hermes" / "continue_hook.json"
    try:
        cfg = ConfigStore(cfg_path).load()
        should, reason = detect(args.text, args.attempt, cfg)
    except Exception as exc:  # never raise into the host
        print(json.dumps({"should_nudge": False, "reason": f"error:{exc}"}))
        return 0
    print(json.dumps({"should_nudge": bool(should), "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    raise SystemExit(_cli(_sys.argv[1:]))
