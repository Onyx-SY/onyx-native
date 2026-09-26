"""core/handlers/builtins.py — 内置命令处理函数（从 Onyx.py 提取）"""

import os
import sys as _sys
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext


def handle_exit(cmd_parts: List[str], request_id: str) -> None:
    """退出程序"""
    from core.context import get_ctx
    from core.log_manager import log_info
    ctx = get_ctx()
    log_info("程序退出", request_id)
    if ctx.log_file_handler:
        ctx.log_file_handler.close()
    if ctx.executor:
        ctx.executor.shutdown()
    from Onyx import graceful_shutdown
    graceful_shutdown(request_id)
    _sys.exit(0)
























def handle_mktool(cmd_parts: List[str], request_id: str) -> None:
    """mktool 命令（委托 bin/mktool_cmd）"""
    from core.context import get_ctx
    from bin.mktool_cmd import handle_mktool_core
    from core.security import check_sandbox_path
    ctx = get_ctx()
    handle_mktool_core(
        cmd_parts=cmd_parts, request_id=request_id,
        ROOT_DIR=ctx.ROOT_DIR, USER_HOME_DIR=ctx.USER_HOME_DIR,
        SYS_SPECIFIC=ctx.global_config.get("sys_specific", {}),
        check_sandbox_path=lambda p, r: check_sandbox_path(ctx, p, r),
        get_virtual_path=lambda p: _get_vp(ctx, p),
        log_info=_log_info, log_error=_log_error,
        Fore=ctx.Fore, Style=ctx.Style,
    )


def handle_import(cmd_parts: List[str], request_id: str) -> None:
    """import 命令（委托 bin/import_cmd）"""
    from core.context import get_ctx
    from bin.import_cmd import handle_import_core
    ctx = get_ctx()
    handle_import_core(
        cmd_parts=cmd_parts, request_id=request_id,
        USER_HOME_DIR=ctx.USER_HOME_DIR, current_sys_cmds=ctx.current_sys_cmds,
        sys_type=ctx.sys_type, BUILTIN_COMMANDS=ctx.BUILTIN_COMMANDS,
        CMD_MAPPING_CACHE=ctx.CMD_MAPPING_CACHE, TOOL_INDEX_CACHE=ctx.TOOL_INDEX_CACHE,
        build_cmd_mapping_cache=lambda rid: build_cmd_mapping_cache(ctx, rid),
        log_info=_log_info, log_error=_log_error,
        Fore=ctx.Fore, Style=ctx.Style,
    )


def handle_sado(cmd_parts: List[str], request_id: str) -> None:
    """sado 命令（委托 bin/sado_cmd）"""
    from core.context import get_ctx
    from bin.sado_cmd import handle_sado_core
    ctx = get_ctx()
    handle_sado_core(
        cmd_parts=cmd_parts, request_id=request_id,
        user_mode=ctx.user_mode, global_config=ctx.global_config,
        SADO_CONFIG=ctx.SADO_CONFIG, SADO_CONFIG_PATH=ctx.SADO_CONFIG_PATH,
        user_info=ctx.user_info, OS_OR_TBS=ctx.OS_OR_TBS, sys_type=ctx.sys_type,
        parse_and_execute=lambda c, ir=False, ia=False: _parse_exec(ctx, c, ir, ia),
        alias_cache={},  # 别名系统已移除
        log_info=_log_info, log_error=_log_error,
        get_current_lang=lambda: ctx.global_config["display_info"]["language"]["current"],
        Fore=ctx.Fore, Style=ctx.Style,
    )


def handle_nanosado(cmd_parts: List[str], request_id: str) -> None:
    """nanosado 命令（委托 bin/nanosado_cmd）"""
    from core.context import get_ctx
    from bin.nanosado_cmd import handle_nanosado_core
    ctx = get_ctx()
    handle_nanosado_core(
        cmd_parts=cmd_parts, request_id=request_id,
        user_mode=ctx.user_mode, OS_OR_TBS=ctx.OS_OR_TBS, sys_type=ctx.sys_type,
        SADO_CONFIG=ctx.SADO_CONFIG, SADO_CONFIG_PATH=ctx.SADO_CONFIG_PATH,
        user_info=ctx.user_info,
        get_current_lang=lambda: ctx.global_config["display_info"]["language"]["current"],
        log_info=_log_info, log_error=_log_error,
        Fore=ctx.Fore, Style=ctx.Style,
    )


def handle_activite(cmd_parts: List[str], request_id: str) -> None:
    """activite 命令（委托 bin/activite_cmd）"""
    from core.context import get_ctx
    from bin.activite_cmd import handle_activite_core
    from core.security import set_tool_permission
    from core.tool_registry import find_tool
    ctx = get_ctx()
    handle_activite_core(
        cmd_parts=cmd_parts, request_id=request_id,
        user_mode=ctx.user_mode, user_info=ctx.user_info,
        global_config=ctx.global_config, ROOT_DIR=ctx.ROOT_DIR,
        SANDBOX_CONFIG=ctx.SANDBOX_CONFIG,
        get_virtual_path=lambda p: _get_vp(ctx, p),
        set_tool_permission=lambda td, p, rid: set_tool_permission(ctx, td, p, rid),
        find_tool=lambda tn, rid: find_tool(ctx, tn, rid),
        verify_admin_password=lambda pw: _verify_admin(ctx, pw),
        ADMIN_PASSWORD_PATH=ctx.ADMIN_PASSWORD_PATH,
        get_current_lang=lambda: ctx.global_config["display_info"]["language"]["current"],
        log_info=_log_info, log_error=_log_error,
        Fore=ctx.Fore, Style=ctx.Style,
    )


def handle_ai(cmd_parts: List[str], request_id: str) -> None:
    """AI 命令（委托 bin/ai_cmd 或 bin/ai_interactive）"""
    from core.context import get_ctx
    ctx = get_ctx()
    # ── 沙盒会话边界：每次 ai 命令启动重置沙盒，下次 handle_ai init 时
    #    重新固定到启动时的 cwd（会话内不随 cd 漂移）──
    try:
        from bin.ai_lib import sandbox as _sandbox
        _sandbox.deactivate()
    except Exception:
        pass
    from bin.ai_lib.config import ensure_api_key_configured
    if not ensure_api_key_configured(ctx.global_config["display_info"]["language"]["current"]):
        return

    is_bare = len(cmd_parts) == 1
    # ── AI 会话 cwd 守卫：会话结束（含异常）自动恢复 cwd + 停用 AI 沙盒 ──
    from bin.ai_lib import sandbox as _ai_sandbox
    with _ai_sandbox.session_guard():
        if not is_bare:
            from bin.ai_cmd import handle_ai as _handle_ai
            _handle_ai(
                cmd_parts=cmd_parts, request_id=request_id,
                onyx_module=_sys.modules.get("Onyx", _sys.modules[__name__]),
                user_home_dir=ctx.USER_HOME_DIR, global_config=ctx.global_config,
                user_info=ctx.user_info, user_mode=ctx.user_mode,
                AI_TOOL_OUTPUT_CACHE=ctx.AI_TOOL_OUTPUT_CACHE,
                BUILTIN_COMMANDS=ctx.BUILTIN_COMMANDS, CMD_MAPPING_CACHE=ctx.CMD_MAPPING_CACHE,
                current_sys_cmds=ctx.current_sys_cmds, sys_type=ctx.sys_type,
                get_cached_cmd=lambda c: _gc(ctx, c),
                parse_and_execute=lambda c, ir=False, ia=False: _parse_exec(ctx, c, ir, ia),
                get_current_lang_func=lambda: ctx.global_config["display_info"]["language"]["current"],
                log_info=_log_info, log_error=_log_error, log_warning=_log_warning,
                security_log=_security_log,
            )
        else:
            from bin.ai_interactive import ai_interactive_session
            ai_interactive_session(
                user_home_dir=ctx.USER_HOME_DIR,
                onyx_module=_sys.modules.get("Onyx", _sys.modules[__name__]),
                global_config=ctx.global_config, user_info=ctx.user_info,
                user_mode=ctx.user_mode,
                parse_and_execute=lambda c, ir=False, ia=False: _parse_exec(ctx, c, ir, ia),
                AI_TOOL_OUTPUT_CACHE=ctx.AI_TOOL_OUTPUT_CACHE,
                BUILTIN_COMMANDS=ctx.BUILTIN_COMMANDS, CMD_MAPPING_CACHE=ctx.CMD_MAPPING_CACHE,
                current_sys_cmds=ctx.current_sys_cmds, sys_type=ctx.sys_type,
                get_cached_cmd=lambda c: _gc(ctx, c),
                get_current_lang_func=lambda: ctx.global_config["display_info"]["language"]["current"],
                log_info=_log_info, log_error=_log_error, log_warning=_log_warning,
                security_log=_security_log,
            )


def handle_autocmd(cmd_parts: List[str], request_id: str) -> None:
    """autocmd 命令"""
    from core.context import get_ctx
    from core.log_manager import log_info
    ctx = get_ctx()
    log_info(f"autocmd invoked: {cmd_parts}", request_id)
    # 委托原有实现
    from Onyx import handle_autocmd as _orig
    _orig(cmd_parts, request_id)


def handle_switch_prompt(cmd_parts: List[str], request_id: str) -> None:
    """switch-prompt 命令"""
    from core.context import get_ctx
    ctx = get_ctx()
    from Onyx import handle_switch_prompt as _orig
    _orig(cmd_parts, request_id)


def handle_set_adv_pwd(cmd_parts: List[str], request_id: str) -> None:
    """set-adv-pwd 命令"""
    from core.context import get_ctx
    ctx = get_ctx()
    from Onyx import handle_set_adv_pwd as _orig
    _orig(cmd_parts, request_id)


# ---- 内部辅助 ----

def _resolve(ctx, path):
    from core.path_ops import resolve_path
    return resolve_path(ctx, path)


def _get_vp(ctx, path):
    from core.path_ops import get_virtual_path
    return get_virtual_path(ctx, path)


def _check_sp(ctx, path, rid):
    from core.security import check_sandbox_path
    return check_sandbox_path(ctx, path, rid)


def _vpp(ctx, param, rid):
    from core.path_ops import validate_param_path
    from core.security import check_sandbox_path
    from core.log_manager import log_info, log_warning
    return validate_param_path(ctx, param, rid,
                               check_sandbox_path_fn=lambda p, r: check_sandbox_path(ctx, p, r),
                               log_info_fn=log_info, log_warning_fn=log_warning)


def _run_cmd_sync(cmd, rid, is_tool=False, tool_perm=3):
    from lib.terminal.exe import run_cmd_sync
    run_cmd_sync(cmd, rid, is_tool=is_tool, tool_perm=tool_perm)


def _log_info(msg, rid):
    from core.log_manager import log_info
    log_info(msg, rid)


def _log_error(msg, rid):
    from core.log_manager import log_error
    log_error(msg, rid)


def _log_warning(msg, rid):
    from core.log_manager import log_warning
    log_warning(msg, rid)


def _security_log(msg, etype, rid):
    from core.log_manager import security_log
    security_log(msg, etype, rid)


def _parse_exec(ctx, cmd, is_recursive=False, is_ai_triggered=False):
    from Onyx import parse_and_execute as _orig
    _orig(cmd, is_recursive=is_recursive, is_ai_triggered=is_ai_triggered)


def _gc(ctx, cmd):
    from Onyx import get_cached_cmd as _orig
    return _orig(cmd)


def _verify_admin(ctx, password):
    from Onyx import verify_admin_password as _orig
    return _orig(password)
