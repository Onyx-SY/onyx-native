# -*- coding: utf-8 -*-
"""单独修 CaptchaScreen：宽限窗口内忽略回车提交。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "ai_tui.py")
s = io.open(P, encoding="utf-8").read()

OLD = '''        def on_mount(self):
            self.query_one("#modal-input", Input).focus()

        def on_input_submitted(self, event):
            self.dismiss((event.value or "").strip().upper() == self.code.upper())'''

NEW = '''        _MODAL_KEY_GRACE = 0.30

        def on_mount(self):
            import time as _time
            self._grace_t = _time.monotonic()
            self.query_one("#modal-input", Input).focus()

        def _grace_blocked(self) -> bool:
            import time as _time
            return (_time.monotonic() - getattr(self, "_grace_t", 0.0)) < self._MODAL_KEY_GRACE

        def on_input_submitted(self, event):
            # 宽限窗口内忽略回车：挡掉「弹窗瞬间到达的残留回车」被当成提交验证码
            if self._grace_blocked():
                return
            self.dismiss((event.value or "").strip().upper() == self.code.upper())'''

if s.count(NEW) == 1:
    print("SKIP：已修")
elif s.count(OLD) == 1:
    s = s.replace(OLD, NEW, 1)
    tmp = P + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(s)
    os.replace(tmp, P)
    print("OK CaptchaScreen 已加宽限窗口")
else:
    print(f"FAIL：锚点命中 {s.count(OLD)} 次")
    sys.exit(1)
