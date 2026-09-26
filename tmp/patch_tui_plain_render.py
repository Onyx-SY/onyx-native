# -*- coding: utf-8 -*-
"""step-1：TUI 模式下不再套 Rich 面板（去掉边框/标题），并修正宽度对齐。

根因：Rich 的 Console.size 读真实终端宽度，而 TUI 的 #log 比整屏窄（还要减侧栏），
面板按整屏宽绘制 → 落进日志区被换行截断 → 框线错位。

做法：ui.py 新增 `tui_plain()`（TUI 返回内容本身 / REPL 返回 Panel），
所有「AI 回答 / 思考 / 工具结果 / 计划 / 分析 / 中断 / 错误 / Debug」面板改用它。

用法：python3 tmp/patch_tui_plain_render.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "bin", "ai_lib", "ui.py")
CMD = os.path.join(ROOT, "bin", "ai_cmd.py")
REPL = os.path.join(ROOT, "bin", "ai_interactive.py")

HELPER = '''def _tui() -> bool:
    """当前是否 TUI 渲染模式（任何异常都当作 REPL）。"""
    try:
        from bin.ai_lib.mode import is_tui_render
        return is_tui_render()
    except Exception:
        return False


def tui_plain(renderable, title: str = "", **panel_kw):
    """按渲染模式产出「带面板」或「纯文本」。

    - REPL：`Panel(renderable, title=title, **panel_kw)`（原样，行为不变）
    - TUI：直接返回 renderable（不加边框/标题）

    TUI 不套面板的原因：TUI 的日志区比整屏窄（宽屏还要减侧栏），而 Rich 的
    Console.size 读的是真实终端宽度 → 面板按整屏宽画，落进日志区就被换行截断，
    框线错位、观感很乱。
    """
    if _tui():
        return renderable
    from rich.panel import Panel as _Panel
    return _Panel(renderable, title=title, **panel_kw)


def render_plan_panel(plan_text: str) -> Panel:'''

UI_PAIRS = [
    ("def render_plan_panel(plan_text: str) -> Panel:", HELPER),
    # 计划面板
    (
        '    return Panel(\n'
        '        md,\n'
        '        title="📋 AI 计划" if _l == "chinese" else "📋 AI Plan",\n'
        '        border_style="cyan",\n'
        '        box=ROUNDED,\n'
        '        padding=(1, 2),\n'
        '    )\n',
        '    return tui_plain(\n'
        '        md,\n'
        '        title="📋 AI 计划" if _l == "chinese" else "📋 AI Plan",\n'
        '        border_style="cyan",\n'
        '        box=ROUNDED,\n'
        '        padding=(1, 2),\n'
        '    )\n',
    ),
    # 分析面板
    (
        '    return Panel(\n'
        '        analysis_text.strip(),\n'
        '        title="🧠 AI 决策分析" if _l == "chinese" else "🧠 AI Decision Analysis",\n'
        '        border_style="blue",\n'
        '        box=ROUNDED,\n'
        '        padding=(1, 2),\n'
        '    )\n',
        '    return tui_plain(\n'
        '        analysis_text.strip(),\n'
        '        title="🧠 AI 决策分析" if _l == "chinese" else "🧠 AI Decision Analysis",\n'
        '        border_style="blue",\n'
        '        box=ROUNDED,\n'
        '        padding=(1, 2),\n'
        '    )\n',
    ),
    # 警告面板
    (
        '    return Panel(\n'
        '        body.strip(),\n'
        '        title=title,\n'
        '        border_style="red",\n'
        '        box=HEAVY,\n'
        '        padding=(1, 2),\n'
        '    )\n',
        '    return tui_plain(\n'
        '        body.strip(),\n'
        '        title=title,\n'
        '        border_style="red",\n'
        '        box=HEAVY,\n'
        '        padding=(1, 2),\n'
        '    )\n',
    ),
    # AI 回答面板
    (
        '    return Panel(\n'
        '        md,\n'
        '        title=title,\n'
        '        border_style="dim",\n'
        '        box=ROUNDED,\n'
        '        padding=(0, 1),\n'
        '    )\n',
        '    return tui_plain(\n'
        '        md,\n'
        '        title=title,\n'
        '        border_style="dim",\n'
        '        box=ROUNDED,\n'
        '        padding=(0, 1),\n'
        '    )\n',
    ),
    # StreamingDisplay：初始 spinner / 流式 / 最终
    (
        '        return Panel(spinner, title="🤖 AI", border_style="green", box=ROUNDED)\n',
        '        return tui_plain(spinner, title="🤖 AI", border_style="green", box=ROUNDED)\n',
    ),
    (
        '            self._live.update(Panel(\n'
        '                self._streamed,\n'
        '                title="🤖 AI",\n'
        '                border_style="green",\n'
        '                box=ROUNDED,\n'
        '            ))\n',
        '            self._live.update(tui_plain(\n'
        '                self._streamed,\n'
        '                title="🤖 AI",\n'
        '                border_style="green",\n'
        '                box=ROUNDED,\n'
        '            ))\n',
    ),
    (
        '                self._live.update(Panel(\n'
        '                    _markdown(final),\n'
        '                    title="🤖 AI",\n'
        '                    border_style="green",\n'
        '                    box=ROUNDED,\n'
        '                ))\n',
        '                self._live.update(tui_plain(\n'
        '                    _markdown(final),\n'
        '                    title="🤖 AI",\n'
        '                    border_style="green",\n'
        '                    box=ROUNDED,\n'
        '                ))\n',
    ),
]

CMD_PAIRS = [
    (
        "    render_tool_table,\n    render_separator,\n)",
        "    render_tool_table,\n    render_separator,\n    tui_plain,\n)",
    ),
    (
        '                parts.append(Panel(Markdown(stream_text.strip()),\n'
        '                                   title="💬 回复", border_style="green", box=ROUNDED))\n',
        '                parts.append(tui_plain(Markdown(stream_text.strip()),\n'
        '                                       title="💬 回复", border_style="green", box=ROUNDED))\n',
    ),
    (
        '                    parts.append(Panel(body, title=header, border_style=style, box=ROUNDED,\n'
        '                                       padding=(0, 1)))\n',
        '                    parts.append(tui_plain(body, title=header, border_style=style, box=ROUNDED,\n'
        '                                           padding=(0, 1)))\n',
    ),
    (
        '                return Panel(Spinner("dots", text=_i18n("thinking", "bilingual"),\n'
        '                                     style="bold cyan"),\n'
        '                            title="🤖 AI", border_style="green", box=ROUNDED)\n',
        '                return tui_plain(Spinner("dots", text=_i18n("thinking", "bilingual"),\n'
        '                                         style="bold cyan"),\n'
        '                                title="🤖 AI", border_style="green", box=ROUNDED)\n',
    ),
    (
        '        initial_panel = Panel(spinner, title="🤖 AI", border_style="green", box=ROUNDED)\n',
        '        initial_panel = tui_plain(spinner, title="🤖 AI", border_style="green", box=ROUNDED)\n',
    ),
    (
        '                        live.update(Panel(\n'
        '                            RichText(_text, style="dim italic"),\n'
        '                            title="🤖 AI 思考中...",\n'
        '                            border_style="bright_black",\n'
        '                            box=ROUNDED,\n'
        '                        ))\n',
        '                        live.update(tui_plain(\n'
        '                            RichText(_text, style="dim italic"),\n'
        '                            title="🤖 AI 思考中...",\n'
        '                            border_style="bright_black",\n'
        '                            box=ROUNDED,\n'
        '                        ))\n',
    ),
    (
        '                    live.update(Panel(_mcp_t("⏹ 已中断", "⏹ Interrupted"), title="🤖 AI", border_style="yellow", box=ROUNDED))\n',
        '                    live.update(tui_plain(_mcp_t("⏹ 已中断", "⏹ Interrupted"), title="🤖 AI", border_style="yellow", box=ROUNDED))\n',
    ),
    (
        '                        live.update(Panel(f"❌ {err_short}", title="🤖 AI", border_style="red", box=ROUNDED))\n',
        '                        live.update(tui_plain(f"❌ {err_short}", title="🤖 AI", border_style="red", box=ROUNDED))\n',
    ),
    (
        '            from rich.panel import Panel as DebugPanel\n'
        '            from rich.box import ROUNDED as DebugBox\n'
        '            console.print(DebugPanel(\n',
        '            from rich.panel import Panel as DebugPanel\n'
        '            from rich.box import ROUNDED as DebugBox\n'
        '            from .ai_lib.ui import tui_plain as _tui_plain\n'
        '            console.print(_tui_plain(DebugPanel(\n',
    ),
]

REPL_PAIRS = [
    (
        '            console.print(Panel(body, title="💰 成本统计", border_style="green"))\n',
        '            from bin.ai_lib.ui import tui_plain as _tui_plain\n'
        '            console.print(_tui_plain(body, title="💰 成本统计", border_style="green"))\n',
    ),
    (
        '            console.print(Panel(body, title="💰 Cost Stats", border_style="green"))\n',
        '            console.print(_tui_plain(body, title="💰 Cost Stats", border_style="green"))\n',
    ),
    (
        '    console.print(Panel("\\n".join(lines), title=title, border_style="cyan"))\n',
        '    from bin.ai_lib.ui import tui_plain as _tui_plain\n'
        '    console.print(_tui_plain("\\n".join(lines), title=title, border_style="cyan"))\n',
    ),
    (
        '                        console.print(Panel(\n'
        '                            _markdown(_plus_think),\n'
        '                            title=("🧠 Plus 执行规划" if ctx.get("lang", "chinese") == "chinese" else "🧠 Plus Plan"),\n'
        '                            border_style="cyan",\n'
        '                        ))\n',
        '                        from bin.ai_lib.ui import tui_plain as _tui_plain\n'
        '                        console.print(_tui_plain(\n'
        '                            _markdown(_plus_think),\n'
        '                            title=("🧠 Plus 执行规划" if ctx.get("lang", "chinese") == "chinese" else "🧠 Plus Plan"),\n'
        '                            border_style="cyan",\n'
        '                        ))\n',
    ),
]


def patch(path, pairs):
    with io.open(path, "r", encoding="utf-8") as f:
        src = f.read()
    for i, (old, new) in enumerate(pairs, 1):
        cnt = src.count(old)
        if cnt != 1:
            print(f"❌ {os.path.basename(path)} 第 {i} 处：匹配 {cnt} 次")
            return False
        src = src.replace(old, new)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, path)
    print(f"✅ {os.path.basename(path)}：{len(pairs)} 处替换完成")
    return True


def main() -> int:
    ok = patch(UI, UI_PAIRS)
    ok = patch(CMD, CMD_PAIRS) and ok
    ok = patch(REPL, REPL_PAIRS) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
