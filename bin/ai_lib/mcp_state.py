# -*- coding: utf-8 -*-
"""
MCP 共享可变状态 — 跨模块全局变量

所有被多个子模块读写的全局变量集中在此，
避免循环导入和 global 关键字失效问题。
从 bin/ai_cmd.py 提取，零功能变更。
"""

import time
import threading
from typing import Dict, List, Optional

# ── AI 中断标志（Ctrl+C 打断思考时置位） ──
_AI_INTERRUPTED = False

# ── MCP debug 模式 ──
_MCP_DEBUG = False
_MCP_DEBUG_START: float = 0.0

# ── MCP 服务器进程缓存 ──
MCP_SERVER_PROCESSES: Dict = {}
MCP_TOOLS_CACHE: Dict[str, List[Dict]] = {}
MCP_TRANSPORTS: Dict = {}

# ── MCP 配置路径 ──
MCP_CONFIG_PATH: Optional[str] = None

# ── MCP 预加载 / 安装锁 ──
MCP_PRELOADED = False
MCP_PRELOAD_LOCK = threading.Lock()
MCP_INSTALL_LOCK = threading.Lock()

# ── MCP 健康检查 ──
MCP_HEALTH_CHECK_INTERVAL = 120
_MCP_LAST_HEALTH_CHECK = 0.0

# ── MCP stderr 收集器（防止管道死锁） ──
_MCP_STDERR_BUFFERS: Dict[int, List[str]] = {}
_MCP_STDERR_LOCKS: Dict[int, threading.Lock] = {}

# ── Plan 模式标记 ──
_PLAN_MODE_ACTIVE = False

# ── 手动压缩请求标志（/compact 命令设置，下一轮 API 调用前触发一次）──
_MANUAL_COMPACT_REQUESTED = False

# ── 线程局部存储 ──
_thread_locals = threading.local()


def _mcp_debug(msg: str) -> None:
    """仅 --debug 模式输出 MCP 调试信息"""
    if _MCP_DEBUG:
        elapsed = time.time() - _MCP_DEBUG_START
        try:
            import sys
            sys.stderr.write(f"[MCP][{elapsed:06.2f}s] {msg}\n")
            sys.stderr.flush()
        except Exception:
            pass


def _mcp_debug_enter(func_name: str) -> None:
    """调试：记录函数进入"""
    if _MCP_DEBUG:
        _mcp_debug(f"▶ {func_name}")


def _mcp_debug_exit(func_name: str, ok: bool = True, detail: str = "") -> None:
    """调试：记录函数退出"""
    if _MCP_DEBUG:
        status = "✓" if ok else "✗"
        _mcp_debug(f"◀ {func_name} {status} {detail}".strip())
