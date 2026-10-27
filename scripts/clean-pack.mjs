/**
 * Remove build artefacts from the working tree before `npm pack`.
 *
 * npm's `files` allowlist cannot be narrowed by `.npmignore` — a directory
 * listed in `files` is included whole, `__pycache__` and all. This script
 * runs as `prepack`, so the tarball only ever contains sources.
 *
 * It only ever deletes `__pycache__` directories and `*.pyc` / `*.pyo` files
 * inside the package root — nothing else, and never outside the root.
 */
import { readdirSync, rmSync, statSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PRUNE_DIRS = new Set(["__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"]);
const PRUNE_FILES = new Set([".DS_Store"]);
const PRUNE_EXT = new Set([".pyc", ".pyo"]);

let removed = 0;

function walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (PRUNE_DIRS.has(entry.name)) {
        rmSync(full, { recursive: true, force: true });
        removed++;
        continue;
      }
      walk(full);
    } else if (entry.isFile()) {
      const ext = path.extname(entry.name);
      if (PRUNE_FILES.has(entry.name) || PRUNE_EXT.has(ext)) {
        rmSync(full, { force: true });
        removed++;
      }
    }
  }
}

if (!existsSync(ROOT)) {
  console.error("package root not found:", ROOT);
  process.exit(1);
}
walk(ROOT);
console.log(`clean-pack: removed ${removed} build artefact(s)`);
