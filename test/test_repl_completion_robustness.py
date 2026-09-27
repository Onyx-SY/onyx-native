#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主 REPL 健壮性 + 补全增强的离线单测（不依赖真实终端）。

覆盖本轮改动：
  1. input_lib._strip_control_noise —— 终端控制序列泄漏过滤（CPR/鼠标/OSC）
  2. com._prefix_match / _prefix_match_exact / _subsequence_positions —— 匹配工具
  3. SmartCompleter._complete_command —— 前缀优先 + smart-case 排序 + 模糊子序列

运行: python3 test/test_repl_completion_robustness.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from lib.terminal.input_lib import _strip_control_noise  # noqa: E402
from lib.terminal.com import (  # noqa: E402
    SmartCompleter,
    _prefix_match,
    _prefix_match_exact,
    _subsequence_positions,
)

FAILS = []


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(name)


# ── 1. 控制序列泄漏过滤 ──────────────────────────────────────────────
def test_strip_control_noise():
    cases_keep = [
        "",                       # 空串
        "ls -la",
        "echo 1;1R",              # 含普通字符 → 不丢
        "printf hello",
        "if true; then\n  echo hi\nfi",   # 合法多行命令
        "grep -r 'foo bar' .",
    ]
    for c in cases_keep:
        check(f"保留 {c!r}", _strip_control_noise(c) == c, repr(_strip_control_noise(c)))

    cases_drop = [
        "\x1b[1;1R",              # CPR 应答（带 ESC）
        "1;1R",                   # CPR 应答残余
        ";1R",                    # CPR 应答残余（无行号）
        "\x1b[31m",               # 纯 CSI
        "\x1b[?2004l",            # 括号粘贴结束序列
        "\x1b]0;title\x07",       # OSC 标题
        "\x1b",                   # 孤立 ESC
    ]
    for c in cases_drop:
        check(f"丢弃 {c!r}", _strip_control_noise(c) == "", repr(_strip_control_noise(c)))


# ── 2. 匹配工具 ──────────────────────────────────────────────────────
def test_match_helpers():
    check("_prefix_match 忽略大小写", _prefix_match("Py", "python"))
    check("_prefix_match 空查询匹配", _prefix_match("", "anything"))
    check("_prefix_match 不匹配", not _prefix_match("xy", "python"))
    check("_prefix_match_exact 大小写敏感", not _prefix_match_exact("Py", "python"))
    check("_prefix_match_exact 命中", _prefix_match_exact("py", "python"))
    check("_prefix_match_exact 空查询不命中", not _prefix_match_exact("", "python"))

    check("子序列 py→python", _subsequence_positions("py", "python") == [0, 1])
    check("子序列 pt→python", _subsequence_positions("pt", "python") == [0, 2])
    check("子序列 不匹配", _subsequence_positions("xy", "python") is None)
    check("子序列 空查询", _subsequence_positions("", "python") == [])


# ── 3. SmartCompleter._complete_command ──────────────────────────────
def _make_completer(cmds):
    sc = SmartCompleter.__new__(SmartCompleter)
    sc.cmd_list = list(cmds)
    return sc


def test_complete_command():
    sc = _make_completer(["git", "grep", "python", "pip", "ls"])

    def texts(word):
        return [c.text for c in sc._complete_command(word, -len(word))]

    check("前缀命中保持频率序", texts("g") == ["git", "grep"], texts("g"))
    check("大写仍能补全（不破坏既有行为）", texts("G") == ["git", "grep"], texts("G"))
    check("前缀命中", texts("py") == ["python"], texts("py"))
    check("模糊子序列补充 gt→git", texts("gt") == ["git"], texts("gt"))
    check("模糊子序列补充 pp→pip", texts("pp") == ["pip"], texts("pp"))
    check("单字符不触发模糊（避免噪声）", texts("z") == [], texts("z"))
    check("无匹配返回空", texts("qqqq") == [], texts("qqqq"))

    # smart-case 排序：大小写完全一致者优先
    sc2 = _make_completer(["Python", "python"])
    out2 = [c.text for c in sc2._complete_command("py", -2)]
    check("smart-case：完全一致者排前", out2[0] == "python", out2)


def test_onyx_history():
    import lib.terminal.input_lib as il

    old = il._HISTORY_BUFFER
    try:
        il._HISTORY_BUFFER = ["newest", "middle", "oldest"]
        h = il.OnyxHistory()
        check(
            "OnyxHistory 顺序 oldest-first（ptk 要求）",
            list(h.load_history_strings()) == ["oldest", "middle", "newest"],
            list(h.load_history_strings()),
        )
        h.store_string("ignored")
        check("OnyxHistory.store_string 为 no-op（不重复落盘）", True)
        il._HISTORY_BUFFER = []
        check("空历史返回空", list(h.load_history_strings()) == [])
    finally:
        il._HISTORY_BUFFER = old


def test_detect_editor():
    from lib.terminal.input_lib import _detect_editor

    e = _detect_editor()
    check("_detect_editor 返回 str 或 None", e is None or isinstance(e, str), e)


# ── 4. 虚影补全：历史缓冲区引用必须长期有效 ─────────────────────────────
def test_history_buffer_reference_kept():
    """回归：历史满上限后 `add_to_history` 曾重新绑定 _HISTORY_BUFFER，
    导致被缓存 PromptSession 里的 SmartCompleter 停在旧对象上 —— 用户侧表现
    为「虚影只认历史文件里的旧命令，当前会话新命令补不出来」。"""
    import lib.terminal.input_lib as il
    from lib.terminal.com import SmartCompleter

    saved_buf = il._HISTORY_BUFFER
    saved_init = il._HISTORY_INITIALIZED
    saved_max = il._HISTORY_MAX_MEMORY
    saved_save = il._save_history_buffer_async
    saved_freq = il.get_command_freq

    class _FakeFreq:
        def record(self, *a, **k):
            return None

    try:
        il._HISTORY_INITIALIZED = True
        il._HISTORY_BUFFER = [f"oldcmd-{i}" for i in range(il._HISTORY_MAX_MEMORY)]
        # 隔离副作用：不要真的写用户历史文件 / 频率文件
        il._save_history_buffer_async = lambda *a, **k: None
        il.get_command_freq = lambda *a, **k: _FakeFreq()

        sc = SmartCompleter(["ls"], history_buffer=il._HISTORY_BUFFER, user_home_dir="")
        check("completer 与 _HISTORY_BUFFER 共享同一列表", sc.history_buffer is il._HISTORY_BUFFER)

        il.add_to_history("fresh-1")
        il.add_to_history("fresh-2")
        check("历史满上限后仍共享引用（未重新绑定）", sc.history_buffer is il._HISTORY_BUFFER)
        check("满上限后当前会话新命令仍进入 completer", "fresh-2" in sc.history_buffer)
        check("满上限后虚影仍能补出当前会话命令",
              sc.get_smart_suggestion("fresh") == "-2",
              repr(sc.get_smart_suggestion("fresh")))
        check("内存上限仍然生效", len(il._HISTORY_BUFFER) <= il._HISTORY_MAX_MEMORY,
              len(il._HISTORY_BUFFER))

        # 空列表也必须保留引用（旧写法 `history_buffer or []` 会换成新 list）
        empty = []
        sc2 = SmartCompleter(["ls"], history_buffer=empty, user_home_dir="")
        check("传空列表时仍保留引用（不被 `or []` 换掉）", sc2.history_buffer is empty)
    finally:
        il._HISTORY_BUFFER = saved_buf
        il._HISTORY_INITIALIZED = saved_init
        il._HISTORY_MAX_MEMORY = saved_max
        il._save_history_buffer_async = saved_save
        il.get_command_freq = saved_freq


# ── 5. 虚影补全：无历史命中时回退到「当前补全内容的第一项」 ────────────────
def test_ghost_fallback_to_first_completion():
    from prompt_toolkit.document import Document
    from lib.terminal.com import SmartCompleter, SmartAutoSuggest

    class _FakeBuffer:
        complete_state = None

    sc = SmartCompleter(["ls", "lsblk", "lsof", "echo", "git", "grep"],
                        history_buffer=["git status"], user_home_dir="")
    sug = SmartAutoSuggest(sc)

    r = sug.get_suggestion(_FakeBuffer(), Document(text="l", cursor_position=1))
    check("无历史命中 → 回退到补全首项（ls → 's'）",
          getattr(r, "text", None) == "s", repr(getattr(r, "text", None)))

    r = sug.get_suggestion(_FakeBuffer(), Document(text="xyzzy", cursor_position=5))
    check("无补全候选 → 不给虚影", r is None, repr(r))

    r = sug.get_suggestion(_FakeBuffer(), Document(text="git s", cursor_position=5))
    check("有历史命中 → 历史优先于补全菜单",
          getattr(r, "text", None) == "tatus", repr(getattr(r, "text", None)))

    # 菜单已有候选：跟随菜单（complete_index 为 None 时用首项）
    class _CS:
        current_completion = None
        completions = [type("C", (), {"text": "lsblk", "start_position": -1})()]

    class _FB2:
        complete_state = _CS()

    r = sug.get_suggestion(_FB2(), Document(text="l", cursor_position=1))
    check("菜单已就绪时用菜单首项", getattr(r, "text", None) == "sblk",
          repr(getattr(r, "text", None)))

    # 替换模式下补全文本与前缀无关（模糊命中）→ 不给虚影，避免拼出重复文本
    class _CS3:
        current_completion = None
        completions = [type("C", (), {"text": "git", "start_position": -2})()]

    class _FB3:
        complete_state = _CS3()

    r = sug.get_suggestion(_FB3(), Document(text="gt", cursor_position=2))
    check("无法换算差异时不给虚影", r is None, repr(r))


def main():
    test_strip_control_noise()
    test_match_helpers()
    test_complete_command()
    test_onyx_history()
    test_detect_editor()
    test_history_buffer_reference_kept()
    test_ghost_fallback_to_first_completion()
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：{FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
