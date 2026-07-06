# bin/ai_lib/tui.py
# TUI 界面模块 — 基于 prompt_toolkit
# 用户通过 ai -tui 进入，支持 / 命令、ESC 退出、双语界面

import os
import sys
from typing import Dict, Callable, Optional, List
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory

# ============================================================
# 双语字典
# ============================================================
LANG_TEXTS = {
    "chinese": {
        "tui_welcome": "🎯 欢迎进入 AI TUI 模式 (输入 /help 查看帮助, ESC 退出)",
        "tui_prompt": "🤖 你: ",
        "tui_exit": "👋 已退出 TUI 模式",
        "tui_esc_ask": "有什么问题吗？(按 ESC 退出, 或继续输入)",
        "tui_cmd_not_found": "未知命令: {}  输入 /help 查看可用命令",
        "tui_help_title": "📋 TUI 命令帮助",
        "tui_help_entry": "  {} — {}",
        "tui_ai_thinking": "🤔 AI 思考中...",
        "tui_ai_done": "✅ AI 回答完成",
        "tui_mode_normal": "模式: 普通",
        "tui_mode_plan": "模式: 计划",
        "tui_status": "{} | {} | 输入 /help 帮助",
    },
    "english": {
        "tui_welcome": "🎯 Welcome to AI TUI mode (type /help for help, ESC to exit)",
        "tui_prompt": "🤖 You: ",
        "tui_exit": "👋 Exited TUI mode",
        "tui_esc_ask": "Any questions? (Press ESC to exit, or continue typing)",
        "tui_cmd_not_found": "Unknown command: {}  Type /help for available commands",
        "tui_help_title": "📋 TUI Command Help",
        "tui_help_entry": "  {} — {}",
        "tui_ai_thinking": "🤔 AI is thinking...",
        "tui_ai_done": "✅ AI response complete",
        "tui_mode_normal": "Mode: Normal",
        "tui_mode_plan": "Mode: Plan",
        "tui_status": "{} | {} | Type /help for help",
    },
}


def get_lang_text(lang: str = "chinese") -> Dict[str, str]:
    return LANG_TEXTS.get(lang, LANG_TEXTS["chinese"])


# ============================================================
# / 命令注册表
# ============================================================
_slash_commands: Dict[str, Dict] = {}


def register_slash_command(name: str, desc: str, handler: Callable,
                           desc_cn: str = ""):
    """注册一个 / 命令"""
    _slash_commands[name] = {
        "desc": desc,
        "desc_cn": desc_cn or desc,
        "handler": handler,
    }


def _load_builtin_slash_commands(lang: str = "chinese"):
    """加载内置 / 命令（bin/ai_bin/ 下的模块）"""
    lt = get_lang_text(lang)

    # ── /help ──
    def cmd_help(ctx):
        lines = [lt["tui_help_title"], ""]
        for name, cmd in sorted(_slash_commands.items()):
            d = cmd["desc_cn"] if lang == "chinese" else cmd["desc"]
            lines.append(lt["tui_help_entry"].format(f"/{name}", d))
        return "\n".join(lines)

    register_slash_command("help", "Show this help", cmd_help, "显示帮助信息")

    # ── /exit ──
    def cmd_exit(ctx):
        ctx["should_exit"] = True
        return lt["tui_exit"]

    register_slash_command("exit", "Exit TUI mode", cmd_exit, "退出 TUI 模式")

    # ── /plan ──
    def cmd_plan(ctx):
        current_mode = ctx.get("mode", "normal")
        if current_mode == "plan":
            ctx["mode"] = "normal"
            if lang == "chinese":
                return "已切换为普通模式"
            return "Switched to normal mode"
        else:
            ctx["mode"] = "plan"
            if lang == "chinese":
                return "已切换为计划模式 (AI 将只生成计划，不执行操作)"
            return "Switched to plan mode (AI will only generate plans)"

    register_slash_command("plan", "Toggle plan mode", cmd_plan,
                           "切换计划/普通模式")

    # ── /clear ──
    def cmd_clear(ctx):
        ctx["messages"] = []
        if lang == "chinese":
            return "对话历史已清空"
        return "Conversation history cleared"

    register_slash_command("clear", "Clear conversation", cmd_clear,
                           "清空对话历史")

    # ── /mcp  (替代原 /tools，使用 MCP 协议) ──
    def cmd_mcp(ctx):
        try:
            # 从已加载的 ai_cmd 模块获取 MCP 工具
            import sys as _sys
            ai_mod = _sys.modules.get("bin.ai_cmd")
            if ai_mod and hasattr(ai_mod, "get_mcp_tools"):
                tools = ai_mod.get_mcp_tools()
                if tools:
                    names = [t.get("name", "?") for t in tools]
                    return "MCP 工具: " + ", ".join(names)
            return "无法获取 MCP 工具列表" if lang == "chinese" else "Cannot get MCP tools"
        except Exception:
            return "MCP 未连接" if lang == "chinese" else "MCP not connected"

    register_slash_command("mcp", "List MCP tools", cmd_mcp,
                           "列出 MCP 工具")

    # ── /tools (兼容旧名，转发到 /mcp) ──
    register_slash_command("tools", "List AI tools (MCP)", cmd_mcp,
                           "列出可用 AI 工具（MCP）")


def handle_slash_command(text: str, ctx: dict, lang: str = "chinese") -> Optional[str]:
    """
    处理 / 命令。如果 text 以 / 开头，执行对应命令。
    返回命令输出字符串，非命令返回 None。
    """
    lt = get_lang_text(lang)
    if not text.startswith("/"):
        return None

    parts = text[1:].strip().split(None, 1)
    cmd_name = parts[0].lower() if parts else ""

    if cmd_name in _slash_commands:
        try:
            result = _slash_commands[cmd_name]["handler"](ctx)
            return str(result) if result is not None else ""
        except Exception as e:
            return f"Error: {str(e)}"
    else:
        return lt["tui_cmd_not_found"].format(cmd_name)


# ============================================================
# TUI 主循环
# ============================================================

def run_tui(
    question_callback: Callable[[str, dict], str],
    lang: str = "chinese",
    initial_question: str = "",
    initial_mode: str = "normal",
) -> None:
    """
    启动 TUI 主循环。

    参数:
      question_callback: 当用户输入问题（非 / 命令）时调用，
                         接收 (question_text, ctx_dict)，返回 AI 回答字符串
      lang: 语言
      initial_question: 初始问题（从命令行传入的问题）
      initial_mode: 初始模式 "normal" / "plan"
    """
    lt = get_lang_text(lang)

    # 初始化
    _load_builtin_slash_commands(lang)

    ctx = {
        "should_exit": False,
        "mode": initial_mode,
        "messages": [],
        "lang": lang,
    }

    # 样式
    style = Style.from_dict({
        "prompt": "bold green",
        "ai": "bold cyan",
        "status": "dim",
        "error": "bold red",
        "dim": "dim",
    })

    # 快捷键
    kb = KeyBindings()

    @kb.add("escape")
    def _(event):
        # ESC: 询问用户是否退出
        event.app.exit(result="__ESC__")

    # 底部状态栏
    def bottom_toolbar():
        mode_text = lt["tui_mode_plan"] if ctx["mode"] == "plan" else lt["tui_mode_normal"]
        return lt["tui_status"].format(mode_text, f"消息数: {len(ctx['messages'])}")

    # Session
    session = PromptSession(
        key_bindings=kb,
        style=style,
        bottom_toolbar=bottom_toolbar,
        history=InMemoryHistory(),
    )

    # 欢迎信息
    from rich.console import Console as RichConsole
    console = RichConsole()
    console.print(lt["tui_welcome"], style="bold green")
    console.print()

    # 如果有初始问题，直接发送
    if initial_question.strip():
        console.print(f"{lt['tui_prompt']}{initial_question}", style="bold")
        console.print(lt["tui_ai_thinking"], style="dim")
        try:
            answer = question_callback(initial_question, ctx)
            console.print(answer, style="white")
            ctx["messages"].append({"role": "user", "text": initial_question})
            ctx["messages"].append({"role": "ai", "text": answer})
        except Exception as e:
            console.print(f"Error: {str(e)}", style="bold red")
        console.print()

    # 主循环
    while not ctx["should_exit"]:
        try:
            user_input = session.prompt(lt["tui_prompt"])

            # 检查是否 ESC 退出
            if user_input == "__ESC__":
                console.print()
                console.print(lt["tui_esc_ask"], style="dim")
                try:
                    follow_up = session.prompt("> ")
                    if follow_up == "__ESC__":
                        ctx["should_exit"] = True
                        continue
                    elif follow_up.strip():
                        user_input = follow_up
                    else:
                        continue
                except (KeyboardInterrupt, EOFError):
                    console.print()
                    ctx["should_exit"] = True
                    continue

            if not user_input.strip():
                continue

            # 检查 / 命令
            cmd_result = handle_slash_command(user_input, ctx, lang)
            if cmd_result is not None:
                console.print(cmd_result, style="dim")
                console.print()
                continue

            # 普通问题：发给 AI
            console.print(lt["tui_ai_thinking"], style="dim")
            try:
                answer = question_callback(user_input, ctx)
                console.print(answer, style="white")
                ctx["messages"].append({"role": "user", "text": user_input})
                ctx["messages"].append({"role": "ai", "text": answer})
            except Exception as e:
                console.print(f"Error: {str(e)}", style="bold red")

            console.print()

        except KeyboardInterrupt:
            console.print("\n^C", style="dim")
            continue
        except EOFError:
            console.print()
            ctx["should_exit"] = True

    console.print(lt["tui_exit"], style="dim")
