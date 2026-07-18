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
import fcntl
import termios
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
def generate_markers():
    """Generate unique marker identifiers for command output delimiting"""
    uid = uuid.uuid4().hex[:8]
    return {
        'start': f"__CMD_START_{uid}__",
        'end': f"__CMD_END_{uid}__:",
    }


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
_persistent_shell: Optional['PersistentShell'] = None
# AI 执行模式标志 — 由 ai_cmd.py 在执行命令前设为 True，执行后恢复
# 用于给 AI 触发的命令加超时保护和用户弹窗
AI_EXECUTION_MODE = False


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

    try:
        if os.isatty(fd):
            rows, cols = struct.unpack('hh', fcntl.ioctl(fd, termios.TIOCGWINSZ, '1234'))
            return rows, cols
    except Exception:
        pass
    return 24, 80


def update_pty_size(master_fd) -> None:
    """Update PTY window size dynamically"""
    global _current_pty_size
    rows, cols = get_terminal_size()
    if rows != _current_pty_size[0] or cols != _current_pty_size[1]:
        _current_pty_size = (rows, cols)
        debug_log(f"PTY size updated: {rows}x{cols}")
        if platform.system() != "Windows":
            try:
                if master_fd is not None:
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                                struct.pack('HHHH', rows, cols, 0, 0))
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


def get_shell() -> str:
    """Get available shell for current system (uses terminal type detection)"""
    if TERMINAL_TYPE_AVAILABLE:
        return get_shell_from_type()
    
    # Fallback logic
    if platform.system() == "Windows":
        for candidate in ["pwsh", "powershell", "cmd"]:
            if shutil.which(candidate):
                debug_log(f"Windows fallback shell: {candidate}")
                return candidate
        return "cmd.exe"

    env_shell = os.environ.get("SHELL")
    if env_shell and os.path.isfile(env_shell) and os.access(env_shell, os.X_OK):
        debug_log(f"Using SHELL env: {env_shell}")
        return env_shell

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
            return cand

    return "/bin/sh"


# ======================================================================
# Collect Caller Variables
# ======================================================================
def _collect_caller_vars(depth: int = 3) -> Dict[str, str]:
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
        self.cwd = cwd or os.getcwd()
        self._dead = False
        self.shell_name = os.path.basename(self.shell).lower()
        self._winpty_handle = None
        self._read_thread = None
        self._read_queue = None
        self._stop_thread = False
        # 用于 PS1 完成检测的随机 marker（在 _init_shell / _setup_prompt / _execute_passthrough 中使用）
        self._done_marker = f"__DONE_{uuid.uuid4().hex[:12]}__"

        # Merge environment variables with extra variables
        self._extra_vars = {}
        # 1. Add current process environment variables
        for key, value in os.environ.items():
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
        self._clear_screen()
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
        # 用 PS1 中的 DONE marker 检测命令完成（而非追加 ; printf … 到命令链）
        _done_marker = self._done_marker
        full_cmd = f"{cmd}\n"
        debug_log(f"Passthrough full_cmd: {repr(full_cmd[:200])}")
        shell_log(full_cmd, 'WRITE_CMD')

        is_windows = platform.system() == "Windows"
        fd_stdin = sys.stdin.fileno()

        # TTY raw mode：箭头键等需要逐字符转发到 PTY，不能行缓冲
        old_tty = None
        if not is_windows and os.isatty(fd_stdin):
            old_tty = termios.tcgetattr(fd_stdin)
            new_tty = termios.tcgetattr(fd_stdin)
            new_tty[0] &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR)
            new_tty[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
            termios.tcsetattr(fd_stdin, termios.TCSANOW, new_tty)

        old_sigint = None
        old_sigwinch = None
        _interrupted_flag = {'value': False}
        if not is_windows:
            def _sigint_handler(signum, frame):
                _interrupted_flag['value'] = True
                debug_log("SIGINT caught by Python handler (passthrough)")
            old_sigint = signal.signal(signal.SIGINT, _sigint_handler)
            if hasattr(signal, 'SIGWINCH'):
                def sigwinch_handler(signum, frame):
                    update_pty_size(self.master_fd)
                old_sigwinch = signal.signal(signal.SIGWINCH, sigwinch_handler)

        full_raw_output = ""
        interrupted = False
        return_code = -1
        fd_stdin = sys.stdin.fileno()

        # 排空 PTY 残留输出（单次 drain，无 sleep — PTY 已就绪时 drain 为 O(1)）
        self._drain_output()
        _echo_skipped = False

        try:
            try:
                self._write_to_master(full_cmd.encode('utf-8'))
                debug_log("Passthrough command written to PTY")
            except OSError as e:
                debug_log(f"Failed to write: {e}", 'error')
                return -1, full_raw_output

            while True:
                if not is_windows and self.master_fd is not None:
                    try:
                        rlist, _, _ = select.select([self.master_fd, fd_stdin], [], [])
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
                    stdin_data = None

                # --- Forward PTY output to terminal ---
                if data is not None:
                    if len(data) == 0:
                        self._dead = True
                        debug_log("PTY EOF (passthrough)")
                        break
                    try:
                        text = data.decode('utf-8', errors='replace')
                    except UnicodeDecodeError:
                        text = data.decode('latin-1', errors='replace')

                    full_raw_output += text

                    # 跳过 shell echo 的命令回显（第一行）
                    if not _echo_skipped:
                        # 移除命令回显行（可能跨多个 chunk）
                        echo_pos = text.find('\n')
                        if echo_pos != -1:
                            text = text[echo_pos + 1:]
                            _echo_skipped = True
                        else:
                            # 整个 chunk 都是命令回显，跳过
                            continue

                    # 检测 shell 提示词中的完成 marker（PS1 的一部分，SIGINT 后仍会打印）
                    if _done_marker:
                        done_pattern = re.escape(_done_marker) + r":(-?\d+)"
                        done_match = re.search(done_pattern, text)
                        if done_match:
                            return_code = int(done_match.group(1))
                            debug_log(f"PS1 done marker, exit={return_code}")
                            before_marker = text[:done_match.start()]
                            if before_marker:
                                clean = before_marker.replace('\r\n', '\n')
                                sys.stdout.write(clean)
                                sys.stdout.flush()
                                if output_buffer is not None:
                                    output_buffer.append(clean)
                            break

                    # Real-time output forwarding
                    clean = text.replace('\r\n', '\n')
                    sys.stdout.write(clean)
                    sys.stdout.flush()
                    if output_buffer is not None:
                        output_buffer.append(clean)

                # --- Forward stdin to PTY (for TUI programs) ---
                if stdin_data is not None and len(stdin_data) > 0:
                    try:
                        self._write_to_master(stdin_data)
                    except OSError:
                        pass

        except Exception as e:
            debug_log(f"Passthrough exception: {e}", 'error')
        finally:
            if not is_windows:
                if old_tty is not None and os.isatty(fd_stdin):
                    try:
                        termios.tcsetattr(fd_stdin, termios.TCSANOW, old_tty)
                    except Exception:
                        pass
                if old_sigint:
                    signal.signal(signal.SIGINT, old_sigint)
                if old_sigwinch and hasattr(signal, 'SIGWINCH'):
                    signal.signal(signal.SIGWINCH, old_sigwinch)

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
            try:
                os.write(self.master_fd, data)
            except OSError as e:
                debug_log(f"Unix write error: {e}", 'error')

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

        # Set PS1 to empty to avoid prompt display
        env['PS1'] = ''

        # Build shell command line based on shell type
        if self.shell_name in ('pwsh', 'powershell'):
            shell_args = [self.shell, '-NoLogo', '-NoProfile', '-NonInteractive']
        elif self.shell_name == 'cmd':
            shell_args = [self.shell]
        else:
            # For WSL/bash on Windows
            shell_args = [self.shell, '--norc', '--noprofile']

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

        try:
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                        struct.pack('HHHH', rows, cols, 0, 0))
        except Exception:
            pass

        pid = os.fork()
        if pid == 0:
            # Child process
            try:
                os.close(master_fd)
                os.setsid()
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)
                # PTY slave 保留 ECHO，否则 input() 等交互式程序的输入不可见。
                # 命令回显由 _execute_passthrough 的 _echo_skipped 机制过滤。

                env = os.environ.copy()
                env['TERM'] = env.get('TERM', 'xterm-256color')
                env['LINES'] = str(rows)
                env['COLUMNS'] = str(cols)
                env['PS1'] = f'{self._done_marker}:$?\\n'
                env['PROMPT'] = '$P$G'
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
                    os.execvpe(self.shell, [
                        self.shell,
                        '--norc',
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
            while time.time() - start_time < 5.0:
                data = self._read_from_master(timeout=0.05)
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

        debug_log(f"Setting up prompt for shell: {self.shell_name}, done_marker={self._done_marker}")

        if self.shell_name == 'fish':
            setup_cmd = (
                f"function fish_prompt; printf '{self._done_marker}:$status\\n'; end\n"
                "function fish_right_prompt; printf ''; end\n"
            )
        elif self.shell_name == 'zsh':
            setup_cmd = (
                "unsetopt PROMPT_CR 2>/dev/null\n"
                "unsetopt PROMPT_SP 2>/dev/null\n"
                f"precmd() {{ printf '{self._done_marker}:$?\\n'; }}\n"
                "PROMPT=''\n"
                "RPROMPT=''\n"
            )
        elif self.shell_name in ('pwsh', 'powershell'):
            setup_cmd = (
                f"function prompt {{ \"{self._done_marker}:$LASTEXITCODE`n\" }}\n"
            )
        elif self.shell_name == 'cmd':
            # CMD prompt can't embed exit code; keep minimal prompt
            setup_cmd = "prompt $G\n@echo off\n"
        else:
            # bash and other sh-compatible shells
            setup_cmd = f"PROMPT_COMMAND=''\nPS1='{self._done_marker}:$?\\n'\n"

        try:
            self._write_to_master(setup_cmd.encode('utf-8'))
            self._drain_output()
        except OSError as e:
            debug_log(f"Failed to setup prompt: {e}", 'error')
            pass

    def _drain_output(self, max_iterations: int = 50):
        """Consume all pending output (non-blocking)"""
        drained = 0
        for _ in range(max_iterations):
            data = self._read_from_master(timeout=0)
            if not data:
                break
            drained += len(data)
        if drained > 0:
            debug_log(f"Drained {drained} bytes of pending output")

    def _clear_screen(self):
        """Clear screen using ANSI escape sequence"""
        if self.master_fd is None and self._winpty_handle is None:
            return
        try:
            clear_cmd = "\033[2J\033[H\033[3J"
            self._write_to_master(clear_cmd.encode('utf-8'))
            self._drain_output()
        except OSError as e:
            debug_log(f"Failed to clear screen: {e}", 'error')
            pass

    def _disable_echo(self):
        """抑制 shell 的 PS1/命令回显。不关 PTY ECHO（否则 input() 输入不可见）。"""
        if self.master_fd is None and self._winpty_handle is None:
            return
        try:
            if self.shell_name == 'cmd':
                self._write_to_master(b"@echo off\n")
            # Unix: 不发送 stty -echo，靠 PS1='' + _echo_skipped 过滤命令回显
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
        Get current working directory from shell process

        Returns:
            Current directory path or None if unable to retrieve
        """
        if (self.master_fd is None and self._winpty_handle is None) or self.pid is None or self._dead:
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
            debug_log("Failed to get CWD")
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
        Execute a command in the persistent shell.
        
        passthrough=True: 命令原样透传，不包 { }、不加 start marker、不设 TTY。
        用于 TUI 程序 (nano/vim) 和普通 shell 命令，保证信号和终端模式正确。
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
    
        # === passthrough 模式：命令原样透传，不修改 TTY，不包 {} ===
        if passthrough:
            return self._execute_passthrough(cmd, output_buffer, log_info, log_error)
    
        markers = generate_markers()
        start_marker = markers['start']      # "__CMD_START_xxx__"
        end_marker = markers['end']          # "__CMD_END_xxx__:"
    
        # For TUI programs, we need to send commands in a way that doesn't interfere
        # Use subshell or command grouping to ensure markers don't break TUI output
        if self.shell_name in ('pwsh', 'powershell'):
            # PowerShell: use script block with markers as separate write-host commands
            full_cmd = (
                f"Write-Host '{start_marker}'; "
                f"{cmd}; "
                f"$EC = $LASTEXITCODE; "
                f"Write-Host ('{end_marker}' + $EC)\n"
            )
        elif self.shell_name == 'fish':
            full_cmd = (
                f"printf '%s\\n' '{start_marker}'; "
                f"{cmd}; "
                f"set EC $status; "
                f"printf '%s:%d\\n' '{end_marker}' $EC\n"
            )
        elif self.shell_name == 'cmd':
            full_cmd = (
                f"echo {start_marker}\n"
                f"{cmd}\n"
                f"set EC=%errorlevel%\n"
                f"echo {end_marker}%EC%\n"
            )
        else:
            # Unix shells: { } grouping so SIGINT kills only the command group.
            # 但如果命令本身含 { }（如 hello() { echo hi; }），嵌套花括号会让 bash 卡死。
            # 此时跳过 { } 包裹，直接执行 + 捕获退出码。
            if '{' in cmd or '}' in cmd:
                full_cmd = (
                    f"printf '%s\\n' '{start_marker}' >&2; "
                    f"{cmd}; "
                    f"EC=$?; "
                    f"printf '%s:%d\\n' '{end_marker}' $EC >&2\n"
                )
            else:
                full_cmd = (
                    f"printf '%s\\n' '{start_marker}' >&2; "
                    f"{{ {cmd}; }}; "
                    f"EC=$?; "
                    f"printf '%s:%d\\n' '{end_marker}' $EC >&2\n"
                )
    
        debug_log(f"Full command with markers: {repr(full_cmd)}")
        shell_log(full_cmd, 'WRITE_CMD')
    
        is_windows = platform.system() == "Windows"
        fd_stdin = sys.stdin.fileno()
    
        old_tty = None
        if not is_windows and os.isatty(fd_stdin):
            old_tty = termios.tcgetattr(fd_stdin)
            new_tty = termios.tcgetattr(fd_stdin)
            # Disable input processing that can interfere with TUI programs
            # Clear ICRNL (CR→NL), INLCR (NL→CR), IGNCR (ignore CR) to pass Enter as \r
            new_tty[0] &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR)
            # Disable canonical mode, echo, and signal generation
            new_tty[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
            termios.tcsetattr(fd_stdin, termios.TCSANOW, new_tty)
            debug_log("TTY settings modified for TUI support")
    
        # v9.7: Safe signal handling with Ctrl+C grace period
        # Save original SIGINT and SIGWINCH handlers
        old_sigint = None
        old_sigwinch = None
        _interrupted_flag = {'value': False}  # mutable container for handler access
        if not is_windows:
            # Custom SIGINT handler as safety net (ISIG is disabled so normally not triggered)
            # We do NOT use SIG_DFL because if a signal somehow reaches us, Python would die
            # and the persistent shell would be killed
            def _sigint_handler(signum, frame):
                _interrupted_flag['value'] = True
                debug_log("SIGINT caught by Python handler (safety net)")
            old_sigint = signal.signal(signal.SIGINT, _sigint_handler)
            if hasattr(signal, 'SIGWINCH'):
                def sigwinch_handler(signum, frame):
                    update_pty_size(self.master_fd)
                old_sigwinch = signal.signal(signal.SIGWINCH, sigwinch_handler)
            debug_log("Signal handlers set (SIGINT=custom_handler, SIGWINCH=handler)")
    
        full_raw_output = ""
        found_start = False
        interrupted = False
    
        try:
            try:
                self._write_to_master(full_cmd.encode('utf-8'))
                debug_log("Command written to PTY")
            except OSError as e:
                debug_log(f"Failed to write to shell: {e}", 'error')
                if log_error:
                    try:
                        log_error(f"Failed to write to shell: {e}", "")
                    except TypeError:
                        log_error(f"Failed to write to shell: {e}")
                return -1, full_raw_output
    
            # AI 执行超时保护：select 加 1s 超时，30s 弹窗询问用户
            _exec_start = time.time()
            _ai_warned = False
            _ai_kill = False

            while True:
                if not is_windows and self.master_fd is not None:
                    try:
                        rlist, _, _ = select.select([self.master_fd, fd_stdin], [], [], 1.0)
                    except (select.error, OSError):
                        continue
                    data = None
                    stdin_data = None
                    if self.master_fd in rlist:
                        try:
                            data = os.read(self.master_fd, 4096)
                        except OSError:
                            data = None
                        if DEBUG_ENABLED and data:
                            shell_log(data, 'READ')
                    if fd_stdin in rlist:
                        try:
                            stdin_data = os.read(fd_stdin, 1024)
                        except OSError:
                            stdin_data = None
                else:
                    # Windows: use reader thread queue
                    data = self._read_from_master(timeout=0.01)
                    stdin_data = None  # handled via msvcrt below

                # ── AI 超时弹窗（不阻塞命令，不自动中断）──
                if AI_EXECUTION_MODE and not found_start and not _ai_warned:
                    _elapsed = time.time() - _exec_start
                    if _elapsed > 30:
                        _ai_warned = True
                        sys.stderr.write(
                            "\n\033[33m⏱ 该命令已执行超过 30 秒，可能未启动或已卡死。"
                            " 输入 \033[1mk\033[22m 终止，或继续等待…\033[0m\n"
                        )
                        sys.stderr.flush()

                # --- Process PTY output ---
                if data is not None:
                    if len(data) == 0:
                        # EOF: shell process died (PTY slave closed)
                        self._dead = True
                        debug_log("PTY EOF detected, shell is dead")
                        break
                    try:
                        text = data.decode('utf-8', errors='replace')
                    except UnicodeDecodeError:
                        text = data.decode('latin-1', errors='replace')

                    if not found_start:
                        # 搜索行首的 start_marker。
                        # 真实的 printf 输出是独立一行 "__CMD_START_xxx__\n"，
                        # 而 bash echo 回显是 "printf ... '__CMD_START_xxx__' ..."（嵌在引号内）。
                        # 逐行扫描，只匹配整行等于 start_marker（忽略首尾空白）的行。
                        _lines = text.split('\n')
                        for _li, _line in enumerate(_lines):
                            if _line.strip() == start_marker:
                                found_start = True
                                debug_log("Start marker detected (line-start match)")
                                # 重建 after_marker：当前行剩余 + 后续所有行
                                _marker_pos = _line.find(start_marker)
                                _after_same = _line[_marker_pos + len(start_marker):]
                                _rest_lines = _lines[_li + 1:]
                                after_marker = '\n'.join(
                                    ([_after_same] if _after_same else []) + _rest_lines
                                )
                                full_raw_output += start_marker + after_marker
                                # Strip leading newlines
                                after_marker = after_marker.lstrip('\n\r')
                                if after_marker:
                                    end_pattern = re.escape(end_marker) + r":(-?\d+)"
                                    end_match = re.search(end_pattern, after_marker)
                                    if end_match:
                                        return_code = int(end_match.group(1))
                                        debug_log(f"End marker in start chunk, exit={return_code}")
                                        before_end = after_marker[:end_match.start()]
                                        if before_end:
                                            self._display_text(before_end)
                                        break
                                    else:
                                        self._display_text(after_marker)
                                break
                    else:
                        full_raw_output += text
                        # Check for end marker
                        end_pattern = re.escape(end_marker) + r":(-?\d+)"
                        end_match = re.search(end_pattern, text)
                        if end_match:
                            return_code = int(end_match.group(1))
                            debug_log(f"End marker detected, exit code: {return_code}")
                            before_end = text[:end_match.start()]
                            if before_end:
                                self._display_text(before_end)
                            break
                        else:
                            self._display_text(text)

                # --- Process stdin input ---
                if not is_windows:
                    if stdin_data is not None:
                        # AI 超时弹窗后：用户输入 'k' 则终止命令
                        if _ai_warned and not _ai_kill:
                            if b'k' in stdin_data.lower():
                                _ai_kill = True
                                sys.stderr.write("\033[33m⏹ 用户选择终止命令\033[0m\n")
                                sys.stderr.flush()
                                self._write_to_master(b'\x03')  # Ctrl+C 送 PTY
                                self._drain_output()
                                self._clear_screen()
                                break
                            else:
                                # 非 k 输入仍然转发
                                try:
                                    self._write_to_master(stdin_data)
                                except OSError:
                                    pass
                        else:
                            try:
                                self._write_to_master(stdin_data)
                            except OSError:
                                pass
                        if DEBUG_ENABLED:
                            shell_log(stdin_data, 'STDIN')
                else:
                    # Windows: use msvcrt.kbhit()
                    try:
                        import msvcrt
                        if msvcrt.kbhit():
                            user_data = msvcrt.getch()
                            # AI 超时弹窗后拦截 'k'
                            if _ai_warned and not _ai_kill and user_data.lower() == b'k':
                                _ai_kill = True
                                sys.stderr.write("\033[33m⏹ 用户选择终止命令\033[0m\n")
                                sys.stderr.flush()
                                try:
                                    self._write_to_master(b'\x03')
                                except OSError:
                                    pass
                                self._drain_output()
                                self._clear_screen()
                                break
                            try:
                                self._write_to_master(user_data)
                            except OSError:
                                pass
                    except ImportError:
                        pass
    
        except KeyboardInterrupt:
            # v9.7: KeyboardInterrupt is a fallback (ISIG is disabled so this shouldn't fire,
            # but our custom SIGINT handler may have been triggered from elsewhere)
            interrupted = True
            debug_log("KeyboardInterrupt caught")
            try:
                # Send ^C byte as backup
                self._write_to_master(b'\x03')
            except OSError:
                pass
            # Give the command a short moment to terminate
            ctrlc_time = time.time()
            self._drain_output()
            self._clear_screen()
        except Exception as e:
            debug_log(f"Internal exception during command execution: {e}\n{traceback.format_exc()}", 'error')
            if log_error:
                try:
                    log_error(f"Internal exception during command execution: {e}\n{traceback.format_exc()}", "")
                except TypeError:
                    log_error(f"Internal exception during command execution: {e}")
            return_code = -1
        finally:
            # Restore TTY settings
            if old_tty and not is_windows and os.isatty(fd_stdin):
                try:
                    termios.tcsetattr(fd_stdin, termios.TCSANOW, old_tty)
                    debug_log("TTY settings restored")
                except termios.error:
                    pass
            
            # Restore signal handlers
            if old_sigint is not None:
                signal.signal(signal.SIGINT, old_sigint)
            if old_sigwinch is not None:
                signal.signal(signal.SIGWINCH, old_sigwinch)
            debug_log("Signal handlers restored")
    
        # After command execution, drain any remaining output to clean up for next command
        self._drain_output()
    
        # v9.7: Check if the safety-net SIGINT handler was triggered
        if not interrupted and _interrupted_flag['value']:
            interrupted = True
            debug_log("Interrupted via SIGINT safety-net handler")
    
        if interrupted:
            output_buffer.append("[Interrupted]")
            return -1, full_raw_output
    
        clean_output = self._extract_clean_output(full_raw_output, markers)
        output_buffer.append(clean_output)
        
        debug_log(f"Command completed with exit code {return_code}, output length: {len(clean_output)}")
    
        return return_code, full_raw_output

    def _display_text(self, text: str):
        """Display text to stdout — streaming filter, no intermediate list"""
        wrote = False
        lines = text.split('\n')
        for i, line in enumerate(lines):
            if '__CMD_START_' in line or '__CMD_END_' in line or '__READY_' in line:
                continue
            if self._done_marker and self._done_marker in line:
                continue
            stripped = line.strip()
            if stripped in _FILTERED_LINES:
                continue
            if stripped.startswith(_FILTERED_PREFIXES):
                continue
            # 跳过 split('\n') 产生的尾部空元素，避免命令输出后多余的换行
            if not stripped and i == len(lines) - 1:
                continue
            sys.stdout.write(line + '\n')
            wrote = True
        if wrote:
            sys.stdout.flush()

    def _extract_clean_output(self, raw: str, markers: dict) -> str:
        """Extract clean command output from raw output"""
        start_marker = markers['start']
        end_marker = markers['end']

        start_idx = raw.find(start_marker)
        if start_idx == -1:
            debug_log("Start marker not found in output extraction")
            return ""

        start_idx += len(start_marker)
        while start_idx < len(raw) and raw[start_idx] in ('\n', '\r'):
            start_idx += 1

        end_pattern = re.escape(end_marker) + r":(-?\d+)"
        end_match = re.search(end_pattern, raw[start_idx:])

        if end_match:
            output = raw[start_idx:start_idx + end_match.start()]
        else:
            output = raw[start_idx:]

        lines = output.split('\n')
        clean_lines = []
        for line in lines:
            stripped = line.strip()
            if '__CMD_START_' in line or '__CMD_END_' in line or '__READY_' in line:
                continue
            if self._done_marker and self._done_marker in line:
                continue
            if stripped in _FILTERED_LINES:
                continue
            if stripped.startswith(_FILTERED_PREFIXES):
                continue
            clean_lines.append(line)

        while clean_lines and not clean_lines[0].strip():
            clean_lines.pop(0)
        while clean_lines and not clean_lines[-1].strip():
            clean_lines.pop()

        return '\n'.join(clean_lines)

    def cleanup(self):
        """Terminate shell process and release resources"""
        debug_log("Cleaning up persistent shell")
        
        # Stop reader thread first
        if platform.system() == "Windows":
            self._stop_thread = True
            if self._read_thread and self._read_thread.is_alive():
                self._read_thread.join(timeout=1.0)

        if self.pid:
            try:
                if platform.system() != "Windows":
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
        """Check if shell is alive"""
        if self._dead or self.pid is None:
            return False
        try:
            if platform.system() == "Windows":
                # On Windows, check winpty handle
                return self._winpty_handle is not None and self._winpty_handle.isalive()
            os.kill(self.pid, 0)
            return True
        except OSError:
            self._dead = True
            return False

    def __del__(self):
        self.cleanup()


# ======================================================================
# Public Interface
# ======================================================================
def _get_persistent_shell(cwd: Optional[str] = None) -> PersistentShell:
    """Thread-safe get or create persistent shell instance"""
    global _persistent_shell
    if _persistent_shell is None or not _persistent_shell.is_alive():
        with _shell_lock:
            if _persistent_shell is None or not _persistent_shell.is_alive():
                if _persistent_shell:
                    _persistent_shell.cleanup()
                start_cwd = cwd or os.getcwd()
                caller_vars = _collect_caller_vars(depth=3)
                _persistent_shell = PersistentShell(cwd=start_cwd, extra_vars=caller_vars)
    if _persistent_shell is not None:
        target_cwd = cwd or os.getcwd()
        if _persistent_shell.cwd != target_cwd:
            _persistent_shell.set_cwd(target_cwd)
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
    """Get current working directory from persistent shell"""
    global _persistent_shell
    if _persistent_shell is None or not _persistent_shell.is_alive():
        _get_persistent_shell()
    if _persistent_shell:
        return _persistent_shell.get_current_cwd()
    return None


def is_shell_alive() -> bool:
    """Check if shell is alive"""
    global _persistent_shell
    return _persistent_shell is not None and _persistent_shell.is_alive()


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
            cwd = os.getcwd()

        shell = _get_persistent_shell(cwd=cwd)

        with _shell_lock:
            return_code, raw_output = shell.execute(
                cmd,
                output_buffer,
                log_info=log_info_func,
                log_error=log_error_func,
                passthrough=passthrough
            )

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
# Auto-cleanup on module unload
# ======================================================================
@atexit.register
def _atexit_cleanup():
    """Clean up shell when Python process exits"""
    cleanup_shell()