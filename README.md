# continue-hook

**Stop the agent from asking permission for things it should just do.**

An agent that ends its turn with 「要不要我顺便把这个补上？」 hands the user a
decision the agent was already able to make. continue-hook catches that
ending, asks the agent to re-check it once, and lets it finish the work.

**别再问了，直接做完。**

```text
Claude Code · Codex CLI · Qwen Code · Cursor · GitHub Copilot CLI ·
Gemini CLI · OpenCode · Pi · DeepSeek Harness · Hermes Agent
```

It never decides for the agent: the detection layer only says *this reply
looks like it is asking permission*; the agent keeps the final word on whether
the question was legitimate.

[![ci](https://github.com/ttxs69/hermes-continue-hook/actions/workflows/ci.yml/badge.svg)](https://github.com/ttxs69/hermes-continue-hook/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/hermes-continue-hook)](https://www.npmjs.com/package/hermes-continue-hook)
[![license: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Why

Agents constantly end turns with questions they could answer themselves:

> 要不要我把这个写进 skill？

The user then has to say "yes" for something the agent was allowed to do all
along. Every such turn is a wasted round-trip — and on a busy day, dozens of
them.

Existing "keep going" tools (`keep-going`, `infinite-claude-code-session`) solve a
different problem: **blind continuation** — never stop, keep burning tokens.
This plugin is **conditional continuation**: it fires only on permission-seeking
endings, and the judgment of *"was this question legitimate?"* stays with the
agent.

| | blind continuation (`keep-going`) | continue-hook |
|---|---|---|
| Fires on | every stop | only permission-seeking endings |
| Judgment | none — always continues | the agent re-checks boundaries |
| Stops for | (never) | real user-only boundaries |
| Cost | burns tokens while looping | one extra turn, only when it fired |

## ⚠️ Read this before choosing a mode

The plugin itself cannot modify Hermes core, so in **plugin mode** it works
*one turn later*:

```
You:    帮我优化 README
Bot:    要不要我顺便也把示例补上？        ← turn 1 ends; the question slips out
Bot:    【自动续】已直接完成：README 优化 + 示例补充。验证：lint 通过。  ← turn 2, seconds later
```

The nudge is injected as a new user-role message into the **same session**
(full conversation history intact), which starts a follow-up turn where the
agent finishes the work by itself. Functionally equivalent — but you will see
**two messages instead of one**, and the stray question stays in the chat log.

**Delivery depends on the host, and one case is not supported yet.**

| Session | Delivered? |
|---|---|
| CLI / TUI / desktop | Yes — the nudge goes into the local input queue |
| Messaging gateway (Telegram, QQ, Discord …) | **No.** `on_session_end` is given `session_id`, `task_id`, `turn_id`, `completed`, `failed`, `interrupted`, `turn_exit_reason`, `model` and `platform` — neither the durable composite `session_key` nor an `origin` mapping that a gateway injection requires. Fabricating a key would target a session that does not exist. |

On a gateway the hook still evaluates and logs, and records
`inject_unsupported_gateway` so the gap is visible in `/ch-stats` instead of
looking like a nudge that silently went nowhere. **For same-turn continuation on
a messaging gateway use [patch mode](patch-mode/README.md)** — it runs inside the
turn and needs no injection.

Injection is privileged, so Hermes also gates it per plugin; a self-hosted
gateway deployment that does pass a `session_key` needs:

```yaml
plugins:
  entries:
    continue-hook:
      allow_gateway_injection: true
```

For the seamless behavior (the gate fires *inside* the agent loop, the question
is never delivered, you only get the finished result) use **patch mode** —
see [Modes](#modes) below.

## Install

```bash
# every host on this machine
npx hermes-continue-hook install --host all

# one host
npx hermes-continue-hook install --host claude-code
```

For Hermes Agent itself:

```bash
hermes plugins install ttxs69/hermes-continue-hook
hermes plugins enable continue-hook
```

Then configure (optional — the defaults work):

```bash
cat > ~/.hermes/continue_hook.json <<'JSON'
{
  "enabled": true,
  "dry_run": false,
  "max_nudges_per_turn": 2,
  "max_nudges_per_session": 20
}
JSON
```

Config changes hot-reload — **no restart needed**. A malformed file falls back
to defaults (it can never crash the gateway).

## Quick start (30 seconds)

```bash
npx hermes-continue-hook install --host all
# then ask an agent for something trivial:
#   "帮我把 README 的安装步骤改短一点"
# and watch whether it asks permission for things it could just do.
```

Check what it saw:

```bash
cat ~/.hermes/continue-hook-events.jsonl | tail -5
```

In Hermes, the same numbers are available as `/ch-stats`, and
`/ch-test 要不要我继续？` dry-runs the detector on any sentence.

## How it works

```
agent turn ends with "…要不要我继续？"
   ↓ detect (configurable: offer / hand-off / confirm-seek / bare ？)
   ↓ exclude: money, identity, publishing, destructive actions, real blockers
   ↓ nudge the agent to self-check:
        touches a user-only boundary?  → re-ask in ONE short line, then stop
        missing info only the user has? → re-ask in ONE short line, then stop
        otherwise                       → DO IT, then report result + verification + risks
```

Detection is a **transport layer** with zero judgment power: it decides only
*whether to consider nudging*. Whether the caught question was legitimate is
decided by the agent, inside the nudge. Patterns are plain configurable regexes
— no model call, no latency.

## Configuration

`~/.hermes/continue_hook.json` (all keys optional; missing keys fall back to defaults):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master switch |
| `dry_run` | `false` | Log-only rollout: record what *would* fire, inject nothing |
| `max_nudges_per_turn` | `2` | Nudges allowed along one turn (resets when a turn ends cleanly) |
| `max_nudges_per_session` | `20` | Backstop for hosts without a host-side cap (DeepSeek Harness, Cursor) |
| `cooldown_seconds` | `0` (off) | Optional minimum gap between two nudges in a session |
| `tail_chars` | `240` | Only inspect the closing N characters |
| `triggers.offer` | — | First-person offers: `要不要我`, `需要我…做吗`, `should i`, `want me to` |
| `triggers.hand_off` | — | Decision hand-offs: `你定`, `听你的`, `your call` |
| `triggers.confirm_seek` | — | Confirmation-seeking: `这样可以吗`, `does this work` |
| `triggers.question_mark` | `true` | Any `？/?` in the closing tail |
| `exclusions.boundary` | — | User-only boundaries: money, identity, credentials, publishing, destructive actions |
| `exclusions.user_must` | — | Second-person requests: only the user can supply/decide this |

Add regexes freely — they are plain Python regex strings. Quoted spans are
stripped before trigger matching (quoting a pattern in a report is a *mention*,
not an *ask*), while exclusion matching still scans the raw text — the safe
direction.

## Supported agents

Three tiers, because agents genuinely differ in what they let you do:

| Agent | Tier | Mechanism | Continue signal |
|---|---|---|---|
| **Claude Code** | enforce | `Stop` hook, `~/.claude/settings.json` | `{"decision":"block","reason":…}` |
| **Codex CLI** | enforce | `Stop` hook, `~/.codex/hooks.json` | same |
| **Qwen Code** | enforce | `Stop` hook, `~/.qwen/settings.json` (Claude-compatible) | same |
| **Cursor** | enforce | `stop` hook, `~/.cursor/hooks.json` | `{"followup_message":…}` |
| **GitHub Copilot CLI** | enforce | `agentStop` hook, `~/.copilot/hooks/` | `{"decision":"block","reason":…}` |
| **Gemini CLI** | enforce | `AfterAgent` hook, `~/.gemini/settings.json` | `{"decision":"deny","reason":…}` |
| **OpenCode** | enforce | JS plugin, `session.idle` event | `client.session.prompt(...)` |
| **Pi** | enforce | TS extension, `agent_before_settle` | `return { continue: true }` |
| **DeepSeek Harness (dsh)** | enforce | native Cordis plugin, `agent/turn-stopping` | `agent.steer(createUserMessage(...))` |
| **Hermes Agent** | enforce | native plugin (this repo's origin) | `inject_message` |
| Kimi Code, Antigravity, Devin CLI, Factory Droid, Grok Build, Muse, Windsurf, Aider, Continue, Cline, Roo, goose, Zed | behavior | `SKILL.md` (Agent Skills standard) + `AGENTS.md` | — (the rule, not a forced turn) |

**enforce** = the host can block the turn end and force another model request.
**behavior** = the agent reads the rule and stops asking on its own.

Every enforce-tier agent shares one detection engine (`detector.py`), one
config (`~/.hermes/continue_hook.json`) and one event log, so the ask-rate is
comparable across all of them.

### Install

```bash
npx hermes-continue-hook install --host all      # every host on this machine
npx hermes-continue-hook install --host claude-code
npx hermes-continue-hook check                    # what is installed
npx hermes-continue-hook uninstall --host all     # remove (keeps your other hooks)
```

Or from a clone:

```bash
node bin/continue-hook.js install --host all
```

The installer is idempotent (installing twice never duplicates a hook) and
non-destructive (your other hooks and settings keys survive both install and
uninstall). `--dry-run` shows what would change without touching anything.

Host-by-host:

| Host | Install | Extra step |
|---|---|---|
| Claude Code | `--host claude-code` | — |
| Codex CLI | `--host codex` | run `/hooks` once in the CLI to trust the hook |
| Qwen Code | `--host qwen` | — |
| Cursor | `--host cursor` | — |
| Copilot CLI | `--host copilot` | — |
| Gemini CLI | `--host gemini` | — |
| OpenCode | `--host opencode` | plugin goes to `~/.config/opencode/plugin/` |
| Pi | `--host pi` | extension goes to `~/.pi/agent/extensions/` |
| DeepSeek Harness | `--host dsh` | `npm install && npm run build`, then `dsh plugin add <dir>` |

Plugin marketplaces for the hosts that have a manifest format read the files
in this repo. Present: Claude Code (`.claude-plugin/`), Codex
(`.codex-plugin/`), Cursor (`.cursor-plugin/`), Kimi (`.kimi-plugin/`),
Antigravity (`.antigravity-plugin/`), Devin (`.devin-plugin/`), Muse
(`.muse-plugin/`), Gemini (`gemini-extension.json`), and the cross-host
`.agents/plugins/marketplace.json`. Copilot CLI, Grok Build, Factory Droid and
Windsurf have no manifest format — install them with `--host copilot` (or the
portable skill layer), which is what actually works:
`.claude-plugin/`, `.codex-plugin/`, `.cursor-plugin/`, `.kimi-plugin/`,
`.antigravity-plugin/`, `.devin-plugin/`, `.muse-plugin/`,
`.agents/plugins/marketplace.json`, `gemini-extension.json`.

### Verify a host actually fires

```bash
printf '%s' '{"session_id":"t","stop_hook_active":false,"last_assistant_message":"要不要我顺便也把示例补上？"}' \
  | python3 ~/.hermes/continue-hook/claude-code/stop_hook.py
# → {"decision": "block", "reason": "【continue-hook】…"}
```

The same payload shape works for Codex and Qwen (`last_assistant_message`);
Cursor needs `transcript_path` instead; Gemini needs `prompt_response`.

### Patch mode is version-pinned — read this before using it

Patch mode modifies the **Hermes core source tree**. That makes it the least
maintainable mode: the patch is written against one Hermes version, and it
**refuses to install** when the target does not match (`git apply --check`
fails, or the version guard does not find the stop-gate chain). A refused
install is the intended behaviour, not a bug — use plugin mode instead.

The file it drops in (`patch-mode/continue_hook.py`) is **generated** from the
shared engine by `scripts/build-patch-mode.py`, so it can never become a
second, drifting implementation of the rules. The build fails when it is out
of date.

## Docs

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the layers fit, and why
  the policy lives in one place
* [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — every knob and its default
* [docs/DESIGN.md](docs/DESIGN.md) — the twelve routes evaluated for the
  Hermes plugin, and why each was rejected
* [docs/PUBLISHING.md](docs/PUBLISHING.md) — how a release reaches npm

## Tests

```bash
python3 -m unittest discover -s tests   # 144 tests: detector, plugin, 8 adapters, dsh/pi contracts
node tests/test_opencode.mjs            # 13 tests: OpenCode plugin against a mock SDK client
node tests/test_installer.mjs           # 33 tests: install / idempotence / uninstall / non-destruction
node tests/test_dsh.mjs                 # 7 tests: dsh plugin, compiled against the real dsh packages
bash adapters/dsh/typecheck.sh          # compiles the dsh plugin against @deepseek-ai/dsh-*
```

The dsh tests need npm registry access (they install the real dsh packages);
everything else runs offline.

## Modes

| Mode | Same-turn seamless | Zero core change | Install |
|---|---|---|---|
| **Plugin (default)** | ✗ (follow-up turn, full context) | ✅ | `hermes plugins install` |
| **Patch mode** — a core stop gate; version-pinned, see the caveat below | ✅ | ✗ | `patch-mode/install.sh <hermes-agent-path>` |
| **Preventive layer** — standing reminder appended to user messages (optional `hooks.pre_llm_call`) | prevention | ✅ | copy `agent-hooks/ch_preventive.sh` into `~/.hermes/agent-hooks/` and register it |
| Upstream PR (make `pre_verify` fire on all text stops) | ✅ | ✅ after merge | roadmap |

Patch mode touches **four core files** (three modified, one new) and is pinned to
core commit `d923a5e`. `patch-mode/install.sh --check <path>` tells you whether
your tree is one it can patch; the installer refuses anything else rather than
half-applying a stale diff.

Twelve approaches were evaluated against this matrix; the rejected ones and
their source-verified reasons are in [`docs/DESIGN.md`](docs/DESIGN.md).

## Safety

- Per-turn nudge cap (default 2) and per-session cap (default 20). The cooldown is
  **off by default** — a non-zero value silently stops the hook for minutes after
  the first nudge.
- **The per-turn cap is a ceiling, not a promise.** Claude Code, Codex, Qwen,
  Cursor, Copilot and Gemini each report "you already continued this stop", and
  the hook never blocks twice in the same stop cycle — so on those six hosts a
  turn gets **one** nudge however high the cap is. Hosts that run the plugin in
  process (Hermes) or through their own extension API (OpenCode, Pi, dsh) can
  reach the configured number.
- Boundary exclusions short-circuit before any trigger: money / identity /
  credentials / publishing / destructive actions are re-asked, never auto-run
- Kill switch: config `enabled: false`, or env `HERMES_CONTINUE_HOOK=0`
- Fail-safe: the plugin never raises into the gateway — a broken config or a
  failed hook degrades to "no nudges", not to a broken session

## Observability

Every evaluation — including skips, so the denominator exists — is appended to
`~/.hermes/continue-hook-events.jsonl`:

```json
{"ts":"2026-10-28 01:03:31","session":"s1","platform":"cli","decision":"skip","reason":"no_trigger","attempt":0,"snippet":"…"}
```

`decision` is `nudge` | `skip` | `nudge_dryrun` | `skip_dryrun` | `error` (the two
`_dryrun` values appear when `dry_run: true` is configured); `reason` says which
rule fired (`trigger:offer`, `boundary`, `user_must`, `cooldown`, `max_nudges`,
`chain_closed`, …).

**Privacy:** each record carries the last ~160 characters of the reply as
`snippet`, so you can see why a decision was made. That is agent output, not
user input, but it is still conversation content — the log file is written
`0600`, and the directory is created `0700` when this tool creates it. Set
`"log_snippet": false` to record the decision
without the text, or point `"events_path"` at a directory you control.

## Development

```bash
./scripts/check.sh        # everything CI runs, one command
npm install               # dev tooling: eslint, tsc, typescript-eslint
```

### Quality gates

| Language | Tool | Config | Runs on |
|---|---|---|---|
| Python | **ruff** (check + format) | `pyproject.toml` | every push |
| Python | **mypy** (strict-ish) | `pyproject.toml` | every push |
| Python | **pytest** + **coverage** | `pyproject.toml` (`fail_under = 65`) | every push |
| Python | **unittest** (dependency-free) | — | 3.10 / 3.11 / 3.12 / 3.13 |
| JS / TS | **eslint** (flat config, typescript-eslint) | `eslint.config.mjs` | every push |
| TS | **tsc** (`strict`, `noUnusedLocals`) | `tsconfig.pi.json`, `adapters/dsh/typecheck.sh` | every push |
| JS | node test suites | `tests/*.mjs` | 18 / 20 / 22 |

The test suite is stdlib `unittest`, so it runs with **no dependencies at all**;
the same suite passes under `pytest`, which is what measures coverage. Coverage
of the shipped Python modules is 70% overall (detector 84%, `__init__` 76%,
patch mode 71%, `_common` 69%, `hookctl` 63%) and the build fails below 65%.

`ruff format` skips `patch-mode/continue_hook.py` — it is generated by
`scripts/build-patch-mode.py`, so its spacing follows the assembly, not the
formatter. It is still linted.

```bash
python -m unittest discover -s tests     # 144 tests
python -m pytest --cov                   # same suite + coverage floor
node tests/test_opencode.mjs             # 13
node tests/test_installer.mjs            # 33
node tests/test_lifecycle.mjs            # 12 invariants
node tests/test_dsh.mjs                  # 7
npx eslint . && npx tsc -p tsconfig.pi.json
```

## Docs

- [`docs/DESIGN.md`](docs/DESIGN.md) — architecture search, 12 evaluated approaches,
  dogfooding findings
- [`CHANGELOG.md`](CHANGELOG.md) — release notes
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to add patterns and tests

## License

MIT — see [LICENSE](LICENSE).
