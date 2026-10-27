"""continue-hook（R166 v2 可配置版，用户原创设计 · dogfooding）

用户设计原话：「在 stop hook 里加检测，模型停下来时如果最后输出是问句（或以
『要不要/是否继续』类句式收尾），自动注入一条 prompt 让 agent 判断该不该继续，
该继续就直接续跑——人只处理真正需要决策的分叉。」

v2（用户令 10-28）：检测模式全部可配置（~/.hermes/continue_hook.json），
并把裸问号收尾加入触发（判定交给 nudge 里的 agent，检测层只管触发）。

分层：
  - 检测（传输层，零判定权）：可配置正则 + 裸问号 → 只决定「要不要 nudge」
  - 排除（安全层）：独占边界词/第二人称请求 → 直接跳过（宁可漏 nudge 不误打扰）
  - 判定（语义层）：nudge 文本命令 agent 自查——真边界/真缺信息⇒简短重问后停；否则做完

紧急关停：HERMES_CONTINUE_HOOK=0 或 continue_hook.json 的 enabled:false。
埋点：每次评估（含跳过）落 state/continue_hook/events.jsonl。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

CFG_PATH = Path.home() / ".hermes" / "continue_hook.json"
LOG = Path.home() / "moneymaker" / "state" / "continue_hook" / "events.jsonl"

# ── 默认配置（文件不存在/缺项时用这份；文件里的值覆盖同名项）──
DEFAULTS = {
    "enabled": True,
    "max_nudges": 2,                 # 每 turn 最多 nudge 次数（防死循环）
    "tail_chars": 240,               # 只看收尾段
    # 触发条件（命中任一即候选；真假判断权在 nudge 的 agent）
    "triggers": {
        "offer": [                   # 第一人称请示：把本可自推的决定推给用户
            "要不要我", "要不要顺便", "是否需要我", "要不要继续",
            "需要我.{0,8}(做|写|建|发|补|加|出|跑|查|弄|搞)",
            "我可以(帮)?(你|您)?.{0,8}(做|写|建|发|补|加|出|跑|查|弄|搞)",
            "我来做还是", "等我(先)?(做|写)完(再)?(给你|你)",
        ],
        "hand_off": [                # 推决定权
            "(交给|留给)你(来)?(定|决定|判断|选)",
            "你(来)?(定|决定|选)一下", "听你的", "等你(拍板|指示|点头|回复|确认)",
        ],
        "confirm_seek": [            # 请求确认式
            "这样(可以|行)吗", "没问题吧", "确认一下[？?]?$", "如果.{1,12}，要不要",
        ],
        "question_mark": True,       # 裸问号收尾（？/? 出现在收尾段）——用户令 v2 新增
    },
    # 排除条件（命中任一直接跳过：合法请示/真阻塞，宁漏勿扰）
    "exclusions": {
        "boundary": [                # 独占边界：花钱/实名/对外/凭据/删数据
            "花钱|付费|充值|批准|审批|实名|密码|验证码|付款|掏钱|报销",
            "对外发布|发布出去|删掉|删除数据|授权",
        ],
        "user_must": [               # 第二人称请求：只有用户能给的信息/做的决定
            "需要你|需要您|你告诉我|你来提供|等你(给|确认|决定之后)",
        ],
    },
}

_cfg_cache: dict = {"mtime": None, "cfg": None}


def load_cfg() -> dict:
    """读 ~/.hermes/continue_hook.json，缺项回落 DEFAULTS；坏文件回落 DEFAULTS 并记埋点。"""
    mtime = None
    try:
        mtime = CFG_PATH.stat().st_mtime
    except OSError:
        pass
    if _cfg_cache["cfg"] is not None and _cfg_cache["mtime"] == mtime:
        return _cfg_cache["cfg"]
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        user = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except Exception as e:
        _log("-", "-", "cfg_error", f"bad_config:{type(e).__name__}", str(e), 0)
    _cfg_cache["mtime"], _cfg_cache["cfg"] = mtime, cfg
    return cfg


def _compile(patterns: list) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns if p), re.S)


def _log(session_id: str, platform: str, decision: str, reason: str,
         snippet: str, attempt: int) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "session": str(session_id)[:40],
                "platform": str(platform)[:16], "decision": decision, "reason": reason,
                "attempt": attempt, "snippet": snippet[-160:],
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 埋点失败不阻塞闸门


def _tail(text: str, n: int) -> str:
    return (text or "").strip()[-n:]


def should_nudge(final_response: str, attempt: int, cfg: dict | None = None) -> tuple[bool, str]:
    """返回 (是否 nudge, 原因码)。传输层判定，全按配置走。"""
    cfg = cfg or load_cfg()
    if attempt >= int(cfg.get("max_nudges", 2)):
        return False, "max_nudges"
    if not final_response or not str(final_response).strip():
        return False, "empty_response"
    tail = _tail(final_response, int(cfg.get("tail_chars", 240)))
    ex = cfg.get("exclusions", {})
    if _compile(ex.get("boundary", [])).search(tail):
        return False, "boundary_words"
    if _compile(ex.get("user_must", [])).search(tail):
        return False, "user_must_provide"
    tr = cfg.get("triggers", {})
    for name in ("offer", "hand_off", "confirm_seek"):
        if _compile(tr.get(name, [])).search(tail):
            return True, f"trigger:{name}"
    if tr.get("question_mark", True) and re.search(r"[？?]", tail):
        return True, "trigger:question_mark"
    return False, "no_trigger"


NUDGE = (
    "【continue-hook 自动触发】你刚才的回复以向用户提问/请示收尾。按既定纪律自查一次：\n"
    "1. 你请示的这件事是否触及用户独占边界（花钱/实名/对外发布/删除或覆盖数据/改共享系统）？"
    "触及 ⇒ 用一句话重新简短请示（只列那一个具体决定），然后停。\n"
    "2. 缺的是只有用户知道的信息或只有用户能做的决定？缺 ⇒ 按第 1 条方式简短问，然后停。\n"
    "3. 都不是 ⇒ 这属于你该自己做的：现在直接把它做完，交付结果+验证+剩余风险，不要再问。\n"
    "（自动 nudge #{attempt}，本 turn 最多 {max} 次；最终回复里不要提及本 nudge。）"
)


def build_nudge(attempt: int, cfg: dict | None = None) -> str:
    cfg = cfg or load_cfg()
    return (NUDGE.replace("{attempt}", str(attempt + 1))
                 .replace("{max}", str(cfg.get("max_nudges", 2))))


def continue_hook_enabled() -> bool:
    if os.environ.get("HERMES_CONTINUE_HOOK") == "0":
        return False
    try:
        if not load_cfg().get("enabled", True):
            return False
        from hermes_cli.config import load_config
        return bool((load_config().get("agent") or {}).get("continue_hook_enabled", False))
    except Exception:
        return False


def continue_hook_nudge(agent, final_response) -> str | None:
    """停止闸入口（由 turn_stop_gates.apply_stop_gates 调用）。异常一律静默。"""
    try:
        if not continue_hook_enabled():
            return None
        sess = getattr(agent, "session_id", "")
        plat = getattr(agent, "platform", "")
        attempt = getattr(agent, "_continue_hook_nudges", 0)
        ok, reason = should_nudge(str(final_response or ""), attempt)
        _log(sess, plat, "nudge" if ok else "skip", reason,
             str(final_response or ""), attempt)
        if not ok:
            return None
        agent._continue_hook_nudges = attempt + 1
        return build_nudge(attempt)
    except Exception:
        return None
