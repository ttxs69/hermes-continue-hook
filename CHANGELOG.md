# Changelog

## 1.3.1 (2026-10-28)

- **The npm release now runs without a stored secret.** The publish workflow
  authenticates with the CI provider's OIDC identity (npm trusted publishing),
  so the repository holds no npm token. The package's provenance attestation
  is attached automatically.

## 1.3.0 (2026-10-28)

- **Published to npm as `@ttxs69/hermes-continue-hook`**, with a SLSA v1
  provenance attestation (`https://slsa.dev/provenance/v1`) and npm registry
  signatures. Install with
  `npx @ttxs69/hermes-continue-hook install --host all`.

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
  script: `npx @ttxs69/hermes-continue-hook install --host all`. Idempotent,
  non-destructive, `--dry-run` and `check` included.
- **npm package** (`@ttxs69/hermes-continue-hook`) with the CLI as its entry point.
- Quality gates: `ruff` (clean), `mypy` (clean), a version-consistency test
  (the version lived in four places and drifted), and a hygiene test that
  fails the build on local paths or credential-shaped strings.
- Docs: `docs/ARCHITECTURE.md` (how the layers fit) and
  `docs/CONFIGURATION.md` (every knob, with the defaults explained).
- Tests: 127 Python + 33 Node (9 OpenCode, 17 installer, 7 DeepSeek Harness).

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

- **Fixed: installing the Cursor/Copilot hook deleted the user's own hooks.**
  Cursor entries are `{command}`, Copilot entries are `{type, bash}` — neither
  carries a `hooks` array, and the cleaner assumed every entry had one. Flat
  entries are now preserved unless they are ours.
- **Fixed: the state file had no lock.** Eight hooks firing at once left only
  three of eight session counters on disk — a lost update that can let the
  nudge caps be exceeded. The read-modify-write now happens under an advisory
  lock; the same test now keeps 8/8.
- **Fixed: the JS/TS adapters passed the reply as a command-line argument**,
  which puts the user's conversation in `/proc/<pid>/cmdline` (readable by
  every process on the machine) and breaks on very long replies. The text now
  goes over stdin (`hookctl.py --text -`).
- **Fixed: a question mark inside code was a question.** Ternaries and
  optional chaining in a code block or inline code no longer trigger.
- **Fixed: the English exclusion list was thinner than the Chinese one** —
  an English agent could talk itself into a payment, a deletion or a publish.
- **Patch mode is generated now.** `patch-mode/continue_hook.py` was a second,
  hand-maintained copy of the detector with its own patterns, its own key
  names and a hardcoded `~/moneymaker/state/…` log path. It is generated from
  the shared engine by `scripts/build-patch-mode.py`, and the build fails
  when the generated file is stale.
- The DeepSeek Harness test now installs the shared decision service into
  its fixture — it used to pass while the plugin would have done nothing.

- **Fixed (release blocker): the Hermes plugin never fired.** Hermes calls
  lifecycle hooks with keyword arguments only (`response_text=…`), and the
  plugin's observer took a required positional `text` — so every call raised
  TypeError, the hook stored nothing, and the plugin silently did nothing.
  The callback now accepts the real call shape, and a test calls the hooks
  exactly the way `agent/turn_finalizer.py` does.
- **Fixed (release blocker): the installer never copied the adapters' shared
  transport module**, so every installed shell hook failed to import and
  failed open — installed and doing nothing. The installer now copies it,
  and a test drives an *installed* hook end-to-end.
- **Fixed: the plugin injected with `origin=True`**, which the host rejects
  (it wants a mapping) — and which also broke the CLI path, where no target
  must be passed at all. A rejected injection is now recorded in the event
  log instead of disappearing.
- **Fixed: a question mark inside a URL counted as a question**, so a reply
  ending with a link looked like it was asking permission.

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

