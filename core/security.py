"""core/security.py — 安全检查：高危命令拦截、沙箱路径校验、工具权限"""

import os
import uuid
from typing import Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext


def check_sandbox_path(ctx: "AppContext", path: str, request_id: str) -> bool:
    """沙箱路径校验：确保路径在允许范围内"""
    from core.i18n import t, set_lang
    set_lang(ctx.global_config["display_info"]["language"]["current"])

    if not ctx._SANDBOX_ENABLED:
        return True

    if not ctx.SANDBOX_CONFIG["enable"]:
        return True

    if ctx.OS_OR_TBS == "OS":
        return True

    try:
        physical_path = os.path.realpath(path)
        abs_path = os.path.abspath(physical_path)
        root = os.path.abspath(ctx.ROOT_DIR)
        if abs_path == root or abs_path.startswith(root + os.sep):
            return True

        err_msg = t("security.check_sandbox_path.path_block")
        print(ctx.Fore.RED + err_msg + ctx.Style.RESET_ALL)
        # Deferred import to avoid circular dependency
        from core.log_manager import log_error, security_log
        log_error(err_msg, request_id)
        security_log(err_msg, "PATH_BLOCK", request_id)
        return False
    except Exception as e:
        err_msg = t("security.check_sandbox_path.path_error", err=str(e))
        print(ctx.Fore.RED + err_msg + ctx.Style.RESET_ALL)
        from core.log_manager import log_error, security_log
        log_error(err_msg, request_id)
        security_log(err_msg, "PATH_ERROR", request_id)
        return False


def check_tool_permission(ctx: "AppContext", required_perm: int) -> bool:
    """检查当前模式是否有权限使用指定等级的工具"""
    from core.i18n import t, set_lang
    set_lang(ctx.global_config["display_info"]["language"]["current"])
    um = ctx.user_mode

    mode_perm_limit = ctx.global_config["mode_config"]["perm_limit"][um.current_mode]["max_tool_perm"]
    if required_perm > mode_perm_limit:
        print(ctx.Fore.RED + t("security.check_tool_permission.perm_exceed", mode=um.current_mode, limit=mode_perm_limit, req=required_perm) + ctx.Style.RESET_ALL)
        return False

    if um.current_mode == "adv":
        return um.current_tool_perm >= required_perm
    else:
        if required_perm >= 4:
            print(ctx.Fore.RED + t("security.check_tool_permission.non_adv_high_perm", req=required_perm) + ctx.Style.RESET_ALL)
            return False
        return um.current_tool_perm >= required_perm


def _get_sys_specific(ctx: "AppContext"):
    """获取系统特定配置"""
    return ctx.global_config.get("sys_specific", {})


def get_tool_permission(ctx: "AppContext", tool_dir: str) -> int:
    """读取工具权限等级"""
    from core.log_manager import log_info, log_error, log_warning
    ss = _get_sys_specific(ctx)
    perm_file = os.path.join(tool_dir, ss.get("perm_file", ".perm"))
    if not os.path.exists(perm_file):
        try:
            with open(perm_file, "w", encoding="utf-8") as f:
                f.write("3")
            if ctx.sys_type in ["Linux/macOS", "Termux", "SpecialLinux"]:
                os.chmod(perm_file, 0o644)
            log_info(f"工具权限文件创建：{perm_file}（默认权限3）", str(uuid.uuid4()))
            return 3
        except Exception as e:
            log_error(f"工具权限文件创建失败：{str(e)}", str(uuid.uuid4()))
            return 3

    try:
        with open(perm_file, "r", encoding="utf-8") as f:
            perm = int(f.read().strip())
        if perm < 1 or perm > 5:
            log_warning(f"工具权限非法（{perm}），默认使用3级", str(uuid.uuid4()))
            return 3
        return perm
    except Exception as e:
        log_error(f"工具权限读取失败：{str(e)}", str(uuid.uuid4()))
        return 3


def set_tool_permission(ctx: "AppContext", tool_dir: str, perm: int, request_id: str) -> bool:
    """设置工具权限等级（仅 adv 模式）"""
    from core.log_manager import log_info, log_error
    um = ctx.user_mode
    if um.current_mode != "adv":
        err_msg = "仅adv模式可修改工具权限"
        print(ctx.Fore.RED + err_msg + ctx.Style.RESET_ALL)
        log_error(err_msg, request_id)
        return False

    if perm < 1 or perm > 5:
        err_msg = "工具权限必须为1-5的整数"
        print(ctx.Fore.RED + err_msg + ctx.Style.RESET_ALL)
        log_error(err_msg, request_id)
        return False

    mode_perm_limit = ctx.global_config["mode_config"]["perm_limit"][um.current_mode]["max_tool_perm"]
    if perm > mode_perm_limit:
        err_msg = f"adv模式权限上限{mode_perm_limit}，无法设置{perm}级权限"
        print(ctx.Fore.RED + err_msg + ctx.Style.RESET_ALL)
        log_error(err_msg, request_id)
        return False

    ss = _get_sys_specific(ctx)
    perm_file = os.path.join(tool_dir, ss.get("perm_file", ".perm"))
    try:
        with open(perm_file, "w", encoding="utf-8") as f:
            f.write(str(perm))
        log_info(f"工具权限设置完成：{tool_dir} → {perm}级", request_id)
        print(ctx.Fore.GREEN + f"{os.path.basename(tool_dir)} → {perm}级" + ctx.Style.RESET_ALL)
        return True
    except Exception as e:
        err_msg = f"工具权限设置失败：{str(e)}"
        print(ctx.Fore.RED + err_msg + ctx.Style.RESET_ALL)
        log_error(err_msg, request_id)
        return False


def check_blocked_cmd(ctx: "AppContext", cmd: str, request_id: str) -> Tuple[bool, bool]:
    """高危命令拦截。返回 (是否拦截, 是否需要二次确认)"""
    if not ctx._SANDBOX_ENABLED:
        return False, False
    if not ctx.SANDBOX_CONFIG["enable"]:
        return False, False
    if ctx.OS_OR_TBS == "OS":
        return False, False

    from core.i18n import t, set_lang
    set_lang(ctx.global_config["display_info"]["language"]["current"])

    # 读取高危命令配置
    blocked_cmds = []
    cmd_config_path = os.path.join(ctx.ROOT_DIR, "onyx", "etc", "dan_cmd")
    try:
        if os.path.exists(cmd_config_path):
            with open(cmd_config_path, "r", encoding="utf-8") as f:
                blocked_cmds = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        if not blocked_cmds:
            blocked_cmds = [
                "rm -rf /", "rm -r /", "rm -rf *", "rm -r *", "rm -f /", "rm /",
                "sudo rm -rf /", "sudo rm -r /", "mkfs", "fdisk", "format",
                "rm -rf /home", "rm -rf /etc", "dd if=/dev/zero of=/dev/sda",
                "shutdown -h now", "reboot -f", "init 0", "mv / /tmp", "chmod -R 777 /"
            ]
    except Exception as e:
        from core.log_manager import log_warning
        log_warning(t("security.check_blocked_cmd.config_load_fail") + f" → {str(e)}", request_id)
        blocked_cmds = [
            "rm -rf /", "rm -r /", "rm -rf *", "rm -r *", "rm -f /", "rm /",
            "sudo rm -rf /", "sudo rm -r /", "mkfs", "fdisk", "format",
            "rm -rf /home", "rm -rf /etc", "dd if=/dev/zero of=/dev/sda",
            "shutdown -h now", "reboot -f", "init 0", "mv / /tmp", "chmod -R 777 /"
        ]

    # 规范化命令
    re_multi = ctx._RE_MULTI_SPACE
    re_opt_slash = ctx._RE_OPT_SLASH
    re_opt_star = ctx._RE_OPT_STAR

    cmd_lower = cmd.strip().lower()
    cmd_normalized = re_multi.sub(' ', cmd_lower)
    cmd_normalized = re_opt_slash.sub(r'\1 \2', cmd_normalized)
    cmd_normalized = re_opt_star.sub(r'\1 \2', cmd_normalized)

    for blocked in blocked_cmds:
        blocked_normalized = re_multi.sub(' ', blocked.strip().lower())
        blocked_normalized = re_opt_slash.sub(r'\1 \2', blocked_normalized)
        blocked_normalized = re_opt_star.sub(r'\1 \2', blocked_normalized)
        if cmd_normalized == blocked_normalized:
            return _handle_blocked_match(ctx, cmd, blocked, request_id, "base_block", "CMD_BLOCK", "CMD_CONFIRM", "CMD_CANCEL")

    # SpecialLinux 专属规则
    if ctx.sys_type == "SpecialLinux":
        for blocked in ctx.SANDBOX_CONFIG.get("special_blocked_cmds", []):
            blocked_normalized = re_multi.sub(' ', blocked.strip().lower())
            blocked_normalized = re_opt_slash.sub(r'\1 \2', blocked_normalized)
            blocked_normalized = re_opt_star.sub(r'\1 \2', blocked_normalized)
            if cmd_normalized == blocked_normalized:
                return _handle_blocked_match(ctx, cmd, blocked, request_id, "special_block", "CMD_BLOCK", "CMD_CONFIRM", "CMD_CANCEL")

    return False, False


def _handle_blocked_match(ctx, cmd, blocked, request_id, block_key, block_type, confirm_type, cancel_type):
    """处理高危命令匹配：adv 模式二次确认，普通模式直接拦截"""
    from core.log_manager import log_error, log_warning, security_log
    from core.i18n import t
    section = f"security.check_blocked_cmd.{block_key}"
    if ctx.user_mode and ctx.user_mode.current_mode == "adv":
        print(ctx.Fore.RED + t(section, cmd=cmd, rule=blocked) + ctx.Style.RESET_ALL)
        log_warning(f"ADV模式高危命令需确认：{cmd}", request_id)
        import secrets as _s
        captcha = _s.token_hex(2).upper()
        print(ctx.Fore.YELLOW + t("security.check_blocked_cmd.captcha_prompt", code=captcha) + ctx.Style.RESET_ALL)
        try:
            confirm = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(ctx.Fore.RED + t("security.check_blocked_cmd.adv_cancelled") + ctx.Style.RESET_ALL)
            return True, False
        if confirm.upper() == captcha:
            security_log(f"ADV模式确认执行高危命令：{cmd}", confirm_type, request_id)
            return False, True
        else:
            print(ctx.Fore.RED + t("security.check_blocked_cmd.captcha_wrong") + ctx.Style.RESET_ALL)
            security_log(f"ADV模式取消高危命令：{cmd}", cancel_type, request_id)
            return True, False
    else:
        err_msg = t(section, cmd=cmd, rule=blocked)
        print(ctx.Fore.RED + err_msg + ctx.Style.RESET_ALL)
        log_error(err_msg, request_id)
        security_log(err_msg, block_type, request_id)
        return True, False


def kill_stale_processes(ctx: "AppContext") -> None:
    """清理僵尸进程"""
    if not ctx.SANDBOX_CONFIG["enable"]:
        return
    from lib.process_control import clear_stale_processes, get_running_processes
    from core.log_manager import log_info, log_error
    try:
        stale_count = clear_stale_processes()
        log_info(f"清理僵尸进程完成：共{stale_count}个", str(uuid.uuid4()))
        ctx.CURRENT_PROCESSES = get_running_processes()
    except Exception as e:
        log_error(f"清理僵尸进程失败：{str(e)}", str(uuid.uuid4()))
