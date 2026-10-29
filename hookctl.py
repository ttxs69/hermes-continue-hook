"""hookctl — the single decision service behind every continue-hook host.

Why one module instead of per-host logic
----------------------------------------
The rule "should this reply be nudged?" is *policy*, not transport. Six hosts
(Claude Code, Codex, Qwen Code, Cursor, GitHub Copilot CLI, Gemini CLI,
OpenCode, Pi, DeepSeek Harness, plus the Hermes plugin) all need the same
answer, and they used to answer it slightly differently — which is how a
project ends up with three caps and two cooldown rules.

So the policy lives here, once:

  * load the config (hot-reload, never raises),
  * apply the caps (per turn, per session, optional cooldown),
  * keep the per-session state,
  * append one event per evaluation — including the skips, so a denominator
    exists and the ask-rate means something,
  * return a verdict.

Hosts call it either directly (Python: the Hermes plugin and the shell-hook
adapters import :func:`decide`) or through the CLI (JavaScript and TypeScript
adapters shell out to ``python3 hookctl.py --text …``), so the rules cannot
drift apart.

Turn semantics
--------------
A *turn* is one user request and the agent work that answers it. The agent may
end a turn several times inside it when a host continues the turn after a
blocking hook, so the per-turn counter counts nudges along that chain:

  * a host that says "I already continued this turn" keeps the chain open;
  * a turn that ends **without** a nudge closes the chain and resets the
    counter, so the next turn starts fresh.

The old implementation never reset the counter, which made
``max_nudges_per_turn: 2`` mean "two nudges per session, ever". That is
fixed here and covered by tests.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # package load (installed as a Python distribution)
    from .detector import ConfigStore, detect  # type: ignore[attr-defined]
except ImportError:  # flat execution (repo layout, npm package, shell hooks)
    from detector import ConfigStore, detect  # type: ignore[no-redef]

__all__ = [
    "Decision",
    "decide",
    "load_config",
    "log_event",
    "read_state",
    "write_state",
]

STATE_VERSION = 2


def default_config_path() -> Path:
    """Where the shared config lives (resolved per call, so HOME can move)."""
    return Path.home() / ".hermes" / "continue_hook.json"


def default_state_path() -> Path:
    return Path.home() / ".hermes" / "continue-hook-state.json"


def default_events_path() -> Path:
    return Path.home() / ".hermes" / "continue-hook-events.jsonl"


@dataclass(frozen=True)
class Decision:
    """The verdict for one evaluation."""

    should_nudge: bool
    reason: str
    attempt: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "should_nudge": self.should_nudge,
            "reason": self.reason,
            "attempt": self.attempt,
        }


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load the shared config; a missing or broken file falls back to defaults."""
    cfg_path = Path(path) if path else default_config_path()
    try:
        return ConfigStore(cfg_path).load()
    except Exception:
        return {}


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "sessions": {}}


def read_state(path: Path | str | None = None) -> dict[str, Any]:
    """Read the state file, migrating the v1 flat layout to v2.

    v1: ``{"<session>": {"count": 1, "last_ts": 123}}``
    v2: ``{"version": 2, "sessions": {"<session>": {...}}}``
    """
    state_path = Path(path) if path else default_state_path()
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    if raw.get("version") == STATE_VERSION and isinstance(raw.get("sessions"), dict):
        return raw
    # v1 migration: every top-level key that maps to an object is a session.
    sessions: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, dict) and "count" in value:
            sessions[key] = {
                "count": int(value.get("count", 0)),
                "total": int(value.get("count", 0)),
                "last_ts": float(value.get("last_ts", 0.0)),
                "chain": False,
            }
    return {"version": STATE_VERSION, "sessions": sessions}


def write_state(state: dict[str, Any], path: Path | str | None = None) -> None:
    """Persist the state file. Never raises — losing state must not break a turn."""
    state_path = Path(path) if path else default_state_path()
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        try:
            state_path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        pass


class _StateLock:
    """An advisory lock around the state file.

    Several hosts can fire hooks at the same moment (a user with two agents
    open, or two hosts in parallel). Without a lock the read-modify-write
    loses updates: the counters for one session overwrite another's, and the
    nudge caps stop holding.

    The lock is a separate ``.lock`` file, so the state file itself is never
    truncated while another process holds it. If locking is unavailable
    (Windows, exotic filesystems), the code still works — it just loses the
    guarantee.
    """

    def __init__(self, state_path: Path):
        self.path = Path(str(state_path) + ".lock")
        self._handle: Any = None

    def __enter__(self) -> _StateLock:
        try:
            import fcntl

            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = open(self.path, "a+")
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        except Exception:
            self._handle = None
        return self

    def __exit__(self, *exc: object) -> None:
        if self._handle is None:
            return
        try:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._handle.close()
        except Exception:
            pass
        self._handle = None


def log_event(
    event: dict[str, Any],
    path: Path | str | None = None,
    *,
    snippet: bool = True,
) -> None:
    """Append one evaluation record. Never raises.

    The record can carry the tail of the reply, which is conversation content.
    ``snippet=False`` keeps the decision and drops the text; the file is written
    owner-only either way.
    """
    events_path = Path(path) if path else default_events_path()
    try:
        record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **event}
        if not snippet:
            record.pop("snippet", None)
        created_dir = not events_path.parent.exists()
        events_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if created_dir:
            try:
                events_path.parent.chmod(0o700)
            except OSError:
                pass
        with open(events_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            events_path.chmod(0o600)
        except OSError:
            pass  # exotic filesystem; the file still works
    except Exception:
        pass


def _num(value: Any, fallback: float, *, integer: bool) -> float:
    """Coerce a config value, tolerating what people actually write.

    ``cooldown_seconds: 5m`` or ``max_nudges_per_turn: two`` used to raise
    inside the hook, which breaks the turn instead of falling back — the exact
    opposite of the documented fail-safe behaviour.
    """
    if isinstance(value, bool):
        return float(fallback)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        multipliers = {"s": 1, "m": 60, "h": 3600}
        if text and text[-1] in multipliers and text[:-1].replace(".", "", 1).isdigit():
            return float(text[:-1]) * multipliers[text[-1]]
        try:
            return float(text)
        except ValueError:
            return float(fallback)
    return float(fallback)


STATE_TTL_SECONDS = 30 * 24 * 3600


def _prune_sessions(state: dict[str, Any], now: float | None = None) -> None:
    """Drop sessions we have not seen for a month.

    Without this the table grows forever, and a host that reuses a session id
    inherits whatever count the old session left behind.
    """
    sessions = state.get("sessions")
    if not isinstance(sessions, dict):
        return
    cutoff = (now if now is not None else time.time()) - STATE_TTL_SECONDS
    for name in [
        name
        for name, entry in sessions.items()
        if not isinstance(entry, dict)
        or _num(entry.get("last_ts", 0.0), 0.0, integer=False) < cutoff
    ]:
        sessions.pop(name, None)


def _session_entry(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    sessions = state.setdefault("sessions", {})
    entry = sessions.get(session_id)
    if not isinstance(entry, dict):
        entry = {"count": 0, "total": 0, "last_ts": 0.0, "chain": False}
        sessions[session_id] = entry
    return entry


def close_chain(
    *,
    session_id: str,
    host: str,
    platform: str = "",
    state_path: Path | str | None = None,
    events_path: Path | str | None = None,
) -> None:
    """End this turn's nudge chain without nudging.

    A host that reports ``stop_hook_active`` is telling us it is already
    continuing because of a block we issued. That continuation ends *this*
    turn's chain, and the next user turn must start from zero.

    Skipping this call entirely — what the adapters used to do — left the
    per-turn counter raised, so ``max_nudges_per_turn: 2`` silently became a
    quota shared across user turns: the second turn spent the first turn's
    allowance and the third got nothing.
    """
    resolved = Path(state_path) if state_path else default_state_path()
    events = Path(events_path) if events_path else default_events_path()
    with _StateLock(resolved):
        state = read_state(resolved)
        entry = _session_entry(state, session_id)
        entry["count"] = 0
        entry["chain"] = False
        state["version"] = STATE_VERSION
        write_state(state, resolved)
    log_event(
        {
            "session": session_id,
            "host": host,
            "platform": platform,
            "decision": "skip",
            "reason": "chain_closed",
            "attempt": 0,
            "snippet": "",
        },
        events,
    )


def decide(
    text: str,
    *,
    session_id: str,
    host: str,
    config_path: Path | str | None = None,
    state_path: Path | str | None = None,
    events_path: Path | str | None = None,
    continued: bool = False,
    platform: str = "",
) -> Decision:
    """Decide whether to nudge, and record why.

    ``continued`` tells the service the host already forced this turn to
    continue (Claude Code / Codex / Copilot / Gemini / Qwen report it as
    ``stop_hook_active``). Hosts that do not report it can leave it ``False``:
    the chain is tracked in the state file either way.
    """
    # The documented kill switch, in one place so that every caller (plugin,
    # shell adapters, patch mode) honours it. The plugin mode used to ignore it.
    if os.environ.get("HERMES_CONTINUE_HOOK") == "0":
        log_event(
            {
                "session": session_id,
                "host": host,
                "platform": platform,
                "decision": "skip",
                "reason": "disabled_env",
                "attempt": 0,
                "snippet": "",
            },
            events_path,
        )
        return Decision(should_nudge=False, reason="disabled_env", attempt=0)

    cfg = load_config(config_path)
    # The config may redirect the log (a private directory, a read-only home).
    if (
        events_path is None
        and isinstance(cfg.get("events_path"), str)
        and cfg["events_path"].strip()
    ):
        events_path = Path(str(cfg["events_path"])).expanduser()
    # The whole read-modify-write happens under the lock: two hooks firing at
    # once must not overwrite each other's counters.
    state_path = Path(state_path) if state_path else default_state_path()
    with _StateLock(state_path):
        return _decide_locked(
            text,
            session_id=session_id,
            host=host,
            cfg=cfg,
            state_path=state_path,
            events_path=events_path,
            continued=continued,
            platform=platform,
        )


# Reasons that mean "this turn really ended" — the next user turn starts from
# zero. Everything else (a cap, a cooldown, an empty reply) must keep the chain
# open: resetting there let 8 identical stop events produce 6 nudges with
# max_nudges_per_turn: 2.
CLOSING_REASONS = frozenset({"no_trigger", "boundary", "user_must"})


def _cap_reason(
    cfg: dict[str, Any],
    *,
    attempt: int,
    total: int,
    chain_open: bool,
    last_ts: float,
    now: float,
) -> str | None:
    """Which cap (if any) stops this nudge. Pure: no state, no clock, no I/O.

    Returns the reason code, or None when the nudge may go ahead.
    """
    if chain_open:
        max_per_turn = int(_num(cfg.get("max_nudges_per_turn", 2), 2, integer=True))
        if attempt >= max_per_turn:
            return "max_nudges"
    max_per_session = int(_num(cfg.get("max_nudges_per_session", 20), 20, integer=True))
    if total >= max_per_session:
        return "max_nudges_per_session"
    cooldown = _num(cfg.get("cooldown_seconds", 0) or 0, 0, integer=False)
    if cooldown > 0 and total > 0 and now - last_ts < cooldown:
        return "cooldown"
    return None


def _record_outcome(
    entry: dict[str, Any],
    *,
    state: dict[str, Any],
    state_path: Path,
    nudge: bool,
    reason: str,
    attempt: int,
    total: int,
    now: float,
) -> None:
    """Update the session's counters and persist the state file."""
    if nudge:
        entry["count"] = attempt + 1
        entry["total"] = total + 1
        entry["last_ts"] = now
        entry["chain"] = True
    elif reason in CLOSING_REASONS:
        # The turn ended without a nudge — close the chain.
        entry["count"] = 0
        entry["chain"] = False
    # else: leave the chain (and the per-turn counter) exactly as it was.
    state["version"] = STATE_VERSION
    write_state(state, state_path)


def _decide_locked(
    text: str,
    *,
    session_id: str,
    host: str,
    cfg: dict[str, Any],
    state_path: Path,
    events_path: Path | str | None,
    continued: bool,
    platform: str,
) -> Decision:
    """The decision itself. Callers hold the state lock."""
    state = read_state(state_path)
    now = time.time()
    _prune_sessions(state, now)
    entry = _session_entry(state, session_id)
    chain_open = bool(entry.get("chain")) or continued
    if not chain_open:
        # A fresh turn: the per-turn counter starts from zero.
        entry["count"] = 0

    attempt = int(_num(entry.get("count", 0), 0, integer=True))
    total = int(_num(entry.get("total", 0), 0, integer=True))
    snippet = (text or "")[-160:]

    def finish(nudge: bool, reason: str) -> Decision:
        _record_outcome(
            entry,
            state=state,
            state_path=state_path,
            nudge=nudge,
            reason=reason,
            attempt=attempt,
            total=total,
            now=now,
        )
        log_event(
            {
                "session": session_id,
                "host": host,
                "platform": platform,
                "decision": ("nudge" if nudge else "skip")
                + ("_dryrun" if cfg.get("dry_run") else ""),
                "reason": reason,
                "attempt": attempt,
                "snippet": snippet,
            },
            events_path,
            snippet=bool(cfg.get("log_snippet", True)),
        )
        return Decision(should_nudge=nudge, reason=reason, attempt=attempt)

    if not cfg.get("enabled", True):
        return finish(False, "disabled")
    if not text or not text.strip():
        return finish(False, "empty")

    try:
        should, reason = detect(text, attempt, cfg)
    except Exception as exc:  # detection must never break a turn
        return finish(False, f"error:{type(exc).__name__}")

    # Judge the text *before* the caps. A clean end of turn has to close the
    # chain even when the per-turn cap is already reached, or the chain stays
    # open forever and that session never nudges again — which is what checking
    # the cap first did to hermes / opencode / pi / dsh / cursor.
    if reason in CLOSING_REASONS:
        return finish(False, reason)

    capped = _cap_reason(
        cfg,
        attempt=attempt,
        total=total,
        chain_open=chain_open,
        last_ts=_num(entry.get("last_ts", 0.0), 0.0, integer=False),
        now=now,
    )
    if capped:
        return finish(False, capped)

    if cfg.get("dry_run"):
        return finish(False, "dry_run")
    return finish(bool(should), reason)


def _cli(argv: Iterable[str]) -> int:
    """CLI entry point for the non-Python hosts.

    Usage:
      hookctl.py --text "…reply…" --session S --host claude-code

    Prints one JSON object and always exits 0 — a decision failure must not
    break the host that asked.
    """
    parser = argparse.ArgumentParser(description="continue-hook decision service")
    parser.add_argument(
        "--text",
        default="",
        help="the reply text; use '-' to read it from stdin "
        "(keeps the conversation out of the process list)",
    )
    parser.add_argument("--session", default="default")
    parser.add_argument("--host", default="unknown")
    parser.add_argument("--platform", default="")
    parser.add_argument("--config", default=None)
    parser.add_argument("--state", default=None)
    parser.add_argument("--events", default=None)
    parser.add_argument(
        "--continued", action="store_true", help="the host already continued this turn"
    )
    parser.add_argument(
        "--nudge-text", action="store_true", help="also print the nudge text to inject"
    )
    args = parser.parse_args(list(argv))

    text = args.text
    if text == "-":
        # Read the reply from stdin: a command-line argument is visible to
        # every process on the machine (/proc/<pid>/cmdline), and the reply is
        # the user's own conversation.
        try:
            text = sys.stdin.read()
        except Exception:
            text = ""

    try:
        decision = decide(
            text,
            session_id=args.session,
            host=args.host,
            config_path=args.config,
            state_path=args.state,
            events_path=args.events,
            continued=args.continued,
            platform=args.platform,
        )
        payload = decision.to_json()
        if args.nudge_text and decision.should_nudge:
            try:
                from detector import build_nudge  # type: ignore[no-redef]

                payload["nudge"] = build_nudge(decision.attempt, decision.reason)
            except Exception:
                pass
        print(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # pragma: no cover - defensive
        print(json.dumps({"should_nudge": False, "reason": f"error:{type(exc).__name__}"}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli(sys.argv[1:]))
