# -*- coding: utf-8 -*-
"""Stage6 补丁 E：
  1) _flush_batch 改为按类型分派（不再要求队列里带标签）→ 保持 _QueueStream 契约不变
     （队列里仍是裸字符串，renderable 直接放）→ 既有测试与外部预期零破坏。
  2) 滑动手势：需要**连续 2 次**高速箭头才判定（第 3 个事件起生效），
     单次快速连击不会误判。
  3) 更新既有历史测试：显式用「人类速度」按键（<30ms 的箭头流现在会被判为触摸滑动）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")
HIST = os.path.join(ROOT, "test", "virtual", "test_tui_input_history.py")
GEST = os.path.join(ROOT, "test", "virtual", "test_tui_arrow_gesture.py")

P = []


def patch(path, old, new):
    P.append((path, old, new))


# ── 1) 队列契约保持「裸字符串 / renderable」 ──
patch(
    TUI,
    """    def _put(self, item) -> None:
        \"\"\"投递一行（带 txt 标签）。队列满时形成背压（等 drain 线程消费），
        但每 0.2s 检查退出标志，保证 App 退出后写入方不会永久阻塞。\"\"\"
        while True:
            try:
                self._q.put(("txt", item), timeout=0.2)
                return
            except queue.Full:
                if self._stop is not None and self._stop.is_set():
                    return""",
    """    def _put(self, item) -> None:
        \"\"\"投递一行（队列里保持**裸字符串**契约）。队列满时形成背压（等 drain 消费），
        但每 0.2s 检查退出标志，保证 App 退出后写入方不会永久阻塞。\"\"\"
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
    """            队列里是带标签的条目：("txt", str) 普通输出 / ("rich", renderable) 结构化面板。
            统一走队列（而不是各自 call_from_thread）是为了让 worker 永不阻塞等待 UI。
            \"\"\"""",
    """            队列里混装两种条目：str（普通文本输出）与 Rich renderable（结构化面板）。
            统一走队列（而不是各自 call_from_thread）是为了让 worker 永不阻塞等待 UI。
            \"\"\"""",
)

patch(
    TUI,
    """        def _enqueue_rich(self, renderable) -> None:
            \"\"\"线程安全：把 Rich renderable 投进输出队列（满时短暂等待，不死等）。\"\"\"
            while True:
                try:
                    self._out_q.put(("rich", renderable), timeout=0.2)
                    return
                except queue.Full:
                    if self._stop.is_set():
                        return

        def _flush_batch(self, batch):
            \"\"\"主线程：按原顺序落盘（连续文本合并成一次 write，rich 条目单独写）。\"\"\"
            buf = []
            for item in batch:
                if isinstance(item, tuple) and len(item) == 2:
                    tag, payload = item
                else:                            # 兼容裸字符串（旧路径 / 测试）
                    tag, payload = "txt", item
                if tag == "rich":
                    if buf:
                        self._log(buf)
                        buf = []
                    self._log_renderable(payload)
                else:
                    buf.append(payload)
            if buf:
                self._log(buf)""",
    """        def _enqueue_rich(self, renderable) -> None:
            \"\"\"线程安全：把 Rich renderable 投进输出队列（满时短暂等待，不死等）。\"\"\"
            while True:
                try:
                    self._out_q.put(renderable, timeout=0.2)
                    return
                except queue.Full:
                    if self._stop.is_set():
                        return

        def _flush_batch(self, batch):
            \"\"\"主线程：按原顺序落盘（连续文本合并成一次 write，renderable 单独写）。

            分派规则就是类型本身：str = 普通输出文本；其余 = Rich renderable。
            （保持 _QueueStream 的「队列里是裸字符串」契约，避免破坏既有调用方与测试。）
            \"\"\"
            buf = []
            for item in batch:
                if isinstance(item, str):
                    buf.append(item)
                    continue
                if buf:
                    self._log(buf)
                    buf = []
                self._log_renderable(item)
            if buf:
                self._log(buf)""",
)

# ── 2) 手势判定：连续 2 次高速才成立 ──
patch(
    TUI,
    """            self._arrow_t = 0.0          # 上一次 ↑/↓ 的时间戳
            self._arrow_gesture = False  # 当前是否处于「滑动流」中""",
    """            self._arrow_t = 0.0          # 上一次 ↑/↓ 的时间戳
            self._arrow_fast = 0         # 连续高速箭头的计数（≥2 才判为滑动流）
            self._arrow_gesture = False  # 当前是否处于「滑动流」中""",
)

patch(
    TUI,
    """        def _arrow_gesture_check(self) -> bool:
            \"\"\"True = 这次 ↑/↓ 属于滑动流（应滚动日志而不是翻历史）。\"\"\"
            now = time.monotonic()
            gap = now - self._arrow_t
            self._arrow_t = now
            if 0 < gap < self._ARROW_GESTURE_GAP:
                if not self._arrow_gesture:
                    self._arrow_gesture = True
                    self._hist_snap_restore()
                return True
            if gap >= self._ARROW_GESTURE_IDLE:
                self._arrow_gesture = False
                self._hist_snap = None
            return False""",
    """        def _arrow_gesture_check(self) -> bool:
            \"\"\"True = 这次 ↑/↓ 属于滑动流（应滚动日志而不是翻历史）。

            判定需要**连续 2 次**高速箭头（即第 3 个事件起生效）：
            单次快速连击（例如程序化连调 / 极快的手速）不会被误判成滑动。
            \"\"\"
            now = time.monotonic()
            gap = now - self._arrow_t
            self._arrow_t = now
            if 0 < gap < self._ARROW_GESTURE_GAP:
                self._arrow_fast += 1
                if self._arrow_gesture:
                    return True
                if self._arrow_fast >= 2:
                    self._arrow_gesture = True
                    self._hist_snap_restore()   # 把这一滑误翻的历史原样退回
                    return True
                return False
            self._arrow_fast = 0
            if gap >= self._ARROW_GESTURE_IDLE:
                self._arrow_gesture = False
                self._hist_snap = None
            return False""",
)

# ── 3) 既有历史测试：按键要有「人类间隔」 ──
patch(
    HIST,
    """        # 2) ↑ / ↓ 翻历史（含边界与草稿恢复）
        inp.value = ""
        app.action_menu_up()
        assert inp.value == "第二条问题", inp.value
        app.action_menu_up()
        assert inp.value == "第一条问题", inp.value
        app.action_menu_up()          # 到顶不越界
        assert inp.value == "第一条问题", inp.value
        app.action_menu_down()
        assert inp.value == "第二条问题", inp.value
        app.action_menu_down()        # 到底恢复草稿
        assert inp.value == "", repr(inp.value)""",
    """        # 2) ↑ / ↓ 翻历史（含边界与草稿恢复）
        # ⚠️ 必须留出「人类按键间隔」：相邻 ↑/↓ 间隔 < 30ms 现在会被判为**触摸滑动**
        #    （终端把上下滑动编码成高速箭头流），那不是本用例要测的路径。
        inp.value = ""
        app.action_menu_up()
        assert inp.value == "第二条问题", inp.value
        time.sleep(0.06)
        app.action_menu_up()
        assert inp.value == "第一条问题", inp.value
        time.sleep(0.06)
        app.action_menu_up()          # 到顶不越界
        assert inp.value == "第一条问题", inp.value
        time.sleep(0.06)
        app.action_menu_down()
        assert inp.value == "第二条问题", inp.value
        time.sleep(0.06)
        app.action_menu_down()        # 到底恢复草稿
        assert inp.value == "", repr(inp.value)""",
)

patch(
    HIST,
    """        inp.value = "我的草稿"
        app._hist_idx = len(app._hist)
        app.action_menu_up()
        assert inp.value == "第二条问题", inp.value
        app.action_menu_down()
        assert inp.value == "我的草稿", inp.value""",
    """        inp.value = "我的草稿"
        app._hist_idx = len(app._hist)
        app._arrow_t = 0.0
        app._arrow_fast = 0
        app._arrow_gesture = False
        app.action_menu_up()
        assert inp.value == "第二条问题", inp.value
        time.sleep(0.06)
        app.action_menu_down()
        assert inp.value == "我的草稿", inp.value""",
)

# ── 4) 手势测试：真实按键路径也要有间隔；并补一条「快速连击不误判」 ──
patch(
    GEST,
    """        seen = []
        for _ in range(3):
            await pilot.press("up")
            await pilot.pause()
            await pilot.pause()
            seen.append(inp.value)""",
    """        seen = []
        for _ in range(3):
            await pilot.press("up")
            await pilot.pause()
            await pilot.pause()
            seen.append(inp.value)
            time.sleep(0.06)          # 人类按键间隔""",
)

patch(
    GEST,
    """        # ── 1) 滑动流：不改历史、改为滚动日志 ──
        app._hist = ["旧命令A", "旧命令B", "旧命令C"]
        app._hist_idx = len(app._hist)
        app._hist_draft = ""
        inp.value = ""
        app._arrow_t = 0.0
        app._arrow_gesture = False
        app._hist_snap = None""",
    """        # ── 0) 快速连击 2 次（未达「连续 2 次高速」阈值）不应被误判成滑动 ──
        app._hist = ["旧命令A", "旧命令B", "旧命令C"]
        app._hist_idx = len(app._hist)
        app._hist_draft = ""
        inp.value = ""
        app._arrow_t = 0.0
        app._arrow_fast = 0
        app._arrow_gesture = False
        app._hist_snap = None
        app._hist_prev()
        time.sleep(SWIPE_GAP)
        app._hist_prev()
        assert app._arrow_gesture is False, "仅 2 次高速箭头不应判为滑动流"
        assert app._hist_idx == 1, f"应正常翻历史，实际 {app._hist_idx}"
        print("PASS 仅 2 次快速箭头不误判（仍需连续 2 次高速才成立）")

        # ── 1) 滑动流：不改历史、改为滚动日志 ──
        app._hist_idx = len(app._hist)
        app._hist_draft = ""
        inp.value = ""
        app._arrow_t = 0.0
        app._arrow_fast = 0
        app._arrow_gesture = False
        app._hist_snap = None""",
)

patch(
    GEST,
    """        app._arrow_t = 0.0
        app._arrow_gesture = False
        app._hist_snap = None
        app._hist_idx = 1
        before = log.scroll_y""",
    """        app._arrow_t = 0.0
        app._arrow_fast = 0
        app._arrow_gesture = False
        app._hist_snap = None
        app._hist_idx = 1
        before = log.scroll_y""",
)

patch(
    GEST,
    """        app._arrow_t = 0.0
        app._arrow_gesture = False
        app._hist_snap = None
        app._hist_prog_values = []""",
    """        app._arrow_t = 0.0
        app._arrow_fast = 0
        app._arrow_gesture = False
        app._hist_snap = None
        app._hist_prog_values = []""",
)


def main():
    cache = {}
    for path, old, new in P:
        if path not in cache:
            with open(path, encoding="utf-8") as f:
                cache[path] = f.read()
        text = cache[path]
        n = text.count(old)
        if n != 1:
            print(f"FAIL {os.path.basename(path)}: 命中 {n} 次\n{old[:200]}")
            return 1
        cache[path] = text.replace(old, new, 1)
    for path, text in cache.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"OK   {os.path.relpath(path, ROOT)}")
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
