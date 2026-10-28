"""Shared plumbing for the continue-hook host adapters.

Every host adapter (Claude Code, Codex, Qwen Code, Cursor, GitHub Copilot CLI,
Gemini CLI) does the same four things and differs in only two places: how the
host's payload is named, and how the host is told to continue.

  1. read the host's payload,
  2. recover the reply text (a payload field, or the transcript on disk),
  3. ask the shared decision service (:mod:`hookctl`) whether to nudge,
  4. print the host's continuation signal.

Step 3 is *policy* and lives in ``hookctl.py`` — one implementation for every
host, so a cap can never mean two different things in two hosts.

Everything here is transport: it never judges whether a question was
legitimate. That judgement belongs to the agent, inside the nudge.

Design rules:
  * Fail open. Any error means "no nudge" — a broken hook must never wedge
    the user's agent session.
  * Never nudge when the host says it already continued this turn.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
# The shared modules live beside this file in the installed layout
# (~/.hermes/continue-hook/) and one level up in the repo layout.
for _candidate in (_HERE, _HERE.parent, _HERE.parent.parent):
    if (_candidate / "hookctl.py").exists():
        sys.path.insert(0, str(_candidate))
        break

try:
    import hookctl
except ImportError:  # pragma: no cover - packaging guard
    sys.stderr.write("continue-hook: hookctl.py not found next to this script\n")
    raise SystemExit(0) from None

__all__ = [
    "decide",
    "disabled",
    "hookctl",
    "host_continued",
    "host_loop_count",
    "host_loop_exceeded",
    "load_config",
    "log_event",
    "print_json",
    "read_first_transcript",
    "read_payload",
    "read_transcript",
    "session_key",
]

MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024


def disabled() -> bool:
    """Kill switch: ``HERMES_CONTINUE_HOOK=0`` disables every adapter."""
    return os.environ.get("HERMES_CONTINUE_HOOK") == "0"


def load_config() -> dict[str, Any]:
    """The shared config (hot-reloadable, never raises)."""
    return hookctl.load_config()


def log_event(host: str, event: dict[str, Any]) -> None:
    """Append one record to the shared event log. Never raises."""
    hookctl.log_event({"host": host, **event})


def _text_from_content(content: Any) -> str:
    """Pull plain text out of the many shapes a message body can take."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in ("text", "output_text", None):
                value = item.get("text")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        value = content.get("text")
        return value if isinstance(value, str) else ""
    return ""


def _record_to_text(record: Any) -> str:
    """Best-effort extraction of assistant text from one transcript record."""
    if not isinstance(record, dict):
        return ""
    if record.get("type") not in ("assistant", "message", "agent", "response", None):
        return ""
    role = record.get("role") or record.get("type")
    if role not in ("assistant", "agent", None):
        return ""
    body = record.get("message", record)
    text = _text_from_content(body.get("content")) if isinstance(body, dict) else _text_from_content(body)
    if not text:
        text = _text_from_content(record.get("content"))
    return text


def read_transcript(path: str) -> str:
    """Return the newest assistant text from a transcript file.

    Tolerates the three shapes seen in the wild:
      * JSONL, Claude-style  ``{"type":"assistant","message":{"content":[…]}}``
      * JSONL, OpenAI-style  ``{"role":"assistant","content":"…"}``
      * a single JSON array of message objects

    Unknown shapes yield "" — the caller then skips the turn (fail open).
    """
    if not path:
        return ""
    try:
        file_path = Path(path).expanduser()
        if not file_path.exists() or not file_path.is_file():
            return ""
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        if len(raw) > MAX_TRANSCRIPT_BYTES:
            raw = raw[-MAX_TRANSCRIPT_BYTES:]
        stripped = raw.strip()
        if not stripped:
            return ""

        records: list[Any] = []
        if stripped[0] == "[":
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    records = parsed
            except Exception:
                records = []
        if not records:
            for line in stripped.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if isinstance(record, dict):
                    records.append(record)

        last = ""
        for record in records:
            text = _record_to_text(record)
            if text.strip():
                last = text
        return last
    except Exception:
        return ""


def read_first_transcript(payload: dict[str, Any], *fields: str) -> str:
    """Read the transcript from the first candidate field that yields text.

    Hosts disagree on the spelling (``transcriptPath`` vs ``transcript_path``)
    and sometimes send both; trying every candidate avoids a silent miss.
    """
    for field in fields:
        value = payload.get(field)
        if not value:
            continue
        text = read_transcript(str(value))
        if text.strip():
            return text
    return ""


def session_key(payload: dict[str, Any], fallback: str) -> str:
    """Pull a stable per-conversation key out of a host payload."""
    for field in ("session_id", "sessionId", "conversation_id", "conversationId",
                  "thread_id", "threadId"):
        value = payload.get(field)
        if value:
            return str(value)[:64]
    return fallback


def host_continued(payload: dict[str, Any]) -> bool:
    """True when the host already forced this turn to continue.

    Hosts spell it differently; check every known spelling.
    """
    # NOT "continue": Gemini's generic output field means the opposite
    # ({"continue": false} stops the loop). Reading it as "already continuing"
    # would make the first nudge of a session never fire.
    for field in ("stop_hook_active", "stopHookActive", "loop_active"):
        if payload.get(field):
            return True
    return False


def host_loop_count(payload: dict[str, Any]) -> int:
    """How many auto follow-ups the host already made for this conversation."""
    for field in ("loop_count", "loopCount"):
        value = payload.get(field)
        if isinstance(value, int):
            return value
    return 0


def host_loop_exceeded(payload: dict[str, Any], max_host_loops: int) -> bool:
    """True when the host's own auto-follow-up cap is reached."""
    if max_host_loops <= 0:
        return False
    return host_loop_count(payload) >= max_host_loops


def decide(text: str, *, session_id: str, host: str,
           payload: dict[str, Any] | None = None,
           platform: str = "") -> hookctl.Decision:
    """Ask the shared decision service whether this reply should be nudged."""
    return hookctl.decide(
        text,
        session_id=session_id,
        host=host,
        continued=host_continued(payload or {}),
        platform=platform,
    )


def print_json(payload: dict[str, Any]) -> None:
    """Print one JSON object on stdout — the shape every host expects."""
    print(json.dumps(payload, ensure_ascii=False))


def read_payload() -> dict[str, Any]:
    """Read the host's JSON payload from stdin (empty dict when not available)."""
    try:
        if sys.stdin.isatty():
            return {}
        return json.load(sys.stdin)
    except Exception:
        return {}


def close_chain(*, session_id: str, host: str, platform: str = "") -> None:
    """Tell the decision service that this turn's nudge chain is over."""
    try:
        hookctl.close_chain(session_id=session_id, host=host, platform=platform)
    except Exception as exc:  # never break the host's turn
        sys.stderr.write(f"continue-hook: close_chain failed: {type(exc).__name__}\n")
