#!/usr/bin/env python3
"""主 REPL 配置命令回归（内置命令 `config-onyx-repl`）。

要求（用户）：主 REPL 的配置要更灵活 —— 无参直接打开 TUI 配置界面，也支持参数式；
同时把这些参数补进补全名单、把命令写进 help 教程。

覆盖：
  1. 配置项注册表：id 唯一、kind 合法、默认值可解析、双语说明齐备；
  2. 取值/校验：bool 变体、语言短码、正整数、非法值被拒；
  3. 读写往返：set → get → reset（全部落在临时目录，不碰真实配置）；
  4. 命令已进内置注册表，且 `list` 能正常输出；
  5. 补全名单（etc/cmd.json）、参数补全表（etc/cmd/cmd_para.json）、
     权限白名单（etc/cmdal.json）都含新命令；
  6. help 教程 JSON 中英齐备；
  7. TUI 配置界面无头渲染能列出全部配置项。

运行: python3 test/virtual/test_repl_config.py
"""
import asyncio
import contextlib
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import bin.repl_config as rc  # noqa: E402

# ── 把配置路径整体重定向到临时目录，避免污染真实配置 ──
_TMP = tempfile.mkdtemp(prefix="onyx_replcfg_")
rc.CONFIG_DIR = os.path.join(_TMP, ".config", "onyx")
rc._FILE_SETTINGS = {k: os.path.join(rc.CONFIG_DIR, os.path.basename(v))
                     for k, v in rc._FILE_SETTINGS.items()}
rc.CONFIG_JSON_PATH = os.path.join(_TMP, "config.json")
rc.SANDBOX_CONFIG_PATH = os.path.join(_TMP, "sandbox")
# ptk.json（主 REPL 配置）也重定向到临时目录，绝不碰真实配置
rc._ptk_path = lambda: os.path.join(_TMP, "ptk.json")
with open(rc.CONFIG_JSON_PATH, "w", encoding="utf-8") as f:
    json.dump({"display_info": {"command_prompts": {"onyx": "x", "kali": "y"}},
               "system_info": {"current_prompt_type": "onyx", "max_history_len": 10000}},
              f, ensure_ascii=False)

SETTING_IDS = [s["id"] for s in rc.SETTINGS]


def test_registry():
    ids = [s["id"] for s in rc.SETTINGS]
    assert len(ids) == len(set(ids)), "配置项 id 必须唯一"
    for s in rc.SETTINGS:
        assert s["kind"] in ("bool", "int", "int_or_false", "choice", "choice_dyn"), s
        assert s["cn"] and s["en"] and s["cn_help"] and s["en_help"], f"{s['id']} 缺双语说明"
        assert isinstance(s["default"], str) and s["default"], f"{s['id']} 缺默认值"
        assert s["default"] == rc.normalize_value(s["id"], s["default"]), \
            f"{s['id']} 默认值不合法：{s['default']}"
    for must in ("language", "debug-times", "clean-log-time", "prompt-style", "history-len"):
        assert must in ids, f"缺配置项 {must}"
    print(f"PASS 注册表：{len(ids)} 项，默认值全部合法、双语齐备")


def test_choices_and_validation():
    assert rc.choices_of("debug-times") == ["true", "false"]
    assert rc.choices_of("language") == ["chinese", "english"]
    assert "onyx" in rc.choices_of("prompt-style"), "prompt-style 应来自 config.json"
    assert rc.choices_of("history-len") == []
    # bool 变体
    for raw in ("1", "true", "ON", "yes", "是", "开"):
        assert rc.normalize_value("debug-times", raw) == "true", raw
    for raw in ("0", "false", "off", "no", "否", "关"):
        assert rc.normalize_value("debug-times", raw) == "false", raw
    assert rc.normalize_value("debug-times", "maybe") is None
    # 语言短码
    assert rc.normalize_value("language", "zh") == "chinese"
    assert rc.normalize_value("language", "EN") == "english"
    assert rc.normalize_value("language", "fr") is None
    # 正整数
    assert rc.normalize_value("history-len", "500") == "500"
    assert rc.normalize_value("history-len", "0") is None
    assert rc.normalize_value("history-len", "abc") is None
    assert rc.normalize_value("clean-log-time", "false") == "false"
    assert rc.normalize_value("clean-log-time", "-1") is None
    print("PASS 取值/校验：bool 变体 / 语言短码 / 正整数 / 非法值被拒")


def test_roundtrip():
    assert rc.get_value("debug-times") == "false"
    ok, err = rc.set_value("debug-times", "true")
    assert ok, err
    assert rc.get_value("debug-times") == "true"
    assert os.path.exists(rc._FILE_SETTINGS["debug-times"]), "应落盘到配置文件"

    ok, err = rc.set_value("history-len", "2000")
    assert ok, err
    assert rc.get_value("history-len") == "2000"
    saved = json.load(open(rc.CONFIG_JSON_PATH, encoding="utf-8"))
    assert saved["system_info"]["max_history_len"] == 2000, saved

    # 非法值不写盘
    assert rc.set_value("history-len", "-5")[0] is False
    assert rc.get_value("history-len") == "2000"
    assert rc.set_value("nope", "1")[1] == "unknown_setting"

    # 恢复默认
    assert rc.reset_value("debug-times")
    assert rc.get_value("debug-times") == "false"
    rc.set_value("spring-mode", "false")
    n = rc.reset_all()
    assert n >= 1
    assert rc.get_value("spring-mode") == "true"
    print("PASS 读写往返：set → get → reset（含非法值不写盘）")


def test_builtin_registered_and_list():
    src = open(os.path.join(ROOT, "core", "cmd_registry.py"), encoding="utf-8").read()
    assert 'registry["config-onyx-repl"]' in src, "未注册进内置命令表"
    assert 'name == "config-onyx-repl"' in src, "未接上 handler"

    from core.cmd_registry import _lazy
    handler = _lazy("config-onyx-repl")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        handler(["config-onyx-repl", "list"], "req")
    out = buf.getvalue()
    for sid in SETTING_IDS:
        assert sid in out, f"list 输出缺 {sid}：{out}"
    print("PASS 内置命令：已注册且 list 输出完整")


def test_completion_and_whitelist():
    cmd_json = json.load(open(os.path.join(ROOT, "etc", "cmd.json"), encoding="utf-8"))
    assert "config-onyx-repl" in cmd_json, "补全名单 etc/cmd.json 缺新命令"
    subs = cmd_json["config-onyx-repl"]["subcommands"]
    for s in ("ui", "list", "get", "set", "reset", "--all"):
        assert s in subs, f"补全 subcommands 缺 {s}"
    for sid in SETTING_IDS:
        assert sid in subs, f"补全 subcommands 缺配置项 {sid}"
    assert "-h" in cmd_json["config-onyx-repl"]["options"]

    para = open(os.path.join(ROOT, "etc", "cmd", "cmd_para.json"), encoding="utf-8").read()
    assert "config-onyx-repl" in para, "参数补全表 cmd_para.json 缺新命令"
    assert "history-len" in para and "prompt-style" in para

    cal = json.load(open(os.path.join(ROOT, "etc", "cmdal.json"), encoding="utf-8"))
    for mode, cfg in (cal.get("perm_limit") or {}).items():
        allowed = cfg.get("allow_commands")
        if allowed == "*":
            continue          # 该模式允许全部命令，无需登记
        assert "config-onyx-repl" in (allowed or []), \
            f"{mode} 模式白名单缺新命令（否则会被权限拦）"
    print("PASS 补全名单 + 参数补全表 + 权限白名单均已更新")


def test_help_doc():
    p = os.path.join(ROOT, "bin", "help", "help_info", "commands", "config-onyx-repl.json")
    assert os.path.exists(p), "缺 help 教程 JSON"
    d = json.load(open(p, encoding="utf-8"))
    entry = d["命令"]["config-onyx-repl"]
    for lang in ("Chinese", "English"):
        assert entry.get(lang), f"help 缺 {lang}"
    assert "用法" in entry["Chinese"] and "Usage" in entry["English"]
    for kw in ("config-onyx-repl list", "config-onyx-repl set", "reset --all"):
        assert kw in entry["Chinese"], f"中文教程缺示例：{kw}"
        assert kw in entry["English"], f"英文教程缺示例：{kw}"
    print("PASS help 教程：中英双语 + 用法/子命令/示例齐备")


def test_tui_lists_all_settings():
    cls = rc.build_tui_app("chinese")
    assert cls is not None, "Textual 不可用"
    app = cls()

    async def _run():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            from textual.widgets import OptionList
            ol = app.query_one("#cfg-items", OptionList)
            # 行 = 通用设置 + 主 REPL 分区入口（4）+ 「⌨️ 主 REPL 按键」入口
            assert ol.option_count == len(rc.SETTINGS) + len(rc.PTK_SECTIONS) + 1, ol.option_count
            texts = [str(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]
            joined = "\n".join(texts)
            for sid in SETTING_IDS:
                assert sid in joined, f"TUI 未列出 {sid}"
            for _sec, cn, _en in rc.PTK_SECTIONS:
                assert cn in joined, f"TUI 未列出主 REPL 分区 {cn}"
            # Enter 打开编辑框 → Esc 取消，不写盘
            # 注意：必须断言「真的退出了弹窗」—— 旧 bug 是 _Edit 只声明了
            # Binding("escape", "cancel") 却没有 action_cancel，Esc 完全无响应，
            # 用户会卡在编辑框里（这里以前只按不查，所以漏掉了）。
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert type(app.screen).__name__ == "_Edit", "Enter 应打开编辑弹窗"
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert type(app.screen).__name__ != "_Edit", \
                "Esc 应关闭编辑弹窗（_Edit 需实现 action_cancel）"
    asyncio.run(_run())
    print("PASS TUI 配置界面：列出全部配置项，可进入编辑并取消")


def test_ptk_settings():
    """主 REPL（ptk.json）配置：默认值与 lib 对齐 + 读写往返 + 非法值被拒。"""
    # 默认值快照必须与 lib.terminal.com:DEFAULT_PTK_CONFIG 一致（防止两边漂移）
    try:
        from lib.terminal.com import DEFAULT_PTK_CONFIG as _lib
        for section, items in rc._PTK_FALLBACK.items():
            for k, v in items.items():
                assert _lib.get(section, {}).get(k) == v, \
                    f"ptk 默认值漂移：{section}.{k} 本地={v} lib={_lib.get(section, {}).get(k)}"
    except ImportError:
        pass

    ids = [s["id"] for s in rc.PTK_SETTINGS]
    assert len(ids) == len(set(ids)), "ptk 配置项 id 必须唯一"
    assert "colors.completion-menu" in ids and "auto_suggest.enabled" in ids

    # 读写往返
    ok, err = rc.ptk_set("colors.completion-menu", "bg:#101010 #eeeeee")
    assert ok, err
    assert rc.ptk_get("colors.completion-menu") == "bg:#101010 #eeeeee"
    assert rc.get_value("colors.completion-menu") == "bg:#101010 #eeeeee"   # 通用接口也认
    saved = json.load(open(rc._ptk_path(), encoding="utf-8"))
    assert saved["colors"]["completion-menu"] == "bg:#101010 #eeeeee", saved

    # bool / int / choice
    assert rc.ptk_set("auto_suggest.enabled", "no")[0]
    assert rc.ptk_get("auto_suggest.enabled") == "false"
    assert rc.ptk_set("completion.max_completions", "250")[0]
    assert rc.ptk_get("completion.max_completions") == "250"
    assert rc.ptk_set("completion.max_completions", "abc")[0] is False
    assert rc.ptk_set("auto_suggest.strategy", "nope")[0] is False

    # 名字解析 + 恢复默认
    assert rc._ptk_resolve("completion-menu", "colors") == "colors.completion-menu"
    assert rc._ptk_resolve("completion.show_hidden") == "completion.show_hidden"
    assert rc._ptk_resolve("nope") is None
    assert rc.ptk_reset("colors.completion-menu")
    assert rc.ptk_get("colors.completion-menu") == rc._PTK_FALLBACK["colors"]["completion-menu"]
    assert rc.ptk_reset_all("colors") == len(rc._PTK_COLOR_META)
    print(f"PASS 主 REPL 配置：{len(ids)} 项，默认值与 lib 对齐、读写/校验/恢复 OK")


def test_home_follows_env():
    """回归：配置目录必须跟随运行时 $HOME（否则「写一个路径、读另一个路径」）。"""
    import importlib
    old = os.environ.get("HOME")
    fake = os.path.join(_TMP, "fakehome")
    try:
        os.environ["HOME"] = fake
        m = importlib.reload(rc)
        assert m.USER_HOME_DIR == fake, m.USER_HOME_DIR
        assert m.CONFIG_DIR == os.path.join(fake, ".config", "onyx"), m.CONFIG_DIR
        assert m._FILE_SETTINGS["language"] == os.path.join(fake, ".config", "onyx", "language")
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old
        importlib.reload(rc)
        # reload 会重置路径 → 重新指回临时目录
        rc.CONFIG_DIR = os.path.join(_TMP, ".config", "onyx")
        rc._FILE_SETTINGS = {k: os.path.join(rc.CONFIG_DIR, os.path.basename(v))
                             for k, v in rc._FILE_SETTINGS.items()}
        rc.CONFIG_JSON_PATH = os.path.join(_TMP, "config.json")
        rc.SANDBOX_CONFIG_PATH = os.path.join(_TMP, "sandbox")
        rc._ptk_path = lambda: os.path.join(_TMP, "ptk.json")
    print("PASS home 路径：USER_HOME_DIR/CONFIG_DIR 跟随 $HOME")


def test_modal_does_not_bubble():
    """回归：模态框里选中值后，栈只能弹一层（OptionSelected 不得冒泡到 App 再开一个框）。"""
    cls = rc.build_tui_app("chinese")
    assert cls is not None, "Textual 不可用"
    app = cls()

    async def _run():
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(0.3)
            from textual.widgets import OptionList
            ol = app.query_one("#cfg-items", OptionList)
            # 进「🎨 颜色」分区（通用设置之后第一个分区）
            ol.highlighted = len(rc.SETTINGS)
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert type(app.screen).__name__ == "_PtkScreen", type(app.screen).__name__
            # 打开第一项的颜色编辑框 → 输入新样式 → Enter
            sec = app.screen
            sec.query_one("#cfg-items", OptionList).highlighted = 0
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert type(app.screen).__name__ == "_Edit", type(app.screen).__name__
            app.screen.query_one("#val").value = "bg:#222222 #dddddd"
            await pilot.press("enter")
            await pilot.pause(0.3)
            stack = [type(s).__name__ for s in app.screen_stack]
            assert stack == ["Screen", "_PtkScreen"], f"弹窗栈异常（冒泡 bug）：{stack}"
            assert rc.ptk_get("colors.completion-menu") == "bg:#222222 #dddddd"
    asyncio.run(_run())
    print("PASS 弹窗不冒泡：选完值只关一层并落盘")


def test_usage_mentions_repl():
    assert "repl list" in rc._USAGE_CN and "colors set" in rc._USAGE_CN
    assert "repl list" in rc._USAGE_EN and "colors set" in rc._USAGE_EN
    print("PASS 用法文案：已包含 repl / colors 子命令")


def main():
    test_registry()
    test_choices_and_validation()
    test_roundtrip()
    test_builtin_registered_and_list()
    test_completion_and_whitelist()
    test_help_doc()
    test_tui_lists_all_settings()
    test_ptk_settings()
    test_home_follows_env()
    test_modal_does_not_bubble()
    test_usage_mentions_repl()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
