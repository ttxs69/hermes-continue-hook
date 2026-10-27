# Architecture

One detection engine, one policy, ten hosts.

```
                    ┌───────────────────────────────────────────┐
                    │            detector.py                    │
                    │  patterns only — no state, no judgement    │
                    │  (triggers + exclusions + quote stripping) │
                    └────────────────────┬──────────────────────┘
                                         │
                    ┌────────────────────▼──────────────────────┐
                    │            hookctl.py                    │
                    │  THE decision service (one implementation) │
                    │  • config (hot-reload)                    │
                    │  • caps: per turn / per session           │
                    │  • per-session state (v2, migrates v1)    │
                    │  • event log (skips included → denominator)│
                    │  • CLI so non-Python hosts can call it    │
                    └────────────────────┬──────────────────────┘
                                         │
        ┌───────────────┬───────────────┼───────────────┬────────────────┐
        │               │               │               │                │
   Claude Code        Codex          Qwen Code       Cursor        Copilot CLI
   Stop hook          Stop hook      Stop hook       stop hook     agentStop hook
        │               │               │               │                │
        │               │            Gemini CLI   OpenCode          Pi
        │               │            AfterAgent   JS plugin      TS extension
        │               │               │               │                │
        │               │            DeepSeek Harness (dsh)
        │               │            Cordis plugin
        │
   Hermes Agent (native plugin, same repo)
```

## Why the split

* **`detector.py` is transport.** It answers "does this reply *look* like it
  ends by asking permission?" — nothing more. It holds no state, so it can be
  unit-tested in isolation and reused from any language.
* **`hookctl.py` is policy.** It answers "should we nudge *now*?" — caps,
  state, logging. It is the only place where those rules exist.

Before this split, four hosts each carried their own copy of the counter and
the cooldown. They drifted: the counter never reset, so
`max_nudges_per_turn: 2` silently meant "two nudges per session, ever", and a
`cooldown_seconds: 300` default meant the hook went quiet for five minutes
after its first nudge. Now there is one implementation and the counters mean
what their names say.

## The layers of a host adapter

Each adapter is a thin shim with exactly two host-specific parts:

1. **how the payload is named** — `session_id` vs `sessionId` vs
   `conversation_id`; `last_assistant_message` vs `prompt_response` vs the
   transcript on disk;
2. **how the host is told to continue** — `{"decision":"block","reason":…}`
   (Claude Code, Codex, Qwen, Copilot), `{"followup_message":…}` (Cursor),
   `{"decision":"deny","reason":…}` (Gemini), `client.session.prompt(...)`
   (OpenCode), `return {continue:true}` (Pi), `agent.steer(...)` (dsh).

Everything else — reading the payload, recovering the reply text, asking for a
decision, writing the event — is shared.

## Turn semantics

A *turn* is one user request and the agent work that answers it. A host may
end a turn several times inside it, when a blocking hook makes it continue.

* `count` counts the nudges along the current chain of turn-ends.
* A turn that ends **without** a nudge closes the chain and resets `count`, so
  the next turn starts fresh.
* `total` counts the nudges in the session and is bounded by
  `max_nudges_per_session` — the backstop for hosts with no host-side cap
  (DeepSeek Harness, Cursor).
* `cooldown_seconds` is an optional session-level rate limit, **off by
  default**: the caps already bound the loop, and a non-zero value silently
  stops the hook from working for minutes after the first nudge.

## State file

`~/.hermes/continue-hook-state.json`

```json
{"version": 2, "sessions": {"<session-id>": {"count": 0, "total": 3, "last_ts": 0, "chain": false}}}
```

Version 1 was a flat `{"<session-id>": {"count": 1, "last_ts": …}}`; it is
migrated on read.

## Failure behaviour

Every layer fails **open**:

* the detector cannot decide → no nudge;
* the hook cannot read the payload → no nudge;
* the decision service is missing → no nudge;
* the host rejects the continuation → the turn ends normally.

A broken hook must never wedge somebody's agent session.
