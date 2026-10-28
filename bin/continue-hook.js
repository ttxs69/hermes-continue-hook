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
    detect: ".claude",
    hook: "claude-code/stop_hook.py",
  },
  codex: {
    label: "Codex CLI",
    kind: "claude-stop",
    config: ".codex/hooks.json",
    detect: ".codex",
    hook: "codex/stop_hook.py",
    note: "run /hooks once in the Codex CLI to trust the new hook",
  },
  qwen: {
    label: "Qwen Code",
    kind: "claude-stop",
    config: ".qwen/settings.json",
    detect: ".qwen",
    hook: "qwen/stop_hook.py",
  },
  cursor: {
    label: "Cursor",
    kind: "cursor-stop",
    config: ".cursor/hooks.json",
    detect: ".cursor",
    hook: "cursor/stop_hook.py",
  },
  copilot: {
    label: "GitHub Copilot CLI",
    kind: "copilot-stop",
    config: ".copilot/hooks/continue-hook.json",
    detect: ".copilot",
    hook: "copilot/stop_hook.py",
  },
  gemini: {
    label: "Gemini CLI",
    kind: "gemini-after",
    config: ".gemini/settings.json",
    detect: ".gemini",
    hook: "gemini/stop_hook.py",
  },
  opencode: {
    label: "OpenCode",
    kind: "file",
    target: ".config/opencode/plugin/continue-hook.js",
    detect: ".config/opencode",
    source: "adapters/opencode/continue-hook.js",
  },
  pi: {
    label: "Pi",
    kind: "file",
    target: ".pi/agent/extensions/continue-hook.ts",
    detect: ".pi/agent",
    source: "adapters/pi/continue-hook.ts",
  },
  dsh: {
    label: "DeepSeek Harness",
    kind: "dsh",
    target: ".hermes/continue-hook/dsh",
    detect: ".dsh",
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
  // "skills" named by hand means "put the portable skill everywhere"; skills
  // that arrive through "all" only go to hosts this machine actually has.
  opts.skillsExplicit = opts.hosts.includes("skills");
  opts.hostsAll = opts.hosts.includes("all");
  if (opts.hostsAll) opts.hosts = [...HOST_NAMES];
  const unknown = opts.hosts.filter((h) => !HOSTS[h]);
  if (unknown.length) {
    console.error(`unknown host(s): ${unknown.join(", ")}\nknown: ${HOST_NAMES.join(", ")}`);
    process.exit(2);
  }
  return opts;
}

/**
 * Read a host config.
 *
 * A missing file is normal ({}); a file we cannot *parse* is not. Swallowing
 * the parse error and then writing our version over it destroyed a user's
 * settings.json — including a JSONC file with comments, which many hosts
 * accept and JSON.parse does not. Callers get the error so they can refuse.
 */
function readJson(file, { allowBroken = false, dryRun = false } = {}) {
  if (!existsSync(file)) return {};
  let raw = readFileSync(file, "utf8");
  // A UTF-8 BOM is what Notepad and several Windows tools write; JSON.parse
  // rejects it, which made install impossible on such a file with a message
  // blaming a trailing comma.
  if (raw.charCodeAt(0) === 0xfeff) raw = raw.slice(1);
  if (!raw.trim()) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (err) {
    if (allowBroken) return {};
    const backup = `${file}.bak-${Date.now()}`;
    // A dry run must leave the disk untouched — including the backup.
    if (!dryRun) writeFileSync(backup, raw, "utf8");
    console.error(
      `✗ ${file} is not valid JSON (${err.message}).\n` +
        `  Nothing was written. ` +
        (dryRun ? `A real run would copy the original to ${backup}.` : `The original is copied to:\n    ${backup}`) +
        `\n  Fix the file (a trailing comma or a // comment is the usual cause) and re-run.`,
    );
    process.exit(3);
  }
}

function writeJson(file, data) {
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

/**
 * The shell command a host runs.
 *
 * Quoted: an unquoted path breaks on a home directory with a space in it
 * ("/Users/John Smith/…"), and the host then sees a non-zero exit — which
 * several hosts read as "block", the exact opposite of fail-open.
 */
function hookCommand(hookFile) {
  const script = path.join(RUNTIME_DIR, hookFile);
  const quote = (s) => (/[\s"'$`\\]/.test(s) ? `"${s.replace(/"/g, '\\"')}"` : s);
  const py =
    process.platform === "win32"
      ? "py -3"
      : process.env.CONTINUE_HOOK_PYTHON || "python3";
  return `${py} ${quote(script)}`;
}

/** The command a hook entry runs, whichever field the host uses. */
const commandOf = (entry) => {
  if (!entry || typeof entry !== "object") return null;
  const cmd = entry.command ?? entry.bash;
  return typeof cmd === "string" ? cmd : null;
};

/**
 * True only for an entry that runs a script *inside our runtime directory*.
 *
 * Substring matching on "continue-hook" was wrong: it also matched a user's
 * own `~/bin/continue-hook-backup.sh`, whose hook we then deleted on install.
 * The runtime directory is the one path this tool owns, so that is what we
 * match.
 */
/**
 * Where the portable skill goes.
 *
 * Writing into all fifteen skill directories created fifteen host folders in a
 * home directory that may only have one agent installed — the README says
 * "every host on this machine", not "create the hosts". Only hosts that are
 * actually present get the skill; `.agents` is the Agent Skills standard
 * location and is always written.
 */
let presentHostDirs = null;

/**
 * Snapshot which host directories existed *before* we touched anything.
 *
 * Evaluating this lazily was wrong: by the time the skills host runs, the
 * earlier hosts in the same `--host all` run have already created their
 * config directories, so every host looked "present" and the skill landed in
 * fifteen places again.
 */
const skillTargetDirs = () => {
  const always = [".agents/skills"];
  if (presentHostDirs === null) {
    presentHostDirs = SKILL_DIRS.filter((dir) => existsSync(path.join(expand(dir), "..")));
  }
  const present = [...new Set([...always, ...presentHostDirs])];
  return present;
};

const configPath = (def) => expand(def.config ?? def.target ?? ".");

/** Host directories this tool has actually written a hook into. */
const installedHosts = () => {
  const out = [];
  for (const host of HOST_NAMES) {
    const def = HOSTS[host];
    // file / dsh / skills do not live in a JSON hook list, but they still use
    // the shared runtime: the Pi and OpenCode extensions locate hookctl.py
    // inside ~/.hermes/continue-hook/. Skipping them here let a single-host
    // uninstall delete the runtime that other installed hosts were running on.
    if (def.kind === "skills") {
      if (SKILL_DIRS.some((d) => existsSync(path.join(expand(d), "continue-hook", "SKILL.md")))) out.push(host);
      continue;
    }
    if (def.kind === "file") {
      // A user's own extension at that path is not ours; deleting it would be
      // the same data loss as the JSON-hook bug.
      if (isOurFile(expand(def.target))) out.push(host);
      continue;
    }
    if (def.kind === "dsh") {
      if (isOurFile(path.join(expand(def.target), "package.json"))) out.push(host);
      continue;
    }
    const data = readJson(expand(def.config));
    const key =
      def.kind === "cursor-stop" ? "stop" : def.kind === "copilot-stop" ? "agentStop" : def.kind === "gemini-after" ? "AfterAgent" : "Stop";
    const list = data?.hooks?.[key];
    if (!Array.isArray(list)) continue;
    if (list.some((g) => isOurs(g) || (Array.isArray(g?.hooks) && g.hooks.some(isOurs)))) out.push(host);
  }
  return out;
};

const isOurs = (entry) => {
  const cmd = commandOf(entry);
  if (!cmd) return false;
  const normalized = cmd.replace(/\\/g, "/");
  // Our own runtime directory, plus the same layout under a different HOME —
  // a machine that moved its home directory still has to be able to clean up
  // after itself.
  const ours = RUNTIME_DIR.replace(/\\/g, "/");
  if (normalized.includes(`${ours}/`)) return true;
  // A different HOME: same layout, but only if it runs one of our files. This
  // used to be a bare substring, so any command mentioning the directory was
  // treated as ours and deleted.
  return (
    normalized.includes("/.hermes/continue-hook/") &&
    /(stop_hook\.py|hookctl\.py|detector\.py|_common\.py|continue-hook\.(js|ts))\b/.test(normalized)
  );
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
    kept.push({ command: hookCmd, loop_limit: 5 });
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

/** Markers that identify a file as ours, whatever the host. */
const OURS_FILE_MARKERS = ["continue-hook", "hookctl.py"];

/** True when the file at `file` exists and was written by this tool. */
const isOurFile = (file) => {
  try {
    if (!existsSync(file)) return false;
    const head = readFileSync(file, "utf8").slice(0, 4000);
    return OURS_FILE_MARKERS.some((marker) => head.includes(marker));
  } catch {
    return false;
  }
};

/**
 * Copy a file into place, backing up anything that is not already ours.
 *
 * It used to be called copyIfMissing but always overwrote (“if missing” was
 * only true of the *source*). A user's own hand-written extension at the same
 * path was replaced without a word.
 */
const copyFile = (from, to, dryRun) => {
  if (!existsSync(from)) return { ok: false, reason: "source-missing" };
  if (existsSync(to) && !isOurFile(to)) {
    const backup = `${to}.bak-${Date.now()}`;
    if (!dryRun) copyFileSync(to, backup);
    console.error(`! ${to} was not written by continue-hook — saved to ${backup} before replacing it`);
  }
  if (!dryRun) {
    mkdirSync(path.dirname(to), { recursive: true });
    copyFileSync(from, to);
  }
  return { ok: true };
};

// ── commands ───────────────────────────────────────────────────────────────
function installRuntime(opts) {
  for (const file of RUNTIME_FILES) {
    copyFile(path.join(PKG_ROOT, file), path.join(RUNTIME_DIR, file), opts.dryRun);
  }
  for (const file of TRANSPORT_FILES) {
    copyFile(path.join(PKG_ROOT, file.from), path.join(RUNTIME_DIR, file.to), opts.dryRun);
  }
  for (const host of HOST_NAMES) {
    const def = HOSTS[host];
    if (def.kind === "claude-stop" || def.kind === "cursor-stop" || def.kind === "copilot-stop" || def.kind === "gemini-after") {
      copyFile(path.join(PKG_ROOT, "adapters", def.hook), path.join(RUNTIME_DIR, def.hook), opts.dryRun);
    }
  }
}

function installHost(host, opts) {
  const def = HOSTS[host];
  const results = [];

  if (def.kind === "skills") {
    for (const dir of opts.skillsExplicit ? SKILL_DIRS : skillTargetDirs()) {
      const target = path.join(expand(dir), "continue-hook", "SKILL.md");
      const ok = copyFile(path.join(PKG_ROOT, "skills", "continue-hook", "SKILL.md"), target, opts.dryRun).ok;
      results.push({ path: target, ok });
    }
    return results;
  }

  if (def.kind === "file") {
    const target = expand(def.target);
    const ok = copyFile(path.join(PKG_ROOT, def.source), target, opts.dryRun).ok;
    results.push({ path: target, ok });
    return results;
  }

  if (def.kind === "dsh") {
    const target = expand(def.target);
    for (const file of ["src/index.ts", "package.json", "cordis.patch.yml", "tsconfig.json"]) {
      results.push({ path: path.join(target, file), ok: copyFile(path.join(PKG_ROOT, "adapters", "dsh", file), path.join(target, file), opts.dryRun).ok });
    }
    results.push({ path: path.join(target, "hookctl.py"), ok: copyFile(path.join(PKG_ROOT, "hookctl.py"), path.join(target, "hookctl.py"), opts.dryRun).ok });
    results.push({ path: path.join(target, "detector.py"), ok: copyFile(path.join(PKG_ROOT, "detector.py"), path.join(target, "detector.py"), opts.dryRun).ok });
    return results;
  }

  // JSON-config hosts
  const configPath = expand(def.config);
  const data = readJson(configPath, { dryRun: opts.dryRun });
  const hookCmd = hookCommand(def.hook);
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
    if (!existsSync(target)) {
      results.push({ path: `${target} (absent)`, ok: true });
      return results;
    }
    if (!isOurFile(target)) {
      console.error(`! ${target} was not written by continue-hook — leaving it alone`);
      results.push({ path: `${target} (not ours)`, ok: true });
      return results;
    }
    if (!opts.dryRun) rmSync(target, { force: true });
    results.push({ path: target, ok: true });
    return results;
  }
  if (def.kind === "dsh") {
    const target = expand(def.target);
    if (!isOurFile(path.join(target, "package.json"))) {
      results.push({ path: `${target} (not ours)`, ok: true });
      return results;
    }
    if (!opts.dryRun) rmSync(target, { recursive: true, force: true });
    results.push({ path: target, ok: true });
    return results;
  }
  const configPath = expand(def.config);
  if (!existsSync(configPath)) {
    // "removes only what this tool installed" — an uninstall must never create
    // the host's config file on a machine that never had one.
    results.push({ path: `${configPath} (absent)`, ok: true });
    return results;
  }
  const data = readJson(configPath, { allowBroken: true });
  const cleaned = removeOurs(data, def.kind);
  if (!opts.dryRun) writeJson(configPath, cleaned);
  results.push({ path: configPath, ok: true });
  return results;
}

function checkHost(host) {
  const def = HOSTS[host];
  // `check` is read-only. readJson() backs the file up and exits 3 on a parse
  // error, which turned a diagnostic into a mutation.

  if (def.kind === "skills") {
    const found = SKILL_DIRS.filter((d) => existsSync(path.join(expand(d), "continue-hook", "SKILL.md")));
    return { host, label: def.label, installed: found.length > 0, detail: `${found.length}/${SKILL_DIRS.length} skill dirs` };
  }
  if (def.kind === "file") {
    const target = expand(def.target);
    return { host, label: def.label, installed: isOurFile(target), detail: target };
  }
  if (def.kind === "dsh") {
    const target = expand(def.target);
    return { host, label: def.label, installed: isOurFile(path.join(target, "package.json")), detail: target };
  }
  const configPath = expand(def.config);
  if (!existsSync(configPath)) {
    return { host, label: def.label, installed: false, detail: `${configPath} (absent)` };
  }
  const data = readJson(configPath, { allowBroken: true });
  const key =
    def.kind === "cursor-stop" ? "stop" : def.kind === "copilot-stop" ? "agentStop" : def.kind === "gemini-after" ? "AfterAgent" : "Stop";
  const list = data?.hooks?.[key];
  // Not a substring match: a user's own ~/bin/continue-hook-backup.sh made
  // `check` report an install that did not exist.
  const installed = Array.isArray(list) && list.some((g) => isOurs(g) || (Array.isArray(g?.hooks) && g.hooks.some(isOurs)));
  return { host, label: def.label, installed, detail: configPath };
}

function doctor() {
  const rows = [];
  for (const host of HOST_NAMES) {
    const def = HOSTS[host];
    // Detect the *host*, not our own artefact: checking for our config file
    // (or our plugin path) can never be true before the first install, so
    // "--host all" refused to install on a machine that only had the directory.
    const detected =
      def.kind === "skills" ? false : existsSync(expand(def.detect ?? def.config ?? def.target ?? "."));
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
  // Freeze the "which hosts already exist" snapshot before this run creates
  // any directory — otherwise every host looks present by the time the skills
  // layer runs.
  presentHostDirs = SKILL_DIRS.filter((dir) => existsSync(path.join(expand(dir), "..")));

  if (opts.hostsAll) {
    // "all" means every host *on this machine*, which is what the README says.
    // Expanding it to all ten created config files for agents that are not
    // installed.
    if (uninstall) {
      // Uninstall has to reach everything we may ever have written, including
      // a config whose host directory the user has since deleted.
      opts.hosts = [...HOST_NAMES];
    } else {
      const detected = doctor().filter((r) => r.detected).map((r) => r.host);
      if (!detected.length) {
        console.error(
          "no supported agent found in this home directory.\n" +
            "  name the hosts explicitly, e.g.  continue-hook install --host claude-code,cursor",
        );
        process.exit(1);
      }
      opts.hosts = [...detected, "skills"];
      console.log(`detected: ${detected.join(", ")}`);
    }
  }

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

  if (uninstall) {
    // The runtime directory holds executable Python. Leaving it behind means
    // "uninstalled" still leaves runnable code in the home directory.
    const stillUsed = installedHosts();
    if (stillUsed.length) {
      console.log(`\nRuntime kept: ${RUNTIME_DIR} (still used by ${stillUsed.join(", ")})`);
    } else if (existsSync(RUNTIME_DIR) && !opts.dryRun) {
      rmSync(RUNTIME_DIR, { recursive: true, force: true });
      console.log(`\nRuntime removed: ${RUNTIME_DIR}`);
    }
  }

  if (!uninstall) {
    console.log(`\nRuntime files: ${RUNTIME_DIR}`);
    console.log("Config:        ~/.hermes/continue_hook.json");
    console.log("Event log:     ~/.hermes/continue-hook-events.jsonl");
  }
}

main();
