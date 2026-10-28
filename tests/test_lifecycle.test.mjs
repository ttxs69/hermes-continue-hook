import { expect, it } from "vitest";

/**
 * A `node:assert` look-alike on top of Vitest's `expect`.
 *
 * The test bodies are the valuable part — every message and edge case came from
 * a review round — so they stay verbatim, and the runner, reporting and
 * coverage come from Vitest.
 */
const assert = {
  ok(value, message) {
    expect(Boolean(value), message).toBe(true);
  },
  strictEqual(actual, expected, message) {
    expect(actual, message).toBe(expected);
  },
  notStrictEqual(actual, expected, message) {
    expect(actual, message).not.toBe(expected);
  },
  deepStrictEqual(actual, expected, message) {
    expect(actual, message).toEqual(expected);
  },
  match(value, pattern, message) {
    expect(value, message).toMatch(pattern);
  },
  doesNotMatch(value, pattern, message) {
    expect(value, message).not.toMatch(pattern);
  },
};

/**
 * Lifecycle invariants — the properties every earlier round of review broke.
 *
 *   node tests/test_lifecycle.mjs
 *
 * Each invariant is stated as a property over a matrix of states, not as a
 * scenario someone thought of once. The rounds that found P0s all found the
 * same shape of bug: a command that reported success while leaving the machine
 * in a state that fails later (a hook pointing at a deleted file, a config
 * truncated to {}, a user file deleted).
 *
 *   I1  After uninstall, no host config references the runtime directory
 *       unless that directory still exists.
 *   I2  uninstall never deletes a file that does not carry our marker.
 *   I3  A second install is a no-op: same entries, no new backups.
 *   I4  A host is never half-written: its config is either our hook plus the
 *       user's keys, or exactly what it was before.
 *   I5  The exit code is non-zero iff at least one host was left untouched.
 */
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CLI = path.join(REPO, "bin", "continue-hook.js");
const MARKER = "hermes-continue-hook: generated";

const freshHome = () => mkdtempSync(path.join(tmpdir(), "ch-life-"));

function cli(args, home) {
  const res = spawnSync(process.execPath, [CLI, ...args], {
    env: { ...process.env, HOME: home },
    encoding: "utf8",
  });
  return { code: res.status ?? 1, out: `${res.stdout || ""}${res.stderr || ""}` };
}

const readJsonFile = (file) => JSON.parse(readFileSync(file, "utf8"));
const walk = (dir) =>
  dir === "" || !existsSync(dir)
    ? []
    : readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
        e.isDirectory() ? walk(path.join(dir, e.name)).map((p) => path.join(e.name, p)) : [e.name],
      );

/** Every host config file this tool writes, relative to HOME. */
const CONFIGS = {
  "claude-code": ".claude/settings.json",
  codex: ".codex/hooks.json",
  qwen: ".qwen/settings.json",
  cursor: ".cursor/hooks.json",
  copilot: ".copilot/hooks/continue-hook.json",
  gemini: ".gemini/settings.json",
};

/** All text any host config holds, so we can look for a reference. */
function allConfigText(home) {
  return Object.values(CONFIGS)
    .map((rel) => {
      const abs = path.join(home, rel);
      return existsSync(abs) ? readFileSync(abs, "utf8") : "";
    })
    .join("\n");
}

const JSONC = '{\n  // my own settings\n  "model": "opus"\n}\n';
const WITH_USER_HOOKS = JSON.stringify({
  model: "opus",
  hooks: { Stop: [{ hooks: [{ type: "command", command: "my-own-hook.sh" }] }] },
});

// ── I1: no dangling reference after uninstall ───────────────────────────────

it("I1 uninstall never leaves a hook pointing at a deleted runtime (parseable config)", () => {
  const home = freshHome();
  assert.strictEqual(cli(["install", "--host", "claude-code,cursor"], home).code, 0);
  const res = cli(["uninstall", "--host", "all"], home);
  assert.strictEqual(res.code, 0, res.out);
  assert.ok(!allConfigText(home).includes("continue-hook/"), "a hook still references the runtime");
  assert.ok(!existsSync(path.join(home, ".hermes", "continue-hook")), "the runtime should be gone");
});

it("I1 uninstall keeps the runtime when a config cannot be read", () => {
  const home = freshHome();
  cli(["install", "--host", "claude-code"], home);
  const settings = path.join(home, ".claude", "settings.json");
  writeFileSync(settings, `// a comment the user added\n${readFileSync(settings, "utf8")}`);

  const res = cli(["uninstall", "--host", "all"], home);
  assert.strictEqual(res.code, 3, "an unreadable host must make the run non-zero");
  assert.ok(
    existsSync(path.join(home, ".hermes", "continue-hook", "hookctl.py")),
    "the runtime was deleted while a hook still pointed at it",
  );
  assert.match(res.out, /skipped/, "the host must be reported as skipped, not removed");
  assert.ok(!/Claude Code: removed/.test(res.out), "it must not claim the host was cleaned");
});

// ── I2: never delete a file without our marker ─────────────────────────────

it("I2 uninstall keeps a user file at our path (file host)", () => {
  const home = freshHome();
  const plugin = path.join(home, ".config/opencode/plugin/continue-hook.js");
  mkdirSync(path.dirname(plugin), { recursive: true });
  const mine = "// hand written, mentions continue-hook\n";
  writeFileSync(plugin, mine);
  cli(["uninstall", "--host", "opencode"], home);
  assert.strictEqual(readFileSync(plugin, "utf8"), mine);
});

it("I2 uninstall keeps a user skill directory", () => {
  const home = freshHome();
  const skill = path.join(home, ".claude/skills/continue-hook/SKILL.md");
  mkdirSync(path.dirname(skill), { recursive: true });
  writeFileSync(skill, "# my own skill\n");
  cli(["uninstall", "--host", "skills"], home);
  assert.strictEqual(readFileSync(skill, "utf8"), "# my own skill\n");
});

it("I2 uninstall keeps an unmarked dsh directory", () => {
  const home = freshHome();
  const pkg = path.join(home, ".hermes/continue-hook/dsh/package.json");
  mkdirSync(path.dirname(pkg), { recursive: true });
  writeFileSync(pkg, JSON.stringify({ name: "someone-elses-plugin" }));
  cli(["uninstall", "--host", "dsh"], home);
  assert.ok(existsSync(pkg), "an unmarked dsh directory was deleted");
});

it("I2 everything we install carries the marker", () => {
  const home = freshHome();
  cli(["install", "--host", "claude-code,opencode,pi,dsh,skills", "--dry-run"], home);
  cli(["install", "--host", "opencode,pi,dsh,skills"], home);
  const ours = [
    ".config/opencode/plugin/continue-hook.js",
    ".pi/agent/extensions/continue-hook.ts",
    ".hermes/continue-hook/dsh/src/index.ts",
    ".hermes/continue-hook/dsh/package.json",
    ".agents/skills/continue-hook/SKILL.md",
  ];
  for (const rel of ours) {
    const abs = path.join(home, rel);
    assert.ok(existsSync(abs), `${rel} was not installed`);
    assert.ok(readFileSync(abs, "utf8").includes(MARKER), `${rel} has no marker`);
  }
});

// ── I3: a second install changes nothing ───────────────────────────────────

it("I3 installing twice adds no entries and no backups", () => {
  const home = freshHome();
  cli(["install", "--host", "claude-code,cursor,copilot"], home);
  const first = JSON.stringify(readJsonFile(path.join(home, ".claude/settings.json")));
  const res = cli(["install", "--host", "claude-code,cursor,copilot"], home);
  assert.strictEqual(res.code, 0, res.out);
  assert.strictEqual(JSON.stringify(readJsonFile(path.join(home, ".claude/settings.json"))), first);
  const backups = walk(home).filter((f) => f.includes(".bak"));
  assert.deepStrictEqual(backups, [], `backups appeared: ${backups.join(", ")}`);
});

it("I3 installing twice over the skills layer adds no backups", () => {
  const home = freshHome();
  for (let i = 0; i < 3; i++) cli(["install", "--host", "skills,pi,dsh"], home);
  const backups = walk(home).filter((f) => f.includes(".bak"));
  assert.deepStrictEqual(backups, [], `backups appeared: ${backups.join(", ")}`);
});

// ── I4: no half-written host ───────────────────────────────────────────────

it("I4 an unparsable config is left byte-identical, with one backup", () => {
  const home = freshHome();
  mkdirSync(path.join(home, ".claude"), { recursive: true });
  writeFileSync(path.join(home, ".claude/settings.json"), JSONC);
  const res = cli(["install", "--host", "claude-code"], home);
  assert.strictEqual(res.code, 3, res.out);
  assert.strictEqual(readFileSync(path.join(home, ".claude/settings.json"), "utf8"), JSONC);
  const files = readdirSync(path.join(home, ".claude"));
  assert.deepStrictEqual(
    files.filter((f) => f.includes(".bak")),
    ["settings.json.bak"],
  );
});

it("I4 the user's own keys and hooks survive install", () => {
  const home = freshHome();
  mkdirSync(path.join(home, ".claude"), { recursive: true });
  writeFileSync(path.join(home, ".claude/settings.json"), WITH_USER_HOOKS);
  const res = cli(["install", "--host", "claude-code"], home);
  assert.strictEqual(res.code, 0, res.out);
  const after = readJsonFile(path.join(home, ".claude/settings.json"));
  assert.strictEqual(after.model, "opus");
  const commands = after.hooks.Stop.flatMap((g) =>
    (g.hooks || [g]).map((h) => h.command || h.bash),
  );
  assert.ok(commands.includes("my-own-hook.sh"), "the user's hook was dropped");
  const runtimeDir = path.join(home, ".hermes", "continue-hook");
  assert.strictEqual(
    commands.filter((c) => typeof c === "string" && c.includes(runtimeDir)).length,
    1,
    `our hook must appear exactly once, got: ${JSON.stringify(commands)}`,
  );
});

it("I4 a batch continues past a broken host", () => {
  const home = freshHome();
  for (const d of [".claude", ".codex", ".cursor"])
    mkdirSync(path.join(home, d), { recursive: true });
  writeFileSync(path.join(home, ".cursor/hooks.json"), JSONC);
  const res = cli(["install", "--host", "all"], home);
  assert.strictEqual(res.code, 3, "the skipped host must be signalled");
  assert.ok(
    existsSync(path.join(home, ".claude/settings.json")),
    "the hosts before it must be installed",
  );
  assert.ok(
    existsSync(path.join(home, ".codex/hooks.json")),
    "the hosts before it must be installed",
  );
  assert.strictEqual(readFileSync(path.join(home, ".cursor/hooks.json"), "utf8"), JSONC);
});

// ── I6: what we write is what the host will load ───────────────────────────

it("I6 every config we write satisfies its host's schema", () => {
  // The matrix proved the tool was self-consistent; it never asserted that the
  // file it wrote would be loaded. Cursor and Copilot both require a top-level
  // `version`, and a file without it is rejected whole.
  const home = freshHome();
  const res = cli(["install", "--host", "cursor,copilot,claude-code,gemini,codex,qwen"], home);
  assert.strictEqual(res.code, 0, res.out);

  for (const [host, rel] of Object.entries(CONFIGS)) {
    const cfg = readJsonFile(path.join(home, rel));
    assert.ok(cfg && typeof cfg === "object" && !Array.isArray(cfg), `${host}: not an object`);
    if (host === "cursor" || host === "copilot") {
      assert.strictEqual(cfg.version, 1, `${host}: a missing version makes the whole file invalid`);
    }
    const key = {
      "claude-code": "Stop",
      codex: "Stop",
      qwen: "Stop",
      cursor: "stop",
      copilot: "agentStop",
      gemini: "AfterAgent",
    }[host];
    assert.ok(Array.isArray(cfg.hooks[key]), `${host}: hooks.${key} must be an array`);
  }
});

it("I6 an unusable config shape is skipped, never overwritten", () => {
  const shapes = {
    "top-level array": "[]",
    "hooks is not an object": '{"hooks": []}',
    "hooks.Stop is a string": '{"hooks": {"Stop": "nope"}}',
  };
  for (const [label, content] of Object.entries(shapes)) {
    const home = freshHome();
    mkdirSync(path.join(home, ".claude"), { recursive: true });
    const file = path.join(home, ".claude/settings.json");
    writeFileSync(file, content);
    const res = cli(["install", "--host", "claude-code"], home);
    assert.strictEqual(res.code, 3, `${label}: must be reported, not reported as installed`);
    assert.strictEqual(readFileSync(file, "utf8"), content, `${label}: the file was modified`);
  }
});

it("I6 a single object where an array is expected keeps the user's hook", () => {
  const home = freshHome();
  mkdirSync(path.join(home, ".claude"), { recursive: true });
  writeFileSync(
    path.join(home, ".claude/settings.json"),
    JSON.stringify({ hooks: { Stop: { hooks: [{ type: "command", command: "teammate.sh" }] } } }),
  );
  const res = cli(["install", "--host", "claude-code"], home);
  assert.strictEqual(res.code, 0, res.out);
  const blob = readFileSync(path.join(home, ".claude/settings.json"), "utf8");
  assert.ok(blob.includes("teammate.sh"), "the user's hook was dropped");
  assert.ok(blob.includes(".hermes"), "our hook was not added");
});

// ── I5: exit code matches what happened ────────────────────────────────────

it("I5 a clean run exits 0 and a skipped host exits 3", () => {
  const clean = freshHome();
  assert.strictEqual(cli(["install", "--host", "claude-code"], clean).code, 0);
  assert.strictEqual(cli(["uninstall", "--host", "all"], clean).code, 0);

  const broken = freshHome();
  mkdirSync(path.join(broken, ".claude"), { recursive: true });
  writeFileSync(path.join(broken, ".claude/settings.json"), JSONC);
  assert.strictEqual(cli(["install", "--host", "claude-code"], broken).code, 3);
});
