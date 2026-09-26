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

RAW = "cat > a.txt << EOF\n12\nls\nbd\nEOF"


def test_up_enter_multiline():
    """Up → Enter：原始多行完整提交，渲染无 ^J"""
    il._HISTORY_BUFFER = [RAW, "echo hi"]
    il._HISTORY_INITIALIZED = True  # 阻止从真实历史文件重载
    il.reset_history_index()

    out = io.StringIO()
    _RealPromptSession = il.PromptSession
    _saved_cache = dict(il._SESSION_CACHE)

    with create_pipe_input() as inp:
        # universal_input 内部自建并缓存 PromptSession（真实输入路径），
        # 这里把 input/output 注入进去，让该会话走管道而非真实 stdin，
        # 从而可以在无人值守下驱动 Up/Enter 并断言真实提交结果。
        _captured = {}

        def _factory(*args, **kwargs):
            _captured.update(kwargs)
            kwargs.setdefault("input", inp)
            kwargs.setdefault("output", Vt100_Output(out, lambda: Size(rows=24, columns=80)))
            return _RealPromptSession(*args, **kwargs)

        il.PromptSession = _factory
        il._SESSION_CACHE["key"] = None
        il._SESSION_CACHE["session"] = None

        def feed():
            time.sleep(0.15)
            inp.send_text("\x1b[A")   # Up
            time.sleep(0.3)
            inp.send_text("\r")       # Enter
        threading.Thread(target=feed, daemon=True).start()

        try:
            result = il.universal_input(
                prompt_func=lambda: "> ",
                user_home_dir="",
                language="chinese",
            )
        finally:
            il.PromptSession = _RealPromptSession
            il._SESSION_CACHE.clear()
            il._SESSION_CACHE.update(_saved_cache)
            il.reset_history_index()

    # Ctrl+R 反查的接线回归：真实会话必须挂上项目历史桥接（跨会话历史）
    assert _captured.get("history") is il._PTK_HISTORY, "PromptSession 未挂 OnyxHistory，Ctrl+R 将只覆盖本次运行"
    assert _captured.get("search_ignore_case") is True, "Ctrl+R 搜索应忽略大小写"

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
