#!/usr/bin/env python3
"""AI 命令执行计时器回归：命令运行期间强制显示「AI 正在执行命令: n 秒」。

覆盖：
  1. REPL 路径：写实时流，每秒刷新，结束时清行（\\r + 清行）；
  2. TUI 路径：推送到活动行（_tui_activity），结束时清空；
  3. 无实时流（None）时不抛异常（回退 sys.__stdout__）。

运行: python3 test/virtual/test_ai_cmd_timer.py
"""
import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def test_repl_timer_prints_and_clears():
    from bin.ai_cmd import AICmdTimer

    buf = io.StringIO()
    t = AICmdTimer(live_stream=buf, tui=False)
    with t:
        assert "AI 正在执行命令: 0 秒" in buf.getvalue(), buf.getvalue()
        time.sleep(1.3)   # 跨过 1s 刷新点
    out = buf.getvalue()
    assert "AI 正在执行命令: 1 秒" in out, out
    assert out.endswith("\r\x1b[K"), repr(out[-8:])   # 结束清行
    print("PASS REPL：计时器每秒刷新并在结束时清行")


def test_tui_timer_uses_activity_line():
    from bin.ai_cmd import AICmdTimer
    import bin.ai_cmd as _m

    seen = []
    orig = _m._tui_activity
    _m._tui_activity = lambda thinking=None, text=None: seen.append(text)
    try:
        with AICmdTimer(live_stream=None, tui=True):
            time.sleep(1.2)
    finally:
        _m._tui_activity = orig
    assert any(s and "AI 正在执行命令" in s for s in seen), seen
    assert seen[-1] == "", f"结束时活动行应清空，实际 {seen[-1]!r}"
    print("PASS TUI：计时器推送到活动行并在结束时清空")


def test_timer_noop_safe_without_stream():
    from bin.ai_cmd import AICmdTimer

    orig = sys.__stdout__
    sys.__stdout__ = io.StringIO()
    try:
        with AICmdTimer(live_stream=None, tui=False):
            time.sleep(0.05)
    finally:
        sys.__stdout__ = orig
    print("PASS 无实时流时不抛异常（回退 sys.__stdout__）")


def main():
    test_repl_timer_prints_and_clears()
    test_tui_timer_uses_activity_line()
    test_timer_noop_safe_without_stream()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
