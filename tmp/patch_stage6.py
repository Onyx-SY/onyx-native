# -*- coding: utf-8 -*-
"""Stage6 补丁：
  1) 模态框里输入的文本被当成 AI 消息（Input.Submitted 冒泡到 App）
  2) 触摸滑动被当成「历史上下翻」（箭头键风暴识别）
  3) 版本号
  4) 平台能力开关 + 400 自动降级
  5) 性能：write_rich 非阻塞（统一输出队列）+ 补全候选缓存
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")
API = os.path.join(ROOT, "bin", "ai_lib", "api.py")
CFG = os.path.join(ROOT, "etc", "config.json")

P = []


def patch(path, old, new):
    P.append((path, old, new))


# ══════════════════ 1) 模态框输入 → 不再被当成 AI 消息 ══════════════════
patch(
    TUI,
    """        def on_input_submitted(self, event):
            \"\"\"单行框：Enter 发送（虚影由 → 接受）。\"\"\"
            self._menu_hide()
            try:
                self.query_one("#prompt", Input).value = ""
            except Exception:
                pass
            self._submit(event.value)""",
    """        def _modal_open(self) -> bool:
            \"\"\"当前是否有模态框占屏（模态期间绝不允许把输入当成 AI 消息）。\"\"\"
            try:
                from textual.screen import ModalScreen
                return isinstance(self.screen, ModalScreen)
            except Exception:
                return False

        def on_input_submitted(self, event):
            \"\"\"单行框：Enter 发送（虚影由 → 接受）。

            ⚠️ Textual 的 Input.Submitted 会从**任意**输入框沿 DOM 冒泡到 App ——
            包括模态框里的 #modal-input（历史搜索 / 文本输入 / 验证码）。不过滤就会出现
            「在弹窗里打完字按回车 → 内容被当成发给 AI 的消息」（用户实测 bug）。
            这里只认主输入框 #prompt，且模态框打开期间一律忽略。
            \"\"\"
            if getattr(getattr(event, "input", None), "id", "") != "prompt":
                return
            if self._modal_open():
                return
            self._menu_hide()
            try:
                self.query_one("#prompt", Input).value = ""
            except Exception:
                pass
            self._submit(event.value)""",
)

# ══════════════════ 2) 触摸滑动 ≠ 历史上下翻 ══════════════════
patch(
    TUI,
    """            self._gc_scanned = False   # 是否已做过一次全堆 Console 扫描（只做一次）""",
    """            self._gc_scanned = False   # 是否已做过一次全堆 Console 扫描（只做一次）
            # ── 触摸滑动（箭头键风暴）识别 ──
            self._arrow_t = 0.0          # 上一次 ↑/↓ 的时间戳
            self._arrow_gesture = False  # 当前是否处于「滑动流」中
            self._hist_snap = None       # 滑动前的 (索引, 输入内容, 草稿)，用于回滚误翻
            self._menu_cache = {}        # 补全候选缓存（避免同一文本重复扫盘）""",
)

patch(
    TUI,
    """        def _hist_prev(self) -> None:
            \"\"\"上翻历史（↑ / Ctrl+P）。首次上翻跳到最新一条，并记下当前草稿。

            索引约定：`_hist` 为「新→旧」，`_hist_idx == len(_hist)` 表示「未在翻历史」。
            上翻从「最新（0）」开始向「更旧（+1）」走。
            \"\"\"
            w = self._focused_input()""",
    """        # ── 触摸滑动 vs 真实按键 ──
        # 终端把「上下滑动」编码成**连续高速**的 ↑/↓ 键流（每个触摸帧一发，间隔约
        # 8~16ms）；人类按键 / 系统长按重复最快也就 ~50ms 一档。旧实现无条件当「翻历史」
        # → 手指一滑就哗啦啦翻过好几条历史（用户实测）。判据就用间隔：< 30ms 判为滑动流
        # → 改为滚动日志，并把这一滑误翻的历史**回滚**回去。
        _ARROW_GESTURE_GAP = 0.030
        _ARROW_GESTURE_IDLE = 0.15

        def _arrow_gesture_check(self) -> bool:
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
            return False

        def _hist_snap_save(self) -> None:
            \"\"\"翻历史前拍快照：万一其实是滑动，要能原样退回。\"\"\"
            try:
                w = self._focused_input()
                cur = getattr(w, "value", None)
                if cur is None:
                    cur = getattr(w, "text", "")
                self._hist_snap = (self._hist_idx, cur or "", self._hist_draft)
            except Exception:
                self._hist_snap = None

        def _hist_snap_restore(self) -> None:
            snap = self._hist_snap
            if not snap:
                return
            self._hist_snap = None
            try:
                idx, val, draft = snap
                self._hist_idx, self._hist_draft = idx, draft
                w = self._focused_input()
                if w is not None:
                    self._set_input_value(w, val)
            except Exception:
                pass

        def _scroll_log(self, delta: int) -> None:
            \"\"\"滚动消息日志（触摸滑动被识别为箭头流时走这里）。\"\"\"
            try:
                self.query_one("#log").scroll_relative(y=delta, animate=False)
            except Exception:
                pass

        def _hist_prev(self) -> None:
            \"\"\"上翻历史（↑ / Ctrl+P）。首次上翻跳到最新一条，并记下当前草稿。

            索引约定：`_hist` 为「新→旧」，`_hist_idx == len(_hist)` 表示「未在翻历史」。
            上翻从「最新（0）」开始向「更旧（+1）」走。
            \"\"\"
            if self._arrow_gesture_check():
                self._scroll_log(-3)      # 滑动流 → 滚日志（手指上滑 = 看更早内容）
                return
            self._hist_snap_save()
            w = self._focused_input()""",
)

patch(
    TUI,
    """        def _hist_next(self) -> None:
            \"\"\"下翻历史（↓ / Ctrl+N）；从最新再下一条即恢复草稿。\"\"\"
            w = self._focused_input()""",
    """        def _hist_next(self) -> None:
            \"\"\"下翻历史（↓ / Ctrl+N）；从最新再下一条即恢复草稿。\"\"\"
            if self._arrow_gesture_check():
                self._scroll_log(3)
                return
            self._hist_snap_save()
            w = self._focused_input()""",
)

# ══════════════════ 5a) write_rich 非阻塞 ══════════════════
patch(
    TUI,
    """        def write_rich(self, renderable):
            \"\"\"把 Rich renderable 直写日志区（保留 Markdown 颜色 / 面板边框）。\"\"\"
            try:
                self.app.call_from_thread(self.app._log_renderable, renderable)
            except Exception:
                pass""",
    """        def write_rich(self, renderable):
            \"\"\"把 Rich renderable 投递到日志区（保留 Markdown 颜色 / 面板边框）。

            非阻塞：走 _out_q 统一队列（drain 线程按顺序落盘）。
            旧实现用 call_from_thread —— 那是**同步阻塞**的，工具面板一多就把 worker
            线程拖成串行等待，AI 反过来被 UI 限流。
            \"\"\"
            try:
                self.app._enqueue_rich(renderable)
            except Exception:
                pass""",
)

patch(
    TUI,
    """    def _put(self, item) -> None:
        \"\"\"投递一行。队列满时形成背压（等 drain 线程消费），但每 0.2s 检查退出标志，
        保证 App 退出后写入方不会永久阻塞。\"\"\"
        while True:
            try:
                self._q.put(item, timeout=0.2)
                return
            except queue.Full:
                if self._stop is not None and self._stop.is_set():
                    return""",
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
)

patch(
    TUI,
    """        def _drain_loop(self):
            \"\"\"把 _out_q 的文本**批量**投递到主线程（逐行投递会产生大量跨线程跳转）。\"\"\"
            while not self._stop.is_set():
                try:
                    first = self._out_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                batch = [first]
                while True:                      # 一次取空积压
                    try:
                        batch.append(self._out_q.get_nowait())
                    except queue.Empty:
                        break
                try:
                    self.call_from_thread(self._log, batch)
                except Exception:
                    pass""",
    """        def _drain_loop(self):
            \"\"\"把 _out_q 的条目**批量**投递到主线程（逐行投递会产生大量跨线程跳转）。

            队列里是带标签的条目：("txt", str) 普通输出 / ("rich", renderable) 结构化面板。
            统一走队列（而不是各自 call_from_thread）是为了让 worker 永不阻塞等待 UI。
            \"\"\"
            while not self._stop.is_set():
                try:
                    first = self._out_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                batch = [first]
                while True:                      # 一次取空积压
                    try:
                        batch.append(self._out_q.get_nowait())
                    except queue.Empty:
                        break
                try:
                    self.call_from_thread(self._flush_batch, batch)
                except Exception:
                    pass

        def _enqueue_rich(self, renderable) -> None:
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
)

# ══════════════════ 5b) 补全候选缓存 ══════════════════
patch(
    TUI,
    """            try:
                from bin.ai_interactive import completion_candidates
                cands = completion_candidates(text, self._lang)
            except Exception:
                cands = []""",
    """            # 同一文本不重复算（路径补全要 listdir，退格/重复输入时省掉大量扫盘）
            key = text or ""
            if key in self._menu_cache:
                cands = self._menu_cache[key]
            else:
                try:
                    from bin.ai_interactive import completion_candidates
                    cands = completion_candidates(key, self._lang)
                except Exception:
                    cands = []
                if len(self._menu_cache) > 48:
                    self._menu_cache.clear()
                self._menu_cache[key] = cands""",
)

# ══════════════════ 4) 平台能力开关 + 400 自动降级 ══════════════════
patch(
    API,
    """def _apply_openai_delta(choices, tool_calls_acc, on_content=None, on_tool_call=None):""",
    """# 可选参数：不是所有 OpenAI 兼容平台都认（严格的服务端收到未知字段直接 400）。
# DeepSeek 全支持，所以只对接 DeepSeek 时不会暴露。
_OPTIONAL_PARAM_KEYS = ("thinking", "reasoning_effort", "stream_options")
# 「未知 / 不支持参数」类报错的特征串（各家措辞都不同）
_UNSUPPORTED_PARAM_HINTS = (
    "unknown parameter", "unrecognized", "unexpected keyword", "extra fields",
    "unknown field", "unsupported parameter", "not permitted",
    "未知参数", "不支持", "额外的字段",
)


def _plat_cap(plat_info: dict, name: str, default):
    \"\"\"平台能力开关：key.json 的 platform 配置里可用 `capabilities` 覆盖。

    例：{"capabilities": {"stream_options": false}} → 不发 stream_options。
    \"\"\"
    try:
        caps = (plat_info or {}).get("capabilities") or {}
        if name in caps:
            return caps[name]
    except Exception:
        pass
    return default


def _degrade_optional_params(payload: dict, detail: str) -> bool:
    \"\"\"平台报「未知/不支持参数」时，摘掉可选字段。返回是否真的摘掉了东西。

    这样换平台不需要改配置：先按最全字段发，被拒就自动降级重试一次。
    \"\"\"
    try:
        low = (detail or "").lower()
        if not any(h in low for h in _UNSUPPORTED_PARAM_HINTS):
            return False
        removed = False
        for k in _OPTIONAL_PARAM_KEYS:
            if k in payload:
                payload.pop(k, None)
                removed = True
        return removed
    except Exception:
        return False


def _apply_openai_delta(choices, tool_calls_acc, on_content=None, on_tool_call=None):""",
)

patch(
    API,
    """    _thinking_cfg = _resolve_thinking(user_params.get("thinking"), plat_info.get("thinking"))
    if _thinking_cfg and stream_fmt in ("openai", "anthropic"):
        payload["thinking"] = _thinking_cfg
    _effort = user_params.get("reasoning_effort") or plat_info.get("reasoning_effort")
    if _effort and stream_fmt in ("openai", "anthropic"):
        payload["reasoning_effort"] = _effort""",
    """    _thinking_cfg = _resolve_thinking(user_params.get("thinking"), plat_info.get("thinking"))
    if (_thinking_cfg and stream_fmt in ("openai", "anthropic")
            and _plat_cap(plat_info, "thinking", True)):
        payload["thinking"] = _thinking_cfg
    _effort = user_params.get("reasoning_effort") or plat_info.get("reasoning_effort")
    if (_effort and stream_fmt in ("openai", "anthropic")
            and _plat_cap(plat_info, "reasoning_effort", True)):
        payload["reasoning_effort"] = _effort""",
)

patch(
    API,
    """    if stream_fmt == "openai":
        payload["stream_options"] = {"include_usage": True}""",
    """    if stream_fmt == "openai" and _plat_cap(plat_info, "stream_options", True):
        payload["stream_options"] = {"include_usage": True}""",
)

patch(
    API,
    """            if response.status_code in (400, 422):
                _detail = response.text[:2000]""",
    """            if response.status_code in (400, 422):
                _detail = response.text[:2000]
                # ── 自动降级：平台不认可选字段（thinking / reasoning_effort /
                # stream_options）时，摘掉它们重试一次，避免「换个平台就 400 全挂」。
                if retry < max_retries - 1 and _degrade_optional_params(payload, _detail):
                    console.print("[yellow]⚠️ 平台不认可选参数，已自动降级重试"
                                  "（thinking / reasoning_effort / stream_options 已摘除）[/]")
                    continue""",
)

# ══════════════════ 3) 版本号 ══════════════════
patch(
    CFG,
    """    "version": "2.9.5.b1",""",
    """    "version": "2.9.6.b1",""",
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
            print(f"FAIL {os.path.relpath(path, ROOT)}: 命中 {n} 次（期望 1）")
            print(old[:250])
            return 1
        cache[path] = text.replace(old, new, 1)
        print(f"OK   {os.path.relpath(path, ROOT)}: {len(old)}B → {len(new)}B")
    for path, text in cache.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
