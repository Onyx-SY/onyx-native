"""core/display.py — prompt 生成、欢迎界面（从 Onyx.py 提取）"""

import os
import re
import time
import socket
from typing import Union, TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext
    from prompt_toolkit.formatted_text import FormattedText


def generate_prompt(ctx: "AppContext") -> "Union[FormattedText, str]":
    """根据小窗功能状态返回对应提示符"""
    from core.path_ops import get_virtual_path, format_virtual_path

    # 会话不变值缓存
    if ctx._CACHED_HOSTNAME is None:
        ctx._CACHED_HOSTNAME = socket.gethostname()

    # 小窗配置读取（30s 缓存）
    now = time.time()
    if ctx._comp_window_cache is None or (now - ctx._comp_window_cache_time) >= ctx._comp_window_cache_ttl:
        COMP_WINDOW_PATH = os.path.join(ctx.USER_HOME_DIR, ".config", "onyx", "comp-window")
        from Onyx import read_config_file
        ctx._comp_window_cache = read_config_file(COMP_WINDOW_PATH, True)
        ctx._comp_window_cache_time = now
    enable_comp_window = ctx._comp_window_cache

    current_dir = os.getcwd()
    virtual_path = get_virtual_path(ctx, current_dir)
    venv_git = _get_venv_git_status(ctx)

    if ctx._LAST_EXIT_CODE == 0:
        exit_mark_raw, exit_mark_hex = "✓", "#2ecc71"
    else:
        exit_mark_raw, exit_mark_hex = f"✗ ({ctx._LAST_EXIT_CODE})", "#e74c3c"

    if ctx._CACHED_PROMPT_CONF:
        template = ctx._CACHED_PROMPT_CONF
    else:
        prompt_type = ctx.global_config["system_info"].get("current_prompt_type", "def")
        templates = ctx.global_config["display_info"]["command_prompts"]
        template = templates.get(prompt_type, templates["def"])

    if enable_comp_window:
        return _build_comp_window_prompt(ctx, template, virtual_path, venv_git, exit_mark_hex)
    else:
        return _build_plain_prompt(ctx, template, virtual_path, venv_git, exit_mark_raw, exit_mark_hex)


def _get_venv_git_status(ctx: "AppContext") -> str:
    """获取 venv + git 状态"""
    from core.path_ops import get_virtual_path
    cwd = os.getcwd()
    vpath = get_virtual_path(ctx, cwd)
    parts = []
    # git branch
    branch = _get_git_branch_fast()
    if branch:
        dirty = _get_git_dirty_fast()
        parts.append(f"git:({branch}{'*' if dirty else ''})")
    return " ".join(parts) if parts else ""


def _get_git_branch_fast() -> str:
    """快速获取 git 分支名"""
    try:
        head = os.path.join(os.getcwd(), ".git", "HEAD")
        if os.path.exists(head):
            with open(head, "r") as f:
                ref = f.read().strip()
            if ref.startswith("ref: refs/heads/"):
                return ref[16:]
    except Exception:
        pass
    return ""


def _get_git_dirty_fast() -> bool:
    """快速检查 git 是否有未提交变更"""
    try:
        index = os.path.join(os.getcwd(), ".git", "index")
        if os.path.exists(index):
            return os.path.getsize(index) > 0
    except Exception:
        pass
    return False


def _build_comp_window_prompt(ctx, template, virtual_path, venv_git, exit_mark_hex):
    """小窗模式 prompt（FormattedText）"""
    from core.path_ops import format_virtual_path
    from prompt_toolkit.formatted_text import FormattedText
    accent_key = "ACCENT_GREEN" if ctx._LAST_EXIT_CODE == 0 else "ACCENT_RED"
    pt = template.replace("{exit_mark}", "")\
        .replace("{BLUE}", "[COLOR=BLUE]")\
        .replace("{RED}", "[COLOR=RED]")\
        .replace("{GREEN}", "[COLOR=GREEN]")\
        .replace("{YELLOW}", "[COLOR=YELLOW]")\
        .replace("{RESET}", "[COLOR=RESET]")\
        .replace("{accent}", f"[COLOR={accent_key}]")\
        .replace("{accent_reset}", "[COLOR=RESET]")

    formatted_parts = []
    current_color = ""
    segments = re.split(r'\[COLOR=(BLUE|RED|GREEN|YELLOW|RESET|ACCENT_GREEN|ACCENT_RED)\]', pt)
    for seg in segments:
        if not seg.strip():
            continue
        if seg in ctx._COLOR_STYLES:
            current_color = ctx._COLOR_STYLES[seg]
        else:
            resolved = seg.format(
                user=ctx.user_info["name"], mode_per=ctx.user_mode.current_mode,
                mode_TS=ctx.OS_OR_TBS, sys_type="Onyx",
                relative_path=format_virtual_path(virtual_path),
                permission=ctx.user_info["permission_flag"],
                host=ctx._CACHED_HOSTNAME, venv_git=venv_git)
            formatted_parts.append((current_color, resolved))
    return FormattedText(formatted_parts)


def _build_plain_prompt(ctx, template, virtual_path, venv_git, exit_mark_raw, exit_mark_hex):
    """标准模式 prompt（HTML）"""
    from core.path_ops import format_virtual_path
    from prompt_toolkit.formatted_text import HTML
    ptk_template = template
    for tag, hex_color in ctx._COLOR_STYLES.items():
        if tag == "RESET":
            ptk_template = ptk_template.replace("{RESET}", "</style>")
        else:
            ptk_template = ptk_template.replace(f"{{{tag}}}", f'<style fg="{hex_color}">')
    ptk_template = ptk_template.replace("{accent}", f'<style fg="{exit_mark_hex}">')
    ptk_template = ptk_template.replace("{accent_reset}", "</style>")
    plain = ptk_template.format(
        user=ctx.user_info["name"], mode_per=ctx.user_mode.current_mode,
        mode_TS=ctx.OS_OR_TBS, sys_type="Onyx",
        relative_path=format_virtual_path(virtual_path),
        permission=ctx.user_info["permission_flag"],
        host=ctx._CACHED_HOSTNAME, venv_git=venv_git,
        exit_mark=f'<style fg="{exit_mark_hex}">{exit_mark_raw}</style>')
    return HTML(plain)
