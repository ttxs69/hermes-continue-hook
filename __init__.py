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

try:  # package load (Hermes loads a plugin dir as ``hermes_plugins.<slug>``)
    from .detector import ConfigStore, build_nudge, detect  # type: ignore[attr-defined]
    from .hookctl import decide  # type: ignore[attr-defined]
except ImportError:  # flat execution (repo layout, tests, npm package)
    from detector import ConfigStore, build_nudge, detect  # type: ignore[no-redef]
    from hookctl import decide  # type: ignore[no-redef]

__version__ = "1.3.0"


def cfg_path() -> Path:
    """Shared config path (resolved per call, so HOME can move)."""
    return Path.home() / ".hermes" / "continue_hook.json"


def events_path() -> Path:
    return Path.home() / ".hermes" / "continue-hook-events.jsonl"


_last_text: dict[str, str] = {}  # session_id → last assistant text


def _log(event: dict[str, Any]) -> None:
    """Best-effort append to the events log. Never raises."""
    try:
        record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **event}
        target = events_path()
        # The log has to exist even when we bail out before the decision
        # service runs (e.g. a gateway we cannot inject into), otherwise the
        # reason we gave is invisible.
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def register(ctx: Any) -> None:
    """Hermes plugin entry point."""
    cfg_store = ConfigStore(cfg_path())

    # --- stash the last LLM output per session (observer; never transforms) ---
    # Hermes calls this hook with keyword arguments only
    # (``response_text=…, session_id=…, model=…, platform=…, turn_id=…``), and
    # a callback that takes a required positional argument would raise
    # TypeError there — the hook would never store anything and the plugin
    # would silently never fire. ``text`` is accepted as a legacy alias.
    def on_llm_output(
        response_text: Any = None, session_id: Any = None, text: Any = None, **kw: Any
    ) -> None:
        try:
            payload = response_text if isinstance(response_text, str) else text
            if isinstance(payload, str) and payload:
                key = str(kw.get("session_id") or session_id or "?")
                _last_text[key] = payload
        except Exception:
            pass
        return None

    ctx.register_hook("transform_llm_output", on_llm_output)

    # --- at turn end: ask the shared service, maybe inject a nudge ---
    def on_turn_end(
        session_id: Any = None,
        platform: Any = None,
        interrupted: Any = None,
        failed: Any = None,
        **kw: Any,
    ) -> None:
        sid = str(session_id or kw.get("session_key") or "?")
        try:
            if interrupted or failed:
                return
            if not cfg_store.load().get("enabled", True):
                return
            text = _last_text.pop(sid, None)
            if not text:
                return
            # Decide whether we could even deliver *before* counting a nudge.
            # On a messaging gateway the payload carries no session_key/origin,
            # so nothing can be injected — and a nudge recorded as delivered
            # would inflate the ask-rate and eat the session's allowance.
            key = str(kw.get("session_key") or "")
            origin = kw.get("origin")
            platform_name = str(platform or "").lower()
            is_local = platform_name in ("", "cli", "tui", "desktop", "local")
            if not (key and ":" in key) and origin is None and not is_local:
                _log(
                    {
                        "session": sid,
                        "decision": "inject_unsupported_gateway",
                        "reason": f"platform {platform_name!r} gives on_session_end no "
                        f"session_key/origin; use patch mode for same-turn "
                        f"continuation on messaging gateways",
                    }
                )
                return
            decision = decide(text, session_id=sid, host="hermes", platform=str(platform or ""))
            if not decision.should_nudge:
                return
            nudge = build_nudge(decision.attempt)
            # Injection contract (hermes_cli/plugins_injection.py):
            #  * CLI / TUI / desktop — no target; the message goes to the local
            #    input queue and the agent continues.
            #  * Messaging gateway   — needs a durable composite ``session_key``
            #    (e.g. "agent:main:discord:thread:42") or an ``origin`` mapping.
            #
            # The ``on_session_end`` payload carries session_id, task_id,
            # turn_id, completed, failed, interrupted, turn_exit_reason, model
            # and platform — neither a session_key nor an origin. So on a
            # gateway there is nothing valid to pass, and a fabricated key would
            # target a session that does not exist. Say so instead of writing a
            # cheerful "nudged" record that never reached anyone.
            if key and ":" in key:
                injected = bool(ctx.inject_message(nudge, role="user", session_key=key))
            elif origin is not None:
                injected = bool(ctx.inject_message(nudge, role="user", origin=origin))
            else:
                injected = bool(ctx.inject_message(nudge, role="user"))
            if not injected:
                _log(
                    {
                        "session": sid,
                        "decision": "inject_failed",
                        "reason": "the host did not accept the nudge",
                    }
                )
        except Exception as exc:
            _log({"session": sid, "decision": "error", "reason": f"{type(exc).__name__}: {exc}"})

    ctx.register_hook("on_session_end", on_turn_end)

    # --- /ch-stats : observability panel ---
    def cmd_stats(*args: Any, **kw: Any) -> str:
        from collections import Counter

        rows: list[dict[str, Any]] = []
        if events_path().exists():
            with open(events_path(), encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
        decisions = Counter(row.get("decision") for row in rows)
        reasons = Counter(
            row.get("reason") for row in rows if str(row.get("decision", "")).startswith("nudge")
        )
        nudges = sum(v for k, v in decisions.items() if str(k).startswith("nudge"))
        evaluations = sum(v for k, v in decisions.items() if str(k).startswith(("nudge", "skip")))
        rate = f"{nudges / evaluations:.0%}" if evaluations else "n/a"
        cfg = cfg_store.load()
        return (
            f"continue-hook: {len(rows)} evaluations | nudges {nudges} "
            f"({rate} of evaluations) | by-reason {dict(reasons)} | "
            f"dry_run={cfg.get('dry_run')} | config={cfg_path()}"
        )

    ctx.register_command(
        "ch-stats", cmd_stats, description="continue-hook: detection stats & current config"
    )

    # --- /ch-test : dry-run the detector on arbitrary text ---
    def cmd_test(text: Any = "", **kw: Any) -> str:
        should, reason = detect(str(text), 0, cfg_store.load())
        return f"detect={should} reason={reason}"

    ctx.register_command(
        "ch-test", cmd_test, description="continue-hook: test detection — /ch-test 要不要我继续？"
    )
