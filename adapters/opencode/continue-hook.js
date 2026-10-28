/**
 * hermes-continue-hook: generated — do not edit
 * continue-hook for OpenCode — a session-idle plugin.
 *
 * OpenCode plugin contract (verified against opencode.ai/docs/plugins and
 * opencode.ai/docs/sdk):
 *   load     : .opencode/plugins/<name>.js  (repo) or ~/.config/opencode/plugin/
 *   API      : the plugin factory receives { project, client, $, directory, worktree }
 *   events   : `event` receives the bus; "session.idle" fires when the agent
 *              loop goes idle — that is the turn-end boundary.
 *   continue : client.session.prompt({ sessionID, parts: [{ type: "text", text }] })
 *              sends a prompt back into the session, which starts another turn.
 *
 * Detection is delegated to the shared Python engine (detector.py) so every
 * host makes the same decision — this plugin never re-implements the rules.
 *
 * Loop safety:
 *   * one nudge per session per turn cycle, capped by the shared config
 *   * caps, the per-session counter and the event log all live in hookctl.py
 *   * if the detector is missing or errors, the plugin does nothing (fail open)
 */
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// hookctl.py is the shared decision service: it owns the caps, the state file
// and the event log, so this plugin never re-implements policy.
function findHookctl() {
  const candidates = [
    path.resolve(__dirname, "..", "..", "hookctl.py"),
    path.resolve(__dirname, "..", "hookctl.py"),
    path.resolve(__dirname, "hookctl.py"),
    path.join(homedir(), ".hermes", "continue-hook", "hookctl.py"),
  ];
  return candidates.find((p) => existsSync(p)) || null;
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

/** Ask the shared decision service whether this reply should be nudged. */
function shouldNudge(text, sessionId) {
  if (!HOOKCTL || !text || !text.trim()) return { nudge: false, reason: "no_hookctl" };
  const res = spawnSync(
    "python3",
    [HOOKCTL, "--text", "-", "--session", sessionId, "--host", "opencode", "--nudge-text"],
    { encoding: "utf8", timeout: 10000, input: text },
  );
  if (res.status !== 0) return { nudge: false, reason: "hookctl_failed" };
  try {
    const out = JSON.parse((res.stdout || "").trim());
    return { nudge: Boolean(out.should_nudge), reason: out.reason || "unknown", nudgeText: out.nudge };
  } catch {
    return { nudge: false, reason: "hookctl_bad_output" };
  }
}

/** Extract the newest assistant text from an OpenCode message list. */
export function lastAssistantText(messages) {
  if (!Array.isArray(messages)) return "";
  for (let i = messages.length - 1; i >= 0; i--) {
    const entry = messages[i];
    const info = entry && entry.info;
    if (!info || (info.role && info.role !== "assistant")) continue;
    const parts = entry.parts || [];
    const text = parts
      .filter((p) => p && (p.type === "text" || typeof p.text === "string"))
      .map((p) => p.text || "")
      .join("\n")
      .trim();
    if (text) return text;
  }
  return "";
}

/** Send the nudge back into the session (v2 client first, v1 as fallback). */
async function promptSession(client, sessionId, text) {
  try {
    await client.session.prompt({ sessionID: sessionId, parts: [{ type: "text", text }] });
    return true;
  } catch {
    try {
      await client.session.prompt({
        path: { id: sessionId },
        body: { parts: [{ type: "text", text }] },
      });
      return true;
    } catch {
      return false;
    }
  }
}

export async function handleIdle(client, event) {
  const sessionId = event?.properties?.sessionID || event?.properties?.info?.id || "";
  if (!sessionId) return { nudged: false, reason: "no_session" };
  // No local debounce: the engine records every evaluation and its per-turn
  // cap is what prevents a double nudge. Keeping a private 1.5 s window here
  // meant a skipped evaluation left no trace, so the event log could not be
  // used as the denominator.
  let messages;
  try {
    messages = await client.session.messages({ path: { id: sessionId } });
  } catch {
    try {
      messages = await client.session.messages({ sessionID: sessionId });
    } catch {
      return { nudged: false, reason: "no_messages" };
    }
  }
  const text = lastAssistantText(messages);
  if (!text) return { nudged: false, reason: "empty" };

  const verdict = shouldNudge(text, sessionId);
  if (!verdict.nudge) return { nudged: false, reason: verdict.reason };
  const sent = await promptSession(client, sessionId, verdict.nudgeText || NUDGE);
  return { nudged: sent, reason: sent ? "nudged" : "prompt_failed" };
}

export const ContinueHookPlugin = async ({ client }) => {
  return {
    event: async ({ event }) => {
      if (!event || event.type !== "session.idle") return;
      try {
        const result = await handleIdle(client, event);
        // Fail open, but not silently: a crash here used to be invisible and
        // the host simply never fired.
        if (!result?.nudged && result?.reason && result.reason.startsWith("adapter_error")) {
          console.error(`continue-hook: ${result.reason}`);
        }
      } catch (err) {
        console.error(`continue-hook: adapter error: ${err && err.message ? err.message : err}`);
      }
    },
  };
};

export default ContinueHookPlugin;
