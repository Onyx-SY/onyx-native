# -*- coding: utf-8 -*-
"""step-4 验证：外观回到改动前 + 三个回归已修 + 增强仍在。"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
fails = []

from prompt_toolkit.input import create_pipe_input          # noqa: E402
from prompt_toolkit.output import DummyOutput               # noqa: E402

from lib.terminal.repl import build_session                 # noqa: E402

# 原补全菜单样式（改动前 input_lib.default_comp_style）
LEGACY_STYLE = {
    "completion-menu": "bg:#2d2d30 #cccccc",
    "completion-menu.completion": "bg:#2d2d30 #aaaaaa",
    "completion-menu.completion.current": "bg:#007acc #ffffff",
    "completion-menu.meta": "bg:#3d3d40 #888888",
    "completion-menu.meta.current": "bg:#007acc #cccccc",
    "scrollbar.background": "bg:#1e1e1e",
    "scrollbar.button": "bg:#555555",
    "bottom-toolbar": "bg:#007acc #ffffff",
}
from prompt_toolkit.styles import Style as PS                 # noqa: E402
legacy_style = PS.from_dict(LEGACY_STYLE)

# 原虚影补全器
from lib.terminal.com import SmartCompleter, SmartAutoSuggest  # noqa: E402


async def type_and_check(sess, typed=b"gi", wait=0.7):
    with create_pipe_input() as inp:
        sess.app.input = inp
        sess.app.output = DummyOutput()
        t = asyncio.ensure_future(sess.prompt_async("> "))
        await asyncio.sleep(0.15)
        inp.send_bytes(typed)
        await asyncio.sleep(wait)
        b = sess.default_buffer
        cs = b.complete_state
        n = len(cs.completions) if cs else 0
        sug = b.suggestion
        inp.send_bytes(b"\r")
        try:
            await asyncio.wait_for(t, timeout=2)
        except Exception:
            pass
        return n, (sug.text if sug is not None else None)


async def main():
    legacy_comp = SmartCompleter(valid_commands={"git", "grep", "ls"}, virtual_root="")
    auto = SmartAutoSuggest(legacy_comp)

    s = build_session(commands=["git", "grep", "ls"], style=legacy_style, auto_suggest=auto)

    print("── ① 边打边弹补全列表（你报的回归）──────────────")
    n, ghost = await type_and_check(s)
    print(f"   输入 'gi' → 菜单候选 {n} 个 | complete_while_typing = "
          f"{bool(s.default_buffer.complete_while_typing())}")
    if n == 0:
        fails.append("自动弹出补全列表仍未恢复")

    print("── ② 虚影 / 幽灵补全（你报的回归）──────────────")
    print(f"   session.auto_suggest = {type(s.auto_suggest).__name__} | 幽灵文本 = {ghost!r}")
    if s.auto_suggest is None:
        fails.append("auto_suggest 未接入（虚影丢失）")

    print("── ③ 补全菜单样式 = 改动前那套 ─────────────────")
    attrs = s.style.get_attrs_for_style_str("class:completion-menu")
    legacy_attrs = legacy_style.get_attrs_for_style_str("class:completion-menu")
    ok = (str(attrs.bgcolor) == str(legacy_attrs.bgcolor)
          and str(attrs.color) == str(legacy_attrs.color))
    print(f"   实际 bg/color = {attrs.bgcolor}/{attrs.color} | 期望 = "
          f"{legacy_attrs.bgcolor}/{legacy_attrs.color}")
    if not ok:
        fails.append("补全菜单配色没回到原样")

    print("── ④ 不再有底部工具条 ─────────────────────────")
    print(f"   bottom_toolbar = {s.bottom_toolbar}")
    if s.bottom_toolbar is not None:
        fails.append("底部工具条未移除")

    print("── ⑤ 多行：判定内核已换，交互模型未变 ──────────")
    from lib.terminal.input_lib import _multiline_text_complete
    cases = [
        ('echo "if x; then y; fi"', "bash", True, "引号内关键字（旧：误判为未闭合）"),
        ("echo \"a << b\"", "bash", True, "引号内 <<（旧：误判 heredoc）"),
        ("echo $((1<<3))", "bash", True, "算术展开（旧：误判 heredoc）"),
        ("if true; then echo 1; fi", "bash", True, "单行 if"),
        ("echo hello", "bash", True, "普通命令"),
        ("if true; then", "bash", False, "真的未闭合"),
        ("cat << EOF", "bash", False, "heredoc 未结束"),
    ]
    for text, syn, expect, why in cases:
        got = _multiline_text_complete(text, syn)
        ok = (got == expect)
        print(f"   {'✅' if ok else '❌'} {why:28} {text!r:30} → {got}")
        if not ok:
            fails.append(f"多行判定: {why}")
    print(f"   session.multiline = {s.multiline}（应为 False：逐行流程）")
    if s.multiline:
        fails.append("多行仍处于单缓冲模式")

    print("\nFAILS:", fails if fails else "none")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
