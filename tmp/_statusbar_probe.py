# -*- coding: utf-8 -*-
"""离线复现：TUI 状态栏（#status-bar）在「空闲态」是否可见。

关注：display / 高度 / 屏幕实际渲染内容，以及 layout=False 更新后是否塌陷。
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def dump(app, tag):
    bar = app.query_one("#status-bar")
    print(f"\n=== {tag} ===")
    print("  display =", bar.display)
    print("  size    =", bar.size, " region =", bar.region)
    print("  content =", repr(str(getattr(bar, "_Static__content", ""))[:120]))
    try:
        scr = app.screen
        print("  screen size =", scr.size)
    except Exception as e:
        print("  screen err:", e)


def screen_lines(app):
    """抓取当前渲染画面（Textual 的 screen 有 _compositor；用 export_text 更稳）。"""
    try:
        from textual.geometry import Region
        out = []
        for y in range(app.size.height):
            row = []
            for x in range(app.size.width):
                seg = app.screen.get_style_at(x, y) if False else None
            out.append(row)
        return out
    except Exception:
        return []


async def main():
    from bin.ai_tui import _build_tui
    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})
    async with app.run_test(size=(60, 24)) as pilot:
        await pilot.pause()
        dump(app, "1) 挂载后（on_mount 里 _render_status({cwd})）")

        # 模拟「AI 跑完一轮」推送完整状态
        app._render_status({"cwd": "/data/data/com.termux/files/home/proj",
                            "ctx": 17214, "cache_pct": 88.4,
                            "cache_supported": True, "balance": "12.34 CNY",
                            "balance_platform": "deepseek"})
        await pilot.pause()
        dump(app, "2) 首轮 _push_status_bar（完整字段）")

        # 再推一次「同样行数」→ layout=False 路径
        app._render_status({"cwd": "/data/data/com.termux/files/home/proj",
                            "ctx": 18000, "cache_pct": 90.0,
                            "cache_supported": True, "balance": "12.34 CNY",
                            "balance_platform": "deepseek"})
        await pilot.pause()
        dump(app, "3) 第二轮同样行数（layout=False 路径）")

        # 空闲：清掉活动行
        app._set_thinking(False)
        await pilot.pause()
        dump(app, "4) 空闲（thinking 关闭）")

        # 直接抓取屏幕文本
        try:
            from textual.geometry import Region
            regions = []
            for w in app.screen.walk_children():
                try:
                    if w.id == "status-bar":
                        regions.append((w.id, w.display, w.region, w.size))
                except Exception:
                    pass
            print("\n  status-bar 节点：", regions)
        except Exception as e:
            print("  walk err:", e)


if __name__ == "__main__":
    asyncio.run(main())
