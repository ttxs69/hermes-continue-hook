#!/usr/bin/env node
/**
 * continue-hook installer — one implementation for every host.
 *
 * Why a CLI instead of a shell script: the shell script could not run on
 * Windows, and the npm package needs an entry point. This CLI is what
 * `npx hermes-continue-hook install` runs.
 *
 * Commands:
 *   install    [--host <list|all>] [--dry-run]
 *   uninstall  [--host <list|all>]
 *   check      [--host <list|all>]     report what is installed
 *   doctor                              detect the hosts on this machine
 *
 * Rules:
 *   * idempotent — installing twice never duplicates a hook,
 *   * non-destructive — your other hooks and settings keys survive,
 *   * reversible — uninstall removes only what this tool installed.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync, copyFileSync, rmSync, cpSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = path.resolve(HERE, "..");
const RUNTIME_DIR = path.join(homedir(), ".hermes", "continue-hook");

/** Shared runtime files every host needs. */
const RUNTIME_FILES = ["detector.py", "hookctl.py"];

/**
 * The shell-hook adapters import their shared transport module from the
 * directory next to them, so it has to be installed beside them — missing it
 * would make every installed hook fail open (and do nothing).
 */
const TRANSPORT_FILES = [{ from: "adapters/_common.py", to: "_common.py" }];

const SKILL_DIRS = [
  ".claude/skills", ".codex/skills", ".cursor/skills", ".gemini/skills",
  ".agents/skills", ".copilot/skills", ".qwen/skills", ".config/opencode/skills",
  ".pi/agent/skills", ".factory/skills", ".grok/skills", ".kimi/skills",
  ".antigravity/skills", ".devin/skills", ".dsh/skills",
];

/**
 * Host table. `kind` decides how the host is wired:
 *   claude-stop  — Claude-Code-shaped Stop hook: {"hooks":{"Stop":[{hooks:[…]}]}}
 *   cursor-stop  — Cursor's stop hook:        {"version":1,"hooks":{"stop":[…]}}
 *   copilot-stop — Copilot's agentStop hook:   {"version":1,"hooks":{"agentStop":[…]}}
 *   gemini-after — Gemini's AfterAgent hook:   {"hooks":{"AfterAgent":[{hooks:[…]}]}}
 *   file         — a plugin/extension file dropped into a directory
 *   dsh          — the DeepSeek Harness plugin package
 *   skills       — the portable SKILL.md layer
 */
const HOSTS = {
  "claude-code": {
    label: "Claude Code",
    kind: "claude-stop",
    config: ".claude/settings.json",
    hook: "claude-code/stop_hook.py",
  },
  codex: {
    label: "Codex CLI",
    kind: "claude-stop",
    config: ".codex/hooks.json",
    hook: "codex/stop_hook.py",
    note: "run /hooks once in the Codex CLI to trust the new hook",
  },
  qwen: {
    label: "Qwen Code",
    kind: "claude-stop",
    config: ".qwen/settings.json",
    hook: "qwen/stop_hook.py",
  },
  cursor: {
    label: "Cursor",
    kind: "cursor-stop",
    config: ".cursor/hooks.json",
    hook: "cursor/stop_hook.py",
  },
  copilot: {
    label: "GitHub Copilot CLI",
    kind: "copilot-stop",
    config: ".copilot/hooks/continue-hook.json",
    hook: "copilot/stop_hook.py",
  },
  gemini: {
    label: "Gemini CLI",
    kind: "gemini-after",
    config: ".gemini/settings.json",
    hook: "gemini/stop_hook.py",
  },
  opencode: {
    label: "OpenCode",
    kind: "file",
    target: ".config/opencode/plugin/continue-hook.js",
    source: "adapters/opencode/continue-hook.js",
  },
  pi: {
    label: "Pi",
    kind: "file",
    target: ".pi/agent/extensions/continue-hook.ts",
    source: "adapters/pi/continue-hook.ts",
  },
  dsh: {
    label: "DeepSeek Harness",
    kind: "dsh",
    target: ".hermes/continue-hook/dsh",
  },
  skills: {
    label: "Portable skills layer",
    kind: "skills",
  },
};

const HOST_NAMES = Object.keys(HOSTS);

// ── helpers ────────────────────────────────────────────────────────────────
const expand = (p) => path.join(homedir(), p.replace(/^~\//, ""));

function parseArgs(argv) {
  const opts = { command: "install", hosts: ["all"], dryRun: false };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--host" || arg === "-H") {
      opts.hosts = String(argv[++i] || "").split(",").map((s) => s.trim()).filter(Boolean);
    } else if (arg.startsWith("--host=")) {
      opts.hosts = arg.slice(7).split(",").map((s) => s.trim()).filter(Boolean);
    } else if (arg === "--dry-run") {
      opts.dryRun = true;
    } else if (arg === "--json") {
      opts.json = true;
    } else if (arg === "-h" || arg === "--help") {
      opts.command = "help";
    } else {
      rest.push(arg);
    }
  }
  if (rest.length) opts.command = rest[0];
  if (opts.hosts.includes("all")) opts.hosts = [...HOST_NAMES];
  const unknown = opts.hosts.filter((h) => !HOSTS[h]);
  if (unknown.length) {
    console.error(`unknown host(s): ${unknown.join(", ")}\nknown: ${HOST_NAMES.join(", ")}`);
    process.exit(2);
  }
  return opts;
}

function readJson(file) {
  try {
    if (!existsSync(file)) return {};
    const parsed = JSON.parse(readFileSync(file, "utf8"));
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeJson(file, data) {
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

const isOurs = (entry) => {
  try {
    return JSON.stringify(entry).includes("continue-hook");
  } catch {
    return false;
  }
};

/** Replace our own hook entry inside a host config, keeping everything else. */
function patchHooks(data, kind, hookCmd) {
  const hooks = (data.hooks ||= {});
  const key =
    kind === "cursor-stop" ? "stop" : kind === "copilot-stop" ? "agentStop" : kind === "gemini-after" ? "AfterAgent" : "Stop";
  const list = Array.isArray(hooks[key]) ? hooks[key] : [];

  const kept = [];
  for (const group of list) {
    if (isOurs(group)) continue;
    if (Array.isArray(group?.hooks)) {
      // Claude-Code-shaped entry: a group of hooks.
      const entries = group.hooks.filter((h) => !isOurs(h));
      if (entries.length) kept.push({ ...group, hooks: entries });
    } else {
      // Flat entry (Cursor: {command}, Copilot: {type, bash}) — the user's
      // own hook. Dropping these would delete somebody else's configuration.
      kept.push(group);
    }
  }

  if (kind === "cursor-stop") {
    kept.push({ command: hookCmd, loop_limit: 2 });
  } else if (kind === "copilot-stop") {
    kept.push({ type: "command", bash: hookCmd, timeoutSec: 30 });
  } else if (kind === "gemini-after") {
    kept.push({ hooks: [{ name: "continue-hook", type: "command", command: hookCmd, timeout: 30000 }] });
  } else {
    kept.push({ hooks: [{ type: "command", command: hookCmd, timeout: 30 }] });
  }
  hooks[key] = kept;
  return data;
}

function removeOurs(data, kind) {
  if (!data.hooks) return data;
  const key =
    kind === "cursor-stop" ? "stop" : kind === "copilot-stop" ? "agentStop" : kind === "gemini-after" ? "AfterAgent" : "Stop";
  const list = Array.isArray(data.hooks[key]) ? data.hooks[key] : [];
  const kept = [];
  for (const group of list) {
    if (isOurs(group)) continue;
    if (Array.isArray(group?.hooks)) {
      const entries = group.hooks.filter((h) => !isOurs(h));
      if (entries.length) kept.push({ ...group, hooks: entries });
    } else {
      kept.push(group);
    }
  }
  if (kept.length) data.hooks[key] = kept;
  else delete data.hooks[key];
  if (Object.keys(data.hooks).length === 0) delete data.hooks;
  return data;
}

const copyIfMissing = (from, to, dryRun) => {
  if (!existsSync(from)) return false;
  if (!dryRun) {
    mkdirSync(path.dirname(to), { recursive: true });
    copyFileSync(from, to);
  }
  return true;
};

// ── commands ───────────────────────────────────────────────────────────────
function installRuntime(opts) {
  for (const file of RUNTIME_FILES) {
    copyIfMissing(path.join(PKG_ROOT, file), path.join(RUNTIME_DIR, file), opts.dryRun);
  }
  for (const file of TRANSPORT_FILES) {
    copyIfMissing(path.join(PKG_ROOT, file.from), path.join(RUNTIME_DIR, file.to), opts.dryRun);
  }
  for (const host of HOST_NAMES) {
    const def = HOSTS[host];
    if (def.kind === "claude-stop" || def.kind === "cursor-stop" || def.kind === "copilot-stop" || def.kind === "gemini-after") {
      copyIfMissing(path.join(PKG_ROOT, "adapters", def.hook), path.join(RUNTIME_DIR, def.hook), opts.dryRun);
    }
  }
}

function installHost(host, opts) {
  const def = HOSTS[host];
  const results = [];

  if (def.kind === "skills") {
    for (const dir of SKILL_DIRS) {
      const target = path.join(expand(dir), "continue-hook", "SKILL.md");
      const ok = copyIfMissing(path.join(PKG_ROOT, "skills", "continue-hook", "SKILL.md"), target, opts.dryRun);
      results.push({ path: target, ok });
    }
    return results;
  }

  if (def.kind === "file") {
    const target = expand(def.target);
    const ok = copyIfMissing(path.join(PKG_ROOT, def.source), target, opts.dryRun);
    results.push({ path: target, ok });
    return results;
  }

  if (def.kind === "dsh") {
    const target = expand(def.target);
    for (const file of ["src/index.ts", "package.json", "cordis.patch.yml", "tsconfig.json"]) {
      results.push({ path: path.join(target, file), ok: copyIfMissing(path.join(PKG_ROOT, "adapters", "dsh", file), path.join(target, file), opts.dryRun) });
    }
    results.push({ path: path.join(target, "hookctl.py"), ok: copyIfMissing(path.join(PKG_ROOT, "hookctl.py"), path.join(target, "hookctl.py"), opts.dryRun) });
    results.push({ path: path.join(target, "detector.py"), ok: copyIfMissing(path.join(PKG_ROOT, "detector.py"), path.join(target, "detector.py"), opts.dryRun) });
    return results;
  }

  // JSON-config hosts
  const configPath = expand(def.config);
  const data = readJson(configPath);
  const hookCmd = `python3 ${path.join(RUNTIME_DIR, def.hook)}`;
  const patched = patchHooks(data, def.kind, hookCmd);
  if (!opts.dryRun) writeJson(configPath, patched);
  results.push({ path: configPath, ok: true });
  return results;
}

function uninstallHost(host, opts) {
  const def = HOSTS[host];
  const results = [];
  if (def.kind === "skills") {
    for (const dir of SKILL_DIRS) {
      const target = path.join(expand(dir), "continue-hook");
      if (existsSync(target) && !opts.dryRun) rmSync(target, { recursive: true, force: true });
      results.push({ path: target, ok: true });
    }
    return results;
  }
  if (def.kind === "file") {
    const target = expand(def.target);
    if (existsSync(target) && !opts.dryRun) rmSync(target, { force: true });
    results.push({ path: target, ok: true });
    return results;
  }
  if (def.kind === "dsh") {
    const target = expand(def.target);
    if (existsSync(target) && !opts.dryRun) rmSync(target, { recursive: true, force: true });
    results.push({ path: target, ok: true });
    return results;
  }
  const configPath = expand(def.config);
  const data = readJson(configPath);
  const cleaned = removeOurs(data, def.kind);
  if (!opts.dryRun) writeJson(configPath, cleaned);
  results.push({ path: configPath, ok: true });
  return results;
}

function checkHost(host) {
  const def = HOSTS[host];
  if (def.kind === "skills") {
    const found = SKILL_DIRS.filter((d) => existsSync(path.join(expand(d), "continue-hook", "SKILL.md")));
    return { host, label: def.label, installed: found.length > 0, detail: `${found.length}/${SKILL_DIRS.length} skill dirs` };
  }
  if (def.kind === "file") {
    const target = expand(def.target);
    return { host, label: def.label, installed: existsSync(target), detail: target };
  }
  if (def.kind === "dsh") {
    const target = expand(def.target);
    return { host, label: def.label, installed: existsSync(path.join(target, "src", "index.ts")), detail: target };
  }
  const configPath = expand(def.config);
  const data = readJson(configPath);
  const blob = JSON.stringify(data);
  return {
    host,
    label: def.label,
    installed: blob.includes("continue-hook"),
    detail: configPath,
  };
}

function doctor() {
  const rows = [];
  for (const host of HOST_NAMES) {
    const def = HOSTS[host];
    const detected =
      def.kind === "skills" ? false : existsSync(expand(def.config ?? def.target ?? "."));
    rows.push({ host, label: def.label, detected });
  }
  return rows;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.command === "help") {
    console.log(`continue-hook — stop the agent from asking permission for things it should just do.

Usage:
  continue-hook install   [--host <${HOST_NAMES.join("|")}|all>] [--dry-run]
  continue-hook uninstall [--host <${HOST_NAMES.join("|")}|all>]
  continue-hook check     [--host <${HOST_NAMES.join("|")}|all>]
  continue-hook doctor

Shared config: ~/.hermes/continue_hook.json
Event log:    ~/.hermes/continue-hook-events.jsonl`);
    return;
  }

  if (opts.command === "doctor") {
    for (const row of doctor()) {
      console.log(`${row.detected ? "●" : "○"} ${row.label.padEnd(22)} ${row.detected ? "detected" : "not found"}`);
    }
    return;
  }

  if (opts.command === "check") {
    let any = false;
    for (const host of opts.hosts) {
      const row = checkHost(host);
      any = any || row.installed;
      console.log(`${row.installed ? "✓" : "✗"} ${row.label.padEnd(22)} ${row.detail}`);
    }
    process.exitCode = any ? 0 : 1;
    return;
  }

  const uninstall = opts.command === "uninstall";
  if (!uninstall) installRuntime(opts);

  for (const host of opts.hosts) {
    const def = HOSTS[host];
    const results = uninstall ? uninstallHost(host, opts) : installHost(host, opts);
    const verb = uninstall ? "removed" : "installed";
    console.log(`${def.label}: ${verb}`);
    if (!uninstall && def.note) console.log(`  → ${def.note}`);
    if (host === "dsh" && !uninstall) {
      console.log(`  → build:  cd ${expand(def.target)} && npm install && npm run build`);
      console.log(`  → load:   npx @deepseek-ai/dsh --profile web --patch ${expand(def.target)}/cordis.patch.yml`);
    }
    for (const r of results) {
      if (!r.ok) console.log(`  ! skipped (source missing): ${r.path}`);
    }
  }

  if (!uninstall) {
    console.log(`\nRuntime files: ${RUNTIME_DIR}`);
    console.log("Config:        ~/.hermes/continue_hook.json");
    console.log("Event log:     ~/.hermes/continue-hook-events.jsonl");
  }
}

main();
