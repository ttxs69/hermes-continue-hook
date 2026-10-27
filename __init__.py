"""continue-hook — a Hermes plugin that stops the agent from asking permission
for things it should just do.

Plugin-native architecture (zero core modification):

    transform_llm_output   (observer: returns None)  → stash last assistant
                                                        text per session
    on_session_end         (turn boundary)           → ask the shared decision
                                                        service whether to nudge
    ctx.inject_message()                             → nudge as a user-role
                                                        message into the SAME
                                                        session; the agent then
                                                        runs a follow-up turn
                                                        with full context and
                                                        finishes the work

The policy (caps, cooldown, event log) lives in :mod:`hookctl`, which every
other host also uses — the Hermes plugin and the shell-hook adapters must not
answer "should this be nudged?" differently.

Loop safety: per-turn nudge cap + per-session cap; boundary / user-must
exclusions short-circuit before any trigger; ``dry_run`` logs without
injecting. Kill switch: config ``enabled: false`` or env
``HERMES_CONTINUE_HOOK=0``.

Observability contract: every evaluation (including skips, so a denominator
exists) is appended to ``~/.hermes/continue-hook-events.jsonl``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

try:  # package load (installed as a Python distribution)
    from .detector import ConfigStore, detect  # type: ignore[attr-defined]
    from .hookctl import decide  # type: ignore[attr-defined]
except ImportError:  # flat execution (repo layout, tests)
    from detector import ConfigStore, detect  # type: ignore[no-redef]
    from hookctl import decide  # type: ignore[no-redef]

__version__ = "1.3.0"

def cfg_path() -> Path:
    """Shared config path (resolved per call, so HOME can move)."""
    return Path.home() / ".hermes" / "continue_hook.json"


def events_path() -> Path:
    return Path.home() / ".hermes" / "continue-hook-events.jsonl"

_last_text: dict[str, str] = {}   # session_id → last assistant text


def _log(event: dict[str, Any]) -> None:
    """Best-effort append to the events log. Never raises."""
    try:
        record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **event}
        with open(events_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def register(ctx: Any) -> None:
    """Hermes plugin entry point."""
    cfg_store = ConfigStore(cfg_path())

    # --- stash the last LLM output per session (observer; never transforms) ---
    def on_llm_output(text: Any, session_id: Any = None, **kw: Any) -> None:
        try:
            if isinstance(text, str) and text:
                key = str(kw.get("session_id") or session_id or "?")
                _last_text[key] = text
        except Exception:
            pass
        return None

    ctx.register_hook("transform_llm_output", on_llm_output)

    # --- at turn end: ask the shared service, maybe inject a nudge ---
    def on_turn_end(session_id: Any = None, platform: Any = None,
                    interrupted: Any = None, failed: Any = None,
                    **kw: Any) -> None:
        sid = str(session_id or kw.get("session_key") or "?")
        try:
            if interrupted or failed:
                return
            if not cfg_store.load().get("enabled", True):
                return
            text = _last_text.pop(sid, None)
            if not text:
                return
            decision = decide(text, session_id=sid, host="hermes",
                              platform=str(platform or ""))
            if not decision.should_nudge:
                return
            try:
                from detector import build_nudge  # type: ignore[no-redef]
                nudge = build_nudge(decision.attempt)
            except Exception:
                nudge = (
                    "【continue-hook】Your last reply ended by asking the user a "
                    "question. Re-check once: if it touches a user-only boundary "
                    "(money, identity, publishing, deleting data, shared systems) "
                    "or needs information only the user has, re-ask in ONE short "
                    "line and stop. Otherwise do the work now and report result, "
                    "verification and remaining risks."
                )
            session_key = kw.get("session_key")
            if session_key:
                ctx.inject_message(nudge, role="user", session_key=session_key)
            else:
                ctx.inject_message(nudge, role="user", origin=True)
        except Exception as exc:
            _log({"session": sid, "decision": "error",
                  "reason": f"{type(exc).__name__}: {exc}"})

    ctx.register_hook("on_session_end", on_turn_end)

    # --- /ch-stats : observability panel ---
    def cmd_stats(*args: Any, **kw: Any) -> str:
        from collections import Counter
        rows: list[dict[str, Any]] = []
        if events_path().exists():
            with open(events_path(), encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
        decisions = Counter(row.get("decision") for row in rows)
        reasons = Counter(row.get("reason") for row in rows
                          if str(row.get("decision", "")).startswith("nudge"))
        nudges = sum(v for k, v in decisions.items() if str(k).startswith("nudge"))
        evaluations = sum(v for k, v in decisions.items()
                          if str(k).startswith(("nudge", "skip")))
        rate = f"{nudges / evaluations:.0%}" if evaluations else "n/a"
        cfg = cfg_store.load()
        return (f"continue-hook: {len(rows)} evaluations | nudges {nudges} "
                f"({rate} of evaluations) | by-reason {dict(reasons)} | "
                f"dry_run={cfg.get('dry_run')} | config={cfg_path()}")

    ctx.register_command("ch-stats", cmd_stats,
                         description="continue-hook: detection stats & current config")

    # --- /ch-test : dry-run the detector on arbitrary text ---
    def cmd_test(text: Any = "", **kw: Any) -> str:
        should, reason = detect(str(text), 0, cfg_store.load())
        return f"detect={should} reason={reason}"

    ctx.register_command("ch-test", cmd_test,
                         description="continue-hook: test detection — /ch-test 要不要我继续？")
