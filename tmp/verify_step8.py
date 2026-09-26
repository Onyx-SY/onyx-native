# -*- coding: utf-8 -*-
"""step-8 验证：新输入层端到端（程序化驱动）+ 会话复用 + 多行 + 回归。"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
fails = []

from prompt_toolkit.input import create_pipe_input                     # noqa: E402
from prompt_toolkit.output import DummyOutput                          # noqa: E402

from lib.terminal.repl import build_session, get_repl_session, session_key  # noqa: E402
from lib.terminal.repl.history import OnyxHistory                      # noqa: E402
from lib.terminal.repl.multiline import is_complete, continuation_prompt    # noqa: E402

ESC = "\x1b"


def drive(keys, commands=None, hist=None):
    """在一个真实 PromptSession 上喂按键，返回提交结果。"""
    sess = build_session(commands=commands or ["git", "ls", "cd"], history=hist)
    with create_pipe_input() as inp:
        sess.app.input = inp
        sess.app.output = DummyOutput()
        inp.send_bytes(keys.encode("latin-1"))
        return sess.prompt("❯ ", prompt_continuation=lambda w, n, s: continuation_prompt(
            is_complete(sess.default_buffer.text)))


print("── ① 单行：完整命令直接提交 ──────────────────────────")
r = drive("echo hi\r")
print(f"   结果 = {r!r}")
if r != "echo hi":
    fails.append(f"单行提交异常: {r!r}")

print("── ② 多行：Enter 自动续行 + 自动缩进 ─────────────────")
r2 = drive("if true; then\r" + "echo hi\r" + "fi\r")
print(f"   结果 = {r2!r}")
if r2 != "if true; then\n    echo hi\n    fi":
    fails.append(f"多行结果异常: {r2!r}")
if "\n" not in r2:
    fails.append("多行未生效")

print("── ③ 补全：Tab 取候选（异步驱动，等补全就绪）──────────")
import asyncio                                                         # noqa: E402


async def drive_async(chunks, commands=None):
    sess = build_session(commands=commands or ["git", "grep", "ls"])
    with create_pipe_input() as inp:
        sess.app.input = inp
        sess.app.output = DummyOutput()
        task = asyncio.ensure_future(sess.prompt_async("❯ "))
        for data, delay in chunks:
            inp.send_bytes(data.encode("latin-1"))
            await asyncio.sleep(delay)
        return await task


r3 = asyncio.run(drive_async([("gi", 0.35), ("\t", 0.35), ("\r", 0.15)]))
print(f"   结果 = {r3!r}")
if not r3.startswith("git"):
    fails.append(f"Tab 补全未生效: {r3!r}")
_sess = build_session(commands=["git", "grep", "ls"])
print(f"   补全器类型 = {type(_sess.completer).__name__}")
if type(_sess.completer).__name__ != "OnyxCompleter":
    fails.append("会话未使用新补全器")

print("── ④ 会话复用：同签名同一对象，签名变化才重建 ─────────")
h = OnyxHistory(os.path.join(tempfile.mkdtemp(prefix="onyx_v2_"), "hist"))
k1 = session_key(commands_hash=hash(frozenset({"ls"})), sys_type="Linux",
                 terminal_type="bash", virtual_root="/r", user_home_dir="/r", lang="chinese")
rs = get_repl_session("unit")
s1 = rs.get(k1, lambda: build_session(commands=["ls"], history=h))
s2 = rs.get(k1, lambda: build_session(commands=["ls"], history=h))
k2 = session_key(commands_hash=hash(frozenset({"ls", "git"})), sys_type="Linux",
                 terminal_type="bash", virtual_root="/r", user_home_dir="/r", lang="chinese")
s3 = rs.get(k2, lambda: build_session(commands=["ls", "git"], history=h))
print(f"   同签名复用 = {s1 is s2} | 变签名重建 = {s3 is not s1} | stats = {rs.stats()}")
if s1 is not s2:
    fails.append("同签名未复用")
if s3 is s1:
    fails.append("签名变化未重建")

print("── ⑤ 工具条 / 帮助文本 ──────────────────────────────")
from lib.terminal.repl import hints, help as helpmod                   # noqa: E402
hints.set_state(mode="SHELL", extra="🔍 git 1/3")
tb = hints.make_toolbar()()
flat = "".join(t for _, t in tb)
print("   工具条 =", flat.strip()[:90])
if "SHELL" not in flat or "Ctrl+R" not in flat:
    fails.append("工具条内容缺失")
htext = helpmod.render_help_text()
print(f"   帮助浮层 {len(htext.splitlines())} 行，含 Ctrl+W: {'Ctrl+W' in htext} / F1: {'F1' in htext}")
if "Ctrl+W" not in htext or "F1" not in htext:
    fails.append("帮助浮层不完整")

print("── ⑥ 真实换行必须穿过 _clean_display_text ────────────")
from lib.terminal.input_lib import _clean_display_text                 # noqa: E402
ml = "if true; then\n    echo hi\nfi"
out = _clean_display_text(ml, decode_escapes=False)
print(f"   {out!r}")
if out != ml:
    fails.append(f"多行被清洗破坏: {out!r}")
lit = 'printf "a\\nb"'
if _clean_display_text(lit, decode_escapes=False) != lit:
    fails.append("字面 \\n 被 decode_escapes=False 破坏")

print("── ⑦ 开关默认值 ─────────────────────────────────────")
import lib.terminal.input_lib as IL                                    # noqa: E402
print(f"   _REPL_V2 = {IL._REPL_V2}（默认应为 True）")
if not IL._REPL_V2:
    fails.append("新输入层未默认启用")

print("\nFAILS:", fails if fails else "none")
sys.exit(1 if fails else 0)
