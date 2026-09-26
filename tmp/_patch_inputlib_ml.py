# -*- coding: utf-8 -*-
"""input_lib.py：Alt+Enter 哨兵 + 打开全屏多行编辑区。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
p = os.path.join(ROOT, "lib", "terminal", "input_lib.py")
s = io.open(p, encoding="utf-8").read()

CONSTS = '''# ── Alt+Enter：独立全屏多行编辑区（kb.py 的 multiline_editor 键触发）──
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


def universal_input('''

HANDLE = '''        user_input = session.prompt(prompt_text)

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
'''

if "MULTILINE_EDITOR_SENTINEL" in s:
    print("SKIP：已存在")
    sys.exit(0)

for name, old, new in (("consts", "def universal_input(", CONSTS),
                       ("handle", "        user_input = session.prompt(prompt_text)\n", HANDLE)):
    n = s.count(old)
    if n != 1:
        print(f"FAIL {name}：锚点命中 {n} 次")
        sys.exit(1)
    s = s.replace(old, new, 1)
    print(f"OK   {name}")

tmp = p + ".tmp"
io.open(tmp, "w", encoding="utf-8").write(s)
os.replace(tmp, p)
print("已写回 input_lib.py")
