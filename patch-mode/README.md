# Patch mode — same-turn seamless continuation

Plugin mode works one turn late (see the top-level README). Patch mode closes
the gap: the stop gate fires **inside** the agent loop, so the
permission-seeking question is never delivered to the user — you get only the
finished result.

> **Version-pinned.** Patch mode modifies the Hermes **core source tree**.
> The patch is written against one Hermes version and *refuses to install*
> when the target does not match: the installer runs `git apply --check`
> first and checks that the stop-gate chain still looks the way the patch
> expects. A refused install is the intended behaviour — use plugin mode
> instead.

## How it works

`hermes-agent` has a chain of stop gates in `agent/turn_stop_gates.py`
(`apply_stop_gates`): verify-on-stop → `pre_verify` plugin hook → kanban
terminal guard. Patch mode adds a gate — `continue-hook` — with the same
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

The gate calls one function:

```python
from agent.continue_hook import continue_hook_nudge

nudge = continue_hook_nudge(agent, final_response)   # str | None
```

`continue_hook_nudge` returns the nudge text when the reply ended by asking
permission, or `None` to let the turn end. Every failure inside it is silent:
a broken gate must not break the loop.

## The file it drops in is generated

`continue_hook.py` has to be self-contained — it lives inside the Hermes core
tree, where this repository is not. It is therefore **generated** from the
shared engine rather than maintained by hand:

```bash
python3 scripts/build-patch-mode.py            # regenerate
python3 scripts/build-patch-mode.py --check    # fail if it is out of date
```

The first version of that file was a hand-written second copy of the detector
— with its own patterns, its own key names and a hardcoded private log path.
It drifted from the shared engine within days. Generating it removes the
second implementation entirely, and `tests/test_patch_mode.py` fails the
build when the generated file is stale.

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

Reverts the patch and deletes `agent/continue_hook.py`.

## Which mode should I use?

| | Plugin | Patch |
|---|---|---|
| Install | `hermes plugins install` | touch your Hermes tree |
| Update path | plugin update | re-apply on Hermes upgrade (the guard tells you when the version moved) |
| User sees | the stray question, then the finished result | only the finished result |
| Maintenance | none | the patch is regenerated when the engine changes |

If you want zero core changes, use plugin mode. If the extra message bothers
you, use patch mode — and check that the version guard accepts your Hermes
build.

## Configuration and observability

The same shared config and the same event log as every other host:

* config — `~/.hermes/continue_hook.json` (see
  [docs/CONFIGURATION.md](../docs/CONFIGURATION.md))
* events — `~/.hermes/continue-hook-events.jsonl`
* kill switch — `HERMES_CONTINUE_HOOK=0`, or `"enabled": false` in the config

## Files

- `core-gate.patch` — the multi-file diff against the Hermes core
- `continue_hook.py` — **generated** module the core gate imports
- `install.sh` — version-guarded installer / remover
