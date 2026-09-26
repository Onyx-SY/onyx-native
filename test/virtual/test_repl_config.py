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
            # 最后一项是「⌨️ 主 REPL 按键」入口
            assert ol.option_count == len(rc.SETTINGS) + 1, ol.option_count
            texts = [str(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]
            joined = "\n".join(texts)
            for sid in SETTING_IDS:
                assert sid in joined, f"TUI 未列出 {sid}"
            # Enter 打开编辑框 → Esc 取消，不写盘
            await pilot.press("enter")
            await pilot.pause(0.3)
            await pilot.press("escape")
            await pilot.pause(0.2)
    asyncio.run(_run())
    print("PASS TUI 配置界面：列出全部配置项，可进入编辑并取消")


def main():
    test_registry()
    test_choices_and_validation()
    test_roundtrip()
    test_builtin_registered_and_list()
    test_completion_and_whitelist()
    test_help_doc()
    test_tui_lists_all_settings()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
