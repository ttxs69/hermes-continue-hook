#!/usr/bin/env python3
"""continue-hook for Cursor — a `stop` hook.

Cursor hooks contract (verified against cursor.com/docs/hooks):
  config : ~/.cursor/hooks.json  {"version": 1, "hooks": {...}}
  input  : stop-specific  {"status": "completed"|"aborted"|"error", "loop_count": 0}
           plus the common schema every Cursor hook receives:
           {conversation_id, generation_id, model, model_id, model_params,
            hook_event_name, cursor_version, workspace_roots, user_email,
            transcript_path}
  output : {"followup_message": "…"} — Cursor submits it as the next user
           message, which keeps the agent iterating.
  guard  : `loop_count` = how many auto follow-ups this conversation already
           made. Cursor's own default cap is 5 per script; we stop at 2.

The stop payload carries no reply text, so the last assistant message is read
from the main conversation transcript (`transcript_path`).

"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# The shared modules live beside this script in the installed layout
# (~/.hermes/continue-hook/<host>/) and up the tree in the repo layout.
for _candidate in (_HERE, _HERE.parent, _HERE.parent.parent, _HERE.parent.parent.parent):
    if (_candidate / "hookctl.py").exists():
        sys.path.insert(0, str(_candidate))
        break
sys.path.insert(0, str(_HERE.parent))  # _common.py lives with the adapters
try:
    import _common
except ImportError:  # pragma: no cover - packaging guard
    sys.stderr.write("continue-hook: _common.py not found next to this script\n")
    raise SystemExit(0) from None



def main() -> None:
    if _common.disabled():
        raise SystemExit(0)
    payload = _common.read_payload()

    if str(payload.get("status", "completed")) != "completed":
        raise SystemExit(0)
    if _common.host_loop_exceeded(payload, 2):
        _common.log_event("cursor", {"session": _common.session_key(payload, "cursor"),
                                     "decision": "skip", "reason": "host_loop_count",
                                     "attempt": 0, "snippet": ""})
        raise SystemExit(0)

    cfg = _common.load_config()
    if not cfg.get("enabled", True):
        raise SystemExit(0)

    text = str(payload.get("last_assistant_message") or "")
    if not text.strip():
        text = _common.read_first_transcript(payload, "transcript_path")
    if not text.strip():
        raise SystemExit(0)

    sid = _common.session_key(payload, "cursor")
    decision = _common.decide(text, session_id=sid, host="cursor", payload=payload)
    if not decision.should_nudge:
        raise SystemExit(0)

    from detector import build_nudge
    _common.print_json({"followup_message": build_nudge(decision.attempt)})

if __name__ == "__main__":
    main()
