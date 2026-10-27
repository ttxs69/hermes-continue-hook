# Design: why these modes exist (and what was rejected)

`continue-hook` was dogfooded against a live Hermes gateway before release.
This document records the architecture search: 12 approaches were evaluated
against the constraint matrix, with reasons — so future contributors don't
re-litigate settled ground.

## Constraint matrix

The ideal mechanism is *same-turn seamless* (the question never reaches the
user) AND *zero core modification* (installable via `hermes plugins install`).
As of Hermes 2026-10, no plugin hook offers both. The plugin surface was
source-verified, not guessed.

## Evaluated approaches

| # | Approach | Verdict | Reason (source-verified) |
|---|---|---|---|
| 1 | `pre_verify` plugin hook | rejected | Core only invokes it after file edits in the turn (`turn_stop_gates.py: _edited gate`) — chat turns never reach it |
| 2 | `agent.verify_on_stop` config | rejected | Requires changed code paths; prose-only turns are explicitly suppressed (`verification_stop.py`) |
| 3 | Preventive `pre_llm_call` injection | shipped (optional layer) | Standing reminder appended to user messages — prevents asks from forming; prompt-level, weaker than mechanism-level |
| 4 | `pre_gateway_dispatch` rewrite | folded into 3 | Gateway-layer equivalent, messaging platforms only |
| 5 | Rewrite the question out via `transform_llm_output` | rejected | Dishonest fix: the question disappears but the work still isn't done |
| 6 | Stream observers (`on_stream_*`) | rejected | Read-only, off the token path, no continuation power |
| 7 | Provider-wrapper plugin (synthetic `tool_use` keeps the loop alive) | viable, advanced | Same-turn seamless + pure plugin; requires selecting a proxy provider. Not implemented — documented as the advanced path |
| 8 | **Patch mode** (4-line core stop gate) | **shipped** | Same-turn seamless today; version-guarded installer, `--remove` rollback; live-dogfooded |
| 9 | **Upstream PR** (make `pre_verify` fire on all text stops, config-gated) | roadmap (endgame) | Small change following the `verify_on_stop` config pattern; once merged, all modes collapse into one |
| 10 | **`inject_message` follow-up turn** | **shipped (default)** | Pure plugin, full session context; costs one turn boundary (see README "What you will actually see") |
| 11 | BOOT.md-style spawn of a fresh agent | rejected | No session context — strictly worse than 10 |
| 12 | Cron/watcher-based | rejected | Cross-turn by construction; cannot continue a live turn |

## Dogfooding findings that shaped the code

- **Quoted mentions are not asks.** A status report that quoted the trigger
  patterns got nudged (live incident). Fix: trigger matching runs on the
  quote-stripped tail; exclusion matching still scans the raw tail — the safe
  direction. Regression test carries the exact sentence.
- **Every evaluation must be logged, including skips.** Without the skip
  denominator there is no way to compute an ask-rate, and dry-run rollouts
  produce no data.
- **Boundary exclusions short-circuit first.** Better to miss a nudge than to
  interrupt a legitimate money/identity/publishing/destructive question.
