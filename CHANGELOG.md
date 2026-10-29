# Changelog

## 1.5.0 (2026-10-29)

**The `boundary` word list is deleted.** The user never asked for it
(「我从来没说过要加 boundary 词表」), and it was doing measurable harm.

### What it was

`exclusions.boundary` was a hand-written list of money / identity / publish /
delete words. `detect()` tested it against the whole 240-character tail, so
**any** reply whose tail happened to contain `¥1`, `publish`, `授权` or `上线`
was released without a nudge.

### Why it was wrong (measured, not argued)

Over **1430 real turn-final replies**:

| | before | after |
|---|---|---|
| released by `boundary` | **510 (36%)** | **0** |
| actually nudged | 33 | **43** (+10 recovered) |
| `user_must` releases | 9 | 24 (boundary no longer steals them) |

**407 of those 510 had no boundary word in the closing sentence at all** —
the word only appeared in the body. The semantic was "this closing sentence is
asking the user to authorise an irreversible action"; the implementation was
"a boundary word appears somewhere in the tail". Two different things.

The hook was, in practice, mostly switched off.

### The guard moved into the nudge text

Both templates already carried the boundary check as prose. The prose was
**conditional** ("needs something only the user has"), which the third review
round rejected: an agent holding DB credentials satisfies
"only the user has" as *false*, so it would go ahead and delete the table.

Both templates now carry an **unconditional list**, keyed on the action itself
rather than on who holds the credential:

> 1. BEFORE running anything, check the step you promised against this list:
>    - spending money / buying / transferring funds / paying anyone;
>    - real-name or identity verification, signing a contract;
>    - publishing, posting or shipping anything outward (npm, PyPI, GitHub
>      release, a message to a customer, a live post);
>    - deleting, overwriting or clearing data; deploying to a live/production
>      system; merging to main; rotating credentials or keys.
>    If the promised step is any of these, ask the user for that ONE thing in a
>    single line and stop — **even if you hold the credentials and even if you
>    were given permission earlier in this conversation. This check is about
>    the action itself, not about who owns the credential.**

So the detector now judges **shape** (is there a question / a promise / a
hand-off?) and the agent judges **meaning** (is this action irreversible?).
The agent has the context; a word list can only guess.

### Also fixed in this release

- `tests/test_detector.py`: the four "known missed" shapes that 1.4.1 pinned
  as released are now caught — `下一步我去检查生产库的索引大小。` and friends
  were released only because a global word list matched a word used as a
  **noun**（「生产库」「deploy」「付款」）. They now fire as `trigger:promise`.
- Two phrases without a first person (`I'll delete the stale snapshots now.`,
  `下一步我立刻付款。`) never matched the promise trigger to begin with; that
  is unchanged and recorded rather than papered over.

### Do not add the word list back

Adding a word only postpones the miss and costs a different one. Measured
today, four times in a row: `付费`, `清空`, `生产配置`, `推送目标` — each catch
bought one hit and cost one miss. Four further word-table designs were built
and rejected against real data (direction words, tense words, inquiry frames,
preceding-frame words); all failed for the same reason — **they were using a
regex to judge meaning**.

`tests/test_detector.py::TestBoundaryDirection::test_the_word_table_is_gone`
asserts the list stays gone.

## 1.4.1 (2026-10-29)

Documentation and dead-code cleanup after 1.4.0 shipped. No behaviour change
except two boundary words.

- The 1.4.0 notes described a `exclusions.hard_boundary` key and a "union"
  mechanism. **That key was deleted before release** — the notes and six code
  comments were describing something that is not there. Both fixed. The rule
  now is written down once: `boundary` + `user_must`, nothing else.
- The traffic figure in the 1.4.0 notes was measured over 2300 messages, which
  included compressed history — the same turn counted repeatedly. Re-measured
  over active turn-final replies only: **1445 messages, 1.3.5 = 31 (2.15%),
  1.4.0 = 32 (2.21%), +1, 0 lost.** That one extra is a **false positive**:
  「**待你拍板**：赞赏和付费永久卡在「原创 0/3」…还是放弃这两条？」— 1.3.5 let
  it through because of 「付费」, 1.4.0 reads it as `question_mark` and nudges.
  Cost: the agent asks once more. Accepted because the same word removal is what
  makes the feature's originating sentence fire at all.
- 「付款」 appeared twice in `boundary`; deduplicated.
- Tried adding `生产配置|线上配置` to `boundary`, then removed it: it also
  released 「下一步我去读一下**生产配置**的文档」, a real promise. Fourth time
  today that adding a word bought one catch and cost one miss — recorded instead
  of patched.

Still open, and deliberately not fixed here.

The fourth review round named eight irreversible phrasings that get nudged where
1.3.5 neither blocked nor nudged them (`push 到 main`, `改生产配置`,
`push the branch`, `go live` / `take it live` / `ship it live`). A 1.4.1
attempt to cover them with three new `boundary` rules was **reverted the same
day**: adding a word bought one catch and cost one miss, four times over today
(付费 / 清空 / 生产配置 / 推送目标). Those eight are 1.3.5 behaviour, not a
regression, and they are recorded here rather than patched.

One real false positive remains, from removing 「付费」 out of `boundary`:
「**待你拍板**：赞赏和付费永久卡在「原创 0/3」…还是放弃这两条？」reads as
`question_mark` where 1.3.5 let it through. One extra turn, capped at two.
Same removal is what makes the feature's originating sentence fire at all.

The general limit, stated once: this trigger decides from text alone, with
patterns. Adding a word to catch one shape reliably releases another. The
remaining shapes are left as they are.

339 tests pass.

## 1.4.0 (2026-10-29)

> **⚠ 本段有误，见 1.4.1。** 这里描述了一个 `exclusions.hard_boundary` 键和
> 「取并集」机制 —— **那个键在发布前就删掉了**，不存在。真实流量数字也不对：
> 本段写的 2291 / 110 / 114 是在**包含压缩历史**的消息上算的，同一回合被重复
> 计入；按活跃 turn-final 重算是 **1445 / 31 / 32 / +1**。「338 tests」实为 339。
> 原文保留不动，以 1.4.1 为准。


A fifth trigger class: **announced-but-undone**.

### The gap
Every existing trigger fires when the agent pushes a decision back to the user —
`offer` (要不要我…), `hand_off` (交给你定), `confirm_seek` (这样可以吗), or a
closing question mark. None fires when the agent says what it *will do next* and
then ends the turn. That reply asks for nothing and carries no question mark, so
the step it just described is silently dropped.

The incident that started this: asked what came next, the agent ended a reply
with 「下一步我去查 Replume 有没有定价页和真实客户证言」 and stopped. It never ran.

### `triggers.promise`
Matches a **first-person** announced next step, in the closing tail only.
Requiring a first person is the whole point — a bare `\bnext step\b` also fired
on "next step is **your call**" and "the next step **needs your** decision",
both of which hand the decision back to the user, the opposite of this trigger's
purpose. Punctuation and spacing variants (「下一步：我去…」「下一步，我会…」
「下一步I'll…」「稍后我去…」) and English forms (`Next I'll`, `Next up: I'll`,
`My next step is`) are covered.

### Where the boundary sits — and where it must not sit
`exclusions.hard_boundary` covers irreversible and outward-facing actions:
paying, deleting or overwriting, publishing, deploying, force-pushing, rotating
keys, real-name identity. Two separate mistakes were made and fixed here, both
caught by review rather than by the author:

**1. The boundary had become prose.** An earlier version let `promise` run ahead
of `boundary` and relied on the nudge text to say "ask first if you need
something only the user has". That is a *possession* test, not an
*irreversibility* test — an agent holding DB credentials and an npm token
satisfies it trivially. 「下一步我去付款开通」 and 「下一步我去把生产库的旧表删掉」
went from code-level releases in 1.3.5 to nudges. The boundary is back in code.

**2. Then it was too wide — and then in the wrong place.** Keying
`hard_boundary` on boundary *nouns* released seven genuine promises that merely
mentioned one (「下一步我去看一下**发布**日志」「下一步我去把**删除**逻辑改成软删」
「下一步我去把 **deploy** 脚本的 typo 修了」「下一步我去把**付款**按钮的颜色改成蓝色」).
It now keys on **position**: the boundary word must follow a modal or directional
verb (去/要/会/将/就/马上/立刻/现在/这就/直接), or form an explicit destructive
structure (把X删掉, drop table, force-push). English requires first-person future
("I'll deploy"), not a bare verb anywhere in the sentence.

At one point the check also sat at the top of `detect()` and short-circuited
**every** trigger rather than just `promise`. Replaying real turn-final replies:
that version went *below* 1.3.5 — it ate nudges that 1.3.5 had produced, purely
because the tail window contained 「发布链路」 or 「未发布」. It now only vetoes the
promise path; the rest of the chain (offer / hand_off / confirm_seek /
question_mark) runs unchanged.

### Wiring
`build_nudge(attempt, reason)` gives a promise its own text. An earlier pass
wired only the six shell adapters, leaving `hookctl.py`, `__init__.py` and the
patch-mode generator on the old one-argument signature — the Hermes plugin would
have sent the *offer* text ("your last reply ended by asking the user a
question") for a promise. `TestWiringCoverage` scans the source tree for
`build_nudge` calls that omit the reason.

`promise` is judged in exactly one place, on the code-stripped window.
Registering it in `_first_trigger_hit`'s tuple as well caused a second judgement
on the raw tail, which is how a fenced `# TODO:` block fired.

`DISPATCHED_TRIGGERS` is a **declaration, not a dispatch table** — the real
dispatch is the `_first_trigger_hit` tuple plus two branches inside `detect()`,
and adding a sixth key there does *not* make it fire. The test that used to
assert "the two hand-written tables are equal" was theatre: it stayed green
while a new key silently fell through to `no_trigger`. It is replaced by
`TestTriggerDispatchIsReal`, which runs one real sentence per configured key and
asserts it actually fires.

### Measured
False positives are measured on two corpora, because the author's own corpus was
written while fixing the bug and therefore flatters the fix.

**Real traffic** — 2291 turn-final replies replayed from the local session store:

| | nudges |
|---|---|
| 1.3.5 (`aa55822`) | 110 (4.80%) |
| this version | 114 (4.98%) |
| **delta** | **+4, 0 lost** |

All four new hits were read by hand and are genuine dropped promises — including
the incident sentence itself and one 「接下来我会…**我马上动，你不用回**」 that
then ended the turn.

**Recall on a 15-sentence variant corpus** (punctuation, word order, mixed
Chinese/English): **10/15 = 67%**. The five misses are deliberate:

- 「我待会再看一下」— the time word is followed by 再+verb with no 去/来/会.
  Covering it would also fire on 「我待会再说」, which is a real hand-off.
- 「我先去把数据拉下来」— 「把 X」 separates the verb.
- 「先去查一下日志」— no first person at all. Without one, a plan-shaped
  sentence in a report fires.
- 「Next: verify the numbers」— English variant.
- 「回头我去核对」— word-order variant not in the time-word alternation.

This is the honest limit of a pattern-based trigger: each miss covered adds a
false positive somewhere else. 67% recall with 0 false positives on a 21-item
negatives set is the chosen point. A semantic classifier would do better; a
word list cannot.

**Hard boundary coverage is measured in both directions**
(`TestHardBoundaryDirection`):

- 11 hard boundaries must be released — including `push to npm`,
  `npm publish`, `merge into main`, `rotate the production keys`,
  `付款开通`, `删库`, `deploy`, `实名`, and `需要你先把 API key 给我`
  (`user_must`).
- 5 promises that merely *mention* a boundary noun must still fire.
- 4 shapes are pinned as **known misses** — a promise whose boundary word
  appears as a modifier (「**生产库**的索引大小」「**deploy** 脚本的 typo」
  「**付款**按钮的颜色」「review the **deploy** logs」). These are released
  because fixing them means loosening the global `boundary` list, which affects
  every trigger class. Pinned, not hidden.

**The boundary is a union, not a replacement.** `promise` is vetoed by
`boundary` **or** `hard_boundary` **or** `user_must`. An earlier pass used only
`hard_boundary`, which is a strict subset — and 22 adversarial irreversible
promises then all got nudged, 14 of which 1.3.5 had released. A narrower net
replacing a wider one is a guardrail regression even when it looks tidier.

**Cost per false positive** is one extra agent turn, capped by
`max_nudges_per_turn: 2`. A reply matching both promise and hand_off nudges once.

### Note on an already-carried-out promise
Not word-listed. A first-person 「下一步我去查了，已经查完了」 still fires. The
nudge text offers the exit ("if you already carried that step out, say so in one
line"), and the trade is bounded: one extra turn, after which the chain closes
because the follow-up carries no promise.

338 tests pass.

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

