#!/usr/bin/env python3
"""中断标志复位测试：Ctrl+C 在 SSE 阶段置位的是 mcp_state 副本，
复位必须覆盖双份——否则标志残留导致后续提问 API 一启动就"自动中断"。

运行: python3 test/virtual/test_interrupt_flag_reset.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin import ai_cmd  # noqa: E402
from bin.ai_lib import mcp_state  # noqa: E402


def test_two_flags_are_independent():
    """ai_cmd._AI_INTERRUPTED 与 mcp_state._AI_INTERRUPTED 是独立绑定（bug 根源）。

    bool 是单例（is 无法区分），改用赋值隔离性验证：置位一方不影响另一方。
    """
    _saved_a, _saved_m = ai_cmd._AI_INTERRUPTED, mcp_state._AI_INTERRUPTED
    try:
        mcp_state._AI_INTERRUPTED = True
        assert ai_cmd._AI_INTERRUPTED is False, "置位 mcp_state 不应影响 ai_cmd 绑定"
        ai_cmd._AI_INTERRUPTED = True
        assert mcp_state._AI_INTERRUPTED is True, "置位 ai_cmd 不应影响 mcp_state 绑定"
    finally:
        ai_cmd._AI_INTERRUPTED = _saved_a
        mcp_state._AI_INTERRUPTED = _saved_m
    print("PASS 两个标志是独立绑定（bug 根源确认）")


def test_reset_clears_both_flags():
    """复位函数必须同时清掉两份标志。"""
    ai_cmd._AI_INTERRUPTED = True
    mcp_state._AI_INTERRUPTED = True
    ai_cmd._reset_ai_interrupt_flags()
    assert ai_cmd._AI_INTERRUPTED is False, "ai_cmd 标志应复位"
    assert mcp_state._AI_INTERRUPTED is False, "mcp_state 标志应复位（此前永不复位）"
    print("PASS 复位覆盖双份标志")


def test_simulated_interrupt_then_reset():
    """模拟真实链路：SSE 阶段 Ctrl+C → _interrupt_handler 只置位 mcp_state 副本
    → 下一次 handle_ai 入口复位 → 不再残留（api.py 检查的正是 mcp_state 副本）。"""
    mcp_state._AI_INTERRUPTED = True   # 模拟 handle_ai SSE 阶段 Ctrl+C（_interrupt_handler）
    ai_cmd._AI_INTERRUPTED = False     # 该阶段 ai_cmd 副本未被置位
    ai_cmd._reset_ai_interrupt_flags()  # 模拟下一次 handle_ai 入口的复位
    assert mcp_state._AI_INTERRUPTED is False, \
        "SSE 副本残留会导致 api.py 立即中断（用户未按 Ctrl+C 却自动中断）"
    print("PASS 模拟 Ctrl+C 后复位：SSE 副本不再残留")


if __name__ == "__main__":
    test_two_flags_are_independent()
    test_reset_clears_both_flags()
    test_simulated_interrupt_then_reset()
    print("\nALL PASS")
