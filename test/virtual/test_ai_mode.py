# -*- coding: utf-8 -*-
"""AI 双模式（REPL / TUI）回归测试。

覆盖：
- 模式开关：DEFAULT_AI_MODE 单变量切换、显式 flag、环境变量优先级
- 模式标志解析：split_mode_flag（不误伤 prompt 文本）
- UI 适配器接缝：委托路由 / 默认无适配器
- TUI 输出流：_QueueStream 按行投递
- 依赖缺失时 ai_tui_session 回退 REPL
- TUI 无头启动（Textual run_test）：底部输入框 / RichLog / 响应式侧栏
"""
import asyncio
import os
import queue
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import bin.ai_tui as ai_tui  # noqa: E402
from bin.ai_lib import mode, ui  # noqa: E402


class TestModeResolution(unittest.TestCase):
    def setUp(self):
        self._saved = mode.DEFAULT_AI_MODE
        os.environ.pop("ONYX_AI_MODE", None)

    def tearDown(self):
        mode.DEFAULT_AI_MODE = self._saved
        os.environ.pop("ONYX_AI_MODE", None)

    def test_single_variable_switch(self):
        mode.DEFAULT_AI_MODE = "tui"
        self.assertEqual(mode.resolve_ai_mode(), "tui")
        mode.DEFAULT_AI_MODE = "repl"
        self.assertEqual(mode.resolve_ai_mode(), "repl")

    def test_explicit_overrides_default(self):
        mode.DEFAULT_AI_MODE = "repl"
        self.assertEqual(mode.resolve_ai_mode("tui"), "tui")
        mode.DEFAULT_AI_MODE = "tui"
        self.assertEqual(mode.resolve_ai_mode("repl"), "repl")

    def test_env_overrides_default(self):
        mode.DEFAULT_AI_MODE = "repl"
        with mock.patch.dict(os.environ, {"ONYX_AI_MODE": "tui"}):
            self.assertEqual(mode.resolve_ai_mode(), "tui")

    def test_split_mode_flag(self):
        self.assertEqual(mode.split_mode_flag(["ai"]), (None, ["ai"]))
        self.assertEqual(mode.split_mode_flag(["ai", "-tui"]), ("tui", ["ai"]))
        self.assertEqual(mode.split_mode_flag(["ai", "-repl", "hi"]), ("repl", ["ai", "hi"]))
        self.assertEqual(mode.split_mode_flag(["ai", "--tui"]), ("tui", ["ai"]))
        # prompt 文本里的 tui 不误判
        self.assertEqual(mode.split_mode_flag(["ai", "explain tui"]), (None, ["ai", "explain tui"]))


class TestUIAdapter(unittest.TestCase):
    def tearDown(self):
        ui.set_ui_adapter(None)

    def test_default_no_adapter(self):
        self.assertIsNone(ui.get_ui_adapter())

    def test_delegation_routing(self):
        calls = {}

        class A:
            def confirm(self, m, d=False, lang=None):
                calls["confirm"] = m
                return True

            def captcha(self, t, w, c, lang=None):
                calls["captcha"] = c
                return c == "XY99"

            def select_option(self, m, o, d="", lang=None):
                calls["select"] = m
                return o[1]

            def text_input(self, m, d="", lang=None):
                calls["text"] = m
                return "typed"

            def secret_input(self, m, d="", lang=None):
                calls["secret"] = m
                return "sec"

        ui.set_ui_adapter(A())
        self.assertTrue(ui.confirm("q?"))
        self.assertTrue(ui.captcha("t", "w", "XY99"))
        self.assertFalse(ui.captcha("t", "w", "NO"))
        self.assertEqual(ui.select_option("pick", ["a", "b"]), "b")
        self.assertEqual(ui.text_input("t"), "typed")
        self.assertEqual(ui.secret_input("s"), "sec")
        self.assertEqual(calls["confirm"], "q?")


class TestQueueStream(unittest.TestCase):
    def test_line_buffering(self):
        q = queue.Queue()
        s = ai_tui._QueueStream(q)
        s.write("hello\nwor")
        s.write("ld\n")
        self.assertEqual(q.get_nowait(), "hello")
        self.assertEqual(q.get_nowait(), "world")
        s.write("tail")
        s.flush()
        self.assertEqual(q.get_nowait(), "tail")

    def test_not_a_tty(self):
        self.assertFalse(ai_tui._QueueStream(queue.Queue()).isatty())


class TestTUIFallback(unittest.TestCase):
    def test_falls_back_to_repl_when_deps_missing(self):
        called = {}
        with mock.patch("bin.ai_lib.tui_deps.ensure_tui_deps", return_value=False), \
             mock.patch("bin.ai_interactive.ai_interactive_session",
                        side_effect=lambda **kw: called.update(kw)):
            ai_tui.ai_tui_session(user_home_dir="/tmp/xyz", onyx_module=None)
        self.assertEqual(called.get("user_home_dir"), "/tmp/xyz")
        self.assertEqual(mode.RENDER_MODE, "repl")


class TestTUIHeadless(unittest.TestCase):
    def test_layout_and_responsive(self):
        try:
            import textual  # noqa: F401
        except Exception:
            self.skipTest("textual not installed")

        App = ai_tui._build_tui()
        ctx = {"session_id": "t", "lang": "chinese", "memory_mode": "global",
               "cwd": os.getcwd(), "_conversation_history": []}

        async def _run():
            from textual.widgets import Input, RichLog
            app = App({}, ctx)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                self.assertIsNotNone(app.query_one("#prompt", Input))
                self.assertIsNotNone(app.query_one("#log", RichLog))
                self.assertTrue(app.screen.has_class("wide"))
                await pilot.resize_terminal(70, 30)
                await pilot.pause()
                self.assertFalse(app.screen.has_class("wide"))
                await pilot.press("ctrl+q")

        asyncio.run(_run())
        self.assertEqual(mode.RENDER_MODE, "repl")


if __name__ == "__main__":
    unittest.main()
