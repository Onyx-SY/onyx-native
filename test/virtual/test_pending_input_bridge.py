#!/usr/bin/env python3
"""离线验证「实时引导桥」——AI 运行中输入排队 → 每轮边界注入。

覆盖：
  1. ui.set_pending_input_provider / drain_pending_input 的语义（无 provider / 取走清空 / 过滤空白 / 异常兜底）；
  2. TUI 侧 _drain_inputs 的队列语义（非阻塞 get_nowait 直到 Empty）；
  3. ai_cmd 注入点位于轮边界（while 循环内、interaction_count += 1 之前）；
  4. ai_tui 在 on_mount 注册 / on_unmount 注销 provider。

运行: python3 test/virtual/test_pending_input_bridge.py
"""
import os
import queue
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bin.ai_lib.ui import set_pending_input_provider, drain_pending_input  # noqa: E402


def test_no_provider():
    set_pending_input_provider(None)
    assert drain_pending_input() == [], "无 provider 应返回空列表"
    print("PASS 无 provider → 空")


def test_provider_drain_and_clear():
    box = []

    def provider():
        out = list(box)
        box.clear()
        return out

    set_pending_input_provider(provider)
    box.extend(["第一条", "第二条"])
    assert drain_pending_input() == ["第一条", "第二条"], "应取走全部排队输入"
    assert drain_pending_input() == [], "取走后应清空"
    print("PASS 取走并清空")


def test_blank_filtered():
    set_pending_input_provider(lambda: ["a", "   ", "", None])
    assert drain_pending_input() == ["a"], "空白/None 应被过滤"
    print("PASS 过滤空白")


def test_provider_exception_safe():
    def boom():
        raise RuntimeError("boom")

    set_pending_input_provider(boom)
    assert drain_pending_input() == [], "provider 异常应兜底为空"
    print("PASS provider 异常兜底")


def test_tui_queue_semantics():
    """镜像 ai_tui.OnyxTUI._drain_inputs 的非阻塞语义。"""
    in_q = queue.Queue()

    def drain():
        items = []
        while True:
            try:
                items.append(in_q.get_nowait())
            except queue.Empty:
                break
        return items

    set_pending_input_provider(drain)
    in_q.put("引导A")
    in_q.put("引导B")
    assert drain_pending_input() == ["引导A", "引导B"], "应按入队顺序取走"
    assert drain_pending_input() == [], "再次调用应为空"
    print("PASS TUI 队列语义")


def test_ai_cmd_hook_at_round_boundary():
    src = open(os.path.join(ROOT, "bin", "ai_cmd.py"), encoding="utf-8").read()
    assert "drain_pending_input" in src, "ai_cmd 应调用 drain_pending_input"
    i_drain = src.index("_pending_inputs = _drain_pending()")
    i_inc = src.index("interaction_count += 1")
    assert i_drain < i_inc, "注入点必须在 interaction_count += 1（本轮 API 调用）之前"
    assert '{"role": "user", "content": _guide_text}' in src, "应作为 user 消息注入"
    print("PASS ai_cmd 注入点在轮边界之前")


def test_tui_registers_provider():
    src = open(os.path.join(ROOT, "bin", "ai_tui.py"), encoding="utf-8").read()
    assert "set_pending_input_provider(self._drain_inputs)" in src, "on_mount 应注册 provider"
    assert "set_pending_input_provider(None)" in src, "on_unmount 应注销 provider"
    assert "def _drain_inputs(self):" in src, "应定义 _drain_inputs"
    print("PASS ai_tui 注册/注销 provider")


if __name__ == "__main__":
    test_no_provider()
    test_provider_drain_and_clear()
    test_blank_filtered()
    test_provider_exception_safe()
    test_tui_queue_semantics()
    test_ai_cmd_hook_at_round_boundary()
    test_tui_registers_provider()
    print("\nALL PASS")
