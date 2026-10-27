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

__all__ = ["DEFAULTS", "ConfigStore", "build_nudge", "default_config", "detect"]

# Quoted spans are *mentions* of a phrase, not *uses* of it. Trigger matching
# runs on the quote-stripped tail (found via live dogfooding: a report that
# quoted the trigger patterns fired the trigger). Exclusion matching still
# scans the RAW tail — the safe direction (skip more, nudge less).
_QUOTE_RE = re.compile(
    r"「[^」]{0,80}」|『[^』]{0,80}』|\u201c[^\u201d]{0,80}\u201d|'[^']{0,60}'"
)

# A question mark inside a URL is not a question. Without this, a reply that
# ends with a link ("docs at https://example.com/x?y=1") looked like a
# permission-seeking ending.
_URL_RE = re.compile(r"https?://[^\s<>）)\]\"']+")
_MARKDOWN_LINK_RE = re.compile(r"\]\([^)]*\)")

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
    "dry_run": False,           # log-only rollout: record what would fire, never inject
    # Caps. The per-turn counter resets when a turn ends without a nudge, so
    # "per turn" means per turn — not per session (the old implementation never
    # reset it, so two nudges silenced the hook for the rest of the session).
    "max_nudges_per_turn": 2,
    "max_nudges_per_session": 20,   # backstop for hosts without a host-side cap
    # Optional session-level rate limit between two nudges. Disabled by default:
    # the caps above already bound the loop, and a non-zero value silently
    # stops the hook from working for minutes after the first nudge.
    "cooldown_seconds": 0,
    "tail_chars": 240,
    # Triggers: any hit makes the reply a nudge candidate.
    "triggers": {
        # First-person offers — pushing a self-advancable decision back to the user.
        "offer": [
            "要不要我", "要不要顺便", "是否需要我", "要不要继续",
            "需要我.{0,8}(做|写|建|发|补|加|出|跑|查|弄|搞)",
            "我可以(帮)?(你|您)?.{0,8}(做|写|建|发|补|加|出|跑|查|弄|搞)",
            "我来做还是", "等我(先)?(做|写)完(再)?(给你|你)",
            r"should i\b", r"do you want me to\b", r"shall i\b", r"want me to\b",
        ],
        # Hand-offs — deferring the decision without a question mark.
        "hand_off": [
            "(交给|留给)你(来)?(定|决定|判断|选)",
            "你(来)?(定|决定|选)一下", "听你的", "等你(拍板|指示|点头|回复|确认)",
            r"(up to you|your call|let me know if)",
        ],
        # Confirmation-seeking endings.
        "confirm_seek": [
            "这样(可以|行)吗", "没问题吧", "确认一下[？?]?$", "如果.{1,12}，要不要",
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
            "花钱|付费|充值|批准|审批|实名|密码|验证码|付款|掏钱|报销",
            "对外发布|发布出去|删掉|删除数据|授权",
            r"(cost|charge|pay(ing)?|purchase|renew)\s*\$?\d|(do you )?approve|approval|"
            r"\bpassword\b|2fa|otp code|delete (the|all) (data|files)|publish (it|this)",
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
    pats = [p for p in (patterns or []) if p]
    if not pats:
        return re.compile(r"(?!x)x")  # never-matching pattern
    return re.compile("|".join(f"(?:{p})" for p in pats), re.S)


def detect(text: str, attempt: int, cfg: dict) -> tuple[bool, str]:
    """Return ``(should_nudge, reason_code)``. Pure transport layer."""
    if attempt >= int(cfg.get("max_nudges_per_turn", 2)):
        return False, "max_nudges"
    if not text or not str(text).strip():
        return False, "empty"
    tail = str(text).strip()[-int(cfg.get("tail_chars", 240)):]
    ex = cfg.get("exclusions", {})
    if _compile(ex.get("boundary")).search(tail):
        return False, "boundary"
    if _compile(ex.get("user_must")).search(tail):
        return False, "user_must"
    tr = cfg.get("triggers", {})
    trig_tail = _QUOTE_RE.sub("", tail)  # quoted mentions are not asks
    # URLs and markdown link targets are stripped before the bare-question-mark
    # trigger: a "?" inside a link is not a question.
    no_urls = _MARKDOWN_LINK_RE.sub(" ", trig_tail)
    no_urls = _URL_RE.sub(" ", no_urls)
    for name in ("offer", "hand_off", "confirm_seek"):
        if _compile(tr.get(name)).search(trig_tail):
            return True, f"trigger:{name}"
    if tr.get("question_mark", True) and re.search(r"[？?]", no_urls):
        return True, "trigger:question_mark"
    return False, "no_trigger"


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
