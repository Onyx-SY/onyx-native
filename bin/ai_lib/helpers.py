# -*- coding: utf-8 -*-
"""
Onyx AI 辅助函数模块 — sleep等待、线程优先级、计划确认、参数解析、loading动画、危险命令

从 bin/ai_cmd.py 提取，零功能变更。
"""

import os
import sys
import time
import threading
import re
import uuid
from typing import Dict, List, Tuple, Optional, Any, Callable

from rich.console import Console
console = Console()

from .config import get_current_lang
from .ui import select_option, render_plan_panel, real_terminal_io, confirm_dangerous as ui_confirm_dangerous


def handle_sleep_wait(sleep_seconds: int, session_id: str, lang_text: Dict[str, str], log_info: Callable = None) -> Tuple[bool, int]:
    """处理AI的sleep等待，返回(是否被中断, 实际等待秒数)"""
    interrupted = False
    waited_seconds = 0

    try:
        for i in range(1, sleep_seconds + 1):
            threading.Event().wait(1)  # 可被 Ctrl+C 中断，替代 time.sleep
            waited_seconds = i
            console.print(f"\r{lang_text['sleep_countdown'].format(sleep_seconds, i, sleep_seconds)}", end="", style="bold blue")
        console.print(f"\n{lang_text['sleep_completed'].format(sleep_seconds)}", style="bold green")
        if log_info:
            log_info(f"AI sleep {sleep_seconds} seconds completed", session_id)
    except KeyboardInterrupt:
        interrupted = True
        console.print(f"\n{lang_text['sleep_interrupted']}", style="bold yellow")
        if log_info:
            log_info(f"AI sleep interrupted after {waited_seconds} seconds", session_id)
    return interrupted, waited_seconds


def set_ai_thread_priority(lang_text: Dict[str, str], thread: threading.Thread, is_core_task: bool = True, onyx_module=None) -> None:
    try:
        if onyx_module and hasattr(onyx_module, "sys_type") and onyx_module.sys_type == "Windows":
            import ctypes
            THREAD_PRIORITY_HIGHEST = 2
            THREAD_PRIORITY_LOWEST = -2
            priority = "High" if is_core_task else "Low"
            thread_id = ctypes.c_longlong(thread.ident)
            handle = ctypes.windll.kernel32.OpenThread(0x001F03FF, False, thread_id)
            if handle:
                ctypes.windll.kernel32.SetThreadPriority(handle, THREAD_PRIORITY_HIGHEST if is_core_task else THREAD_PRIORITY_LOWEST)
                ctypes.windll.kernel32.CloseHandle(handle)
                win_msg = lang_text.get("thread_priority_win", "Windows thread priority: {} (ID: {})")
                if onyx_module and hasattr(onyx_module, "log_info"):
                    onyx_module.log_info(win_msg.format(priority, thread.ident), str(uuid.uuid4()))
        else:
            current_nice = os.nice(0)
            target_nice = max(-10, current_nice - 5) if is_core_task else min(10, current_nice + 5)
            os.nice(target_nice - current_nice)
            task_type = "core" if is_core_task else "non-core"
            linux_msg = lang_text.get("thread_priority_linux", "Linux/Termux priority: nice {}→{} ({task} task)")
            if onyx_module and hasattr(onyx_module, "log_info"):
                onyx_module.log_info(linux_msg.format(current_nice, target_nice, task=task_type), str(uuid.uuid4()))
    except Exception as e:
        fail_msg = lang_text.get("thread_priority_fail", "Thread priority adjustment failed: {} (function not affected)")
        if onyx_module and hasattr(onyx_module, "log_warning"):
            onyx_module.log_warning(fail_msg.format(str(e)[:30]), str(uuid.uuid4()))


def confirm_plan(plan_text: str, lang_text: Dict[str, str]) -> str:
    """上下键选择 Plan 确认流程：Rich Panel 展示计划 + 箭头键选择。
    返回: "confirm" / "guide" / "discard"
    """
    console.print(render_plan_panel(plan_text))
    console.print()
    try:
        choice = select_option(
            message=lang_text.get("plan_prompt", "请选择操作: / Please choose:"),
            options=[
                lang_text.get("plan_opt_confirm", "✅ 确认计划，开始执行 | ✅ Confirm plan and start"),
                lang_text.get("plan_opt_guide", "💡 提出修改意见 | 💡 Suggest changes"),
                lang_text.get("plan_opt_discard", "🗑️ 摒弃计划，重新制定 | 🗑️ Discard and redo"),
            ],
            default=lang_text.get("plan_opt_confirm", "✅ 确认计划，开始执行 | ✅ Confirm plan and start"),
            lang=get_current_lang(),
        )
    except (KeyboardInterrupt, EOFError):
        console.print()
        return "confirm"
    if choice in (lang_text.get("plan_opt_discard", "🗑️ 摒弃计划，重新制定 | 🗑️ Discard and redo"),):
        return "discard"
    elif choice in (lang_text.get("plan_opt_guide", "💡 提出修改意见 | 💡 Suggest changes"),):
        return "guide"
    return "confirm"


def parse_arguments(cmd_parts: List[str], lang_text: Dict[str, str], onyx_module=None) -> Tuple:
    if onyx_module and not hasattr(onyx_module, "SANDBOX_CONFIG"):
        onyx_module.SANDBOX_CONFIG = {"enable": False}

    ai_args = cmd_parts[1:] if len(cmd_parts) > 1 else []
    auto_exec = True
    content_type = "direct"
    content = ""
    extra_info = None
    new_key = None
    chat_action = None
    chat_param = None
    mode = "normal"
    times = 1
    mode_explicitly_set = False  # 用户是否显式指定了模式
    debug_requested = False      # --debug 修饰符已出现

    i = 0
    while i < len(ai_args):
        arg = ai_args[i]
        if arg == "-model":
            model_name = ai_args[i + 1] if i + 1 < len(ai_args) and not ai_args[i + 1].startswith("-") else None
            i += 2 if model_name else 1
            return ("model_command", model_name or "", [], auto_exec, new_key, None, None, mode, times)
        elif arg == "-effort":
            effort_val = ai_args[i + 1] if i + 1 < len(ai_args) and not ai_args[i + 1].startswith("-") else None
            i += 2 if effort_val else 1
            return ("effort_command", effort_val or "", [], auto_exec, new_key, None, None, mode, times)
        elif arg in ("-mid", "-machine-id"):
            return ("machine_id_command", "", [], auto_exec, new_key, None, None, mode, times)
        elif arg in ("-plugin", "plugin"):
            sub = ai_args[i + 1] if i + 1 < len(ai_args) and not ai_args[i + 1].startswith("-") else "list"
            extra = []
            if sub in ("load", "sign", "verify", "compile"):
                extra = ai_args[i + 2:] if i + 2 < len(ai_args) else []
                i += len(extra) + 2
            else:
                i += 2 if sub != "list" else 1
            return ("plugin_command", sub, extra, auto_exec, new_key, None, None, mode, times)
        elif arg in ("-mcp", "mcp"):
            if i + 1 >= len(ai_args):
                return ("mcp_command", "list", [], auto_exec, new_key, None, None, mode, times)
            mcp_sub = ai_args[i + 1].lower()
            mcp_args = ai_args[i + 2:] if i + 2 < len(ai_args) else []
            if mcp_sub in ("install", "remove", "list", "start"):
                return ("mcp_command", mcp_sub, mcp_args, auto_exec, new_key, None, None, mode, times)
            return ("error", f"Invalid -mcp subcommand: {mcp_sub}. Use install/list/remove", None, auto_exec, new_key, None, None, mode, times)
        elif arg == "-cmd":
            if i + 1 >= len(ai_args):
                return ("error", lang_text["cmd_option_usage"], None, auto_exec, new_key, None, None, mode, times)
            cmd_val = ai_args[i+1].lower()
            if cmd_val not in ["true", "false"]:
                return ("error", lang_text["cmd_option_usage"], None, auto_exec, new_key, None, None, mode, times)
            auto_exec = (cmd_val == "true")
            i += 2
        elif arg == "-m":
            if i + 1 >= len(ai_args):
                return ("error", "Missing mode for -m parameter", None, auto_exec, new_key, None, None, mode, times)
            mode_val = ai_args[i+1].lower()
            if mode_val not in ["plan", "normal"]:
                return ("error", lang_text.get("invalid_mode", "Invalid -m mode! Must be 'plan' or 'normal' | 无效 -m 模式，必须是 plan 或 normal"), None, auto_exec, new_key, None, None, mode, times)
            mode = mode_val
            mode_explicitly_set = True
            i += 2
        elif arg == "-mode":
            if i + 1 >= len(ai_args):
                return ("error", "Missing mode type for -mode", None, auto_exec, new_key, None, None, mode, times)
            mode_type = ai_args[i+1].lower()
            mode_val = ai_args[i+2] if i + 2 < len(ai_args) and not ai_args[i+2].startswith("-") else "true"
            if mode_type == "deep-aff":
                return ("deep_aff_mode", mode_val, [], auto_exec, new_key, None, None, mode, times)
            return ("error", f"Unknown mode: {mode_type}", None, auto_exec, new_key, None, None, mode, times)
        elif arg == "-c":
            if i + 1 >= len(ai_args):
                return ("chat_only", "", None, auto_exec, new_key, None, None, mode, times)
            chat_action = ai_args[i+1].lower()
            if chat_action in ["switch", "new"]:
                if i + 2 >= len(ai_args):
                    return ("chat_only", f"Missing name for -c {chat_action}", None, auto_exec, new_key, chat_action, None, mode, times)
                chat_param = ai_args[i+2]
                i += 3
            elif chat_action == "list":
                i += 2
            else:
                return ("error", f"Invalid -c action: {chat_action}. Use switch/list/new", None, auto_exec, new_key, None, None, mode, times)
        elif arg == "-t":
            if i + 1 >= len(ai_args):
                return ("error", lang_text["text_usage"], None, auto_exec, new_key, None, None, mode, times)
            text_parts = []
            j = i + 1
            while j < len(ai_args) and not ai_args[j].startswith("-"):
                text_parts.append(ai_args[j])
                j += 1
            if not text_parts:
                return ("error", lang_text["text_usage"], None, auto_exec, new_key, None, None, mode, times)
            content = " ".join(text_parts)
            content_type = "text"
            i = j
        elif arg == "-f":
            if i + 1 >= len(ai_args):
                return ("error", lang_text["file_usage"], None, auto_exec, new_key, None, None, mode, times)
            file_path = ai_args[i+1]
            if onyx_module and hasattr(onyx_module, "SANDBOX_CONFIG") and onyx_module.SANDBOX_CONFIG.get("enable", False):
                if hasattr(onyx_module, "check_sandbox_path") and not onyx_module.check_sandbox_path(file_path, str(uuid.uuid4())):
                    return ("error", lang_text["sandbox_block"].format(file_path), None, auto_exec, new_key, None, None, mode, times)
            if not os.path.exists(file_path):
                return ("error", lang_text["file_not_exist"].format(file_path), None, auto_exec, new_key, None, None, mode, times)
            if not os.access(file_path, os.R_OK):
                return ("error", lang_text["file_no_perm"].format(file_path), None, auto_exec, new_key, None, None, mode, times)
            if os.path.getsize(file_path) > 1024 * 200:
                return ("error", lang_text["file_too_large"].format(file_path), None, auto_exec, new_key, None, None, mode, times)
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    file_content = f.read().strip()
                file_prefix = "[File]" if get_current_lang() == "english" else "[文件]"
                content = f"{file_prefix}{os.path.basename(file_path)}\n{file_content[:4000]}..."
                content_type = "file"
                extra_info = file_path
                i += 2
            except Exception as e:
                return ("error", lang_text["file_read_fail"].format(str(e)[:20]), None, auto_exec, new_key, None, None, mode, times)
        elif arg == "-key":
            if i + 1 >= len(ai_args):
                return ("error", lang_text["key_format_error"], None, auto_exec, new_key, None, None, mode, times)
            new_key = ai_args[i+1].strip()
            if len(new_key) != 32:
                return ("error", lang_text["key_format_error"], None, auto_exec, new_key, None, None, mode, times)
            i += 2
        elif arg in ("new", "switch", "list"):
            chat_action = arg
            if arg in ("new", "switch"):
                if i + 1 >= len(ai_args):
                    return ("chat_only", f"Missing name for {arg}", None, auto_exec, new_key, arg, None, mode, times)
                chat_param = ai_args[i + 1]
                i += 2
            else:
                i += 1
        elif arg == "mcp":
            if i + 1 >= len(ai_args):
                return ("mcp_command", "list", [], auto_exec, new_key, None, None, mode, times)
            mcp_sub = ai_args[i + 1].lower()
            mcp_args = ai_args[i + 2:] if i + 2 < len(ai_args) else []
            if mcp_sub in ("install", "remove", "list", "start"):
                return ("mcp_command", mcp_sub, mcp_args, auto_exec, new_key, None, None, mode, times)
            return ("error", f"Invalid mcp subcommand: {mcp_sub}. Use install/list/remove | 无效 mcp 子命令: {mcp_sub}。使用 install/list/remove", None, auto_exec, new_key, None, None, mode, times)
        elif arg in ("plan", "normal"):
            mode = arg
            mode_explicitly_set = True
            i += 1
        elif arg == "--debug":
            # --debug 是纯修饰符，不消耗问题参数。
            # 仅 --debug 无问题时 → 应进入交互 REPL（由下方 interactive 分支处理）
            debug_requested = True
            i += 1
        elif arg.startswith("-"):
            i += 1
        else:
            question_parts = []
            while i < len(ai_args) and not ai_args[i].startswith("-"):
                question_parts.append(ai_args[i])
                i += 1
            if question_parts:
                content = " ".join(question_parts)
                content_type = "direct"
            else:
                i += 1

    if chat_action is not None:
        return ("chat_only", "", None, auto_exec, new_key, chat_action, chat_param, mode, times)
    if new_key is not None and not content:
        return ("key_only", "", None, auto_exec, new_key, None, None, mode, times)
    if not content and new_key is None and not mode_explicitly_set and not debug_requested:
        return ("error", lang_text["param_error"], None, auto_exec, new_key, None, None, mode, times)
    # 显式指定模式或 --debug 且无内容时 → 进入交互 REPL
    if not content and (mode_explicitly_set or debug_requested):
        return ("interactive", "", None, auto_exec, new_key, None, None, mode, times)
    return (content_type, content, extra_info, auto_exec, new_key, None, None, mode, times)


def show_loading(loading_flag: List[bool], lang_text: Dict[str, str]) -> None:
    symbols = ["◐", "◓", "◑", "◒"]
    idx = 0
    while loading_flag[0]:
        sys.stdout.write(f"\r{symbols[idx%4]} {lang_text['loading']}")
        sys.stdout.flush()
        idx += 1
        threading.Event().wait(0.1)  # 动画节流（功能性），可中断
    sys.stdout.write("\r" + " " * 30 + "\r")
    sys.stdout.flush()


# ── 危险命令 ──

def init_ai_dangerous_commands(home_dir: str, log_info_func=None) -> None:
    danger_dir = os.path.join(home_dir, ".config", "onyx", "ai_danger")
    if not os.path.exists(danger_dir):
        os.makedirs(danger_dir, mode=0o755)
        default_cmds = [
            "rm", "rmdir", "del", "rd",
            "format", "mkfs", "fdisk",
            "dd", "shred", "wipe",
            "shutdown", "reboot", "halt",
            "kill", "pkill", "killall",
            "chmod", "chown", "chattr",
            "mv", "cp", "dd",
            "sudo", "su", "passwd",
            # 2026-09 加固：sado 可临时提升模式/工具权限，必须人工确认
            "sado", "nanosado",
        ]
        default_file = os.path.join(danger_dir, "dangerous_commands.txt")
        with open(default_file, "w", encoding="utf-8") as f:
            f.write("\n".join(default_cmds))
        if log_info_func:
            log_info_func(f"AI dangerous commands config initialized: {default_file}", str(uuid.uuid4()))


# ── 特别高危命令清单（独立文件）：任何上下文下都强制用户确认，绝不自动放行 ──
_EXTRA_DANGEROUS_FILE = "extra_dangerous_commands.txt"

_EXTRA_DANGEROUS_DEFAULT = [
    # 组合型：必须整条匹配（rm -rf 等，避免误伤普通 rm file）
    "rm -rf", "rm -fr", "rm -r -f", "rm -f -r", "rm -rf /",
    "rm -r /", "rmdir /", "rd /",
    # 2026-09 加固：长参数形式（token 化检测看不到 -rf）
    "rm --recursive", "rm --force --recursive", "rm --recursive --force",
    "rm --no-preserve-root", "rm -rf --no-preserve-root", "rm -Rf",
    "sudo rm", "sudo dd", "sudo mkfs", "sudo fdisk",
    "mkfs.ext4", "mkfs.ext3", "mkfs.xfs", "mkfs.vfat", "mkfs.ntfs",
    "dd if=/dev/zero", "dd if=/dev/random", "dd of=/dev/sda", "dd of=/dev/sdb",
    "chmod -R 777", "chmod -R 000", "chown -R",
    "shutdown -h now", "shutdown -r now", "reboot -f", "poweroff",
    "init 0", "init 6", "halt -f",
    "format c:", "format d:",
    "> /dev/sda", "> /dev/sdb",
    "mv / /", "mv /* /",
    # 2026-09 加固：AI 记忆/密钥文件（H1/H2）——任何触碰这些路径的命令都必须人工确认
    ".ai_s/", "onyx_ai.md",
    ".config/onyx/ai/", "key.conf", "key.json",
]


def init_ai_extra_dangerous_commands(home_dir: str, log_info_func=None) -> None:
    """初始化特别高危命令清单文件（不存在时创建默认值）"""
    danger_dir = os.path.join(home_dir, ".config", "onyx", "ai_danger")
    if not os.path.exists(danger_dir):
        os.makedirs(danger_dir, mode=0o755)
    extra_file = os.path.join(danger_dir, _EXTRA_DANGEROUS_FILE)
    if not os.path.exists(extra_file):
        with open(extra_file, "w", encoding="utf-8") as f:
            f.write("\n".join(_EXTRA_DANGEROUS_DEFAULT))
        if log_info_func:
            log_info_func(f"AI extra dangerous commands config initialized: {extra_file}", str(uuid.uuid4()))


def load_ai_extra_dangerous_commands(home_dir: str, log_info_func=None) -> list:
    """加载特别高危命令模式清单，返回字符串列表（保留顺序，长模式优先匹配）。

    2026-09 加固：内置默认值始终合并进结果（已存在的用户文件可能缺少新增模式，
    如 .ai_s/ 路径保护），而不是仅读磁盘文件。
    """
    danger_dir = os.path.join(home_dir, ".config", "onyx", "ai_danger")
    extra_file = os.path.join(danger_dir, _EXTRA_DANGEROUS_FILE)
    if not os.path.exists(extra_file):
        init_ai_extra_dangerous_commands(home_dir, log_info_func)
    patterns = list(_EXTRA_DANGEROUS_DEFAULT)  # 内置默认始终生效
    try:
        with open(extra_file, "r", encoding="utf-8") as f:
            for line in f:
                p = line.strip().lower()
                if p and not p.startswith("#") and p not in patterns:
                    patterns.append(p)
    except Exception as e:
        if log_info_func:
            log_info_func(f"Failed to load extra dangerous commands file {extra_file}: {str(e)}", str(uuid.uuid4()))
    # 长模式优先（rm -rf 先于 rm 匹配）
    patterns.sort(key=len, reverse=True)
    return patterns


def is_extra_dangerous_command(cmd_str: str, extra_patterns: list) -> Tuple[bool, str]:
    """检测特别高危命令（模式子串匹配，长模式优先）。

    返回 (是否高危, 命中的模式)。普通危险命令（单 token）不在此列——
    特别高危清单只收录「组合型/毁灭型」命令，任何上下文下都强制确认。
    """
    if not cmd_str or not extra_patterns:
        return False, ""
    try:
        norm = " ".join(cmd_str.strip().lower().split())
        if not norm:
            return False, ""
        for pattern in extra_patterns:
            if pattern in norm:
                return True, pattern
        return False, ""
    except Exception:
        return False, ""


def load_ai_dangerous_commands(home_dir: str, log_info_func=None) -> set:
    danger_dir = os.path.join(home_dir, ".config", "onyx", "ai_danger")
    dangerous_commands = set()
    if not os.path.exists(danger_dir):
        init_ai_dangerous_commands(home_dir, log_info_func)
    for filename in os.listdir(danger_dir):
        if filename.endswith(".txt"):
            filepath = os.path.join(danger_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        cmd = line.strip().lower()
                        if cmd and not cmd.startswith("#"):
                            dangerous_commands.add(cmd)
            except Exception as e:
                if log_info_func:
                    log_info_func(f"Failed to load dangerous commands file {filename}: {str(e)}", str(uuid.uuid4()))
    # 2026-09 加固：内置默认 token 始终合并（已存在的用户文件可能缺少新增项，如 sado）
    for _default_cmd in [
        "rm", "rmdir", "del", "rd",
        "format", "mkfs", "fdisk",
        "dd", "shred", "wipe",
        "shutdown", "reboot", "halt",
        "kill", "pkill", "killall",
        "chmod", "chown", "chattr",
        "mv", "cp", "dd",
        "sudo", "su", "passwd",
        "sado", "nanosado",
    ]:
        dangerous_commands.add(_default_cmd)
    return dangerous_commands


def is_dangerous_command(cmd_str: str, dangerous_commands: set) -> Tuple[bool, str]:
    if not cmd_str or not cmd_str.strip():
        return False, ""
    try:
        clean_cmd = re.sub(r'[^a-zA-Z0-9]', ' ', cmd_str)
        cmd_parts_check = [part for part in clean_cmd.split() if part.strip()]
        if not cmd_parts_check:
            return False, ""
        for part in cmd_parts_check:
            part_lower = part.lower()
            if part_lower in dangerous_commands:
                return True, part_lower
        return False, ""
    except Exception:
        return False, ""


# ── 危险命令上下文分级阈值（token 数，2026-09 恢复信任区间）──
_CTX_TRUST_BELOW = 300_000        # 上下文 < 300k：完全信任 AI，不弹任何危险提示
_CTX_TIMEOUT_BELOW = 600_000      # 300k ≤ 上下文 ≤ 600k：弹窗，10s 无操作默认放行
_CTX_FORCE_ABOVE = 600_000        # 上下文 > 600k：强制用户回答（无超时）
_CONFIRM_TIMEOUT_SECONDS = 10


def confirm_dangerous_command(cmd_str: str, cmd_name: str, lang_text: dict,
                              session_id: str, initial_question: str,
                              interaction_count: int, log_info: Callable = None,
                              context_tokens: int = 0,
                              extra_dangerous: bool = False) -> Tuple[bool, str, str]:
    """危险命令确认：上下文三级分级（2026-09 用户恢复信任区间，统一适用所有危险命令）。

    规则：
      - 上下文 < 300k：完全不弹危险提示，直接放行（信任 AI；硬安全由沙盒边界提供）
      - 300k ≤ 上下文 ≤ 600k：弹窗询问，10 秒无操作默认放行（用户可能离开）
      - 上下文 > 600k：强制用户回答（无超时，必须 y/n，不自动放行）
      - 上下文估算失败（≤0）：按 >600k 强制确认处理（安全方向）
      - extra_dangerous 参数保留（调用方兼容）；特别高危清单与普通危险命令同走三级

    返回 (是否放行, 用户响应, 拒绝原因)。
    """
    MAX_REFUSE_REASON_LEN = 500
    current_lang = get_current_lang()

    # 上下文估算失败（调用方异常回退 0）→ 保守按强制确认档处理
    if context_tokens <= 0:
        context_tokens = _CTX_FORCE_ABOVE + 1

    # ── 第一级：上下文 < 300k → 完全信任，不弹任何提示 ──
    if context_tokens < _CTX_TRUST_BELOW:
        if log_info:
            log_info(f"AI dangerous command auto-allowed (ctx {context_tokens // 1000}k trust zone): {cmd_str}", session_id)
        return True, "auto", ""

    # ── 第二级：300k ≤ 上下文 ≤ 600k → 弹窗询问，10s 无操作默认放行 ──
    if context_tokens <= _CTX_TIMEOUT_BELOW:
        if log_info:
            log_info(f"AI dangerous command soft-confirm (ctx {context_tokens // 1000}k): {cmd_str}", session_id)
        console.print(
            (f"[yellow]⚠️ 上下文 {context_tokens // 1000}k：危险命令需确认，10 秒无操作默认执行[/]"
             if current_lang == "chinese"
             else f"[yellow]⚠️ Context {context_tokens // 1000}k: confirm required, auto-executes after 10s[/]")
        )
        # 确认框全程走真实终端（防 stdout 被捕获流替换导致框不可见）
        with real_terminal_io():
            confirmed, user_resp, refuse_reason = ui_confirm_dangerous(
                title=lang_text["danger_cmd_title"],
                command=f"{lang_text['danger_cmd_display']}: {cmd_str}",
                reason=lang_text['danger_cmd_msg'].format(cmd_name),
                timeout=_CONFIRM_TIMEOUT_SECONDS,
                timeout_default=True,   # 10s 无操作默认放行（用户可能离开）
            )
        if user_resp == "timeout":
            refuse_reason = (
                "确认超时（默认执行）" if current_lang == "chinese"
                else "Confirmation timeout (auto-executed)"
            )
    else:
        # ── 第三级：上下文 > 600k → 强制用户回答（无超时，不自动放行）──
        if log_info:
            log_info(f"AI dangerous command force-confirm (ctx {context_tokens // 1000}k): {cmd_str}", session_id)
        console.print(
            (f"[red]⚠️ 上下文 {context_tokens // 1000}k：必须人工确认，不会自动放行[/]"
             if current_lang == "chinese"
             else f"[red]⚠️ Context {context_tokens // 1000}k: manual confirmation required[/]")
        )
        # 确认框全程走真实终端（防 stdout 被捕获流替换导致框不可见）
        with real_terminal_io():
            confirmed, user_resp, refuse_reason = ui_confirm_dangerous(
                title=lang_text["danger_cmd_title"],
                command=f"{lang_text['danger_cmd_display']}: {cmd_str}",
                reason=lang_text['danger_cmd_msg'].format(cmd_name),
                timeout=None,           # 一直等待，强制用户回答
                timeout_default=False,
            )

    # ── 正常弹窗结果处理（用户真实选择 / 超时放行 / 超时拒绝）──
    if confirmed:
        console.print(lang_text["danger_cmd_executing"], style="bold green")
        if log_info:
            log_info(f"AI dangerous command confirmed: {cmd_str}", session_id)
        return True, "y", ""
    else:
        console.print(lang_text["danger_cmd_cancelled"], style="bold red")
        if log_info:
            log_info(f"AI dangerous command cancelled: {cmd_str}", session_id)
        if len(refuse_reason) > MAX_REFUSE_REASON_LEN:
            refuse_reason = refuse_reason[:MAX_REFUSE_REASON_LEN] + (
                "...(truncated)" if current_lang == "english" else "...(截断)"
            )
        if refuse_reason:
            console.print(lang_text["danger_cmd_reason_recorded"], style="bold green")
        return False, "n", refuse_reason


def has_forbidden_syntax(cmd: str) -> bool:
    """检测命令是否包含 adv_code 模式禁止的语法"""
    forbidden_patterns = ['|', '>', '<', '<<', '>>', '&>', '|&', '<<-']
    for pattern in forbidden_patterns:
        if pattern in cmd:
            return True
    return False
