# -*- coding: utf-8 -*-
"""精修：AI 正文缩进对齐、文件树/侧栏字形统一、Header 品牌字形。"""
import io

# ── 1) ui.py：AI 底色块缩进 2 → 1（与 ◆ 标签文字左对齐）──
P1 = "bin/ai_lib/ui.py"
s1 = io.open(P1, encoding="utf-8").read()
old1 = '        return _Group(onyx_label(role, label or "AI"), _Pad(block, (0, 0, 0, 2)))'
new1 = ('        # 缩进 1：底色块自带 padding 0 1 → 正文正好落在标签文字的左对齐列\n'
        '        return _Group(onyx_label(role, label or "AI"), _Pad(block, (0, 0, 0, 1)))')
assert s1.count(old1) == 1, ("ui indent", s1.count(old1))
s1 = s1.replace(old1, new1)
io.open(P1, "w", encoding="utf-8").write(s1)
print("✅ ui.py AI 缩进")

# ── 2) ai_tui.py：文件树字形 + Header 品牌字形 ──
P2 = "bin/ai_tui.py"
s2 = io.open(P2, encoding="utf-8").read()

old2 = '''    class CompletionMenu(OptionList):'''
new2 = '''    class OnyxDirectoryTree(DirectoryTree):
        """文件树：用几何字形替代 emoji（等宽对齐，避免缩进错位）。"""

        ICON_NODE = "▸ "
        ICON_NODE_EXPANDED = "▾ "
        ICON_FILE = "· "

    class CompletionMenu(OptionList):'''
assert s2.count(old2) == 1, ("tree class", s2.count(old2))
s2 = s2.replace(old2, new2)

old3 = '''                    yield DirectoryTree(os.getcwd(), id="files")'''
new3 = '''                    yield OnyxDirectoryTree(os.getcwd(), id="files")'''
assert s2.count(old3) == 1, ("tree yield", s2.count(old3))
s2 = s2.replace(old3, new3)

old4 = '''            try:
                self.register_theme(_onyx_theme())
                self.theme = "onyx"
            except Exception:
                pass'''
new4 = '''            try:
                self.register_theme(_onyx_theme())
                self.theme = "onyx"
            except Exception:
                pass
            # 顶栏图标换成品牌菱形（默认是「⭘」；用类型选择器避免引入私有 API）
            try:
                for _hi in self.query("HeaderIcon"):
                    _hi.icon = "◆"
            except Exception:
                pass'''
assert s2.count(old4) == 1, ("header icon", s2.count(old4))
s2 = s2.replace(old4, new4)

io.open(P2, "w", encoding="utf-8").write(s2)
print("✅ ai_tui.py 字形")

# ── 3) lang.json：侧栏小节标题去 emoji ──
P3 = "bin/ai_lib/lang.json"
s3 = io.open(P3, encoding="utf-8").read()
pairs = [
    ('"tui_todo_title": "📋 TODO"', '"tui_todo_title": "▣ TODO"'),
    ('"tui_files_title": "📁 FILES"', '"tui_files_title": "▤ FILES"'),
]
for old, new in pairs:
    n = s3.count(old)
    assert n == 2, (old, n)      # 中英各一处
    s3 = s3.replace(old, new)
io.open(P3, "w", encoding="utf-8").write(s3)
print("✅ lang.json 侧栏标题")
