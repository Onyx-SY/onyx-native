# -*- coding: utf-8 -*-
"""Stage3 补丁 B：把 PlanConfirmScreen 暴露到模块级（供无头测试直接推屏）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")

PATCHES = [
    (
        """_TUI_APP_CLASS = None""",
        """_TUI_APP_CLASS = None
# 计划确认弹窗类（在 _build_tui 内定义，导出供无头测试直接推屏）
_TUI_PLAN_SCREEN = None""",
    ),
    (
        """    _TUI_APP_CLASS = OnyxTUI
    return _TUI_APP_CLASS""",
        """    global _TUI_PLAN_SCREEN
    _TUI_PLAN_SCREEN = PlanConfirmScreen
    _TUI_APP_CLASS = OnyxTUI
    return _TUI_APP_CLASS""",
    ),
]


def main():
    with open(TUI, encoding="utf-8") as f:
        text = f.read()
    for old, new in PATCHES:
        n = text.count(old)
        if n != 1:
            print(f"FAIL: 命中 {n} 次\n{old[:200]}")
            return 1
        text = text.replace(old, new, 1)
    with open(TUI, "w", encoding="utf-8") as f:
        f.write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
