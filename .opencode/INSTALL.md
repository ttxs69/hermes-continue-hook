# continue-hook for OpenCode

The canonical plugin file is **`adapters/opencode/continue-hook.js`** — there
is exactly one copy of it in this repository, and the installer copies it
where OpenCode looks for it.

**User-wide (recommended)**

```bash
npx @ttxs69/hermes-continue-hook install --host opencode
# → ~/.config/opencode/plugin/continue-hook.js
```

**Project-local**

```bash
mkdir -p .opencode/plugins
cp path/to/repo/adapters/opencode/continue-hook.js .opencode/plugins/
```

The plugin watches the `session.idle` event, asks the shared decision service
(`hookctl.py`) whether the reply ended by asking permission, and — if so —
sends one continuation prompt into the same session.

Requirements: `python3` on PATH, and `hookctl.py` reachable from the plugin
file (this repository, or `~/.hermes/continue-hook/hookctl.py`).
