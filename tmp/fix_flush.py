# -*- coding: utf-8 -*-
"""修复 _QueueStream.flush 的 NameError（buf 未定义）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")

OLD = """    def flush(self) -> None:
        with self._lock:
            if self._buf:
                buf, self._buf = self._buf, ""
        if buf:
            self._put(buf)"""

NEW = """    def flush(self) -> None:
        buf = ""
        with self._lock:
            if self._buf:
                buf, self._buf = self._buf, ""
        if buf:
            self._put(buf)"""

with open(TUI, encoding="utf-8") as f:
    text = f.read()
n = text.count(OLD)
if n != 1:
    print(f"FAIL: 命中 {n} 次")
    sys.exit(1)
with open(TUI, "w", encoding="utf-8") as f:
    f.write(text.replace(OLD, NEW, 1))
print("FIX_OK")
