# -*- coding: utf-8 -*-
"""kb.py：硬编码键改配置 + 动作表 + 多行编辑区键（用更精确的尾部锚点）。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
p = os.path.join(ROOT, "lib", "terminal", "kb.py")
s = io.open(p, encoding="utf-8").read()

TAIL_ANCHOR = "                buffer.cursor_position = pos + 1\n\n    return kb"
TAIL_NEW = '''                buffer.cursor_position = pos + 1

    # Alt+Enter：进入独立全屏多行编辑区（键名可在 ptk.json 里改）
    @_add("multiline_editor")
    def _(event):
        buffer = event.app.current_buffer
        input_lib_module._request_multiline_editor(buffer.text)
        event.app.exit(result=input_lib_module.MULTILINE_EDITOR_SENTINEL)

    return kb


# 主 REPL 可配置动作表：(动作 id, ptk.json 里的键名, 中文说明, English)
# 供 `config-onyx-repl keys` 与 TUI 按键设置界面使用。
REPL_KEY_ACTIONS = [
    ("history_up", "history_up", "历史上一条", "History previous"),
    ("history_down", "history_down", "历史下一条", "History next"),
    ("prefix_history_up", "prefix_history_up", "前缀历史上一条（Alt+↑）", "Prefix history prev"),
    ("prefix_history_down", "prefix_history_down", "前缀历史下一条（Alt+↓）", "Prefix history next"),
    ("completion_next", "completion_next", "补全下一项", "Completion next"),
    ("completion_prev", "completion_prev", "补全上一项", "Completion previous"),
    ("completion_page_up", "completion_page_up", "补全菜单上翻页", "Completion page up"),
    ("completion_page_down", "completion_page_down", "补全菜单下翻页", "Completion page down"),
    ("completion_menu_up", "completion_menu_up", "补全菜单选择：上", "Completion menu up"),
    ("completion_menu_down", "completion_menu_down", "补全菜单选择：下", "Completion menu down"),
    ("completion_trigger", "completion_trigger", "手动触发补全", "Trigger completion"),
    ("completion_alt_next", "completion_alt_next", "补全下一项（备用键）", "Completion next (alt)"),
    ("completion_alt_prev", "completion_alt_prev", "补全上一项（备用键）", "Completion prev (alt)"),
    ("completion_lock", "completion_lock", "切换补全锁定", "Toggle completion lock"),
    ("clear_screen", "clear_screen", "清屏", "Clear screen"),
    ("multiline_editor", "multiline_editor", "进入全屏多行编辑区", "Open multi-line editor"),
]
'''

if "REPL_KEY_ACTIONS" in s:
    print("SKIP：已存在")
    sys.exit(0)

pairs = [
    ("helper",
     "    kb = KeyBindings()\n\n    # 默认键位映射",
     '''    kb = KeyBindings()

    def _add(action_key: str):
        """按 default_keys（来自 ptk.json）绑定 handler；支持 'escape, space' 多键序列。"""
        raw = default_keys.get(action_key) or ""
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if not parts:
            return lambda f: f
        try:
            return kb.add(*parts)
        except Exception:
            return lambda f: f

    # 默认键位映射'''),
    ("c-up", "    @kb.add('c-up')\n    def _(event):\n        buffer = event.app.current_buffer\n        if buffer.complete_state:\n            buffer.complete_previous()",
     "    @_add(\"completion_menu_up\")\n    def _(event):\n        buffer = event.app.current_buffer\n        if buffer.complete_state:\n            buffer.complete_previous()"),
    ("c-down", "    @kb.add('c-down')\n    def _(event):",
     "    @_add(\"completion_menu_down\")\n    def _(event):"),
    ("c-space", "    @kb.add('c-space')\n    def _(event):",
     "    @_add(\"completion_trigger\")\n    def _(event):"),
    ("c-n", "    @kb.add('c-n')\n    def _(event):",
     "    @_add(\"completion_alt_next\")\n    def _(event):"),
    ("c-p", "    @kb.add('c-p')\n    def _(event):",
     "    @_add(\"completion_alt_prev\")\n    def _(event):"),
    ("lock", "    @kb.add('escape', 'space')\n    def _(event):\n        global _completion_locked",
     "    @_add(\"completion_lock\")\n    def _(event):\n        global _completion_locked"),
    ("tail", TAIL_ANCHOR, TAIL_NEW.rstrip("\n")),
]

for name, old, new in pairs:
    n = s.count(old)
    if n != 1:
        print(f"FAIL {name}：锚点命中 {n} 次")
        sys.exit(1)
    s = s.replace(old, new, 1)
    print(f"OK   {name}")

tmp = p + ".tmp"
io.open(tmp, "w", encoding="utf-8").write(s)
os.replace(tmp, p)
print("已写回 kb.py")
