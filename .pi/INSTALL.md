# continue-hook for Pi

The canonical extension file is **`adapters/pi/continue-hook.ts`** — there is
exactly one copy of it in this repository, and the installer copies it where
Pi looks for extensions.

```bash
npx @ttxs69/hermes-continue-hook install --host pi
# → ~/.pi/agent/extensions/continue-hook.ts
```

The extension hooks `agent_before_settle`, the final actionable boundary, and
returns `continue: true` with one custom message when the reply ended by
asking permission for something the agent could have done itself.

Requirements: `python3` on PATH, and `hookctl.py` reachable from the
extension file (this repository, or `~/.hermes/continue-hook/hookctl.py`).
