# -*- coding: utf-8 -*-
"""抽出所有「确认/询问」类函数的返回与异常分支，检查是否存在 fail-open。"""
import re
import sys

ROOT = "."
TARGETS = [
    ("bin/ai_lib/ui.py", 300, 425, "confirm_dangerous(REPL 主体)"),
    ("bin/ai_lib/ui.py", 510, 575, "confirm()"),
    ("bin/ai_lib/ui.py", 426, 505, "text_input()"),
    ("bin/ai_lib/helpers.py", 500, 595, "confirm_dangerous_command"),
    ("bin/ai_tui.py", 694, 800, "TUI _modal / select_option / confirm_dangerous"),
    ("bin/ai_cmd.py", 2510, 2625, "计划门禁调用点"),
]
KEY = re.compile(r"(return|except|raise|timeout|default|EOF|Keyboard|confirm|yes|no\b)")


def show(path, start, end, label):
    print(f"\n===== {label}  [{path}:{start}-{end}] =====")
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except Exception as e:
        print("  读取失败:", e)
        return
    for i in range(start - 1, min(end, len(lines))):
        ln = lines[i]
        if KEY.search(ln):
            print(f"{i + 1:5d}| {ln[:160]}")


for t in TARGETS:
    show(*t)
