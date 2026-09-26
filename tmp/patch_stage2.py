# -*- coding: utf-8 -*-
"""Stage2 补丁：渲染性能 / 内存（增量流式、Console 注册表、有界队列、布局稳定）。

用法: python tmp/patch_stage2.py
每个替换都断言「恰好命中一次」，任何一条不匹配立即失败且不写盘。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")
CMD = os.path.join(ROOT, "bin", "ai_cmd.py")

PATCHES = []


def patch(path, old, new):
    PATCHES.append((path, old, new))


# ────────────────────────── bin/ai_tui.py ──────────────────────────

# 1) Console 注册表：取代每轮 gc 全堆扫描（只在首次做一次兜底扫描）
patch(
    TUI,
    """            try:
                import gc
                from rich.console import Console as _C
                # 排除 Textual 自己的 App.console：强制它的宽度/终端会干扰 Textual 渲染
                mine = getattr(self, "console", None)
                self._rich_consoles = [o for o in gc.get_objects()
                                       if isinstance(o, _C) and o is not mine]
            except Exception:
                self._rich_consoles = []""",
    """            try:
                from rich.console import Console as _C
                # 排除 Textual 自己的 App.console：强制它的宽度/终端会干扰 Textual 渲染
                mine = getattr(self, "console", None)
                found = [o for o in list(_RICH_CONSOLE_REGISTRY)
                         if isinstance(o, _C) and o is not mine]
                if not self._gc_scanned:
                    # 兜底：TUI 之前就 import 的模块（ui.py 等）其 console 早于钩子安装，
                    # 注册表里没有 → 只在**首次**做一次全堆扫描，之后由钩子增量维护。
                    # 旧实现每轮 AI 调用都 gc.get_objects()（大进程里几十毫秒），
                    # 且强引用所有 Console 阻碍回收。
                    import gc
                    for o in gc.get_objects():
                        if isinstance(o, _C) and o is not mine and o not in found:
                            found.append(o)
                    self._gc_scanned = True
                self._rich_consoles = found
            except Exception:
                self._rich_consoles = []""",
)

# 2) 有界输出队列 + 流式/扫描状态
patch(
    TUI,
    """            self._in_q = queue.Queue(maxsize=256)   # 有界：防 AI 长忙时输入堆积爆内存
            self._out_q = queue.Queue()""",
    """            self._in_q = queue.Queue(maxsize=256)   # 有界：防 AI 长忙时输入堆积爆内存
            # 输出队列同样有界：drain 线程追不上时对写入方形成背压，而不是无限吃内存
            self._out_q = queue.Queue(maxsize=8192)""",
)

patch(
    TUI,
    """            self._stream_t = 0.0       # 流式渲染节流时间戳""",
    """            self._stream_t = 0.0       # 流式渲染节流时间戳
            self._stream_prev = ""     # 已渲染的流式文本快照（增量追加用）
            self._stream_kind = ""     # 当前流式类型（reply/reason，切换时重建）
            self._gc_scanned = False   # 是否已做过一次全堆 Console 扫描（只做一次）""",
)

# 3) 流式预览：clear+全文重渲染 → 增量追加
patch(
    TUI,
    """        def _render_stream(self, text: str, kind: str = "reply") -> None:
            \"\"\"把累积的流式文本渲染到 #stream（节流 ≥80ms，避免每 chunk 重绘）。

            kind="reply"  → 正文（「AI 回复」标题 + 底色块）
            kind="reason" → 思考过程（灰色斜体，对齐 REPL 的「🤖 AI 思考中…」）
            \"\"\"
            now = time.monotonic()
            if now - self._stream_t < 0.08:
                return
            self._stream_t = now
            try:
                w = self.query_one("#stream", RichLog)
                w.clear()
                if kind == "reason":
                    from rich.text import Text as _T
                    w.write(_T((text or "")[-4000:], style="dim italic"))
                else:
                    from bin.ai_lib.ui import render_ai_panel as _rap
                    w.write(_rap(text or ""))
                w.display = True
            except Exception:
                pass

        def _end_stream(self) -> None:
            \"\"\"结束流式：清空并隐藏 #stream（正式回复由 write_rich 落进主日志）。\"\"\"
            try:
                w = self.query_one("#stream", RichLog)
                w.clear()
                w.display = False
            except Exception:
                pass""",
    """        def _render_stream(self, text: str, kind: str = "reply") -> None:
            \"\"\"把流式文本**增量追加**到 #stream（只写新增部分）。

            旧实现每 80ms `clear()` + 全文重渲染 + 全文 Markdown 重解析：单条长回复
            总成本 O(n²)（n = 输出字符数），而且每次都在主线程同步做完整渲染。
            改成增量追加后是 O(n)，与输出长度线性相关。

            kind="reply"  → 正文
            kind="reason" → 思考过程（灰字斜体，对齐 REPL 的「🤖 AI 思考中…」）
            \"\"\"
            try:
                w = self.query_one("#stream", RichLog)
            except Exception:
                return
            text = text or ""
            # 需要重建的三种情况：类型切换 / 源端截断（文本变短）/ 前缀不一致
            if (kind != self._stream_kind or len(text) < len(self._stream_prev)
                    or not text.startswith(self._stream_prev)):
                try:
                    w.clear()
                except Exception:
                    pass
                self._stream_prev = ""
                self._stream_kind = kind
                try:
                    from bin.ai_lib.ui import onyx_label as _ol
                    _cn = self._lang != "english"
                    if kind == "reason":
                        w.write(_ol("reason", "思考中" if _cn else "Thinking"))
                    else:
                        w.write(_ol("ai", "回复" if _cn else "Reply"))
                except Exception:
                    pass
            delta = text[len(self._stream_prev):]
            self._stream_prev = text
            if not delta:
                return
            try:
                from rich.text import Text as _T
                if kind == "reason":
                    w.write(_T(delta, style="grey54 italic"))
                else:
                    w.write(_T(delta))
                w.display = True
            except Exception:
                pass

        def _end_stream(self) -> None:
            \"\"\"结束流式：清空并隐藏 #stream（正式回复由 write_rich 落进主日志）。\"\"\"
            self._stream_prev = ""
            self._stream_kind = ""
            try:
                w = self.query_one("#stream", RichLog)
                w.clear()
                w.display = False
            except Exception:
                pass""",
)

# 4) 活动行 / 状态栏：Static.update 默认 layout=True → 每 0.12s 触发父容器重排
patch(
    TUI,
    """                    act.update(txt)
                    act.display = True
                elif self._subagent_text:
                    act.update(self._subagent_text)
                    act.display = True""",
    """                    # layout=False：本行高度恒为 1，默认的 layout=True 会让
                    # #log-wrap 每 0.12s 重排一次（日志区整块重绘 = 「全面渲染」观感）
                    act.update(txt, layout=False)
                    act.display = True
                elif self._subagent_text:
                    act.update(self._subagent_text, layout=False)
                    act.display = True""",
)

patch(
    TUI,
    """                    bar.update(out)
                    bar.display = True""",
    """                    bar.update(out, layout=False)
                    bar.display = True""",
)

# 5) 有界队列投递：满时等待但不死等
patch(
    TUI,
    """    def __init__(self, q: \"queue.Queue\"):
        self._q = q
        self._buf = ""
        self._lock = threading.Lock()   # 多线程并发写（主/子代理/工具线程）时保护缓冲""",
    """    def __init__(self, q: \"queue.Queue\", stop_event=None):
        self._q = q
        self._buf = ""
        self._stop = stop_event
        self._lock = threading.Lock()   # 多线程并发写（主/子代理/工具线程）时保护缓冲

    def _put(self, item) -> None:
        \"\"\"投递一行。队列满时形成背压（等 drain 线程消费），但每 0.2s 检查退出标志，
        保证 App 退出后写入方不会永久阻塞。\"\"\"
        while True:
            try:
                self._q.put(item, timeout=0.2)
                return
            except queue.Full:
                if self._stop is not None and self._stop.is_set():
                    return""",
)

patch(
    TUI,
    """        for line in parts:
            self._q.put(line)
        return len(s)""",
    """        for line in parts:
            self._put(line)
        return len(s)""",
)

patch(
    TUI,
    """        with self._lock:
            if self._buf:
                self._q.put(self._buf)
                self._buf = \"\"""",
    """        with self._lock:
            if self._buf:
                buf, self._buf = self._buf, ""
        if buf:
            self._put(buf)""",
)

patch(
    TUI,
    """            out = _QueueStream(self._out_q)""",
    """            out = _QueueStream(self._out_q, stop_event=self._stop)""",
)

# ────────────────────────── bin/ai_cmd.py ──────────────────────────

# 6) 流式节流下沉到 worker 侧 + TUI 跳过 Live renderable 构建
patch(
    CMD,
    """            # 更新 Live Panel
            if live_ref[0]:
                live_ref[0].update(_render_all_panels())

            # TUI：Live 在 TUI 下是静默的，改由 #stream 做实时预览（节流在 TUI 侧）
            _tui_stream(stream_text)""",
    """            if _tui_mode:
                # TUI：Live 面板挂在静默 Console 上（渲染结果直接丢弃），却仍要对**全文**
                # 做一次 Markdown 解析 —— 每个 chunk 一次 O(n)，整轮 O(n²)。这里直接跳过。
                #
                # 节流也必须在这一侧做：适配器的 update_stream 走 call_from_thread，
                # 那是**同步阻塞**的，放在主线程侧节流等于每个 chunk 都要跨线程往返一次
                # （SSE 线程被 UI 渲染反向限流 → 观感「不是流式」）。
                _now = time.monotonic()
                if _now - _tui_push[0] >= 0.1:
                    _tui_push[0] = _now
                    _tui_stream(stream_text)
            else:
                # 更新 Live Panel
                if live_ref[0]:
                    live_ref[0].update(_render_all_panels())""",
)

patch(
    CMD,
    """        def on_stream_content(chunk: str) -> None:
            \"\"\"实时流式回调：纯 Markdown 直通累积 + 更新复合 Panel\"\"\"
            nonlocal stream_text, _content_started""",
    """        _tui_push = [0.0]        # TUI 流式推送节流时间戳（worker 侧，见下）

        def on_stream_content(chunk: str) -> None:
            \"\"\"实时流式回调：纯 Markdown 直通累积 + 更新复合 Panel\"\"\"
            nonlocal stream_text, _content_started""",
)


def main():
    cache = {}
    for path, old, new in PATCHES:
        if path not in cache:
            with open(path, encoding="utf-8") as f:
                cache[path] = f.read()
        text = cache[path]
        n = text.count(old)
        if n != 1:
            print(f"FAIL {os.path.relpath(path, ROOT)}: 命中 {n} 次（期望 1）")
            print("----- 目标片段 -----")
            print(old[:400])
            return 1
        cache[path] = text.replace(old, new, 1)
        print(f"OK   {os.path.relpath(path, ROOT)}: {len(old)}B → {len(new)}B")

    for path, text in cache.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"写入 {os.path.relpath(path, ROOT)}")
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
