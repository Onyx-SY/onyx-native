# -*- coding: utf-8 -*-
"""ConfirmScreen：宽限期内一切按键/点击都不生效（不只挡「是」）。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "ai_tui.py")
s = io.open(P, encoding="utf-8").read()

OLD = '''        def on_button_pressed(self, event):
            if self._grace_blocked() and event.button.id == "yes":
                return                      # 宽限期内点/按「是」不生效（防误触、防连发）
            self.dismiss(event.button.id == "yes")'''

NEW = '''        def on_button_pressed(self, event):
            # 宽限期内**一切**按键/点击都不生效：残留回车既不能「确认」也不能「取消」，
            # 弹窗必须等用户真正做出选择（否则一条合法命令会被误发的回车静默否掉）。
            if self._grace_blocked():
                return
            self.dismiss(event.button.id == "yes")'''

if s.count(NEW) == 1:
    print("SKIP：已改")
elif s.count(OLD) == 1:
    io.open(P + ".tmp", "w", encoding="utf-8").write(s.replace(OLD, NEW, 1))
    os.replace(P + ".tmp", P)
    print("OK 已改：宽限期内全部按键/点击不生效")
else:
    print(f"FAIL：锚点命中 {s.count(OLD)} 次")
    sys.exit(1)
