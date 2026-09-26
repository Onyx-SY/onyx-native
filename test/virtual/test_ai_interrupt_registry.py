#!/usr/bin/env python3
"""AI 命令中断注册表回归：Ctrl+C 逐级升级（SIGINT→SIGTERM→SIGKILL）。

覆盖：
  1. 无活跃命令 → interrupt_active_ai_cmds() 返回 0（调用方应改走「打断 AI」）；
  2. 有活跃命令 → 重复调用逐级升级；已退出的进程被跳过。

运行: python3 test/virtual/test_ai_interrupt_registry.py
"""
import os
import signal
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


class _FakeProc:
    """假进程：不存在的 pid → os.getpgid 抛错 → 走 send_signal（记录信号）。"""
    def __init__(self, alive=True):
        self.pid = 999_999_999
        self.signals = []
        self._alive = alive

    def poll(self):
        return None if self._alive else 0

    def send_signal(self, sig):
        self.signals.append(sig)


def test_empty_registry_returns_zero():
    from lib.terminal import exe
    exe._AI_ACTIVE_CMDS.clear()
    assert exe.interrupt_active_ai_cmds() == 0
    print("PASS 无活跃命令 → 返回 0（调用方改走打断 AI 路径）")


def test_escalation():
    from lib.terminal import exe
    exe._AI_ACTIVE_CMDS.clear()
    p = _FakeProc()
    exe.register_ai_cmd(p)
    try:
        for _ in range(4):
            assert exe.interrupt_active_ai_cmds() == 1
        assert p.signals == [signal.SIGINT, signal.SIGTERM,
                             signal.SIGKILL, signal.SIGKILL], p.signals
    finally:
        exe.unregister_ai_cmd(p)
    assert exe.interrupt_active_ai_cmds() == 0
    print("PASS 重复 Ctrl+C 逐级升级 SIGINT→SIGTERM→SIGKILL（并封顶在 SIGKILL）")


def test_dead_proc_skipped():
    from lib.terminal import exe
    exe._AI_ACTIVE_CMDS.clear()
    p = _FakeProc(alive=False)
    exe.register_ai_cmd(p)
    try:
        assert exe.interrupt_active_ai_cmds() == 0, "已退出的进程应被跳过"
        assert p.signals == []
    finally:
        exe.unregister_ai_cmd(p)
    print("PASS 已退出的命令进程被跳过（不误发信号）")


def test_pty_proxy_targets_foreground_pgid():
    """交互式命令回退 PTY 时：代理的 pid 取 PTY 前台进程组，信号打到那里。"""
    from lib.terminal import exe
    exe._AI_ACTIVE_CMDS.clear()

    calls = []
    real_killpg = os.killpg
    real_tcgetpgrp = getattr(os, "tcgetpgrp", None)

    class _Shell:
        master_fd = 42          # 非真实 fd：tcgetpgrp 被替换
        pid = 999_999_999

    os.tcgetpgrp = lambda fd: 4242
    os.killpg = lambda pgid, sig: calls.append((pgid, sig))
    try:
        p = exe.PtyCmdProxy(_Shell())
        assert p.pid == 4242, p.pid
        assert p.poll() is None
        exe.register_ai_cmd(p)
        assert exe.interrupt_active_ai_cmds() == 1
        assert calls == [(4242, signal.SIGINT)], calls
        assert exe.interrupt_active_ai_cmds() == 1          # 第二次升级
        assert calls[-1] == (4242, signal.SIGTERM), calls
        exe.unregister_ai_cmd(p)
        assert exe.interrupt_active_ai_cmds() == 0
    finally:
        os.killpg = real_killpg
        if real_tcgetpgrp is not None:
            os.tcgetpgrp = real_tcgetpgrp
        exe._AI_ACTIVE_CMDS.clear()
    print("PASS PTY 交互命令被登记，Ctrl+C 打到 PTY 前台进程组（可升级）")


def test_pty_proxy_falls_back_to_shell_pid():
    """拿不到前台进程组（如 winpty / 无 job control）时退回 shell 自身 pid。"""
    from lib.terminal import exe
    real_tcgetpgrp = getattr(os, "tcgetpgrp", None)

    class _Shell:
        master_fd = None        # Windows(winpty) 情形
        pid = 999_999_999

    if real_tcgetpgrp is not None:
        os.tcgetpgrp = lambda fd: 0
    try:
        assert exe.PtyCmdProxy(_Shell()).pid == 999_999_999
    finally:
        if real_tcgetpgrp is not None:
            os.tcgetpgrp = real_tcgetpgrp
    print("PASS 无前台进程组时退回 shell pid（不抛异常）")


def main():
    test_empty_registry_returns_zero()
    test_escalation()
    test_dead_proc_skipped()
    test_pty_proxy_targets_foreground_pgid()
    test_pty_proxy_falls_back_to_shell_pid()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
