# Patch mode — same-turn seamless continuation

Plugin mode works one turn late (see README). Patch mode closes the gap: the
stop gate fires **inside** the agent loop, so the permission-seeking question is
never delivered to the user — you get only the finished result.

## How it works

`hermes-agent` already has a chain of stop gates in `agent/turn_stop_gates.py`
(`apply_stop_gates`): verify-on-stop → `pre_verify` plugin hook → kanban
terminal guard. Patch mode adds a fourth gate — `continue-hook` — with the same
contract the kanban guard uses: append a synthetic user-role row, clear
`final_response`, re-enter the loop.

```diff
+ _ch_nudge = continue_hook_nudge(agent, final_response)
+ if _ch_nudge:
+     final_msg["finish_reason"] = "continue_hook_nudge"
+     verdict = _continue(_ch_nudge, "_continue_hook_synthetic")
+     return verdict
```

Two more one-line changes register the synthetic flag so the finalizer and the
session store treat the nudge row as synthetic (same as the existing
verification nudges).

## Install

```bash
./install.sh /path/to/hermes-agent          # apply
# enable:
hermes config set agent.continue_hook_enabled true
# restart the Hermes gateway process
```

The installer is version-guarded: it refuses to patch a tree whose
`turn_stop_gates.py` doesn't contain the kanban guard (i.e. a different Hermes
version), and `git apply --check` refuses a tree that is already patched.

## Remove

```bash
./install.sh --remove /path/to/hermes-agent
```

Reverts the patch and deletes `agent/continue_hook.py`. Verified: install →
import → duplicate-install rejected → remove → import, all green.

## Which mode should I use?

| | Plugin | Patch |
|---|---|---|
| Install | `hermes plugins install` | touch your Hermes tree |
| Update path | plugin update | re-apply on Hermes upgrade (guard will tell you if the version moved) |
| User sees | the stray question, then the finished result | only the finished result |
| Upstream | works today | works today, and is the same change we want upstream |

If you want zero core changes, use plugin mode. If the extra message bothers
you, use patch mode. When the upstream PR lands, the plugin switches to the
seamless behavior automatically.

## Files

- `core-gate.patch` — the 3-file diff against Hermes core
- `continue_hook.py` — the detector module the core gate imports
- `install.sh` — version-guarded installer / remover
