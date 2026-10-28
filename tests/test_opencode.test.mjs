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
 * OpenCode plugin tests — mocked OpenCode SDK client, real Python detector.
 *
 *   node tests/test_opencode.mjs
 *
 * The plugin is imported directly, so the test exercises the same code path
 * OpenCode would run: session.idle → read messages → detect → prompt.
 */
import { mkdtempSync, writeFileSync, readFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

const REPO = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const HOME = mkdtempSync(path.join(tmpdir(), "ch-oc-"));
mkdirSync(path.join(HOME, ".hermes"), { recursive: true });
process.env.HOME = HOME;

const { handleIdle, lastAssistantText, ContinueHookPlugin } = await import(
  path.join(REPO, "adapters", "opencode", "continue-hook.js")
);

const ASK = "要不要我顺便也把示例补上？";
const PLAIN = "已完成，验证通过。";

function mockClient(messages, { failPrompt = false } = {}) {
  const calls = { prompts: [] };
  return {
    calls,
    client: {
      session: {
        async messages() {
          return messages;
        },
        async prompt(body) {
          if (failPrompt) throw new Error("prompt failed");
          calls.prompts.push(body);
          return { info: { id: "x" }, parts: [] };
        },
      },
    },
  };
}

function assistant(text) {
  return { info: { role: "assistant" }, parts: [{ type: "text", text }] };
}
function user(text) {
  return { info: { role: "user" }, parts: [{ type: "text", text }] };
}

it("lastAssistantText picks the newest assistant message", () => {
  const text = lastAssistantText([user("hi"), assistant("older"), assistant("newest")]);
  assert.strictEqual(text, "newest");
});

it("lastAssistantText ignores user-only lists", () => {
  assert.strictEqual(lastAssistantText([user("hi")]), "");
  assert.strictEqual(lastAssistantText(null), "");
});

it("permission-seeking ending triggers exactly one continuation", async () => {
  const { client, calls } = mockClient([user("go"), assistant(ASK)]);
  const res = await handleIdle(client, { properties: { sessionID: "s1" } });
  assert.strictEqual(res.nudged, true);
  assert.strictEqual(calls.prompts.length, 1);
  const body = calls.prompts[0];
  const text = body.parts ? body.parts[0].text : body.body.parts[0].text;
  assert.ok(text.includes("continue-hook"));
});

it("the registered session.idle handler actually nudges", async () => {
  // Every other test calls handleIdle() directly — which is why a ReferenceError
  // inside the *registered* handler (a leftover `state` argument after the
  // debounce was removed) passed CI while OpenCode could never fire at all.
  const { client, calls } = mockClient([user("go"), assistant(ASK)]);
  const plugin = await ContinueHookPlugin({ client });
  await plugin.event({ event: { type: "session.idle", properties: { sessionID: "h1" } } });
  assert.strictEqual(calls.prompts.length, 1, "the handler must prompt exactly once");
});

it("the registered handler stays silent for a clean reply", async () => {
  const { client, calls } = mockClient([assistant(PLAIN)]);
  const plugin = await ContinueHookPlugin({ client });
  await plugin.event({ event: { type: "session.idle", properties: { sessionID: "h2" } } });
  assert.strictEqual(calls.prompts.length, 0);
});

it("the registered handler ignores other event types", async () => {
  const { client, calls } = mockClient([assistant(ASK)]);
  const plugin = await ContinueHookPlugin({ client });
  await plugin.event({ event: { type: "session.updated", properties: { sessionID: "h3" } } });
  assert.strictEqual(calls.prompts.length, 0);
});

it("declarative ending does not continue", async () => {
  const { client, calls } = mockClient([assistant(PLAIN)]);
  const res = await handleIdle(client, { properties: { sessionID: "s2" } });
  assert.strictEqual(res.nudged, false);
  assert.strictEqual(calls.prompts.length, 0);
});

it("boundary question is not nudged", async () => {
  const { client, calls } = mockClient([assistant("这会花 $69，需要你批准后我才买。")]);
  const res = await handleIdle(client, { properties: { sessionID: "s3" } });
  assert.strictEqual(res.nudged, false);
  assert.strictEqual(calls.prompts.length, 0);
});

it("a repeated idle is recorded by the engine, not silently debounced", async () => {
  // The plugin used to hold a 1.5 s window of its own and return "debounce"
  // without logging anything, so the event log could not be used as a
  // denominator for "each evaluation is appended".
  writeFileSync(
    path.join(HOME, ".hermes", "continue_hook.json"),
    JSON.stringify({ max_nudges_per_turn: 1 }),
  );
  const { client } = mockClient([assistant(ASK)]);
  const first = await handleIdle(client, { properties: { sessionID: "s5" } });
  const second = await handleIdle(client, { properties: { sessionID: "s5" } });
  assert.strictEqual(first.nudged, true);
  assert.strictEqual(second.nudged, false);
  assert.strictEqual(second.reason, "max_nudges");
  const events = readFileSync(path.join(HOME, ".hermes", "continue-hook-events.jsonl"), "utf8")
    .trim()
    .split("\n")
    .map((l) => JSON.parse(l));
  const forS5 = events.filter((e) => e.session === "s5");
  assert.strictEqual(forS5.length, 2, "both evaluations must be recorded");
});

it("max nudges per turn is respected", async () => {
  const { client, calls } = mockClient([assistant(ASK)]);
  writeFileSync(
    path.join(HOME, ".hermes", "continue_hook.json"),
    JSON.stringify({ max_nudges_per_turn: 1, cooldown_seconds: 0 }),
  );
  const first = await handleIdle(client, { properties: { sessionID: "s4" } });
  const second = await handleIdle(client, { properties: { sessionID: "s4" } });
  assert.strictEqual(first.nudged, true);
  assert.strictEqual(second.nudged, false);
  assert.strictEqual(calls.prompts.length, 1);
});

it("dry_run never continues", async () => {
  writeFileSync(
    path.join(HOME, ".hermes", "continue_hook.json"),
    JSON.stringify({ dry_run: true, cooldown_seconds: 0 }),
  );
  const { client, calls } = mockClient([assistant(ASK)]);
  const res = await handleIdle(client, { properties: { sessionID: "s5" } }, {});
  assert.strictEqual(res.nudged, false);
  assert.strictEqual(calls.prompts.length, 0);
});

it("failed prompt is reported, not thrown", async () => {
  writeFileSync(
    path.join(HOME, ".hermes", "continue_hook.json"),
    JSON.stringify({ cooldown_seconds: 0 }),
  );
  const { client } = mockClient([assistant(ASK)], { failPrompt: true });
  const res = await handleIdle(client, { properties: { sessionID: "s6" } }, {});
  assert.strictEqual(res.nudged, false);
  assert.strictEqual(res.reason, "prompt_failed");
});

it("detector CLI agrees with the python engine", () => {
  const out = execFileSync("python3", [path.join(REPO, "detector.py"), "--text", ASK], {
    encoding: "utf8",
  });
  const parsed = JSON.parse(out.trim());
  assert.strictEqual(parsed.should_nudge, true);
});
