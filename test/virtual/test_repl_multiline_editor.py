#!/usr/bin/env python3
"""主 REPL 全屏多行编辑区回归（Alt+Enter 进入）。

覆盖：
  1. 键位：`multiline_editor` 默认 `escape, enter`，且已进 REPL_KEY_ACTIONS；
  2. 接线：kb.py 绑了该键、input_lib 有哨兵与打开编辑区的分支；
  3. 交互（无头 ptk 驱动）：Enter=换行、Alt+Enter=提交、Ctrl+D=提交、Ctrl+C=取消。

运行: python3 test/virtual/test_repl_multiline_editor.py
"""
import contextvars
import io
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def test_key_and_wiring():
    from lib.terminal.com import DEFAULT_PTK_CONFIG
    from lib.terminal.kb import REPL_KEY_ACTIONS
    assert DEFAULT_PTK_CONFIG["key_bindings"].get("multiline_editor") == "escape, enter", \
        "multiline_editor 默认键应为 escape, enter"
    ids = [a[0] for a in REPL_KEY_ACTIONS]
    assert "multiline_editor" in ids, f"REPL_KEY_ACTIONS 缺 multiline_editor：{ids}"
    assert len(ids) == len(set(ids)), "动作 id 必须唯一"
    for aid, pkey, cn, en in REPL_KEY_ACTIONS:
        assert cn and en, f"{aid} 缺双语说明"

    kb_src = io.open(os.path.join(ROOT, "lib", "terminal", "kb.py"), encoding="utf-8").read()
    assert '@_add("multiline_editor")' in kb_src, "kb.py 未绑定 multiline_editor"
    assert "MULTILINE_EDITOR_SENTINEL" in kb_src
    # 原先硬编码的键应已全部改走配置
    for stale in ("@kb.add('c-up')", "@kb.add('c-down')", "@kb.add('c-space')",
                  "@kb.add('c-n')", "@kb.add('c-p')", "@kb.add('escape', 'space')"):
        assert stale not in kb_src, f"kb.py 仍有硬编码键：{stale}"

    il = io.open(os.path.join(ROOT, "lib", "terminal", "input_lib.py"), encoding="utf-8").read()
    assert "MULTILINE_EDITOR_SENTINEL" in il and "MultiLineEditor" in il, \
        "input_lib 未接入多行编辑区"
    print("PASS 键位与接线：multiline_editor=escape,enter，硬编码键已清零")


def _drive(keys, initial="", timeout=8.0):
    """在后台线程里跑 MultiLineEditor，用 pipe input 送按键；返回编辑结果。"""
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from lib.terminal.mul_line import MultiLineEditor

    result = {}
    with create_pipe_input() as inp:
        with create_app_session(input=inp, output=DummyOutput()):
            ctx = contextvars.copy_context()
            ed = MultiLineEditor(syntax="bash", lang="chinese")

            t = threading.Thread(target=lambda: ctx.run(
                lambda: result.__setitem__("v", ed.edit(initial))), daemon=True)
            t.start()
            time.sleep(0.6)
            for k in keys:
                inp.send_text(k)
                time.sleep(0.3)
            t.join(timeout=timeout)
    return result.get("v")


def test_enter_newline_alt_enter_submit():
    v = _drive(["a", "\r", "b", "\x1b\r"])
    assert v == "a\nb", f"Enter 应换行、Alt+Enter 应提交，实际 {v!r}"
    print("PASS Enter=换行 · Alt+Enter=提交")


def test_ctrl_d_submits():
    v = _drive(["x", "y", "\x04"])
    assert v == "xy", f"Ctrl+D 应提交，实际 {v!r}"
    print("PASS Ctrl+D=提交")


def test_ctrl_c_cancels():
    v = _drive(["a", "\x03"])
    assert v is None, f"Ctrl+C 应取消（返回 None），实际 {v!r}"
    print("PASS Ctrl+C=取消")


def test_initial_text_carried_in():
    v = _drive(["\x1b\r"], initial="echo hi")
    assert v == "echo hi", f"应带回已输入内容，实际 {v!r}"
    print("PASS 带入已输入内容（Alt+Enter 直接提交不丢字）")


def main():
    test_key_and_wiring()
    test_enter_newline_alt_enter_submit()
    test_ctrl_d_submits()
    test_cancel = test_ctrl_c_cancels
    test_cancel()
    test_initial_text_carried_in()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
