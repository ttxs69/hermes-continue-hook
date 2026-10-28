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
 * Installer tests — the Node CLI that npm users run.
 *
 *   node tests/test_installer.mjs
 *
 * The CLI is driven against a temporary HOME, so the developer's real agent
 * configuration is never touched.
 */
import { execFileSync, spawnSync } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  existsSync,
  readdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ALL_HOSTS = "claude-code,codex,qwen,cursor,copilot,gemini,opencode,pi,dsh,skills";
const CLI = path.join(REPO, "bin", "continue-hook.js");

function runCli(args, home) {
  // Both streams: warnings and refusals go to stderr, and a test that only
  // reads stdout on success cannot see them.
  const res = spawnSync(process.execPath, [CLI, ...args], {
    env: { ...process.env, HOME: home },
    encoding: "utf8",
  });
  return { code: res.status ?? 1, out: `${res.stdout || ""}${res.stderr || ""}` };
}

function freshHome() {
  const home = mkdtempSync(path.join(tmpdir(), "ch-inst-"));
  return home;
}

function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

it("install writes a Claude Code Stop hook", () => {
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

it("install is idempotent", () => {
  const home = freshHome();
  runCli(["install", "--host", "claude-code"], home);
  runCli(["install", "--host", "claude-code"], home);
  const settings = readJson(path.join(home, ".claude", "settings.json"));
  assert.strictEqual(settings.hooks.Stop.length, 1, "re-install must not duplicate");
});

it("install keeps the user's own hooks and settings", () => {
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

it("uninstall removes only our hook", () => {
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

it("install never deletes the user's own flat hooks", () => {
  // Cursor entries are {command}, Copilot entries are {type, bash} — neither
  // has a "hooks" array. A cleaner that assumed a "hooks" array dropped them,
  // which deleted the user's own hooks on install.
  const home = freshHome();
  mkdirSync(path.join(home, ".cursor"), { recursive: true });
  mkdirSync(path.join(home, ".copilot", "hooks"), { recursive: true });
  writeFileSync(
    path.join(home, ".cursor", "hooks.json"),
    JSON.stringify({
      version: 1,
      hooks: { stop: [{ command: "my-own-cursor-hook.sh" }, { command: "another.sh" }] },
    }),
  );
  writeFileSync(
    path.join(home, ".copilot", "hooks", "continue-hook.json"),
    JSON.stringify({
      version: 1,
      hooks: { agentStop: [{ type: "command", bash: "my-own-copilot.sh", timeoutSec: 5 }] },
    }),
  );
  runCli(["install", "--host", "cursor,copilot"], home);

  const cursor = readJson(path.join(home, ".cursor", "hooks.json"));
  const cursorBlob = JSON.stringify(cursor);
  assert.ok(cursorBlob.includes("my-own-cursor-hook.sh"), "cursor hook was deleted");
  assert.ok(cursorBlob.includes("another.sh"), "second cursor hook was deleted");
  assert.ok(cursorBlob.includes("continue-hook"), "our hook must be added");

  const copilot = readJson(path.join(home, ".copilot", "hooks", "continue-hook.json"));
  const copilotBlob = JSON.stringify(copilot);
  assert.ok(copilotBlob.includes("my-own-copilot.sh"), "copilot hook was deleted");
  assert.ok(copilotBlob.includes("continue-hook"), "our hook must be added");

  // and uninstall must remove only ours
  runCli(["uninstall", "--host", "cursor,copilot"], home);
  const after = JSON.stringify(readJson(path.join(home, ".cursor", "hooks.json")));
  assert.ok(!after.includes("continue-hook"), "our hook must be gone");
  assert.ok(after.includes("my-own-cursor-hook.sh"), "the user's hook must survive");
});

it("a user hook that merely shares our name is not ours", () => {
  // isOurs() used to match the substring "continue-hook", so a user's own
  // ~/bin/continue-hook-backup.sh was treated as our entry and deleted.
  const home = freshHome();
  mkdirSync(path.join(home, ".cursor"), { recursive: true });
  writeFileSync(
    path.join(home, ".cursor", "hooks.json"),
    JSON.stringify({
      version: 1,
      hooks: {
        stop: [{ command: "~/bin/continue-hook-backup.sh" }, { command: "my-linter --quiet" }],
      },
    }),
  );

  runCli(["install", "--host", "cursor"], home);
  const installed = JSON.stringify(readJson(path.join(home, ".cursor", "hooks.json")));
  assert.ok(installed.includes("continue-hook-backup.sh"), "install deleted the user's own script");
  assert.ok(installed.includes("my-linter --quiet"), "install deleted an unrelated hook");

  runCli(["uninstall", "--host", "cursor"], home);
  const after = JSON.stringify(readJson(path.join(home, ".cursor", "hooks.json")));
  assert.ok(after.includes("continue-hook-backup.sh"), "uninstall deleted the user's own script");
  assert.ok(after.includes("my-linter --quiet"), "uninstall deleted an unrelated hook");
  assert.ok(!after.includes(".hermes/continue-hook/"), "our hook must be gone");
});

it("uninstall removes a stale hook from a previous home directory", () => {
  // A user who moved their home directory still has an entry pointing at the
  // old runtime path. Uninstall has to take it with it, or two hooks fire.
  const home = freshHome();
  mkdirSync(path.join(home, ".cursor"), { recursive: true });
  writeFileSync(
    path.join(home, ".cursor", "hooks.json"),
    JSON.stringify({
      version: 1,
      hooks: { stop: [{ command: "python3 /old/home/.hermes/continue-hook/cursor/stop_hook.py" }] },
    }),
  );
  runCli(["uninstall", "--host", "cursor"], home);
  const after = JSON.stringify(readJson(path.join(home, ".cursor", "hooks.json")));
  assert.ok(!after.includes(".hermes/continue-hook/"), "the stale entry survived");
});

it("every JSON host gets its own config file", () => {
  // Explicitly, because "--host all" now means "the agents this machine has".
  const home = freshHome();
  const res = runCli(["install", "--host", ALL_HOSTS], home);
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

it("the skills layer lands in the skill directories", () => {
  const home = freshHome();
  runCli(["install", "--host", "skills"], home);
  for (const dir of [".claude/skills", ".cursor/skills", ".agents/skills"]) {
    const skill = path.join(home, dir, "continue-hook", "SKILL.md");
    assert.ok(existsSync(skill), `${skill} must exist`);
    assert.ok(readFileSync(skill, "utf8").includes("continue-hook"));
  }
});

it("--host all means the agents this machine actually has", () => {
  // It used to mean "all ten host types": a home with one agent installed got
  // six config files and nine SKILL.md files for agents that are not there.
  const home = freshHome();
  mkdirSync(path.join(home, ".claude"), { recursive: true });
  writeFileSync(path.join(home, ".claude", "settings.json"), JSON.stringify({ model: "opus" }));
  const res = runCli(["install", "--host", "all"], home);
  assert.strictEqual(res.code, 0, res.out);
  assert.ok(
    !existsSync(path.join(home, ".cursor", "hooks.json")),
    "a Cursor config was invented for a host that is not installed",
  );
  assert.ok(
    !existsSync(path.join(home, ".codex", "hooks.json")),
    "a Codex config was invented for a host that is not installed",
  );
  assert.ok(
    existsSync(path.join(home, ".claude", "settings.json")),
    "the detected host must be installed",
  );
  const created = readdirSync(home, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name);
  // The home starts empty, so nothing is "present" and only the standard
  // location may be written. Asserting on a hand-picked list of six hosts was
  // a false green: those were never the ones that got created.
  // Only the standard location and the one host that is actually present.
  const skillDirs = created
    .filter((d) => existsSync(path.join(home, d, "skills", "continue-hook", "SKILL.md")))
    .sort();
  assert.deepStrictEqual(
    skillDirs,
    [".agents", ".claude"],
    `skills went to: ${skillDirs.join(", ")}`,
  );
  const invented = created.filter(
    (d) => d.startsWith(".") && ![".agents", ".claude", ".hermes"].includes(d),
  );
  assert.deepStrictEqual(invented, [], `host directories were invented: ${invented.join(", ")}`);
});

it("uninstall removes the runtime so nothing executable is left behind", () => {
  const home = freshHome();
  runCli(["install", "--host", "claude-code"], home);
  const runtime = path.join(home, ".hermes", "continue-hook");
  assert.ok(existsSync(runtime), "install should create the runtime");
  runCli(["uninstall", "--host", "claude-code"], home);
  assert.ok(!existsSync(runtime), "uninstall left executable Python behind");
});

it("--host all on a machine with no agent says so instead of guessing", () => {
  const home = freshHome();
  const res = runCli(["install", "--host", "all"], home);
  assert.notStrictEqual(res.code, 0, "should fail loudly");
  assert.match(res.out, /no supported agent found/);
  const created = readdirSync(home, { withFileTypes: true }).filter((e) => e.isDirectory());
  assert.deepStrictEqual(
    created.map((e) => e.name),
    [],
    "nothing may be created",
  );
});

it("uninstalling one host keeps the runtime other hosts are using", () => {
  // installedHosts() skipped file/dsh/skills hosts, so removing Claude Code
  // deleted ~/.hermes/continue-hook/ — and with it the hookctl.py that an
  // installed Pi or dsh extension locates there. Those hosts then failed
  // silently forever.
  const home = freshHome();
  runCli(["install", "--host", "dsh,claude-code"], home);
  runCli(["uninstall", "--host", "claude-code"], home);
  assert.ok(
    existsSync(path.join(home, ".hermes", "continue-hook", "hookctl.py")),
    "the shared runtime was deleted while dsh still needed it",
  );
  assert.ok(existsSync(path.join(home, ".hermes", "continue-hook", "dsh", "src", "index.ts")));

  const home2 = freshHome();
  runCli(["install", "--host", "pi,opencode"], home2);
  runCli(["uninstall", "--host", "opencode"], home2);
  assert.ok(
    existsSync(path.join(home2, ".hermes", "continue-hook", "hookctl.py")),
    "the Pi extension would never find hookctl again",
  );
});

it("an unparsable host config is backed up, never overwritten", () => {
  // JSON.parse failure was swallowed into {}, and the next write replaced the
  // user's settings.json — including a JSONC file with comments, which many
  // hosts accept and JSON.parse does not.
  const home = freshHome();
  mkdirSync(path.join(home, ".claude"), { recursive: true });
  const original = '{\n  // my settings\n  "model": "opus"\n}\n';
  writeFileSync(path.join(home, ".claude", "settings.json"), original);
  const res = runCli(["install", "--host", "claude-code"], home);
  assert.strictEqual(res.code, 3, "install must refuse instead of overwriting");
  assert.strictEqual(
    readFileSync(path.join(home, ".claude", "settings.json"), "utf8"),
    original,
    "the original file was modified",
  );
  const backups = readdirSync(path.join(home, ".claude")).filter((f) => f.includes(".bak"));
  assert.strictEqual(backups.length, 1, "the original must be backed up");
  assert.strictEqual(
    backups[0],
    "settings.json.bak",
    "one stable name; a timestamped one accumulated",
  );
});

it("the hook command survives a home directory with a space", () => {
  const base = freshHome();
  const home = path.join(base, "John Smith");
  mkdirSync(home, { recursive: true });
  runCli(["install", "--host", "claude-code"], home);
  const cmd = readJson(path.join(home, ".claude", "settings.json")).hooks.Stop[0].hooks[0].command;
  assert.ok(cmd.includes('"'), `the path must be quoted: ${cmd}`);
  // and it really runs: an unquoted path exits 2 here, which hosts read as "block"
  const out = execFileSync(
    "sh",
    ["-c", `echo '{"session_id":"s","last_assistant_message":"要不要我顺便补个测试？"}' | ${cmd}`],
    { env: { ...process.env, HOME: home }, encoding: "utf8" },
  );
  assert.ok(out.includes("decision"), "the installed hook did not run");
});

it("check is read-only: it never writes a backup or fails on JSONC", () => {
  const home = freshHome();
  mkdirSync(path.join(home, ".claude"), { recursive: true });
  writeFileSync(
    path.join(home, ".claude", "settings.json"),
    '{\n  // mine\n  "model": "opus"\n}\n',
  );
  const res = runCli(["check", "--host", "claude-code"], home);
  const files = readdirSync(path.join(home, ".claude"));
  assert.deepStrictEqual(files, ["settings.json"], "check wrote something");
  assert.notStrictEqual(res.code, 3, "check must not abort on an unparsable file");
});

it("uninstall on a clean machine creates nothing", () => {
  const home = freshHome();
  const res = runCli(["uninstall", "--host", "all"], home);
  const created = readdirSync(home, { withFileTypes: true }).map((e) => e.name);
  assert.deepStrictEqual(created, [], `uninstall created: ${created.join(", ")}`);
  assert.strictEqual(res.code, 0, res.out);
});

it("a UTF-8 BOM does not make the config uninstallable", () => {
  // Notepad and several Windows tools write a BOM; JSON.parse rejects it and
  // install then failed forever with a message blaming a trailing comma.
  const home = freshHome();
  mkdirSync(path.join(home, ".claude"), { recursive: true });
  writeFileSync(path.join(home, ".claude", "settings.json"), '\ufeff{"model": "opus"}\n');
  const res = runCli(["install", "--host", "claude-code"], home);
  assert.strictEqual(res.code, 0, res.out);
  assert.strictEqual(
    JSON.parse(readFileSync(path.join(home, ".claude", "settings.json"), "utf8")).model,
    "opus",
  );
});

it("a host is detected by its own directory, not by our file", () => {
  // Detection used to look for the artefact we install, which cannot exist
  // before the first install — so "--host all" refused on a machine that had
  // the host but not our hook.
  const home = freshHome();
  mkdirSync(path.join(home, ".claude"), { recursive: true });
  const res = runCli(["install", "--host", "all"], home);
  assert.strictEqual(res.code, 0, res.out);
  assert.ok(existsSync(path.join(home, ".claude", "settings.json")));
  assert.ok(!existsSync(path.join(home, ".cursor")), "a cursor directory was invented");
});

it("a user's own extension is backed up, never silently replaced or deleted", () => {
  const home = freshHome();
  const ext = path.join(home, ".pi", "agent", "extensions", "continue-hook.ts");
  mkdirSync(path.dirname(ext), { recursive: true });
  const mine = "// my own extension\nexport default function mine(pi) {}\n";
  writeFileSync(ext, mine);

  const before = runCli(["check", "--host", "pi"], home);
  assert.match(before.out, /✗/, "a user file must not be reported as our install");

  runCli(["install", "--host", "pi"], home);
  const backups = readdirSync(path.dirname(ext)).filter((f) => f.includes(".bak-"));
  assert.strictEqual(backups.length, 1, "the user's file was replaced without a backup");

  runCli(["uninstall", "--host", "pi"], home);
  assert.ok(backups.length >= 1);
});

it("uninstall leaves a user file it did not write", () => {
  const home = freshHome();
  const ext = path.join(home, ".pi", "agent", "extensions", "continue-hook.ts");
  mkdirSync(path.dirname(ext), { recursive: true });
  writeFileSync(ext, "// hand written by the user\n");
  const res = runCli(["uninstall", "--host", "pi"], home);
  assert.strictEqual(res.code, 0, res.out);
  assert.ok(existsSync(ext), "uninstall deleted a file it did not write");
});

it("--dry-run writes nothing at all, not even a backup", () => {
  const home = freshHome();
  mkdirSync(path.join(home, ".claude"), { recursive: true });
  writeFileSync(path.join(home, ".claude", "settings.json"), "{\n  // mine\n}\n");
  const res = runCli(["install", "--host", "claude-code", "--dry-run"], home);
  assert.notStrictEqual(res.code, 0, "a run that would fail should fail in dry-run too");
  assert.deepStrictEqual(readdirSync(path.join(home, ".claude")), ["settings.json"]);
});

it("uninstall never truncates a config it cannot parse", () => {
  // It used readJson(..., allowBroken) here, which returned {} and then wrote
  // it — turning a JSONC settings.json into a single "{}".
  const home = freshHome();
  // Install first (so the runtime exists and a hook points at it), then break
  // the file the way a user would — by adding a comment.
  runCli(["install", "--host", "claude-code"], home);
  const settings = path.join(home, ".claude", "settings.json");
  const withHook = readFileSync(settings, "utf8");
  const original = `// my model\n${withHook}`;
  writeFileSync(settings, original);
  const res = runCli(["uninstall", "--host", "all"], home);
  assert.strictEqual(res.code, 3, "an untouched host must make the run non-zero");
  assert.strictEqual(readFileSync(settings, "utf8"), original, "the user's config was truncated");
  assert.match(res.out, /not valid JSON/, "the user must be told why it was skipped");
  assert.match(res.out, /skipped/, "the host must not be reported as removed");
  // and the runtime must survive: the hook in that file still points at it
  assert.ok(
    existsSync(path.join(home, ".hermes", "continue-hook", "hookctl.py")),
    "the runtime was deleted while a hook still referenced it",
  );
});

it("uninstall leaves a user file whose text mentions continue-hook", () => {
  // isOurFile() matched the substring "continue-hook", so a hand-written
  // plugin that merely mentions the name in a comment was deleted.
  const home = freshHome();
  const plugin = path.join(home, ".config", "opencode", "plugin", "continue-hook.js");
  mkdirSync(path.dirname(plugin), { recursive: true });
  const mine = "// my own continue-hook replacement, hand written\nexport const x = 1\n";
  writeFileSync(plugin, mine);
  const before = runCli(["check", "--host", "opencode"], home);
  assert.match(before.out, /✗/, "a user file must not be reported as ours");
  runCli(["uninstall", "--host", "opencode"], home);
  assert.strictEqual(readFileSync(plugin, "utf8"), mine, "uninstall deleted the user's file");
});

it("every installed host fires through the command written into its config", () => {
  // The point of this test: install, then run exactly what the host would run,
  // with that host's real payload shape. Calling the adapter by hand cannot
  // catch a wrong command, a missing transport module, or a renamed field.
  const home = freshHome();
  const transcript = path.join(home, "transcript.jsonl");
  writeFileSync(
    transcript,
    JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "text", text: "要不要我顺便补个测试？" }] },
    }) + "\n",
  );

  runCli(["install", "--host", "claude-code,codex,qwen,cursor,copilot,gemini"], home);

  const cases = [
    {
      host: "claude-code",
      file: ".claude/settings.json",
      key: "Stop",
      payload: {
        session_id: "c1",
        stop_hook_active: false,
        last_assistant_message: "要不要我顺便补个测试？",
      },
      expect: "block",
    },
    {
      host: "codex",
      file: ".codex/hooks.json",
      key: "Stop",
      payload: {
        session_id: "x1",
        turn_id: "t1",
        stop_hook_active: false,
        last_assistant_message: "要不要我顺便补个测试？",
      },
      expect: "block",
    },
    {
      host: "qwen",
      file: ".qwen/settings.json",
      key: "Stop",
      payload: { session_id: "q1", last_assistant_message: "要不要我顺便补个测试？" },
      expect: "block",
    },
    {
      host: "cursor",
      file: ".cursor/hooks.json",
      key: "stop",
      payload: {
        status: "completed",
        loop_count: 0,
        conversation_id: "cu1",
        transcript_path: transcript,
      },
      expect: "followup_message",
    },
    {
      host: "copilot",
      file: ".copilot/hooks/continue-hook.json",
      key: "agentStop",
      payload: { sessionId: "cp1", transcriptPath: transcript, stopReason: "end_turn" },
      expect: "block",
    },
    {
      host: "gemini",
      file: ".gemini/settings.json",
      key: "AfterAgent",
      payload: {
        prompt: "go",
        prompt_response: "要不要我顺便补个测试？",
        stop_hook_active: false,
        session_id: "g1",
        hook_event_name: "AfterAgent",
        timestamp: 1,
      },
      expect: "deny",
    },
  ];

  for (const c of cases) {
    const group = readJson(path.join(home, c.file)).hooks[c.key][0];
    const cmd = group.hooks ? group.hooks[0].command : group.command || group.bash;
    assert.ok(cmd, `${c.host}: no command was written`);
    const out = execFileSync("sh", ["-c", cmd], {
      input: JSON.stringify(c.payload),
      env: { ...process.env, HOME: home },
      encoding: "utf8",
    });
    assert.ok(out.includes(c.expect), `${c.host} did not emit ${c.expect}: ${out.slice(0, 120)}`);
    assert.ok(out.includes("continue-hook"), `${c.host}: wrong nudge payload`);
  }
});

it("a skill the user wrote is not deleted by uninstall", () => {
  const home = freshHome();
  const skill = path.join(home, ".claude", "skills", "continue-hook", "SKILL.md");
  mkdirSync(path.dirname(skill), { recursive: true });
  const mine = "# my own skill\n";
  writeFileSync(skill, mine);
  const before = runCli(["check", "--host", "skills"], home);
  assert.match(before.out, /✗/, "a user skill must not be reported as ours");
  const res = runCli(["uninstall", "--host", "skills"], home);
  assert.strictEqual(res.code, 0, res.out);
  assert.strictEqual(readFileSync(skill, "utf8"), mine, "uninstall deleted the user's own skill");
});

it("repeated installs do not pile up backups", () => {
  const home = freshHome();
  for (let i = 0; i < 3; i++) runCli(["install", "--host", "skills,pi,dsh"], home);
  const found = execFileSync("sh", ["-c", `find ${JSON.stringify(home)} -name '*.bak*' | wc -l`], {
    encoding: "utf8",
  }).trim();
  assert.strictEqual(found, "0", `backups accumulated: ${found}`);
});

it("check reports what is installed", () => {
  const home = freshHome();
  runCli(["install", "--host", "claude-code"], home);
  const res = runCli(["check", "--host", "claude-code"], home);
  assert.strictEqual(res.code, 0, res.out);
  assert.ok(res.out.includes("Claude Code"));
});

it("dry-run writes nothing", () => {
  const home = freshHome();
  const res = runCli(["install", "--host", "claude-code", "--dry-run"], home);
  assert.strictEqual(res.code, 0, res.out);
  assert.ok(!existsSync(path.join(home, ".claude", "settings.json")));
});

it("an unknown host is rejected", () => {
  const home = freshHome();
  const res = runCli(["install", "--host", "not-a-host"], home);
  assert.strictEqual(res.code, 2);
  assert.ok(res.out.includes("unknown host"));
});

it("an installed shell hook actually runs and blocks", () => {
  // The missing-transport-module bug: the installed hook imported a module
  // the installer never copied, so it failed open and did nothing — silently.
  const home = freshHome();
  runCli(["install", "--host", "claude-code"], home);
  const hook = path.join(home, ".hermes", "continue-hook", "claude-code", "stop_hook.py");
  assert.ok(existsSync(hook), "the hook must be installed");
  const out = execFileSync("python3", [hook], {
    env: { ...process.env, HOME: home },
    encoding: "utf8",
    input: JSON.stringify({
      session_id: "e2e",
      stop_hook_active: false,
      last_assistant_message: "要不要我顺便也把示例补上？",
    }),
  });
  const decision = JSON.parse(out.trim());
  assert.strictEqual(decision.decision, "block");
  assert.ok(decision.reason.includes("continue-hook"));
});

it("an installed shell hook stays silent on a clean reply", () => {
  const home = freshHome();
  runCli(["install", "--host", "claude-code"], home);
  const hook = path.join(home, ".hermes", "continue-hook", "claude-code", "stop_hook.py");
  const out = execFileSync("python3", [hook], {
    env: { ...process.env, HOME: home },
    encoding: "utf8",
    input: JSON.stringify({
      session_id: "e2e2",
      stop_hook_active: false,
      last_assistant_message: "已完成，42 项测试全绿。",
    }),
  });
  assert.strictEqual(out.trim(), "");
});

it("the installed runtime can answer a decision", () => {
  const home = freshHome();
  runCli(["install", "--host", "claude-code"], home);
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
