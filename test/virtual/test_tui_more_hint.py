#!/usr/bin/env python3
"""选项弹窗回归：一次显示 8 项 + 上下「还有 N 项」计数（双语）。

背景（用户反馈）：弹窗里一眼只看到 4 项时，用户会误以为「就这几个选项」。
现在列表一次显示 8 项，并在列表上下各加一行「↑ 上方还有 N 个选项 /
↓ 下方还有 N 个选项」（仅当确有隐藏项时显示），随高亮/滚动实时更新。
覆盖 SelectScreen / PlanConfirmScreen / HistorySearchScreen 三个弹窗 + 双语取词。

运行: python3 test/virtual/test_tui_more_hint.py
"""
import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import bin.ai_tui as tui  # noqa: E402

MORE_MAX = 8   # 一次显示的选项数


def _txt(widget) -> str:
    """取 Static 当前内容（Static 把内容存在 _Static__content）。"""
    return str(getattr(widget, "_Static__content", ""))


def test_i18n_keys():
    """两个语言块都必须有计数文案键（双语替换体系）。"""
    path = os.path.join(ROOT, "bin", "ai_lib", "lang.json")
    data = json.load(open(path, encoding="utf-8"))
    for lang in ("chinese", "english"):
        block = data.get(lang) or {}
        for key in ("tui_more_up", "tui_more_down"):
            assert key in block, f"lang.json[{lang}] 缺 {key}"
            assert "{n}" in block[key], f"{lang}.{key} 缺 {{n}} 占位符：{block[key]}"
    print("PASS 双语键齐备：tui_more_up / tui_more_down（含 {n} 占位符）")


def test_screens_use_mixin():
    """三个选项弹窗都必须接入 MoreHintMixin + 计数行 + _more_list。"""
    src = open(os.path.join(ROOT, "bin", "ai_tui.py"), encoding="utf-8").read()
    for cls in ("class SelectScreen(MoreHintMixin, ModalScreen)",
                "class PlanConfirmScreen(MoreHintMixin, ModalScreen)",
                "class HistorySearchScreen(MoreHintMixin, ModalScreen)"):
        assert cls in src, f"未接入 MoreHintMixin：{cls}"
    assert src.count("yield self._more_up_widget()") == 3, "三个弹窗都要有上方计数行"
    assert src.count("yield self._more_down_widget()") == 3, "三个弹窗都要有下方计数行"
    assert "max-height: 10" in src, "#modal-list 应给到 10（8 选项 + OptionList 自带 2 行边框）"
    print("PASS 三个选项弹窗均已接入计数行")


async def test_select_screen_more_hint():
    from textual.widgets import OptionList, Static

    App = tui._build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_more_")},
              {"lang": "chinese", "session_id": "t", "memory_mode": "global",
               "cwd": os.getcwd()})
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(tui._TUI_SELECT_SCREEN(
            "请选择：", [f"opt-{i:02d}" for i in range(1, 21)], lang="chinese"))
        await pilot.pause(0.4)
        ol = app.screen.query_one("#modal-list", OptionList)
        up_w = app.screen.query_one("#modal-more-up", Static)
        dn_w = app.screen.query_one("#modal-more-down", Static)

        # 一次显示 8 个选项（OptionList 的 size 已扣掉自带边框）
        assert ol.size.height == MORE_MAX, f"应一次显示 {MORE_MAX} 项，实际 {ol.size.height}"
        assert ol.option_count == 20
        assert not up_w.display, "第 1 项时上方不该有隐藏项"
        assert dn_w.display and "12" in _txt(dn_w), _txt(dn_w)
        assert "还有" in _txt(dn_w), _txt(dn_w)

        # 高亮下移 → 计数实时更新
        for _ in range(9):
            await pilot.press("down")
        await pilot.pause(0.3)
        assert up_w.display and "2" in _txt(up_w), _txt(up_w)
        assert dn_w.display and "10" in _txt(dn_w), _txt(dn_w)

        # 移到末项 → 下方计数行隐藏
        for _ in range(10):
            await pilot.press("down")
        await pilot.pause(0.3)
        assert "12" in _txt(up_w), _txt(up_w)
        assert not dn_w.display, "末项时下方不该有隐藏项"
    print("PASS SelectScreen：一次 8 项 + 上下计数随高亮实时更新")


async def test_select_screen_short_list():
    """选项数 ≤ 8 时不该出现任何计数行（不浪费屏幕）。"""
    from textual.widgets import OptionList, Static

    App = tui._build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_more_")},
              {"lang": "chinese", "session_id": "t", "memory_mode": "global",
               "cwd": os.getcwd()})
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(tui._TUI_SELECT_SCREEN(
            "请选择：", [f"opt-{i:02d}" for i in range(1, 6)], lang="chinese"))
        await pilot.pause(0.4)
        ol = app.screen.query_one("#modal-list", OptionList)
        assert ol.size.height == 5, ol.size.height
        assert not app.screen.query_one("#modal-more-up", Static).display
        assert not app.screen.query_one("#modal-more-down", Static).display
    print("PASS 短列表（≤8 项）不显示计数行")


async def test_bilingual():
    from textual.widgets import Static

    App = tui._build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_more_")},
              {"lang": "english", "session_id": "t", "memory_mode": "global",
               "cwd": os.getcwd()})
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(tui._TUI_SELECT_SCREEN(
            "Pick:", [f"opt-{i:02d}" for i in range(1, 21)], lang="english"))
        await pilot.pause(0.4)
        txt = _txt(app.screen.query_one("#modal-more-down", Static))
        assert "more option(s) below" in txt and "12" in txt, txt
        assert "还有" not in txt, f"英文界面不应出现中文：{txt}"
    print("PASS 英文界面取英文文案（双语替换体系）")


async def test_history_search_more_hint():
    """历史搜索弹窗同样有 8 项 + 上下计数。"""
    from textual.widgets import OptionList, Static

    App = tui._build_tui()
    app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_more_")},
              {"lang": "chinese", "session_id": "t", "memory_mode": "global",
               "cwd": os.getcwd()})
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.pause(0.2)
        hist = [f"history item {i:02d}" for i in range(1, 31)]
        app.push_screen(tui._TUI_HIST_SEARCH_SCREEN(hist, lang="chinese"))
        await pilot.pause(0.6)
        ol = app.screen.query_one("#hist-list", OptionList)
        dn_w = app.screen.query_one("#modal-more-down", Static)
        assert ol.option_count == 30, ol.option_count
        assert ol.size.height == MORE_MAX, ol.size.height
        assert dn_w.display and "22" in _txt(dn_w), _txt(dn_w)
    print("PASS HistorySearchScreen：一次 8 项 + 下方计数")


async def test_plan_confirm_more_hint():
    """计划确认弹窗（正文 + 选项同屏）：窄屏也不能溢出，列表仍露 8 项 + 下方计数。"""
    from textual.containers import Vertical
    from textual.widgets import OptionList, Static

    body = "\n".join(f"## 步骤 {i}\n- 做点事情 {i}" for i in range(1, 25))
    opts = [f"选项 {i:02d}" for i in range(1, 21)]
    for screen_h in (24, 30):
        App = tui._build_tui()
        app = App({"user_home_dir": tempfile.mkdtemp(prefix="onyx_more_")},
                  {"lang": "chinese", "session_id": "t", "memory_mode": "global",
                   "cwd": os.getcwd()})
        async with app.run_test(size=(80, screen_h)) as pilot:
            await pilot.pause(0.2)
            app.push_screen(tui._TUI_PLAN_SCREEN("计划已就绪，确认执行？", opts, "选项 01",
                                                 body=body, lang="chinese"))
            await pilot.pause(0.6)
            modal = app.screen.query_one("#modal", Vertical)
            ol = app.screen.query_one("#modal-list", OptionList)
            body_w = app.screen.query_one("#modal-body")
            dn_w = app.screen.query_one("#modal-more-down", Static)
            assert modal.size.height <= int(screen_h * 0.88) + 1, \
                f"{screen_h} 行屏下弹窗溢出：{modal.size.height}"
            assert ol.size.height == MORE_MAX, ol.size.height
            assert body_w.size.height >= 3, f"正文区被挤没了：{body_w.size.height}"
            assert dn_w.display and "12" in _txt(dn_w), _txt(dn_w)
    print("PASS PlanConfirmScreen：24/30 行屏下不溢出，列表 8 项 + 正文可读")


async def _run_all_async():
    await test_select_screen_more_hint()
    await test_select_screen_short_list()
    await test_bilingual()
    await test_history_search_more_hint()
    await test_plan_confirm_more_hint()


def main():
    test_i18n_keys()
    test_screens_use_mixin()
    asyncio.run(_run_all_async())
    print("\nALL PASS")


if __name__ == "__main__":
    main()
