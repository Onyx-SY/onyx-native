#!/usr/bin/env python3
"""主 REPL 键位可配置回归。

要求（用户）：主 REPL 的绑定要够灵活 —— 原先只有 9 个键走 ptk.json，其余硬编码；
现在全部可配置，并能在 `config-onyx-repl keys` / TUI 里改。

覆盖：
  1. 动作表完整、双语齐备、与 DEFAULT_PTK_CONFIG 一一对应；
  2. 读取/写入/恢复（写回 ptk.json），非法键与未知动作被拒；
  3. `config-onyx-repl keys [set|reset]` 输出正确；
  4. 补全名单（etc/cmd.json / cmd_para.json）与 help 教程已含 keys 子命令。

运行: python3 test/virtual/test_repl_keys.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import bin.repl_config as rc  # noqa: E402
from lib.terminal import com as repl_com  # noqa: E402

# ── 把 ptk.json 重定向到临时目录，避免污染真实配置 ──
_TMP = tempfile.mkdtemp(prefix="onyx_replkeys_")
repl_com.PTK_CONFIG_PATH = os.path.join(_TMP, "ptk.json")
rc.CONFIG_DIR = os.path.join(_TMP, ".config", "onyx")


def test_actions_table():
    acts = rc.repl_key_actions()
    assert acts, "REPL_KEY_ACTIONS 为空"
    ids = [a[0] for a in acts]
    pkeys = [a[1] for a in acts]
    assert len(ids) == len(set(ids)), "动作 id 必须唯一"
    assert len(pkeys) == len(set(pkeys)), "ptk.json 键名必须唯一"
    defaults = repl_com.DEFAULT_PTK_CONFIG["key_bindings"]
    for aid, pkey, cn, en in acts:
        assert cn and en, f"{aid} 缺双语说明"
        assert pkey in defaults, f"{pkey} 不在 DEFAULT_PTK_CONFIG.key_bindings 里"
    for must in ("multiline_editor", "completion_lock", "clear_screen",
                 "history_up", "completion_trigger"):
        assert must in pkeys, f"动作表缺 {must}"
    print(f"PASS 动作表：{len(acts)} 项，与 DEFAULT_PTK_CONFIG 一一对应")


def test_read_defaults():
    cur = rc.read_repl_keys()
    assert cur.get("multiline_editor") == "escape,enter", cur
    assert cur.get("clear_screen") == "c-l", cur
    assert cur.get("history_up") == "up", cur
    print("PASS 读取：未配置时回落默认值")


def test_set_and_roundtrip():
    ok, err = rc.set_repl_key("multiline_editor", "f6")
    assert ok, err
    assert rc.read_repl_keys().get("multiline_editor") == "f6", \
        rc.read_repl_keys().get("multiline_editor")
    # 真的写进了 ptk.json
    raw = json.load(io.open(os.path.join(_TMP, "ptk.json"), encoding="utf-8"))
    assert raw["key_bindings"]["multiline_editor"] == "f6", raw

    # 组合键（ctrl/alt）应转成 ptk 写法
    ok, _ = rc.set_repl_key("completion_trigger", "ctrl+space")
    assert ok
    assert rc.read_repl_keys().get("completion_trigger") == "c-space"

    # 非法 / 未知
    assert rc.set_repl_key("multiline_editor", "not-a-key!!")[0] is False
    assert rc.set_repl_key("nope", "f6")[1] == "unknown_action"
    assert rc.read_repl_keys().get("multiline_editor") == "f6", "非法值不该写盘"

    # 恢复
    assert rc.reset_repl_key("multiline_editor")
    assert rc.read_repl_keys().get("multiline_editor") == "escape,enter"
    rc.set_repl_key("clear_screen", "f7")
    n = rc.reset_all_repl_keys()
    assert n >= 1
    assert rc.read_repl_keys().get("clear_screen") == "c-l"
    print("PASS 写入/往返/非法拒绝/恢复默认")


def test_cli_output():
    from core.cmd_registry import _lazy
    handler = _lazy("config-onyx-repl")

    def _run(args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            handler(["config-onyx-repl"] + args, "req")
        return buf.getvalue()

    out = _run(["keys"])
    for pkey in ("multiline_editor", "completion_lock", "clear_screen"):
        assert pkey in out, f"keys 输出缺 {pkey}：{out}"
    assert "escape,enter" in out, out

    out2 = _run(["keys", "set", "clear_screen", "f8"])
    assert "clear_screen" in out2 and "f8" in out2, out2
    assert rc.read_repl_keys().get("clear_screen") == "f8"

    out3 = _run(["keys", "set", "clear_screen", "!!bad!!"])
    assert "无效按键" in out3 or "invalid" in out3.lower(), out3

    out4 = _run(["keys", "reset", "--all"])
    assert rc.read_repl_keys().get("clear_screen") == "c-l", rc.read_repl_keys()
    assert out4.strip(), "reset --all 应有输出"
    print("PASS config-onyx-repl keys：list / set / 非法拒绝 / reset --all")


def test_completion_and_help():
    cmd_json = json.load(io.open(os.path.join(ROOT, "etc", "cmd.json"), encoding="utf-8"))
    subs = cmd_json["config-onyx-repl"]["subcommands"]
    for k in ("keys", "keys set", "keys reset", "keys list"):
        assert k in subs, f"补全名单缺 {k}"
    para = io.open(os.path.join(ROOT, "etc", "cmd", "cmd_para.json"), encoding="utf-8").read()
    assert "keys set" in para, "参数补全表缺 keys set"
    for pkey in ("multiline_editor", "completion_lock"):
        assert pkey in para, f"参数补全表缺键位动作 {pkey}"

    h = json.load(io.open(os.path.join(ROOT, "bin", "help", "help_info", "commands",
                                       "config-onyx-repl.json"), encoding="utf-8"))
    entry = h["命令"]["config-onyx-repl"]
    for lang in ("Chinese", "English"):
        assert "keys set" in entry[lang], f"{lang} 教程缺 keys 子命令"
    assert "多行编辑" in entry["Chinese"] and "multi-line editor" in entry["English"]
    print("PASS 补全名单 + 参数补全表 + help 教程均已含 keys / 多行编辑区")


def test_tui_has_key_section():
    src = io.open(os.path.join(ROOT, "bin", "repl_config.py"), encoding="utf-8").read()
    assert "class _KeysScreen" in src and "class _Capture" in src, "TUI 缺按键设置界面"
    assert "主 REPL 按键" in src and "Main REPL keys" in src
    print("PASS TUI 配置界面已含「⌨️ 主 REPL 按键」栏")


def main():
    test_actions_table()
    test_read_defaults()
    test_set_and_roundtrip()
    test_cli_output()
    test_completion_and_help()
    test_tui_has_key_section()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
