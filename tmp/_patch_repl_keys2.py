# -*- coding: utf-8 -*-
"""step-2/3（重跑）：kb.py 硬编码键改配置 + 动作表 + 多行编辑区键；input_lib 哨兵。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def patch(path, pairs, tag):
    p = os.path.join(ROOT, path)
    s = io.open(p, encoding="utf-8").read()
    for name, old, new in pairs:
        if new and new in s:
            print(f"SKIP {tag}.{name}：已存在")
            continue
        n = s.count(old)
        if n != 1:
            print(f"FAIL {tag}.{name}：锚点命中 {n} 次")
            sys.exit(1)
        s = s.replace(old, new, 1)
        print(f"OK   {tag}.{name}")
    tmp = p + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(s)
    os.replace(tmp, p)


TAIL_NEW = '''    # Alt+Enter：进入独立全屏多行编辑区（键名可在 ptk.json 里改）
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

patch("lib/terminal/kb.py", [
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
    ("tail", "    return kb", TAIL_NEW.rstrip("\n")),
], "kb")

patch("lib/terminal/input_lib.py", [
    ("consts",
     "def universal_input(",
     '''# ── Alt+Enter：独立全屏多行编辑区（kb.py 的 multiline_editor 键触发）──
MULTILINE_EDITOR_SENTINEL = "\\x00__ONYX_MULTILINE_EDITOR__\\x00"
_ML_EDITOR_REQUEST = {"text": None}


def _request_multiline_editor(text: str) -> None:
    """记录进入编辑区时的缓冲区内容（供 universal_input 取用）。"""
    _ML_EDITOR_REQUEST["text"] = text or ""


def _editor_lang() -> str:
    """尽力取当前界面语言（失败回退 chinese）。"""
    try:
        from bin.manage import get_current_language
        return get_current_language() or "chinese"
    except Exception:
        return "chinese"


def universal_input('''),
    ("handle",
     "        user_input = session.prompt(prompt_text)\n",
     '''        user_input = session.prompt(prompt_text)

        # ── Alt+Enter：进入独立全屏多行编辑区（可滚动回看、能改任意行）──
        if user_input == MULTILINE_EDITOR_SENTINEL:
            try:
                from .mul_line import MultiLineEditor
                _init_text = _ML_EDITOR_REQUEST.get("text") or ""
                _ML_EDITOR_REQUEST["text"] = None
                _edited = MultiLineEditor(syntax=get_terminal_type(),
                                          lang=_editor_lang()).edit(_init_text)
            except Exception:
                _edited = None
            if _edited is None:
                reset_history_index()
                return ""                      # 取消 → 当作空输入
            user_input = _edited
''')], "input_lib")
