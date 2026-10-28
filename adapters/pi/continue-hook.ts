/**
 * hermes-continue-hook: generated — do not edit
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
 * Loop safety: every cap, the counter and the event log live in hookctl.py.
 * This extension holds no state of its own — a second counter here would
 * disagree with the shell adapters about the caps.
 */
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
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

/**
 * The text of one message, whatever shape its content takes. Pure.
 *
 * Pi has used three shapes for `content` — a string, an array of parts, and an
 * object with a `text` field. All three are read here, so a rename upstream
 * does not silently stop the hook.
 */
export function textOfMessage(message: any): string {
  const candidates = [message?.content, message?.parts, message?.info?.content];
  for (const content of candidates) {
    const text = textOfContent(content);
    if (text) return text;
  }
  return "";
}

/** The text of one content value, whatever shape it takes. Pure. */
function textOfContent(content: any): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return partsToText(content);
  if (content && typeof content.text === "string") return content.text;
  return "";
}

/** The text of an array of parts. Pure. */
function partsToText(parts: any[]): string {
  return parts
    .filter((c: any) => c && typeof c.text === "string")
    .map((c: any) => c.text)
    .join("\n");
}

/**
 * The newest assistant text out of a Pi message list. Pure.
 *
 * Walks backwards and returns the first message with text, so the newest one
 * wins.
 */
export function lastAssistantText(messages: any[]): string {
  if (!Array.isArray(messages)) return "";
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m) continue;
    const role = m.role ?? m.info?.role;
    if (role && role !== "assistant") continue;
    const text = textOfMessage(m);
    if (text.trim()) return text;
  }
  return "";
}

function shouldNudge(
  text: string,
  sessionId: string,
): { nudge: boolean; reason: string; nudgeText?: string } {
  if (!HOOKCTL || !text.trim()) return { nudge: false, reason: "no_hookctl" };
  const res = spawnSync(
    "python3",
    [HOOKCTL, "--text", "-", "--session", sessionId, "--host", "pi", "--nudge-text"],
    // The reply goes in over stdin, not argv: a command-line argument is
    // visible to every process on the machine.
    { encoding: "utf8", timeout: 10000, input: text },
  );
  if (res.status !== 0) return { nudge: false, reason: "hookctl_failed" };
  try {
    const out = JSON.parse((res.stdout ?? "").trim());
    return {
      nudge: Boolean(out.should_nudge),
      reason: out.reason ?? "unknown",
      nudgeText: out.nudge,
    };
  } catch {
    return { nudge: false, reason: "hookctl_bad_output" };
  }
}

/** The message list of a settle event, under either field name. Pure. */
function messagesOf(event: any): any[] {
  return event?.messages ?? event?.entries ?? [];
}

/** The session id of a settle event. Pure. */
function sessionIdOf(event: any): string {
  return String(event?.sessionId ?? event?.session_id ?? "pi");
}

/**
 * What to hand back to Pi for one settle event. Pure.
 *
 * Returns the continuation payload, or undefined for "do nothing".
 */
export function settleResponse(event: any): { continue: true; entries: any[] } | undefined {
  const text = lastAssistantText(messagesOf(event));
  if (!text) {
    reportUnreadableEvent();
    return undefined;
  }

  const verdict = shouldNudge(text, sessionIdOf(event));
  if (!verdict.nudge) return undefined;

  // One extra model request, carrying the nudge as a custom message.
  return {
    continue: true,
    entries: [{ type: "custom_message", content: verdict.nudgeText ?? NUDGE }],
  };
}

/**
 * Say that the event carried no readable text.
 *
 * On stderr rather than passing silently, so a Pi API change that renames the
 * field shows up in the user's log instead of "it just never fires".
 */
function reportUnreadableEvent(): void {
  console.error(
    "continue-hook(pi): no assistant text in the settle event — " +
      "Pi may have renamed messages/entries; nothing was evaluated",
  );
}

export default function continueHookPiExtension(pi: any) {
  pi.on("agent_before_settle", (event: any) => {
    try {
      return settleResponse(event);
    } catch {
      return undefined; // fail open
    }
  });
}
