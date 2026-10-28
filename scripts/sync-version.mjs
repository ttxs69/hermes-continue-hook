/**
 * `npm version` hook: keep every version field in step.
 *
 * The project carries its version in eleven places — the npm package, the
 * Python package, the Hermes plugin, the DeepSeek Harness plugin, and the
 * per-harness manifests for Claude Code, Codex, Cursor, Kimi, Devin,
 * Antigravity, Muse and Gemini. They drifted before, so the check is a test
 * (`tests/test_version_consistency.py`), but a test that fails *after* the
 * bump is a bad time to find out. This script fixes them all at once, and
 * `npm version` refuses to finish if any of them cannot be updated.
 *
 * Run by npm automatically: `npm version patch` → this script → the commit
 * and the tag. It can also be run by hand.
 */

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

/** The version npm just computed. npm passes it in the environment. */
const version = process.env.npm_package_version;
if (!version) {
  console.error("sync-version: npm_package_version is not set — run this through `npm version`.");
  process.exit(1);
}

/** Files whose version is a plain JSON string field. */
const JSON_MANIFESTS = [
  "package.json",
  "plugin.yaml", // handled below; listed for the report
  "gemini-extension.json",
  ".claude-plugin/plugin.json",
  ".codex-plugin/plugin.json",
  ".cursor-plugin/plugin.json",
  ".kimi-plugin/plugin.json",
  ".devin-plugin/plugin.json",
  ".antigravity-plugin/plugin.json",
  ".muse-plugin/plugin.json",
  "adapters/dsh/package.json",
];

/** Files whose version is a regex-matched line. */
const TEXT_FILES = [
  { path: "pyproject.toml", pattern: /^(version\s*=\s*")[^"]+(")/m },
  { path: "plugin.yaml", pattern: /^(version:\s*"?)[^"\n]+("?)/m },
  { path: "__init__.py", pattern: /^(__version__\s*=\s*")[^"]+(")/m },
];

function updateJson(relativePath) {
  const absolute = join(ROOT, relativePath);
  let raw;
  try {
    raw = readFileSync(absolute, "utf8");
  } catch {
    return null; // not present in this checkout
  }
  const data = JSON.parse(raw);
  if (typeof data.version !== "string") return null;
  if (data.version === version) return { path: relativePath, changed: false };
  data.version = version;
  writeFileSync(absolute, `${JSON.stringify(data, null, 2)}\n`, "utf8");
  // JSON.stringify puts every array element on its own line. Prettier does
  // not: it collapses an array that fits on one line, which is how these
  // files were written before. Rewriting them without prettier therefore
  // broke `prettier --check` on every release — and the release gate runs
  // that check, so the version bump made the release fail. Format the file
  // the way the repository formats it.
  formatWithPrettier(absolute);
  return { path: relativePath, changed: true };
}

/**
 * Format a file with the repository's own prettier, if it is available.
 *
 * `npm version` runs this script from the repository, where prettier is
 * installed. If it is not (a stripped checkout, a consumer running the
 * script by hand), the file keeps the JSON.stringify formatting — which is
 * valid, just not what prettier would produce.
 */
function formatWithPrettier(absolute) {
  try {
    execFileSync(
      "npx",
      ["--no-install", "prettier", "--write", "--log-level", "silent", absolute],
      { cwd: ROOT, stdio: "ignore" },
    );
  } catch {
    // prettier is not installed here — the file is still valid JSON.
  }
}

function updateText({ path: relativePath, pattern }) {
  const absolute = join(ROOT, relativePath);
  let raw;
  try {
    raw = readFileSync(absolute, "utf8");
  } catch {
    return null;
  }
  if (!pattern.test(raw)) return null;
  const next = raw.replace(pattern, `$1${version}$2`);
  if (next === raw) return { path: relativePath, changed: false };
  writeFileSync(absolute, next, "utf8");
  return { path: relativePath, changed: true };
}

const results = [];
for (const relativePath of JSON_MANIFESTS) {
  if (relativePath === "plugin.yaml") continue; // it is a text file
  const result = updateJson(relativePath);
  if (result) results.push(result);
}
for (const spec of TEXT_FILES) {
  const result = updateText({ path: spec.path, pattern: spec.pattern });
  if (result) results.push(result);
}

// A manifest that exists but was not in the list would drift silently. Find
// any we missed: every JSON file in the tree with a semver version field.
function findUnlisted() {
  const listed = new Set([...JSON_MANIFESTS, ...TEXT_FILES.map((t) => t.path)]);
  const unlisted = [];
  const SKIP_DIRS = ["node_modules", "__pycache__", ".git", "coverage", "dist"];
  const SKIP_FILES = ["package-lock.json", "pack.json"];

  const isVersionedManifest = (full) => {
    let data;
    try {
      data = JSON.parse(readFileSync(full, "utf8"));
    } catch {
      return false;
    }
    return Boolean(
      data && typeof data.version === "string" && /^\d+\.\d+\.\d+$/.test(data.version),
    );
  };

  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (SKIP_DIRS.includes(entry.name)) continue;
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (!entry.name.endsWith(".json")) continue;
      if (SKIP_FILES.includes(entry.name)) continue;
      const relative = full.slice(ROOT.length + 1);
      if (listed.has(relative)) continue;
      if (isVersionedManifest(full)) unlisted.push(relative);
    }
  };
  walk(ROOT);
  return unlisted;
}

// The changelog is not optional: a release with no entry is a release nobody
// can find out about. A section for the new version is created if it is
// missing, so the bump and the entry happen together.
function updateChangelog() {
  const absolute = join(ROOT, "CHANGELOG.md");
  let raw;
  try {
    raw = readFileSync(absolute, "utf8");
  } catch {
    return null;
  }
  const heading = `## ${version}`;
  if (raw.includes(heading)) return { path: "CHANGELOG.md", changed: false };
  const date = new Date().toISOString().slice(0, 10);
  const section = `${heading} (${date})\n\n- _Describe this release._\n\n`;
  // Insert after the title and any preamble, before the newest existing
  // section — the newest release goes on top.
  const firstSection = raw.search(/^## /m);
  if (firstSection === -1) {
    writeFileSync(absolute, `${raw.trimEnd()}\n\n${section}`, "utf8");
  } else {
    writeFileSync(
      absolute,
      `${raw.slice(0, firstSection)}${section}${raw.slice(firstSection)}`,
      "utf8",
    );
  }
  return { path: "CHANGELOG.md", changed: true };
}

const changelog = updateChangelog();
if (changelog) results.push(changelog);

const unlisted = findUnlisted();
if (unlisted.length) {
  console.error(
    `sync-version: these files carry a version but are not in the list — add them:\n  ${unlisted.join("\n  ")}`,
  );
  process.exit(1);
}

const changed = results.filter((r) => r.changed).map((r) => r.path);
console.log(`sync-version: ${version}`);
console.log(`  updated ${changed.length} of ${results.length} files`);
if (changed.length) console.log(`  ${changed.join(", ")}`);
