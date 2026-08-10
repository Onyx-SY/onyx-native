# -*- coding: utf-8 -*-
"""
端到端回归：真实 ptk 会话中「Up 键回填多行命令 → Enter 提交」的完整链路。
- Up 后缓冲区回填原始多行命令（含真实换行）
- ptk 渲染为多行（无 ^J）
- Enter 后完整多行命令原样提交，不进入续行输入循环
- 与历史文件 JSON 存储格式兼容
"""
import io
import os
import sys
import re
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output.vt100 import Vt100_Output, Size
    from prompt_toolkit import PromptSession
    HAS_PTK = True
except Exception as e:
    print(f"[warn] 无法导入 prompt_toolkit（{e}），跳过 e2e 测试")
    HAS_PTK = False

if HAS_PTK:
    from lib.terminal import input_lib as il
    from lib.terminal.kb import create_key_bindings

RAW = "cat > a.txt << EOF\n12\nls\nbd\nEOF"


def test_up_enter_multiline():
    """Up → Enter：原始多行完整提交，渲染无 ^J"""
    il._HISTORY_BUFFER = [RAW, "echo hi"]
    il._HISTORY_INITIALIZED = True  # 阻止从真实历史文件重载
    il.reset_history_index()

    kb = create_key_bindings(sys_type="Linux", terminal_type="bash")
    out = io.StringIO()
    _orig_prompt = il.prompt

    with create_pipe_input() as inp:
        session = PromptSession(input=inp, output=Vt100_Output(out, lambda: Size(rows=24, columns=80)))

        def feed():
            time.sleep(0.12)
            inp.send_text("\x1b[A")   # Up
            time.sleep(0.3)
            inp.send_text("\r")       # Enter
        threading.Thread(target=feed, daemon=True).start()

        def _prompt(message="", **kwargs):
            kwargs.pop("input", None)
            kwargs.pop("output", None)
            return session.prompt(message, **kwargs)

        il.prompt = _prompt
        try:
            result = il.universal_input(
                prompt_func=lambda: "> ",
                user_home_dir="",
                language="chinese",
            )
        finally:
            il.prompt = _orig_prompt
            il.reset_history_index()

    # 提交结果 = 完整原始多行
    assert result == RAW, f"提交结果应为完整多行，got {result!r}"

    # 渲染无 ^J、无压平
    assert "^J" not in out.getvalue(), "渲染不应出现 ^J"
    print("  [OK] Up→Enter 多行命令完整提交，渲染无 ^J")


def test_storage_roundtrip():
    """JSON 存储往返后仍可原样回填"""
    encoded = il._encode_multiline_for_storage(RAW)
    decoded = il._decode_multiline_from_storage(encoded)
    assert decoded == RAW, f"存储往返不一致: {decoded!r}"

    # 用往返后的条目做导航
    il._HISTORY_BUFFER = [decoded, "ls"]
    il._HISTORY_INITIALIZED = True
    il.reset_history_index()
    t1, _ = il.handle_up_arrow_normal("")
    assert t1 == RAW, f"往返后 Up 回填不一致: {t1!r}"
    print("  [OK] JSON 存储往返 + Up 回填一致")


def test_second_up_continues():
    """Up 两次继续导航（多行条目不阻断）"""
    il._HISTORY_BUFFER = [RAW, "for i in 1 2 3; do\n  echo $i\ndone", "ls"]
    il._HISTORY_INITIALIZED = True
    il.reset_history_index()
    t1, _ = il.handle_up_arrow_normal("")
    assert t1 == RAW
    t2, _ = il.handle_up_arrow_normal(t1)
    assert t2 == "for i in 1 2 3; do\n  echo $i\ndone", f"up2 got {t2!r}"
    t3, _ = il.handle_up_arrow_normal(t2)
    assert t3 == "ls", f"up3 got {t3!r}"
    d1, _ = il.handle_down_arrow_normal(t3)
    assert d1 == "for i in 1 2 3; do\n  echo $i\ndone", f"down1 got {d1!r}"
    print("  [OK] 连续 Up/Down 导航不受多行条目影响")


def main():
    if not HAS_PTK:
        print("SKIPPED")
        return
    test_storage_roundtrip()
    test_second_up_continues()
    test_up_enter_multiline()
    print("ALL E2E TESTS PASSED")


if __name__ == "__main__":
    main()
