# -*- coding: utf-8 -*-
"""同步 test_tui_layout_headless：todo 行改为「序号 + 状态字形」，思考行改左对齐。"""
import io

P = "test/virtual/test_tui_layout_headless.py"
s = io.open(P, encoding="utf-8").read()


def rep(old, new, tag):
    global s
    n = s.count(old)
    assert n == 1, (tag, n)
    s = s.replace(old, new)
    print("✅", tag)


rep("  2. 思考中 → #thinking 可见且位于「输出框」区域内，文本居中；关闭后隐藏；",
    "  2. 思考中 → #thinking 可见且位于「输出框」区域内，文本左对齐；关闭后隐藏；",
    "docstring 2")

rep("  4. todo 行带数字序号（侧栏）；",
    "  4. todo 行带数字序号 + 状态字形（✓/▸/○，侧栏）；",
    "docstring 4")

rep('''        body = _text(app.query_one("#todo-body"))
        assert body.startswith("1. "), body
        assert "2. " in body and "3. " in body, body
        print(f"PASS todo 带序号：{body!r}")''',
    '''        body = _text(app.query_one("#todo-body"))
        # 视觉语言：序号 + 状态字形（✓ 完成 / ▸ 进行中 / ○ 待办）
        assert "1 ✓" in body and "2 ▸" in body and "3 ○" in body, body
        print(f"PASS todo 带序号与状态字形：{body!r}")''',
    "todo 断言")

rep('        print(f"PASS 思考中显示在输出框内（{thr}）并居中，结束即隐藏")',
    '        print(f"PASS 思考中显示在输出框内（{thr}）左对齐，结束即隐藏")',
    "print 居中→左对齐")

io.open(P, "w", encoding="utf-8").write(s)
print("written")
