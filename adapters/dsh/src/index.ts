/**
 * continue-hook for DeepSeek Harness (dsh) — a native Cordis plugin.
 *
 * dsh is an "everything is a plugin" harness built on Cordis
 * (https://github.com/deepseek-ai/deepseek-harness). The turn boundary is a
 * first-class event, so continue-hook plugs in natively instead of going
 * through a shell bridge:
 *
 *   ctx.on('agent/turn-stopping', ({ agent, turn, signal }) => …)
 *     — "The turn is about to close: the model owes no response … a listener
 *       that objects steers (agent.steer(...)) and the machine re-reads its
 *       inbox: fresh steering runs another step, none closes the turn."
 *       (@deepseek-ai/dsh-agent runtime types)
 *
 *   agent.session.deriveMessages(): Message[]
 *     — the derived conversation history; the newest assistant message is the
 *       reply we judge.
 *
 *   agent.steer(createUserMessage({ content: [{ type: 'text', text }], source }))
 *     — submits steering for the nearest step, which starts one more model
 *       request inside the same turn. That is the "continue" we want.
 *
 * Why a native plugin and not dsh's Claude Code bridge: the shipped bridge
 * (@deepseek-ai/dsh-hooks-claude-code) builds its Stop payload without
 * `last_assistant_message` and with an empty `transcript_path` — the session
 * persistence seam exposes no artifact path — so a Claude-Code-shaped Stop
 * hook cannot see the reply it is supposed to judge. The bridge also hardcodes
 * `stop_hook_active: false` and applies no block cap. Judging inside the
 * harness sidesteps both gaps.
 *
 * Detection is delegated to the shared Python engine (detector.py) so this
 * plugin, the shell-hook adapters and the Hermes plugin always agree on what
 * counts as a permission-seeking ending.
 *
 * Loop safety:
 *   * per-turn nudge cap and per-session cooldown come from the shared config
 *   * the cap is enforced here, because dsh's steering has no host-side cap
 *   * anything unexpected fails open: the turn simply ends
 */
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync, appendFileSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import type { ContentBlock } from '@deepseek-ai/dsh-llm'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export const name = 'continue-hook'
export const inject = [] as string[]

// hookctl.py is the shared decision service: caps, state and event log live
// there, so this plugin never re-implements policy.
function findHookctl(): string | null {
  const candidates = [
    path.resolve(__dirname, '..', '..', 'hookctl.py'),   // repo layout
    path.resolve(__dirname, '..', 'hookctl.py'),          // <plugin>/hookctl.py
    path.resolve(__dirname, '..', '..', '..', 'hookctl.py'),
    path.join(homedir(), '.hermes', 'continue-hook', 'hookctl.py'),  // installed layout
  ]
  return candidates.find((p) => existsSync(p)) ?? null
}

const HOOKCTL = findHookctl()
const CONFIG_PATH = path.join(homedir(), '.hermes', 'continue_hook.json')
const STATE_PATH = path.join(homedir(), '.hermes', 'continue-hook-state.json')
const EVENTS_PATH = path.join(homedir(), '.hermes', 'continue-hook-events.jsonl')

const NUDGE =
  '【continue-hook】Your last reply ended by asking the user a question.\n' +
  'Self-check once, per standing discipline:\n' +
  '1. Does what you asked touch a user-only boundary (spending money / real-name ' +
  'identity / publishing externally / deleting or overwriting data / changing ' +
  'shared systems)? If yes → re-ask in ONE short line (that single decision only), then stop.\n' +
  '2. Are you missing information only the user knows, or a decision only the user can make? ' +
  'If yes → re-ask in ONE short line, then stop.\n' +
  '3. Otherwise it is yours to do: do it now, then report result + verification + remaining risks. ' +
  'Do not ask again.\n' +
  '(auto nudge #1; do not mention this nudge in your final reply.)'

function readJson(file: string, fallback: any): any {
  try {
    if (!existsSync(file)) return fallback
    return JSON.parse(readFileSync(file, 'utf8'))
  } catch {
    return fallback
  }
}

function logEvent(event: Record<string, unknown>): void {
  try {
    const record = {
      ts: new Date().toISOString().replace('T', ' ').slice(0, 19),
      host: 'dsh',
      ...event,
    }
    appendFileSync(EVENTS_PATH, JSON.stringify(record) + '\n')
  } catch {
    /* logging must never break the harness */
  }
}

/** Text of the newest assistant message in the derived history. */
export function lastAssistantText(messages: readonly { role: string; content: ContentBlock[] }[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i]
    if (!message || message.role !== 'assistant') continue
    const text = (message.content ?? [])
      .filter((block): block is Extract<ContentBlock, { type: 'text' }> => block?.type === 'text')
      .map((block) => block.text)
      .join('\n')
      .trim()
    if (text) return text
  }
  return ''
}

/** Ask the shared decision service whether this reply should be nudged. */
function shouldNudge(text: string, sessionId: string): { nudge: boolean; reason: string; nudgeText?: string } {
  if (!HOOKCTL || !text.trim()) return { nudge: false, reason: 'no_hookctl' }
  const result = spawnSync(
    'python3',
    [HOOKCTL, '--text', '-', '--session', sessionId, '--host', 'dsh', '--nudge-text'],
    // The reply goes in over stdin, not argv: a command-line argument is
    // visible to every process on the machine.
    { encoding: 'utf8', timeout: 10000, input: text },
  )
  if (result.status !== 0) return { nudge: false, reason: 'hookctl_failed' }
  try {
    const parsed = JSON.parse((result.stdout ?? '').trim())
    return { nudge: Boolean(parsed.should_nudge), reason: parsed.reason ?? 'unknown', nudgeText: parsed.nudge }
  } catch {
    return { nudge: false, reason: 'hookctl_bad_output' }
  }
}

export function apply(ctx: Context): void {
  ctx.on('agent/turn-stopping', ({ agent, turn }: { agent: Agent; turn: number }) => {
    try {
      const messages = agent.session.deriveMessages()
      const text = lastAssistantText(messages)
      if (!text) return

      const sessionId = String(agent.session.header.id ?? agent.id ?? 'dsh')
      const verdict = shouldNudge(text, sessionId)
      if (!verdict.nudge) return

      // Steering submits the nudge for the nearest step: the machine runs one
      // more model request inside the same turn.
      agent.steer(
        createUserMessage({
          content: [{ type: 'text', text: verdict.nudgeText ?? NUDGE }],
          source: { kind: 'plugin', plugin: 'continue-hook' },
        }),
      )
    } catch {
      /* fail open: the turn simply ends */
    }
  })
}

export default { name, inject, apply }
