# -*- coding: utf-8 -*-
"""对 bin/ai_lib/ui.py 做剩余三处替换（幂等 + 断言命中）。"""
import io
import sys

P = "bin/ai_lib/ui.py"
src = io.open(P, encoding="utf-8").read()


def rep(old, new, tag):
    global src
    n = src.count(old)
    if n != 1:
        print(f"❌ {tag}: 命中 {n} 次（应为 1）")
        sys.exit(1)
    src = src.replace(old, new)
    print(f"✅ {tag}")


OLD_PLAIN = '''def tui_plain(renderable, title: str = "", **panel_kw):
    """按渲染模式产出「带面板」或「带标题的纯文本」。

    - REPL：`Panel(renderable, title=title, **panel_kw)`（原样，行为不变）
    - TUI：**不套面板**，但把 `title` 渲染成独立标题行 —— 与 REPL 面板标题的信息对齐。
      否则「🤖 AI / 🧠 分析 / ⚠️ 警告 / ✅ 工具名」等标签全丢，用户分不清每块是什么。

    TUI 不套面板的原因：① 日志区比整屏窄（宽屏还要减侧栏），面板按整屏宽画会落进
    日志区被换行截断、框线错位；② 日志区本身已有边框，再套一层显得杂乱。
    """
    if _tui():
        if not title:
            return renderable
        from rich.console import Group
        style = panel_kw.get("border_style") or "bold"
        return Group(Text("▌ " + str(title), style=style), renderable)
    from rich.panel import Panel as _Panel
    return _Panel(renderable, title=title, **panel_kw)
'''

NEW_PLAIN = '''def tui_block(title: str, body, border_style: str = ""):
    """TUI 内容块：角色标签行 + 缩进 2 格的正文（无框线）。

    label 落在第 0 列（字形 1 宽 + 1 空格），正文缩进 2 → 正文与标签文字左对齐。
    """
    from rich.console import Group
    from rich.padding import Padding
    role, label = onyx_role(title, border_style)
    if not label:
        return Padding(body, (0, 0, 0, 2))
    return Group(onyx_label(role, label), Padding(body, (0, 0, 0, 2)))


def tui_plain(renderable, title: str = "", **panel_kw):
    """按渲染模式产出「带面板」或「带角色标签的纯文本」。

    - REPL：`Panel(renderable, title=title, **panel_kw)`（原样，行为不变）
    - TUI：**不套面板**，把 `title` 渲染成「角色字形 + 语义色」的标签行，正文缩进 2
      —— 既保住「🤖 AI / 🧠 分析 / ⚠️ 警告 / ✅ 工具名」的信息，又统一成一套视觉语言。

    TUI 不套面板的原因：① 日志区比整屏窄（宽屏还要减侧栏），面板按整屏宽画会落进
    日志区被换行截断、框线错位；② 日志区已有一条左侧发丝线，再套框会显得杂乱。
    """
    if _tui():
        if not title:
            return renderable
        return tui_block(title, renderable, panel_kw.get("border_style") or "")
    from rich.panel import Panel as _Panel
    return _Panel(renderable, title=title, **panel_kw)
'''
rep(OLD_PLAIN, NEW_PLAIN, "tui_plain → tui_block")

OLD_AI = '''    """渲染 AI 回答。

    - REPL：带边框 + 标题的 Panel（原样）
    - TUI：**不套边框**，但保留「▌ AI 回复」标题行，并给正文整块染上淡蓝底色
      （即便 Markdown 一句都解析不出，也有一整块底色，一眼就能区分 AI 说了什么）
'''
NEW_AI = '''    """渲染 AI 回答。

    - REPL：带边框 + 标题的 Panel（原样）
    - TUI：**不套边框**，用「◆ 角色标签 + 缩进正文」的视觉语言，正文整块染上极淡的
      蓝紫底色（`onyx-ai-bg`）—— 即便 Markdown 一句都解析不出，也有一整块底色，
      一眼就能区分「哪些是 AI 说的」。
'''
rep(OLD_AI, NEW_AI, "render_ai_panel 文档")

OLD_AI2 = '''    if _tui():
        from rich.panel import Panel as _Panel
        from rich import box as _box
        # box=MINIMAL 的「边框」全是空格 → 无可见框线，只留整块底色（真正的底色块）
        block = _Panel(body, box=_box.MINIMAL, style=_TUI_AI_BG, padding=(0, 1))
        return _Group(Text("▌ " + title, style="bold bright_cyan"), block)
'''
NEW_AI2 = '''    if _tui():
        from rich.panel import Panel as _Panel
        from rich.padding import Padding as _Pad
        from rich import box as _box
        role, label = onyx_role(title, "cyan")
        # box=MINIMAL 的「边框」全是空格 → 无可见框线，只留整块底色（真正的底色块）
        block = _Panel(body, box=_box.MINIMAL,
                       style="on " + ONYX_VARS["onyx-ai-bg"], padding=(0, 1))
        return _Group(onyx_label(role, label or "AI"), _Pad(block, (0, 0, 0, 2)))
'''
rep(OLD_AI2, NEW_AI2, "render_ai_panel TUI 分支")

if '_TUI_AI_BG = ' in src:      # 常量定义（若仍在）
    rep('_TUI_AI_BG = "on #16263c"   # TUI 下 AI 回复的淡蓝底色块\n\n\n', "", "移除 _TUI_AI_BG 常量")

io.open(P, "w", encoding="utf-8").write(src)
print("written", len(src), "chars")
