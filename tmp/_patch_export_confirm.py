# -*- coding: utf-8 -*-
"""导出 _TUI_CONFIRM_SCREEN / _TUI_CAPTCHA_SCREEN 供无头测试使用。"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "ai_tui.py")
s = io.open(P, encoding="utf-8").read()

a = "_TUI_HIST_SEARCH_SCREEN = None\n"
b = ("_TUI_HIST_SEARCH_SCREEN = None\n"
     "# 确认框 / 验证码框（同上：导出便于无头测试直接推屏）\n"
     "_TUI_CONFIRM_SCREEN = None\n"
     "_TUI_CAPTCHA_SCREEN = None\n")
if "_TUI_CONFIRM_SCREEN" in s:
    print("SKIP 导出")
else:
    assert s.count(a) == 1
    s = s.replace(a, b, 1)

    c = "    global _TUI_PLAN_SCREEN, _TUI_SELECT_SCREEN, _TUI_HIST_SEARCH_SCREEN\n"
    d = ("    global _TUI_PLAN_SCREEN, _TUI_SELECT_SCREEN, _TUI_HIST_SEARCH_SCREEN\n"
         "    global _TUI_CONFIRM_SCREEN, _TUI_CAPTCHA_SCREEN\n")
    assert s.count(c) == 1
    s = s.replace(c, d, 1)

    e = "    _TUI_HIST_SEARCH_SCREEN = HistorySearchScreen\n"
    f = ("    _TUI_HIST_SEARCH_SCREEN = HistorySearchScreen\n"
         "    _TUI_CONFIRM_SCREEN = ConfirmScreen\n"
         "    _TUI_CAPTCHA_SCREEN = CaptchaScreen\n")
    assert s.count(e) == 1
    s = s.replace(e, f, 1)

    tmp = P + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(s)
    os.replace(tmp, P)
    print("OK 已导出")
