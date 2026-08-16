#!/usr/bin/env python3
"""实测 _exec_ai_subprocess 的 Ctrl+C 打断行为（不启动 AI 会话）。

模拟 handle_ai 的信号栈：主线程装了 _on_interrupt（置标志+抛 KI），
然后调用 _exec_ai_subprocess 跑 sleep，1 秒后向自身发 SIGINT。

运行: python3 test/virtual/repro_ctrlc_cmd.py
"""
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from lib.terminal import exe as exe_mod
from lib.terminal.exe import _exec_ai_subprocess


def _run_in_thread(name, fn):
    """在指定线程执行 fn 并收集异常/返回值。"""
    result = {}

    def _wrapped():
        try:
            result["ret"] = fn()
        except BaseException as e:
            result["exc"] = e
        result["done"] = True

    t = threading.Thread(target=_wrapped, name=name, daemon=True)
    t.start()
    return t, result


def _install_ai_handler():
    """模拟 handle_ai 6593 的 _on_interrupt：置标志 + 恢复原 handler + 抛 KI。"""
    _orig = signal.getsignal(signal.SIGINT)

    def _on_interrupt(signum, frame):
        signal.signal(signal.SIGINT, _orig)
        raise KeyboardInterrupt("User interrupted")

    signal.signal(signal.SIGINT, _on_interrupt)
    return _orig


def _send_sigint_after(delay):
    threading.Timer(delay, lambda: os.kill(os.getpid(), signal.SIGINT)).start()


def test_main_thread():
    """主线程执行命令：期望 SIGINT 转发到命令组，命令被杀，返回 -1，全局标志不置位。"""
    out = []
    _install_ai_handler()
    from bin.ai_lib import mcp_state
    mcp_state._AI_INTERRUPTED = False
    _send_sigint_after(1.0)
    t0 = time.time()
    rc = _exec_ai_subprocess("sleep 30", out, None)
    dt = time.time() - t0
    print(f"[主线程] rc={rc} 耗时={dt:.1f}s 输出={out!r} mcp_state._AI_INTERRUPTED={mcp_state._AI_INTERRUPTED}")
    ok = (rc == -1 and dt < 10 and "中断" in "".join(out))
    print(f"        主线程结论: {'✅ 符合预期（命令被中断，AI 标志未置位）' if ok else '❌ 不符合'}")
    return ok


def test_worker_thread():
    """非主线程执行命令（模拟子代理/异步路径）：信号 handler 装不上 → 观察行为。"""
    out = []
    _install_ai_handler()
    from bin.ai_lib import mcp_state
    mcp_state._AI_INTERRUPTED = False
    _send_sigint_after(1.0)
    t0 = time.time()
    t, res = _run_in_thread("cmd-worker", lambda: _exec_ai_subprocess("sleep 30", out, None))
    t.join(timeout=12)
    dt = time.time() - t0
    print(f"[非主线程] 线程仍在运行={t.is_alive()} rc/异常={res.get('ret', res.get('exc'))} 耗时={dt:.1f}s 输出={out!r}")
    print(f"          全局标志 _AI_INTERRUPTED={mcp_state._AI_INTERRUPTED}")
    if t.is_alive():
        print("          ❌ 命令仍在执行（Ctrl+C 打不到它）")
        return False
    return True


if __name__ == "__main__":
    ok1 = test_main_thread()
    print()
    ok2 = test_worker_thread()
    print()
    print("主线程路径:", "✅" if ok1 else "❌", "| 非主线程路径:", "✅" if ok2 else "❌")
