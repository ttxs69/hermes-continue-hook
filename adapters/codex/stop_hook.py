#!/usr/bin/env python3
"""continue-hook for Codex CLI — a Stop hook.

Codex hooks contract (verified against the Codex CLI hooks docs):
  config : ~/.codex/hooks.json  (or [hooks] in ~/.codex/config.toml)
  stdin  : {"session_id", "turn_id", "stop_hook_active",
            "last_assistant_message"}
  stdout : {"decision": "block", "reason": "…"} keeps Codex going; the reason
           becomes the continuation prompt. Empty output = stop.
  guard  : `stop_hook_active` prevents the loop.

Codex hands us the final reply text directly (`last_assistant_message`).

Note: Codex reviews non-managed hooks — run /hooks once in the CLI to trust
the newly installed hook.

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
        _common.close_chain(session_id=_common.session_key(payload, "codex"), host="codex")
        raise SystemExit(0)

    cfg = _common.load_config()
    if not cfg.get("enabled", True):
        raise SystemExit(0)

    text = str(payload.get("last_assistant_message") or "")
    if not text.strip():
        text = _common.read_first_transcript(payload, "transcript_path", "transcriptPath")
    if not text.strip():
        raise SystemExit(0)

    sid = _common.session_key(payload, "codex")
    decision = _common.decide(text, session_id=sid, host="codex", payload=payload)
    if not decision.should_nudge:
        raise SystemExit(0)

    from detector import build_nudge
    _common.print_json({"decision": "block", "reason": build_nudge(decision.attempt)})

if __name__ == "__main__":
    main()
