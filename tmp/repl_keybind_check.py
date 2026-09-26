# -*- coding: utf-8 -*-
"""step-6 验证：REPL 的「→ 接受虚影」「/ 立即弹命令列表」两个键位在合并后确实生效。

做法：把 PromptSession 换成捕获桩，真实跑一遍 ai_interactive_session 的键位构造，
再对合并后的 KeyBindings 取匹配列表的最后一个（key_processor 实际命中的那个）来断言。

用法：python3 tmp/repl_keybind_check.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bin.ai_interactive as m  # noqa: E402
from prompt_toolkit.keys import Keys  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(name)
    return cond


# ── 捕获桩：拿到 session 的 key_bindings 后立刻 EOF 退出 ──
captured = {}


class FakeSession:
    def __init__(self, *a, **kw):
        captured.setdefault("sessions", []).append(kw)
        captured["kb"] = kw.get("key_bindings")
        self._input = None

    def prompt(self, *a, **kw):
        raise EOFError


class FakeSuggestion:
    def __init__(self, text):
        self.text = text


class FakeDoc:
    def __init__(self, at_end=True):
        self.is_cursor_at_the_end = at_end


class FakeBuffer:
    def __init__(self, text="", suggestion=None, at_end=True):
        self.text = text
        self.suggestion = suggestion
        self.cursor_position = len(text)
        self.document = FakeDoc(at_end)
        self.completions_started = 0

    def insert_text(self, t):
        self.text = self.text[:self.cursor_position] + t + self.text[self.cursor_position:]
        self.cursor_position += len(t)

    def start_completion(self, select_first=False):
        self.completions_started += 1


class FakeEvent:
    def __init__(self, buf):
        self.current_buffer = buf


def main():
    m.PromptSession = FakeSession
    m._check_and_setup_key = lambda *a, **k: "dummy-key"
    home = tempfile.mkdtemp(prefix="onyx_repl_test_")
    try:
        m.ai_interactive_session(user_home_dir=home)
    except Exception as e:  # 会话结束路径允许
        print(f"（会话结束：{type(e).__name__}: {e}）")

    kb = captured.get("kb")
    if not check("拿到合并后的 key_bindings", kb is not None):
        return 1
    check("REPL 仍启用历史虚影 auto_suggest",
          any(s.get("auto_suggest") is not None for s in captured.get("sessions", [])))

    # ── 1. → ：最后一个匹配绑定必须是我们的 _accept_ghost ──
    right = kb.get_bindings_for_keys((Keys.Right,))
    names = [b.handler.__name__ for b in right]
    print(f"    right 匹配绑定（共 {len(right)}）：{names}")
    check("→ 生效的是 _accept_ghost（不是 emacs cursor-right）",
          bool(right) and right[-1].handler.__name__ == "_accept_ghost")

    handler = right[-1].handler

    # 1a. 有虚影且光标在末尾 → 直接接受整条虚影
    #     注：prompt_toolkit 的 Suggestion.text 是「剩余部分」（AutoSuggestFromHistory 返回
    #     line[len(text):]），所以这里给剩余片段，接受后应拼成完整行。
    buf = FakeBuffer("检查", FakeSuggestion("一下当前目录"))
    handler(FakeEvent(buf))
    check("→ 有虚影：直接接受整条", buf.text == "检查一下当前目录", buf.text)
    check("→ 有虚影：清掉虚影状态", buf.suggestion is None)

    # 1b. 无虚影 → 右移一格（不破坏原方向键语义）
    buf2 = FakeBuffer("abc")
    buf2.cursor_position = 1
    handler(FakeEvent(buf2))
    check("→ 无虚影：右移一格", buf2.cursor_position == 2, buf2.cursor_position)
    check("→ 无虚影：文本不变", buf2.text == "abc", buf2.text)

    # 1c. 光标不在末尾 → 不吞虚影，只右移
    buf3 = FakeBuffer("abc", FakeSuggestion("abcdef"), at_end=False)
    buf3.cursor_position = 0
    handler(FakeEvent(buf3))
    check("→ 光标不在末尾：只右移、不插入虚影", buf3.text == "abc" and buf3.cursor_position == 1,
          f"{buf3.text!r}@{buf3.cursor_position}")

    # ── 2. `/` ：最后一个匹配绑定必须是我们的 _slash_menu，且真的弹菜单 ──
    slash = kb.get_bindings_for_keys(("/",))
    s_names = [b.handler.__name__ for b in slash]
    print(f"    '/' 匹配绑定（共 {len(slash)}）：{s_names}")
    check("`/` 生效的是 _slash_menu", bool(slash) and slash[-1].handler.__name__ == "_slash_menu")

    buf4 = FakeBuffer("")
    slash[-1].handler(FakeEvent(buf4))
    check("`/`：字符已插入", buf4.text == "/", buf4.text)
    check("`/`：立即启动了补全菜单（不必手动 Tab）", buf4.completions_started == 1,
          buf4.completions_started)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：{FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
