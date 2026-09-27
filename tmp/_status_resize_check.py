# -*- coding: utf-8 -*-
"""验证：状态栏窄屏折行 + resize 后按新宽度重排（无异常）。"""
import asyncio, os, sys, tempfile, time, uuid, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


async def main():
    from bin.ai_tui import _build_tui, _enable_alt_enter_keys
    from bin.ai_lib import mode as _mode
    _enable_alt_enter_keys()
    ctx = {"lang": "chinese", "session_id": str(uuid.uuid4()), "memory_mode": "global",
           "cwd": os.getcwd(), "quiet": False, "show_time": True,
           "session_start": time.time(), "mode": "normal"}
    App = _build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_rc_")}, ctx)
    ok = True
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.4)
        _mode.set_render_mode("tui")
        st = {"cwd": os.getcwd(), "ctx": 17214, "cache_pct": 87.3, "balance": "¥42.10"}

        def lines_of():
            bar = app.query_one("#status-bar")
            c = bar.content
            txt = c.plain if hasattr(c, "plain") else str(c)
            return txt.split("\n")

        app._render_status(st)
        await pilot.pause(0.2)
        wide = lines_of()
        print("WIDE(100) ->", wide)

        # 模拟 resize 到窄屏（46 列）：应触发 on_resize 内的重排
        from textual.geometry import Size
        app._size = Size(46, 40)   # 让 self.size.width 真正变小
        ev = types.SimpleNamespace(size=types.SimpleNamespace(width=46, height=40))
        app.on_resize(ev)
        await pilot.pause(0.2)
        narrow = lines_of()
        print("NARROW(46) ->", narrow)

        # 断言
        assert app._last_status.get("ctx") == 17214, "状态未被记录"
        assert any("ctx" in l for l in narrow), "窄屏丢了 ctx"
        assert any("cache" in l for l in narrow), "窄屏丢了 cache"
        assert any(l.strip().startswith("◆") for l in narrow), "窄屏丢了 path 锚点"
        print("RESIZE RERENDER OK")

    print("ALL PASS" if ok else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
