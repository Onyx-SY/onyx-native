"""core/shutdown.py — 优雅关闭逻辑（从 Onyx.py 提取）"""

import os
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext


def graceful_shutdown(ctx: "AppContext", request_id: str = "") -> None:
    """优雅关闭：保存所有缓存 + 清理资源 + 恢复终端"""
    if not request_id:
        request_id = str(uuid.uuid4())

    _disable_logging(ctx)

    try:
        # 恢复终端属性（raw → cooked）
        from lib.terminal.exe import restore_terminal_attrs
        restore_terminal_attrs()
    except Exception:
        pass

    try:
        _save_persistent_data(ctx, request_id)

        # 保存路径缓存
        from Onyx import save_msgpack
        if save_msgpack(ctx.PATH_INDEX_MSG_PATH, ctx.PATH_RESOLVE_CACHE):
            from core.log_manager import log_info
            log_info(f"路径缓存保存完成：{ctx.PATH_INDEX_MSG_PATH}", request_id)
        if save_msgpack(ctx.DIR_CACHE_MSG_PATH, ctx.DIR_FILE_CACHE):
            from core.log_manager import log_info
            log_info(f"目录缓存保存完成：{ctx.DIR_CACHE_MSG_PATH}", request_id)

        if ctx.sys_type in ctx.CMD_MAPPING_CACHE:
            serializable = {ctx.sys_type: ctx.CMD_MAPPING_CACHE[ctx.sys_type]}
            if save_msgpack(ctx.CMD_MAPPING_MSG_PATH, serializable):
                from core.log_manager import log_info
                log_info(f"命令映射缓存保存完成：{ctx.CMD_MAPPING_MSG_PATH}", request_id)

        _terminate_running_processes(ctx, request_id)
        _shutdown_thread_pool(ctx)

    except Exception as e:
        print(ctx.Fore.RED + f"Error during shutdown: {str(e)}" + ctx.Style.RESET_ALL)


def _disable_logging(ctx: "AppContext") -> None:
    if ctx.log_file_handler and not ctx.log_file_handler.closed:
        try:
            ctx.log_file_handler.close()
        except Exception:
            pass


def _save_persistent_data(ctx: "AppContext", request_id: str) -> None:
    try:
        from Onyx import save_history_incremental, save_user_config
        save_history_incremental()
        save_user_config()
        from core.log_manager import log_info, log_error
        log_info("Persistent data saved", request_id)
    except Exception as e:
        from core.log_manager import log_error
        log_error(f"Failed to save persistent data: {str(e)}", request_id)


def _shutdown_thread_pool(ctx: "AppContext") -> None:
    if ctx.executor:
        try:
            ctx.executor.shutdown(wait=False, cancel_futures=True)
            ctx.executor = None
        except Exception:
            pass


def _terminate_running_processes(ctx: "AppContext", request_id: str) -> None:
    from core.log_manager import log_info, log_error
    with ctx.process_lock:
        if not ctx.CURRENT_PROCESSES:
            log_info("No processes to terminate", request_id)
            return
        terminated = 0
        failed = 0
        for pid, _, req_id, cmd in ctx.CURRENT_PROCESSES[:]:
            try:
                if _terminate_single_process(pid, cmd, request_id):
                    terminated += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        ctx.CURRENT_PROCESSES.clear()
        if failed == 0:
            log_info(f"Terminated {terminated} processes", request_id)
        else:
            log_info(f"Terminated {terminated} processes, {failed} failed", request_id)


def _terminate_single_process(pid: int, cmd: str, request_id: str) -> bool:
    from lib.process_control import kill_process, check_process_alive
    from core.log_manager import log_info, log_error
    try:
        if not check_process_alive(pid):
            return True
        success = kill_process(pid)
        if success:
            log_info(f"终止进程成功：PID={pid}, command={cmd}", request_id)
        else:
            log_error(f"终止进程失败：PID={pid}, command={cmd}", request_id)
        return success
    except Exception as e:
        log_error(f"终止进程异常：PID={pid}, error={str(e)}", request_id)
        return False
