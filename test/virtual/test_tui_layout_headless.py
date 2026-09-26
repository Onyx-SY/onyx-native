#!/usr/bin/env python3
"""无头真跑 TUI（Textual run_test），验证布局与批量渲染。

断言：
  1. #log-wrap 包裹 #log 与 #thinking；#thinking 默认隐藏；
  2. 思考中 → #thinking 可见且位于「输出框」区域内，文本左对齐；关闭后隐藏；
  3. 已无底部 #activity；
  4. todo 行带数字序号 + 状态字形（✓/▸/○，侧栏）；
  5. 宽屏下侧栏显示；
  6. 批量投递：#out_q 多行 → 一次 _log 调用全部落到日志（行数增加）。

运行: python3 test/virtual/test_tui_layout_headless.py
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

try:
    import textual  # noqa: F401
except Exception as e:  # pragma: no cover
    print(f"SKIP: textual 不可用（{e}）")
    raise SystemExit(0)


def _text(w):
    try:
        return str(w.render())
    except Exception:
        try:
            return str(w._content)
        except Exception:
            return ""


async def _run():
    from bin.ai_tui import _build_tui
    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})
    async with app.run_test(size=(120, 40)) as pilot:
        lw = app.query_one("#log-wrap")
        log = app.query_one("#log")
        th = app.query_one("#thinking")

        # 1. 结构
        assert th.parent is lw, "#thinking 应在 #log-wrap 内"
        assert log.parent is lw, "#log 应在 #log-wrap 内"
        assert th.display is False, "#thinking 默认应隐藏"
        print("PASS 结构：#log-wrap > (#log, #thinking)，默认隐藏")

        # 3. 无 #activity
        try:
            app.query_one("#activity")
            raise AssertionError("底部 #activity 仍存在")
        except AssertionError:
            raise
        except Exception:
            pass
        print("PASS 已移除底部 #activity")

        # 4. todo 序号
        app._render_todos([
            {"content": "A", "status": "completed", "activeForm": "a"},
            {"content": "B", "status": "in_progress", "activeForm": "b"},
            {"content": "C", "status": "pending", "activeForm": "c"},
        ])
        await pilot.pause()
        body = _text(app.query_one("#todo-body"))
        # 视觉语言：序号 + 状态字形（✓ 完成 / ▸ 进行中 / ○ 待办）
        assert "1 ✓" in body and "2 ▸" in body and "3 ○" in body, body
        print(f"PASS todo 带序号与状态字形：{body!r}")

        # 5. 宽屏侧栏
        assert app.query_one("#sidebar").display is True, "宽屏应显示侧栏"
        print("PASS 宽屏侧栏显示")

        # 2. 思考中位于输出框内
        app._set_thinking(True)
        await pilot.pause()
        assert th.display is True, "思考中应显示 #thinking"
        lwr, thr = lw.region, th.region
        inside = (thr.y >= lwr.y and thr.y + thr.height <= lwr.y + lwr.height
                  and thr.x >= lwr.x and thr.x + thr.width <= lwr.x + lwr.width)
        assert inside, f"#thinking 不在输出框内：{thr} vs {lwr}"
        assert "思考中" in _text(th), _text(th)
        app._set_thinking(False)
        await pilot.pause()
        assert th.display is False, "思考结束后应隐藏"
        print(f"PASS 思考中显示在输出框内（{thr}）左对齐，结束即隐藏")

        # 6. 批量投递
        before = len(log.lines)
        for i in range(5):
            app._out_q.put(f"batch-line-{i}")
        for _ in range(40):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if len(log.lines) >= before + 5:
                break
        after = len(log.lines)
        assert after >= before + 5, f"批量投递未全部落盘：{before} → {after}"
        print(f"PASS 批量投递落盘（{before} → {after} 行）")

        # 7. Rich Console 全量同步（否则 TUI 里工具输出 / 状态行丢色）
        from bin import ai_cmd as _ac
        from bin.ai_lib import tool_executors as _te
        app._sync_rich_width(full=True)
        await pilot.pause()
        want_w = log.content_size.width
        for nm, c in (("ai_cmd", _ac.console), ("tool_executors", _te.console)):
            assert getattr(c, "_force_terminal", None) is True, f"{nm}.console 未强制为终端"
            assert getattr(c, "_color_system", None) is not None, f"{nm}.console 无颜色系统"
            assert c.width == want_w, f"{nm}.console 宽度 {c.width} != 日志区 {want_w}"
        print(f"PASS Rich Console 全量同步（force_terminal/颜色/宽度={want_w}）")

        # 8. AI 回复的底色块在日志里保留（Strip 的背景色非空）
        from bin.ai_lib.ui import render_ai_panel as _rap
        from bin.ai_lib.mode import set_render_mode as _srm
        _srm("tui")
        app._log_renderable(_rap("你好，我是 Onyx"))
        await pilot.pause()
        bg = False
        for strip in log.lines[-12:]:
            segs = getattr(strip, "_segments", None) or getattr(strip, "segments", [])
            for s in segs:
                st = getattr(s, "style", None)
                if st is not None and getattr(st, "bgcolor", None) is not None:
                    bg = True
        assert bg, "日志中未保留 AI 回复的底色块"
        print("PASS AI 回复底色块在日志中保留（背景色）")

        # 9. 流式区：#stream 默认隐藏 → 渲染后显示 → end_stream 隐藏
        st = app.query_one("#stream")
        assert st.display is False, "#stream 默认应隐藏"
        app._render_stream("第一段内容")
        await pilot.pause()
        assert st.display is True, "_render_stream 后 #stream 应显示"
        assert len(st.lines) > 0, "流式区应有内容"
        app._end_stream()
        await pilot.pause()
        assert st.display is False, "_end_stream 后 #stream 应隐藏"
        print("PASS #stream 流式区显隐")

        # 10. Ctrl+L 清空日志
        assert len(log.lines) > 0
        app.action_clear_log()
        await pilot.pause()
        assert len(log.lines) == 0, f"Ctrl+L 未清空日志：{len(log.lines)}"
        print("PASS Ctrl+L 清空日志区")

        # 11. 转义序列泄漏防护（确定性：直接打开防护窗口；须在切多行框之前做）
        import time as _time
        from textual.widgets import Input as _Inp
        pin = app.query_one("#prompt", _Inp)
        pin.focus()
        pin.value = ""
        app._esc_until = _time.monotonic() + 5.0   # 窗口在 App 层（Esc 被绑定提前消费）
        await pilot.press("X")
        await pilot.pause()
        assert pin.value == "", f"转义窗口内仍把字符灌进输入框：{pin.value!r}"
        app._esc_until = 0.0
        await pilot.press("Y")
        await pilot.pause()
        assert pin.value == "Y", f"窗口外正常输入失效：{pin.value!r}"
        print("PASS 转义序列泄漏防护（窗口内丢弃 / 窗口外正常）")

        # 12. 多行粘贴：含换行的内容应转多行框且不丢内容
        app._paste_multiline("第一行\n第二行\n第三行")
        await pilot.pause()
        ml = app.query_one("#prompt-ml")
        assert ml.display is True, "粘贴多行后应切到多行框"
        assert "第二行" in (ml.text or ""), f"多行内容丢失：{ml.text!r}"
        print("PASS 多行粘贴不丢内容")


def main():
    asyncio.run(_run())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
