#!/usr/bin/env python3
"""跨宿主一致性核验 —— 证明 10 个宿主跑的是**同一套规则**，不是 10 份实现。

**为什么有这个脚本（面试向 · 追问 4）**

面试官问「10 个宿主，你怎么保证规则一致？」，
最能加分的回答不是「我做了抽象」，
是**当场证明：全部 adapter 都引用同一个决策入口**（9 个 adapter +
Hermes 本仓 core patch mode = 10 个 enforce 宿主）。

这个脚本做三件事：

1. 列出全部 adapter，并检查它们**是否都走同一个 hookctl / detector 入口**
2. 跑契约测试（`test_dsh_contract` / `test_pi_contract` / `test_commands`）
3. 报告「实现份数」——**真实答案：1 份 detector + 9 个 adapter 薄适配**
   （README Supported agents 表里 enforce 级是 **10 个宿主**；
   第 10 个是 **Hermes Agent 本身 = 本仓的 core patch mode**，不走 adapters/）

**历史上真出过这个问题**（这才是面试里值钱的点）：
patch 模式曾是**手写的第二份实现**，与插件版的 detector 漂移了，
机器上同时跑两套、行为不一致却没人发现。
⇒ 修法：删掉手写那份，改成由共享引擎生成
（`scripts/build-patch-mode.py`）。**「规则只有一处」是可验证的，不是口号。**

用法:
  python3 scripts/check_hosts.py
  python3 scripts/check_hosts.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS = ROOT / "adapters"
# 所有宿主最终都必须落到这两个入口之一
CANONICAL = ("hookctl", "detector", "continue_hook")


def list_adapters() -> list[Path]:
    if not ADAPTERS.exists():
        return []
    return sorted(p for p in ADAPTERS.iterdir() if p.is_dir() and not p.name.startswith("_"))


# ⚠ 扩展名白名单必须含 .ts（dsh / pi 是 TypeScript 适配器）
#   —— 漏了它，检查器会误报「这两个 adapter 没找到规范入口」。
#   **误报比漏报更坏**：它会让你以为代码有问题而去改没问题的代码。
ADAPTER_SUFFIXES = (".py", ".js", ".mjs", ".ts", ".sh", ".json")


def adapter_files(p: Path) -> list[Path]:
    return sorted(f for f in p.rglob("*") if f.is_file() and f.suffix in ADAPTER_SUFFIXES)


# ── 结构化路由判据（R311 红队 E4）──
# ⚠⚠ **不能用「文件里出现过这个词」当路由证明**：
#   红队实测：把 adapter 的全部代码换成一行注释
#   `# this adapter mentions detector and hookctl in a comment only`
#   ⇒ 旧判据照样报「routed_to_canonical: 9, missing_route: []」。
#   ⇒ 结论为真、但脚本证不出来 —— 这正是要防的「我以为 vs 实测」。
#
# ⇒ 改成**结构化匹配**：必须出现**真实的 import / 调用 / require / subprocess**，
#    且**注释行不算**。
ROUTE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Python: from detector import ... / import detector / from hookctl import ...
    (
        "py-import",
        re.compile(
            r"^\s*(?:from|import)\s+(?:[\w.]+\.)?"
            r"(detector|hookctl|continue_hook)\b",
            re.M,
        ),
    ),
    # Python: hookctl.detect(...) / detector.detect(...)
    ("py-call", re.compile(r"^\s*(?:[\w.]+\.)?(?:detect|build_nudge|hookctl)\s*\(", re.M)),
    # JS/TS: require("...detector...") / from "..." import
    (
        "js-require",
        re.compile(
            r"""(?:require\s*\(\s*['"][^'"]*(?:detector|hookctl|continue_hook)[^'"]*['"]"""
            r"""|from\s+['"][^'"]*(?:detector|hookctl|continue_hook))""",
            re.M,
        ),
    ),
    # Shell: python3 .../hookctl.py / .../detector.py
    (
        "sh-exec",
        re.compile(
            r"""(?:python3?|node|bash)\s+[^\n]*(?:hookctl|detector|continue_hook)[^\n]*\.py""",
            re.M,
        ),
    ),
    # TS/JS adapter 的真实写法：拼出 hookctl.py 的路径再 spawn python。
    #   形如 `path.resolve(__dirname, "..", "hookctl.py")` +
    #   `spawn("python3", [hookctlPath, ...])`。
    #   ⚠ 这不是注释 —— 是可执行代码 ⇒ 算真实路由。
    (
        "js-path-spawn",
        re.compile(
            r"""(?:path\.(?:resolve|join)|__dirname)[^\n]*"""
            r"""['"](?:[^'"]*/)*(?:hookctl|detector|continue_hook)\.py['"]""",
            re.M,
        ),
    ),
)


def strip_comments(text: str) -> str:
    """去掉注释与**字符串字面量** —— 里面提到入口**不算路由**。

    ⚠ 红队第二轮 D3/D4 实测的绕过面：
      ① docstring 里的 import 示例：``\"\"\"from detector import build_nudge\"\"\"``
      ② JSON 里的字符串值：``{"__comment__": "from detector import build_nudge"}``
      ⇒ 原实现只去 `#` / `//` 行 ⇒ 两种都算「已路由」。
    ⇒ 现在：**Python 文件用 ast 精确剥掉所有 docstring**；
      **JSON 文件只保留 key**（值是数据，不是代码）。
    """
    import json as _json

    stripped = _strip_line_comments(text)

    # ① Python：用 ast 剥掉所有 docstring（模块/类/函数的）
    if ("<" not in text[:200] and "def " in text) or "import " in text:
        try:
            import ast as _ast

            tree = _ast.parse(text)
            spans = [
                (node.body[0].lineno - 1, node.body[0].end_lineno)
                for node in _ast.walk(tree)
                if isinstance(
                    node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)
                )
                and node.body
                and isinstance(node.body[0], _ast.Expr)
                and isinstance(getattr(node.body[0], "value", None), _ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ]
            if spans:
                lines = stripped.splitlines()
                for start, end in spans:
                    for i in range(start, min(end, len(lines))):
                        lines[i] = ""
                stripped = "\n".join(lines)
        except Exception:
            pass  # 语法不完整 ⇒ 保留原样（下面的行注释剥离已生效）

    # ② JSON：只保留 key（值是数据，不是路由证据）
    if stripped.lstrip().startswith("{"):
        try:
            obj = _json.loads(stripped)
            if isinstance(obj, dict):
                stripped = "\n".join(f'"{k}"' for k in obj)
        except Exception:
            pass

    return stripped


def _strip_line_comments(text: str) -> str:
    """去掉注释行 —— 注释里提到入口**不算路由**（红队实测的绕过手法）。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#") or s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        # Python 行尾注释（粗略：# 前有空格且不在引号里 —— 足够挡住红队的绕过手法）
        if "#" in line and not any(q in line for q in ("'", '"')):
            line = line.split("#", 1)[0]
        out.append(line)
    return "\n".join(out)


def check_routes() -> tuple[dict, list[str]]:
    """每个 adapter 是否**结构化地**引用了规范入口（import / call / require / exec）。

    ⚠ 注释行不算 —— 见 ROUTE_PATTERNS 上面的说明。
    """
    out = {}
    missing = []
    for a in list_adapters():
        hits = set()
        total_lines = 0
        for f in adapter_files(a):
            try:
                raw = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            total_lines += len(raw.splitlines())
            code = strip_comments(raw)
            for kind, pat in ROUTE_PATTERNS:
                if pat.search(code):
                    hits.add(kind)
        out[a.name] = {
            "files": len(adapter_files(a)),
            "routes_to": sorted(hits),
            "lines": total_lines,
        }
        if not hits:
            missing.append(a.name)
    return out, missing


def count_implementation_copies() -> dict:
    """实测「独立实现 detect 决策逻辑的文件数」（R311 红队 E4）。

    ⚠ 原来这里是**字面量 1** —— 这个 demo 的全部卖点是「每个数字当场可复算」，
    而这里唯一一个没有复算的，正是它最核心的那句话。
    ⇒ 改成**实测**：
      ① 扫描仓库里所有定义了 `def detect(` 的文件（独立实现）
      ② 跑 `scripts/build-patch-mode.py --check` 确认 patch-mode 产物是**生成的**
         （生成物不算独立实现 —— 它由 detector.py 派生）
      ③ 输出判定依据
    """
    # ① 谁自己实现了 detect
    impls = []
    for py in sorted(ROOT.rglob("*.py")):
        rel = py.relative_to(ROOT)
        parts = set(rel.parts)
        if parts & {".git", "node_modules", ".venv", "venv", "tests"}:
            continue
        if rel.as_posix() == "patch-mode/continue_hook.py":
            continue  # 生成物，下面单独判定
        try:
            src = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if re.search(r"^\s*def\s+detect\s*\(", src, re.M):
            impls.append(rel.as_posix())

    # ② patch-mode 是不是生成物
    patch_fresh = False
    patch_detail = ""
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build-patch-mode.py"), "--check"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT),
        )
        patch_fresh = r.returncode == 0
        patch_detail = (
            (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else ""
        )
    except Exception as e:
        patch_detail = f"检查失败: {e}"

    return {
        "independent_detector_files": impls,
        "independent_copies": len(impls),
        "patch_mode_is_generated": patch_fresh,
        "patch_mode_check": patch_detail,
        "basis": (
            "扫描仓库内所有 `def detect(` 定义（排除 tests/ 与生成物 patch-mode/）；"
            "patch-mode 由 scripts/build-patch-mode.py 从 detector.py 生成，"
            "用 --check 验证产物新鲜 ⇒ 不算第二份实现。"
        ),
    }


def readme_enforce_hosts() -> dict:
    """从 README 的「Supported agents」表**解析** enforce 级宿主数（R311 红队 E4）。

    ⚠ 原来 `enforce_hosts = n + 1` 是「从 adapter 目录数推出来的」——
    加一个 adapter 目录它会报 11，而 README 仍写 10 ⇒ 二者从未互相校验。
    ⇒ 改成解析 README，再与 adapter 数交叉核对，不一致就报差异。
    """
    readme = ROOT / "README.md"
    if not readme.exists():
        return {"found": False, "enforce": None, "note": "README.md 不存在"}
    text = readme.read_text(encoding="utf-8", errors="ignore")
    enforce = 0
    in_table = False
    for line in text.splitlines():
        if "Supported agents" in line or ("支持" in line and "|" in line):
            in_table = True
        if not in_table:
            continue
        if not line.strip().startswith("|"):
            if enforce:
                break
            continue
        cells = [c.strip().lower() for c in line.strip().strip("|").split("|")]
        if any(c in {"enforce", "强制", "✅"} for c in cells) and len(cells) >= 2:
            # 表头行也要排除
            if any("---" in c or c == "agent" or "宿主" in c for c in cells):
                continue
            if any("enforce" in c for c in cells):
                enforce += 1
    return {
        "found": True,
        "enforce": enforce or None,
        "note": "从 README「Supported agents」表解析 enforce 行数",
    }


def adapter_layer_lines() -> dict:
    """适配层行数口径 —— 必须包含 adapters/ 根下的共享模块（R311 红队 E4）。

    ⚠ 原来只遍历各 adapter 子目录 ⇒ 漏了 `adapters/_common.py`（285 行，
    被 6 个 adapter import）⇒ 少报（方向上对自己不利，但口径要说清）。
    """
    per_adapter = 0
    for a in list_adapters():
        for f in adapter_files(a):
            try:
                per_adapter += len(f.read_text(encoding="utf-8", errors="ignore").splitlines())
            except Exception:
                pass
    shared = 0
    shared_files = []
    if ADAPTERS.exists():
        for f in ADAPTERS.iterdir():
            if f.is_file() and f.suffix in ADAPTER_SUFFIXES:
                shared_files.append(f.name)
                try:
                    shared += len(f.read_text(encoding="utf-8", errors="ignore").splitlines())
                except Exception:
                    pass
    return {
        "adapter_subdir_lines": per_adapter,
        "shared_module_lines": shared,
        "shared_module_files": sorted(shared_files),
        "total": per_adapter + shared,
        "note": "总口径 = 各 adapter 子目录 + adapters/ 根下的共享模块（_common.py 等）",
    }


def run_contract_tests() -> dict:
    tests = [
        "tests.test_dsh_contract",
        "tests.test_pi_contract",
        "tests.test_commands",
        "tests.test_host_protocol",
    ]
    results = {}
    for t in tests:
        try:
            r = subprocess.run(
                [sys.executable, "-m", "unittest", t],
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(ROOT),
            )
            ok = r.returncode == 0
            ran = ""
            m = re.search(r"Ran (\d+) tests?", r.stderr or "")
            if m:
                ran = m.group(1)
            results[t] = {"ok": ok, "ran": ran}
        except Exception as e:
            results[t] = {"ok": False, "error": str(e)[:60]}
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    adapters, missing = check_routes()
    tests = run_contract_tests()
    impl = count_implementation_copies()
    readme = readme_enforce_hosts()
    layer = adapter_layer_lines()
    n = len(adapters)
    all_ok = all(v["ok"] for v in tests.values())
    routed = n - len(missing)
    # ⚠⚠ patch-mode 不是生成物 ⇒ **必须失败**（红队第二轮 D6）：
    #   原来它只被打印成「❌ 否」却仍然 exit 0 ⇒ 这道门是空的。
    #   而且逻辑是**反的**：「❌ 否 ⇒ 不算第二份实现」——
    #   它不是生成物，恰恰**就是**第二份实现。
    patch_ok = bool(impl["patch_mode_is_generated"])

    # enforce 口径：README 解析出来的数（若能解析）否则退回 n+1
    enforce = readme.get("enforce") or (n + 1)
    enforce_mismatch = readme.get("enforce") is not None and readme["enforce"] != n + 1

    result = {
        "adapters": n,
        "enforce_hosts": enforce,
        "enforce_hosts_source": (
            "README「Supported agents」表解析"
            if readme.get("enforce")
            else "adapter 目录数 +1（Hermes 本仓）"
        ),
        "enforce_hosts_mismatch": enforce_mismatch,
        "enforce_hosts_note": (
            "README Supported agents 表：enforce 级 10 个宿主；"
            "adapter 目录 9 个，第 10 个是 Hermes 本仓自身（core patch mode）。"
        ),
        "routed_to_canonical": routed,
        "missing_route": missing,
        "route_check_method": "结构化匹配（import / call / require / exec），**注释行不算**",
        "adapter_layer_lines": layer,
        "contract_tests": tests,
        "all_contract_tests_pass": all_ok,
        "implementation_copies": impl["independent_copies"],
        "implementation_copies_detail": impl,
        "note": (
            f"{impl['independent_copies']} 份独立 detector 实现 + N 个薄适配（不是 N 份实现）。"
            "历史上 patch 模式曾手写第二份实现导致行为漂移 ⇒ 已改为共享引擎生成。"
        ),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all_ok and not missing and patch_ok else 78

    # ⭐ 口径：README「Supported agents」表里 **enforce 级 = 10 个宿主**；
    #   adapter 目录只有 9 个，因为 **Hermes Agent 本身是本仓的 origin**
    #   （core patch mode，不走 adapters/）。
    #   ⇒ 不要写「9 个 adapter = 9 个宿主」。
    print("=" * 70)
    print("  跨宿主一致性核验 —— 10 个 enforce 宿主，1 份规则")
    print("=" * 70)
    print()
    print(f"  adapter 数: {n}（+ Hermes 本仓 core patch mode = {enforce} 个 enforce 宿主）")
    print(
        f"  全部路由到规范入口: {routed}/{n}    "
        f"适配层总行数: {layer['total']}"
        f"（子目录 {layer['adapter_subdir_lines']} + 共享模块 {layer['shared_module_lines']}）"
    )
    print("  路由判据: 结构化匹配（import/call/require/exec），**注释行不算**")
    if enforce_mismatch:
        print(
            f"  ⚠ enforce 口径不一致：README 解析 = {readme['enforce']}，"
            f"adapter 数 +1 = {n + 1} ⇒ 请同步 README"
        )
    print()
    print(f"  {'adapter':<14} {'文件':>4} {'行数':>6}  路由到")
    for name, v in adapters.items():
        print(
            f"  {name:<14} {v['files']:>4} {v['lines']:>6}  "
            f"{', '.join(v['routes_to']) or '❌ 未找到规范入口'}"
        )
    print()
    print("  契约测试：")
    for t, v in tests.items():
        print(
            f"    {'✅' if v['ok'] else '❌'} {t:<32} "
            f"{'Ran ' + v.get('ran', '?') + ' tests' if v.get('ran') else v.get('error', '')}"
        )
    print()
    if missing:
        print(f"  ⛔ 这些 adapter 没找到规范入口：{missing}")
    print(
        f"  ⇒ 实现份数：**{impl['independent_copies']}**（实测：扫仓库内 `def detect(` 定义）"
        f" + 薄适配层，不是 {enforce} 份实现。"
    )
    print(f"     实测到定义 detect 的文件：{impl['independent_detector_files'] or '（无）'}")
    if impl["patch_mode_is_generated"]:
        print("     patch-mode 是生成物：✅ 是（build-patch-mode.py --check 通过）⇒ 不算第二份实现")
    else:
        # ⚠ 逻辑别写反：它**不是**生成物 ⇒ 它**就是**第二份实现（手写的）。
        print("     patch-mode 不是生成物：❌ 否 ⇒ **它就是第二份实现**（手写的，会漂移）")
        print(f"       详情：{impl['patch_mode_check']}")
    print("     历史上 patch 模式曾手写第二份 ⇒ 与插件版漂移 ⇒ 已改为共享引擎生成。")
    print()
    print("=" * 70)
    print("  复算：python3 scripts/check_hosts.py")
    print("=" * 70)
    # ⚠ patch-mode 过期 ⇒ 失败（红队第二轮 D6）
    return 0 if all_ok and not missing and patch_ok else 78


if __name__ == "__main__":
    sys.exit(main())
