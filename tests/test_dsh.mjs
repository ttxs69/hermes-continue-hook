/**
 * Runtime test for the DeepSeek Harness (dsh) plugin.
 *
 * This does not need a live harness: the plugin is compiled against the real
 * `@deepseek-ai/dsh-*` packages and then driven with a mock Cordis context
 * and a mock agent, so the real code path runs:
 *
 *   ctx.on('agent/turn-stopping') → agent.session.deriveMessages()
 *   → detector.py → agent.steer(createUserMessage(...))
 *
 * It needs network access to the npm registry (it installs dsh + tsc).
 *
 *   node tests/test_dsh.mjs
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, mkdirSync, cpSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PLUGIN_SRC = path.join(REPO, "adapters", "dsh", "src", "index.ts");

const WORK = mkdtempSync(path.join(tmpdir(), "ch-dsh-"));
const HOME = path.join(WORK, "home");
mkdirSync(path.join(HOME, ".hermes", "continue-hook"), { recursive: true });
// the plugin looks for the shared detector in the installed layout
cpSync(path.join(REPO, "detector.py"), path.join(HOME, ".hermes", "continue-hook", "detector.py"));
process.env.HOME = HOME;

console.log("installing dsh packages + typescript …");
execFileSync(
  "npm",
  [
    "i", "--no-save", "--no-audit", "--no-fund",
    "@deepseek-ai/dsh-agent", "@deepseek-ai/dsh-llm", "@deepseek-ai/dsh-session",
    "@deepseek-ai/cordis", "typescript", "@types/node",
  ],
  { cwd: WORK, stdio: "ignore" },
);

const SRC = path.join(WORK, "src");
mkdirSync(SRC, { recursive: true });
cpSync(PLUGIN_SRC, path.join(SRC, "index.ts"));
// The plugin shells out to the shared decision service — without it in the
// fixture the test passes while the plugin would do nothing in production.
cpSync(path.join(REPO, "hookctl.py"), path.join(WORK, "hookctl.py"));
cpSync(path.join(REPO, "detector.py"), path.join(WORK, "detector.py"));
writeFileSync(
  path.join(WORK, "package.json"),
  JSON.stringify({ name: "ch-dsh-test", type: "module", private: true }, null, 2),
);
writeFileSync(
  path.join(WORK, "tsconfig.json"),
  JSON.stringify(
    {
      compilerOptions: {
        module: "NodeNext",
        moduleResolution: "NodeNext",
        target: "ES2022",
        strict: true,
        skipLibCheck: true,
        rootDir: "src",
        outDir: "lib",
        types: ["node"],
      },
      include: ["src/index.ts"],
    },
    null,
    2,
  ),
);
console.log("compiling the plugin …");
execFileSync(path.join(WORK, "node_modules", ".bin", "tsc"), ["-p", "tsconfig.json"], {
  cwd: WORK,
  stdio: "inherit",
});

const mod = await import(path.join(WORK, "lib", "index.js"));
const { apply, lastAssistantText } = mod;

/** Build a mock Cordis context + agent and run one turn-stopping event. */
function runTurn(reply, { sessionId = "s1", config = {} } = {}) {
  writeFileSync(
    path.join(HOME, ".hermes", "continue_hook.json"),
    JSON.stringify({ cooldown_seconds: 0, max_nudges_per_turn: 2, ...config }),
  );
  const steered = [];
  let handler = null;
  const ctx = {
    on(event, fn) {
      if (event === "agent/turn-stopping") handler = fn;
    },
  };
  apply(ctx);
  if (!handler) throw new Error("plugin did not register agent/turn-stopping");
  const agent = {
    id: sessionId,
    session: {
      header: { id: sessionId },
      deriveMessages: () => [
        { role: "user", content: [{ type: "text", text: "go" }] },
        { role: "assistant", content: [{ type: "text", text: reply }] },
      ],
    },
    steer(message) {
      steered.push(message);
    },
  };
  handler({ agent, turn: 1, signal: new AbortController().signal });
  return steered;
}

let passed = 0;
let failed = 0;
async function test(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`  ok  ${name}`);
  } catch (err) {
    failed++;
    console.error(`FAIL  ${name}\n      ${err.message}`);
    process.exitCode = 1;
  }
}

await test("lastAssistantText reads the newest assistant message", () => {
  const text = lastAssistantText([
    { role: "user", content: [{ type: "text", text: "hi" }] },
    { role: "assistant", content: [{ type: "text", text: "older" }] },
    { role: "assistant", content: [{ type: "text", text: "newest" }] },
  ]);
  if (text !== "newest") throw new Error(`got ${JSON.stringify(text)}`);
});

await test("permission-seeking ending steers one continuation", () => {
  const steered = runTurn("要不要我顺便也把示例补上？");
  if (steered.length !== 1) throw new Error(`expected 1 steer, got ${steered.length}`);
  const text = steered[0].content[0].text;
  if (!text.includes("continue-hook")) throw new Error("nudge text missing");
  if (steered[0].role !== "user") throw new Error(`role is ${steered[0].role}`);
});

await test("declarative ending does not steer", () => {
  const steered = runTurn("已完成，验证通过。", { sessionId: "s2" });
  if (steered.length !== 0) throw new Error(`expected 0 steers, got ${steered.length}`);
});

await test("boundary question does not steer", () => {
  const steered = runTurn("这会花 $69，需要你批准后我才买。", { sessionId: "s3" });
  if (steered.length !== 0) throw new Error(`expected 0 steers, got ${steered.length}`);
});

await test("the per-turn cap is enforced", () => {
  const steered = runTurn("要不要我继续？", {
    sessionId: "s4",
    config: { max_nudges_per_turn: 1 },
  });
  if (steered.length !== 1) throw new Error(`expected 1 steer, got ${steered.length}`);
});

await test("dry_run never steers", () => {
  const steered = runTurn("要不要我继续？", {
    sessionId: "s5",
    config: { dry_run: true },
  });
  if (steered.length !== 0) throw new Error(`expected 0 steers, got ${steered.length}`);
});

await test("a broken detector fails open", () => {
  // The plugin calls hookctl.py, which imports the detector sitting next to
  // it — so that is the file to break (breaking the copy in HOME would not
  // be exercised at all).
  const detector = path.join(WORK, "detector.py");
  const backup = `${detector}.bak`;
  cpSync(detector, backup);
  writeFileSync(detector, "import sys\nsys.exit(3)\n");
  const steered = runTurn("要不要我继续？", { sessionId: "s6" });
  cpSync(backup, detector);
  if (steered.length !== 0) throw new Error(`expected 0 steers, got ${steered.length}`);
});

const total = passed + failed;
console.log(`\n${passed}/${total} dsh plugin test(s) passed`);
if (passed !== total) process.exitCode = 1;
if (!existsSync(path.join(WORK, "lib", "index.js"))) process.exitCode = 1;
