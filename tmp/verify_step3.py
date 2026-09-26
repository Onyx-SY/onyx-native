# -*- coding: utf-8 -*-
"""step-3 验证：键位层（程序化驱动，非 PTY）。

用 prompt_toolkit 的 create_pipe_input() 把按键直接喂给 PromptSession，
断言缓冲区最终内容 —— 等价于"敲这些键会得到什么"。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
fails = []

from lib.terminal.repl import editor as E

ESC = "\x1b"
LEFT = ESC + "[D"


def run(keys: str, history=None):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    flags = {k: v for k, v in E.SESSION_FLAGS.items() if k != "complete_while_typing"}
    with create_pipe_input() as inp:
        sess = PromptSession(input=inp, output=DummyOutput(),
                             key_bindings=E.build_key_bindings(),
                             history=InMemoryHistory(history or []), **flags)
        inp.send_bytes(keys.encode("latin-1"))
        return sess.prompt()


def case(label, keys, expect, history=None):
    try:
        got = run(keys, history=history)
    except Exception as e:
        print(f"❌ {label}: 抛异常 {e!r}")
        fails.append(label)
        return
    ok = (got == expect)
    print(f"{'✅' if ok else '❌'} {label}: {got!r}（期望 {expect!r}）")
    if not ok:
        fails.append(label)


print("── kill-ring ──────────────────────────────────────────")
case("Ctrl+K 剪切 + Ctrl+Y 粘回", "abcd" + LEFT + LEFT + "\x0b" + "\x19" + "\r", "abcd")
case("Ctrl+U 删到行首 + Ctrl+Y 粘回", "hello" + LEFT + LEFT + "\x15" + "\x19" + "\r", "hello")
case("Ctrl+W 删除前一词", "foo bar\x17\r", "foo ")
case("Alt+D 删除后一词", "foo bar" + ESC + "[D" + ESC + "[D" + ESC + "[D" + ESC + "d\r", "foo ")
# kill-ring 是模块级单例，先清空再测轮换（否则会拿到前面用例留下的内容）
E.KILL_RING._ring.clear()
case("Alt+Y 轮换 kill-ring",
     "aa\x01\x0b" + "bb\x01\x0b" + "\x19" + ESC + "y\r", "aa")

print("── 历史 / 编辑 ────────────────────────────────────────")
case("Alt+. 插入上一条命令末参数", ESC + ".\r", "world", history=["echo hello world"])
case("Alt+Enter 插入换行", "a" + ESC + "\r" + "b\r", "a\nb")
case("Ctrl+A/E 行首行尾（ptk 默认）", "abc\x01X\x05Y\r", "XabcY")

print("── Ctrl+L 不调子进程 ──────────────────────────────────")
_orig_system = os.system
_called = {"n": 0}


def _boom(*a, **kw):
    _called["n"] += 1
    raise AssertionError("Ctrl+L 不应该调用 os.system")


os.system = _boom
try:
    got = run("abc\x0c\r")
finally:
    os.system = _orig_system
ok = (got == "abc" and _called["n"] == 0)
print(f"{'✅' if ok else '❌'} Ctrl+L 清屏未调子进程: 结果={got!r} os.system 调用={_called['n']}")
if not ok:
    fails.append("Ctrl+L")

print("── 会话开关 ───────────────────────────────────────────")
for flag in ("enable_history_search", "enable_suspend", "enable_open_in_editor"):
    v = E.SESSION_FLAGS.get(flag)
    print(f"{'✅' if v else '❌'} {flag} = {v}")
    if not v:
        fails.append(flag)

print("── kill-ring 对象 ─────────────────────────────────────")
kr = E.KillRing()
kr.kill("one"); kr.kill("two")
ok = kr.yank() == "two" and kr.rotate() == "one"
print(f"{'✅' if ok else '❌'} KillRing yank/rotate: yank={kr.yank()!r} rotate={kr.rotate()!r}")
if not ok:
    fails.append("KillRing")

print("── 帮助表 ─────────────────────────────────────────────")
tbl = E.binding_table()
n = sum(len(items) for _, items in tbl)
print(f"{'✅' if n >= 25 else '❌'} binding_table: {len(tbl)} 组 / {n} 条")
if n < 25:
    fails.append("binding_table 过少")

print("\nFAILS:", fails if fails else "none")
sys.exit(1 if fails else 0)
