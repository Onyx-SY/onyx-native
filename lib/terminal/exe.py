#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
onyx pty execute - Model version 9.7

Command Execution Core Module (Persistent Shell v9.7 - Fully Cross-Platform)
Provides cross-platform command execution with PTY-based persistent shell sessions
Supports: process cleanup, CWD sync, marker mechanism, screen clearing, variable reading

Improvements v9.7:
- Fixed marker mixing issue: added newline before/after markers to prevent mixing with output
- Added debug logging system with file output (optional, disabled by default)
- Debug logs to ~/.debug_pty/{session_id}/shell.log and debug.log
- Environment variable ONYX_PTY_DEBUG=1 to enable debugging

Improvements v9.6:
- Restored original v8 signal handling (SIGWINCH temporary + proper SIGINT forwarding)
- Fixed Ctrl+C handling: sends signal to process group, not just ^C byte
- TUI programs (nano, vim, top) now work correctly with Ctrl+C and window resize
- Removed all signal pollution that broke interactive programs
- Maintains all v9.5 features except signal handling reverts to v8 proven approach
- Fixed Enter key handling for TUI programs (cleared ICRNL/INLCR/IGNCR)
"""

import os
import sys
import re
import select
import struct
import errno
try:
    import fcntl
    import termios
    HAVE_FCNTL_TERMIOS = True
except ImportError:
    fcntl = None
    termios = None
    HAVE_FCNTL_TERMIOS = False
import signal
import threading
import platform
import shutil
import time
import atexit
import traceback
import uuid
import queue
import logging
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any, Callable

# ======================================================================
# Debug Configuration
# ======================================================================
DEBUG_ENABLED = os.environ.get('ONYX_PTY_DEBUG', '0') == '1'
DEBUG_DIR = os.path.expanduser('~/.debug_pty')

_session_id: Optional[str] = None
_debug_logger: Optional[logging.Logger] = None
_shell_logger: Optional[logging.Logger] = None


def _init_debug_logging():
    """Initialize debug logging system."""
    global _session_id, _debug_logger, _shell_logger
    
    if not DEBUG_ENABLED:
        return
    
    # Generate session ID if not exists
    if _session_id is None:
        _session_id = datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]
    
    # Create debug directory
    session_dir = os.path.join(DEBUG_DIR, _session_id)
    os.makedirs(session_dir, exist_ok=True)
    
    # Debug logger (for module-level debug messages)
    _debug_logger = logging.getLogger('pty_debug')
    _debug_logger.setLevel(logging.DEBUG)
    
    debug_log_path = os.path.join(session_dir, 'debug.log')
    debug_handler = logging.FileHandler(debug_log_path, encoding='utf-8')
    debug_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    _debug_logger.addHandler(debug_handler)
    
    # Shell logger (for PTY I/O)
    _shell_logger = logging.getLogger('pty_shell')
    _shell_logger.setLevel(logging.DEBUG)
    
    shell_log_path = os.path.join(session_dir, 'shell.log')
    shell_handler = logging.FileHandler(shell_log_path, encoding='utf-8')
    shell_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    _shell_logger.addHandler(shell_handler)
    
    _debug_logger.info(f"Debug session started: {session_dir}")
    _debug_logger.info(f"Debug enabled via ONYX_PTY_DEBUG=1")


def debug_log(msg: str, level: str = 'info'):
    """Write debug message to debug.log."""
    if not DEBUG_ENABLED:
        return
    
    if _debug_logger is None:
        _init_debug_logging()
    
    if _debug_logger:
        getattr(_debug_logger, level.lower(), _debug_logger.info)(msg)


def shell_log(msg: str, direction: str = 'N/A'):
    """Write PTY I/O to shell.log."""
    if not DEBUG_ENABLED:
        return
    
    if _shell_logger is None:
        _init_debug_logging()
    
    if _shell_logger:
        # Format: [DIRECTION] message (escape special chars for readability)
        escaped = repr(msg)[1:-1]  # Use repr to show escapes, remove outer quotes
        _shell_logger.debug(f"[{direction}] {escaped}")


def get_debug_session_dir() -> Optional[str]:
    """Get current debug session directory path."""
    if not DEBUG_ENABLED or _session_id is None:
        return None
    return os.path.join(DEBUG_DIR, _session_id)


def close_debug_logging():
    """Close debug logging handlers."""
    global _debug_logger, _shell_logger
    if _debug_logger:
        for handler in _debug_logger.handlers[:]:
            handler.close()
            _debug_logger.removeHandler(handler)
    if _shell_logger:
        for handler in _shell_logger.handlers[:]:
            handler.close()
            _shell_logger.removeHandler(handler)


# Import terminal type detection
try:
    from lib.get_terminal_type import get_terminal_type
    TERMINAL_TYPE_AVAILABLE = True
except ImportError:
    TERMINAL_TYPE_AVAILABLE = False
    def get_terminal_type() -> str:
        """Fallback shell detection"""
        return 'bash'

# Windows PTY support
if platform.system() == "Windows":
    try:
        import winpty
        WINPTY_AVAILABLE = True
    except ImportError:
        WINPTY_AVAILABLE = False
        print("Warning: winpty not installed. Install with: pip install pywinpty", file=sys.stderr)
else:
    WINPTY_AVAILABLE = False


# ======================================================================
# Marker Definitions
# ======================================================================
def generate_var_marker():
    """Generate marker for variable reading"""
    uid = uuid.uuid4().hex[:8]
    return f"\n__VAR_{uid}__\n"


def generate_func_marker():
    """Generate marker for function name reading"""
    uid = uuid.uuid4().hex[:8]
    return f"\n__FUNC_{uid}__\n"


# ======================================================================
# Filtered output lines — frozenset for O(1) lookup
# ======================================================================
_FILTERED_LINES = frozenset({
    'PROMPT_COMMAND', "PROMPT_COMMAND='printf \"\"'",
    "printf '%s\\n'", "printf '%s\\n' '__READY_",
    "function fish_prompt; printf ''; end",
    'unsetopt PROMPT_CR', 'unsetopt PROMPT_SP',
    "precmd() { printf ''; }",
    "PS1=''", "PROMPT=''", "RPROMPT=''",
    '@echo off', 'prompt $g',
    "Function prompt { '' }",
})
_FILTERED_PREFIXES = ('__READY_', '__VAR_', '__FUNC_', '__CWD_')

# ======================================================================
# Global State
# ======================================================================
_current_pty_size = (24, 80)
_shell_lock = threading.Lock()

# 可调超时（环境变量可覆盖，便于高负载 / 慢设备调优）
_ECHO_SKIP_TIMEOUT = float(os.environ.get('ONYX_ECHO_TIMEOUT', '0.2'))
_WRITE_TOTAL_TIMEOUT = float(os.environ.get('ONYX_WRITE_TIMEOUT', '10'))
_SHELL_READY_TIMEOUT = float(os.environ.get('ONYX_SHELL_READY_TIMEOUT', '5'))
# 回显行里可能夹着 shell 的控制序列（\x1b[?2004l 等），比较前需剥掉
_ANSI_RE = re.compile(rb'\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[=>]')
_persistent_shell: Optional['PersistentShell'] = None
# AI 执行模式标志 — 由 ai_cmd.py 在执行命令前设为 True，执行后恢复
# 用于给 AI 触发的命令加超时保护和用户弹窗
AI_EXECUTION_MODE = False
# AI 触发的上一条命令退出码（ai_cmd.py 执行前重置为 None；
# 内置命令不经 subprocess → 保持 None，显示 "-"）
AI_LAST_EXIT_CODE: Optional[int] = None

# ── AI 命令中断注册表（2026-09）──
# 目的：Ctrl+C 必须能杀掉「AI 正在执行的命令」，即使命令跑在**工作线程**里
# （signal.signal 只能装在主线程 → 工作线程装不上 handler，Ctrl+C 打不到子进程）。
# 这里维护「当前活跃的 AI 命令进程组」表；主线程的 SIGINT handler（ai_cmd 的
# _on_interrupt）优先调用 interrupt_active_ai_cmds() 转发信号，而不是打断 AI 循环。
_AI_CMD_LOCK = threading.Lock()
_AI_ACTIVE_CMDS: Dict[int, Dict[str, Any]] = {}   # id(proc) -> {"proc": Popen, "hits": int}
_AI_CMD_ESCALATE = (signal.SIGINT, signal.SIGTERM,
                    getattr(signal, "SIGKILL", signal.SIGTERM))   # Windows 无 SIGKILL


def register_ai_cmd(proc) -> None:
    """登记一个正在运行的 AI 命令进程（供 Ctrl+C 转发）。"""
    try:
        with _AI_CMD_LOCK:
            _AI_ACTIVE_CMDS[id(proc)] = {"proc": proc, "hits": 0}
    except Exception:
        pass


def unregister_ai_cmd(proc) -> None:
    """注销 AI 命令进程（命令结束后调用）。"""
    try:
        with _AI_CMD_LOCK:
            _AI_ACTIVE_CMDS.pop(id(proc), None)
    except Exception:
        pass


class PtyCmdProxy:
    """把 PTY 前台进程组包装成「类 Popen」对象，供 interrupt_active_ai_cmds 转发信号。

    用途：AI 模式下**交互式命令**（vim/ssh/python 等）会回退到常驻 PTY 执行，
    它没有 Popen 对象 → 注册表里没有条目 → Ctrl+C 转发不到（旧实现只有 subprocess
    路径被登记，这就是「有时 Ctrl+C 杀不掉 AI 正在执行的命令」的一个来源）。

    `pid` 取 PTY 的**前台进程组**（tcgetpgrp）：这正是终端 Ctrl+C 打的目标；
    取不到时退回 shell 自身 pid（等价于终端无 job control 时的行为）。
    """

    def __init__(self, shell):
        self._shell = shell

    @property
    def pid(self):
        sh = self._shell
        try:
            fd = getattr(sh, "master_fd", None)
            if fd is not None:
                pgid = os.tcgetpgrp(fd)
                if pgid:
                    return pgid
        except Exception:
            pass
        return getattr(sh, "pid", None)

    def poll(self):
        return None      # 命令是否结束由调用方（run_cmd_sync）负责注销

    def send_signal(self, sig) -> None:
        pid = self.pid
        if pid is None:
            return
        try:
            os.killpg(pid, sig)          # pid 已是进程组 id
        except (ProcessLookupError, PermissionError, OSError, AttributeError):
            try:
                os.kill(pid, sig)
            except Exception:
                pass


def interrupt_active_ai_cmds() -> int:
    """把中断信号转发给所有活跃的 AI 命令进程组（重复按 Ctrl+C 逐级升级）。

    返回处理的命令数（0 = 当前没有 AI 命令在跑，调用方应改走「打断 AI」路径）。
    升级策略：第 1 次 SIGINT → 第 2 次 SIGTERM → 第 3 次及以后 SIGKILL。
    解决「命令忽略 SIGINT / 卡在不可中断状态 → Ctrl+C 杀不掉」的问题。
    """
    handled = 0
    try:
        with _AI_CMD_LOCK:
            entries = list(_AI_ACTIVE_CMDS.values())
        for ent in entries:
            proc = ent.get("proc")
            if proc is None:
                continue
            try:
                if proc.poll() is not None:
                    continue
            except Exception:
                pass
            hits = int(ent.get("hits", 0))
            sig = _AI_CMD_ESCALATE[min(hits, len(_AI_CMD_ESCALATE) - 1)]
            ent["hits"] = hits + 1
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError, OSError, AttributeError):
                try:
                    proc.send_signal(sig)
                except Exception:
                    pass
            handled += 1
    except Exception:
        pass
    return handled

# ========== getcwd 缓存（减少频繁系统调用） ==========
_cached_cwd: Optional[str] = None
_cached_cwd_time: float = 0
_CWD_CACHE_TTL = 0.5  # 秒


def _get_cwd_cached() -> str:
    global _cached_cwd, _cached_cwd_time
    now = time.time()
    if _cached_cwd is None or (now - _cached_cwd_time) > _CWD_CACHE_TTL:
        _cached_cwd = os.getcwd()
        _cached_cwd_time = now
    return _cached_cwd


def invalidate_cwd_cache() -> None:
    """强制失效 CWD 缓存，使下次 _get_cwd_cached() 重新读取 os.getcwd()"""
    global _cached_cwd, _cached_cwd_time
    _cached_cwd = None
    _cached_cwd_time = 0


# ======================================================================
# Utility Functions
# ======================================================================
def get_terminal_size(fd: int = sys.stdin.fileno()) -> Tuple[int, int]:
    """Get terminal window size"""
    if platform.system() == "Windows":
        try:
            import shutil
            cols, rows = shutil.get_terminal_size()
            return rows, cols
        except Exception:
            pass
        return 24, 80

    if HAVE_FCNTL_TERMIOS and os.isatty(fd):
        try:
            # ⚠️ 不能用 fcntl.ioctl(fd, TIOCGWINSZ, '1234') 这种 4 字节写法：
            # struct winsize 是 8 字节，内核会写 8 字节进 4 字节缓冲区，
            # Python 3.14 直接抛 SystemError('buffer overflow')，被 except 吞掉后
            # 永远回退 (24,80) → 所有 TUI 按 24x80 绘制 → 形变/错位。
            # os.get_terminal_size() 内部用正确的 8 字节 ioctl，且不读 COLUMNS/LINES 环境变量。
            _sz = os.get_terminal_size(fd)
            if _sz.lines > 0 and _sz.columns > 0:
                return _sz.lines, _sz.columns
        except OSError as _e:
            debug_log(f"get_terminal_size failed on fd={fd}: {_e}", 'error')
    return 24, 80


def update_pty_size(master_fd) -> None:
    """Update PTY window size dynamically"""
    global _current_pty_size
    rows, cols = get_terminal_size()
    if rows != _current_pty_size[0] or cols != _current_pty_size[1]:
        _current_pty_size = (rows, cols)
        debug_log(f"PTY size updated: {rows}x{cols}")
        if HAVE_FCNTL_TERMIOS:
            try:
                if master_fd is not None:
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                                struct.pack('HHHH', rows, cols, 0, 0))
            except Exception:
                pass
        elif platform.system() == "Windows":
            # Windows: 通过 winpty 的 set_size 调整窗口
            try:
                import winpty
                if _persistent_shell and _persistent_shell._winpty_handle:
                    _persistent_shell._winpty_handle.set_size(cols, rows)
            except Exception:
                pass
        elif hasattr(master_fd, 'set_size'):
            try:
                master_fd.set_size(cols, rows)
            except Exception:
                pass


def get_shell_from_type() -> str:
    """
    Get shell path based on terminal type detection.
    Uses lib.get_terminal_type for consistent detection.
    """
    terminal_type = get_terminal_type()
    debug_log(f"Detected terminal type: {terminal_type}")
    
    # Map terminal type to shell path
    shell_map = {
        'bash': '/bin/bash',
        'zsh': '/bin/zsh',
        'fish': '/usr/bin/fish',
        'powershell': 'pwsh',
        'cmd': 'cmd.exe',
        'sh': '/bin/sh',
    }
    
    shell_cmd = shell_map.get(terminal_type, 'bash')
    
    # Verify the shell exists, fallback if needed
    if shutil.which(shell_cmd):
        debug_log(f"Using shell: {shell_cmd}")
        return shell_cmd
    
    # Fallback logic
    for candidate in ['bash', 'zsh', 'fish', 'sh']:
        if shutil.which(candidate):
            debug_log(f"Fallback to shell: {candidate}")
            return candidate
    
    return '/bin/sh'


_shell_cache: Optional[str] = None
# 用户显式指定的底层 shell（--shell / ONYX_SHELL），绝对路径；None = 未指定
_shell_override: Optional[str] = None

# 已知 shell 候选（用于 --list-shells 与覆盖解析）
_KNOWN_SHELL_NAMES = ('bash', 'zsh', 'fish', 'sh', 'dash', 'ksh',
                      'pwsh', 'powershell', 'cmd')


def _resolve_shell_candidate(name_or_path: Optional[str]) -> Optional[str]:
    """把 'fish' / '/usr/bin/fish' 解析为可执行绝对路径；无效返回 None。"""
    if not name_or_path:
        return None
    cand = str(name_or_path).strip()
    if not cand:
        return None
    is_path = os.sep in cand or (os.altsep and os.altsep in cand)
    if is_path:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.abspath(cand)
        return None
    found = shutil.which(cand)
    return found or None


_SUBPROC_SHELL_CACHE = None


def _subprocess_shell() -> str:
    """AI 模式 shell=True 用的解释器（POSIX）。

    subprocess 的 shell=True 默认用 /bin/sh（dash）—— 它**不支持 source**，
    也没有 [[ ]] / 数组 / $'...' 等 bash 扩展，于是 `source xxx.sh` 会报
    "source: not found"、`[[ ... ]]` 会语法错误。这里优先选 bash，其次 zsh，
    再退到用户 shell（仅 POSIX 系），最后 /bin/sh。
    """
    global _SUBPROC_SHELL_CACHE
    if _SUBPROC_SHELL_CACHE:
        return _SUBPROC_SHELL_CACHE
    for _name in ('bash', 'zsh'):
        _p = shutil.which(_name)
        if _p:
            _SUBPROC_SHELL_CACHE = _p
            return _p
    try:
        _s = get_shell()
        if _s and os.path.basename(_s).lower() in ('sh', 'dash', 'ksh', 'ash'):
            _SUBPROC_SHELL_CACHE = _s
            return _s
    except Exception:
        pass
    _SUBPROC_SHELL_CACHE = '/bin/sh'
    return _SUBPROC_SHELL_CACHE


def list_available_shells() -> List[Tuple[str, str]]:
    """列出本机可用的已知 shell：[(名字, 绝对路径), ...]（去重、保序）。

    非 Windows 上跳过 'cmd'（避免把 Termux/Unix 上同名的无关命令列进来）。
    """
    out: List[Tuple[str, str]] = []
    seen = set()
    _is_win = platform.system() == "Windows"
    for name in _KNOWN_SHELL_NAMES:
        if name == 'cmd' and not _is_win:
            continue
        path = shutil.which(name)
        if path and path not in seen:
            seen.add(path)
            out.append((name, path))
    return out


def set_shell_override(name_or_path: Optional[str]) -> Optional[str]:
    """设置底层 shell 覆盖。返回解析后的绝对路径；无效（不存在）返回 None。"""
    global _shell_override, _shell_cache
    resolved = _resolve_shell_candidate(name_or_path)
    _shell_override = resolved
    _shell_cache = None          # 强制 get_shell() 重新计算
    if resolved:
        debug_log(f"Shell override set: {resolved}")
    return resolved


def get_shell() -> str:
    """Get available shell for current system (uses terminal type detection)"""
    global _shell_cache, _shell_override
    if _shell_cache:
        return _shell_cache

    # ── 0. 用户显式覆盖（--shell / ONYX_SHELL）优先级最高 ──
    override = _shell_override or os.environ.get('ONYX_SHELL') or ''
    if override:
        resolved = _resolve_shell_candidate(override)
        if resolved:
            _shell_override = resolved
            _shell_cache = resolved
            debug_log(f"Using shell override: {resolved}")
            return resolved
        debug_log(f"Shell override {override!r} not found, falling back to auto-detect", 'error')

    # ── 1. 持久化配置 etc/onyx/shell（`manage shell <name>` 写入）──
    # 与 lib/parse.normalize_shell_type() 共用同一来源，保证「输入模块」
    # 与「底层 PTY」用的是同一种 shell。
    try:
        with open(os.path.join(os.path.expanduser('~'), '.onyx', 'shell'),
                  encoding='utf-8') as _f:
            _cfg_shell = _f.read().strip()
    except Exception:
        _cfg_shell = ''
    if _cfg_shell:
        _r = _resolve_shell_candidate(_cfg_shell)
        if _r:
            _shell_cache = _r
            debug_log(f"Using configured shell: {_r}")
            return _r
    
    if TERMINAL_TYPE_AVAILABLE:
        _shell_cache = get_shell_from_type()
        return _shell_cache
    
    # Fallback logic
    if platform.system() == "Windows":
        for candidate in ["pwsh", "powershell", "cmd"]:
            if shutil.which(candidate):
                debug_log(f"Windows fallback shell: {candidate}")
                _shell_cache = candidate
                return _shell_cache
        _shell_cache = "cmd.exe"
        return _shell_cache

    env_shell = os.environ.get("SHELL")
    if env_shell and os.path.isfile(env_shell) and os.access(env_shell, os.X_OK):
        debug_log(f"Using SHELL env: {env_shell}")
        _shell_cache = env_shell
        return _shell_cache

    candidates = [
        "/bin/bash", "/usr/bin/bash",
        "/bin/zsh", "/usr/bin/zsh",
        "/usr/bin/fish", "/bin/fish",
        "/bin/dash", "/usr/bin/dash",
        "/bin/sh", "/usr/bin/sh"
    ]
    for cand in candidates:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            debug_log(f"Found shell: {cand}")
            _shell_cache = cand
            return _shell_cache

    _shell_cache = "/bin/sh"
    return _shell_cache


# ======================================================================
# Collect Caller Variables
# ======================================================================
def _collect_caller_vars(depth: int = 1) -> Dict[str, str]:
    """
    Collect local and global variables from call stack

    Args:
        depth: Stack frame depth to trace upward

    Returns:
        Variable dictionary {var_name: var_value_str}
    """
    vars_dict = {}

    try:
        frame = sys._getframe(depth)
        while frame and depth > 0:
            # Collect local variables
            for key, value in frame.f_locals.items():
                if key not in vars_dict and isinstance(value, (str, int, float, bool)):
                    vars_dict[key] = str(value)
            # Collect global variables
            for key, value in frame.f_globals.items():
                if key not in vars_dict and isinstance(value, (str, int, float, bool)):
                    vars_dict[key] = str(value)
            frame = frame.f_back
            depth -= 1
    except (ValueError, AttributeError):
        pass

    debug_log(f"Collected {len(vars_dict)} caller variables")
    return vars_dict


# ======================================================================
# Persistent Shell Session
# ======================================================================
class PersistentShell:
    """Persistent interactive shell session (PTY-based with Windows support)"""

    def __init__(self, shell_path: Optional[str] = None, cwd: Optional[str] = None,
                 extra_vars: Optional[Dict[str, str]] = None):
        """
        Initialize persistent shell

        Args:
            shell_path: Shell path
            cwd: Working directory
            extra_vars: Additional variables from caller's local/global scope
        """
        debug_log(f"Initializing PersistentShell: cwd={cwd}, shell_path={shell_path}")
        self.shell = shell_path or get_shell()
        self.master_fd = None
        self.pid: Optional[int] = None
        # shell 进程 starttime（/proc/<pid>/stat 第 22 字段）——防 PID 复用误判
        self._start_ticks = 0
        self.cwd = cwd or os.getcwd()
        self._dead = False
        self.shell_name = os.path.basename(self.shell).lower()
        self._winpty_handle = None
        self._read_thread = None
        self._read_queue = None
        self._stop_thread = False
        # 用于 PS1 完成检测的随机 marker（在 _init_shell / _setup_prompt / _execute_passthrough 中使用）
        self._done_marker = f"__DONE_{uuid.uuid4().hex[:12]}__"
        # 动态 prompt 模式列表：除 marker 外，还实时学习 shell 实际 PS1
        # 即使 PS1 被 venv/主题等改写，也能通过 fallback 模式检测命令完成
        self._prompt_patterns: List[re.Pattern] = []
        # PS1 探测（_probe_and_learn_prompt）已是死代码：通用 prompt 正则匹配
        # 于 2026-07-22 被移除，_prompt_patterns 不再被读取。它却会在**宿主终端
        # 仍是 cooked 模式**时向 PTY 发 probe 并阻塞最多 2.6s，还会把 probe 的
        # __DONE__ PS1 残留进 PTY，导致紧随其后的第一条命令被误判为"已完成"。
        # 因此这里直接标记为已探测，彻底跳过它（方法保留以兼容调用方）。
        self._prompt_probed = True
        # 首条命令前需要"耐心排空"PTY 残留（shell 初始化的收尾输出），避免其
        # __DONE__ PS1 被当成完成信号导致首条命令提前返回、TUI 整帧丢失。
        self._first_exec_done = False

        # Merge environment variables with extra variables
        self._extra_vars = {}
        # 1. Add current process environment variables
        # 不复制父进程的 PS1/PROMPT——_init_shell_unix 中会设为 marker 用于命令完成检测
        # VIRTUAL_ENV 保留不动，让 PTY 子 shell 也能在同一个 venv 中使用
        _SKIP_ENV_KEYS = frozenset({'PS1', 'PROMPT', 'PROMPT_COMMAND'})
        for key, value in os.environ.items():
            if key in _SKIP_ENV_KEYS:
                continue
            if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
                self._extra_vars[key] = value
        # 2. Add caller's variables (overwrites environment variables with same name)
        if extra_vars:
            for key, value in extra_vars.items():
                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
                    if isinstance(value, (str, int, float, bool)):
                        self._extra_vars[key] = str(value)

        self._init_shell()
        # PTY slave 保留 ECHO（否则 input() 输入不可见），
        # 命令回显由 _echo_skipped 机制过滤。
        self._setup_prompt()
        # Skip _inject_extra_vars() on initial creation: env vars are already
        # passed to the child process via os.execvpe(..., env) in _init_shell.
        # _inject_extra_vars() is still called in execute() when rebuilding a
        # dead shell, where the env dict is not re-applied.
        # Clear screen completely to remove all initialization residue
        # _clear_screen 移除：init 时无残留需要清除，省一次 PTY 写入
        debug_log(f"PersistentShell initialized: shell={self.shell}, shell_name={self.shell_name}")

    def _inject_extra_vars(self):
        """Inject extra variables into shell process via export statements"""
        if not self._extra_vars or (self.master_fd is None and self._winpty_handle is None):
            return

        inject_cmds = []
        for var_name, var_value in self._extra_vars.items():
            # Skip certain special variables
            if var_name.startswith('_') and len(var_name) > 1:
                continue
            if var_name in ('self', 'cls'):
                continue
            # Safe shell escaping for values
            if self.shell_name in ('pwsh', 'powershell'):
                escaped_val = var_value.replace("'", "''")
                inject_cmds.append(f"$env:{var_name} = '{escaped_val}'")
            elif self.shell_name == 'cmd':
                escaped_val = var_value
                inject_cmds.append(f"set {var_name}={escaped_val}")
            else:
                # Unix shell: wrap in single quotes, escape internal single quotes
                escaped_val = var_value.replace("'", "'\"'\"'")
                inject_cmds.append(f"export {var_name}='{escaped_val}'")

        if inject_cmds:
            debug_log(f"Injecting {len(inject_cmds)} environment variables")
            try:
                inject_script = '\n'.join(inject_cmds) + '\n'
                shell_log(inject_script, 'WRITE')
                self._write_to_master(inject_script.encode('utf-8'))
                self._drain_output()
            except OSError as e:
                debug_log(f"Failed to inject variables: {e}", 'error')

    def _probe_and_learn_prompt(self) -> None:
        """探测 shell 实际 PS1，加入 fallback 模式列表。

        发送一个无声命令（空 echo），抓取其输出与下一个 prompt 之间
        的文本作为当前 PS1 的样貌。之后 mark 完成时既认 __DONE__
         marker，也认实测到的 PS1。
        """
        if self._prompt_probed:
            return
        self._prompt_probed = True

        probe = "echo __PSL_PROBE__\n"
        try:
            self._write_to_master(probe.encode('utf-8'))
        except OSError:
            return

        # 读回显 + probe 结果 + 紧接其后的 prompt
        buf = ""
        deadline = time.time() + 2.0
        seen_probe = False
        while time.time() < deadline:
            try:
                chunk = self._read_from_master(timeout=0.3)
            except Exception:
                break
            if not chunk:
                if seen_probe:
                    break
                continue
            # 修复：原代码 `buf += chunk` 是 str += bytes（TypeError）被 except 吞掉，
            # 探测循环实际上第一轮就 break → 从未学到 prompt，且把 probe 的
            # __DONE__ PS1 残留在 PTY 里 → 下一条命令被误判为"已完成"→ 首条命令输出丢失。
            buf += chunk.decode('utf-8', errors='replace')
            if "__PSL_PROBE__" in buf:
                seen_probe = True

        # 收尾：把 probe 命令自身的回显与紧随其后的 PS1（__DONE__ marker）读干净，
        # 否则残留 marker 会被第一条真实命令误当成完成信号。
        quiet_deadline = time.time() + 0.6
        while time.time() < quiet_deadline:
            try:
                extra = self._read_from_master(timeout=0.08)
            except Exception:
                break
            if not extra:
                break
            buf += extra.decode('utf-8', errors='replace')

        # 从 buf 中提取 probe 输出之后、prompt 之前/之后的内容
        idx = buf.rfind("__PSL_PROBE__")
        if idx < 0:
            return
        after_probe = buf[idx + len("__PSL_PROBE__"):]
        # 去掉 probe 自身的回显（可能包含换行）
        after_probe = after_probe.strip()
        if not after_probe:
            return

        # 把实际观察到的 prompt 文本加入模式列表
        # 转义 regex 特殊字符，匹配行尾
        escaped = re.escape(after_probe)
        pattern_str = f"(?:{escaped})"
        try:
            compiled = re.compile(pattern_str)
            self._prompt_patterns.append(compiled)
            debug_log(f"Learned PS1 prompt pattern: {after_probe!r}")
        except re.error:
            pass

    def _execute_passthrough(
        self,
        cmd: str,
        output_buffer: List[str],
        log_info: Optional[Callable] = None,
        log_error: Optional[Callable] = None
    ) -> Tuple[int, str]:
        """
        Passthrough 模式：命令裸写 bash，不包 {}、不设 TTY。
        用 shell 的 PS1（__DONE__:$?）检测命令完成，不追加 marker。
        PS1 是 shell 提示词机制的一部分，SIGINT 无法阻止它被打印。
        """
        # ── 提前初始化 finally/函数尾部要访问的变量 ──
        # 必须在 try 之前：try 中途任意异常（如 PTY 探测失败）都会跳过后续赋值，
        # 否则 finally 与尾部检查会触发 UnboundLocalError 并掩盖真实错误。
        is_windows = platform.system() == "Windows"
        _passthrough_entered_raw = False
        # 只有主线程才拥有"用户键盘"。后台线程（alias-sender / submit_cmd_async /
        # 子代理）绝不能读 fd 0，否则会和 prompt_toolkit / 前台 TUI 抢按键。
        _is_main_thread = (threading.current_thread() is threading.main_thread())
        old_sigint = None
        old_sigwinch = None
        _interrupted_flag = {'value': False}
        full_raw_output = ""
        interrupted = False
        return_code = -1

        # 首次执行时探测 shell 实际 PS1，加入 fallback 模式列表
        try:
            if not self._prompt_probed:
                self._probe_and_learn_prompt()

            # 命令文本若含哨兵串（例如用户 echo "$PS1"）→ 换一个哨兵，
            # 否则命令回显/输出里的哨兵会被误判为「命令结束」。
            if self._done_marker and self._done_marker in cmd:
                debug_log("Command contains sentinel string; regenerating marker")
                self._done_marker = f"__DONE_{uuid.uuid4().hex[:12]}__"
                try:
                    self._ensure_sentinel()
                except Exception:
                    pass

            # 用哨兵检测命令完成
            _done_marker = self._done_marker
            full_cmd = f"{cmd}\n"
            debug_log(f"Passthrough full_cmd: {repr(full_cmd[:200])}")
            shell_log(full_cmd, 'WRITE_CMD')

            fd_stdin = sys.stdin.fileno()

            # 命令执行期间进入 raw 模式，确保 TUI 程序的逐键输入能正确转发到 PTY。
            # 命令结束后恢复到 cooked 模式，与 bash/zsh 行为一致。
            if not is_windows and _is_main_thread:
                try:
                    import termios as _pt
                    if os.isatty(fd_stdin):
                        save_terminal_attrs()
                        _new_tty = _pt.tcgetattr(fd_stdin)
                        # 对齐 tty.setraw()：字节级透传 + 不拦截特殊键
                        _new_tty[0] &= ~(_pt.BRKINT | _pt.ICRNL | _pt.INPCK
                                         | _pt.ISTRIP | _pt.IXON | _pt.IXOFF)
                        _new_tty[1] &= ~_pt.OPOST          # 关输出处理（不再自动补 CR）
                        _new_tty[2] = (_new_tty[2] & ~(_pt.CSIZE | _pt.PARENB)) | _pt.CS8
                        _new_tty[3] &= ~(_pt.ICANON | _pt.ECHO | _pt.IEXTEN | _pt.ISIG)
                        _new_tty[6][_pt.VMIN] = 1
                        _new_tty[6][_pt.VTIME] = 0
                        _pt.tcsetattr(fd_stdin, _pt.TCSANOW, _new_tty)
                        _passthrough_entered_raw = True
                except (ImportError, OSError):
                    pass

            if not is_windows:
                def _sigint_handler(signum, frame):
                    """将 SIGINT 转发到 PTY 子进程组，使 Ctrl+C 能真正杀死前台程序"""
                    _interrupted_flag['value'] = True
                    debug_log("SIGINT caught by Python handler, forwarding to PTY process group")
                    try:
                        # 优先发给 PTY 的"前台进程组"（真正在跑的 TUI/命令），
                        # 而不是 shell 自己的进程组 —— 二者通常不同。
                        pgid = None
                        if self.master_fd is not None:
                            try:
                                pgid = os.tcgetpgrp(self.master_fd)
                            except OSError:
                                pgid = None
                        if not pgid and self.pid is not None:
                            pgid = os.getpgid(self.pid)
                        if pgid:
                            os.killpg(pgid, signal.SIGINT)
                    except (ProcessLookupError, PermissionError, OSError) as e:
                        debug_log(f"Failed to forward SIGINT to process group: {e}", 'error')
                # 后台线程（alias-sender）读写 tty 时避免被 SIGTTOU 挂起（真 tty 语义）
                old_sigttou = None
                try:
                    if hasattr(signal, 'SIGTTOU'):
                        old_sigttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
                except (ValueError, OSError):
                    pass
                try:
                    old_sigint = signal.signal(signal.SIGINT, _sigint_handler)
                    if hasattr(signal, 'SIGWINCH'):
                        def sigwinch_handler(signum, frame):
                            update_pty_size(self.master_fd)
                        old_sigwinch = signal.signal(signal.SIGWINCH, sigwinch_handler)
                except ValueError:
                    # 非主线程无法设置信号处理器（如后台 alias-sender 线程），静默跳过
                    pass

            fd_stdin = sys.stdin.fileno()

            # 命令执行前无条件刷新 PTY 尺寸：
            # 提示符期间 resize 终端不会触发本函数的 SIGWINCH handler，
            # 不在此刷新会导致 TUI 按旧尺寸绘制（形变/错位）。
            if not is_windows:
                update_pty_size(self.master_fd)

            # 排空 PTY 残留输出。
            # 首条命令更耐心：shell 初始化（PS1 设置等）的收尾输出可能稍后才到，
            # 若残留里的 __DONE__ PS1 被当成"完成 marker"，首条命令会提前返回、
            # 整帧 TUI 画面丢失（表现为 TUI 一闪而过或全黑）。
            if self._first_exec_done:
                self._drain_output()
            else:
                self._drain_output(max_iterations=20, quiet_timeout=0.03)
            _echo_pending = True  # 需要跳过 shell 回显的命令文本
            _echo_buf = b""       # 累积回显字节（bytes：与原始数据同域，避免编解码损坏）
            _echo_start_time = time.time()  # echo 跳过超时计时（TUI 程序无 \n 时避免吞输出）

            try:
                self._write_to_master(full_cmd.encode('utf-8'))
                debug_log("Passthrough command written to PTY")
            except OSError as e:
                debug_log(f"Failed to write: {e}", 'error')
                return -1, full_raw_output

            def _emit(_b: bytes):
                """把 PTY 原始字节直接写「真实终端」（保留 CRLF；避免 UTF-8 往返损坏）。

                必须绕过 sys.stdout：AI 路径下 capture_command_output() 会把
                sys.stdout 换成 RealTimeOutputCatcher（其 .buffer 是个 list），
                旧的 getattr(sys.stdout,'buffer') 拿到 list 后 .write 抛 AttributeError
                被 except 吞掉 → TUI 输出全部丢失。
                """
                _target = sys.__stdout__ if sys.__stdout__ is not None else sys.stdout
                _buf = getattr(_target, 'buffer', None)
                try:
                    if _buf is not None and hasattr(_buf, 'write'):
                        _buf.write(_b)
                        _buf.flush()
                        return
                except Exception:
                    pass
                try:
                    os.write(1, _b)
                    return
                except Exception:
                    pass
                try:
                    sys.stdout.write(_b.decode('utf-8', errors='replace'))
                    sys.stdout.flush()
                except Exception:
                    pass

            def _safe(_s: str) -> str:
                """把 surrogateescape 代理字符还原为合法文本（供 output_buffer/AI 展示）。"""
                try:
                    return _s.encode('utf-8', 'surrogateescape').decode('utf-8', 'replace')
                except Exception:
                    return _s

            # ── 哨兵扫描状态（累积缓冲 + 延迟转发）──
            # 哨兵可能被 4096 字节分块切开：只对单次 read 做正则会漏检 → 命令永不返回。
            # 这里维护「尚未转发」的尾部字节缓冲，只转发确定不属于哨兵前缀的部分
            # （KEEP 字节），命中时精确截断。转发的是**原始字节**，无 UTF-8 往返损坏。
            _SENT_KEEP = 40   # 哨兵最长约 30B（含 CRLF）；越小转发越实时
            _scan_bytes = bytearray()
            _last_out_byte = [None]   # 最近一次已转发内容的最后一个字节
            _sent_re_b = re.compile(
                rb'(?:^|\n)' + re.escape(_done_marker.encode('utf-8')) + rb'(?::(-?\d+))?'
            )
            _sentinel_seen = False
            _hard_timed_out = False
            _sentinel_lost = False

            def _scan_feed(_b: bytes):
                """喂入原始字节并转发安全部分。返回 (found, exit_code_or_None)。"""
                nonlocal _scan_bytes
                if not _b:
                    return False, None
                _scan_bytes.extend(_b)
                _buf = bytes(_scan_bytes)
                _m = _sent_re_b.search(_buf)
                if _m:
                    _cut = _m.start()
                    if _cut > 0 and _buf[_cut - 1:_cut] == b'\r':
                        _cut -= 1          # 哨兵的 CR 也切掉，避免多一个空行
                    _head = _buf[:_cut]
                    if _head:
                        # 末尾是 \r 时不补：那是 shell 控制序列的尾巴
                        # （如 \x1b[?2004l\r），补了会凭空多一个空行 ——
                        # cd 这类「无输出」命令最常见。
                        if not _head.endswith((b'\n', b'\r')):
                            _head += b'\n'   # 输出未以换行结束 → 补一个
                    elif _last_out_byte[0] not in (None, b'\n'):
                        _head = b'\n'        # 上次输出没换行结尾 → 补一个，避免粘连
                    _scan_bytes = bytearray(_buf[_m.end():])
                    if _head:
                        _emit(_head)
                        _last_out_byte[0] = _head[-1:]
                        if output_buffer is not None:
                            output_buffer.append(
                                _safe(_head.decode('utf-8', 'replace').replace('\r\n', '\n')))
                    _c = _m.group(1)
                    return True, (int(_c) if _c is not None else None)
                if len(_buf) > _SENT_KEEP:
                    _head = _buf[:len(_buf) - _SENT_KEEP]
                    _scan_bytes = bytearray(_buf[len(_buf) - _SENT_KEEP:])
                    _emit(_head)
                    _last_out_byte[0] = _head[-1:]
                    if output_buffer is not None:
                        output_buffer.append(
                            _safe(_head.decode('utf-8', 'replace').replace('\r\n', '\n')))
                return False, None

            # ── 空闲 / 活性判定参数 ──
            _POLL_T = 0.25          # 空闲轮询（省 CPU）
            _POLL_T_PENDING = 0.01  # 扫描缓冲还有待放字节时的轮询（低延迟）
            _IDLE_PROBE_AFTER = 1.5   # shell 空闲超过它且无哨兵 → 判定哨兵失效
            _MAX_HEAL = 3
            _last_activity = time.time()
            _heal_count = 0
            _shell_pgid = self.pid
            _hard_deadline = None
            try:
                _tmo_env = float(os.environ.get('ONYX_CMD_TIMEOUT', '0') or 0)
            except (TypeError, ValueError):
                _tmo_env = 0.0
            if _tmo_env > 0:
                _hard_deadline = time.time() + _tmo_env

            while True:
                if not is_windows and self.master_fd is not None:
                    try:
                        # 非主线程（alias-sender / 异步任务 / 子代理）不监听 fd 0，
                        # 否则会把用户按键从 prompt_toolkit/前台 TUI 手里抢走。
                        _watch_fds = [self.master_fd]
                        if _is_main_thread:
                            _watch_fds.append(fd_stdin)
                        rlist, _, _ = select.select(
                            _watch_fds, [], [],
                            _POLL_T_PENDING if _scan_bytes else _POLL_T)
                    except (select.error, OSError):
                        continue
                    data = None
                    stdin_data = None
                    if self.master_fd in rlist:
                        try:
                            data = os.read(self.master_fd, 4096)
                        except OSError:
                            data = None
                    if fd_stdin in rlist:
                        try:
                            stdin_data = os.read(fd_stdin, 1024)
                        except OSError:
                            stdin_data = None
                else:
                    data = self._read_from_master(timeout=0.01)
                    # Windows: 用 msvcrt 读取键盘输入并转发到 PTY
                    stdin_data = None
                    if is_windows:
                        try:
                            import msvcrt
                            if msvcrt.kbhit():
                                stdin_data = msvcrt.getch()
                        except ImportError:
                            pass

                # --- Forward PTY output to terminal ---
                if data is not None:
                    if len(data) == 0:
                        self._dead = True
                        debug_log("PTY EOF (passthrough)")
                        break
                    try:
                        text = data.decode('utf-8', errors='surrogateescape')
                    except UnicodeDecodeError:
                        text = data.decode('latin-1', errors='replace')

                    full_raw_output += text
                    _last_activity = time.time()

                    # 跳过 shell 回显的命令文本（仅匹配首部，不吞 TUI 输出）。
                    # 全程用 bytes 比较，避免编解码往返损坏跨块的多字节字符。
                    _emit_bytes = data
                    if _echo_pending:
                        # TUI 程序可能长时间输出无 \n 字节（如全屏控制码），
                        # 超时 200ms 则视为已过 echo 阶段，直接转发全部累积数据
                        if _echo_buf and time.time() - _echo_start_time > _ECHO_SKIP_TIMEOUT:
                            _emit_bytes = _echo_buf + data
                            _echo_buf = b""
                            _echo_pending = False
                        else:
                            _echo_buf += data
                            _max_echo_len = len(cmd.encode('utf-8', 'replace')) + 64
                            _nl = _echo_buf.find(b'\n')
                            _cmd_b = cmd.strip().encode('utf-8', 'replace')
                            if _nl != -1 and _nl < _max_echo_len:
                                _echo_line = _ANSI_RE.sub(b'', _echo_buf[:_nl]).strip()
                                if _echo_line == _cmd_b or _cmd_b.startswith(_echo_line):
                                    _emit_bytes = _echo_buf[_nl + 1:]
                                    _echo_buf = b""
                                    _echo_pending = False
                                    if not _emit_bytes:
                                        continue
                                else:
                                    _emit_bytes = _echo_buf
                                    _echo_buf = b""
                                    _echo_pending = False
                            elif len(_echo_buf) > _max_echo_len:
                                # 超过命令长度还没 \n → 不是命令回显（TUI 程序），全部放过
                                _emit_bytes = _echo_buf
                                _echo_buf = b""
                                _echo_pending = False
                            else:
                                continue

                    # ── 哨兵检测（累积缓冲 + 行锚定）──
                    # 命中 __DONE_x:<code>（bash 的 PROMPT_COMMAND 主哨兵）或
                    # 裸 __DONE_x（cmd 无退出码哨兵）即视为命令结束。
                    _found, _code = _scan_feed(_emit_bytes)
                    if _found:
                        return_code = _code if _code is not None else 0
                        _sentinel_seen = True
                        debug_log(f"Sentinel hit, exit={return_code}")
                        break

                    # ── 不再使用 fallback prompt pattern 检测 ──
                    # 2026-07-22: 移除了通用 prompt 正则（$ > # %）和 _prompt_patterns 匹配。
                    # 这些会误抓交互式程序（如 c.py 的 "🔢 >"）自身的提示符，导致 passthrough
                    # 提前退出。交互式程序不产生哨兵，必须保持 passthrough
                    # 直通直到用户 Ctrl+C 或程序自己退出（产生 PTY EOF）。
                    # 只有 shell 的哨兵才是命令完成的可靠信号。

                # --- Forward stdin to PTY (for TUI programs) ---
                if stdin_data is not None and len(stdin_data) > 0:
                    try:
                        self._write_to_master(stdin_data)
                    except OSError:
                        pass
                    _last_activity = time.time()

                # --- 空闲判定 / 硬超时兜底（绝不永久阻塞）---
                _now = time.time()
                # 静默 15ms → 扫描缓冲全部放出：TUI 首屏（nano/vim）不能被
                # 「等哨兵」的保留区扣住，否则要等用户按键才渲染。
                # （配合上面的 _POLL_T_PENDING，最坏延迟 ≈ 15ms。）
                if _scan_bytes and _now - _last_activity > 0.015:
                    _b = bytes(_scan_bytes)
                    _emit(_b)
                    _last_out_byte[0] = _b[-1:]
                    if output_buffer is not None:
                        output_buffer.append(
                            _safe(_b.decode('utf-8', 'replace').replace('\r\n', '\n')))
                    _scan_bytes = bytearray()
                if _hard_deadline is not None and _now > _hard_deadline:
                    debug_log(f"Passthrough hard timeout after {_tmo_env:.0f}s", 'error')
                    if output_buffer is not None:
                        output_buffer.append(
                            f"[命令超过 ONYX_CMD_TIMEOUT={_tmo_env:.0f}s，已中止]")
                    _hard_timed_out = True
                    if _scan_bytes:  # flush 尾部，避免丢最后 KEEP 字节
                        _emit(bytes(_scan_bytes))
                        _scan_bytes = bytearray()
                    break
                if _now - _last_activity > _IDLE_PROBE_AFTER:
                    _fg = None
                    if not is_windows and self.master_fd is not None:
                        try:
                            _fg = os.tcgetpgrp(self.master_fd)
                        except Exception:
                            _fg = None
                    if _fg is not None and _shell_pgid is not None and _fg != _shell_pgid:
                        # 有子进程（TUI / 子 shell / sleep…）占前台 → 正常等待，不干预
                        _last_activity = _now
                    else:
                        # shell 自己在等输入却没有哨兵 → PS1/hook 被改写 → 自愈
                        _heal_count += 1
                        debug_log(f"Sentinel missing while shell idle; healing "
                                  f"(attempt {_heal_count})", 'error')
                        _ok, _leftover = self._ensure_sentinel()
                        _last_activity = _now
                        if _leftover:
                            # 探测期间读到的字节（可能已含恢复后的哨兵）喂回扫描器
                            _f2, _c2 = _scan_feed(_leftover)
                            if _f2:
                                return_code = _c2 if _c2 is not None else 0
                                _sentinel_seen = True
                                break
                        if _heal_count >= _MAX_HEAL + 2:
                            if output_buffer is not None:
                                output_buffer.append(
                                    "[无法恢复命令完成标记（提示符被改写），已中止本命令]")
                            _sentinel_lost = True
                            if _scan_bytes:
                                _emit(bytes(_scan_bytes))
                                _scan_bytes = bytearray()
                            break

        except Exception as e:
            debug_log(f"Passthrough exception: {e}", 'error')
        finally:
            self._first_exec_done = True
            if not is_windows:
                # 恢复信号 handlers（非主线程或信号已被改动时静默跳过）
                try:
                    # 用 is not None：SIG_DFL 的值是 0，用真值判断会导致
                    # "上一个 handler 是 SIG_DFL"时永远不还原。
                    if old_sigint is not None:
                        signal.signal(signal.SIGINT, old_sigint)
                    if old_sigwinch is not None and hasattr(signal, 'SIGWINCH'):
                        signal.signal(signal.SIGWINCH, old_sigwinch)
                    if old_sigttou is not None and hasattr(signal, 'SIGTTOU'):
                        signal.signal(signal.SIGTTOU, old_sigttou)
                except ValueError:
                    pass
                # 恢复终端到 cooked 模式（与 bash/zsh 一致）
                if _passthrough_entered_raw:
                    try:
                        restore_terminal_attrs()
                    except Exception:
                        pass

        # v9.7+: Check if the safety-net SIGINT handler was triggered
        if not interrupted and _interrupted_flag['value']:
            interrupted = True
            debug_log("Passthrough interrupted via SIGINT safety-net handler")

        if interrupted:
            if output_buffer is not None:
                output_buffer.append("[Interrupted]")
            return -1, full_raw_output

        return return_code, full_raw_output

    def _write_to_master(self, data: bytes):
        """Write data to master PTY (cross-platform)"""
        if platform.system() == "Windows" and self._winpty_handle:
            try:
                self._winpty_handle.write(data.decode('utf-8', errors='replace'))
            except Exception as e:
                debug_log(f"Windows write error: {e}", 'error')
        elif self.master_fd is not None:
            # 循环写满：os.write 可能部分写（PTY 缓冲满），单次写会静默丢字节
            mv = memoryview(data)
            # 总超时兜底：shell 被 SIGSTOP / 卡死时不至于把调用方永久挂住
            _wdeadline = time.time() + _WRITE_TOTAL_TIMEOUT
            while mv:
                if time.time() > _wdeadline:
                    debug_log(f"write_to_master timeout, {len(mv)} bytes dropped", 'error')
                    break
                try:
                    n = os.write(self.master_fd, mv)
                except InterruptedError:
                    continue
                except BlockingIOError:
                    # 非阻塞 fd 且缓冲满 → 等可写再重试，绝不丢字节
                    try:
                        select.select([], [self.master_fd], [], 0.1)
                    except Exception:
                        time.sleep(0.01)
                    continue
                except OSError as e:
                    debug_log(f"Unix write error: {e}", 'error')
                    break
                if n <= 0:
                    break
                mv = mv[n:]

    def _read_from_master(self, timeout: float = 0.01) -> Optional[bytes]:
        """
        Read data from master PTY with optional timeout.
        On Unix: uses non-blocking os.read (or select with timeout).
        On Windows: retrieves data from the reader thread queue.
        """
        if platform.system() == "Windows":
            if self._read_queue is None:
                return None
            try:
                # Wait for data up to timeout seconds
                text = self._read_queue.get(timeout=timeout)
                if text is None:  # Sentinel for thread termination
                    return None
                return text.encode('utf-8', errors='replace')
            except queue.Empty:
                return None
        else:
            # Unix: use select with timeout to avoid blocking
            if self.master_fd is None:
                return None
            try:
                rlist, _, _ = select.select([self.master_fd], [], [], timeout)
                if self.master_fd in rlist:
                    data = os.read(self.master_fd, 4096)
                    if DEBUG_ENABLED:
                        shell_log(data, 'READ')
                    return data
            except (select.error, OSError):
                pass
            return None

    def _reader_thread_func(self):
        """Background thread that reads from winpty handle and puts data into queue."""
        debug_log("Starting Windows reader thread")
        while not self._stop_thread and self._winpty_handle:
            try:
                # winpty read may block, but we rely on process exit to break
                data = self._winpty_handle.read()
                if data:
                    if DEBUG_ENABLED:
                        shell_log(data, 'READ')
                    self._read_queue.put(data)
                else:
                    # No data or process ended
                    if not self._winpty_handle.isalive():
                        break
                    
            except Exception as e:
                debug_log(f"Reader thread error: {e}", 'error')
                break
        self._read_queue.put(None)  # Sentinel
        debug_log("Windows reader thread stopped")

    def _init_shell(self):
        """Initialize shell subprocess and PTY"""
        if platform.system() == "Windows" and WINPTY_AVAILABLE:
            self._init_shell_windows()
        else:
            self._init_shell_unix()
        self._record_start_ticks()

    def _read_proc_stat(self):
        """读 /proc/<pid>/stat，返回 (state_char, starttime) 或 None。

        comm 字段可能含空格/括号 → 用最后一个 ')' 切分。
        starttime 是第 22 字段，在 ')' 之后是第 20 个。
        """
        if not self.pid:
            return None
        try:
            with open(f"/proc/{self.pid}/stat", "rb") as _f:
                _raw = _f.read()
            _r = _raw.rfind(b')')
            if _r < 0:
                return None
            _rest = _raw[_r + 2:].split()
            _state = _rest[0].decode('ascii', 'replace')
            _start = int(_rest[19]) if len(_rest) > 19 else 0
            return _state, _start
        except (OSError, ValueError, IndexError):
            return None

    def _record_start_ticks(self) -> None:
        """记录 shell 进程的 starttime（用于 is_alive 防 PID 复用）。"""
        _st = self._read_proc_stat()
        self._start_ticks = _st[1] if _st else 0

    def _init_shell_windows(self):
        """Initialize shell on Windows using winpty + reader thread."""
        if not WINPTY_AVAILABLE:
            raise RuntimeError("winpty not available. Install pywinpty for Windows support.")

        rows, cols = get_terminal_size()
        debug_log(f"Initializing Windows PTY: {cols}x{rows}")

        # Prepare environment
        env = os.environ.copy()
        if self._extra_vars:
            for var_name, var_value in self._extra_vars.items():
                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', var_name):
                    env[var_name] = str(var_value)

        # 注入哨兵（与 Unix 路径一致；若把 PS1 置空则 bash 类 shell 无哨兵 → 永久阻塞）
        for _k in ('PS1', 'PROMPT_COMMAND', 'PROMPT'):
            env.pop(_k, None)
        env['PS1'] = f'{self._done_marker}:$?\\n'
        if self.shell_name == 'bash':
            env['PROMPT_COMMAND'] = (
                'printf "\\n' + self._done_marker + ':%s\\n" "$?"'
            )

        # Build shell command line based on shell type
        if self.shell_name in ('pwsh', 'powershell'):
            shell_args = [self.shell, '-NoLogo', '-NoProfile', '-NonInteractive']
        elif self.shell_name == 'cmd':
            shell_args = [self.shell]
        elif self.shell_name == 'zsh':
            shell_args = [self.shell, '--no-rcs', '--no-globalrcs', '-f']
        else:
            # For WSL/bash on Windows
            shell_args = [self.shell, '--norc', '--noprofile']

        # 预热 shell 二进制到系统缓存
        try:
            _shell_path = shutil.which(self.shell) or self.shell
            with open(_shell_path, 'rb') as _f:
                _f.read(4096)
        except Exception:
            pass

        try:
            from winpty import PtyProcess
            self._winpty_handle = PtyProcess.spawn(
                shell_args,
                cwd=self.cwd,
                env=env,
                dimensions=(rows, cols)
            )
            self.pid = self._winpty_handle.pid
            # No file descriptor available on Windows
            self.master_fd = None

            # Setup reader thread and queue
            self._read_queue = queue.Queue()
            self._stop_thread = False
            self._read_thread = threading.Thread(target=self._reader_thread_func, daemon=True)
            self._read_thread.start()

            debug_log(f"Windows PTY initialized: PID={self.pid}")

        except Exception as e:
            debug_log(f"Failed to create winpty process: {e}", 'error')
            raise RuntimeError(f"Failed to create winpty process: {e}")

        # Wait for shell to be ready (silently)
        self._wait_for_shell_ready_silent()

    def _init_shell_unix(self):
        """Initialize shell on Unix using standard PTY with proper zsh support"""
        try:
            import pty
        except ImportError:
            raise RuntimeError("pty module not available on this Unix system")

        fd_stdin = sys.stdin.fileno()
        rows, cols = get_terminal_size(fd_stdin)
        debug_log(f"Initializing Unix PTY: {cols}x{rows}")

        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as e:
            debug_log(f"Unable to create PTY: {e}", 'error')
            raise RuntimeError(f"Unable to create PTY: {e}")

        self.master_fd = master_fd

        if HAVE_FCNTL_TERMIOS:
            try:
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                            struct.pack('HHHH', rows, cols, 0, 0))
            except Exception:
                pass
        # 同步全局记录：避免 update_pty_size 误判"尺寸未变"而跳过刷新
        global _current_pty_size
        _current_pty_size = (rows, cols)
        # 预热 shell 二进制到 page cache，加速 os.execvpe()
        try:
            with open(self.shell, 'rb') as _f:
                _f.read(4096)
        except Exception:
            pass

        pid = os.fork()
        if pid == 0:
            # Child process
            try:
                os.close(master_fd)
                os.setsid()
                # 建立控制终端（真 tty 语义：作业控制 / tcsetpgrp / Ctrl+Z）
                if HAVE_FCNTL_TERMIOS:
                    try:
                        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
                    except Exception:
                        pass
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)
                # PTY slave 保留 ECHO，否则 input() 等交互式程序的输入不可见。
                # 命令回显由 _execute_passthrough 的 _echo_skipped 机制过滤。

                env = os.environ.copy()
                env['TERM'] = env.get('TERM', 'xterm-256color')
                # 不导出 LINES/COLUMNS：真实 tty 下二者是 shell 变量、不导出。
                # 导出会让 ncurses（use_env 默认开）优先用 env 而非 TIOCGWINSZ，
                # resize 后即拿到过期尺寸 → 形变。
                # 注意：必须真正从 env 里 pop 掉 —— os.environ.copy() 会原样带过来，
                # _extra_vars 也会把它们重新塞回去（旧代码只在注释里"声明"不导出）。
                env.pop('LINES', None)
                env.pop('COLUMNS', None)
                if self.shell_name == 'bash':
                    # bash 有 PROMPT_COMMAND → PS1 保持干净（空），
                    # 避免提示符本身把 __DONE_x:0 显示在用户终端上。
                    env['PS1'] = ''
                else:
                    env['PS1'] = f'{self._done_marker}:$?\\n'
                env['PROMPT'] = '$P$G'
                # 主哨兵：bash 用 PROMPT_COMMAND（每次提示符前必执行，抗 PS1 改写）
                if self.shell_name == 'bash':
                    env['PROMPT_COMMAND'] = (
                        'printf "\\n' + self._done_marker + ':%s\\n" "$?"'
                    )
                # For zsh compatibility: disable prompt and other features
                env['ZDOTDIR'] = '/dev/null'
                env['HISTFILE'] = '/dev/null'
                env['HISTSIZE'] = '0'
                env['SAVEHIST'] = '0'

                # Inject all collected variables into child process environment
                if self._extra_vars:
                    for var_name, var_value in self._extra_vars.items():
                        if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', var_name):
                            env[var_name] = str(var_value)

                if self.cwd and os.path.isdir(self.cwd):
                    os.chdir(self.cwd)

                # Proper shell initialization for different shells
                if self.shell_name == 'zsh':
                    # 注意：zsh 没有 --norc（那是 bash 的选项）——传了会
                    # "no such option: norc" 直接退出。只用 zsh 自己的开关。
                    os.execvpe(self.shell, [
                        self.shell,
                        '--no-rcs',
                        '--no-globalrcs',
                        '-f'  # no startup files
                    ], env)
                elif self.shell_name in ('bash', 'sh', 'dash'):
                    os.execvpe(self.shell, [self.shell, '--norc', '--noprofile'], env)
                elif self.shell_name == 'fish':
                    os.execvpe(self.shell, [self.shell, '--no-config', '--private'], env)
                elif self.shell_name in ('pwsh', 'powershell'):
                    os.execvpe(self.shell, [
                        self.shell,
                        '-NoLogo',
                        '-NoProfile',
                        '-NonInteractive',
                        '-Command', '-'
                    ], env)
                else:
                    os.execvpe(self.shell, [self.shell], env)
            except Exception as e:
                debug_log(f"Child process exec failed: {e}", 'error')
                os._exit(1)
        else:
            # Parent process
            os.close(slave_fd)
            self.pid = pid
            debug_log(f"Unix PTY initialized: PID={pid}")
            
            # Wait for shell initialization to complete (silently, no output leakage)
            self._wait_for_shell_ready_silent()
            # 再清一次残留：shell 初始化末次提示符（含哨兵）可能晚于 ready 探测到达，
            # 残留在 PTY 缓冲里会在下一屏被转发 → 用户看到凭空一行 __DONE_x:0。
            self._drain_output(max_iterations=20, quiet_timeout=0.03)

    def _wait_for_shell_ready_silent(self):
        """Silently wait for shell initialization, discarding all output"""
        if self.master_fd is None and self._winpty_handle is None:
            return

        ready_marker = f"__READY_{uuid.uuid4().hex[:8]}__\n"

        if self.shell_name in ('pwsh', 'powershell'):
            test_cmd = f"Write-Host '{ready_marker}'\n"
        elif self.shell_name == 'cmd':
            test_cmd = f"echo {ready_marker}\n"
        else:
            test_cmd = f"printf '%s\\n' '{ready_marker}'\n"

        debug_log(f"Waiting for shell ready, marker: {ready_marker.strip()}")

        try:
            self._write_to_master(test_cmd.encode('utf-8'))
            start_time = time.time()
            while time.time() - start_time < _SHELL_READY_TIMEOUT:
                data = self._read_from_master(timeout=5.0)
                if data:
                    text = data.decode('utf-8', errors='replace')
                    if ready_marker.strip() in text:
                        debug_log("Shell ready detected")
                        self._drain_output()
                        return
        except OSError as e:
            debug_log(f"Error waiting for shell ready: {e}", 'error')
            pass

        debug_log("Shell ready timeout, continuing anyway")
        self._drain_output()

    def _setup_prompt(self):
        """Set PS1 to unique marker for command completion detection.
        The marker is printed by the shell after EVERY command (including
        commands killed by SIGINT), enabling reliable end-of-command detection."""
        if self.master_fd is None and self._winpty_handle is None:
            return

        # bash/sh/dash 的哨兵已在 fork 时经 env 传入；这里再幂等下发一次，
        # 防止 /etc/bash.bashrc、系统 rc 等把 PS1/PROMPT_COMMAND 覆盖掉
        # （覆盖后哨兵丢失 → 首条命令要等 1.5s 自愈，或干脆卡住）。
        if self.shell_name in ('bash', 'sh', 'dash'):
            try:
                self._write_to_master(
                    self._sentinel_commands(self.shell_name).encode('utf-8'))
                self._drain_output()
            except OSError as e:
                debug_log(f"Failed to (re)set sentinel for {self.shell_name}: {e}", 'error')
            return

        debug_log(f"Setting up prompt for shell: {self.shell_name}, done_marker={self._done_marker}")

        if self.shell_name == 'fish':
            # fish：单引号内不做插值，必须用 printf 的 %s + 参数传 $status。
            setup_cmd = (
                f"function fish_prompt; printf '\\n{self._done_marker}:%s\\n' $status; end\n"
                "function fish_right_prompt; printf ''; end\n"
            )
        elif self.shell_name == 'zsh':
            # zsh：哨兵放 precmd_functions（**追加**，不覆盖用户已有 precmd）。
            # 单引号内不插值 → 用 %s + 参数传 $?。
            # （旧代码写 '$?' 在单引号里 → 哨兵恒为字面 "$?"，正则匹配不到
            #   → zsh 下命令永不返回，这是必须修掉的旧 bug。）
            setup_cmd = (
                "unsetopt PROMPT_CR 2>/dev/null\n"
                "unsetopt PROMPT_SP 2>/dev/null\n"
                f"__onyx_sentinel() {{ printf '\\n{self._done_marker}:%s\\n' $?; }}\n"
                "typeset -ga precmd_functions\n"
                "precmd_functions+=(__onyx_sentinel)\n"
                "PROMPT=''\n"
                "RPROMPT=''\n"
            )
        elif self.shell_name in ('pwsh', 'powershell'):
            setup_cmd = (
                f"function prompt {{ \"{self._done_marker}:$LASTEXITCODE`n\" }}\n"
            )
        elif self.shell_name == 'cmd':
            # cmd 的 prompt 无法动态取退出码（%ERRORLEVEL% 只在设置 prompt 时
            # 展开一次），因此只放「无退出码哨兵」—— 检测器接受裸哨兵
            # （退出码视为未知），保证 cmd 下命令能正常结束而不是永久阻塞。
            setup_cmd = f"prompt {self._done_marker}$G\n@echo off\n"
        else:
            # bash and other sh-compatible shells
            setup_cmd = f"PROMPT_COMMAND=''\nPS1='{self._done_marker}:$?\\n'\n"

        try:
            self._write_to_master(setup_cmd.encode('utf-8'))
            self._drain_output()
        except OSError as e:
            debug_log(f"Failed to setup prompt: {e}", 'error')
            pass

    def _detect_live_shell_name(self) -> str:
        """探测 shell 进程**当前**实际是什么 shell。

        `exec zsh` 会直接替换掉 shell 进程 → self.shell_name 过期。
        Linux/Android 用 /proc/<pid>/comm 读取；失败回退 self.shell_name。
        """
        if self.pid:
            try:
                with open(f"/proc/{self.pid}/comm", "r") as _f:
                    _n = _f.read().strip().lower()
                if _n:
                    return os.path.basename(_n)
            except (OSError, IOError):
                pass
        return self.shell_name

    def _sentinel_commands(self, shell_name: str) -> str:
        """按 shell 类型生成「(重)设哨兵」的脚本文本。

        bash 用 PROMPT_COMMAND（每次提示符前必执行 → 抗 PS1 改写）；
        zsh 用 precmd_functions 追加；fish 用 fish_prompt；
        pwsh 用 prompt 函数；cmd 用无退出码 prompt；其余用 PS1。
        """
        m = self._done_marker
        if shell_name == 'fish':
            return (
                f"function fish_prompt; printf '\\n{m}:%s\\n' $status; end\n"
                "function fish_right_prompt; printf ''; end\n"
            )
        if shell_name == 'zsh':
            return (
                "unsetopt PROMPT_CR 2>/dev/null\n"
                "unsetopt PROMPT_SP 2>/dev/null\n"
                f"__onyx_sentinel() {{ printf '\\n{m}:%s\\n' $?; }}\n"
                "typeset -ga precmd_functions\n"
                "precmd_functions+=(__onyx_sentinel)\n"
                "PROMPT=''\nRPROMPT=''\n"
            )
        if shell_name in ('pwsh', 'powershell'):
            return f"function prompt {{ \"{m}:$LASTEXITCODE`n\" }}\n"
        if shell_name == 'cmd':
            return f"prompt {m}$G\n@echo off\n"
        if shell_name == 'bash':
            # PS1 保持为空：哨兵只由 PROMPT_COMMAND 打印（提示符干净，
            # 不会把 __DONE_x:0 显示给用户）。
            return (
                'PROMPT_COMMAND=\'printf "\\n' + m + ':%s\\n" "$?"\'\n'
                "PS1=''\n"
            )
        # sh / dash / ksh 等：只有 PS1 可用
        return f"PS1='{m}:$?\\n'\n"

    def _ensure_sentinel(self):
        """确保 shell 仍在输出哨兵；失效则重新下发并探测。

        返回 (ok, leftover_bytes)：leftover 是探测期间从 PTY 读到的原始字节，
        调用方应把它喂给扫描器（其中可能就含恢复后的哨兵）。

        触发场景：用户/AI 执行 `PS1=...`、`unset PROMPT_COMMAND`、
        `exec zsh`（换 shell）等，导致命令完成标记消失 → 若不修复，
        命令将永远等不到结束信号（旧实现无超时 → 永久阻塞）。
        """
        leftover = b""
        if self.master_fd is None and self._winpty_handle is None:
            return False, leftover
        try:
            live = self._detect_live_shell_name()
            if live and live != self.shell_name:
                debug_log(f"Shell replaced: {self.shell_name} -> {live}")
                self.shell_name = live
            self._write_to_master(self._sentinel_commands(live).encode('utf-8'))
            self._drain_output()
            # 空行触发一次提示符（PROMPT_COMMAND / precmd 会打印哨兵）
            self._write_to_master(b"\n")
            deadline = time.time() + 1.0
            pat = re.compile(
                rb'(?:^|\n)' + re.escape(self._done_marker.encode('utf-8'))
                + rb'(?::(-?\d+))?'
            )
            while time.time() < deadline:
                chunk = self._read_from_master(timeout=0.1)
                if not chunk:
                    continue
                leftover += chunk
                if pat.search(leftover):
                    debug_log("Sentinel restored")
                    return True, leftover
            debug_log("Sentinel restore probe failed", 'error')
            return False, leftover
        except Exception as e:
            debug_log(f"_ensure_sentinel failed: {e}", 'error')
            return False, leftover

    def _drain_output(self, max_iterations: int = 8, quiet_timeout: float = 0.01):
        """Consume all pending output (non-blocking).

        quiet_timeout: 单次读取等待时长。默认 2ms（够快，用于常规收尾）；
        首条命令前会传更大的值，耐心等到"真的安静"为止，避免 shell 初始化
        收尾输出（含 __DONE__ PS1）稍后才到、被下一条命令误判为完成 marker。
        """
        drained = 0
        empty_count = 0
        for _ in range(max_iterations):
            data = self._read_from_master(timeout=quiet_timeout)
            if not data:
                empty_count += 1
                if empty_count >= 2:
                    break
                continue
            empty_count = 0
            drained += len(data)
        if drained > 0:
            debug_log(f"Drained {drained} bytes of pending output")

    def _clear_screen(self):
        pass

    def _disable_echo(self):
        """抑制 shell 的命令回显（由 _execute_passthrough 的智能 echo 跳过处理）"""
        if self.master_fd is None and self._winpty_handle is None:
            return
        try:
            if self.shell_name == 'cmd':
                self._write_to_master(b"@echo off\n")
                self._drain_output()
        except OSError as e:
            debug_log(f"Failed to disable echo: {e}", 'error')
            pass

    def set_cwd(self, cwd: str):
        """Update working directory"""
        if cwd and os.path.isdir(cwd):
            debug_log(f"Changing CWD to: {cwd}")
            self.cwd = cwd
            if self.master_fd is not None or self._winpty_handle is not None:
                try:
                    if self.shell_name in ('pwsh', 'powershell'):
                        cd_cmd = f"Set-Location '{cwd}'\n"
                    else:
                        cd_cmd = f"cd '{cwd}'\n"
                    self._write_to_master(cd_cmd.encode('utf-8'))
                    self._drain_output()
                except OSError as e:
                    debug_log(f"Failed to change CWD: {e}", 'error')
                    pass

    def get_current_cwd(self) -> Optional[str]:
        """
        Get current working directory from shell process.

        优先使用 /proc/<pid>/cwd（Linux/Android，零延迟，无 PTY 污染风险），
        回退到 PTY marker 方式（Windows / macOS / 非 Linux）。

        Returns:
            Current directory path or None if unable to retrieve
        """
        if self.pid is None or self._dead:
            return None

        # === 快速路径：/proc/<pid>/cwd（Linux / Android） ===
        proc_cwd = f"/proc/{self.pid}/cwd"
        try:
            if os.path.islink(proc_cwd) or os.path.exists(proc_cwd):
                cwd = os.readlink(proc_cwd)
                if cwd and os.path.isdir(cwd):
                    debug_log(f"Got CWD via /proc: {cwd}")
                    return os.path.abspath(cwd)
        except (OSError, ValueError):
            pass  # 回退到 PTY marker

        # === 回退：PTY marker 方式（Windows / macOS / proc 不可用） ===
        if (self.master_fd is None and self._winpty_handle is None):
            return None

        marker = generate_var_marker()
        start_marker = f"__CWD_START_{marker}__"
        end_marker = f"__CWD_END_{marker}__"

        if self.shell_name in ('pwsh', 'powershell'):
            read_cmd = f"Write-Host '{start_marker}'; (Get-Location).Path; Write-Host '{end_marker}'\n"
        elif self.shell_name == 'cmd':
            read_cmd = f"echo {start_marker}\ncd\necho {end_marker}\n"
        elif self.shell_name == 'fish':
            read_cmd = f"printf '%s\\n' '{start_marker}'; pwd; printf '%s\\n' '{end_marker}'\n"
        else:
            read_cmd = f"printf '%s\\n' '{start_marker}'; pwd; printf '%s\\n' '{end_marker}'\n"

        try:
            # Clear any pending output before reading cwd
            self._drain_output()
            self._write_to_master(read_cmd.encode('utf-8'))

            full_output = ""
            start_time = time.time()
            while time.time() - start_time < 3.0:
                data = self._read_from_master(timeout=0.05)
                if data:
                    text = data.decode('utf-8', errors='replace')
                    full_output += text
                    if end_marker in full_output:
                        start_idx = full_output.find(start_marker)
                        end_idx = full_output.find(end_marker)
                        if start_idx != -1 and end_idx != -1:
                            cwd_part = full_output[start_idx + len(start_marker):end_idx]
                            lines = cwd_part.strip().split('\n')
                            # Find the first non-empty line that looks like a path
                            for line in lines:
                                line = line.strip()
                                if line and not line.startswith('__') and len(line) > 1:
                                    # Simple path validation
                                    if (line[0] == '/' or (len(line) >= 2 and line[1] == ':')):
                                        debug_log(f"Got CWD: {line}")
                                        return os.path.abspath(line)
                            # Fallback: return the first non-empty line
                            if lines:
                                debug_log(f"Got CWD (fallback): {lines[0].strip()}")
                                return os.path.abspath(lines[0].strip())
                        break
            debug_log("Failed to get CWD via PTY")
            return None
        except OSError as e:
            debug_log(f"Error getting CWD: {e}", 'error')
            return None

    def get_var_value(self, var_name: str) -> Optional[str]:
        """
        Read variable value from shell process

        Since all variables are already injected into the child process environment,
        we can directly read environment variables to get all variable values.

        Args:
            var_name: Variable name (without $ prefix)

        Returns:
            Variable value string, None if unable to read
        """
        if (self.master_fd is None and self._winpty_handle is None) or self.pid is None or self._dead:
            return None

        marker = generate_var_marker()
        start_marker = f"__VAR_START_{marker}__"
        end_marker = f"__VAR_END_{marker}__"

        # Simplified reading: use echo or printf to output variable value
        # Since variables are already exported to environment, all shells can read them directly
        if self.shell_name in ('pwsh', 'powershell'):
            read_cmd = (
                f"Write-Host '{start_marker}'; "
                f"if (Test-Path env:{var_name}) {{ Write-Host $env:{var_name} }}; "
                f"Write-Host '{end_marker}'\n"
            )
        elif self.shell_name == 'cmd':
            read_cmd = (
                f"echo {start_marker}\n"
                f"echo %{var_name}%\n"
                f"echo {end_marker}\n"
            )
        elif self.shell_name == 'fish':
            read_cmd = (
                f"printf '%s\\n' '{start_marker}'; "
                f"if set -q {var_name}; printf '%s' \"${var_name}\"; end; "
                f"printf '%s\\n' '{end_marker}'\n"
            )
        else:
            # bash/zsh/sh/dash: use ${var:-} syntax for safe reading
            read_cmd = (
                f"printf '%s\\n' '{start_marker}'; "
                f"eval 'printf \"%s\" \"${{{var_name}}}\"'; "
                f"printf '%s\\n' '{end_marker}'\n"
            )

        try:
            self._drain_output()
            self._write_to_master(read_cmd.encode('utf-8'))

            full_output = ""
            start_time = time.time()
            while time.time() - start_time < 3.0:
                data = self._read_from_master(timeout=0.05)
                if data:
                    text = data.decode('utf-8', errors='replace')
                    full_output += text
                    if end_marker in full_output:
                        start_idx = full_output.find(start_marker)
                        end_idx = full_output.find(end_marker)
                        if start_idx != -1 and end_idx != -1:
                            value = full_output[start_idx + len(start_marker):end_idx]
                            value = value.strip()
                            if value:
                                lines = value.split('\n')
                                clean_lines = [l for l in lines
                                              if not l.strip().startswith('__')
                                              and l.strip() not in ('printf', 'eval', 'echo')]
                                value = '\n'.join(clean_lines).strip()
                            debug_log(f"Got var {var_name}: {repr(value)}")
                            return value if value else None
                        break
            debug_log(f"Failed to get var {var_name}")
            return None
        except OSError as e:
            debug_log(f"Error getting var {var_name}: {e}", 'error')
            return None

    def get_functions_list(self) -> List[str]:
        """Read function name list from shell process"""
        if (self.master_fd is None and self._winpty_handle is None) or self.pid is None or self._dead:
            return []

        marker = generate_func_marker()
        start_marker = f"__FUNC_START_{marker}__"
        end_marker = f"__FUNC_END_{marker}__"

        if self.shell_name in ('pwsh', 'powershell'):
            get_cmd = (
                f"Write-Host '{start_marker}'; "
                f"Get-ChildItem Function: | ForEach-Object {{ Write-Host $_.Name }}; "
                f"Write-Host '{end_marker}'\n"
            )
        elif self.shell_name == 'fish':
            get_cmd = (
                f"printf '%s\\n' '{start_marker}'; "
                f"functions -n; "
                f"printf '%s\\n' '{end_marker}'\n"
            )
        elif self.shell_name == 'cmd':
            get_cmd = (
                f"echo {start_marker}\n"
                f"echo {end_marker}\n"
            )
        else:
            get_cmd = (
                f"printf '%s\\n' '{start_marker}'; "
                f"declare -F 2>/dev/null | awk '{{print $3}}' || "
                f"typeset -f + 2>/dev/null | grep '^[a-zA-Z_]' | awk '{{print $1}}'; "
                f"printf '%s\\n' '{end_marker}'\n"
            )

        try:
            self._drain_output()
            self._write_to_master(get_cmd.encode('utf-8'))

            full_output = ""
            start_time = time.time()
            while time.time() - start_time < 3.0:
                data = self._read_from_master(timeout=0.05)
                if data:
                    text = data.decode('utf-8', errors='replace')
                    full_output += text
                    if end_marker in full_output:
                        start_idx = full_output.find(start_marker)
                        end_idx = full_output.find(end_marker)
                        if start_idx != -1 and end_idx != -1:
                            content = full_output[start_idx + len(start_marker):end_idx].strip()
                            functions = [f.strip() for f in content.split('\n') if f.strip()]
                            debug_log(f"Got {len(functions)} functions")
                            return functions
                        break
            debug_log("Failed to get functions")
            return []
        except OSError as e:
            debug_log(f"Error getting functions: {e}", 'error')
            return []

    def execute(
        self,
        cmd: str,
        output_buffer: List[str],
        log_info: Optional[Callable] = None,
        log_error: Optional[Callable] = None,
        passthrough: bool = False
    ) -> Tuple[int, str]:
        """
        Execute a command in the persistent shell（统一 TTY 通道）。
        
        passthrough 参数保留但忽略：所有命令走 PTY 直通模式，
        使用 PS1 marker 检测完成，无限接近原生 TTY 行为。
        """
        debug_log(f"Executing command (passthrough={passthrough}): {repr(cmd)}")
        
        if (self.master_fd is None and self._winpty_handle is None) or self.pid is None or self._dead:
            if log_error:
                try:
                    log_error("Shell is dead, rebuilding...", "")
                except TypeError:
                    log_error("Shell is dead, rebuilding...")
            debug_log("Shell is dead, rebuilding...")
            try:
                self.cleanup()
                self._init_shell()
                self._disable_echo()
                self._setup_prompt()
                self._inject_extra_vars()
                self._clear_screen()
                debug_log("Shell rebuilt successfully")
            except Exception as e:
                debug_log(f"Failed to rebuild shell: {e}", 'error')
                if log_error:
                    try:
                        log_error(f"Failed to rebuild shell: {e}", "")
                    except TypeError:
                        log_error(f"Failed to rebuild shell: {e}")
                return -1, ""
    
        return_code = -1
    
        # 统一使用 passthrough 模式（PS1 marker + PTY 直通）
        # 所有命令（TUI/系统/工具）共用 TTY 通道，能力最强
        return self._execute_passthrough(cmd, output_buffer, log_info, log_error)

    def cleanup(self):
        """Terminate shell process and release resources"""
        debug_log("Cleaning up persistent shell")
        
        # Stop reader thread first
        if platform.system() == "Windows":
            self._stop_thread = True
            if self._read_thread and self._read_thread.is_alive():
                self._read_thread.join(timeout=1.0)

        if self.pid:
            if platform.system() == "Windows":
                # Windows: use taskkill to kill the entire process tree
                try:
                    import subprocess as _sp
                    _sp.run(["taskkill", "/T", "/F", "/PID", str(self.pid)],
                            capture_output=True, timeout=5)
                except Exception:
                    pass
            else:
                # 先确认这个 PID 仍属于我们的 shell（防 PID 复用 → 误杀无关进程）
                _st = self._read_proc_stat()
                if (_st is not None and self._start_ticks
                        and _st[1] and _st[1] != self._start_ticks):
                    debug_log(f"PID {self.pid} reused; skip killing", 'error')
                    try:
                        os.waitpid(self.pid, os.WNOHANG)
                    except OSError:
                        pass
                else:
                    try:
                        os.killpg(self.pid, signal.SIGTERM)
                    except OSError:
                        pass
                    try:
                        os.kill(self.pid, signal.SIGTERM)
                        for _ in range(5):
                            try:
                                pid_result, status = os.waitpid(self.pid, os.WNOHANG)
                                if pid_result:
                                    break
                            except OSError:
                                break

                        try:
                            os.kill(self.pid, signal.SIGKILL)
                            os.waitpid(self.pid, 0)
                        except OSError:
                            pass
                    except OSError:
                        pass
            self.pid = None

        if self.master_fd is not None and platform.system() != "Windows":
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

        if self._winpty_handle:
            try:
                self._winpty_handle.close()
            except Exception:
                pass
            self._winpty_handle = None

        if self._read_queue:
            self._read_queue = None

        self._dead = True
        debug_log("Shell cleanup complete")

    def is_alive(self) -> bool:
        """Check if shell is alive.

        优先检查 /proc/<pid>/stat（Android/Linux 上 os.kill 可能因 SELinux 失败），
        回退到 os.kill(pid, 0)。

        加固点（旧实现只用 os.path.isdir('/proc/<pid>')，有两个误判来源）：
          - **僵尸进程**：进程已退出但未被 wait() 回收时 /proc/<pid> 依然存在
            → 旧实现永远返回 True，shell 已死却被当成活着；
          - **PID 复用**：原进程已消失、PID 被别的进程占用 → /proc/<pid> 存在
            → 误判为「还活着」，后续写入落到无关进程的终端。
        现在：读 stat 判断 state（'Z' = 僵尸 → 判死），并与创建时记录的
        starttime 比对（不一致 → PID 被复用 → 判死）。
        """
        if self._dead or self.pid is None:
            return False
        if platform.system() == "Windows":
            return self._winpty_handle is not None and self._winpty_handle.isalive()
        _st = self._read_proc_stat()
        if _st is not None:
            _state, _start = _st
            if _state == 'Z':
                debug_log(f"Shell pid {self.pid} is a zombie", 'error')
                self._dead = True
                return False
            if self._start_ticks and _start and _start != self._start_ticks:
                debug_log(f"Shell pid {self.pid} reused "
                          f"(starttime {_start} != {self._start_ticks})", 'error')
                self._dead = True
                return False
            return True
        # /proc 不可用（macOS / 非 Linux）→ 回退 os.kill
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            self._dead = True
            return False

    def __del__(self):
        # __del__ 可能在解释器退出/GC 期间被调用，此时模块全局（os/platform…）
        # 可能已被置空，且异常无法向上传播（只会打印 "Exception ignored"）。
        try:
            self.cleanup()
        except Exception:
            pass


# ======================================================================
# Public Interface
# ======================================================================
def _get_persistent_shell(cwd: Optional[str] = None) -> PersistentShell:
    """Thread-safe get or create persistent shell instance（性能优化版）"""
    global _persistent_shell
    if _persistent_shell is None or not _persistent_shell.is_alive():
        with _shell_lock:
            if _persistent_shell is None or not _persistent_shell.is_alive():
                if _persistent_shell:
                    _persistent_shell.cleanup()
                start_cwd = cwd or _get_cwd_cached()
                # 背景预热时无需采集调用者变量（栈回溯开销大且无用）
                _persistent_shell = PersistentShell(cwd=start_cwd)
    if _persistent_shell is not None:
        target_cwd = cwd or _get_cwd_cached()
        if _persistent_shell.cwd != target_cwd:
            _persistent_shell.set_cwd(target_cwd)
    return _persistent_shell


def get_persistent_shell_raw() -> Optional[PersistentShell]:
    """返回持久 shell 实例，但**不做任何 cwd 同步副作用**。

    与 _get_persistent_shell 的区别：本函数只读取全局实例，不会因为
    ps.cwd 与当前 cwd 不一致而向 shell 补发 `cd`（补发 cd 会覆盖 shell
    的 OLDPWD，破坏 `cd -`）。供 _sync_cwd_from_shell 等「只拿实例、
    只改属性」的场景使用。
    """
    return _persistent_shell


def get_var_from_shell(var_name: str) -> Optional[str]:
    """Read variable value from persistent shell"""
    global _persistent_shell
    if _persistent_shell is None or not _persistent_shell.is_alive():
        _get_persistent_shell()
    if _persistent_shell:
        return _persistent_shell.get_var_value(var_name)
    return None


def get_functions_from_shell() -> List[str]:
    """Read function name list from persistent shell"""
    global _persistent_shell
    if _persistent_shell is None or not _persistent_shell.is_alive():
        _get_persistent_shell()
    if _persistent_shell:
        return _persistent_shell.get_functions_list()
    return []


def get_shell_cwd() -> Optional[str]:
    """Get current working directory from persistent shell.
    
    直接读 shell CWD（优先 /proc/<pid>/cwd），不依赖 os.kill(pid, 0)
    因为在 Android/Termux 等受限环境中 kill 可能因 SELinux 失败。
    """
    global _persistent_shell
    if _persistent_shell is None:
        _get_persistent_shell()
    if _persistent_shell:
        return _persistent_shell.get_current_cwd()
    return None


def is_shell_alive() -> bool:
    """Check if shell is alive"""
    global _persistent_shell
    return _persistent_shell is not None and _persistent_shell.is_alive()


# ======================================================================
# AI 执行模式：subprocess 直跑（避免 PTY 终端污染 + 可靠退出码）
# ======================================================================
# AI 触发的命令（RunCommand 工具）不走持久 shell PTY：
#   - PTY 会混入命令回显、bracketed-paste 控制序列（[?2004l）等终端垃圾，
#     污染 AI 工具结果与 library 记录；
#   - subprocess 直接捕获 stdout/stderr + 真实退出码，输出干净可解析。
# 交互式程序（vim/top/ssh 等）需要 TTY，仍回退 PTY 路径。

# 需要 TTY 的交互式命令（subprocess 无 TTY 会挂死/报错）
_AI_INTERACTIVE_TOKENS = frozenset({
    "vim", "vi", "nvim", "nano", "emacs", "top", "htop", "btop", "less",
    "more", "watch", "man", "ssh", "telnet", "ftp", "sftp", "mc", "ranger",
    "screen", "tmux", "fzf", "psql", "mysql", "sqlite3", "redis-cli",
    "gdb", "lldb", "dialog", "whiptail",
})


def _exec_ai_subprocess(cmd: str, output_buffer: List[str],
                        cwd: Optional[str]) -> Optional[int]:
    """AI 执行模式：subprocess 直跑命令。

    返回退出码；命令属于交互式（需要 TTY）时返回 None 让调用方回退 PTY。

    2026-09 用户决策：AI 运行中按 Ctrl+C 只杀命令、不杀 AI——
    子进程放独立进程组（start_new_session），临时 SIGINT handler 只把信号
    转发到命令进程组；命令结束后恢复原 handler。AI 会话的全局中断标志
    （mcp_state._AI_INTERRUPTED）不被置位 → 下一次 API 调用正常 → AI 循环继续。
    """
    import subprocess as _sp
    import signal as _sig
    _head = (cmd or "").strip().split(None, 1)[0].lower() if (cmd or "").strip() else ""
    if _head and os.path.basename(_head) in _AI_INTERACTIVE_TOKENS:
        return None  # 交互式命令 → 回退 PTY

    _proc = None
    _old_sigint = None
    _interrupted = {'value': False}

    def _kill_cmd_process():
        """杀掉命令进程组（bash + 其子孙），尽力而为。"""
        if _proc is None:
            return
        try:
            os.killpg(os.getpgid(_proc.pid), _sig.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError, AttributeError):
            try:
                _proc.kill()
            except Exception:
                pass

    def _cmd_sigint_handler(signum, frame):
        """AI 执行模式下 Ctrl+C：把信号转发给命令进程组（重复按逐级升级），
        不置位任何全局中断标志（AI 循环继续）。

        升级：第 1 次 SIGINT → 第 2 次 SIGTERM → 第 3 次及以后 SIGKILL ——
        解决「命令忽略 SIGINT / 卡在不可中断状态 → Ctrl+C 杀不掉」的问题。
        """
        _interrupted['value'] = True
        interrupt_active_ai_cmds()

    try:
        _old_sigint = _sig.signal(_sig.SIGINT, _cmd_sigint_handler)
    except ValueError:
        _old_sigint = None  # 非主线程无法装 handler → 保持原样（子进程独立组，Ctrl+C 打不到它）

    try:
        _proc = _sp.Popen(
            cmd, shell=True, stdout=_sp.PIPE, stderr=_sp.PIPE,
            # 显式指定解释器：默认 /bin/sh（dash）不支持 source / [[ ]] 等
            # bash 扩展 → 用 bash（Windows 上传 None，走系统默认）。
            executable=(None if platform.system() == "Windows"
                        else _subprocess_shell()),
            text=True, errors="replace", cwd=cwd,
            start_new_session=True,  # 独立进程组：终端 Ctrl+C 不直接打到命令
        )
        register_ai_cmd(_proc)   # 登记活跃命令：主线程 Ctrl+C handler 可据此转发
        _out_b, _err_b = _proc.communicate(timeout=600)
        _out = (_out_b or "").rstrip()
        _err = (_err_b or "").rstrip()
    except _sp.TimeoutExpired:
        _kill_cmd_process()
        try:
            _proc.wait(timeout=5)      # 回收，避免僵尸
        except Exception:
            pass
        output_buffer.append("[AI 命令执行超时（>600s），已终止]")
        return 124
    except KeyboardInterrupt:
        # 兜底：handler 未生效（非主线程）时默认 SIGINT 处理抛出的 KeyboardInterrupt
        _kill_cmd_process()
        output_buffer.append("[命令被用户中断]")
        return -1
    except Exception as _e:
        _kill_cmd_process()
        output_buffer.append(f"[AI 命令执行异常: {_e}]")
        return -1
    finally:
        unregister_ai_cmd(_proc)
        if _old_sigint is not None:
            try:
                _sig.signal(_sig.SIGINT, _old_sigint)
            except ValueError:
                pass

    if _interrupted['value']:
        # handler 已转发 SIGINT：命令被用户中断（不置位任何全局标志 → AI 循环继续）
        output_buffer.append("[命令被用户中断]")
        return -1

    # 写回 output_buffer（供 is_tool 缓存 / AI_TOOL_OUTPUT_CACHE 使用）
    if _out:
        output_buffer.append(_out)
        try:
            sys.stdout.write(_out + "\n")  # 经 RealTimeOutputCatcher 实时显示前 N 行
            sys.stdout.flush()
        except Exception:
            pass
    if _err:
        output_buffer.append(_err)
        try:
            sys.stderr.write(_err + "\n")
            sys.stderr.flush()
        except Exception:
            pass
    return _proc.returncode


def run_cmd_sync(
    cmd: str,
    request_id: str,
    is_tool: bool = False,
    tool_perm: int = 3,
    sys_type: str = None,
    check_tool_permission_func: Optional[Callable] = None,
    user_mode: Any = None,
    log_info_func: Optional[Callable] = None,
    log_error_func: Optional[Callable] = None,
    AI_TOOL_OUTPUT_CACHE: Optional[Dict] = None,
    is_interactive_command_func: Optional[Callable] = None,
    user_interactive_cmds: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    passthrough: bool = False
) -> int:
    """Execute command synchronously (based on persistent shell)"""
    global AI_LAST_EXIT_CODE
    _ = is_interactive_command_func
    _ = user_interactive_cmds

    output_buffer: List[str] = []

    try:
        debug_log(f"run_cmd_sync: {repr(cmd)}, request_id={request_id}")
        
        if log_info_func:
            try:
                log_info_func(f"Executing command: {cmd}", request_id)
            except TypeError:
                log_info_func(f"Executing command: {cmd}")

        if cwd is None:
            cwd = _get_cwd_cached()

        # ── AI 执行模式：subprocess 直跑（避免 PTY 终端污染 + 可靠退出码）──
        if AI_EXECUTION_MODE:
            _ai_ret = _exec_ai_subprocess(cmd, output_buffer, cwd)
            if _ai_ret is not None:
                if _ai_ret != 0:
                    if log_error_func:
                        try:
                            log_error_func(f"Command failed (exit code {_ai_ret}): {cmd}", request_id)
                        except TypeError:
                            log_error_func(f"Command failed (exit code {_ai_ret}): {cmd}")
                else:
                    if log_info_func:
                        try:
                            log_info_func(f"Command succeeded (exit code 0): {cmd}", request_id)
                        except TypeError:
                            log_info_func(f"Command succeeded (exit code 0): {cmd}")
                if is_tool and AI_TOOL_OUTPUT_CACHE is not None:
                    full_output = "".join(output_buffer) if output_buffer else ""
                    AI_TOOL_OUTPUT_CACHE[request_id] = full_output.strip() or "[No output]"
                AI_LAST_EXIT_CODE = _ai_ret
                return _ai_ret
            # 交互式命令 → 回退下方 PTY 路径
            debug_log(f"AI interactive command fallback to PTY: {repr(cmd)}")

        shell = _get_persistent_shell(cwd=cwd)

        # 交互式命令回退 PTY：也登记进中断注册表 —— 否则 Ctrl+C 打不到它
        # （这就是「有时 Ctrl+C 杀不掉 AI 正在执行的命令」的一个来源）。
        _pty_proxy = None
        if AI_EXECUTION_MODE:
            try:
                _pty_proxy = PtyCmdProxy(shell)
                register_ai_cmd(_pty_proxy)
            except Exception:
                _pty_proxy = None
        try:
            with _shell_lock:
                return_code, raw_output = shell.execute(
                    cmd,
                    output_buffer,
                    log_info=log_info_func,
                    log_error=log_error_func,
                )
        finally:
            if _pty_proxy is not None:
                unregister_ai_cmd(_pty_proxy)

        if AI_EXECUTION_MODE:
            AI_LAST_EXIT_CODE = return_code

        if return_code != 0:
            if log_error_func:
                try:
                    log_error_func(f"Command failed (exit code {return_code}): {cmd}", request_id)
                except TypeError:
                    log_error_func(f"Command failed (exit code {return_code}): {cmd}")
        else:
            if log_info_func:
                try:
                    log_info_func(f"Command succeeded (exit code {return_code}): {cmd}", request_id)
                except TypeError:
                    log_info_func(f"Command succeeded (exit code {return_code}): {cmd}")

        if is_tool and AI_TOOL_OUTPUT_CACHE is not None:
            full_output = "".join(output_buffer) if output_buffer else ""
            AI_TOOL_OUTPUT_CACHE[request_id] = full_output.strip() or "[No output]"

        return return_code

    except KeyboardInterrupt:
        print("\n\033[33mCtrl+C\033[0m")
        debug_log("run_cmd_sync interrupted by Ctrl+C")
        if AI_EXECUTION_MODE:
            AI_LAST_EXIT_CODE = -1
        if log_error_func:
            try:
                log_error_func(f"User interrupted command: {cmd}", request_id)
            except TypeError:
                log_error_func(f"User interrupted command: {cmd}")
        if is_tool and AI_TOOL_OUTPUT_CACHE is not None:
            AI_TOOL_OUTPUT_CACHE[request_id] = "[Interrupted]"
        return -1
    except Exception as e:
        err_msg = str(e)
        print(f"\033[91m{err_msg}\033[0m")
        debug_log(f"run_cmd_sync exception: {err_msg}\n{traceback.format_exc()}", 'error')
        if AI_EXECUTION_MODE:
            AI_LAST_EXIT_CODE = -1
        if log_error_func:
            try:
                log_error_func(f"Command execution exception: {err_msg}, command: {cmd}", request_id)
            except TypeError:
                log_error_func(f"Command execution exception: {err_msg}, command: {cmd}")
        if is_tool and AI_TOOL_OUTPUT_CACHE is not None:
            AI_TOOL_OUTPUT_CACHE[request_id] = f"[Exception] {err_msg}"
        return -1


def submit_cmd_async(
    cmd: str,
    request_id: str,
    is_tool: bool = False,
    tool_perm: int = 3,
    sys_type: str = None,
    executor: Any = None,
    PROCESS_LOCK: Any = None,
    CURRENT_PROCESSES: list = None,
    check_tool_permission_func: Optional[Callable] = None,
    user_mode: Any = None,
    log_info_func: Optional[Callable] = None,
    log_error_func: Optional[Callable] = None,
    is_interactive_command_func: Optional[Callable] = None,
    cwd: Optional[str] = None
) -> bool:
    """Execute command asynchronously (background thread)"""
    _ = is_interactive_command_func

    if cwd is None:
        cwd = os.getcwd()

    debug_log(f"submit_cmd_async: {repr(cmd)}, request_id={request_id}")

    def _async_job():
        try:
            shell = _get_persistent_shell(cwd=cwd)
            output_buffer: List[str] = []
            with _shell_lock:
                ret, _ = shell.execute(cmd, output_buffer, log_info_func, log_error_func)
            if log_info_func:
                try:
                    log_info_func(f"Asynchronous complete (exit code {ret}): {cmd}", request_id)
                except TypeError:
                    log_info_func(f"Asynchronous complete (exit code {ret}): {cmd}")
        except Exception as e:
            debug_log(f"Async job exception: {e}", 'error')
            if log_error_func:
                try:
                    log_error_func(f"Asynchronous exception: {e}, command: {cmd}", request_id)
                except TypeError:
                    log_error_func(f"Asynchronous exception: {e}, command: {cmd}")

    try:
        if executor:
            executor.submit(_async_job)
        else:
            threading.Thread(target=_async_job, daemon=True).start()
        return True
    except Exception as e:
        debug_log(f"Async submission failed: {e}", 'error')
        if log_error_func:
            try:
                log_error_func(f"Async submission failed: {e}", request_id)
            except TypeError:
                log_error_func(f"Async submission failed: {e}")
        return False


# ======================================================================
# Process Cleanup API
# ======================================================================
def cleanup_shell():
    """Clean up global persistent shell"""
    global _persistent_shell
    debug_log("cleanup_shell called")
    with _shell_lock:
        if _persistent_shell:
            _persistent_shell.cleanup()
            _persistent_shell = None
    close_debug_logging()


def warmup_persistent_shell():
    """Pre-create persistent shell in background during startup.
    Creates shell AND triggers prompt probe so the first real command
    doesn't pay the 2s probe timeout."""
    def _warmup():
        try:
            shell = _get_persistent_shell()
            # 触发 PS1 探测（避免首条命令等 2 秒超时）
            if shell is not None:
                shell._probe_and_learn_prompt()
            debug_log("Persistent shell pre-created in background")
        except Exception as e:
            debug_log(f"Background shell warmup failed: {e}", 'error')
    t = threading.Thread(target=_warmup, daemon=True)
    t.start()


def set_debug_enabled(enabled: bool):
    """Enable or disable debug logging at runtime."""
    global DEBUG_ENABLED
    DEBUG_ENABLED = enabled
    if enabled:
        _init_debug_logging()
        debug_log("Debug logging enabled at runtime")
    else:
        close_debug_logging()


def is_debug_enabled() -> bool:
    """Check if debug logging is currently enabled."""
    return DEBUG_ENABLED


def get_debug_session_info() -> Optional[Dict[str, str]]:
    """Get current debug session information."""
    if not DEBUG_ENABLED or _session_id is None:
        return None
    return {
        'session_id': _session_id,
        'session_dir': os.path.join(DEBUG_DIR, _session_id),
        'debug_log': os.path.join(DEBUG_DIR, _session_id, 'debug.log'),
        'shell_log': os.path.join(DEBUG_DIR, _session_id, 'shell.log'),
    }


# ======================================================================
# Terminal attribute save/restore
# ======================================================================
_saved_term_attrs = None
_term_guard_installed = False


def save_terminal_attrs():
    """保存当前终端属性，供 restore_terminal_attrs() 恢复。
    由 main_loop 在进入 raw 模式前调用。

    保存成功后顺带安装「终端兜底恢复」信号处理（幂等，只装一次）。
    """
    global _saved_term_attrs
    try:
        import termios as _t
        fd = sys.stdin.fileno()
        if os.isatty(fd):
            _saved_term_attrs = _t.tcgetattr(fd)
            install_terminal_guard()
    except (ImportError, OSError):
        _saved_term_attrs = None


def install_terminal_guard() -> None:
    """安装终端兜底恢复：收到 SIGTERM/SIGHUP/SIGQUIT 时先把终端恢复为 cooked。

    会**链式保留**已有 handler（Onyx/Main 已装 SIGINT/SIGTERM 逻辑）：
    先恢复终端，再调用原 handler；原 handler 为默认行为时按默认行为退出。
    幂等：重复调用只生效一次；非主线程调用静默跳过。
    """
    global _term_guard_installed
    if _term_guard_installed or not HAVE_FCNTL_TERMIOS:
        return
    _prev_map = {}
    for _name in ('SIGTERM', 'SIGHUP', 'SIGQUIT'):
        _sig = getattr(signal, _name, None)
        if _sig is None:
            continue
        try:
            _prev_map[_sig] = signal.getsignal(_sig)
        except (ValueError, OSError):
            return          # 非主线程无法读写 handler → 放弃安装
    if not _prev_map:
        return

    def _guard(signum, frame):
        try:
            restore_terminal_attrs()
        except Exception:
            pass
        _prev = _prev_map.get(signum, signal.SIG_DFL)
        try:
            if callable(_prev):
                _prev(signum, frame)
                return
            if _prev == signal.SIG_IGN:
                return
        except Exception:
            pass
        try:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        except Exception:
            pass

    try:
        for _sig in list(_prev_map.keys()):
            signal.signal(_sig, _guard)
        _term_guard_installed = True
    except (ValueError, OSError, TypeError):
        pass


def restore_terminal_attrs():
    """恢复终端属性到 save_terminal_attrs() 保存的状态。

    由 graceful_shutdown / crash handler / 信号兜底 / atexit 调用，
    确保终端回到 cooked 模式。

    注意：**不**清空 _saved_term_attrs —— 恢复动作可能被多次触发
    （正常退出、崩溃兜底、atexit、信号），清空会让后续路径失效；
    而保存的是 cooked 基线，重复恢复幂等、无害。
    """
    global _saved_term_attrs
    if _saved_term_attrs is None:
        return
    try:
        import termios as _t
        fd = sys.stdin.fileno()
        if os.isatty(fd):
            _t.tcsetattr(fd, _t.TCSANOW, _saved_term_attrs)
    except (ImportError, OSError):
        pass


def print_terminal_recovery_hint() -> None:
    """终端可能仍处于 raw 模式时，打印手动恢复提示（被强杀/崩溃兜底）。"""
    if not HAVE_FCNTL_TERMIOS:
        return
    try:
        import termios as _t
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
        _cur = _t.tcgetattr(fd)
        if not (_cur[3] & _t.ICANON) or not (_cur[3] & _t.ECHO):
            print("\n\033[33m[提示] 终端可能仍处于 raw 模式，"
                  "如输入无回显请执行: stty sane\033[0m")
    except (ImportError, OSError, IndexError):
        pass


# ======================================================================
# Auto-cleanup on module unload
# ======================================================================
@atexit.register
def _atexit_cleanup():
    """Clean up shell and restore terminal when Python process exits"""
    try:
        restore_terminal_attrs()
    except Exception:
        pass
    try:
        cleanup_shell()
    except Exception:
        pass