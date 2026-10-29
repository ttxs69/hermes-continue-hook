#!/usr/bin/env python3
"""continue-hook for GitHub Copilot CLI — an `agentStop` hook.

Copilot CLI hooks contract (verified against docs.github.com/copilot hooks):
  config : ~/.copilot/hooks/*.json  {"version": 1, "hooks": {...}}
  input  : camelCase  {sessionId, timestamp, cwd, transcriptPath, stopReason}
           VS Code form uses snake_case (hook_event_name, session_id,
           transcript_path, stop_reason, stop_hook_active) — both accepted.
  output : {"decision": "block", "reason": "…"} forces another agent turn
           with `reason` as the prompt.
  guard  : `stop_hook_active` is true when a previous block already continued
           this turn; the host also caps runaway blocks.

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

    if _common.host_continued(payload):
        # The host is already continuing because of our block: this turn's
        # chain is over. Closing it resets the per-turn counter — skipping
        # this is what made the counter a quota shared across user turns.
        _common.close_chain(session_id=_common.session_key(payload, "copilot"), host="copilot")
        raise SystemExit(0)

    cfg = _common.load_config()
    if not cfg.get("enabled", True):
        raise SystemExit(0)

    text = str(payload.get("last_assistant_message") or "")
    if not text.strip():
        text = _common.read_first_transcript(payload, "transcriptPath", "transcript_path")
    if not text.strip():
        raise SystemExit(0)

    sid = _common.session_key(payload, "copilot")
    decision = _common.decide(text, session_id=sid, host="copilot", payload=payload)
    if not decision.should_nudge:
        raise SystemExit(0)

    from detector import build_nudge

    _common.print_json(
        {"decision": "block", "reason": build_nudge(decision.attempt, decision.reason)}
    )


if __name__ == "__main__":
    main()
