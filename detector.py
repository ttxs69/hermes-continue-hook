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
    # Strip code and links from the WHOLE reply, then take the closing window.
    # Doing it the other way round meant a fenced block whose closing ``` fell
    # outside the last 240 characters left its "?" in place — and "the reply
    # ends with a code block" is one of the most common shapes.
    stripped = _CODE_BLOCK_RE.sub(" ", full)
    stripped = _INLINE_CODE_RE.sub(" ", stripped)
    stripped = _MARKDOWN_LINK_RE.sub(" ", stripped)
    stripped = _URL_RE.sub(" ", stripped)
    tail_chars = _int(cfg.get("tail_chars", 240), 240)
    tail = full[-tail_chars:]
    ex = cfg.get("exclusions", {})
    if _compile(ex.get("boundary")).search(tail):
        return False, "boundary"
    if _compile(ex.get("user_must")).search(tail):
        return False, "user_must"
    tr = cfg.get("triggers", {})
    trig_tail = _QUOTE_RE.sub("", tail)  # quoted mentions are not asks
    # The closing window of the code-stripped text, so the "?" check only sees
    # punctuation that is actually prose.
    no_urls = _QUOTE_RE.sub("", stripped[-tail_chars:])
    hit = None
    for name in ("offer", "hand_off", "confirm_seek"):
        if _compile(tr.get(name)).search(trig_tail):
            hit = f"trigger:{name}"
            break
    if hit is None and tr.get("question_mark", True) and re.search(r"[？?]", no_urls):
        hit = "trigger:question_mark"
    if hit is None:
        # The turn ended cleanly — say so even when the cap is already reached.
        return False, "no_trigger"
    if _int(attempt, 0) >= _int(cfg.get("max_nudges_per_turn", 2), 2):
        return False, "max_nudges"
    return True, hit


def build_nudge(attempt: int) -> str:
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
