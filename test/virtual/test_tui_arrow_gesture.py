#!/usr/bin/env python3
"""触摸滑动识别回归：滑动不能被当成「历史上下翻」，也不能让历史在输入框里频闪。

判据（2026-09 升级）：终端把触摸滑动编码成**一连串** ↑/↓，人类按键则是零散几下。
  「N 次箭头（默认 4）落在 WINDOW 秒（默认 1.2s）内，且相邻间隔 < GAP（默认 0.35s）」
  → 判为滑动流 → 滚日志，并把「误翻」一次性还原（不改历史、不污染输入框）。
语义可用 ONYX_TUI_ARROW_MODE=auto|history|scroll 切换；参数用 ONYX_TUI_ARROW_* 调。

⚠️ 与旧版的关键差异：↑/↓ 现在是**延迟应用**（_hist_nav → set_timer → _hist_commit）：
  - 箭头间隔 < DEFER(0.08) 时永不提交 → 输入框一个字都不会变（消除频闪）；
  - 箭头间隔 < GAP(0.35) 时改用 GAP 作延迟（可能是滑动，先不提交）；
  - 孤立按键走 DEFER(0.08) → 跟手。
  因此「按键之后」必须 `await asyncio.sleep(...)` 让事件循环跑到定时器 ——
  必须用 await（time.sleep 不会推进事件循环）。

运行: python3 test/virtual/test_tui_arrow_gesture.py
"""
import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

try:
    import textual  # noqa: F401
except Exception as e:  # pragma: no cover
    print(f"SKIP: textual 不可用（{e}）")
    raise SystemExit(0)

SWIPE_GAP = 0.008      # 触摸帧驱动的滑动流：8ms
SLOW_SWIPE = 0.12      # 慢节奏滑动流：120ms（旧判据 30ms 抓不到，新窗口判据能抓到）
HUMAN_GAP = 0.450      # 人类按键：450ms（> GAP，不被当成滑动）


def test_env_knobs():
    from bin.ai_tui import _arrow_mode, _arrow_cfg

    old = {k: os.environ.get(k) for k in
           ("ONYX_TUI_ARROW_MODE", "ONYX_TUI_ARROW_N", "ONYX_TUI_ARROW_WINDOW")}
    try:
        os.environ.pop("ONYX_TUI_ARROW_MODE", None)
        assert _arrow_mode() == "auto", "默认应为 auto"
        os.environ["ONYX_TUI_ARROW_MODE"] = "scroll"
        assert _arrow_mode() == "scroll"
        os.environ["ONYX_TUI_ARROW_MODE"] = "garbage"
        assert _arrow_mode() == "auto", "非法值应回退 auto"
        os.environ["ONYX_TUI_ARROW_N"] = "6"
        assert _arrow_cfg()[0] == 6, "N 应可由 env 调整"
        os.environ["ONYX_TUI_ARROW_N"] = "999"
        assert _arrow_cfg()[0] == 12, "N 应被钳制在上限内"
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("PASS 手势参数与语义开关：env 可调、非法值回退、越界钳制")


def test_input_debug_capture(tmp_home):
    """ONYX_TUI_INPUT_DEBUG=1 → 原始字节落盘（hex）。"""
    from bin.ai_tui import _SanitizingDecoder
    old_home = os.environ.get("HOME")
    old_flag = os.environ.get("ONYX_TUI_INPUT_DEBUG")
    try:
        os.environ["HOME"] = tmp_home
        os.environ["ONYX_TUI_INPUT_DEBUG"] = "1"
        _SanitizingDecoder().decode(b"abc", final=False)
        path = os.path.join(tmp_home, ".config", "onyx", "tui_input_debug.log")
        assert os.path.exists(path), "开启抓包后应生成日志文件"
        content = open(path, encoding="utf-8").read()
        assert "616263" in content, f"应记录 hex（abc→616263），实际 {content!r}"
        os.environ.pop("ONYX_TUI_INPUT_DEBUG", None)
        _SanitizingDecoder().decode(b"zzz", final=False)
        content2 = open(path, encoding="utf-8").read()
        assert content2 == content, "关闭抓包后不应再写入"
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        if old_flag is not None:
            os.environ["ONYX_TUI_INPUT_DEBUG"] = old_flag
    print("PASS 输入抓包：开启写 hex、关闭不写")


async def _run():
    from bin.ai_tui import _build_tui
    from textual.widgets import Input

    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})
    async with app.run_test(size=(100, 30)) as pilot:
        log = app.query_one("#log")
        app._log([f"第 {i} 行日志内容" for i in range(300)])
        await pilot.pause()

        inp = app.query_one("#prompt", Input)
        inp.focus()
        await pilot.pause()
        N = app._ARROW_N
        IDLE = app._ARROW_IDLE

        async def _reset(hist_idx=None):
            inp.value = ""
            app._hist_prog_values = []
            app._hist_cancel_pending()
            app._hist_snap = None
            await pilot.pause()
            app._hist = ["旧命令A", "旧命令B", "旧命令C"]
            app._hist_idx = len(app._hist) if hist_idx is None else hist_idx
            app._hist_draft = ""
            app._arrow_t = 0.0
            app._arrow_ts = []
            app._arrow_gesture = False

        async def _settle(secs=0.45):
            """让事件循环跑到「延迟提交」的定时器（必须 await，time.sleep 推不动循环）。"""
            await asyncio.sleep(secs)
            await pilot.pause()

        # ── 0) 少于 N 次快速箭头 → 不判为滑动（避免误伤快速连按）──
        await _reset()
        for _ in range(N - 1):
            app._hist_nav(-1)
            await asyncio.sleep(SWIPE_GAP)
        assert app._arrow_gesture is False, f"仅 {N - 1} 次箭头不应判为滑动流"
        await _settle()
        assert inp.value == "旧命令C" and app._hist_idx == 2, \
            f"非滑动流应正常翻历史，实际 {inp.value!r}/{app._hist_idx}"
        print(f"PASS 仅 {N - 1} 次快速箭头不误判（阈值 N={N}），且连按不丢步")

        # ── 1) 高速滑动流：不改历史、改为滚动日志 ──
        await _reset()
        before = log.scroll_y
        for _ in range(8):
            app._hist_nav(-1)
            await asyncio.sleep(SWIPE_GAP)
        await _settle()
        assert app._hist_idx == len(app._hist), \
            f"滑动流不应改变历史游标，实际 {app._hist_idx}/{len(app._hist)}"
        assert inp.value == "", f"滑动流不应把历史内容灌进输入框：{inp.value!r}"
        assert app._arrow_gesture is True, "应处于滑动流状态"
        assert log.scroll_y < before, f"滑动流应滚动日志：{before} → {log.scroll_y}"
        print(f"PASS 高速滑动流：历史未动、输入框未污染，日志滚动 {before}→{log.scroll_y}")

        # ── 2) 慢节奏滑动流（120ms/个）—— 旧 30ms 判据抓不到，窗口判据能抓到 ──
        await asyncio.sleep(IDLE + 0.05)
        await _reset()
        before = log.scroll_y
        for _ in range(N + 2):
            app._hist_nav(-1)
            await asyncio.sleep(SLOW_SWIPE)
        await _settle()
        assert app._arrow_gesture is True, "120ms 节奏的连续箭头也应判为滑动流"
        assert app._hist_idx == len(app._hist), f"应回滚误翻，实际 {app._hist_idx}"
        assert inp.value == "", f"慢速滑动也应还原输入框，实际 {inp.value!r}"
        assert log.scroll_y < before, "慢节奏滑动流同样应滚动日志"
        print(f"PASS 慢节奏滑动流（{int(SLOW_SWIPE*1000)}ms/个）被识别并一次性回滚（无频闪残留）")

        # ── 3) 空闲后恢复正常：↑ 正常翻历史 ──
        await asyncio.sleep(IDLE + 0.05)
        await _reset()
        app._hist_nav(-1)
        assert app._arrow_gesture is False, f"空闲 >{IDLE}s 后应退出滑动流状态"
        await _settle(0.2)
        assert app._hist_idx == 0 and inp.value == "旧命令A", \
            f"应正常翻历史，实际 idx={app._hist_idx} val={inp.value!r}"
        print("PASS 空闲结束后恢复正常：↑ 正常翻历史")

        # ── 4) 人类节奏（450ms、3 次）仍逐条翻历史 ──
        await asyncio.sleep(IDLE + 0.05)
        await _reset()
        seen = []
        for _ in range(3):
            app._hist_nav(-1)
            await asyncio.sleep(HUMAN_GAP)
            seen.append(inp.value)
        assert app._hist_idx == 2, f"连续正常按键应逐条上翻，实际 {app._hist_idx}"
        assert seen == ["旧命令A", "旧命令B", "旧命令C"], f"翻历史顺序异常：{seen}"
        print(f"PASS 人类节奏按键逐条翻历史：{seen}")

        # ── 5) ↓ 方向同样识别 ──
        await asyncio.sleep(IDLE + 0.05)
        await _reset(hist_idx=1)
        before = log.scroll_y
        for _ in range(N + 2):
            app._hist_nav(1)
            await asyncio.sleep(SWIPE_GAP)
        await _settle()
        assert app._arrow_gesture is True
        assert app._hist_idx == 1, f"↓ 滑动流不应改动历史游标，实际 {app._hist_idx}"
        assert log.scroll_y > before, "↓ 滑动流应向下滚动日志"
        print(f"PASS ↓ 滑动流同样被识别（日志滚动 {before}→{log.scroll_y}）")

        # ── 6) 真实按键路径（走事件循环 + 异步 Changed）──
        await asyncio.sleep(IDLE + 0.05)
        await _reset()
        seen = []
        for _ in range(3):
            await pilot.press("up")
            await asyncio.sleep(HUMAN_GAP)
            await pilot.pause()
            seen.append(inp.value)
        assert seen == ["旧命令A", "旧命令B", "旧命令C"], f"真实按键翻历史异常：{seen}"
        print(f"PASS 真实按键路径（含异步 Changed）逐条翻历史：{seen}")

        # ── 7) 语义开关：history → 永不判滑动；scroll → 永远判滑动 ──
        await asyncio.sleep(IDLE + 0.05)
        await _reset()
        app._arrow_mode = "history"
        for _ in range(N + 2):
            app._hist_nav(-1)
            await asyncio.sleep(SWIPE_GAP)
        await _settle()
        assert app._arrow_gesture is False, "mode=history 时不应判为滑动流"
        assert app._hist_idx != len(app._hist), "mode=history 时应正常翻历史"
        print("PASS ONYX_TUI_ARROW_MODE=history：箭头永远翻历史")

        await asyncio.sleep(IDLE + 0.05)
        await _reset()
        app._arrow_mode = "scroll"
        before = log.scroll_y
        app._hist_nav(-1)
        await _settle(0.2)
        assert app._hist_idx == len(app._hist), "mode=scroll 时不应翻历史"
        assert log.scroll_y < before, "mode=scroll 时应滚动日志"
        app._arrow_mode = "auto"
        print("PASS ONYX_TUI_ARROW_MODE=scroll：箭头永远滚日志")


def main():
    import tempfile
    test_env_knobs()
    with tempfile.TemporaryDirectory() as tmp:
        test_input_debug_capture(tmp)
    asyncio.run(_run())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
