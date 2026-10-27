#!/bin/bash
# continue-hook 预防层（pre_llm_call transform）：在用户消息尾附常设提醒，降低请示句产生率
# stdin: pre_llm_call payload JSON。stdout: 修改后的 user_message（transform 契约）
exec python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    msg = d.get("user_message", "")
    if d.get("is_first_turn") or not msg or "CH_PREVENTIVE" in msg or len(msg) > 20000:
        print(msg); sys.exit(0)
    print(msg + "\n\n[CH_PREVENTIVE 常设提醒：不要以『要不要我…？/你定』类请示句收尾把本可自己推进的决定推给用户；除非触及花钱/实名/对外发布/删数据边界或确缺只有用户知道的信息，否则直接做完再交付。]")
except Exception:
    pass
'
