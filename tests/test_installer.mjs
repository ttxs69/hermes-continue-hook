/**
 * Installer tests — the Node CLI that npm users run.
 *
 *   node tests/test_installer.mjs
 *
 * The CLI is driven against a temporary HOME, so the developer's real agent
 * configuration is never touched.
 */
import assert from "node:assert";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CLI = path.join(REPO, "bin", "continue-hook.js");

function runCli(args, home) {
  try {
    return {
      code: 0,
      out: execFileSync(process.execPath, [CLI, ...args], {
        env: { ...process.env, HOME: home },
        encoding: "utf8",
      }),
    };
  } catch (err) {
    return { code: err.status ?? 1, out: `${err.stdout || ""}${err.stderr || ""}` };
  }
}

function freshHome() {
  const home = mkdtempSync(path.join(tmpdir(), "ch-inst-"));
  return home;
}

function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

let passed = 0;
async function test(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`  ok  ${name}`);
  } catch (err) {
    console.error(`FAIL  ${name}\n      ${err.message}`);
    process.exitCode = 1;
  }
}

await test("install writes a Claude Code Stop hook", () => {
  const home = freshHome();
  const res = runCli(["install", "--host", "claude-code"], home);
  assert.strictEqual(res.code, 0, res.out);
  const settings = readJson(path.join(home, ".claude", "settings.json"));
  const stop = settings.hooks.Stop;
  assert.ok(Array.isArray(stop) && stop.length === 1, "one Stop group expected");
  const cmd = stop[0].hooks[0].command;
  assert.ok(cmd.includes("continue-hook"), `command should reference the tool: ${cmd}`);
  assert.ok(cmd.includes("claude-code/stop_hook.py"), cmd);
});

await test("install is idempotent", () => {
  const home = freshHome();
  runCli(["install", "--host", "claude-code"], home);
  runCli(["install", "--host", "claude-code"], home);
  const settings = readJson(path.join(home, ".claude", "settings.json"));
  assert.strictEqual(settings.hooks.Stop.length, 1, "re-install must not duplicate");
});

await test("install keeps the user's own hooks and settings", () => {
  const home = freshHome();
  mkdirSync(path.join(home, ".claude"), { recursive: true });
  writeFileSync(
    path.join(home, ".claude", "settings.json"),
    JSON.stringify({
      model: "opus",
      hooks: { Stop: [{ hooks: [{ type: "command", command: "my-own-hook.sh" }] }] },
    }),
  );
  runCli(["install", "--host", "claude-code"], home);
  const settings = readJson(path.join(home, ".claude", "settings.json"));
  assert.strictEqual(settings.model, "opus");
  const blob = JSON.stringify(settings);
  assert.ok(blob.includes("my-own-hook.sh"), "the user's own hook must survive");
  assert.ok(blob.includes("continue-hook"), "our hook must be added");
});

await test("uninstall removes only our hook", () => {
  const home = freshHome();
  mkdirSync(path.join(home, ".claude"), { recursive: true });
  writeFileSync(
    path.join(home, ".claude", "settings.json"),
    JSON.stringify({
      model: "opus",
      hooks: { Stop: [{ hooks: [{ type: "command", command: "my-own-hook.sh" }] }] },
    }),
  );
  runCli(["install", "--host", "claude-code"], home);
  runCli(["uninstall", "--host", "claude-code"], home);
  const settings = readJson(path.join(home, ".claude", "settings.json"));
  const blob = JSON.stringify(settings);
  assert.ok(!blob.includes("continue-hook"), "our hook must be gone");
  assert.ok(blob.includes("my-own-hook.sh"), "the user's own hook must survive");
  assert.strictEqual(settings.model, "opus");
});

await test("every JSON host gets its own config file", () => {
  const home = freshHome();
  const res = runCli(["install", "--host", "all"], home);
  assert.strictEqual(res.code, 0, res.out);
  for (const file of [
    ".claude/settings.json",
    ".codex/hooks.json",
    ".qwen/settings.json",
    ".cursor/hooks.json",
    ".copilot/hooks/continue-hook.json",
    ".gemini/settings.json",
  ]) {
    assert.ok(existsSync(path.join(home, file)), `${file} must exist`);
  }
  assert.ok(existsSync(path.join(home, ".config", "opencode", "plugin", "continue-hook.js")));
  assert.ok(existsSync(path.join(home, ".pi", "agent", "extensions", "continue-hook.ts")));
  assert.ok(existsSync(path.join(home, ".hermes", "continue-hook", "hookctl.py")));
  assert.ok(existsSync(path.join(home, ".hermes", "continue-hook", "detector.py")));
});

await test("the skills layer lands in the skill directories", () => {
  const home = freshHome();
  runCli(["install", "--host", "skills"], home);
  for (const dir of [".claude/skills", ".cursor/skills", ".agents/skills"]) {
    const skill = path.join(home, dir, "continue-hook", "SKILL.md");
    assert.ok(existsSync(skill), `${skill} must exist`);
    assert.ok(readFileSync(skill, "utf8").includes("continue-hook"));
  }
});

await test("check reports what is installed", () => {
  const home = freshHome();
  runCli(["install", "--host", "claude-code"], home);
  const res = runCli(["check", "--host", "claude-code"], home);
  assert.strictEqual(res.code, 0, res.out);
  assert.ok(res.out.includes("Claude Code"));
});

await test("dry-run writes nothing", () => {
  const home = freshHome();
  const res = runCli(["install", "--host", "claude-code", "--dry-run"], home);
  assert.strictEqual(res.code, 0, res.out);
  assert.ok(!existsSync(path.join(home, ".claude", "settings.json")));
});

await test("an unknown host is rejected", () => {
  const home = freshHome();
  const res = runCli(["install", "--host", "not-a-host"], home);
  assert.strictEqual(res.code, 2);
  assert.ok(res.out.includes("unknown host"));
});

await test("the installed runtime can answer a decision", () => {
  const home = freshHome();
  runCli(["install", "--host", "all"], home);
  const out = execFileSync(
    "python3",
    [
      path.join(home, ".hermes", "continue-hook", "hookctl.py"),
      "--text",
      "要不要我顺便也把示例补上？",
      "--session",
      "t1",
      "--host",
      "test",
    ],
    { env: { ...process.env, HOME: home }, encoding: "utf8" },
  );
  const decision = JSON.parse(out.trim());
  assert.strictEqual(decision.should_nudge, true);
  assert.ok(decision.reason.startsWith("trigger:"));
});

console.log(`\n${passed} installer test(s) passed`);
