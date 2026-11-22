# -*- coding: utf-8 -*-
"""补丁 2：防抖秒数支持显式 0（不再被 `or 3` 覆盖）。"""
import io
import sys

P = "bin/ai_lib/senses.py"
s = io.open(P, encoding="utf-8").read()

old = '''            deb = float(a.get("debounce_sec") or cfg["notify"].get("debounce_sec") or 3)'''
new = '''            _deb = a.get("debounce_sec")
            if _deb is None:
                _deb = cfg["notify"].get("debounce_sec")
            deb = float(_deb) if _deb is not None else 3.0'''
if s.count(old) != 1:
    print("FAIL: debounce block not found")
    sys.exit(1)
s = s.replace(old, new, 1)

io.open(P, "w", encoding="utf-8").write(s)
print("OK")
