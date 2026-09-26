# -*- coding: utf-8 -*-
"""修复 CSS 字符串缺失的闭合三引号（按标记定位，不依赖缩进）。"""
import io

P = "bin/ai_tui.py"
src = io.open(P, encoding="utf-8").read()

anchor = "hist-list { max-height: 12; background: $surface; }"
i = src.index(anchor)
j = src.index("\n", i)                 # 该行行尾
k = src.index("BINDINGS = [", j)       # 紧随其后的 BINDINGS 行（含缩进前导空白）

src = src[:j] + '\n        """\n\n        ' + src[k:]
io.open(P, "w", encoding="utf-8").write(src)
print("fixed")
