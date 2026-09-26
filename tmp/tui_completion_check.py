# -*- coding: utf-8 -*-
"""step-4/5/7 验证：TUI 补全下拉菜单（Tab/↑↓/Esc）+ Ctrl+C 取消语义。

用法：python3 tmp/tui_completion_check.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin.ai_tui import _build_tui  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(name)
    return cond


async def main():
    App = _build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_menu_")},
              {"lang": "chinese", "session_id": "sess-1", "memory_mode": "global",
               "cwd": os.getcwd()})

    async with app.run_test(size=(100, 30)) as pilot:
        inp = app.query_one("#prompt")
        menu = app.query_one("#complete-menu")

        # ── 1. 输入 / 弹出菜单 ──
        check("初始菜单隐藏", not menu.display)
        await pilot.press("/")
        await pilot.pause(0.3)
        check("输入 / → 菜单弹出", menu.display)
        check("菜单候选=全部斜杠命令", menu.option_count == 29, menu.option_count)

        # ── 2. 继续输入 → 过滤 ──
        for ch in "he":
            await pilot.press(ch)
        await pilot.pause(0.3)
        check("输入 /he → 过滤到 /help", menu.option_count == 1, menu.option_count)
        check("输入框内容正确", inp.value == "/he", inp.value)

        # ── 3. Tab 接受 ──
        await pilot.press("tab")
        await pilot.pause(0.3)
        check("Tab 补全 → /help", inp.value == "/help", inp.value)
        check("补全后菜单收起（/help 无更多候选）", not menu.display or menu.option_count == 0,
              menu.option_count)

        # ── 4. ↑↓ 选择 + Tab ──
        inp.value = ""
        await pilot.press("/")
        await pilot.pause(0.3)
        first = inp.value
        check("重新输入 / 又弹出菜单", menu.display and menu.option_count == 29, menu.option_count)
        await pilot.press("down", "down")
        await pilot.pause(0.2)
        check("↓↓ 移动高亮", menu.highlighted == 2, menu.highlighted)
        await pilot.press("tab")
        await pilot.pause(0.3)
        check("Tab 接受第 3 项", inp.value not in ("", "/") and inp.value != first, inp.value)

        # ── 5. Esc 关闭菜单（不退出 App） ──
        inp.value = ""
        await pilot.press("/")
        await pilot.pause(0.3)
        await pilot.press("escape")
        await pilot.pause(0.3)
        check("Esc 关闭菜单", not menu.display)
        check("Esc 没有退出 App", app.is_running)

        # ── 6. 参数枚举 / 路径候选 ──
        inp.value = "/lang "
        await pilot.pause(0.4)
        check("/lang 空格 → 枚举候选 cn/en", menu.display and menu.option_count == 2, menu.option_count)
        await pilot.press("tab")
        await pilot.pause(0.2)
        check("Tab 补成 /lang cn", inp.value == "/lang cn", inp.value)

        inp.value = "/cd bin"
        await pilot.pause(0.4)
        check("/cd bin → 路径候选", menu.display and menu.option_count >= 1, menu.option_count)

        # ── 7. 普通输入不弹菜单 ──
        inp.value = "你好世界"
        await pilot.pause(0.4)
        check("普通文本不弹菜单", not menu.display)

        # ── 8. Ctrl+C：空闲时清空输入，不退出 ──
        inp.value = "半截输入"
        await pilot.pause(0.2)
        await pilot.press("ctrl+c")
        await pilot.pause(0.3)
        check("Ctrl+C 空闲 → 清空输入框", inp.value == "", repr(inp.value))
        check("Ctrl+C 没有退出 TUI", app.is_running)

        # ── 9. Ctrl+C：忙时取消生成（置中断标志） ──
        from bin.ai_lib import mcp_state
        mcp_state._AI_INTERRUPTED = False
        app._busy = True
        await pilot.press("ctrl+c")
        await pilot.pause(0.3)
        check("Ctrl+C 忙 → 置中断标志", bool(mcp_state._AI_INTERRUPTED))
        check("Ctrl+C 忙 → 仍不退出", app.is_running)
        app._busy = False
        mcp_state._AI_INTERRUPTED = False

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：{FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
