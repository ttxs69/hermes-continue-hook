# Contributing

## Setting up

Node **22.12+** for the JavaScript toolchain (vitest 5, eslint 10, typescript 6).
The published package itself still runs on Node 18 — CI proves that separately
with a job that installs and uninstalls using nothing but `node`.

```bash
npm install     # dev tooling only; the shipped package has no runtime deps
```

## Before you push

```bash
./scripts/check.sh      # ruff, mypy, pytest, eslint, prettier, tsc ×2, vitest, npm pack
```

It is the same set of checks CI runs, minus the DeepSeek Harness typecheck
(which needs the npm registry):

```bash
bash adapters/dsh/typecheck.sh          # compiles the dsh plugin against @deepseek-ai/dsh-*
npx vitest run tests/test_dsh.test.mjs  # runs it against a mock harness
```

## The rules that matter

- **One decision service.** Caps, state and the event log live in
  `hookctl.py`. A host adapter may not re-implement them — a cap that means
  two different things in two hosts is a bug. `detector.py` stays pure
  pattern matching: it answers "does this reply look like it is asking
  permission?", never "should we nudge now?".
- **The agent decides.** Detection only decides *whether to consider nudging*.
  Whether the caught question was legitimate stays with the agent, inside the
  nudge. See `docs/ARCHITECTURE.md`.
- **Fail open.** Any error means "no nudge". A broken hook must never wedge
  somebody's agent session.
- **One copy of an adapter.** Each adapter implementation lives in exactly
  one place; `tests/test_version_consistency.py` fails the build if a second
  copy appears (a stale copy of the OpenCode plugin once shipped in the repo).
- **New trigger/exclusion patterns:** add a test case with the *exact*
  sentence that motivated it. A real-world quote beats an invented example.
- **Version in four places** (`package.json`, `pyproject.toml`, `plugin.yaml`,
  `__init__.py`) — the version-consistency test keeps them in step.
- **Python tests** use the standard library `unittest` only, no third-party
  test dependencies, and must pass in BOTH runners — `python tests/test_x.py`
  and `python -m unittest discover -s tests` — and under an empty `HOME`.
- **JavaScript tests** are Vitest (`tests/*.test.mjs`). The bodies keep their
  `assert.*` style through a small shim at the top of each file, so a test
  written for the old hand-rolled runner moves over by renaming only.
- **One tool per job.** Lint is eslint, format is prettier, types are `tsc`
  (`tsconfig.json` for TS, `tsconfig.checkjs.json` for the JS), tests are
  vitest. Adding a second tool for a job already covered is not an improvement.

## Adding a host

1. Add the adapter under `adapters/<host>/` and its example config.
2. If the host can block a turn end, add it to `HOOKS` in
   `bin/continue-hook.js` with the right `kind`, and add a test that drives
   the adapter with that host's real payload shape.
3. Add the host to the support matrix in `README.md`, with the source of the
   contract you relied on (a doc URL beats a description).
4. If the host has no turn-end hook, add it to the behaviour tier — the
   `skills/continue-hook/SKILL.md` layer already covers it.
