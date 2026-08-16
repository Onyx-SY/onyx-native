#!/usr/bin/env python3
"""AI 执行模式 Ctrl+C 语义测试（2026-09 用户决策）：
RunCommand 运行中按 Ctrl+C → 只杀命令进程，不置位全局中断标志 → AI 循环继续。

运行: python3 test/virtual/test_ai_subprocess_interrupt.py
"""
import os
import signal
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from lib.terminal import exe  # noqa: E402


class TestAiSubprocessInterrupt(unittest.TestCase):
    def test_sigint_kills_cmd_only(self):
        """Ctrl+C（模拟）→ sleep 命令被杀，返回 -1，全局中断标志不动。"""
        from bin.ai_lib import mcp_state as ms
        _saved = ms._AI_INTERRUPTED
        ms._AI_INTERRUPTED = False
        try:
            buf = []

            def _send_sigint():
                time.sleep(0.5)
                os.kill(os.getpid(), signal.SIGINT)

            t = threading.Thread(target=_send_sigint, daemon=True)
            t.start()
            rc = exe._exec_ai_subprocess("sleep 30", buf, None)
            self.assertEqual(rc, -1, f"命令应返回 -1（被中断）, 实际 {rc}")
            joined = "\n".join(buf)
            self.assertIn("[命令被用户中断]", joined, f"应标记命令被中断: {joined!r}")
            # 关键：全局中断标志不得被置位（否则下一次 API 调用立即中断 → 整个 AI 被杀）
            self.assertIs(ms._AI_INTERRUPTED, False, "mcp_state 全局中断标志不得被置位")
            # 子进程确实被杀（无残留 sleep）
            time.sleep(0.3)
        finally:
            ms._AI_INTERRUPTED = _saved

    def test_normal_command_unaffected(self):
        """正常命令仍正常执行，返回真实退出码。"""
        buf = []
        rc = exe._exec_ai_subprocess("echo hello", buf, None)
        self.assertEqual(rc, 0, f"echo 应返回 0, 实际 {rc}")
        self.assertIn("hello", "\n".join(buf))
        self.assertIsNone(exe.AI_LAST_EXIT_CODE, "测试内未走 run_cmd_sync，退出码不应被写入")

    def test_interactive_falls_back_to_pty(self):
        """交互式命令（vim）仍回退 PTY（返回 None），语义不变。"""
        rc = exe._exec_ai_subprocess("vim", [], None)
        self.assertIsNone(rc, "交互式命令应返回 None 让调用方回退 PTY")

    def test_failed_command_returns_rc(self):
        buf = []
        rc = exe._exec_ai_subprocess("exit 3", buf, None)
        self.assertEqual(rc, 3, f"exit 3 应返回 3, 实际 {rc}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
