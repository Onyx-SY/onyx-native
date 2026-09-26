# -*- coding: utf-8 -*-
"""给 OnyxTUI 增加 get_theme_variable_defaults（CSS 里的 $onyx-* 变量早于主题注册解析）。"""
import io

P = "bin/ai_tui.py"
src = io.open(P, encoding="utf-8").read()

old = '''        def __init__(self, session_kwargs, ctx):
            super().__init__()'''
new = '''        def get_theme_variable_defaults(self):
            """CSS 引用的 $onyx-* 变量默认值。

            样式表在 App 构造时就解析，而 onyx 主题要到 on_mount 才注册 ——
            没有这个回退，CSS 会因 `$onyx-rail` 未定义而整表解析失败（界面直接崩）。
            """
            try:
                from bin.ai_lib.ui import ONYX_VARS
                return dict(ONYX_VARS)
            except Exception:
                return {}

        def __init__(self, session_kwargs, ctx):
            super().__init__()'''

assert src.count(old) == 1, src.count(old)
src = src.replace(old, new)
io.open(P, "w", encoding="utf-8").write(src)
print("ok")
