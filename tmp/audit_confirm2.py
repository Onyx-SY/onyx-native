# -*- coding: utf-8 -*-
"""打印关键分支的完整代码（只看与「默认放行」相关的片段）。"""
RANGES = [
    ("bin/ai_lib/ui.py", 344, 372, "输入可用性等待 / 返回 True 的异常分支"),
    ("bin/ai_lib/ui.py", 386, 420, "_fallback_confirm_dangerous 尾部"),
    ("bin/ai_cmd.py", 2515, 2560, "计划门禁调用点 A"),
    ("bin/ai_cmd.py", 2585, 2620, "计划门禁调用点 B"),
    ("bin/ai_cmd.py", 288, 325, "MCP ask 分支"),
]

for path, a, b, label in RANGES:
    print(f"\n===== {label}  [{path}:{a}-{b}] =====")
    lines = open(path, encoding="utf-8").read().splitlines()
    for i in range(a - 1, min(b, len(lines))):
        print(f"{i + 1:5d}| {lines[i][:170]}")
