#!/usr/bin/env python3
"""AI 按键绑定可配置回归。

要求（用户）：**所有属于 AI 的绑定键都要能换**，用户通过配置窗口自由选择；
默认保持原有键位（含 Alt+Enter）。

覆盖：
  1. 注册表：动作唯一、分组合法、默认键全部可解析；
  2. 读写：改键落盘 → 新进程读取生效；恢复默认；非法键/未知动作被拒；
  3. 冲突检测只在同分组内（TUI 与对话模式同键互不影响）；
  4. TUI：Binding 由注册表生成，改键后重建即生效（含多行框）；
  5. AI 对话模式：ptk 绑定由注册表生成；
  6. /config 菜单有「⌨️ 按键设置」入口。

运行: python3 test/virtual/test_ai_keymap.py
"""
import asyncio
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bin.ai_lib import keymap as km  # noqa: E402


def test_registry():
    ids = [a[0] for a in km.ACTIONS]
    assert len(ids) == len(set(ids)), "动作 id 必须唯一"
    all_defaults = km.defaults()
    for aid, group, cn, en, _raw in km.ACTIONS:
        assert group in km.GROUP_ORDER, f"{aid}: 分组非法 {group}"
        assert cn and en, f"{aid}: 缺双语说明"
        resolved = all_defaults.get(aid) or []
        assert resolved, f"{aid}: 默认键不能为空"
        for c in resolved:
            assert km.normalize_combo(c) == c, f"{aid}: 默认键不规范 {c}"
        assert km.to_ptk(resolved[0]), f"{aid}: 默认键无法转 ptk"
    # 多行/换行键的平台化默认：桌面 Shift+Enter，Termux Alt+Enter；两种键互为别名
    ml = km.combos("tui.multiline")
    primary = km.multiline_default_keys()[0]
    assert ml[0] == primary, f"首选键应为 {primary}，实际 {ml[0]}"
    assert "alt+enter" in ml and "shift+enter" in ml, f"两种键都应保留：{ml}"
    assert primary == ("alt+enter" if km.is_termux() else "shift+enter")
    assert "alt+enter" in km.combos("multiline.send")
    assert km.combos("tui.history_search") == ["ctrl+r"]
    print(f"PASS 注册表：{len(ids)} 个动作，默认键全部合法"
          f"（多行首选 {primary}，Termux={km.is_termux()}）")


def test_persistence():
    home = tempfile.mkdtemp(prefix="onyx_km_")
    km.init(home)
    assert km.combos("tui.history_search") == ["ctrl+r"]
    ok, err = km.set_binding("tui.history_search", ["f2"])
    assert ok, err
    assert km.combos("tui.history_search") == ["f2"]
    assert os.path.exists(km.config_path())
    raw = json.load(io.open(km.config_path(), encoding="utf-8"))
    assert raw["bindings"]["tui.history_search"] == ["f2"]

    # 模拟新进程：清缓存后重新加载
    km._OVERRIDES = None
    km._HOME = None
    km.init(home)
    assert km.combos("tui.history_search") == ["f2"], "落盘后应能被重新读取"

    # 非法 / 未知
    assert km.set_binding("tui.history_search", ["not-a-key!!"])[0] is False
    assert km.set_binding("nope.nope", ["f2"])[0] is False
    assert km.normalize_combo("") is None
    assert km.normalize_combo("ctrl+ctrl+r") == "ctrl+r"

    # 恢复
    km.reset("tui.history_search")
    assert km.combos("tui.history_search") == ["ctrl+r"]
    km.set_binding("tui.quit", ["f9"])
    km.reset_all()
    assert km.combos("tui.quit") == ["ctrl+q"]
    print("PASS 读写：改键落盘/重载/恢复默认/非法键被拒")


def test_conflicts_are_group_scoped():
    home = tempfile.mkdtemp(prefix="onyx_km_")
    km.init(home)
    km.reset_all()
    conf = km.conflicts()
    # 默认状态同分组内不应有冲突（TUI 与 REPL 的同名键不算）
    assert conf == [], f"默认不该有同组冲突：{conf}"
    km.set_binding("tui.history_search", ["ctrl+q"])      # 与 tui.quit 同组冲突
    conf = km.conflicts()
    assert any(c == "ctrl+q" for c, _ in conf), conf
    km.reset_all()
    print("PASS 冲突检测：只在同分组内报冲突")


def test_ptk_conversion():
    assert km.to_ptk("ctrl+r") == ["c-r"]
    assert km.to_ptk("alt+enter") == ["a-enter"]
    assert km.to_ptk("shift+tab") == ["s-tab"]
    assert km.to_ptk("escape,enter") == ["escape", "enter"]
    assert km.to_ptk("pageup") == ["pageup"]
    assert km.to_ptk("f2") == ["f2"]
    assert km.to_ptk("bogus+key") == []
    print("PASS ptk 键名转换")


def test_tui_bindings_follow_registry():
    import bin.ai_tui as tui
    from textual.widgets import Input

    home = tempfile.mkdtemp(prefix="onyx_km_")
    km.init(home)
    km.reset_all()

    tui._TUI_APP_CLASS = None
    App = tui._build_tui()
    keys = [b.key for b in App.BINDINGS]
    for expect in ("ctrl+c", "ctrl+q", "ctrl+r", "pageup", "alt+enter", "alt+b"):
        assert expect in keys, f"主界面缺默认键 {expect}：{keys}"

    # 改键 → 重建即生效
    assert km.set_binding("tui.history_search", ["f2"])[0]
    assert km.set_binding("tui.multiline", ["f4"])[0]        # 进入多行框
    assert km.set_binding("multiline.send", ["f5"])[0]       # 多行框内发送
    tui._TUI_APP_CLASS = None
    App2 = tui._build_tui()
    keys2 = [b.key for b in App2.BINDINGS]
    assert "f2" in keys2 and "ctrl+r" not in keys2, keys2
    assert "f4" in keys2 and "alt+enter" not in keys2, keys2

    submitted = []
    app = App2({"user_home_dir": home},
               {"lang": "chinese", "session_id": "t", "memory_mode": "global",
                "cwd": os.getcwd()})
    app._submit = lambda text: submitted.append(text)

    async def _run():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.2)
            app.query_one("#prompt", Input).focus()
            await pilot.press("f4")      # 注册表里的新键 → 应进入多行框
            await pilot.pause(0.3)
            ml = app.query_one("#prompt-ml")
            assert ml.display, "按新键应进入多行框"
            ml.focus()
            ml.text = "line1\nline2"
            await pilot.pause(0.2)
            await pilot.press("f5")      # 注册表里的新发送键
            await pilot.pause(0.4)
            assert submitted and "line1" in submitted[0], f"新发送键应提交内容：{submitted}"
    asyncio.run(_run())
    km.reset_all()
    tui._TUI_APP_CLASS = None
    print("PASS TUI：主界面 + 多行框绑定均由注册表生成，改键即生效")


def test_repl_bindings_follow_registry():
    src = io.open(os.path.join(ROOT, "bin", "ai_interactive.py"), encoding="utf-8").read()
    for aid in ("repl.submit", "repl.newline", "repl.complete", "repl.complete_prev",
                "repl.cancel", "repl.quit"):
        assert f'_add_binding("{aid}"' in src, f"对话模式未接入注册表：{aid}"
    # 旧的硬编码装饰器必须已移除
    for stale in ("@_kb.add('enter', eager=True", "@_kb.add('escape', 'enter', eager=True",
                  "@_kb.add('c-i', eager=True", "@_kb.add('s-tab', eager=True",
                  "@_kb.add('c-c', eager=True", "@_kb.add('c-d', filter="):
        assert stale not in src, f"仍残留硬编码绑定：{stale}"
    print("PASS AI 对话模式：ptk 绑定全部改由注册表生成")


def test_config_menu_entry():
    src = io.open(os.path.join(ROOT, "bin", "ai_interactive.py"), encoding="utf-8").read()
    assert "⌨️ 按键设置" in src and "⌨️ Key bindings" in src, "/config 缺按键设置入口"
    assert "def _keymap_menu(" in src
    assert "elif idx == 5:" in src
    tui_src = io.open(os.path.join(ROOT, "bin", "ai_tui.py"), encoding="utf-8").read()
    assert "class KeyCaptureScreen" in tui_src, "TUI 缺按键捕获框"
    assert "def capture_key" in tui_src
    ui_src = io.open(os.path.join(ROOT, "bin", "ai_lib", "ui.py"), encoding="utf-8").read()
    assert "def capture_key(" in ui_src, "ui.capture_key 缺失"
    print("PASS /config → ⌨️ 按键设置 入口 + TUI 按键捕获框 + ui.capture_key")


def main():
    test_registry()
    test_persistence()
    test_conflicts_are_group_scoped()
    test_ptk_conversion()
    test_config_menu_entry()
    test_repl_bindings_follow_registry()
    test_tui_bindings_follow_registry()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
