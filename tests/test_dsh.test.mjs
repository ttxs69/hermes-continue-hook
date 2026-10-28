import { it, afterAll } from "vitest";

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
import {
  mkdtempSync,
  writeFileSync,
  mkdirSync,
  cpSync,
  existsSync,
  readdirSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PLUGIN_SRC = path.join(REPO, "adapters", "dsh", "src", "index.ts");

const WORK = mkdtempSync(path.join(tmpdir(), "ch-dsh-"));
// The scratch directory holds a node_modules with the real dsh packages —
// about 75 MB. Without this the test leaves one behind on every run, and
// they pile up: 136 of them filled 5 GB of the disk before anyone noticed.
//
// `process.on("exit")` does not fire in a vitest worker, so the cleanup is
// registered as an `afterAll` hook below instead.
function cleanupScratch() {
  try {
    rmSync(WORK, { recursive: true, force: true });
  } catch {
    // best effort — a leftover directory must never fail the test
  }
}
const HOME = path.join(WORK, "home");
afterAll(cleanupScratch);
mkdirSync(path.join(HOME, ".hermes", "continue-hook"), { recursive: true });
// the plugin looks for the shared detector in the installed layout
cpSync(path.join(REPO, "detector.py"), path.join(HOME, ".hermes", "continue-hook", "detector.py"));
process.env.HOME = HOME;

console.log("installing dsh packages + typescript …");
// The package.json has to exist BEFORE the install. `npm i --no-save` in a
// directory that has no package.json fails on npm 12 ("npm error code 1",
// nothing installed) — and the test then dies on a missing
// node_modules/.bin/tsc, which reads like a missing dependency rather than a
// wrong order of operations. Writing package.json first also stops npm from
// walking up to a parent directory that has one and installing *there*.
writeFileSync(
  path.join(WORK, "package.json"),
  JSON.stringify(
    {
      name: "ch-dsh-test",
      type: "module",
      private: true,
      // The dependencies live in the manifest rather than on the npm command
      // line. `npm i --no-save <pkgs>` behaves differently from npm version to
      // npm version — the release workflow installs the newest npm before
      // publishing, and that combination installed nothing and left
      // node_modules/.bin/tsc missing. A manifest plus a plain `npm install`
      // is the boring path every npm version agrees on.
      dependencies: {
        "@deepseek-ai/dsh-agent": "*",
        "@deepseek-ai/dsh-llm": "*",
        "@deepseek-ai/dsh-session": "*",
        "@deepseek-ai/cordis": "*",
        // typescript is pinned to the 5.x line on purpose. TypeScript 7 ships
        // as a native binary (`@typescript/typescript-linux-x64`), and npm
        // does not create a `node_modules/.bin/tsc` shim for it — so the
        // compile step below failed with a bare ENOENT while the install
        // itself had succeeded. A test that installs "latest" is a test that
        // breaks when the ecosystem moves.
        typescript: "~5.9.3",
        "@types/node": "*",
      },
    },
    null,
    2,
  ),
);
// Use the npm that is next to the node running this test, not whatever `npm`
// happens to be first on PATH. The release workflow upgrades the global npm
// mid-job, so the two can differ — and then the install lands somewhere the
// test does not look.
const NPM = process.platform === "win32" ? "npm.cmd" : "npm";
execFileSync(
  NPM,
  [
    "install",
    // --prefix pins the install location. Without it npm resolves the install
    // itself and may land somewhere else — it walks up looking for a
    // package.json. The failure mode is silent and nasty: npm reports
    // "added 17 packages" while the directory it was supposed to fill stays
    // empty, so the test dies on a missing file instead of on a broken
    // install. The release workflow's mid-job npm upgrade is what exposed
    // this — it was the only job where the install landed elsewhere.
    "--prefix",
    WORK,
    "--no-package-lock",
    "--no-audit",
    "--no-fund",
    // The official registry, explicitly. A user (or CI) whose ~/.npmrc points
    // at a regional mirror gets a 404 for these packages — the mirror does not
    // carry them — and the test fails for a reason that has nothing to do with
    // this plugin.
    "--registry=https://registry.npmjs.org/",
  ],
  // Not "ignore": with the output swallowed, a failed install looked like a
  // missing dependency ("node_modules/.bin/tsc ENOENT") instead of what it
  // was — the install itself failing. The npm error is the useful part.
  { cwd: WORK, stdio: "inherit" },
);

const SRC = path.join(WORK, "src");
mkdirSync(SRC, { recursive: true });
cpSync(PLUGIN_SRC, path.join(SRC, "index.ts"));
// The plugin shells out to the shared decision service — without it in the
// fixture the test passes while the plugin would do nothing in production.
cpSync(path.join(REPO, "hookctl.py"), path.join(WORK, "hookctl.py"));
cpSync(path.join(REPO, "detector.py"), path.join(WORK, "detector.py"));
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
// Call typescript through node, not through `node_modules/.bin/tsc`.
//
// The `.bin` shim is created by npm's bin-linking step, and that step does
// not reliably run for every npm version in every environment — the release
// workflow deliberately upgrades npm mid-job, and there the shim was missing
// while the package itself had been installed correctly (the log said
// "added 17 packages"). Going through the package's own entry point skips
// the shim entirely: `node node_modules/typescript/bin/tsc` is the same
// program the shim would have run.
const TSC_JS = path.join(WORK, "node_modules", "typescript", "bin", "tsc");
if (!existsSync(TSC_JS)) {
  console.error("continue-hook dsh test: typescript is not installed at", TSC_JS);
  console.error("  npm root:", execFileSync("npm", ["root"], { encoding: "utf8" }).trim());
  console.error("  npm prefix:", execFileSync("npm", ["prefix"], { encoding: "utf8" }).trim());
  console.error("  npm config prefix:", execFileSync("npm", ["config", "get", "prefix"], { encoding: "utf8" }).trim());
  console.error("  node_modules present:", existsSync(path.join(WORK, "node_modules")));
  if (existsSync(path.join(WORK, "node_modules"))) {
    console.error(
      "  node_modules contains:",
      readdirSync(path.join(WORK, "node_modules")).join(", "),
    );
  }
  console.error("  npm on PATH:", execFileSync("which", ["npm"], { encoding: "utf8" }).trim());
  process.exit(1);
}
execFileSync("node", [TSC_JS, "-p", "tsconfig.json"], {
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

it("lastAssistantText reads the newest assistant message", () => {
  const text = lastAssistantText([
    { role: "user", content: [{ type: "text", text: "hi" }] },
    { role: "assistant", content: [{ type: "text", text: "older" }] },
    { role: "assistant", content: [{ type: "text", text: "newest" }] },
  ]);
  if (text !== "newest") throw new Error(`got ${JSON.stringify(text)}`);
});

it("permission-seeking ending steers one continuation", () => {
  const steered = runTurn("要不要我顺便也把示例补上？");
  if (steered.length !== 1) throw new Error(`expected 1 steer, got ${steered.length}`);
  const text = steered[0].content[0].text;
  if (!text.includes("continue-hook")) throw new Error("nudge text missing");
  if (steered[0].role !== "user") throw new Error(`role is ${steered[0].role}`);
});

it("declarative ending does not steer", () => {
  const steered = runTurn("已完成，验证通过。", { sessionId: "s2" });
  if (steered.length !== 0) throw new Error(`expected 0 steers, got ${steered.length}`);
});

it("boundary question does not steer", () => {
  const steered = runTurn("这会花 $69，需要你批准后我才买。", { sessionId: "s3" });
  if (steered.length !== 0) throw new Error(`expected 0 steers, got ${steered.length}`);
});

it("the per-turn cap is enforced", () => {
  const steered = runTurn("要不要我继续？", {
    sessionId: "s4",
    config: { max_nudges_per_turn: 1 },
  });
  if (steered.length !== 1) throw new Error(`expected 1 steer, got ${steered.length}`);
});

it("dry_run never steers", () => {
  const steered = runTurn("要不要我继续？", {
    sessionId: "s5",
    config: { dry_run: true },
  });
  if (steered.length !== 0) throw new Error(`expected 0 steers, got ${steered.length}`);
});

it("a broken detector fails open", () => {
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
