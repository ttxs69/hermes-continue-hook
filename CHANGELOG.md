# Changelog

## 1.4.0 (2026-10-29)

A new trigger class: **announced-but-undone**.

- **The gap:** every existing trigger fires when the agent pushes a decision back
  to the user — an offer (`要不要我…`), a hand-off (`交给你定`), a confirmation
  (`这样可以吗`), or any closing question mark. None of them fires when the agent
  says what it *will do next* and then ends the turn. That reply has no question
  mark and asks for nothing; it silently drops the work.

- **The incident:** asked what came next, the agent ended a reply with
  「下一步我去查 Replume 有没有定价页和真实客户证言」 and stopped. The task it had
  just described never ran. Live, this is the exact behaviour the hook exists to
  prevent, and it was going undetected.

- **`triggers.promise`** matches an announced next step in the **closing tail
  only** — a narrative 「下一步是…」 in the middle of a report does not fire, and a
  promise that was already carried out (`…我已经部署完了`) does not either.
  Chinese and English forms are both covered.

- **`promise` is evaluated before the boundary exclusions.** The real closing
  sentence above contains 「付费**证据**」 — a noun — and `boundary`'s 「付费」
  matched it, releasing the whole reply. Word lists cannot tell a noun from an
  intent, and adding words only moves the hole. A stated next step is not a
  question being asked, so it is judged first; the promise nudge's first clause
  already covers the boundary case ("if that step needs something only the user
  has, ask for that one thing and stop").

- **`build_nudge(attempt, reason)`** takes the reason, so a promise gets its own
  text ("you said you would do it — do it now") instead of the offer text.

- `_first_trigger_hit` now names `promise` in its dispatch tuple. Adding a key to
  `triggers` without naming it there is a silent no-op — that is how the first
  version of this change produced `no_trigger` on every test case.

- 10 new tests in `TestPromise` (including the live incident sentence verbatim,
  the noun-collision case, and both regression directions).

- **False positives are measured, not assumed.** The first version of this
  trigger fired on 6 of 33 hand-written reply shapes (18.2%) — three from the
  double-judgement bug above, two from a `next step` pattern that did not
  require a first-person actor, one from an already-carried-out promise.
  `TestPromiseFalsePositiveBudget` now pins those 33 shapes to a <10% budget
  (current 3.1%), nails the code-block bug, asserts `promise` is not
  double-judged, and pins recall so tightening cannot silently kill it.

- **Measured on real traffic**, not just constructed cases: replaying the 1430
  real turn-final assistant replies in the local session store,
  1.3.5 nudges 31 (2.2%), this version nudges 34 (2.4%) — 3 extra, all
  `trigger:promise`. The trigger fires on ~0.2% of real turns.

- 329 tests pass.

## 1.3.5 (2026-10-29)

Three more fixes the independent red team found on 1.3.4.

- **The node-version check had a hole of its own.**
  1.3.4 checked that `.nvmrc` pinned a node *at or above* 24.18.0, but that
  comparison is not monotone across node majors: node 26.0.0–26.2.0 sort
  above 24.18.0 and still ship npm 11.12.1 / 11.13.0, which is short of the
  11.15.0 that `npm stage publish` needs. The check is now a **whitelist of
  ranges** whose members all ship a new enough npm — 24.18.0 → 24.x and
  26.3.0 → 26.x — and node 25.x is absent because every 25.x release ships
  npm ≤ 11.12.1. An unknown major (node 27, say) is **rejected** until
  somebody checks what npm it ships and adds the range.

- **The negative control was a tautology.**
  `tests/test_engines.py` had a "negative control" that re-implemented the
  rule against a hard-coded dictionary instead of calling the real one — so
  deleting the real gate, or inverting its assertion, left the negative
  control green. The rule now lives in one function (`engines_problems()`)
  that both the gate and the negative control call, and a separate test
  asserts the gate itself still exists (without it, nothing reads the real
  package.json any more).

- **The README's host table was missing one host.**
  `--host skills` is accepted by the CLI and does something real (it writes
  `SKILL.md` + `AGENTS.md` — the rule, not a forced turn), but it was absent
  from the host-by-host table in both READMEs.
## 1.3.4 (2026-10-29)

- **The README told people to run a command that does not exist.**
  Two lines said `npx hermes-continue-hook check` / `npx hermes-continue-hook
  uninstall --host all` — the *unscoped* name. The package is published under
  the scope `@ttxs69/hermes-continue-hook`, and the unscoped name has never
  existed on the registry (`GET /hermes-continue-hook` → 404). Anyone who
  followed the install section hit a 404 on their first command. Both READMEs
  now use the scoped name.

- **The npm-version requirement is now checked where it matters.**
  1.3.3 moved the requirement out of `engines` (it belongs to the release
  toolchain, not to everyone who installs the package) and left it in a
  comment in `publish.yml`, with a test asserting the comment still contained
  the string "11.15.0". That test could not fail for the reason it existed:
  comments get rewritten, and no npm version was ever compared to anything.

  `publish.yml` now compares the running npm against the floor it needs
  (`sort -V`, so 11.9.0 is correctly read as older than 11.15.0) and fails
  the job if it is too old. The unit test no longer pretends to check a
  comment; it checks that the publish workflow still runs
  `npm stage publish`, and that `.nvmrc` pins a node whose bundled npm is
  new enough.

  That last check used to be "node major >= 24", which was wrong twice over:
  node 24.0.0 ships npm 11.3.0 (too old), and **every** node 25.x ships npm
  ≤ 11.12.1 (too old, because the odd-numbered line carries an older npm).
  The floor is now node 24.18.0 (npm 11.16.0) or node 26.3.0, derived from
  nodejs.org's own release metadata.

- **The release workflow's shell blocks are now syntax-checked.**
  While adding the check above, one `echo` lost its closing quote. YAML
  parsed it fine, prettier passed it, and every existing test was green —
  but bash would have failed at publish time, after the tag was already
  pushed. A new test feeds every `run:` block in every workflow to `bash -n`,
  and the test that does so also proves it fails on an unclosed quote.
## 1.3.3 (2026-10-29)

- **`engines` no longer requires a specific npm version.**
  The manifest said `"npm": ">=11.15.0"`. That number is the **release
  toolchain's** requirement — `npm stage publish` needs 11.15.0+ and
  trusted publishing needs 11.5.1+ — but putting it in `engines` turned it
  into a requirement on **every person who installs the package**. Anyone on
  a default npm saw this before the CLI even started:

  ```
  npm warn EBADENGINE Unsupported engine {
  npm warn EBADENGINE   package: '@ttxs69/hermes-continue-hook@1.3.2',
  npm warn EBADENGINE   required: { npm: '>=11.15.0' },
  npm warn EBADENGINE   current: { node: 'v22.23.2', npm: '10.9.8' }
  npm warn EBADENGINE }
  ```

  The package worked fine (rc=0), but a first-time user meets a warning
  before they meet the tool. `engines` now states only `{"node": ">=18"}`,
  which is the real runtime floor.

  The release-side requirement stays in `.github/workflows/publish.yml`, and
  the test that used to assert the wrong thing now asserts two
  **machine-checkable** facts instead of a string in a comment: that
  `publish.yml` still runs `npm stage publish` as an executable line, and
  that `.nvmrc` pins a node major that ships npm 11 (measured: node 22.23.2
  → npm 10.9.8, node 24.22.1 → npm 11.19.1). The old test required
  `engines` to carry the npm floor — it enforced the very thing that caused
  the warning.

## 1.3.2 (2026-10-28)

- **The patch no longer carries its own copy of the detector.**
  `core-gate.patch` embedded `agent/continue_hook.py` by hand while the file
  next to it was generated from the shared engine — and the two drifted. The
  copy in the patch was missing `strip_code_and_links` (so a reply ending in
  a code block counted as a question) and carried an older trigger list, which
  meant **patch mode and plugin mode ran different rules on the same
  machine**. The patch now carries only the core edits; the installer copies
  the generated module in. Three tests lock this down, one of them comparing
  the two modules' behaviour on real sentences rather than their bytes.

- **The release gate no longer needs the npm registry.** `scripts/check.sh`
  always said it ran "everything CI runs except the dsh typecheck, which needs
  the npm registry" — but `vitest run --coverage` picked up the dsh suite
  anyway, so `npm publish` was doing a network install in the middle of the
  publish. The gate is now genuinely offline (verified with the registry
  pointed at an unreachable address); CI still runs the dsh suite, in its own
  job.

- **One node version, one npm, everywhere.** The publish workflow ran
  `npm install -g npm@latest` while every other job ran the npm that ships
  with its node — so the publish job was the only one with a different npm,
  and npm 12 installs a different layout. "Latest" is not a version. The
  upgrade was never needed (node 24 ships npm 11.19.x, which satisfies both
  of npm's requirements). `.nvmrc` now pins the node version, the gate
  refuses to run on a different one, and four tests enforce it.

- **Fixed: the release could not be published at all.** The publish workflow
  never installed the Python toolchain, and `npm publish` runs the package's
  own `prepublishOnly` script — so the publish died on "ruff: command not
  found" *after* the tag was pushed.

- **Fixed: the version bump broke the release.** The version hook rewrote the
  plugin manifests with `JSON.stringify`, which puts every array element on
  its own line; prettier wants short arrays on one line. The bump therefore
  made `prettier --check` fail, and the release gate runs that check — so the
  release failed on its own version bump. The hook now formats what it
  writes.

- **Fixed: the dsh test leaked 75 MB per run.** 136 leftover directories had
  filled 5 GB of disk on a development machine; the test now removes its
  scratch directory.

- **New: git hooks.** `npm install` installs a `pre-commit` (the fast checks,
  on the staged files) and a `pre-push` (the whole gate), so a mismatch with
  CI shows up before the push instead of after it. Both refuse to run on the
  wrong node version.

- **The README now says how to verify the hook works** — one command that
  reproduces the whole chain, plus the measured numbers from the plugin's own
  event log (60 evaluations, 32 nudges, 53% hit rate).

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

