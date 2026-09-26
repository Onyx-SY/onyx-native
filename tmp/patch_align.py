# -*- coding: utf-8 -*-
"""对齐修正：AI 底色块与 ◆ 标签文字左对齐；活动行/流式预览对齐日志内容列。"""
import io

# ── ui.py ──
P1 = "bin/ai_lib/ui.py"
s1 = io.open(P1, encoding="utf-8").read()
old1 = ('        # 缩进 1：底色块自带 padding 0 1 → 正文正好落在标签文字的左对齐列\n'
        '        return _Group(onyx_label(role, label or "AI"), _Pad(block, (0, 0, 0, 1)))')
new1 = ('        # 不再额外缩进：底色块自带 1 格边框 + 1 格 padding → 正文正好落在\n'
        '        # 「◆ 」标签文字的左对齐列（col 2），整块底色与标签左缘齐平。\n'
        '        return _Group(onyx_label(role, label or "AI"), block)')
assert s1.count(old1) == 1, ("ai pad", s1.count(old1))
s1 = s1.replace(old1, new1)
# _Pad 不再使用 → 去掉该局部导入，避免未用变量
old1b = '''        from rich.panel import Panel as _Panel
        from rich.padding import Padding as _Pad
        from rich import box as _box'''
new1b = '''        from rich.panel import Panel as _Panel
        from rich import box as _box'''
assert s1.count(old1b) == 1, ("ai import", s1.count(old1b))
s1 = s1.replace(old1b, new1b)
io.open(P1, "w", encoding="utf-8").write(s1)
print("✅ ui.py 对齐")

# ── ai_tui.py CSS ──
P2 = "bin/ai_tui.py"
s2 = io.open(P2, encoding="utf-8").read()

old2 = '''        #thinking { height: 1; display: none; padding: 0 2; color: $accent;
                    background: $background; }'''
new2 = '''        #thinking { height: 1; display: none; padding: 0 2 0 3; color: $accent;
                    background: $background; }'''
assert s2.count(old2) == 1, ("thinking", s2.count(old2))
s2 = s2.replace(old2, new2)

old3 = '''        #stream { height: auto; max-height: 8; display: none; padding: 0 2;
                  border-top: solid $onyx-rule; background: $background;
                  overflow-x: hidden; }'''
new3 = '''        #stream { height: auto; max-height: 8; display: none; padding: 0 2 0 3;
                  border-top: solid $onyx-rule; background: $background;
                  overflow-x: hidden; }'''
assert s2.count(old3) == 1, ("stream", s2.count(old3))
s2 = s2.replace(old3, new3)

io.open(P2, "w", encoding="utf-8").write(s2)
print("✅ ai_tui.py CSS 对齐")
