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
    "/quiet":  "切换精简模式（隐藏 token/耗时等辅助信息）",
    "/tokens": "显示当前会话累计 token 用量",
    "/stats":  "显示会话统计（轮数、token、耗时）",
    "/reset":  "重置对话上下文，开始新对话",
    "/export": "导出对话记录到文件",
    "/lang":   "切换语言 (cn/en)",
    "/mode":   "切换 AI 模式 (normal/plan)",
    "/time":   "切换是否显示每轮耗时",
    "/config": "⚙️ 统一配置 AI 模型/密钥/参数",
    "/model":  "查看/切换 AI 模型与参数",
    "/param":  "查看/设置模型参数 (temperature/top_p/max_tokens/effort)",
    "/key":    "查看/更换 API 密钥",
    "/chat":   "管理聊天记忆 (list / switch <name> / new <name>)",
    "/mcp":    "MCP 服务器管理 (list / install <name> / remove <name>)",
    "/compact": "手动压缩对话历史，释放上下文空间",
}

_SLASH_COMMANDS_EN: Dict[str, str] = {
    "/help":   "Show this help",
    "/exit":   "Exit AI mode, return to shell",
    "/quit":   "Same as /exit",
    "/clear":  "Clear screen",
    "/quiet":  "Toggle quiet mode (hide token/timing info)",
    "/tokens": "Show cumulative token usage for this session",
    "/stats":  "Show session statistics (rounds, tokens, time)",
    "/reset":  "Reset conversation context, start fresh",
    "/export": "Export conversation to file",
    "/lang":   "Switch language (cn/en)",
    "/mode":   "Switch AI mode (normal/plan)",
    "/time":   "Toggle per-round timing display",
    "/config": "⚙️ Unified AI config (model/key/params)",
    "/model":  "View/switch AI model and params",
    "/param":  "View/set model params (temperature/top_p/max_tokens/effort)",
    "/key":    "View/change API key",
    "/chat":   "Manage chat memory (list / switch <name> / new <name>)",
    "/mcp":    "MCP server management (list / install <name> / remove <name>)",
    "/compact": "Manually compress conversation history to free context",
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
    """获取双语文本 — 委托 I18n 单例"""
    from bin.ai_lib.i18n import I18n
    return I18n.get_instance().t(key, lang, **fmt)


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


# ─────────────────────────────── 配置写入辅助 ───────────────────────────────

def _save_conf(conf: dict, ctx: Dict[str, Any]) -> None:
    """将配置 dict 完整写入 key.conf（api_key 混淆存储）"""
    import json as _json
    key_conf_path = os.path.join(ctx["user_home_dir"], ".config", "onyx", "ai", "key.conf")
    os.makedirs(os.path.dirname(key_conf_path), exist_ok=True)
    # 混淆 api_key 后再写入
    write_conf = dict(conf)
    if "api_key" in write_conf and isinstance(write_conf["api_key"], str):
        from bin.ai_cmd import _obfuscate as _obs
        write_conf["api_key"] = _obs(write_conf["api_key"])
    with open(key_conf_path, "w", encoding="utf-8") as f:
        _json.dump(write_conf, f, ensure_ascii=False, indent=2)
    os.chmod(key_conf_path, 0o600)


# ─────────────────────────────── 参数编辑辅助 ───────────────────────────────

def _edit_params_interactive(conf: dict, lang: str) -> dict:
    """交互式编辑模型参数，返回更新后的 params dict"""
    from bin.ai_lib.ui import text_input
    from bin.ai_cmd import _SUPPORTED_PLATFORMS

    params = conf.get("params", {})
    if not isinstance(params, dict):
        params = {}
    platform = conf.get("platform", "deepseek")
    info = _SUPPORTED_PLATFORMS.get(platform, {}) if platform != "custom" else {}
    default_params = info.get("params", {})

    def _input(label: str, key: str, default_val, converter=None):
        current = params.get(key, default_val)
        raw = text_input(f"{label} [{current}]:", str(current), lang=lang)
        if raw:
            try:
                params[key] = converter(raw) if converter else raw
            except (ValueError, TypeError):
                pass

    _input("temperature (0-2)", "temperature", default_params.get("temperature", 0.1), float)
    _input("top_p (0-1)", "top_p", default_params.get("top_p", 0.2), float)
    _input("max_tokens", "max_tokens", default_params.get("max_tokens", 4096), int)
    _input("reasoning_effort (high/max)", "reasoning_effort",
           params.get("reasoning_effort", ""), str)

    return params


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

    elif cmd == "/model":
        from bin.ai_cmd import load_key_conf, _SUPPORTED_PLATFORMS
        from bin.ai_lib.ui import text_input
        conf = load_key_conf()
        if not conf:
            console.print(f"[yellow]{_t('no_key', lang)}[/]")
            return True
        platform = conf.get("platform", "deepseek")
        current_model = conf.get("model", "")
        api_url = conf.get("api_url", "")
        params = conf.get("params", {})
        is_custom = (platform == "custom")
        plat_name = "Custom" if is_custom else _SUPPORTED_PLATFORMS.get(platform, {}).get("name", platform)

        # ── 展示当前配置概览 ──
        unset_label = _t("unset", lang)
        default_label = _t("default", lang)
        param_items = ", ".join(f"{k}={v}" for k, v in sorted(params.items())) or default_label
        console.print(Panel(
            f"{_t('label_platform', lang)}{plat_name} ({platform})\n"
            f"{_t('label_model', lang)}{current_model or unset_label}\n"
            f"{_t('label_params', lang)}{param_items}\n"
            f"{_t('label_url', lang)}{api_url or default_label}",
            title=_t("config_title", lang), border_style="cyan"
        ))

        # ── 列出可用模型 ──
        if is_custom:
            models = [current_model] if current_model else ["gpt-4"]
        else:
            models = _SUPPORTED_PLATFORMS.get(platform, {}).get("models", [])
        if not models:
            console.print("[yellow]No models available[/]")
            return True

        console.print(_t("model_list_title", lang, platform=plat_name))
        for i, m in enumerate(models, 1):
            marker = " ←" if m == current_model else ""
            console.print(f"  [{i}] {m}{marker}")

        # ── 选择新模型 ──
        try:
            choice = input(_t("model_select_prompt", lang)).strip()
            if not choice:
                console.print(f"[dim]{_t('model_cancelled', lang)}[/]")
                return True
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                new_model = models[idx]
                conf["model"] = new_model
                _save_conf(conf, ctx)
                console.print(_t("model_switched", lang, model=new_model))

                # ── 询问是否编辑参数 ──
                try:
                    if input(_t("edit_params_prompt", lang)).strip().lower() == "y":
                        conf["params"] = _edit_params_interactive(conf, lang)
                        _save_conf(conf, ctx)
                        console.print(_t("config_ok_params", lang))
                except (KeyboardInterrupt, EOFError):
                    pass
            else:
                console.print(f"[yellow]Invalid selection[/]")
        except (ValueError, KeyboardInterrupt, EOFError):
            console.print(f"[dim]{_t('model_cancelled', lang)}[/]")
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

    elif cmd == "/config":
        """⚙️ 统一配置菜单 — 平台/模型/密钥/参数/URL"""
        from bin.ai_cmd import load_key_conf, _SUPPORTED_PLATFORMS, _setup_key_conf_interactive
        from bin.ai_lib.ui import select_option, text_input

        while True:
            conf = load_key_conf()
            if not conf:
                console.print(f"[yellow]{_t('no_key', lang)}[/]")
                new_conf = _setup_key_conf_interactive(lang)
                if not new_conf:
                    return True
                conf = load_key_conf()
                if not conf:
                    return True

            platform = conf.get("platform", "deepseek")
            current_model = conf.get("model", "")
            api_key = conf.get("api_key", "")
            params = conf.get("params", {})
            api_url = conf.get("api_url", "")

            masked_key = api_key[:4] + "*" * 24 + api_key[-4:] if len(api_key) > 28 else "***"
            unset_label = _t("unset", lang)
            default_label = _t("default", lang)
            param_items = ", ".join(f"{k}={v}" for k, v in sorted(params.items())) or default_label
            plat_name = "Custom" if platform == "custom" else _SUPPORTED_PLATFORMS.get(platform, {}).get("name", platform)

            # ── 展示当前配置 ──
            console.print(Panel(
                f"{_t('label_platform', lang)}{plat_name} ({platform})\n"
                f"{_t('label_model', lang)}{current_model or unset_label}\n"
                f"{_t('label_key', lang)}{masked_key}\n"
                f"{_t('label_params', lang)}{param_items}\n"
                f"{_t('label_url', lang)}{api_url or default_label}",
                title=_t("config_title", lang), border_style="cyan"
            ))

            # ── 菜单 ──
            if lang == "chinese":
                opts = ["🔄 切换平台", "🤖 切换模型", "🔑 更换密钥",
                        "⚙️ 编辑参数", "🌐 自定义 URL", "❌ 关闭"]
            else:
                opts = ["🔄 Change platform", "🤖 Change model", "🔑 Change key",
                        "⚙️ Edit params", "🌐 Custom URL", "❌ Close"]
            choice = select_option(
                "选择操作:" if lang == "chinese" else "Action:",
                opts, default=opts[0], lang=lang
            )
            if not choice or choice == opts[-1]:
                break

            idx = opts.index(choice)

            # ── 切换平台 ──
            if idx == 0:
                platforms = list(_SUPPORTED_PLATFORMS.keys())
                plat_labels = [_SUPPORTED_PLATFORMS[p]["name"] for p in platforms]
                p_choice = select_option(
                    "选择 AI 平台" if lang == "chinese" else "Select AI platform",
                    plat_labels + ["Custom"], default=plat_labels[0], lang=lang
                )
                if not p_choice:
                    continue
                if p_choice == "Custom":
                    conf["platform"] = "custom"
                    url = text_input(
                        "API 地址:" if lang == "chinese" else "API URL:",
                        "https://api.openai.com/v1/chat/completions", lang=lang
                    )
                    if url:
                        conf["api_url"] = url
                    model_name = text_input(
                        "模型名称:" if lang == "chinese" else "Model name:",
                        "gpt-4", lang=lang
                    )
                    if model_name:
                        conf["model"] = model_name
                else:
                    p_idx = plat_labels.index(p_choice)
                    new_plat = platforms[p_idx]
                    conf["platform"] = new_plat
                    info = _SUPPORTED_PLATFORMS[new_plat]
                    conf["model"] = info.get("default_model", info["models"][0])
                    conf["params"] = dict(info.get("params", {}))
                    if platform == "custom":
                        conf.pop("api_url", None)
                _save_conf(conf, ctx)
                _ok = "✅ Platform switched" if lang == "english" else "✅ 平台已切换"
                console.print(f"[green]{_ok}[/]")

            # ── 切换模型 ──
            elif idx == 1:
                is_custom = (conf["platform"] == "custom")
                if is_custom:
                    new_model = text_input(
                        "输入模型名称:" if lang == "chinese" else "Enter model name:",
                        current_model, lang=lang
                    )
                    if new_model:
                        conf["model"] = new_model
                        _save_conf(conf, ctx)
                        _ok = f"✅ Model switched: {new_model}" if lang == "english" else f"✅ 模型已切换: {new_model}"
                        console.print(f"[green]{_ok}[/]")
                    continue
                info = _SUPPORTED_PLATFORMS.get(platform)
                if not info:
                    continue
                models = info.get("models", [])
                m_labels = [f"{m} {'← 当前' if m == current_model else ''}" for m in models]
                m_choice = select_option(
                    f"选择模型 ({info['name']}):" if lang == "chinese" else f"Select model ({info['name']}):",
                    m_labels, default=m_labels[0], lang=lang
                )
                if m_choice:
                    new_model = m_choice.split(" ←")[0].strip()
                    conf["model"] = new_model
                    _save_conf(conf, ctx)
                    _ok = f"✅ Model switched: {new_model}" if lang == "english" else f"✅ 模型已切换: {new_model}"
                    console.print(f"[green]{_ok}[/]")

            # ── 更换密钥 ──
            elif idx == 2:
                new_key = text_input(
                    "请输入新的 API Key:" if lang == "chinese" else "Enter new API Key:",
                    "", lang=lang
                )
                if new_key:
                    conf["api_key"] = new_key
                    _save_conf(conf, ctx)
                    _ok = "✅ Key updated" if lang == "english" else "✅ 密钥已更新"
                    console.print(f"[green]{_ok}[/]")

            # ── 编辑参数 ──
            elif idx == 3:
                conf["params"] = _edit_params_interactive(conf, lang)
                _save_conf(conf, ctx)
                console.print(_t("config_ok_params", lang))

            # ── 自定义 URL ──
            elif idx == 4:
                url = text_input(
                    "API 地址:" if lang == "chinese" else "API URL:",
                    api_url or "https://", lang=lang
                )
                if url:
                    conf["api_url"] = url
                    _save_conf(conf, ctx)
                    _ok = "✅ API URL updated" if lang == "english" else "✅ API 地址已更新"
                    console.print(f"[green]{_ok}[/]")

        return True

    elif cmd == "/param":
        """快速查看/设置模型参数"""
        from bin.ai_cmd import load_key_conf
        conf = load_key_conf()
        if not conf:
            console.print(f"[yellow]{_t('no_key', lang)}[/]")
            return True

        params = conf.get("params", {})
        if not isinstance(params, dict):
            params = {}

        if not args:
            console.print(f"\n{_t('param_title', lang)}")
            if params:
                for k, v in sorted(params.items()):
                    console.print(f"  {k} = {v}")
            else:
                console.print(f"  {_t('param_no_custom', lang)}")
            console.print(f"\n{_t('param_usage', lang)}")
            console.print(_t("param_valid_names", lang))
            return True

        if len(args) == 2:
            name, value = args[0].lower(), args[1]
            valid_params = {
                "temperature": float, "top_p": float,
                "max_tokens": int, "reasoning_effort": str
            }
            if name not in valid_params:
                console.print(_t("param_invalid_name", lang, name=name))
                console.print(_t("param_valid_names", lang))
                return True
            try:
                if name == "reasoning_effort":
                    if value.lower() not in ("high", "max"):
                        console.print(_t("param_effort_invalid", lang))
                        return True
                    params[name] = value.lower()
                elif name == "max_tokens":
                    params[name] = int(value)
                else:
                    params[name] = float(value)
            except ValueError:
                console.print(_t("param_invalid_value", lang, value=value))
                return True

            conf["params"] = params
            _save_conf(conf, ctx)
            console.print(_t("param_set_ok", lang, name=name, value=value))
            return True

        console.print(_t("param_usage_full", lang))
        return True

    elif cmd == "/quiet":
        # 切换精简模式
        current = ctx.get("quiet", False)
        ctx["quiet"] = not current
        status = _t("quiet_on", lang) if not current else _t("quiet_off", lang)
        console.print(f"[dim]{status}[/]")
        return True

    elif cmd == "/tokens":
        """显示累计 token 用量（依赖 API 精确值）"""
        from bin.ai_lib.mcp_state import _thread_locals
        sp = getattr(_thread_locals, 'session_total_prompt', 0)
        sc = getattr(_thread_locals, 'session_total_completion', 0)
        rc = getattr(_thread_locals, 'session_round_count', 0)
        ch = getattr(_thread_locals, 'session_total_cache_hit', 0)
        if sp or sc:
            total = sp + sc
            if lang == "chinese":
                console.print(Panel(
                    f"累计 Token 用量（API 精确值）\n\n"
                    f"  交互轮数: {rc}\n"
                    f"  Prompt:     {sp:,} tokens\n"
                    f"  Completion: {sc:,} tokens\n"
                    f"  合计:       {total:,} tokens\n"
                    f"  Cache 命中: {ch:,} tokens" if ch else f"  合计: {total:,} tokens",
                    title="📊 Token 统计", border_style="cyan"
                ))
            else:
                console.print(Panel(
                    f"Cumulative Token Usage (API precise)\n\n"
                    f"  Rounds:  {rc}\n"
                    f"  Prompt:     {sp:,} tokens\n"
                    f"  Completion: {sc:,} tokens\n"
                    f"  Total:      {total:,} tokens\n"
                    f"  Cache hit:  {ch:,} tokens" if ch else f"  Total: {total:,} tokens",
                    title="📊 Token Stats", border_style="cyan"
                ))
        else:
            no_data = _t("no_token_data", lang)
            console.print(f"[dim]{no_data}[/]")
        return True

    elif cmd == "/stats":
        """会话统计"""
        from bin.ai_lib.mcp_state import _thread_locals
        sp = getattr(_thread_locals, 'session_total_prompt', 0)
        sc = getattr(_thread_locals, 'session_total_completion', 0)
        rc = getattr(_thread_locals, 'session_round_count', 0)
        start = ctx.get("session_start", 0)
        elapsed = time.time() - start if start else 0
        avg = elapsed / rc if rc > 0 else 0
        if lang == "chinese":
            console.print(Panel(
                f"  交互轮数:   {rc}\n"
                f"  Token 总计: {sp + sc:,}\n"
                f"  会话时长:   {elapsed:.0f}s\n"
                f"  平均每轮:   {avg:.1f}s",
                title="📈 会话统计", border_style="cyan"
            ))
        else:
            console.print(Panel(
                f"  Rounds:      {rc}\n"
                f"  Total tokens: {sp + sc:,}\n"
                f"  Duration:    {elapsed:.0f}s\n"
                f"  Avg/round:   {avg:.1f}s",
                title="📈 Session Stats", border_style="cyan"
            ))
        return True

    elif cmd == "/reset":
        """重置对话上下文"""
        from bin.ai_lib.mcp_state import _thread_locals
        # 清除累计 token
        for attr in ('session_total_prompt', 'session_total_completion',
                     'session_total_cache_hit', 'session_round_count',
                     'last_prompt_tokens', 'last_completion_tokens',
                     'last_cache_hit', 'last_cache_miss'):
            if hasattr(_thread_locals, attr):
                delattr(_thread_locals, attr)
        # 重置会话起始时间
        ctx["session_start"] = time.time()
        confirm = _t("reset_done", lang)
        console.print(f"[green]{confirm}[/]")
        return True

    elif cmd == "/export":
        """导出对话记录到文件"""
        from bin.ai_cmd import get_ai_session_library_dir, get_current_chat_name
        home = ctx["user_home_dir"]
        lib_dir = get_ai_session_library_dir(home)
        session_id = ctx.get("session_id", "")
        src = os.path.join(lib_dir, f"{session_id}.txt") if session_id else ""
        if src and os.path.exists(src):
            export_name = f"ai_export_{session_id[:8]}.md"
            dst = os.path.join(home, export_name)
            try:
                with open(src, "r", encoding="utf-8") as f:
                    content = f.read()
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(f"# AI 对话导出\n\n{content}")
                console.print(f"[green]{_t('export_ok', lang).format(dst)}[/]")
            except Exception as e:
                console.print(f"[red]{_t('export_fail', lang).format(e)}[/]")
        else:
            console.print(f"[yellow]{_t('export_no_data', lang)}[/]")
        return True

    elif cmd == "/lang":
        """切换语言"""
        if args:
            new_lang = args[0].lower()
            if new_lang in ("cn", "chinese", "zh"):
                ctx["lang"] = "chinese"
            elif new_lang in ("en", "english"):
                ctx["lang"] = "english"
            else:
                console.print(f"[yellow]{_t('lang_invalid', lang).format(new_lang)}[/]")
                return True
            console.print(f"[green]{_t('lang_switched', ctx['lang'])}[/]")
        else:
            # 无参数时切换
            ctx["lang"] = "english" if lang == "chinese" else "chinese"
            console.print(f"[green]{_t('lang_switched', ctx['lang'])}[/]")
        return True

    elif cmd == "/mode":
        """切换 AI 模式"""
        if args:
            new_mode = args[0].lower()
            if new_mode in ("normal", "plan"):
                ctx["mode"] = new_mode
                console.print(f"[green]{_t('mode_switched', lang).format(new_mode)}[/]")
            else:
                console.print(f"[yellow]{_t('mode_invalid', lang).format(new_mode)}[/]")
        else:
            current_mode = ctx.get("mode", "normal")
            new_mode = "plan" if current_mode == "normal" else "normal"
            ctx["mode"] = new_mode
            console.print(f"[green]{_t('mode_switched', lang).format(new_mode)}[/]")
        return True

    elif cmd == "/time":
        """切换每轮耗时显示"""
        current = ctx.get("show_time", True)
        ctx["show_time"] = not current
        status = _t("time_on", lang) if ctx["show_time"] else _t("time_off", lang)
        console.print(f"[dim]{status}[/]")
        return True

    elif cmd == "/compact":
        """手动压缩对话历史"""
        import bin.ai_lib.mcp_state as _mcp_shared
        _mcp_shared._MANUAL_COMPACT_REQUESTED = True
        console.print(f"[green]{_t('compact_queued', lang)}[/]")
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
        "mode": "normal",
        "quiet": False,
        "show_time": True,
        "session_start": time.time(),
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
