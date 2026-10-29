# continue-hook

**Stop your agent from ending every turn with a question it could answer itself.**

> "Do you want me to continue?" · "Want me to run the tests?" · "要我顺便把这个也做了吗？" (Chinese)

Your agent finishes the work — then asks whether it may do the next obvious
thing. It hands you a decision it was already able to make, and you spend a
turn on a one-word answer. On a busy day that is dozens of wasted round-trips.

continue-hook catches that ending, asks the agent to re-check it once, and lets
it finish the work.

**Stop asking. Just finish the work.**

```text
Claude Code · Codex CLI · Qwen Code · Cursor · GitHub Copilot CLI ·
Gemini CLI · OpenCode · Pi · DeepSeek Harness · Hermes Agent
```

It never decides for the agent: the detection layer only says *this reply
looks like it is ending on an offer*; the agent keeps the final word on whether
the question was legitimate.

> **Not the same thing as permission prompts.** Claude Code's `⏵ Allow Bash(…)`
> dialog is a *tool-permission* question and this plugin does not touch it.
> What this catches is the agent asking *you* whether to keep working.

[![ci](https://github.com/ttxs69/hermes-continue-hook/actions/workflows/ci.yml/badge.svg)](https://github.com/ttxs69/hermes-continue-hook/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/@ttxs69/hermes-continue-hook)](https://www.npmjs.com/package/@ttxs69/hermes-continue-hook)
[![license: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[简体中文](README.zh-CN.md)

## Why

Agents constantly end turns with questions they could answer themselves:

> "Should I add this to the skill?"

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
You:    Tighten the README install steps
Bot:    Want me to also tighten the examples?   ← turn 1 ends; the question slips out
Bot:    [auto-continue] Done: README install steps tightened + examples tightened.
        Verification: lint passed.             ← turn 2, seconds later
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
npx @ttxs69/hermes-continue-hook install --host all

# one host
npx @ttxs69/hermes-continue-hook install --host claude-code
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
npx @ttxs69/hermes-continue-hook install --host all
# then ask an agent for something trivial:
#   "Tighten the README install steps"
# and watch whether it asks permission for things it could just do.
```

Check what it saw:

```bash
cat ~/.hermes/continue-hook-events.jsonl | tail -5
```

In Hermes, the same numbers are available as `/ch-stats`, and
`/ch-test Want me to continue?` dry-runs the detector on any sentence.

## Verify it works — without waiting for a real conversation

"Ask the agent something and hope" is not a test. This reproduces the whole
chain in one command: the same detector, the same decision service, the same
JSON the host receives — with a message that *must* be nudged and one that
*must not* be.

The published package contains the adapter, so this works **before** you
install anything:

```bash
npm pack @ttxs69/hermes-continue-hook --silent   # → ttxs69-hermes-continue-hook-<version>.tgz
tar xzf ttxs69-hermes-continue-hook-*.tgz

# 1. the trigger fires on a permission-seeking ending
printf '%s' '{"last_assistant_message":"好的，已经改完了。\n\n要不要我顺便把示例也补上？"}' \
  | python3 package/adapters/claude-code/stop_hook.py

# 2. a clean delivery is left alone
printf '%s' '{"last_assistant_message":"已完成 README 优化。验证：lint 通过。"}' \
  | python3 package/adapters/claude-code/stop_hook.py
```

Expected output — case 1 prints a JSON object asking the agent to re-check
itself, case 2 prints nothing:

```json
{"decision": "block", "reason": "【continue-hook】Your last reply ended by asking the user a question.\nSelf-check once, per standing discipline:\n1. Does what you asked touch a user-only boundary …"}
```

If you already installed it, the same check runs against the installed copy:

```bash
printf '%s' '{"last_assistant_message":"要不要我继续？"}' \
  | python3 ~/.hermes/continue-hook/claude-code/stop_hook.py
```

The hook only *asks the agent to look again*. Whether the question was
legitimate is still the agent's call — that is the whole design.

If case 1 prints nothing, the hook is not installed for that host. Run
`npx @ttxs69/hermes-continue-hook check` to see which hosts are wired.

## What it looks like in practice

Measured on a real machine, over 60 evaluations (the plugin's own event log,
`~/.hermes/continue-hook-events.jsonl`):

| | |
|---|---|
| evaluations | 60 |
| nudges | 32 (53% of evaluations) |
| most common trigger | `trigger:offer` — 31 of 32 nudges |
| skipped | `no_trigger` 14, `cooldown` 9, `stop_hook_active` 3, `boundary` 2 |

A 53% hit rate means the agent really does end roughly every other turn with
a permission-seeking question — that is the problem this exists for. The
`boundary` skips are the important ones: they are the cases where asking
was correct.

## How it works

```
agent turn ends with "…Want me to continue?"
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
npx @ttxs69/hermes-continue-hook install --host all      # every host on this machine
npx @ttxs69/hermes-continue-hook install --host claude-code
npx @ttxs69/hermes-continue-hook check                    # what is installed
npx @ttxs69/hermes-continue-hook uninstall --host all     # remove (keeps your other hooks)
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
| Portable skills layer | `--host skills` | writes `SKILL.md` + `AGENTS.md` — the rule, not a forced turn |

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
  (trusted publishing / OIDC, and the staged-publishing path for a first release)

## Tests

```bash
python3 -m unittest discover -s tests   # 191 tests: detector, plugin, 8 adapters, dsh/pi contracts
npx vitest run                          # 68 tests: OpenCode plugin, installer, lifecycle invariants, dsh
bash adapters/dsh/typecheck.sh          # compiles the dsh plugin against @deepseek-ai/dsh-*
```

The dsh suite needs npm registry access (it installs the real dsh packages);
everything else runs offline.

Both suites are also reachable through `npm test`, and the whole gate — linters,
type-checkers, both test suites, packaging — through `./scripts/check.sh`.


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
npm install               # dev tooling: eslint, prettier, tsc, vitest, typescript-eslint
./scripts/check.sh        # everything CI runs, one command
```

`npm install` also installs two git hooks (husky): a `pre-commit` that runs
the fast checks on the files you staged (~2 s), and a `pre-push` that runs
the whole gate (~60 s) — so a mismatch with CI shows up before the push
rather than after it. See [CONTRIBUTING.md](CONTRIBUTING.md).

Use the **exact** node version in `.nvmrc` (`nvm use`). The gate and the
hooks compare the whole version and refuse to run on anything else. A major
prefix is a range, and a range resolves differently in different places:
`.nvmrc` held `24`, CI resolved it to 24.21.0, and this machine ran 24.22.1.
A check that runs on a different node than CI does not predict CI.

### Quality gates

Each row is the job one tool does, and the tool this repository uses for it.
Python and TypeScript ecosystems solve the same four problems with different
binaries; the point of the table is that no row is missing.

| Job | Python | TypeScript / JS | Config | Runs on |
|---|---|---|---|---|
| **Lint** | ruff `check` | **eslint** (flat config, typescript-eslint) | `pyproject.toml`, `eslint.config.mjs` | every push |
| **Complexity** | ruff `C901` (ceiling **10**) | eslint `complexity` (ceiling **10**) | `pyproject.toml`, `eslint.config.mjs` | every push |
| **Format** | ruff `format` | **prettier** | `pyproject.toml`, `.prettierrc.json` | every push |
| **Types** | **mypy** | **tsc** — `strict` for the TS sources, `checkJs` for the JS ones | `pyproject.toml`, `tsconfig.json`, `tsconfig.checkjs.json` | every push |
| **Tests** | **pytest** | **vitest** | `pyproject.toml`, `vitest.config.mjs` | every push |
| **Coverage floor** | pytest-cov (`fail_under = 80`) | vitest v8 (`lines 80`) | same files | every push |
| **Zero-dependency run** | stdlib `unittest` | — | — | 3.10–3.13 |

**The complexity ceilings are not decoration.** Before the gate existed, the
worst function in the shipped code was 47 (the installer's `main`) and the
worst in Python was 17 (the plugin entry point) — every one of them a decision
table, a parse, or a per-kind dispatch written as a chain of branches. The fix
was the "functional core, imperative shell" split: the *decision* moved into a
pure function that takes data and returns data, and the *shell* kept the I/O.
`parseArgs` now throws a `UsageError` instead of calling `process.exit`;
`configShapeProblem` answers a question instead of validating in place; the
installer's per-kind work is three small functions and a dispatch, not one
47-branch function. The highest function left is 9.

Both ceilings are checked by their own test (`tests/test_complexity_gate.py`),
which asserts the configured threshold, proves a deliberately too-complex
function *is* reported, and runs the real check over the real files — a gate
that cannot fail is not a gate.

Python's `ruff` plays two roles (lint and format) in one binary; on the JS side
those are two tools, eslint and prettier. `mypy` has no JS equivalent that
works without annotations, so `tsconfig.checkjs.json` deliberately runs with
`noImplicitAny: false`: it catches the bugs that actually shipped — a property
that does not exist, an `unknown` error read as an `Error` — without demanding
JSDoc on two thousand lines of plain ESM. The TS sources (`adapters/pi`,
`adapters/dsh`) are checked under full `strict`.

**What the coverage numbers cover, and what they do not.** The Python suite
imports its modules, so its 79% is real statement coverage of the shipped code
(`hookctl` 94%, `detector` 84%, `__init__` 76%, `_common` 69%, patch mode 71%).
The JS coverage floor applies to the OpenCode plugin (82% statements), which its
tests import. The installer is **not** in that number: its tests drive the real
CLI in a throwaway `HOME` as a subprocess — the only faithful way to test a
program whose entire job is editing other files — and V8 coverage in the test
process cannot see a child process. Its evidence is behavioural: 33 installer
tests and 15 lifecycle invariants, all against the real binary. Counting it as
0% would have been a lie in the other direction, so it is excluded rather than
averaged in.

`ruff format` skips `patch-mode/continue_hook.py` — it is generated by
`scripts/build-patch-mode.py`, so its spacing follows the assembly, not the
formatter. It is still linted. `prettier` skips Markdown for the same kind of
reason: the docs are hand-authored and reviewed by eye, and reflowing tables
would be churn, not hygiene.

```bash
python -m unittest discover -s tests     # 191 tests, no dependencies
python -m pytest --cov                   # same suite + coverage floor
npx vitest run --coverage                # 68 tests + coverage floor
npx eslint . && npx prettier --check '**/*.{js,mjs,ts,json,yml}'
npx tsc -p tsconfig.json && npx tsc -p tsconfig.checkjs.json
```


## Docs

- [`docs/DESIGN.md`](docs/DESIGN.md) — architecture search, 12 evaluated approaches,
  dogfooding findings
- [`CHANGELOG.md`](CHANGELOG.md) — release notes
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to add patterns and tests

## License

MIT — see [LICENSE](LICENSE).
