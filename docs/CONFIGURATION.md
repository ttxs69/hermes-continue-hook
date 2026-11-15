# Configuration

One file, shared by every host:

```
~/.hermes/continue_hook.json
```

```jsonc
{
  "enabled": true,                 // false disables every host at once
  "dry_run": false,                // log what would fire, never nudge
  "max_nudges_per_turn": 2,        // nudges along one turn's chain
  "max_nudges_per_session": -1,    // v1.6.0: OFF by default — lifetime caps disarm the hook in long sessions; set >0 to re-enable the backstop
  "cooldown_seconds": 0,           // optional session-level rate limit (off)
  "tail_chars": 240,               // how much of the reply is inspected

  "triggers": {                    // any hit makes the reply a candidate
    "offer": ["要不要我", "should i\b", "do you want me to\b", …],
    "hand_off": ["听你的", "交给…你定", "up to you", …],
    "confirm_seek": ["这样可以吗", "does this work", …],
    "question_mark": true,         // any ? or ？ in the tail
    "unverified_claim": {          // completion claim with no visible evidence
      "completion_words": ["已完成", "已修", "搞定", "done", "fixed", …]
    }
  },

  "exclusions": {                  // any hit short-circuits to "no nudge"
    "boundary": ["花钱|付费|批准|实名|密码", "cost|approve|password|2fa", …],
    "user_must": ["需要你|你来提供|你告诉我", …]
  }
}
```

The file is hot-reloaded: edit it and the next turn uses the new rules. A
missing or broken file falls back to the defaults — it never breaks a turn.

## What the detection layer does and does not do

The detector only decides **whether to consider nudging**. Whether the caught
question was legitimate is decided by the agent, inside the nudge:

* money, real-name identity, publishing externally, deleting data, changing
  shared systems → re-ask in one line, then stop;
* information only the user has → re-ask in one line, then stop;
* anything else → do the work, report result + verification + remaining risks.

The exclusions list is the first line of defence for those cases, but the
agent has the final word — that is why the nudge asks rather than commands.

## Where to look

| What | Where |
|---|---|
| every evaluation (including the skips) | `~/.hermes/continue-hook-events.jsonl` |
| per-session counters | `~/.hermes/continue-hook-state.json` |
| the agent's own view (Hermes) | `/ch-stats`, `/ch-test 要不要我继续？` |
