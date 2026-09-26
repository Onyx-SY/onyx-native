#!/usr/bin/env python3
"""TUI Ctrl+C → 杀「AI 正在执行的命令」回归。

背景：TUI 里终端处于 raw 模式，Ctrl+C 不会变成 SIGINT 信号，只会作为按键
(`ctrl+c`) 交给 App。旧实现只置位 mcp_state._AI_INTERRUPTED（停止生成），
正在跑的命令子进程没人管 → 用户观感「Ctrl+C 杀不掉 AI 正在执行的命令」。
现在 action_cancel 会先把中断转发给活跃 AI 命令（重复按逐级升级）。

运行: python3 test/virtual/test_tui_ctrlc_cmd.py
"""
import asyncio
import os
import signal
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

try:
    import textual  # noqa: F401
except Exception as e:  # pragma: no cover
    print(f"SKIP: textual 不可用（{e}）")
    raise SystemExit(0)


class _FakeProc:
    """假进程：不存在的 pid → os.getpgid 抛错 → 走 send_signal（记录信号）。"""
    def __init__(self):
        self.pid = 999_999_999
        self.signals = []

    def poll(self):
        return None

    def send_signal(self, sig):
        self.signals.append(sig)


async def _run():
    from lib.terminal import exe
    from bin.ai_lib import mcp_state as _ms
    from bin.ai_tui import _build_tui

    App = _build_tui()
    app = App(session_kwargs={"user_home_dir": os.path.expanduser("~")}, ctx={"lang": "chinese"})
    async with app.run_test(size=(100, 30)) as pilot:
        app._stop.set()
        await pilot.pause()

        # 1) 忙 + 有活跃命令 → 转发信号给命令，且**不**打断 AI 循环
        exe._AI_ACTIVE_CMDS.clear()
        p = _FakeProc()
        exe.register_ai_cmd(p)
        _ms._AI_INTERRUPTED = False
        app._busy = True
        try:
            app.action_cancel()
            await pilot.pause()
            assert p.signals == [signal.SIGINT], p.signals
            assert _ms._AI_INTERRUPTED is False, "有命令在跑时不应打断 AI 循环"
            # 再按一次 → 升级
            app.action_cancel()
            await pilot.pause()
            assert p.signals == [signal.SIGINT, signal.SIGTERM], p.signals
        finally:
            exe.unregister_ai_cmd(p)
        print("PASS TUI Ctrl+C 转发给活跃命令（重复按升级），且不打断 AI 循环")

        # 2) 忙 + 无活跃命令 → 回落到「打断生成」路径
        _ms._AI_INTERRUPTED = False
        app._busy = True
        app.action_cancel()
        await pilot.pause()
        assert _ms._AI_INTERRUPTED is True, "无命令时应打断生成"
        print("PASS TUI Ctrl+C 无活跃命令时回落到打断生成")

        # 3) 闲 → 只清空输入框，不置中断标志
        _ms._AI_INTERRUPTED = False
        app._busy = False
        from textual.widgets import Input
        inp = app.query_one("#prompt", Input)
        inp.value = "待清空"
        app.action_cancel()
        await pilot.pause()
        assert inp.value == "", repr(inp.value)
        assert _ms._AI_INTERRUPTED is False
        print("PASS TUI Ctrl+C 空闲时只清空输入框")


def main():
    asyncio.run(_run())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
