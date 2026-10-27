#!/usr/bin/env python3
"""continue-hook for Qwen Code — a `Stop` hook.

Qwen Code's hook events are Claude-Code-compatible, and the docs give the
`Stop` contract explicitly:
  * event        : Stop — "Executed before Qwen concludes its response"
  * input        : {stop_hook_active, last_assistant_message, …}
  * output       : {"decision": "block", "reason": "…"} blocks the stop and
                   continues the turn with `reason` as the instruction
  * blocking cap : consecutive blocks are counted per prompt; the turn ends
                   after stopHookBlockingCap blocks (default 8, overridable
                   with QWEN_CODE_STOP_HOOK_BLOCK_CAP)
  * config       : ~/.qwen/settings.json  (or .qwen/settings.json in a project)

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
        _common.log_event("qwen", {"session": _common.session_key(payload, "qwen"),
                                   "decision": "skip", "reason": "stop_hook_active",
                                   "attempt": 0, "snippet": ""})
        raise SystemExit(0)

    cfg = _common.load_config()
    if not cfg.get("enabled", True):
        raise SystemExit(0)

    text = str(payload.get("last_assistant_message") or "")
    if not text.strip():
        text = _common.read_first_transcript(payload, "transcript_path", "transcriptPath")
    if not text.strip():
        raise SystemExit(0)

    sid = _common.session_key(payload, "qwen")
    decision = _common.decide(text, session_id=sid, host="qwen", payload=payload)
    if not decision.should_nudge:
        raise SystemExit(0)

    from detector import build_nudge
    _common.print_json({"decision": "block", "reason": build_nudge(decision.attempt)})

if __name__ == "__main__":
    main()
