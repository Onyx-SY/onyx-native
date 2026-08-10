"""core/log_manager.py — 日志子系统：初始化、分级写入、安全日志"""

import os
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext


def init_logger(ctx: "AppContext") -> None:
    """初始化日志系统"""
    username = ctx.user_info.get("name", "default")
    log_dir = os.path.join(ctx.ROOT_DIR, "onyxlog", "onyx")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, mode=0o755)
    ctx.LOG_DIR = log_dir
    ctx.LOG_TIMESTAMP = datetime.now().strftime("%Y%m%d%H%M%S")
    ctx.LOG_FILE_PATH = os.path.join(log_dir, f".onyx_main_{ctx.LOG_TIMESTAMP}.log")
    try:
        ctx.log_file_handler = open(ctx.LOG_FILE_PATH, "a", encoding="utf-8")
        if ctx.sys_type != "Windows":
            os.chmod(ctx.LOG_FILE_PATH, 0o600)
        _write_log(ctx, "INFO", f"Logger initialized — {ctx.LOG_FILE_PATH}", str(uuid.uuid4()))
    except Exception:
        ctx.log_file_handler = None


def _write_log(ctx: "AppContext", level: str, message: str, request_id: str) -> None:
    """写入日志"""
    if ctx.CURRENT_LOG_LEVEL > ctx.LOG_LEVELS.get(level, 1):
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] [{level}] [{request_id[:8]}] {message}\n"
    if ctx.log_file_handler and not ctx.log_file_handler.closed:
        try:
            ctx.log_file_handler.write(line)
            ctx.log_file_handler.flush()
        except Exception:
            pass


def log_info(message: str, request_id: str) -> None:
    """INFO 级别日志"""
    from core.context import get_ctx
    _write_log(get_ctx(), "INFO", message, request_id)


def log_error(message: str, request_id: str) -> None:
    """ERROR 级别日志"""
    from core.context import get_ctx
    _write_log(get_ctx(), "ERROR", message, request_id)


def log_warning(message: str, request_id: str) -> None:
    """WARNING 级别日志"""
    from core.context import get_ctx
    _write_log(get_ctx(), "WARNING", message, request_id)


def security_log(message: str, event_type: str, request_id: str) -> None:
    """安全事件日志"""
    from core.context import get_ctx
    ctx = get_ctx()
    log_path = ctx.SANDBOX_CONFIG.get("log_path", "")
    if not log_path:
        _write_log(ctx, "SECURITY", f"[{event_type}] {message}", request_id)
        return
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{event_type}] [{request_id[:8]}] {message}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        _write_log(ctx, "SECURITY", f"[{event_type}] {message}", request_id)


def check_log_rotation(ctx: "AppContext") -> None:
    """检查日志轮转（简单实现）"""
    if not ctx.LOG_FILE_PATH or not os.path.exists(ctx.LOG_FILE_PATH):
        return
    try:
        size_mb = os.path.getsize(ctx.LOG_FILE_PATH) / (1024 * 1024)
        if size_mb > 50:
            ctx.log_file_handler.close()
            ctx.LOG_TIMESTAMP = datetime.now().strftime("%Y%m%d%H%M%S")
            ctx.LOG_FILE_PATH = os.path.join(ctx.LOG_DIR, f".onyx_main_{ctx.LOG_TIMESTAMP}.log")
            ctx.log_file_handler = open(ctx.LOG_FILE_PATH, "a", encoding="utf-8")
    except Exception:
        pass


def get_log_context(ctx: "AppContext") -> str:
    """获取当前日志上下文摘要"""
    return f"session={ctx.user_info.get('session_id', '?')} user={ctx.user_info.get('name', '?')} sys={ctx.sys_type} mode={ctx.user_mode.current_mode if ctx.user_mode else '?'}"
