#!/usr/bin/env python3
"""离线验证 TodoWrite 共享状态 + UI 适配器推送 + TUI 接线。

覆盖：
  1. tool_executors.set_todos / get_todos 的语义（规范化 / 副本 / 清空）；
  2. _exec_todo_write 既返回格式化文本，又更新共享状态并推送适配器；
  3. ai_tui 侧栏 / 窄屏状态条 / 适配器 set_todos 接线存在。

运行: python3 test/virtual/test_tui_todo_display.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bin.ai_lib import tool_executors as te  # noqa: E402
from bin.ai_lib.ui import set_ui_adapter      # noqa: E402


class _FakeAdapter:
    def __init__(self):
        self.received = None

    def set_todos(self, todos):
        self.received = todos


def test_store_roundtrip():
    te.set_todos([{"content": "A", "status": "pending", "activeForm": "a"},
                  {"content": "B", "status": "in_progress", "activeForm": "b"}])
    got = te.get_todos()
    assert [t["content"] for t in got] == ["A", "B"], got
    # 副本：外部修改不影响内部状态
    got.append({"content": "X"})
    assert len(te.get_todos()) == 2, "get_todos 应返回副本"
    print("PASS 状态往返 + 副本")


def test_store_normalizes():
    te.set_todos(["not-a-dict", {"content": "C", "status": "completed", "activeForm": "c"}])
    assert [t["content"] for t in te.get_todos()] == ["C"], "非 dict 项应被过滤"
    te.set_todos([])
    assert te.get_todos() == [], "清空"
    print("PASS 规范化 + 清空")


def test_executor_updates_store_and_pushes():
    fake = _FakeAdapter()
    set_ui_adapter(fake)
    try:
        out = te._exec_todo_write([
            {"content": "步骤一", "status": "completed", "activeForm": "做一"},
            {"content": "步骤二", "status": "in_progress", "activeForm": "做二"},
        ])
        assert "步骤一" in out and "步骤二" in out, out
        assert "1. " in out and "2. " in out, f"工具输出应带数字序号：{out}"
        assert [t["content"] for t in te.get_todos()] == ["步骤一", "步骤二"]
        assert fake.received is not None and [t["content"] for t in fake.received] == ["步骤一", "步骤二"], \
            "应推送适配器 set_todos"
    finally:
        set_ui_adapter(None)
    print("PASS 执行器写状态 + 推送适配器")


def test_executor_empty_clears():
    fake = _FakeAdapter()
    set_ui_adapter(fake)
    try:
        te._exec_todo_write([])
        assert te.get_todos() == [], "空列表应清空状态"
        assert fake.received == [], "应推送空列表"
    finally:
        set_ui_adapter(None)
    print("PASS 空列表清空")


def test_tui_wiring_present():
    src = open(os.path.join(ROOT, "bin", "ai_tui.py"), encoding="utf-8").read()
    for needle in ('id="todo-strip"', "def _render_todos(self, todos)",
                   "def _todo_window(todos)", "def set_todos(self, todos)",
                   "def _todo_line(idx: int, t: dict)",
                   "self._render_todos(self._todos)"):
        assert needle in src, f"ai_tui 缺少：{needle}"
    print("PASS TUI 接线存在")


if __name__ == "__main__":
    test_store_roundtrip()
    test_store_normalizes()
    test_executor_updates_store_and_pushes()
    test_executor_empty_clears()
    test_tui_wiring_present()
    print("\nALL PASS")
