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
import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  copyFileSync,
  rmSync,
  readdirSync,
} from "node:fs";
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
  ".claude/skills",
  ".codex/skills",
  ".cursor/skills",
  ".gemini/skills",
  ".agents/skills",
  ".copilot/skills",
  ".qwen/skills",
  ".config/opencode/skills",
  ".pi/agent/skills",
  ".factory/skills",
  ".grok/skills",
  ".kimi/skills",
  ".antigravity/skills",
  ".devin/skills",
  ".dsh/skills",
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
    selfNamed: true,
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

/** A thrown value as a message, whatever it is: JS lets you throw anything. */
const describeError = (err) =>
  err instanceof Error ? err.message : typeof err === "string" ? err : JSON.stringify(err);
/** The errno code (EACCES, ENOENT) when there is one. */
const errorCode = (err) =>
  err && typeof err === "object" && "code" in err ? String(err.code) : "";

/** A usage error: the command line is wrong. The shell turns it into exit 2. */
class UsageError extends Error {}

/** Split a --host value into host names. */
const parseHostList = (value) =>
  value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

const KNOWN_COMMANDS = new Set(["install", "uninstall", "check", "doctor", "help"]);

/** The flags that only switch something on. */
const SIMPLE_FLAGS = {
  "--dry-run": "dryRun",
  "--json": "json",
  "-h": "help",
  "--help": "help",
};

/** Apply a boolean flag. */
const applySimpleFlag = (opts, flag) => {
  if (flag === "help") opts.command = "help";
  else opts[flag] = true;
};

/**
 * Parse the command line. Pure: returns options, or throws UsageError.
 *
 * The parser never calls process.exit and never prints. That is what makes it
 * testable — a bad flag is a returned error, not a killed process.
 */
/**
 * The flags that take a value. A table, not a chain of ifs: the parser's
 * complexity comes from having one branch per flag, and a table has none.
 */
const VALUE_FLAGS = new Set(["--host", "-H"]);

/** Parse the command line. Pure: returns options, or throws UsageError. */
/**
 * Parse the command line. Pure: returns options, or throws UsageError.
 *
 * The flags are a table, not a chain of ifs: a chain costs one complexity
 * point per flag, and a table costs none. The two flags that need a value get
 * their own branch; the rest is a lookup.
 */
function parseArgs(argv) {
  const opts = { command: "install", hosts: ["all"], dryRun: false };
  const rest = [];

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];

    if (VALUE_FLAGS.has(arg)) {
      const value = String(argv[++i] || "");
      if (!value.trim()) {
        // `--host` with no value installed nothing while still writing the
        // runtime, and exited 0.
        throw new UsageError("--host needs a value: a comma-separated list, or 'all'");
      }
      opts.hosts = parseHostList(value);
      continue;
    }
    if (arg.startsWith("--host=")) {
      opts.hosts = parseHostList(arg.slice(7));
      continue;
    }
    const flag = SIMPLE_FLAGS[arg];
    if (flag) {
      applySimpleFlag(opts, flag);
      continue;
    }
    rest.push(arg);
  }

  if (rest.length) opts.command = rest[0];
  if (!KNOWN_COMMANDS.has(opts.command)) {
    // `uninstal` used to fall through to install: a typo pointed the command in
    // the opposite direction.
    throw new UsageError(
      `unknown command: ${opts.command}\nknown: ${[...KNOWN_COMMANDS].join(", ")}`,
    );
  }
  // "skills" named by hand means "put the portable skill everywhere"; skills
  // that arrive through "all" only go to hosts this machine actually has.
  opts.skillsExplicit = opts.hosts.includes("skills");
  opts.hostsAll = opts.hosts.includes("all");
  if (opts.hostsAll) opts.hosts = [...HOST_NAMES];
  assertKnownHosts(opts.hosts);
  return opts;
}

/** Every host name must be one we know. Throws UsageError otherwise. */
function assertKnownHosts(hosts) {
  const unknown = hosts.filter((h) => !HOSTS[h]);
  if (unknown.length) {
    throw new UsageError(`unknown host(s): ${unknown.join(", ")}\nknown: ${HOST_NAMES.join(", ")}`);
  }
}

/**
 * Parse a config file's text. Pure: text in, result out, no filesystem.
 *
 * The shape of the result is the point of the split:
 *   { kind: "object", data }   — usable
 *   { kind: "empty" }          — a blank file, same as {}
 *   { kind: "broken", reason } — must not be overwritten
 */
function parseConfigText(raw) {
  // A UTF-8 BOM is what Notepad and several Windows tools write; JSON.parse
  // rejects it, which made install impossible on such a file with a message
  // blaming a trailing comma.
  const text = raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw;
  if (!text.trim()) return { kind: "empty" };
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) {
      // A JSON array parses fine and is still not a config file: writing
      // `.hooks` onto an array produces a string that silently drops it, so
      // the install reported success and the hook was never there.
      return { kind: "broken", reason: "is a JSON array, not a config object" };
    }
    return { kind: "object", data: parsed && typeof parsed === "object" ? parsed : {} };
  } catch (err) {
    return { kind: "broken", reason: `is not valid JSON (${describeError(err)})` };
  }
}

/**
 * Read a host config.
 *
 * A missing file is normal ({}); a file we cannot *parse* is not. Swallowing
 * the parse error and then writing our version over it destroyed a user's
 * settings.json — including a JSONC file with comments, which many hosts
 * accept and JSON.parse does not. Callers get the error so they can refuse.
 */
/**
 * What to do about a config we could not parse.
 *
 * Pure: the policy is a value, not a side effect. The shell prints and writes.
 */
function brokenConfigAction(mode, file, reason, { dryRun = false } = {}) {
  if (mode === "ignore") return { kind: "ignore" };
  if (mode === "skip") {
    return { kind: "report", message: `✗ ${file} ${reason} — left untouched.` };
  }
  // "abort": back the file up before anything else touches it, then say what
  // happened. One stable backup name: a timestamped one accumulated on every
  // retry.
  const backup = `${file}.bak`;
  const needsBackup = !dryRun && !existsSync(backup);
  return {
    kind: "backup",
    backup,
    needsBackup,
    message:
      `✗ ${file} ${reason}.\n` +
      `  This host was left untouched` +
      (dryRun ? "." : ` (original saved as ${backup}).`) +
      `\n  Fix it (a trailing comma or a // comment is the usual cause) and re-run.`,
  };
}

/**
 * Read a host config.
 *
 * A missing file is normal ({}); a file we cannot *parse* is not. Swallowing
 * the parse error and then writing our version over it destroyed a user's
 * settings.json — including a JSONC file with comments, which many hosts
 * accept and JSON.parse does not. Callers get the error so they can refuse.
 */
function readJson(file, { onBroken = "abort", dryRun = false } = {}) {
  if (!existsSync(file)) return {};
  const raw = readFileSync(file, "utf8");
  const result = parseConfigText(raw);
  if (result.kind === "object") return result.data;
  if (result.kind === "empty") return {};

  const action = brokenConfigAction(onBroken, file, result.reason, { dryRun });
  if (action.kind === "ignore") return {};
  if (action.kind === "report") {
    console.error(action.message);
    return null;
  }
  if (action.needsBackup) writeFileSync(action.backup, raw, "utf8");
  console.error(action.message);
  return null;
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
  const py = process.platform === "win32" ? "py -3" : process.env.CONTINUE_HOOK_PYTHON || "python3";
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

/**
 * True when a host's config contains one of our hook entries.
 *
 * Pure with respect to the config: it reads the object it is given.
 */
function configHasOurHook(data, kind) {
  const list = data?.hooks?.[hookKeyFor(kind)];
  if (!Array.isArray(list)) return false;
  // Not a substring match: a user's own ~/bin/continue-hook-backup.sh made
  // `check` report an install that did not exist.
  return list.some((g) => isOurs(g) || (Array.isArray(g?.hooks) && g.hooks.some(isOurs)));
}

/**
 * The file that proves a host holds our copy — the one thing a host of each
 * kind has in common. Null for the skills layer, which has many.
 *
 * The same question was asked three times ("is this host installed?", "what do
 * we remove?", "what do we write?") and each answer was a separate if-chain
 * over `kind`. They drifted: `installedHosts` and `checkHost` each carried
 * their own copy of the hook key, and one of them fell out of date. One
 * function, one answer.
 */
function markerPathFor(def) {
  if (def.kind === "file") return expand(def.target);
  if (def.kind === "dsh") return path.join(expand(def.target), "package.json");
  return null;
}

/** The skill files we would have written, for the portable layer. */
const skillMarkerPaths = () =>
  SKILL_DIRS.map((dir) => path.join(expand(dir), "continue-hook", "SKILL.md"));

/**
 * True when this host holds our copy. One answer per kind, shared by
 * `installedHosts`, `checkHost` and the uninstall path.
 */
function hostIsInstalled(def) {
  if (def.kind === "skills") {
    return skillMarkerPaths().some((p) => isOurFile(p));
  }
  const marker = markerPathFor(def);
  if (marker) return isOurFile(marker);
  // JSON-config hosts: the evidence is an entry in their config file.
  const data = readJson(expand(def.config), { onBroken: "ignore" });
  return configHasOurHook(data, def.kind);
}

/** Host directories this tool has actually written a hook into. */
const installedHosts = () => HOST_NAMES.filter((host) => hostIsInstalled(HOSTS[host]));

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
/** The key a host keeps its hooks under. */
const hookKeyFor = (kind) =>
  kind === "cursor-stop"
    ? "stop"
    : kind === "copilot-stop"
      ? "agentStop"
      : kind === "gemini-after"
        ? "AfterAgent"
        : "Stop";

/**
 * Reasons a config cannot safely be patched. Returned instead of throwing so a
 * single bad host cannot stop the others.
 */
function configShapeProblem(data, kind) {
  if (!data || typeof data !== "object" || Array.isArray(data)) return "not a JSON object";
  const hooksProblem = hooksShapeProblem(data.hooks);
  if (hooksProblem) return hooksProblem;
  // No `hooks` member at all is a normal empty config: there is nothing to
  // check, and the install creates the member.
  if (data.hooks === undefined) return null;
  const key = hookKeyFor(kind);
  const existing = data.hooks[key];
  if (existing !== undefined && !Array.isArray(existing) && typeof existing !== "object") {
    return `hooks.${key} is not an array or an object`;
  }
  return null;
}

/**
 * The shape of the `hooks` member, if it is one we cannot patch. Pure.
 *
 * `undefined` means "no hooks yet", which is a normal empty config.
 */
function hooksShapeProblem(hooks) {
  if (hooks === undefined) return null;
  if (hooks === null || typeof hooks !== "object" || Array.isArray(hooks)) {
    return "`hooks` is not an object";
  }
  return null;
}

/**
 * The hook entries in a list that are NOT ours. Pure.
 *
 * Two shapes appear in the wild:
 *   * a group of hooks — {"hooks": [...]} (Claude Code, Gemini)
 *   * a flat entry    — {"command": "..."} (Cursor), {"type","bash"} (Copilot)
 *
 * A flat entry belongs to the user, and dropping it would delete somebody
 * else's configuration. A group is kept only if it still has something in it
 * after our own entries are taken out.
 */
function keepForeignEntries(list) {
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
  return kept;
}

/**
 * The hook entry a host expects. Pure: kind + command in, entry out.
 *
 * Each host has its own shape, and getting it wrong means the host silently
 * ignores the hook — a file that parses, a hook that never runs.
 */
function hookEntryFor(kind, hookCmd) {
  if (kind === "cursor-stop") {
    return { command: hookCmd, loop_limit: 5 };
  }
  if (kind === "copilot-stop") {
    return {
      type: "command",
      bash: hookCmd,
      // Copilot reads `bash` on POSIX and `powershell` on Windows; a bash-only
      // entry silently does nothing there.
      powershell: hookCmd,
      timeoutSec: 30,
    };
  }
  if (kind === "gemini-after") {
    return {
      hooks: [{ name: "continue-hook", type: "command", command: hookCmd, timeout: 30000 }],
    };
  }
  return { hooks: [{ type: "command", command: hookCmd, timeout: 30 }] };
}

function patchHooks(data, kind, hookCmd) {
  const hooks = (data.hooks ||= {});
  const key = hookKeyFor(kind);
  // A single object where the host expects an array: keep it. Overwriting it
  // would delete the user's own hook with no backup.
  const existing = hooks[key];
  const list = Array.isArray(existing)
    ? existing
    : existing && typeof existing === "object"
      ? [existing]
      : [];

  const kept = keepForeignEntries(list);

  // Cursor and Copilot both require a top-level `version`; a file without it
  // is rejected whole, so the hook we just wrote would never load.
  if (kind === "cursor-stop" || kind === "copilot-stop") {
    if (typeof data.version !== "number") data.version = 1;
  }

  kept.push(hookEntryFor(kind, hookCmd));
  hooks[key] = kept;
  return data;
}

/** Remove our hook entries from a config, leaving everything else alone. */
function removeOurs(data, kind) {
  if (!data.hooks) return data;
  const key = hookKeyFor(kind);
  const list = Array.isArray(data.hooks[key]) ? data.hooks[key] : [];
  const kept = keepForeignEntries(list);
  if (kept.length) data.hooks[key] = kept;
  else delete data.hooks[key];
  if (Object.keys(data.hooks).length === 0) delete data.hooks;
  return data;
}

/**
 * The exact line every file we install carries.
 *
 * A substring test on "continue-hook" was not good enough: a user's own
 * extension that merely mentions the name in a comment was treated as ours and
 * deleted by uninstall.
 */
const GENERATED_MARKER = "hermes-continue-hook: generated";

/** True when the file at `file` exists and was written by this tool. */
const isOurFile = (file) => {
  try {
    if (!existsSync(file)) return false;
    return readFileSync(file, "utf8").slice(0, 4000).includes(GENERATED_MARKER);
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
const copyFile = (from, to, dryRun, { own = false } = {}) => {
  if (!existsSync(from)) return { ok: false, reason: "source-missing" };
  // Files under our own runtime directory are ours by definition — checking a
  // marker there produced a fresh .bak on every re-install.
  if (!own && existsSync(to) && !isOurFile(to)) {
    const backup = `${to}.bak-${Date.now()}`;
    if (!dryRun) copyFileSync(to, backup);
    console.error(
      `! ${to} was not written by continue-hook — saved to ${backup} before replacing it`,
    );
  }
  if (!dryRun) {
    mkdirSync(path.dirname(to), { recursive: true });
    copyFileSync(from, to);
  }
  return { ok: true };
};

// ── commands ───────────────────────────────────────────────────────────────
const MANIFEST_NAME = ".installed.json";

function installRuntime(opts) {
  const written = [];
  const put = (from, to) => {
    if (copyFile(from, to, opts.dryRun, { own: true }).ok) {
      written.push(path.relative(RUNTIME_DIR, to).split(path.sep).join("/"));
    }
  };
  for (const file of RUNTIME_FILES) put(path.join(PKG_ROOT, file), path.join(RUNTIME_DIR, file));
  for (const file of TRANSPORT_FILES)
    put(path.join(PKG_ROOT, file.from), path.join(RUNTIME_DIR, file.to));
  for (const host of HOST_NAMES) {
    const def = HOSTS[host];
    if (
      def.kind === "claude-stop" ||
      def.kind === "cursor-stop" ||
      def.kind === "copilot-stop" ||
      def.kind === "gemini-after"
    ) {
      put(path.join(PKG_ROOT, "adapters", def.hook), path.join(RUNTIME_DIR, def.hook));
    }
  }
  // The manifest is what makes "removes only what it installed" true for the
  // runtime directory, where files carry no per-file marker.
  if (!opts.dryRun) {
    mkdirSync(RUNTIME_DIR, { recursive: true });
    writeFileSync(
      path.join(RUNTIME_DIR, MANIFEST_NAME),
      `${JSON.stringify({ files: written }, null, 2)}\n`,
      "utf8",
    );
  }
}

/**
 * The files a host needs from this package, and where they go.
 *
 * Pure: kind + options in, a list of {from, to} out. The copy itself is the
 * shell's job — this says *what*, never *how*.
 */
function installPlanFor(def, opts) {
  if (def.kind === "skills") {
    const dirs = opts.skillsExplicit ? SKILL_DIRS : skillTargetDirs();
    return dirs.map((dir) => ({
      from: path.join(PKG_ROOT, "skills", "continue-hook", "SKILL.md"),
      to: path.join(expand(dir), "continue-hook", "SKILL.md"),
    }));
  }
  if (def.kind === "file") {
    return [{ from: path.join(PKG_ROOT, def.source), to: expand(def.target) }];
  }
  if (def.kind === "dsh") {
    const target = expand(def.target);
    return [
      ...["src/index.ts", "package.json", "cordis.patch.yml", "tsconfig.json"].map((file) => ({
        from: path.join(PKG_ROOT, "adapters", "dsh", file),
        to: path.join(target, file),
      })),
      { from: path.join(PKG_ROOT, "hookctl.py"), to: path.join(target, "hookctl.py") },
      { from: path.join(PKG_ROOT, "detector.py"), to: path.join(target, "detector.py") },
    ];
  }
  return [];
}

/** Install the portable skill layer, the extension files, or the dsh package. */
function installFilesystemHost(def, opts) {
  const plan = installPlanFor(def, opts);
  // The whole dsh directory is ours once its package.json carries the marker,
  // so its support files are copied without the "is this ours" dance.
  const own = def.kind === "dsh" ? isOurFile(path.join(expand(def.target), "package.json")) : false;
  const results = plan.map((step) => ({
    path: step.to,
    ok: copyFile(step.from, step.to, opts.dryRun, { own }).ok,
  }));
  return { status: "ok", results };
}

/** Patch a JSON-config host so it loads our hook. */
function installJsonHost(def, opts) {
  const configPath = expand(def.config);
  const data = readJson(configPath, { dryRun: opts.dryRun });
  if (data === null) {
    // One unparsable config must not abort the other hosts: it is skipped, the
    // user is told, and the run ends with a non-zero status.
    return { status: "skipped", reason: "unparsable config", results: [] };
  }
  const problem = configShapeProblem(data, def.kind);
  if (problem) {
    return { status: "skipped", reason: problem, results: [] };
  }
  const patched = patchHooks(data, def.kind, hookCommand(def.hook));
  // Whatever we hand the host has to survive serialisation — patching a shape
  // we did not anticipate used to produce text without our hook in it, and the
  // install still reported success.
  if (!JSON.stringify(patched).includes("stop_hook.py")) {
    return { status: "failed", reason: "the patched config would lose our hook", results: [] };
  }
  try {
    if (!opts.dryRun) writeJson(configPath, patched);
  } catch (err) {
    // A read-only directory, or a shape we could not represent. Either way the
    // other hosts still deserve their turn.
    return {
      status: "failed",
      reason: `cannot write (${errorCode(err) || describeError(err)})`,
      results: [],
    };
  }
  return { status: "ok", results: [{ path: configPath, ok: true }] };
}

function installHost(host, opts) {
  const def = HOSTS[host];
  if (def.kind === "skills" || def.kind === "file" || def.kind === "dsh") {
    return installFilesystemHost(def, opts);
  }
  return installJsonHost(def, opts);
}

/** Remove the portable skill layer. */
function uninstallSkillsHost(opts) {
  const results = [];
  for (const dir of SKILL_DIRS) {
    const target = path.join(expand(dir), "continue-hook");
    const skill = path.join(target, "SKILL.md");
    if (!existsSync(skill)) continue;
    // A skill directory the user wrote is not ours to delete.
    if (!isOurFile(skill)) {
      console.error(`! ${skill} was not written by continue-hook — left alone`);
      results.push({ path: `${skill} (not ours)`, ok: true });
      continue;
    }
    if (!opts.dryRun) rmSync(target, { recursive: true, force: true });
    results.push({ path: skill, ok: true });
  }
  return { status: "ok", results };
}

/** Remove an extension file we dropped into a host's directory. */
function uninstallFileHost(def, opts) {
  const target = expand(def.target);
  if (!existsSync(target)) {
    return { status: "ok", results: [{ path: `${target} (absent)`, ok: true }] };
  }
  if (!isOurFile(target)) {
    console.error(`! ${target} was not written by continue-hook — leaving it alone`);
    return { status: "ok", results: [{ path: `${target} (not ours)`, ok: true }] };
  }
  // A file carrying our marker is reproducible from the package, so it is
  // deleted outright — and never left beside the host, where OpenCode would
  // load "something.js.removed" as a plugin.
  if (!opts.dryRun) rmSync(target, { force: true });
  return { status: "ok", results: [{ path: target, ok: true }] };
}

/** Remove the dsh plugin package. */
function uninstallDshHost(def, opts) {
  const target = expand(def.target);
  if (!isOurFile(path.join(target, "package.json"))) {
    return { status: "ok", results: [{ path: `${target} (not ours)`, ok: true }] };
  }
  if (!opts.dryRun) rmSync(target, { recursive: true, force: true });
  return { status: "ok", results: [{ path: target, ok: true }] };
}

/** Remove the portable skill layer, an extension file, or the dsh package. */
function uninstallFilesystemHost(def, opts) {
  if (def.kind === "skills") return uninstallSkillsHost(opts);
  if (def.kind === "file") return uninstallFileHost(def, opts);
  return uninstallDshHost(def, opts);
}

/**
 * True when a config still holds something of the user's own.
 *
 * Pure: reads the object. A config this tool created and then emptied should
 * not survive as "{}" — but a config the user had before we arrived must.
 */
function configHasUserContent(cleaned) {
  return Object.keys(cleaned).some((key) => {
    if (key === "version") return false; // we added it
    if (key === "hooks") return Object.keys(cleaned.hooks || {}).length > 0;
    return true;
  });
}

/** Remove our hook entry from a JSON-config host, leaving the rest alone. */
function uninstallJsonHost(def, opts) {
  const configPath = expand(def.config);
  if (!existsSync(configPath)) {
    // "removes only what this tool installed" — an uninstall must never create
    // the host's config file on a machine that never had one.
    return { status: "ok", results: [{ path: `${configPath} (absent)`, ok: true }] };
  }
  const data = readJson(configPath, { dryRun: opts.dryRun, onBroken: "skip" });
  if (data === null) {
    // We cannot see whether our hook is in there, so we cannot claim this host
    // was cleaned — and the caller must not delete the runtime it still points
    // at. That combination left a dead hook that exits 2 (a blocking error in
    // Claude Code) on every stop.
    return { status: "skipped", reason: "unparsable config", results: [] };
  }
  const problem = configShapeProblem(data, def.kind);
  if (problem) {
    return { status: "skipped", reason: problem, results: [] };
  }
  const cleaned = removeOurs(data, def.kind);
  try {
    if (!opts.dryRun) {
      if (!configHasUserContent(cleaned) && def.selfNamed) rmSync(configPath, { force: true });
      else writeJson(configPath, cleaned);
    }
  } catch (err) {
    return {
      status: "failed",
      reason: `cannot write (${errorCode(err) || describeError(err)})`,
      results: [],
    };
  }
  return { status: "ok", results: [{ path: configPath, ok: true }] };
}

function uninstallHost(host, opts) {
  const def = HOSTS[host];
  if (def.kind === "skills" || def.kind === "file" || def.kind === "dsh") {
    return uninstallFilesystemHost(def, opts);
  }
  return uninstallJsonHost(def, opts);
}

function checkHost(host) {
  const def = HOSTS[host];
  // `check` is read-only. readJson() backs the file up and exits 3 on a parse
  // error, which turned a diagnostic into a mutation.
  if (def.kind === "skills") {
    const found = skillMarkerPaths().filter((p) => isOurFile(p));
    return {
      host,
      label: def.label,
      installed: found.length > 0,
      detail: `${found.length}/${SKILL_DIRS.length} skill dirs`,
    };
  }
  const marker = markerPathFor(def);
  if (marker) {
    return { host, label: def.label, installed: isOurFile(marker), detail: marker };
  }
  const configPath = expand(def.config);
  if (!existsSync(configPath)) {
    return { host, label: def.label, installed: false, detail: `${configPath} (absent)` };
  }
  const data = readJson(configPath, { onBroken: "ignore" });
  return {
    host,
    label: def.label,
    installed: configHasOurHook(data, def.kind),
    detail: configPath,
  };
}

function doctor() {
  const rows = [];
  for (const host of HOST_NAMES) {
    const def = HOSTS[host];
    // Detect the *host*, not our own artefact: checking for our config file
    // (or our plugin path) can never be true before the first install, so
    // "--host all" refused to install on a machine that only had the directory.
    const detected =
      def.kind === "skills"
        ? false
        : existsSync(expand(def.detect ?? def.config ?? def.target ?? "."));
    rows.push({ host, label: def.label, detected });
  }
  return rows;
}

const HELP_TEXT = `continue-hook — stop the agent from asking permission for things it should just do.

Usage:
  continue-hook install   [--host <${HOST_NAMES.join("|")}|all>] [--dry-run]
  continue-hook uninstall [--host <${HOST_NAMES.join("|")}|all>]
  continue-hook check     [--host <${HOST_NAMES.join("|")}|all>]
  continue-hook doctor

Shared config: ~/.hermes/continue_hook.json
Event log:    ~/.hermes/continue-hook-events.jsonl`;

/** `check` — report what is installed, per host. */
function commandCheck(opts) {
  let any = false;
  const rows = [];
  for (const host of opts.hosts) {
    const row = checkHost(host);
    any = any || row.installed;
    rows.push(row);
    if (!opts.json) {
      console.log(`${row.installed ? "✓" : "✗"} ${row.label.padEnd(22)} ${row.detail}`);
    }
  }
  // `--json` was parsed and then ignored; scripts had to scrape the table.
  if (opts.json) console.log(JSON.stringify(rows, null, 2));
  process.exitCode = any ? 0 : 1;
}

/** `doctor` — report which supported agents are present on this machine. */
function commandDoctor(opts) {
  const rows = doctor();
  if (opts.json) {
    console.log(JSON.stringify(rows, null, 2));
    return;
  }
  for (const row of rows) {
    console.log(
      `${row.detected ? "●" : "○"} ${row.label.padEnd(22)} ${row.detected ? "detected" : "not found"}`,
    );
  }
}

/**
 * Which hosts an install or uninstall should touch.
 *
 * "all" means every host *on this machine*, which is what the README says.
 * Expanding it to all ten created config files for agents that are not
 * installed. An uninstall is the exception: it has to reach everything we may
 * ever have written, including a config whose host directory the user has
 * since deleted.
 */
function resolveHosts(opts, uninstall) {
  if (!opts.hostsAll) return opts.hosts;
  if (uninstall) return [...HOST_NAMES];
  const detected = doctor()
    .filter((r) => r.detected)
    .map((r) => r.host);
  if (!detected.length) {
    console.error(
      "no supported agent found in this home directory.\n" +
        "  name the hosts explicitly, e.g.  continue-hook install --host claude-code,cursor",
    );
    process.exit(1);
  }
  console.log(`detected: ${detected.join(", ")}`);
  return [...detected, "skills"];
}

/** Print what a host's install or uninstall did. */
function reportHostOutcome(host, def, outcome, opts, uninstall) {
  if (outcome.status === "ok") {
    const verb = uninstall ? "removed" : "installed";
    console.log(`${def.label}: ${opts.dryRun ? `would be ${verb}` : verb}`);
    if (!uninstall && def.note) console.log(`  → ${def.note}`);
    if (host === "dsh" && !uninstall) printDshInstructions(def);
    return;
  }
  // The host was not touched: say so, and keep going.
  console.error(`${def.label}: ${outcome.status} — ${outcome.reason}; nothing was changed for it`);
}

/** The two commands a user has to run to load the dsh plugin. */
function printDshInstructions(def) {
  console.log(`  → build:  cd ${expand(def.target)} && npm install && npm run build`);
  console.log(
    `  → load:   npx @deepseek-ai/dsh --profile web --patch ${expand(def.target)}/cordis.patch.yml`,
  );
}

/** Install or uninstall for one host, and say what happened. */
function runForHost(host, opts, uninstall) {
  const def = HOSTS[host];
  const outcome = uninstall ? uninstallHost(host, opts) : installHost(host, opts);
  reportHostOutcome(host, def, outcome, opts, uninstall);
  for (const r of outcome.results || []) {
    if (!r.ok) console.log(`  ! skipped (source missing): ${r.path}`);
  }
  return outcome.status === "ok";
}

/** The files this tool put in the runtime directory, per the install manifest. */
function runtimeOwnedFiles() {
  const manifest = path.join(RUNTIME_DIR, MANIFEST_NAME);
  try {
    if (existsSync(manifest)) {
      const owned = JSON.parse(readFileSync(manifest, "utf8")).files;
      if (Array.isArray(owned)) return owned;
    }
  } catch {
    // A manifest we cannot read is no manifest.
  }
  // No manifest (an install from before this file existed): fall back to the
  // known file list.
  return [
    ...RUNTIME_FILES,
    ...TRANSPORT_FILES.map((file) => file.to),
    ...HOST_NAMES.map((host) => HOSTS[host].hook).filter(Boolean),
  ];
}

/** Remove a directory and every empty directory under it. */
function pruneEmpty(dir) {
  if (!existsSync(dir)) return;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const child = path.join(dir, entry.name);
    pruneEmpty(child);
    if (existsSync(child) && !readdirSync(child).length) {
      rmSync(child, { recursive: true, force: true });
    }
  }
}

/**
 * Clean up the runtime directory after an uninstall.
 *
 * The runtime holds executable Python, so it should go — but only when nothing
 * still points at it. A host we could not read counts as "still used":
 * deleting the runtime would leave its hook firing into a file that no longer
 * exists (exit 2, which several hosts treat as "block").
 */
function cleanupRuntime(opts, skipped) {
  const stillUsed = [...new Set([...installedHosts(), ...skipped])];
  if (stillUsed.length) {
    console.log(`\nRuntime kept: ${RUNTIME_DIR} (still needed by ${stillUsed.join(", ")})`);
    return;
  }
  if (!existsSync(RUNTIME_DIR) || opts.dryRun) return;

  // Only the files we put there. The dsh target lives *inside* this directory,
  // so removing the tree would delete a user's own dsh plugin. Delete exactly
  // the files this tool put there, from the manifest it wrote at install time.
  // A user's own hookctl.py in the same directory is not in the manifest, so
  // it survives.
  for (const rel of runtimeOwnedFiles()) {
    rmSync(path.join(RUNTIME_DIR, rel), { force: true });
  }
  rmSync(path.join(RUNTIME_DIR, MANIFEST_NAME), { force: true });
  pruneEmpty(RUNTIME_DIR);
  const leftover = existsSync(RUNTIME_DIR) ? readdirSync(RUNTIME_DIR) : [];
  if (!leftover.length) {
    rmSync(RUNTIME_DIR, { recursive: true, force: true });
    console.log(`\nRuntime removed: ${RUNTIME_DIR}`);
  } else {
    console.log(`\nRuntime kept: ${RUNTIME_DIR} (still holds ${leftover.join(", ")})`);
  }
}

/** `install` / `uninstall` — the same procedure, run in one direction or the other. */
function commandInstallOrUninstall(opts) {
  const uninstall = opts.command === "uninstall";
  // Freeze the "which hosts already exist" snapshot before this run creates
  // any directory — otherwise every host looks present by the time the skills
  // layer runs.
  presentHostDirs = SKILL_DIRS.filter((dir) => existsSync(path.join(expand(dir), "..")));

  const hosts = resolveHosts(opts, uninstall);
  if (!uninstall) installRuntime(opts);

  const skipped = [];
  for (const host of hosts) {
    if (!runForHost(host, opts, uninstall)) skipped.push(host);
  }

  if (uninstall) cleanupRuntime(opts, skipped);

  if (skipped.length) {
    console.error(
      `\n${skipped.length} host(s) left untouched: ${skipped.join(", ")}.\n` +
        `  Fix the file(s) above and re-run for those hosts.`,
    );
    process.exitCode = 3;
  }

  if (!uninstall && !skipped.length) {
    console.log(`\nRuntime files: ${RUNTIME_DIR}`);
    console.log("Config:        ~/.hermes/continue_hook.json");
    console.log("Event log:     ~/.hermes/continue-hook-events.jsonl");
  }
}

function main() {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (err) {
    // The shell's job: turn a pure "this command line is wrong" into an exit
    // code. The parser itself never exits.
    if (err instanceof UsageError) {
      console.error(err.message);
      process.exit(2);
    }
    throw err;
  }

  if (opts.command === "help") {
    console.log(HELP_TEXT);
    return;
  }
  if (opts.command === "doctor") {
    commandDoctor(opts);
    return;
  }
  if (opts.command === "check") {
    commandCheck(opts);
    return;
  }
  commandInstallOrUninstall(opts);
}

main();
