#!/usr/bin/env python3
"""离线验证 TUI 渲染接线（Rich 着色 / 无面板 / 保留标题 / 输出框内状态行）。

覆盖：
  1. ui.tui_plain：TUI 无标题→裸 renderable；TUI 有标题→Group（标题行 + 内容，无面板）；
     REPL→Panel；
  2. render_ai_panel 在 TUI 下 **不**返回 Panel，但保留「🤖 AI」标签；
  3. Markdown 不被圆点前缀破坏（回归）；
  4. ai_tui / ai_cmd 渲染接线（#thinking、#log-wrap、全量 Console 同步、静默 Live）。

运行: python3 test/virtual/test_tui_render.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bin.ai_lib import mode as _mode            # noqa: E402
from bin.ai_lib.ui import tui_plain, render_ai_panel  # noqa: E402
from rich.console import Console, Group          # noqa: E402
from rich.panel import Panel                     # noqa: E402
from rich.text import Text                       # noqa: E402


def _render(renderable, width=50):
    buf = io.StringIO()
    Console(file=buf, width=width, force_terminal=True, color_system="truecolor").print(renderable)
    return buf.getvalue()


def test_tui_plain_modes():
    _mode.set_render_mode("tui")
    bare = Text("hello")
    assert tui_plain(bare) is bare, "TUI 无标题时应返回裸 renderable"
    titled = tui_plain(bare, title="T")
    assert not isinstance(titled, Panel), "TUI 不应套面板"
    assert isinstance(titled, Group), "TUI 带标题时应为 Group（标题行 + 内容）"
    _mode.set_render_mode("repl")
    assert isinstance(tui_plain(bare, title="T"), Panel), "REPL 应返回 Panel"
    _mode.set_render_mode("tui")
    print("PASS tui_plain 双模式（TUI 无面板但保留标题行）")


def test_render_ai_panel_no_panel_in_tui():
    _mode.set_render_mode("tui")
    r = render_ai_panel("## 标题\n正文 **粗体**")
    assert not isinstance(r, Panel), f"TUI 下 AI 回复不应套面板，实际 {type(r)}"
    assert "AI 回复" in _render(r), "TUI 下应保留「AI 回复」标题"
    _mode.set_render_mode("repl")
    assert isinstance(render_ai_panel("x"), Panel), "REPL 应返回 Panel"
    _mode.set_render_mode("tui")
    print("PASS AI 回复 TUI 下不套面板但保留标签")


def test_tui_ai_block_header_and_background():
    """TUI：AI 回复上方要有「AI 回复」标题，正文整块染淡蓝底色（哪怕解析不出结构）。"""
    _mode.set_render_mode("tui")
    out = _render(render_ai_panel("你好，我是 Onyx"))
    assert "AI 回复" in out, f"AI 回复上方应显示标题：{out[:200]!r}"
    assert "\x1b[48;2;" in out, "AI 回复应有底色块（背景色）"
    out_empty = _render(render_ai_panel(""))
    assert "\x1b[48;2;" in out_empty, "即使一句都解析不出来也应有底色块"
    _mode.set_render_mode("repl")
    print("PASS TUI AI 回复：标题行 + 淡蓝底色块")


def test_markdown_not_broken_by_bullet():
    """回归：`● ` 前缀曾被拼进 Markdown 源文本 → `## 标题` 原样显示、Markdown 全失效。"""
    _mode.set_render_mode("tui")
    out = _render(render_ai_panel("## 标题\n\n这是 **粗体**。"))
    assert "## 标题" not in out, f"Markdown 语法未被解析（圆点前缀破坏首行）：{out[:200]!r}"
    assert "\x1b[" in out, "AI 回复应带 ANSI 颜色"
    print("PASS Markdown 不再被圆点前缀破坏")


def test_ai_tui_wiring():
    src = open(os.path.join(ROOT, "bin", "ai_tui.py"), encoding="utf-8").read()
    for needle in ("def write_rich(self, renderable)", "def _log_renderable(self, renderable)",
                   "def set_thinking(self, active)", "def _set_thinking(self, active: bool)",
                   "def _set_subagent_activity(self, text: str)",
                   'id="thinking"', 'id="log-wrap"', "max_lines=5000",
                   "Text.from_ansi", "def _render_activity(self)",
                   "def _collect_rich_consoles(self, full: bool = False)",
                   "def _sync_rich_width(self, full: bool = False)",
                   # 性能加固：增量流式 + 注册表 + 有界队列 + 节流下沉
                   "self._stream_prev", "_RICH_CONSOLE_REGISTRY",
                   "def _put(self, item) -> None"):
        assert needle in src, f"ai_tui 缺少：{needle}"
    assert 'id="activity"' not in src, "ai_tui 不应再有底部 #activity"
    print("PASS ai_tui 渲染接线")


def test_ai_cmd_wiring():
    src = open(os.path.join(ROOT, "bin", "ai_cmd.py"), encoding="utf-8").read()
    for needle in ("def _tui_write_rich(renderable)", "def _live_console()",
                   "_tui_write_rich(_reply_panel) if _tui_mode",
                   "console=_live_console()", "_tui_activity(thinking=True)",
                   "if _parts and not _tui_mode:"):
        assert needle in src, f"ai_cmd 缺少：{needle}"
    print("PASS ai_cmd 渲染接线")


if __name__ == "__main__":
    test_tui_plain_modes()
    test_render_ai_panel_no_panel_in_tui()
    test_tui_ai_block_header_and_background()
    test_markdown_not_broken_by_bullet()
    test_ai_tui_wiring()
    test_ai_cmd_wiring()
    print("\nALL PASS")
