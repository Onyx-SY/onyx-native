# -*- coding: utf-8 -*-
"""G2：滑动手势判据升级 + 三态开关 + 输入抓包。

旧判据（间隔 <30ms 且连续 2 次）在真机上仍误判 —— 说明 Termux 的滑动箭头流
间隔可能 ≥30ms，或一次滑动只发 1~2 个箭头。改为**滑动窗口计数**判据：
「N 次箭头落在 WINDOW 秒内，且相邻间隔 < GAP」才算滑动流（可 env 调参）。
另外提供 ONYX_TUI_ARROW_MODE=auto|history|scroll 三态开关，以及
ONYX_TUI_INPUT_DEBUG=1 原始字节抓包（下次可用事实定标阈值）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")

P = []


def patch(old, new):
    P.append((old, new))


# ── 1) 模块级：三态开关 + 参数 + 抓包 ──
patch(
    '''def _tui_mouse_enabled() -> bool:''',
    '''def _arrow_mode() -> str:
    """箭头键语义：auto（默认，按手势判据区分）| history（永远翻历史）| scroll（永远滚日志）。

    为什么给开关：不同终端把触摸滑动编码成箭头流的**节奏差异极大**，无法用一套阈值
    覆盖所有设备。默认 auto 足够好；若在某个终端上仍误判，用户可以一键切换语义，
    不必改代码。
    """
    try:
        v = (os.environ.get("ONYX_TUI_ARROW_MODE") or "auto").strip().lower()
    except Exception:
        return "auto"
    return v if v in ("auto", "history", "scroll") else "auto"


def _arrow_cfg():
    """箭头手势参数 (N, 窗口秒, 单间隔上限, 空闲结束秒)，可用 env 微调。"""
    def _f(name, default, lo, hi):
        try:
            return max(lo, min(hi, float(os.environ.get(name) or default)))
        except Exception:
            return default
    n = int(_f("ONYX_TUI_ARROW_N", 4, 2, 12))
    return (n,
            _f("ONYX_TUI_ARROW_WINDOW", 1.2, 0.2, 5.0),
            _f("ONYX_TUI_ARROW_GAP", 0.35, 0.02, 2.0),
            _f("ONYX_TUI_ARROW_IDLE", 0.6, 0.1, 5.0))


def _input_debug_enabled() -> bool:
    """ONYX_TUI_INPUT_DEBUG=1 → 把原始输入字节落盘（默认关闭）。"""
    try:
        return (os.environ.get("ONYX_TUI_INPUT_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return False


def _debug_dump_input(raw: bytes) -> None:
    """把一批原始输入字节写进 ~/.config/onyx/tui_input_debug.log（hex + 时间戳）。

    用途：滑动手势判据目前是基于「终端行为推断」的阈值；有了真实字节流就能按事实定标
    （例如确认滑动到底发的是 ESC[A 流、还是 SGR 滚轮、还是别的编码）。
    有界：文件超过 512KB 自动截断重写，避免写爆用户磁盘。
    """
    try:
        path = os.path.join(os.path.expanduser("~"), ".config", "onyx", "tui_input_debug.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > 512 * 1024:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("# truncated\\n")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.6f} {raw.hex()}\\n")
    except Exception:
        pass


def _tui_mouse_enabled() -> bool:''',
)

# ── 2) 解码器里接抓包 ──
patch(
    '''    def decode(self, data: bytes, final: bool = False) -> str:
        try:
            clean = self._san.feed(data)''',
    '''    def decode(self, data: bytes, final: bool = False) -> str:
        if _DEBUG_INPUT and data:
            _debug_dump_input(data)
        try:
            clean = self._san.feed(data)''',
)

patch(
    '''class _SanitizingDecoder:
    """伪装成 codecs 增量解码器（LinuxDriver 只用到 .decode 这一个方法）。"""''',
    '''_DEBUG_INPUT = _input_debug_enabled()


class _SanitizingDecoder:
    """伪装成 codecs 增量解码器（LinuxDriver 只用到 .decode 这一个方法）。"""''',
)

# ── 3) 手势判据：滑动窗口计数 ──
patch(
    '''        # ── 触摸滑动 vs 真实按键 ──
        # 终端把「上下滑动」编码成**连续高速**的 ↑/↓ 键流（每个触摸帧一发，间隔约
        # 8~16ms）；人类按键 / 系统长按重复最快也就 ~50ms 一档。旧实现无条件当「翻历史」
        # → 手指一滑就哗啦啦翻过好几条历史（用户实测）。判据就用间隔：< 30ms 判为滑动流
        # → 改为滚动日志，并把这一滑误翻的历史**回滚**回去。
        _ARROW_GESTURE_GAP = 0.030
        _ARROW_GESTURE_IDLE = 0.15

        def _arrow_gesture_check(self) -> bool:
            """True = 这次 ↑/↓ 属于滑动流（应滚动日志而不是翻历史）。

            判定需要**连续 2 次**高速箭头（即第 3 个事件起生效）：
            单次快速连击（例如程序化连调 / 极快的手速）不会被误判成滑动。
            """
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
            return False''',
    '''        # ── 触摸滑动 vs 真实按键 ──
        # 终端把「上下滑动」编码成**一连串** ↑/↓（触摸帧驱动），人类按键则是零散的几下。
        # 早期判据（间隔 <30ms 且连续 2 次）在真机上仍会误判 —— 说明实际滑动流的节奏
        # 因终端而异（可能 40~150ms 一个）。这里改成**滑动窗口计数**：
        #   「N 次箭头落在 WINDOW 秒内，且相邻间隔 < GAP」→ 判定为滑动流。
        # 参数可用 env 调（ONYX_TUI_ARROW_N / _WINDOW / _GAP / _IDLE），语义还可用
        # ONYX_TUI_ARROW_MODE 直接切成 history / scroll。
        def _arrow_gesture_check(self) -> bool:
            """True = 这次 ↑/↓ 属于滑动流（应滚动日志而不是翻历史）。"""
            now = time.monotonic()
            gap = now - self._arrow_t
            self._arrow_t = now

            if self._arrow_mode == "history":
                return False
            if self._arrow_mode == "scroll":
                return True

            if gap >= self._ARROW_IDLE:
                # 空闲够久 → 上一段结束（误翻快照作废）
                self._arrow_gesture = False
                self._hist_snap = None
                self._arrow_ts = []
            self._arrow_ts.append(now)
            if len(self._arrow_ts) > self._ARROW_N:
                del self._arrow_ts[:-self._ARROW_N]
            if self._arrow_gesture:
                return True
            if (len(self._arrow_ts) >= self._ARROW_N
                    and (self._arrow_ts[-1] - self._arrow_ts[0]) <= self._ARROW_WINDOW
                    and gap < self._ARROW_GAP):
                self._arrow_gesture = True
                self._hist_snap_restore()   # 把这一滑误翻的历史原样退回
                return True
            return False''',
)

# ── 4) __init__：新状态 ──
patch(
    '''            self._arrow_t = 0.0          # 上一次 ↑/↓ 的时间戳
            self._arrow_fast = 0         # 连续高速箭头的计数（≥2 才判为滑动流）
            self._arrow_gesture = False  # 当前是否处于「滑动流」中''',
    '''            self._arrow_t = 0.0          # 上一次 ↑/↓ 的时间戳
            self._arrow_ts = []          # 最近 N 次箭头的时间戳（滑动窗口判据）
            self._arrow_gesture = False  # 当前是否处于「滑动流」中
            # 手势参数与语义（env 可调，见 _arrow_cfg / _arrow_mode）
            self._arrow_mode = _arrow_mode()
            (_an, _aw, _ag, _ai) = _arrow_cfg()
            self._ARROW_N, self._ARROW_WINDOW = _an, _aw
            self._ARROW_GAP, self._ARROW_IDLE = _ag, _ai''',
)


def main():
    with open(TUI, encoding="utf-8") as f:
        text = f.read()
    for old, new in P:
        n = text.count(old)
        if n != 1:
            print(f"FAIL: 命中 {n} 次\n{old[:200]}")
            return 1
        text = text.replace(old, new, 1)
    with open(TUI, "w", encoding="utf-8") as f:
        f.write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
