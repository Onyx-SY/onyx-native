#!/usr/bin/env python3
"""流式渲染性能回归（增量追加 / 节流下沉 / 无每轮全堆扫描 / 有界队列）。

背景：长上下文下 TUI 变慢的三个根因
  1. #stream 每 80ms `clear()` + 全文重渲染 + 全文 Markdown 重解析 → 单条回复 O(n²)；
  2. 每个 SSE chunk 都走 call_from_thread（同步阻塞）→ 流式被 UI 反向限流；
  3. 每轮 AI 调用都 gc.get_objects() 全堆扫描 + 强引用所有 Console。

运行: python3 test/virtual/test_tui_stream_perf.py
"""
import asyncio
import os
import queue
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

try:
    import textual  # noqa: F401
except Exception as e:  # pragma: no cover
    print(f"SKIP: textual 不可用（{e}）")
    raise SystemExit(0)

from bin.ai_tui import _QueueStream  # noqa: E402


def _plain(w) -> str:
    """把 RichLog 里的内容拼成纯文本（用于判断「旧内容是否被清掉」）。"""
    out = []
    for strip in w.lines:
        segs = getattr(strip, "_segments", None) or getattr(strip, "segments", []) or []
        for s in segs:
            txt = getattr(s, "text", "")
            if txt:
                out.append(txt)
    return "".join(out)


def test_queue_stream_backpressure():
    """有界队列 + 停止标志：写入方不会被永久阻塞。"""
    q = queue.Queue(maxsize=2)
    q.put("x")
    q.put("y")                      # 队列已满
    stop = threading.Event()
    s = _QueueStream(q, stop_event=stop)
    stop.set()                      # 模拟 App 退出（drain 线程不再消费）
    t0 = time.perf_counter()
    s.write("a\nb\nc\n")
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"退出后写入仍被阻塞 {elapsed:.2f}s"
    print(f"PASS 有界队列 + 退出标志：满队列写入 {elapsed:.2f}s 内返回（不死锁）")

    q2 = queue.Queue(maxsize=1)
    s2 = _QueueStream(q2, stop_event=threading.Event())
    s2.write("one\n")               # 队列容量 1，写入成功
    assert q2.qsize() == 1
    print("PASS 有界队列容量生效")


def test_source_no_full_rerender_and_worker_throttle():
    tui_src = open(os.path.join(ROOT, "bin", "ai_tui.py"), encoding="utf-8").read()
    cmd_src = open(os.path.join(ROOT, "bin", "ai_cmd.py"), encoding="utf-8").read()

    assert "self._stream_prev" in tui_src, "流式区应记录已渲染快照（增量追加）"
    assert "_tui_push" in cmd_src, "流式节流应下沉到 worker 侧（_tui_push）"
    assert "if _tui_mode:" in cmd_src and "_render_all_panels()" in cmd_src, \
        "TUI 下应跳过 Live 面板 renderable 构建"
    # TUI 分支里不能再无条件调用 _render_all_panels
    idx = cmd_src.index("_tui_push = [0.0]")
    seg = cmd_src[idx:idx + 1400]
    assert "live_ref[0].update(_render_all_panels())" not in seg.split("else:")[0], \
        "TUI 分支不应构建 Live renderable"
    print("PASS 源码断言：增量流式 + worker 侧节流 + TUI 跳过 Live 渲染")


def test_console_registry_scans_once():
    """Console 收集：注册表 + 只在首次全堆扫描。"""
    import gc
    from bin import ai_tui as _t

    calls = {"n": 0}
    orig = gc.get_objects

    def counting():
        calls["n"] += 1
        return orig()

    class _Fake:
        console = None
        _rich_consoles = []
        _gc_scanned = False
        _collect = _t._build_tui  # 占位，避免误用

    gc.get_objects = counting
    try:
        obj = _Fake()
        obj._collect_rich_consoles = lambda: _t._build_tui and None
        # 直接调用真实实现：用 OnyxTUI 的未绑定方法
        from bin.ai_tui import _build_tui
        App = _build_tui()
        bound = App._collect_rich_consoles
        state = {"_rich_consoles": [], "_gc_scanned": False, "console": None}
        holder = type("H", (), state)()
        bound(holder)
        first = calls["n"]
        bound(holder)
        second = calls["n"]
    finally:
        gc.get_objects = orig

    assert first == 1, f"首次应做一次全堆扫描，实际 {first} 次"
    assert second == first, f"第二次不应再全堆扫描（{first} → {second}）"
    assert holder._gc_scanned is True
    print(f"PASS Console 收集：首次扫描 1 次，后续复用注册表（gc 调用 {first}→{second}）")


async def _run_headless():
    from bin.ai_tui import _build_tui
    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})
    async with app.run_test(size=(120, 40)) as pilot:
        st = app.query_one("#stream")

        # 1) 首段
        app._render_stream("第一段")
        await pilot.pause()
        n1 = len(st.lines)
        text1 = _plain(st)
        assert "第一段" in text1, text1

        # 2) 追加：旧内容必须还在（增量），行数不回落
        app._render_stream("第一段第二段")
        await pilot.pause()
        n2 = len(st.lines)
        text2 = _plain(st)
        assert "第一段" in text2 and "第二段" in text2, f"增量追加丢内容：{text2!r}"
        assert n2 >= n1, f"追加后行数不应减少：{n1} → {n2}"

        # 3) 标签只写一次（重建才写标签）
        assert text2.count("回复") == 1, f"角色标签被重复写入：{text2!r}"

        # 4) 类型切换必须重建（reason 与 reply 不混在一起）
        app._render_stream("思考内容", kind="reason")
        await pilot.pause()
        text3 = _plain(st)
        assert "第一段" not in text3, f"切换类型后应重建：{text3!r}"
        assert "思考内容" in text3

        # 5) 源端截断（文本变短）也必须重建
        app._render_stream("思考", kind="reason")
        await pilot.pause()
        text4 = _plain(st)
        assert "思考" in text4 and "内容" not in text4, f"截断未重建：{text4!r}"

        # 6) end_stream 清空 + 复位快照
        app._end_stream()
        await pilot.pause()
        assert st.display is False and app._stream_prev == "" and app._stream_kind == ""
        print("PASS 无头：增量追加 / 标签一次 / 类型切换重建 / 截断重建 / 结束复位")

        # 7) 有界输出队列
        assert app._out_q.maxsize and app._out_q.maxsize > 0, "输出队列应有界"
        print(f"PASS 输出队列有界（maxsize={app._out_q.maxsize}）")

        # 8) 活动行刷新不带 layout（否则每 0.12s 重排日志区）
        src = open(os.path.join(ROOT, "bin", "ai_tui.py"), encoding="utf-8").read()
        assert "act.update(txt, layout=False)" in src, "活动行刷新应关闭 layout"
        print("PASS 活动行刷新关闭 layout（不再每 0.12s 重排日志区）")


def main():
    test_queue_stream_backpressure()
    test_source_no_full_rerender_and_worker_throttle()
    test_console_registry_scans_once()
    asyncio.run(_run_headless())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
