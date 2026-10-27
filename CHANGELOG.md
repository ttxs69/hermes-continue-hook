# Changelog

## 1.3.0 (2026-10-28)

- DeepSeek Harness (dsh) support as a **native Cordis plugin**
  (`adapters/dsh/`): hooks `agent/turn-stopping`, reads the reply from
  `agent.session.deriveMessages()`, and continues with
  `agent.steer(createUserMessage(...))`.
- `adapters/dsh/typecheck.sh` compiles the plugin against the real
  `@deepseek-ai/dsh-*` packages, so API drift fails the build.
- `install.sh dsh` stages the plugin, its bundle manifest and its cordis patch;
  `install.sh skills` now also covers `~/.dsh/skills`.
- Documented why dsh's Claude Code bridge cannot carry continue-hook: its Stop
  payload has no `last_assistant_message` and an empty `transcript_path`, and it
  applies no block cap (dsh discussion #1521).
- Tests: 16 dsh contract + detector-CLI tests.

## 1.3.0 (2026-10-28)

- **One decision service.** `hookctl.py` now owns the caps, the per-session
  state and the event log; `detector.py` is pure pattern matching. The Hermes
  plugin, the six shell-hook adapters, the OpenCode plugin, the Pi extension
  and the DeepSeek Harness plugin all ask the same service, so a cap cannot
  mean two different things in two hosts.
- **Fixed: the per-turn counter never reset.** `max_nudges_per_turn: 2` used
  to mean "two nudges per session, ever" — after two nudges the hook went
  silent for the rest of the session. A turn that ends without a nudge now
  closes the chain and resets the counter.
- **Fixed: `cooldown_seconds` defaulted to 300**, which silenced the hook for
  five minutes after its first nudge. It is now an optional session-level rate
  limit, off by default; the caps bound the loop instead.
- New `max_nudges_per_session` (default 20) — the backstop for hosts with no
  host-side cap (DeepSeek Harness, Cursor).
- **Cross-platform installer.** `bin/continue-hook.js` replaces the bash
  script: `npx hermes-continue-hook install --host all`. Idempotent,
  non-destructive, `--dry-run` and `check` included.
- **npm package** (`hermes-continue-hook`) with the CLI as its entry point.
- Quality gates: `ruff` (clean), `mypy` (clean), a version-consistency test
  (the version lived in four places and drifted), and a hygiene test that
  fails the build on local paths or credential-shaped strings.
- Docs: `docs/ARCHITECTURE.md` (how the layers fit) and
  `docs/CONFIGURATION.md` (every knob, with the defaults explained).
- Tests: 107 Python + 19 Node (OpenCode, installer, DeepSeek Harness).

## 1.2.0 (2026-10-28)

Multi-agent support, modelled on the per-harness manifest pattern used by
`obra/superpowers`.

- Enforcement adapters: Claude Code, Codex CLI, Qwen Code, Cursor, GitHub
  Copilot CLI, Gemini CLI, OpenCode (JS plugin), Pi (TS extension).
- Shared `adapters/_common.py`: one place for config, event log, per-session
  state, transcript reading and the nudge decision. Every adapter is now a
  thin shim over it.
- `detector.py --text …` CLI so non-Python hosts reuse the same rules instead
  of re-implementing them.
- Portable behaviour layer: `skills/continue-hook/SKILL.md` (Agent Skills
  standard) plus `AGENTS.md` / `QWEN.md` / `CLAUDE.md` / `GEMINI.md` context
  files, for agents that have no turn-end hook.
- Plugin manifests for the marketplace-based harnesses: `.claude-plugin/`,
  `.codex-plugin/`, `.cursor-plugin/`, `.kimi-plugin/`,
  `.antigravity-plugin/`, `.devin-plugin/`, `.muse-plugin/`,
  `.agents/plugins/marketplace.json`, `gemini-extension.json`, plus the
  `.opencode/` and `.pi/` package layouts.
- `adapters/install.sh` covers 8 hosts + 14 skill directories, idempotent and
  non-destructive (your other hooks and settings keys survive install and
  uninstall).
- Tests: 35 Python adapter tests, 13 Pi contract + detector-CLI tests, 9
  Node tests for the OpenCode plugin.

## 1.1.0 (2026-10-28)

- Claude Code Stop hook adapter.
- Codex CLI Stop hook adapter.
- `adapters/install.sh` for both hosts.

## 1.0.0 (2026-10-27)

- Initial release.
- Plugin mode (inject_message): zero core modification, follow-up-turn continuation.
- Patch mode: 4-line core gate, same-turn seamless continuation, version-guarded
  installer with `--remove` rollback.
- Configurable trigger/exclusion patterns (zh + en), hot-reload, bare question-mark
  trigger, quoted-mention immunity.
- Loop safety: per-turn nudge cap + per-session cooldown; boundary exclusions.
- Observability: full evaluation log (denominator included), `/ch-stats`, `/ch-test`,
  `dry_run` rollout mode.
