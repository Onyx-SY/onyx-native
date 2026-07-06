"""core/cmd_registry.py — 内置命令注册表 + 交互式命令检测"""

import os
from typing import Dict, List, Callable, TYPE_CHECKING, FrozenSet

if TYPE_CHECKING:
    from core.context import AppContext

# 默认交互式命令集合
_INTERACTIVE_DEFAULT: FrozenSet[str] = frozenset([
    "vim", "vi", "nano", "top", "htop", "less", "more", "watch",
    "ssh", "telnet", "python", "python3", "irb", "node",
])


def get_interactive_commands(ctx: "AppContext") -> FrozenSet[str]:
    """获取交互式命令集合（合并默认 + 用户自定义）"""
    user_cmds = _load_user_interactive_cmds(ctx)
    if user_cmds:
        return ctx._INTERACTIVE_DEFAULT | frozenset(user_cmds)
    return ctx._INTERACTIVE_DEFAULT


def _load_user_interactive_cmds(ctx: "AppContext") -> List[str]:
    """从用户配置加载交互式命令"""
    path = os.path.join(ctx.USER_HOME_DIR, ".onyx", "interactive_cmds")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except Exception:
        return []


def is_interactive_command(ctx: "AppContext", cmd: str) -> bool:
    """判断命令是否为交互式命令"""
    from lib.terminal.exe import is_interactive_command as _is_interactive
    user_cmds = _load_user_interactive_cmds(ctx)
    return _is_interactive(cmd, user_cmds)


def build_builtin_registry(ctx: "AppContext") -> Dict[str, Callable]:
    """构建内置命令注册表（延迟导入 handler，避免循环依赖）"""
    from core.handlers.builtins import (
        handle_clear, handle_exit, handle_run, handle_export,
        handle_cd, handle_ai, handle_import, handle_switch_prompt,
        handle_set_adv_pwd, handle_autocmd, handle_unalias,
        handle_mktool, handle_sado, handle_nanosado, handle_activite,
    )

    registry: Dict[str, Callable] = {
        "clear": handle_clear,
        "exit": handle_exit,
        "run": handle_run,
        "export": handle_export,
        "activite": handle_activite,
        "import": handle_import,
        "switch-prompt": handle_switch_prompt,
        "ai": handle_ai,
        "set-adv-pwd": handle_set_adv_pwd,
        "autocmd": handle_autocmd,
        "mktool": handle_mktool,
        "unalias": handle_unalias,
        "cd": handle_cd,
        "sado": handle_sado,
        "nanosado": handle_nanosado,
    }

    # 延迟导入项
    registry["manage"] = _lazy("manage")
    registry["help"] = _lazy("help")
    registry["source"] = _lazy("source")
    registry["which"] = _lazy("which")
    registry["refresh"] = _make_refresh(ctx)

    ctx.BUILTIN_COMMANDS = registry
    return registry


def _lazy(name: str) -> Callable:
    """创建延迟加载包装器"""
    def wrapper(cmd_parts, request_id):
        if name == "manage":
            from bin.manage import handle_manage
            handle_manage(cmd_parts, request_id)
        elif name == "help":
            from bin.help.help import handle_help
            handle_help(cmd_parts, request_id)
        elif name == "source":
            from bin.source_cmd import handle_source
            handle_source(cmd_parts, request_id)
        elif name == "which":
            from bin.which_cmd import handle_which
            handle_which(cmd_parts, request_id)
    return wrapper


def _make_refresh(ctx: "AppContext") -> Callable:
    def refresh(cmd_parts, request_id):
        if ctx.executor:
            from core.tool_registry import build_tool_index
            ctx.executor.submit(build_tool_index, ctx, request_id)
    return refresh
