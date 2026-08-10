#!/usr/bin/env python3
"""粘贴合并（快速连续回车检测）：多行粘贴一次发给 AI。

实现：主循环同步（handle_ai/斜杠菜单的信号与同步 prompt 不受影响），
探测在子线程中跑 asyncio（wait_for 超时取消 prompt_async）。
离线验证：mock session 单测 + pty 端到端（不访问网络/AI）。
运行: python3 test/virtual/test_paste_merge.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin import ai_interactive  # noqa: E402


class _FakeSession:
    """模拟探测会话：依次返回预设行，耗尽后挂起（可被 wait_for 取消）。

    prompt_async 由探测子线程调用（asyncio.run 内）。
    """

    def __init__(self, lines, hang="sleep"):
        self._queue = list(lines)
        self._hang = hang
        self._output = None          # 模拟 PromptSession._output
        self.app = "REAL_APP"       # 模拟 PromptSession.app

    async def prompt_async(self, **kwargs):
        if self._queue:
            return self._queue.pop(0)
        if self._hang == "interrupt":
            raise KeyboardInterrupt
        await asyncio.sleep(999)


def test_merge_two_pasted_lines():
    """粘贴两行：第二行在窗口内到达 → 合并为一条。"""
    s = _FakeSession(["line2"])
    out = ai_interactive._collect_paste_lines(s, "line1", window=0.2)
    assert out == "line1\nline2", out
    print("PASS 两行粘贴合并为一条消息")


def test_single_line_no_merge():
    """普通单行：窗口内无后续行 → 原样返回（仅付一次窗口等待）。"""
    s = _FakeSession([])
    out = ai_interactive._collect_paste_lines(s, "hello", window=0.2)
    assert out == "hello", out
    print("PASS 单行消息不受影响")


def test_multiline_first_skips_probe():
    """首行已含换行（Alt+Enter 多行 / 括号粘贴）→ 跳过探测，零延迟，不消费后续输入。"""
    s = _FakeSession(["should-not-be-consumed"])
    out = ai_interactive._collect_paste_lines(s, "a\nb", window=0.2)
    assert out == "a\nb", out
    assert s._queue == ["should-not-be-consumed"], "不应消费后续输入"
    print("PASS 多行首行跳过探测（零延迟）")


def test_slash_command_skips_probe():
    """斜杠命令 → 跳过探测，零延迟。"""
    s = _FakeSession(["x"])
    out = ai_interactive._collect_paste_lines(s, "/help", window=0.2)
    assert out == "/help", out
    assert s._queue == ["x"], "不应消费后续输入"
    print("PASS 斜杠命令跳过探测（零延迟）")


def test_interrupt_stops_merge():
    """合并窗口内用户 Ctrl+C → 停止探测，保留已合并内容。"""
    s = _FakeSession(["line2"], hang="interrupt")
    out = ai_interactive._collect_paste_lines(s, "line1", window=0.2)
    assert out == "line1\nline2", out
    print("PASS Ctrl+C 中断合并，保留已累积内容")


def test_three_lines_merge():
    """三行连续粘贴 → 全部合并。"""
    s = _FakeSession(["l2", "l3"])
    out = ai_interactive._collect_paste_lines(s, "l1", window=0.2)
    assert out == "l1\nl2\nl3", out
    print("PASS 三行粘贴全部合并")


def test_probe_does_not_touch_session_state():
    """探测不修改传入会话的任何属性（独立探测会话设计：主会话零污染）。

    修复目标：PromptSession.app 构造时只建一次，探测若复用主会话 app 会
    污染其渲染状态 → 下一轮提示符消失（双 pty 实测确认）。
    """
    s = _FakeSession(["line2"])
    s._output = "REAL_OUTPUT"
    out = ai_interactive._collect_paste_lines(s, "line1", window=0.2)
    assert out == "line1\nline2", out
    assert s._output == "REAL_OUTPUT", "探测不应修改 _output"
    assert s.app == "REAL_APP", "探测不应修改 app"
    print("PASS 探测不修改主会话状态（独立探测会话）")


def test_e2e_pty_paste_burst_merged():
    """端到端（pty 模拟真实终端）：无括号粘贴突发 → 两行合并为一条。

    主循环同步 prompt() + 子线程探测（与 ai_interactive_session 实际结构一致）。
    """
    import pty
    from prompt_toolkit import PromptSession
    from prompt_toolkit.filters import is_searching
    from prompt_toolkit.input.vt100 import Vt100Input
    from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
    from prompt_toolkit.key_binding.defaults import load_key_bindings

    _kb = KeyBindings()

    @_kb.add('enter', eager=True, filter=~is_searching)
    @_kb.add('c-j', eager=True, filter=~is_searching)
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @_kb.add('escape', 'enter', eager=True, filter=~is_searching)
    def _newline(event):
        event.current_buffer.insert_text('\n')

    master, slave = pty.openpty()

    class _Fd:
        encoding = "utf-8"

        def __init__(self, fd):
            self.fd = fd

        def fileno(self):
            return self.fd

        def isatty(self):
            return True

    session = PromptSession(
        input=Vt100Input(_Fd(slave)), multiline=True,
        key_bindings=merge_key_bindings([_kb, load_key_bindings()]),
    )
    # 独立探测会话：共享 input + DummyOutput（与 ai_interactive_session 一致）
    peek = PromptSession(
        input=session._input, output=ai_interactive._DUMMY_OUTPUT,
        multiline=True, key_bindings=merge_key_bindings([_kb, load_key_bindings()]),
    )
    # 无括号粘贴：两行 + 回车一次突发写入（终端粘贴的原始形态）
    os.write(master, b"paste line 1\rpaste line 2\r")
    first = session.prompt()  # 同步主 prompt（与真实 REPL 一致）
    assert first == "paste line 1", first
    out = ai_interactive._collect_paste_lines(peek, first, window=0.5)
    assert out == "paste line 1\npaste line 2", out
    # 探测后主 prompt 复用正常
    os.write(master, b"next question\r")
    assert session.prompt() == "next question"
    print("PASS 端到端（pty）：粘贴突发两行合并 + 探测后 prompt 复用")


if __name__ == "__main__":
    test_merge_two_pasted_lines()
    test_single_line_no_merge()
    test_multiline_first_skips_probe()
    test_slash_command_skips_probe()
    test_interrupt_stops_merge()
    test_three_lines_merge()
    test_probe_does_not_touch_session_state()
    test_e2e_pty_paste_burst_merged()
    print("\nALL PASS")
