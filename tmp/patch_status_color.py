# -*- coding: utf-8 -*-
"""状态栏配色改用 Onyx 调色板（不再用终端 ANSI 名色，保证与主题同源）。"""
import io

P = "bin/ai_tui.py"
src = io.open(P, encoding="utf-8").read()

old = '''                s = status or {}
                wide = self.size.width >= 90
                segs = []   # [(文本, 颜色)]'''
new = '''                from bin.ai_lib.ui import ONYX_PALETTE as _PAL
                s = status or {}
                wide = self.size.width >= 90
                segs = []   # [(文本, 颜色)]'''
assert src.count(old) == 1, ("head", src.count(old))
src = src.replace(old, new)

pairs = [
    ('                    segs.append((cwd, "cyan"))',
     '                    segs.append((cwd, _PAL["primary"]))'),
    ('                    segs.append(("ctx " + _fmt_compact(ctx), "magenta"))',
     '                    segs.append(("ctx " + _fmt_compact(ctx), _PAL["primary"]))'),
    ('                    segs.append(("cache " + f"{s[\'cache_pct\']:.1f}%", "yellow"))',
     '                    segs.append(("cache " + f"{s[\'cache_pct\']:.1f}%", _PAL["warning"]))'),
    ('                    segs.append((str(bal), "green"))',
     '                    segs.append((str(bal), _PAL["success"]))'),
]
for old_s, new_s in pairs:
    n = src.count(old_s)
    assert n == 1, (old_s[:40], n)
    src = src.replace(old_s, new_s)

io.open(P, "w", encoding="utf-8").write(src)
print("ok")
