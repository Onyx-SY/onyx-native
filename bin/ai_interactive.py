#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 独立交互会话 — 持久对话 REPL
仿 lib/terminal/exe.py 的轻量模式，但 AI 命令体系与 shell 完全分开。

用法：由 ai_cmd.handle_ai 入口调用，或 Onyx.py 直接调用 ai_interactive_session()。
"""

import os
import sys
import time
import uuid
from typing import Dict, Any, Optional, Callable, List

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PromptStyle

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

console = Console()


# ─────────────────────────────── / 指令表 ───────────────────────────────

_SLASH_COMMANDS_CN: Dict[str, str] = {
    "/help":   "显示此帮助",
    "/exit":   "退出 AI 对话，返回 shell",
    "/quit":   "同 /exit",
    "/clear":  "清屏",
    "/key":    "查看/更换 API 密钥",
    "/chat":   "管理聊天记忆 (list / switch <name> / new <name>)",
    "/mcp":    "MCP 服务器管理 (list / install <name> / remove <name>)",
}

_SLASH_COMMANDS_EN: Dict[str, str] = {
    "/help":   "Show this help",
    "/exit":   "Exit AI mode, return to shell",
    "/quit":   "Same as /exit",
    "/clear":  "Clear screen",
    "/key":    "View/change API key",
    "/chat":   "Manage chat memory (list / switch <name> / new <name>)",
    "/mcp":    "MCP server management (list / install <name> / remove <name>)",
}

_TEXTS = {
    "chinese": {
        "title": "AI 对话模式",
        "welcome": "直接输入问题开始对话。\n输入 [bold]/help[/] 查看所有指令，[bold]/exit[/] 返回 shell。",
        "no_key": "[bold yellow]未检测到 API 密钥[/]\n\n请输入 32 位密钥以使用 AI 功能。\n输入 `/exit` 或留空回车可返回 shell。",
        "enter_key": "🔑 请输入密钥: ",
        "key_bad_len": "[bold red]密钥格式错误（需 32 位）[/]",
        "key_saved": "[green]✅ 密钥已保存[/]",
        "key_save_fail": "[red]保存失败: {}[/]",
        "key_bad_format": "[yellow]密钥格式异常，请重新设置[/]",
        "bye": "👋 退出 AI 模式",
        "unknown_cmd": "未知指令: {}。输入 /help 查看可用指令。",
        "ai_error": "AI 请求失败: {}",
        "ai_exception": "AI 会话异常: {}",
        "ai_exited": "已退出 AI 模式",
        "help_title": "## AI 交互模式",
        "help_intro": "直接输入问题即可与 AI 对话。AI 会记住本次会话的上下文。",
        "help_tips": "- 按 `Esc` 两次可中断当前 AI 请求\n- 按 `Ctrl+C` 可中断等待中的命令\n- 输入 `/exit` 返回正常 shell",
        "current_key": "当前密钥: [dim]{}[/]",
        "change_key": "更换密钥？(y/n): ",
        "key_read_fail": "[red]读取密钥失败[/]",
        "mcp_usage": "用法: /mcp list | install <name> | remove <name>",
        "chat_usage": "用法: /chat list | switch <name> | new <name>",
        "press_esc": "[dim]按 /exit 退出 AI 模式[/]",
        "interrupted": "[dim]已中断[/]",
        "esc_marked": "已标记，AI 本轮完成后会询问你",
        "ask_after_esc": "有什么要补充或修改的吗？直接输入或按 Enter 跳过",
    },
    "english": {
        "title": "AI Chat Mode",
        "welcome": "Type your question to start chatting.\nType [bold]/help[/] for commands, [bold]/exit[/] to return to shell.",
        "no_key": "[bold yellow]No API key detected[/]\n\nEnter your 32-char key to use AI features.\nType `/exit` or press Enter to return to shell.",
        "enter_key": "🔑 Enter key: ",
        "key_bad_len": "[bold red]Invalid key format (32 chars required)[/]",
        "key_saved": "[green]✅ Key saved[/]",
        "key_save_fail": "[red]Save failed: {}[/]",
        "key_bad_format": "[yellow]Key format invalid, please re-enter[/]",
        "bye": "👋 Exiting AI mode",
        "unknown_cmd": "Unknown command: {}. Type /help for available commands.",
        "ai_error": "AI request failed: {}",
        "ai_exception": "AI session error: {}",
        "ai_exited": "Exited AI mode",
        "help_title": "## AI Interactive Mode",
        "help_intro": "Type your question directly to chat with AI. Context is maintained within the session.",
        "help_tips": "- Press `Esc` twice to interrupt AI request\n- Press `Ctrl+C` to interrupt running commands\n- Type `/exit` to return to shell",
        "current_key": "Current key: [dim]{}[/]",
        "change_key": "Change key? (y/n): ",
        "key_read_fail": "[red]Failed to read key[/]",
        "mcp_usage": "Usage: /mcp list | install <name> | remove <name>",
        "chat_usage": "Usage: /chat list | switch <name> | new <name>",
        "press_esc": "[dim]Type /exit to leave AI mode[/]",
        "interrupted": "[dim]Interrupted[/]",
    },
}

_HELP_TEXT_CN = """\
## AI 交互模式

直接输入问题即可与 AI 对话。AI 会记住本次会话的上下文。

### / 指令
{commands}

### 提示
- 按 `Esc` 两次可中断当前 AI 请求
- 按 `Ctrl+C` 可中断等待中的命令
- 输入 `/exit` 返回正常 shell
"""

_HELP_TEXT_EN = """\
## AI Interactive Mode

Type your question directly to chat with AI. Context is maintained within the session.

### / Commands
{commands}

### Tips
- Press `Esc` twice to interrupt AI request
- Press `Ctrl+C` to interrupt running commands
- Type `/exit` to return to shell
"""


def _t(key: str, lang: str = "chinese", **fmt) -> str:
    """获取双语文本，fmt 中的值会用于 .format()"""
    texts = _TEXTS.get(lang, _TEXTS["chinese"])
    s = texts.get(key, key)
    if fmt:
        try:
            s = s.format(**fmt)
        except (KeyError, IndexError):
            pass
    return s


def _build_help(lang: str = "chinese") -> str:
    lines = []
    cmds = _SLASH_COMMANDS_CN if lang == "chinese" else _SLASH_COMMANDS_EN
    for cmd, desc in cmds.items():
        lines.append(f"- `{cmd}` — {desc}")
    template = _HELP_TEXT_CN if lang == "chinese" else _HELP_TEXT_EN
    return template.format(commands="\n".join(lines))


# ─────────────────────────────── Key 检查 ───────────────────────────────

def _check_and_setup_key(user_home_dir: str, lang: str = "chinese") -> Optional[str]:
    """检查 key.conf 是否存在，Onyx.py 入口已处理引导配置"""
    from bin.ai_cmd import load_key_conf
    conf = load_key_conf()
    if conf and conf.get("api_key"):
        return conf["api_key"]
    return None


# ─────────────────────────────── 提示符 ───────────────────────────────

_AI_PROMPT_STYLE = PromptStyle.from_dict({
    "prompt": "bold cyan",
    "separator": "dim",
})


def _make_ai_prompt() -> str:
    """生成 AI 模式提示符"""
    return "🤖 > "


# ─────────────────────────────── Slash 指令分发 ───────────────────────────────

def _dispatch_slash(cmd_line: str, ctx: Dict[str, Any]) -> bool:
    """
    处理 / 指令。返回 True 表示继续对话，False 表示退出。
    """
    parts = cmd_line.strip().split()
    if not parts:
        return True

    cmd = parts[0].lower()
    args = parts[1:]
    lang = ctx.get("lang", "chinese")

    if cmd in ("/exit", "/quit"):
        console.print(f"[dim]{_t('bye', lang)}[/]")
        return False

    elif cmd == "/help":
        console.print(Markdown(_build_help(lang)))
        return True

    elif cmd == "/clear":
        console.clear()
        return True

    elif cmd == "/key":
        from bin.ai_cmd import load_key_conf, save_key_conf, _SUPPORTED_PLATFORMS, _setup_key_conf_interactive
        conf = load_key_conf()
        if conf:
            plat = conf.get("platform", "?")
            key = conf.get("api_key", "")
            masked = key[:4] + "*" * 24 + key[-4:] if len(key) > 28 else "***"
            console.print(f"  平台: {plat}  Key: {masked}", style="dim")
        choice = input(_t("change_key", lang)).strip().lower()
        if choice == "y":
            _setup_key_conf_interactive(lang)
        return True

    elif cmd == "/chat":
        from bin.ai_cmd import list_chat_memories, switch_chat_memory, create_chat_memory
        sub = args[0] if args else "list"
        home = ctx["user_home_dir"]
        if sub == "list":
            memories = list_chat_memories()
            console.print(memories)
        elif sub == "switch" and len(args) > 1:
            result = switch_chat_memory(home, args[1])
            console.print(result)
            ctx["_chat_changed"] = True
        elif sub == "new" and len(args) > 1:
            result = create_chat_memory(home, args[1])
            console.print(result)
            ctx["_chat_changed"] = True
        else:
            console.print(_t("chat_usage", lang))
        return True

    elif cmd == "/mcp":
        from bin.ai_cmd import handle_mcp_command
        sub = args[0] if args else "list"
        handle_mcp_command(sub, args[1:])
        return True

    else:
        console.print(f"[yellow]{_t('unknown_cmd', lang).format(cmd)}[/]")
        return True


# ─────────────────────────────── 主入口 ───────────────────────────────

def ai_interactive_session(
    user_home_dir: str,
    onyx_module=None,
    global_config: Dict[str, Any] = None,
    user_info: Dict[str, Any] = None,
    user_mode=None,
    parse_and_execute: Callable = None,
    **kwargs
) -> None:
    """
    AI 持久对话 REPL。

    由 Onyx.py 的 handle_ai builtin 调用。首次进入检查 key，
    然后进入持续的 AI 对话循环，直到用户输入 /exit。
    """
    # ── 语言 ──
    current_lang = "chinese"
    if global_config:
        current_lang = global_config.get("display_info", {}).get("language", {}).get("current", "chinese")

    # ── Key 检查 ──
    key = _check_and_setup_key(user_home_dir, current_lang)
    if key is None:
        return

    # ── 上下文 ──
    ctx = {
        "user_home_dir": user_home_dir,
        "lang": current_lang,
        "session_id": str(uuid.uuid4()),
        "_key_changed": False,
        "_chat_changed": False,
    }

    console.print(Panel(
        Markdown(_t("welcome", current_lang)),
        title=f"🤖 Onyx AI — {_t('title', current_lang)}"
    ))

    # ── / 指令补全 ──
    slash_cmds = list((_SLASH_COMMANDS_CN if current_lang == "chinese" else _SLASH_COMMANDS_EN).keys())
    completer = WordCompleter(slash_cmds, ignore_case=True, sentence=True)

    # ── 对话循环 ──
    # ESC 不杀 AI，只设标记；AI 本轮完成后询问用户是否有补充
    esc_flag = [False]

    try:
        session = PromptSession(
            _make_ai_prompt,
            style=_AI_PROMPT_STYLE,
            completer=completer,
        )

        while True:
            # 上轮 AI 被 ESC 标记 → 先问用户
            if esc_flag[0]:
                esc_flag[0] = False
                console.print(f"[dim]💬 {_t('ask_after_esc', current_lang)}[/]")

            try:
                user_input = session.prompt().strip()
            except KeyboardInterrupt:
                # prompt 阶段的 ESC → 设标记，等 AI 结束后问
                esc_flag[0] = True
                console.print(f"\n[dim]📌 {_t('esc_marked', current_lang)}[/]")
                continue
            except EOFError:
                console.print(f"\n[dim]{_t('bye', current_lang)}[/]")
                break

            if not user_input:
                continue

            # / 指令
            if user_input.startswith("/"):
                if not _dispatch_slash(user_input, ctx):
                    break
                continue

            # ── 发给 AI（ESC 只标记不中断——signal 临时吞掉 SIGINT） ──
            import signal as _signal
            def _on_sigint(signum, frame):
                esc_flag[0] = True
            _old_sigint = _signal.signal(_signal.SIGINT, _on_sigint)
            try:
                _call_ai_engine(user_input, user_home_dir, onyx_module, global_config,
                              user_info, user_mode, parse_and_execute, ctx, **kwargs)
            except Exception as e:
                console.print(f"[red]{_t('ai_error', current_lang).format(str(e))}[/]")
            finally:
                _signal.signal(_signal.SIGINT, _old_sigint)
                if esc_flag[0]:
                    console.print(f"\n[dim]📌 {_t('esc_marked', current_lang)}[/]")

            # AI 回复后 → 检查 ESC 标记
            if esc_flag[0]:
                esc_flag[0] = False
                console.print(f"[dim]💬 {_t('ask_after_esc', current_lang)}[/]")

    except Exception as e:
        console.print(f"[red]{_t('ai_exception', current_lang).format(str(e))}[/]")
    finally:
        console.print(f"[dim]{_t('ai_exited', current_lang)}[/]")


def _call_ai_engine(
    question: str,
    user_home_dir: str,
    onyx_module,
    global_config: Dict,
    user_info: Dict,
    user_mode,
    parse_and_execute: Callable,
    ctx: Dict,
    **kwargs
) -> None:
    """
    单次 AI 交互：发问题 → 收响应 → 执行命令/工具 → 显示结果。
    
    当前实现：委托给 ai_cmd.handle_ai 单次调用，
    后续 step-3 会重构为真正的引擎循环。
    """
    from bin.ai_cmd import handle_ai

    # 构造 cmd_parts 模拟 ai 命令
    cmd_parts = ["ai", question]

    try:
        # 使用 REPL 会话级 session_id，同一会话的所有交互写入同一个 library 文件
        call_request_id = ctx.get("session_id", str(uuid.uuid4()))
        handle_ai(
            cmd_parts=cmd_parts,
            request_id=call_request_id,
            onyx_module=onyx_module,
            user_home_dir=user_home_dir,
            global_config=global_config,
            user_info=user_info or {"name": "default", "session_id": ctx["session_id"]},
            user_mode=user_mode,
            parse_and_execute=parse_and_execute,
            _in_repl=True,
            **{k: v for k, v in kwargs.items() if k not in ("cmd_parts", "request_id", "onyx_module", "user_home_dir", "global_config", "user_info", "user_mode", "parse_and_execute")}
        )
    except Exception as e:
        console.print(f"[red]{_t('ai_error', ctx.get('lang', 'chinese')).format(str(e))}[/]")
