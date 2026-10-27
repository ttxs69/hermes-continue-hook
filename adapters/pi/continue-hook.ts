/**
 * continue-hook for Pi — an extension that continues the turn when the
 * assistant ends by asking permission for something it could have done.
 *
 * Pi extension contract (verified against pi.dev/docs/latest/extensions and
 * packages/coding-agent/docs/extensions.md):
 *   load   : ~/.pi/agent/extensions/<name>.ts  (or a Pi package)
 *   API    : the factory receives the ExtensionAPI (`pi`)
 *   events : `agent_before_settle` is "the final actionable boundary: it can
 *            append entries and request one continuation"; `turn_end` is the
 *            per-model-turn boundary. Both may return `continue: true` for
 *            one next model request — and the docs warn to guard the
 *            condition, because an unconditional continuation loops forever.
 *   entries: a proposed `custom_message` entry becomes the next instruction.
 *
 * Detection is delegated to the shared Python engine (detector.py) so this
 * extension and the shell-hook adapters always agree.
 *
 * Loop safety: the shared config caps nudges per turn and enforces a cooldown;
 * the extension also keeps its own per-session counter.
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// hookctl.py is the shared decision service: caps, state and event log live
// there, so this extension never re-implements policy.
function findHookctl(): string | null {
  const candidates = [
    path.resolve(__dirname, "..", "..", "hookctl.py"),
    path.resolve(__dirname, "..", "hookctl.py"),
    path.resolve(__dirname, "hookctl.py"),
    path.join(homedir(), ".hermes", "continue-hook", "hookctl.py"),
  ];
  return candidates.find((p) => existsSync(p)) ?? null;
}

const HOOKCTL = findHookctl();
const CONFIG_PATH = path.join(homedir(), ".hermes", "continue_hook.json");
const STATE_PATH = path.join(homedir(), ".hermes", "continue-hook-state.json");
const EVENTS_PATH = path.join(homedir(), ".hermes", "continue-hook-events.jsonl");

const NUDGE =
  "【continue-hook】Your last reply ended by asking the user a question.\n" +
  "Self-check once, per standing discipline:\n" +
  "1. Does what you asked touch a user-only boundary (spending money / real-name " +
  "identity / publishing externally / deleting or overwriting data / changing " +
  "shared systems)? If yes → re-ask in ONE short line (that single decision only), then stop.\n" +
  "2. Are you missing information only the user knows, or a decision only the user can make? " +
  "If yes → re-ask in ONE short line, then stop.\n" +
  "3. Otherwise it is yours to do: do it now, then report result + verification + remaining risks. " +
  "Do not ask again.\n" +
  "(auto nudge #1; do not mention this nudge in your final reply.)";

function readJson(file: string, fallback: any): any {
  try {
    if (!existsSync(file)) return fallback;
    return JSON.parse(readFileSync(file, "utf8"));
  } catch {
    return fallback;
  }
}

function logEvent(event: Record<string, unknown>): void {
  try {
    const record = {
      ts: new Date().toISOString().replace("T", " ").slice(0, 19),
      host: "pi",
      ...event,
    };
    appendFileSync(EVENTS_PATH, JSON.stringify(record) + "\n");
  } catch {
    /* never break the host */
  }
}

/** Pull the newest assistant text out of a Pi message list. */
export function lastAssistantText(messages: any[]): string {
  if (!Array.isArray(messages)) return "";
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m) continue;
    const role = m.role ?? m.info?.role;
    if (role && role !== "assistant") continue;
    const content = m.content ?? m.parts ?? m.info?.content;
    let text = "";
    if (typeof content === "string") {
      text = content;
    } else if (Array.isArray(content)) {
      text = content
        .filter((c: any) => c && typeof c.text === "string")
        .map((c: any) => c.text)
        .join("\n");
    } else if (content && typeof content.text === "string") {
      text = content.text;
    }
    if (text.trim()) return text;
  }
  return "";
}

function shouldNudge(text: string, sessionId: string): { nudge: boolean; reason: string; nudgeText?: string } {
  if (!HOOKCTL || !text.trim()) return { nudge: false, reason: "no_hookctl" };
  const res = spawnSync(
    "python3",
    [HOOKCTL, "--text", text, "--session", sessionId, "--host", "pi", "--nudge-text"],
    { encoding: "utf8", timeout: 10000 },
  );
  if (res.status !== 0) return { nudge: false, reason: "hookctl_failed" };
  try {
    const out = JSON.parse((res.stdout ?? "").trim());
    return { nudge: Boolean(out.should_nudge), reason: out.reason ?? "unknown", nudgeText: out.nudge };
  } catch {
    return { nudge: false, reason: "hookctl_bad_output" };
  }
}

export default function continueHookPiExtension(pi: any) {
  pi.on("agent_before_settle", async (event: any) => {
    try {
      const messages: any[] = event?.messages ?? event?.entries ?? [];
      const text = lastAssistantText(messages);
      if (!text) return undefined;

      const sessionId = String(event?.sessionId ?? event?.session_id ?? "pi");
      const verdict = shouldNudge(text, sessionId);
      if (!verdict.nudge) return undefined;

      // One extra model request, carrying the nudge as a custom message.
      return {
        continue: true,
        entries: [{ type: "custom_message", content: verdict.nudgeText ?? NUDGE }],
      };
    } catch {
      return undefined; // fail open
    }
  });
}
