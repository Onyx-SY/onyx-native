# -*- coding: utf-8 -*-
"""
mcp_client_core.py — MCP (Model Context Protocol) 客户端核心

从 bin/ai_cmd.py 拆分（模块化架构重构）：
- stderr 收集器 /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test JSON-RPC 收发 /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test connect/preload/health_check /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test 工具发现；
- 进程注册表（MCP_SERVER_PROCESSES）与工具缓存（MCP_TOOLS_CACHE）为本模块状态；
- 调试标志（_MCP_DEBUG）由 ai_cmd 持有（handle_ai --debug 设置），本模块的
  _mcp_debug 系列在函数体内延迟导入 ai_cmd 状态——单一状态源，避免双份分叉。
"""

import json
import os
import shutil
import subprocess
import threading
import time
from typing import Dict, List, Optional

from rich.console import Console

from . import sandbox
from .config import ROOT_DIR, USER_HOME_DIR, get_current_lang
from .mcp_registry import MCPSchemaCache, get_registry
from .mcp_transport import StdioTransport
from .native_tools import invalidate_native_tools_cache

console = Console()


# ── 旧版兼容变量（逐步迁移中）──
MCP_SERVER_PROCESSES: Dict[str, subprocess.Popen] = {}
MCP_TOOLS_CACHE: Dict[str, List[Dict]] = {}          # 旧缓存，逐步替换为 registry
MCP_TRANSPORTS: Dict[str, StdioTransport] = {}        # 新版 transport 实例
MCP_CONFIG_PATH = os.path.join(ROOT_DIR, "onyx", "etc", "mcp", "mcp.json")
MCP_PRELOADED = False
MCP_PRELOAD_LOCK = threading.Lock()
MCP_INSTALL_LOCK = threading.Lock()
MCP_HEALTH_CHECK_INTERVAL = 120
_MCP_LAST_HEALTH_CHECK = 0.0

# stderr 收集器（防止管道死锁：daemon 线程持续读取，避免子进程阻塞在 stderr write）
_MCP_STDERR_BUFFERS: Dict[int, List[str]] = {}       # proc.pid → [lines...]
_MCP_STDERR_LOCKS: Dict[int, threading.Lock] = {}    # proc.pid → Lock
# 内存上限：每 PID 缓冲最多保留最近 N 行（读取只用 buf[-50:]）。
# MCP server 长时间运行持续输出日志时，无上限会导致该列表无限增长（内存泄漏）。
_MCP_STDERR_MAX_LINES = 200


def _cleanup_mcp_stderr_buffers(pid: int) -> None:
    """进程退出/断开时清理 stderr 缓冲与锁（防 PID 复用读到旧数据 + 防内存残留）。"""
    try:
        _MCP_STDERR_BUFFERS.pop(pid, None)
    except Exception:
        pass
    try:
        _MCP_STDERR_LOCKS.pop(pid, None)
    except Exception:
        pass


def _start_stderr_reader(proc: subprocess.Popen, name: str = "mcp") -> None:
    """启动 daemon 线程持续读取 stderr，防止管道缓冲区满导致子进程死锁。"""
    pid = proc.pid
    lock = threading.Lock()
    _MCP_STDERR_LOCKS[pid] = lock
    _MCP_STDERR_BUFFERS[pid] = []

    stderr_fd = proc.stderr.fileno() if hasattr(proc.stderr, 'fileno') else None

    def _reader():
        try:
            if stderr_fd is not None:
                import os as _os
                # 直接读原始 fd（避免 TextIOWrapper 缓冲问题）
                buf = b""
                while True:
                    try:
                        chunk = _os.read(stderr_fd, 4096)
                        if not chunk:
                            break
                        buf += chunk
                        # 按行拆分
                        while b"\n" in buf:
                            line_b, buf = buf.split(b"\n", 1)
                            line = line_b.decode("utf-8", errors="replace").strip()
                            if line:
                                with lock:
                                    _MCP_STDERR_BUFFERS[pid].append(line)
                                    # 超出上限丢最旧（O(1) 摊还：只 del 头部切片）
                                    if len(_MCP_STDERR_BUFFERS[pid]) > _MCP_STDERR_MAX_LINES:
                                        _MCP_STDERR_BUFFERS[pid] = _MCP_STDERR_BUFFERS[pid][-_MCP_STDERR_MAX_LINES:]
                    except (OSError, BlockingIOError, ValueError):
                        break
            else:
                # 回退：TextIOWrapper 逐行读取
                for line in proc.stderr:
                    line = line.strip()
                    if line:
                        with lock:
                            _MCP_STDERR_BUFFERS[pid].append(line)
                            if len(_MCP_STDERR_BUFFERS[pid]) > _MCP_STDERR_MAX_LINES:
                                _MCP_STDERR_BUFFERS[pid] = _MCP_STDERR_BUFFERS[pid][-_MCP_STDERR_MAX_LINES:]
        except Exception:
            pass

    t = threading.Thread(target=_reader, daemon=True, name=f"mcp-stderr-{name}-{pid}")
    t.start()


def _get_stderr_lines(proc: subprocess.Popen) -> str:
    """获取已收集的 stderr 内容（用于诊断输出）。"""
    pid = proc.pid
    lock = _MCP_STDERR_LOCKS.get(pid)
    buf = _MCP_STDERR_BUFFERS.get(pid, [])
    if lock:
        with lock:
            return "\n".join(buf[-50:])  # 最近 50 行
    return "\n".join(buf[-50:])


# Schema 缓存单例
_schema_cache: Optional[MCPSchemaCache] = None

def _get_schema_cache() -> MCPSchemaCache:
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = MCPSchemaCache()
    return _schema_cache

# Shell/bash 类工具名过滤列表
MCP_TOOL_FILTER = {
    "shell", "bash", "sh", "zsh", "fish", "terminal", "execute_command",
    "run_command", "exec", "spawn", "pty", "tty",
}


def _ensure_dir(path: str) -> None:
    """安全创建目录（兼容安卓等 exist_ok 不生效的平台，处理旧文件冲突）"""
    if os.path.isfile(path):
        # 旧版本 manage set mcp 把 mcp 写成文件，现在它是目录，删掉重建
        os.remove(path)
    if not os.path.isdir(path):
        try:
            os.makedirs(path, exist_ok=True)
        except FileExistsError:
            pass  # 目录已被其他线程创建


def _get_mcp_config_dir(user_home_dir: str = None) -> str:
    """获取用户 MCP 配置目录（按用户隔离）"""
    home = user_home_dir or USER_HOME_DIR
    return os.path.join(home, ".config", "onyx", "mcp")


def _get_mcp_config_path(user_home_dir: str = None) -> str:
    """获取用户 MCP 配置文件路径"""
    return os.path.join(_get_mcp_config_dir(user_home_dir), "mcp.json")


def _migrate_mcp_config_if_needed(user_home_dir: str = None) -> str:
    """
    如果用户目录下没有 MCP 配置，从全局模板复制一份。
    返回用户配置文件路径。
    """
    user_path = _get_mcp_config_path(user_home_dir)
    if os.path.exists(user_path):
        return user_path

    # 从全局模板复制（保留 {CWD} 模板标记，运行时动态替换为当前工作目录）
    global_path = MCP_CONFIG_PATH
    if os.path.exists(global_path):
        _ensure_dir(os.path.dirname(user_path))
        try:
            with open(global_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            # 保持 {CWD} 模板不变，运行时由 connect_mcp_server 动态替换
            with open(user_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return user_path
        except Exception:
            pass

    # 没有模板，创建默认配置（使用 {CWD} 模板标记）
    # 默认零 MCP：不预置任何 server（避免默认下载/连接；需要时用 ai -mcp install <name>）
    default_config = {
        "_comment": "Onyx MCP server registry — 默认零 MCP（不自动下载/连接）。需要时用 ai -mcp install <name> 显式安装",
        "servers": {}
    }
    _ensure_dir(os.path.dirname(user_path))
    with open(user_path, "w", encoding="utf-8") as f:
        json.dump(default_config, f, ensure_ascii=False, indent=2)
    return user_path


def _validate_mcp_mount_path(server_info: dict, user_home_dir: str) -> bool:
    """
    校验 MCP server 的挂载路径是否安全。
    允许：用户主目录内 或 当前工作目录内。
    返回 True 表示安全，False 表示越界。
    """
    args = server_info.get("args", [])
    user_home = os.path.realpath(user_home_dir)
    cwd = os.path.realpath(os.getcwd())

    def _is_under(path: str, parent: str) -> bool:
        return path == parent or path.startswith(parent + os.sep)

    for i, arg in enumerate(args):
        if arg.startswith("/") and not arg.startswith("-"):
            real_path = os.path.realpath(arg) if os.path.exists(arg) else os.path.abspath(arg)
            # 检查是否在用户主目录内或当前工作目录内
            if _is_under(real_path, user_home) or _is_under(real_path, cwd):
                continue
            else:
                return False
    return True


def _load_mcp_config(user_home_dir: str = None) -> Dict:
    """加载 MCP 服务器注册表（按用户）"""
    config_path = _migrate_mcp_config_if_needed(user_home_dir)
    if not os.path.exists(config_path):
        return {"servers": {}}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"servers": {}}


def _save_mcp_config(config: Dict, user_home_dir: str = None) -> None:
    """保存 MCP 服务器注册表（按用户）"""
    config_path = _get_mcp_config_path(user_home_dir)
    _ensure_dir(os.path.dirname(config_path))
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# MCP 调试开关（由 handle_ai 根据 --debug 设置）
_MCP_DEBUG = False

# AI 中断标志（Ctrl+C 打断思考时置位）
_AI_INTERRUPTED = False
_MCP_DEBUG_START: float = 0.0  # --debug 启动时的基准时间

# 持久化调试模式：ai --debug 进入 REPL 后，跨 handle_ai 调用保持 debug_mode=True
_PERSIST_DEBUG = False


def _mcp_debug(msg: str) -> None:
    """--debug 模式实时追踪：打印带时间戳的消息（输出到 stderr 确保立即可见）"""
    from ..ai_cmd import _MCP_DEBUG, _MCP_DEBUG_START
    if _MCP_DEBUG:
        import sys as _sys
        elapsed = (time.time() - _MCP_DEBUG_START) if _MCP_DEBUG_START else 0
        _sys.stderr.write(f"[{elapsed:06.2f}s] MCP {msg}\n")
        _sys.stderr.flush()


def _mcp_debug_enter(func_name: str) -> None:
    """函数进入时的 debug 追踪"""
    from ..ai_cmd import _MCP_DEBUG, _MCP_DEBUG_START
    if _MCP_DEBUG:
        import sys as _sys
        elapsed = (time.time() - _MCP_DEBUG_START) if _MCP_DEBUG_START else 0
        _sys.stderr.write(f"[{elapsed:06.2f}s] → {func_name}\n")
        _sys.stderr.flush()


def _mcp_debug_exit(func_name: str, ok: bool = True, detail: str = "") -> None:
    """函数退出时的 debug 追踪"""
    from ..ai_cmd import _MCP_DEBUG, _MCP_DEBUG_START
    if _MCP_DEBUG:
        import sys as _sys
        elapsed = (time.time() - _MCP_DEBUG_START) if _MCP_DEBUG_START else 0
        status = "OK" if ok else "FAIL"
        extra = f" ({detail})" if detail else ""
        _sys.stderr.write(f"[{elapsed:06.2f}s] ← {func_name} {status}{extra}\n")
        _sys.stderr.flush()


def _mcp_t(cn: str, en: str) -> str:
    """MCP 消息双语：根据当前语言返回中文或英文"""
    return cn if get_current_lang() == "chinese" else en


def _mcp_send(proc: subprocess.Popen, msg: Dict) -> None:
    """通过 stdin 发送 JSON-RPC 消息（换行分隔 JSON，MCP stdio 传输标准）"""
    body = json.dumps(msg, ensure_ascii=False) + "\n"
    method = msg.get("method", "?")
    _mcp_debug_enter(f"_mcp_send({method})")
    _mcp_debug(f"SEND → {body[:200]}{'...' if len(body) > 200 else ''}")
    _mcp_debug(f"  stdin type={type(proc.stdin).__name__}, closed={getattr(proc.stdin, 'closed', '?')}")
    try:
        proc.stdin.write(body)
        proc.stdin.flush()
        _mcp_debug("  write+flush OK")
        _mcp_debug_exit("_mcp_send", ok=True)
    except (BrokenPipeError, OSError) as e:
        _mcp_debug(f"  FAILED: {e}")
        _mcp_debug_exit("_mcp_send", ok=False, detail=str(e))
        raise ConnectionError(f"MCP server disconnected: {e}")


def _mcp_recv(proc: subprocess.Popen, timeout: float = 30.0) -> Optional[Dict]:
    """通过 stdout 接收 JSON-RPC 消息（换行分隔 JSON）
    
    关键修复：用 os.read(fd, 1) 直接读原始文件描述符，而不是 proc.stdout.read(1)。
    后者在 text=True 时经过 TextIOWrapper → BufferedReader 多层缓冲，
    导致 select.select (监视内核 fd) 与 read (读 Python 缓冲区) 脱节，
    表现为 select 频繁超时（每次最多等 1s），整行 JSON 看起来像"立即卡死"。
    """
    import select as _select
    import os as _os
    _mcp_debug_enter(f"_mcp_recv(timeout={timeout}s)")
    deadline = time.time() + timeout
    fd = proc.stdout.fileno() if hasattr(proc.stdout, 'fileno') else proc.stdout
    _mcp_debug(f"RECV waiting (timeout={timeout}s, fd={fd}, stdout_type={type(proc.stdout).__name__})")
    line_bytes = b""
    while True:
        # 检查中断标志（Ctrl+C），允许用户打断卡住的 MCP 请求
        if _AI_INTERRUPTED:
            _mcp_debug(f"RECV interrupted by user after {len(line_bytes)} bytes")
            _mcp_debug_exit("_mcp_recv", ok=False, detail="interrupted")
            return None
        remaining = deadline - time.time()
        if remaining <= 0:
            _mcp_debug(f"RECV TIMEOUT after {len(line_bytes)} bytes: {line_bytes[:200]}")
            _mcp_debug_exit("_mcp_recv", ok=False, detail="timeout")
            return None
        if _select.select([fd], [], [], min(remaining, 1.0))[0]:
            try:
                ch = _os.read(fd, 1)  # 直接读原始 fd，与 select 监视的是同一层
            except (OSError, BlockingIOError):
                _mcp_debug(f"RECV os.read error, fd may be closed")
                _mcp_debug_exit("_mcp_recv", ok=False, detail="os.read error")
                return None
            if not ch:
                _mcp_debug(f"RECV EOF after {len(line_bytes)} bytes")
                _mcp_debug_exit("_mcp_recv", ok=False, detail="EOF")
                return None
            # os.read 始终返回 bytes，无需 isinstance 判断
            if ch == b'\n':
                _mcp_debug(f"RECV \\n (total {len(line_bytes)} bytes)")
                break
            line_bytes += ch
        else:
            continue
    line = line_bytes.decode('utf-8').strip()
    _mcp_debug(f"RECV ← {line[:200]}{'...' if len(line) > 200 else ''}")
    if not line:
        _mcp_debug_exit("_mcp_recv", ok=False, detail="empty line")
        return None
    try:
        result = json.loads(line)
        _mcp_debug_exit("_mcp_recv", ok=True, detail=f"{len(line_bytes)} bytes")
        return result
    except json.JSONDecodeError as e:
        _mcp_debug(f"RECV JSON parse error: {e}")
        _mcp_debug_exit("_mcp_recv", ok=False, detail="JSON parse error")
        return None


def _mcp_request(proc: subprocess.Popen, method: str, params: Dict = None,
                 msg_id: int = None) -> Optional[Dict]:
    """发送 JSON-RPC 请求并等待响应"""
    _mcp_debug_enter(f"_mcp_request({method})")
    if msg_id is None:
        msg_id = int(time.time() * 1000) % 1000000
    _mcp_send(proc, {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "params": params or {},
    })
    result = _mcp_recv(proc)
    _mcp_debug_exit(f"_mcp_request({method})", ok=result is not None and "error" not in result)
    return result


def _mcp_notification(proc: subprocess.Popen, method: str, params: Dict = None) -> None:
    """发送 JSON-RPC 通知（无响应）"""
    _mcp_send(proc, {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
    })


def is_mcp_server_running(name: str) -> bool:
    """检查 MCP server 是否在运行"""
    if name in MCP_SERVER_PROCESSES:
        proc = MCP_SERVER_PROCESSES[name]
        return proc.poll() is None
    return False


def install_default_mcp_server(user_home_dir: str = None, auto_extras: bool = False) -> bool:
    """默认零 MCP：不再自动注册/标记任何 server（取消默认下载/连接）。

    需要 MCP server 时请用 `ai -mcp install <name>` 显式安装；
    显式安装且 auto_start=true 的 server 由 preload_mcp_servers 在
    mcp_enabled=true 时预载。
    """
    return False


def connect_mcp_server(name: str = "filesystem", user_home_dir: str = None) -> Optional[subprocess.Popen]:
    """启动并初始化 MCP 服务器（同步阻塞直到 initialize 完成）"""
    _mcp_debug_enter(f"connect_mcp_server({name})")
    if is_mcp_server_running(name):
        _mcp_debug(f"Server '{name}' already running, returning cached proc")
        _mcp_debug_exit("connect_mcp_server", ok=True, detail="already running")
        return MCP_SERVER_PROCESSES[name]

    home = user_home_dir or USER_HOME_DIR
    config = _load_mcp_config(home)
    server_info = config.get("servers", {}).get(name)
    if not server_info:
        console.print(_mcp_t(
            f"❌ MCP server '{name}' 未注册",
            f"❌ MCP server '{name}' not registered"
        ), style="bold red")
        return None

    # 检查是否已安装（避免对未安装的 server 反复尝试启动）
    if not server_info.get("installed", False):
        console.print(
            f"⚠️ MCP server '{name}' 尚未安装。请执行: ai -mcp install {name}",
            style="bold yellow"
        )
        return None

    # 安全校验：挂载路径是否安全
    if not _validate_mcp_mount_path(server_info, home):
        lang = get_current_lang()
        args = server_info.get("args", [])
        bad_paths = [a for a in args if a.startswith("/") and not a.startswith("-")]
        fallback_dir = os.getcwd()
        msg = (
            f"⚠️ MCP server '{name}' 挂载路径 {bad_paths} 超出安全范围！\n"
            f"   用户目录: {home}\n"
            f"   已自动修正为当前工作目录。如需自定义请手动编辑配置文件。"
        ) if lang == "chinese" else (
            f"⚠️ MCP server '{name}' mount path {bad_paths} outside safe range!\n"
            f"   User home: {home}\n"
            f"   Auto-corrected to CWD. Edit config manually to customize."
        )
        console.print(msg, style="bold yellow")
        # 自动修正：替换越界路径为 {CWD} 模板（与下方动态替换逻辑一致；
        # 避免 os.getcwd() 本身为不安全路径时修正无效），并写回配置文件持久化，
        # 否则每次启动都重新加载原值、重复警告，json 文件也始终不变。
        fixed_args = []
        for a in server_info.get("args", []):
            if a.startswith("/") and not a.startswith("-"):
                fixed_args.append("{CWD}")
            else:
                fixed_args.append(a)
        server_info["args"] = fixed_args
        try:
            _save_mcp_config(config, home)
        except Exception:
            pass

    cmd = server_info.get("command", "npx")
    args = list(server_info.get("args", []))  # 拷贝避免修改原配置

    # ── 动态路径替换：{CWD} → 当前工作目录（每次 ai 命令时实时获取）──
    cwd_now = os.getcwd()
    for i, arg in enumerate(args):
        if arg == "{CWD}":
            args[i] = cwd_now
        elif arg == "{USER_HOME}":
            args[i] = home

    # Termux: npx 在 FUSE/exFAT 上极不可靠，已历经 EACCES → TAR_ENTRY_ERROR → ECOMPROMISED
    # 改为全局安装后直接运行二进制，彻底绕过 npx 的临时安装和缓存机制
    env = os.environ.copy()
    _is_on_termux = False
    try:
        from lib.get_lib_path import _is_termux_environment
        if _is_termux_environment():
            _is_on_termux = True
            from lib.get_lib_path import TERMUX_PREFIX, TERMUX_HOME

            # Termux 上恢复真实 HOME（npm 用 $HOME 解析 prefix 等路径）
            env["HOME"] = TERMUX_HOME

            # 查找全局安装的 MCP filesystem 二进制
            mcp_bin = os.path.join(TERMUX_PREFIX, "bin", "mcp-server-filesystem")
            if not os.path.exists(mcp_bin):
                # 首次使用：npm install -g（仅一次，后续直接运行二进制）
                console.print(_mcp_t(
                    "📱 Termux: 首次安装 MCP filesystem server（约 30-60s）...",
                    "📱 Termux: Installing MCP filesystem server (~30-60s)..."
                ), style="cyan")
                termux_cache = os.path.join(TERMUX_PREFIX, "tmp", "npm_cache")
                _ensure_dir(termux_cache)
                install_env = env.copy()
                install_env["NPM_CONFIG_CACHE"] = termux_cache
                install_env["npm_config_cache"] = termux_cache
                install_env["npm_config_prefix"] = TERMUX_PREFIX
                result = subprocess.run(
                    ["npm", "install", "-g", "@modelcontextprotocol/server-filesystem"],
                    capture_output=True, text=True, timeout=120,
                    env=install_env
                )
                if result.returncode != 0:
                    console.print(
                        _mcp_t("❌ Termux: npm install -g 失败", "❌ Termux: npm install -g failed") +
                        f"\n{result.stderr[:500]}", style="bold red")
                    return None
                if not os.path.exists(mcp_bin):
                    console.print(
                        f"❌ Termux: 安装完成但 binary 不存在\n"
                        f"   预期路径: {mcp_bin}\n"
                        f"   npm stdout: {result.stdout[:300]}",
                        style="bold red"
                    )
                    return None
                console.print(_mcp_t("✅ Termux: MCP server 就绪", "✅ Termux: MCP server ready"), style="green")

            # 直接用二进制 + PTY 替代 stdbuf -o0
            # Node.js stdout 在 pipe 模式下全缓冲，stdbuf 在 Termux 不稳定
            # PTY 天然行缓冲，彻底解决 JSON-RPC 握手超时
            cmd = mcp_bin
            # binary 直接运行只需挂载路径，不需要 npx 的 -y 等参数
            # AI 虚拟沙盒：挂载点 = cwd（AI 视角虚拟根 /）
            args = [sandbox.get_root() or os.getcwd()]
            import pty as _pty
            import termios as _termios
            _master_fd, _slave_fd = _pty.openpty()
            _mcp_debug(f"PTY created: master={_master_fd}, slave={_slave_fd}")
            # PTY 设为原始模式：关闭行缓冲(ICANON)、输出处理(OPOST)、回显(ECHO)、信号(ISIG)
            _attrs = _termios.tcgetattr(_slave_fd)
            _mcp_debug(f"PTY attrs: iflag=0x{_attrs[0]:x} oflag=0x{_attrs[1]:x} cflag=0x{_attrs[2]:x} lflag=0x{_attrs[3]:x}")
            _attrs[0] = _attrs[0] & ~(_termios.ICRNL | _termios.INLCR)  # 输入不转换
            _attrs[1] = _attrs[1] & ~_termios.OPOST                      # 输出不转换
            _attrs[3] = _attrs[3] & ~(_termios.ICANON | _termios.ECHO | _termios.ISIG)
            _termios.tcsetattr(_slave_fd, _termios.TCSANOW, _attrs)
            _mcp_debug(f"PTY raw mode: lflag=0x{_attrs[3]:x} ICANON={'ON' if _attrs[3] & _termios.ICANON else 'OFF'} OPOST={'ON' if _attrs[1] & _termios.OPOST else 'OFF'}")
            _mcp_debug(f"Starting: {cmd} {' '.join(args)}")
            proc = subprocess.Popen(
                [cmd] + args,
                stdin=subprocess.PIPE,
                stdout=_slave_fd,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            _mcp_debug(f"Process started pid={proc.pid}, stdin_type={type(proc.stdin).__name__}")
            os.close(_slave_fd)
            # 用 PTY master 替换 proc.stdout（无缓冲二进制，直接走 OS read）
            proc.stdout = os.fdopen(_master_fd, 'rb', buffering=0)
            _mcp_debug(f"stdout replaced with PTY master (rb, buffering=0)")
            # 启动 stderr 读取线程（防止管道死锁）
            _start_stderr_reader(proc, name)
            # 跳过下面的通用 Popen 路径
            raise StopIteration
    except StopIteration:
        pass
    except Exception:
        pass

    if not (_is_on_termux and 'proc' in dir()):
        _mcp_debug(f"Non-Termux: starting {cmd} {' '.join(args)}")

        # Node.js 在 pipe 模式下 stdout 全缓冲（默认 16KB），
        # JSON-RPC 响应通常远小于此阈值，会长期滞留在缓冲区不发出。
        # 导致 Python _mcp_recv 在 select+read 上无限等待。
        # Termux 用 PTY 避开了这个问题；非 Termux 用 stdbuf 强制行缓冲。
        _full_cmd = [cmd] + args
        if shutil.which("stdbuf"):
            _full_cmd = ["stdbuf", "-o0"] + _full_cmd
            _mcp_debug(f"stdbuf available, using: stdbuf -o0 {' '.join([cmd] + args)}")
        else:
            # 备选：设置 NODE_OPTIONS 禁止警告输出（防 stderr 洪水），
            # 但无法解决 Node stdout 缓冲问题。没有 stdbuf 时只能接受风险。
            env.setdefault("NODE_NO_WARNINGS", "1")
            _mcp_debug("stdbuf not available, Node.js pipe buffering risk remains")

        try:
            proc = subprocess.Popen(
                _full_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            _mcp_debug(f"Process started pid={proc.pid}")
        except FileNotFoundError:
            console.print(_mcp_t(
                f"❌ 命令 '{cmd}' 未找到，请确认已安装",
                f"❌ Command '{cmd}' not found, please verify installation"
            ), style="bold red")
            return None
        except Exception as e:
            console.print(_mcp_t(
                f"❌ 启动 MCP server 失败: {str(e)}",
                f"❌ Failed to start MCP server: {str(e)}"
            ), style="bold red")
            return None

    # 立即启动 stderr 读取线程，防止管道缓冲区满导致子进程死锁
    # （npx 在首次下载时 stderr 输出大量进度条，很容易超过 64KB 管道缓冲）
    _start_stderr_reader(proc, name)

    # 快速诊断：非阻塞检查进程是否已立即崩溃（不再固定等待 2s）
    _mcp_debug(f"Checking liveness... pid={proc.pid}")
    exit_code = proc.poll()
    _mcp_debug(f"Process status: exit_code={exit_code}, pid={proc.pid}")
    # 读取启动 stderr（从收集器获取，不再直接读管道）
    early_stderr = _get_stderr_lines(proc)
    if early_stderr:
        _mcp_debug(f"Startup stderr: {early_stderr[:500]}")
    if exit_code is not None:
        stderr_output = _get_stderr_lines(proc)
        _mcp_debug(f"stderr: {stderr_output[:500]}")
        console.print(
            _mcp_t(
                f"❌ MCP server 启动后立即退出 (exit={exit_code})\n   命令: {cmd} {' '.join(args)}\n   stderr: {stderr_output[:500] or '(无)'}",
                f"❌ MCP server exited immediately (exit={exit_code})\n   Command: {cmd} {' '.join(args)}\n   stderr: {stderr_output[:500] or '(none)'}"
            ),
            style="bold red"
        )
        _cleanup_mcp_stderr_buffers(proc.pid)
        return None

    # 发送 initialize 请求
    _mcp_debug("Sending initialize request...")
    _mcp_send(proc, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "Onyx", "version": "2.7.0"},
        },
    })
    # Termux 上二进制已在本地，30s 足够；非 Termux 首次 npx 下载可能较慢给 90s
    init_timeout = 30.0 if _is_on_termux else 90.0
    _mcp_debug(f"Waiting initialize response (timeout={init_timeout}s)...")
    init_result = _mcp_recv(proc, timeout=init_timeout)
    _mcp_debug(f"Initialize result: {'OK' if init_result and 'error' not in init_result else f'FAIL {init_result}'}")

    if init_result is None:
        exit_code = proc.poll()
        stderr_output = ""
        try:
            stderr_output = proc.stderr.read()
        except Exception:
            pass
        proc.kill()
        _cleanup_mcp_stderr_buffers(proc.pid)
        if exit_code is not None:
            err_hint_cn = stderr_output[:500] if stderr_output else "(无 stderr)"
            err_hint_en = stderr_output[:500] if stderr_output else "(no stderr)"
            console.print(
                _mcp_t(
                    f"❌ MCP server 进程异常退出 (exit={exit_code})\n   命令: {cmd} {' '.join(args)}\n   stderr: {err_hint_cn}",
                    f"❌ MCP server crashed (exit={exit_code})\n   Command: {cmd} {' '.join(args)}\n   stderr: {err_hint_en}"
                ),
                style="bold red"
            )
        else:
            # 超时 — 收集更多诊断信息
            diag_lines_cn = [f"   命令: {cmd} {' '.join(args)}"]
            diag_lines_en = [f"   Command: {cmd} {' '.join(args)}"]
            if stderr_output:
                diag_lines_cn.append(f"   stderr: {stderr_output[:500]}")
                diag_lines_en.append(f"   stderr: {stderr_output[:500]}")
            diag_lines_cn.append(f"   提示: MCP server 握手超时（已等待{int(init_timeout)}s），请检查进程是否正常运行")
            diag_lines_en.append(f"   Hint: MCP server handshake timed out after {int(init_timeout)}s, check if the process is running normally")
            console.print(_mcp_t(
                f"❌ MCP server 初始化超时 ({int(init_timeout)}s)\n" + "\n".join(diag_lines_cn),
                f"❌ MCP server init timeout ({int(init_timeout)}s)\n" + "\n".join(diag_lines_en)
            ), style="bold red")
        return None

    if "error" in init_result:
        proc.kill()
        _cleanup_mcp_stderr_buffers(proc.pid)
        console.print(_mcp_t(
            f"❌ MCP server 初始化失败: {init_result['error']}",
            f"❌ MCP server init failed: {init_result['error']}"
        ), style="bold red")
        return None

    # 发送 initialized 通知
    _mcp_notification(proc, "notifications/initialized")

    MCP_SERVER_PROCESSES[name] = proc
    MCP_TOOLS_CACHE.pop(name, None)  # 清空旧缓存

    # 立即拉取工具列表并缓存（避免后续 get_mcp_tools 再次阻塞请求 tools/list）
    # 之前这里只做握手就返回，紧接着 build_mcp_tools_prompt → _discover_mcp_tools
    # 又会发起一次 tools/list 阻塞请求，如果 Node.js stdout 全缓冲或 server 慢响应
    # 就会表现为"AI 立即卡死"
    _mcp_debug("准备发送 tools/list 请求...")
    tools_result = _mcp_request(proc, "tools/list", msg_id=2)
    _mcp_debug(f"tools/list 返回: {'OK' if tools_result and 'result' in tools_result else 'FAIL'}")
    if tools_result and "result" in tools_result:
        tools = tools_result["result"].get("tools", [])
        _mcp_debug(f"解析到 {len(tools)} 个工具")
        MCP_TOOLS_CACHE[name] = tools
        _mcp_debug("已写入 MCP_TOOLS_CACHE")
        # 同步到新版 Registry
        try:
            _mcp_debug("同步到 Registry...")
            registry = get_registry()
            registry.replace_server(name, tools)
            # MCP 工具表已变化 → 失效冻结缓存，下次 handle_ai 重建含新工具
            invalidate_native_tools_cache()
            _mcp_debug("Registry 同步完成")
            # 写入 Schema 缓存（加速下次冷启动）
            home = user_home_dir or USER_HOME_DIR
            _mcp_debug(f"写入 Schema 缓存 (home={home[:30]}...)...")
            config = _load_mcp_config(home)
            server_info2 = config.get("servers", {}).get(name, {})
            if server_info2:
                fp = MCPSchemaCache.fingerprint(server_info2)
                _get_schema_cache().put(name, fp, tools)
                _mcp_debug(f"Schema 缓存写入完成 (fp={fp})")
        except Exception as _e:
            _mcp_debug(f"Registry/缓存同步异常: {_e}")

    # 标记首次连接成功，后续启动仅健康检查
    try:
        _mcp_debug("写入 mcp_connected.flag...")
        flag_path = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "mcp_connected.flag")
        _ensure_dir(os.path.dirname(flag_path))
        with open(flag_path, "w") as _f:
            _f.write(str(time.time()))
        _mcp_debug("mcp_connected.flag 写入完成")
    except Exception as _e2:
        _mcp_debug(f"mcp_connected.flag 写入异常: {_e2}")

    _mcp_debug("即将输出 ✅ 已连接...")
    console.print(_mcp_t(f"✅ MCP server '{name}' 已连接", f"✅ MCP server '{name}' connected"), style="dim")
    _mcp_debug("✅ 已连接输出完成")
    _mcp_debug_exit("connect_mcp_server", ok=True, detail=f"pid={proc.pid}")
    return proc


def preload_mcp_servers(user_home_dir: str = None) -> None:
    """
    预加载 MCP server（后台线程，不阻塞主流程）。
    在 Main.py 初始化阶段调用。
    """
    global MCP_PRELOADED
    with MCP_PRELOAD_LOCK:
        if MCP_PRELOADED:
            return
        MCP_PRELOADED = True  # 防止重复预加载

    home = user_home_dir or USER_HOME_DIR

    def _do_preload():
        try:
            _migrate_mcp_config_if_needed(home)
            # ── 默认零 MCP：不自动下载/连接任何 server ──
            # 仅当用户显式启用 MCP（manage set mcp true 写入 mcp_enabled 文件）
            # 且配置中存在 installed+auto_start 的 server 时才预载（用户配置驱动）。
            _enabled = False
            try:
                _ep = os.path.join(home, ".config", "onyx", "mcp_enabled")
                if os.path.exists(_ep) and os.path.isfile(_ep):
                    with open(_ep, "r") as _f:
                        _enabled = _f.read().strip().lower() in ("true", "1", "yes", "on")
            except Exception:
                pass
            if not _enabled:
                return
            _cfg = _load_mcp_config(home) or {}
            for _sname, _sinfo in (_cfg.get("servers") or {}).items():
                try:
                    if _sinfo.get("installed") and _sinfo.get("auto_start"):
                        if connect_mcp_server(_sname, home):
                            _tools = _discover_mcp_tools(_sname, home)
                            if _tools:
                                console.print(_mcp_t(
                                    f"✅ MCP 预加载: {len(_tools)} 个工具就绪（{_sname}）",
                                    f"✅ MCP preload: {len(_tools)} tools ready ({_sname})"
                                ), style="dim")
                                # 标记预加载已完成，后续启动跳过
                                try:
                                    flag_path = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "mcp_preloaded.flag")
                                    _ensure_dir(os.path.dirname(flag_path))
                                    with open(flag_path, "w") as _f:
                                        _f.write(str(time.time()))
                                except Exception:
                                    pass
                except Exception:
                    pass
        except Exception:
            pass
        except Exception as e:
            pass  # 预加载失败不打扰用户

    t = threading.Thread(target=_do_preload, daemon=True)
    t.start()


def health_check_mcp(user_home_dir: str = None) -> None:
    """
    后台检查 MCP server 健康状态 + 工具增量更新。
    每次 AI 命令后调用（非阻塞）。
    """
    home = user_home_dir or USER_HOME_DIR

    def _do_health_check():
        global _MCP_LAST_HEALTH_CHECK
        now = time.time()
        if now - _MCP_LAST_HEALTH_CHECK < MCP_HEALTH_CHECK_INTERVAL:
            return
        _MCP_LAST_HEALTH_CHECK = now

        for name in list(MCP_SERVER_PROCESSES.keys()):
            if not is_mcp_server_running(name):
                console.print(_mcp_t(
                    f"⚠️ MCP server '{name}' 已断开，尝试重连...",
                    f"⚠️ MCP server '{name}' disconnected, reconnecting..."
                ), style="dim yellow")
                _dead_proc = MCP_SERVER_PROCESSES.pop(name, None)
                if _dead_proc is not None:
                    try:
                        _cleanup_mcp_stderr_buffers(_dead_proc.pid)
                    except Exception:
                        pass
                connect_mcp_server(name, home)

        # 增量更新工具缓存
        for name in list(MCP_SERVER_PROCESSES.keys()):
            try:
                old_tools = MCP_TOOLS_CACHE.get(name, [])
                old_names = {t.get("name") for t in old_tools}
                new_tools = _discover_mcp_tools(name, home)
                new_names = {t.get("name") for t in new_tools}
                added = new_names - old_names
                removed = old_names - new_names
                if added or removed:
                    MCP_TOOLS_CACHE[name] = new_tools
                    if added:
                        console.print(_mcp_t(
                            f"🔧 MCP 工具新增: {added}",
                            f"🔧 MCP tools added: {added}"
                        ), style="dim")
                    if removed:
                        console.print(_mcp_t(
                            f"🔧 MCP 工具移除: {removed}",
                            f"🔧 MCP tools removed: {removed}"
                        ), style="dim")
            except Exception:
                pass

    t = threading.Thread(target=_do_health_check, daemon=True)
    t.start()


def _schedule_mcp_health_check(user_home_dir: str = None) -> None:
    """每次 AI 命令后调度后台健康检查（非阻塞）"""
    health_check_mcp(user_home_dir)


def _discover_mcp_tools(name: str = "filesystem", user_home_dir: str = None) -> List[Dict]:
    """从 MCP server 获取工具列表（内部，带缓存 + Registry 同步）"""
    if name in MCP_TOOLS_CACHE:
        return MCP_TOOLS_CACHE[name]

    proc = connect_mcp_server(name, user_home_dir)
    if proc is None:
        return []

    result = _mcp_request(proc, "tools/list", msg_id=2)
    if result is None or "error" in result:
        console.print(_mcp_t(
            f"⚠️ 获取 MCP 工具列表失败: {result.get('error', 'timeout') if result else 'timeout'}",
            f"⚠️ Failed to get MCP tool list: {result.get('error', 'timeout') if result else 'timeout'}"
        ), style="yellow")
        return []

    tools = result.get("result", {}).get("tools", [])
    MCP_TOOLS_CACHE[name] = tools

    # ── 同步到新版 Registry ──
    try:
        registry = get_registry()
        registry.replace_server(name, tools)
        # MCP 工具表已变化 → 失效冻结缓存，下次 handle_ai 重建含新工具
        invalidate_native_tools_cache()
        # 写入 Schema 缓存（加速下次冷启动）
        home = user_home_dir or USER_HOME_DIR
        config = _load_mcp_config(home)
        server_info = config.get("servers", {}).get(name, {})
        if server_info:
            fp = MCPSchemaCache.fingerprint(server_info)
            _get_schema_cache().put(name, fp, tools)
    except Exception:
        pass

    return tools


def get_mcp_tools(name: str = "filesystem", user_home_dir: str = None) -> List[Dict]:
    """
    获取 MCP 工具列表，过滤掉 shell/bash 类工具。
    优先从 Registry 读取（支持 lazy 加载的缓存 schema），回退到旧 MCP_TOOLS_CACHE。
    返回: [{"name": "...", "description": "...", "inputSchema": {...}}, ...]
    """
    # 尝试从 Registry 获取（可能已通过缓存预加载）
    registry = get_registry()
    registry_tools = registry.get_by_server(name)
    if registry_tools:
        all_tools = registry_tools
    else:
        # 回退：旧版缓存（会触发 connect + tools/list）
        all_tools = _discover_mcp_tools(name, user_home_dir)
    filtered = []
    for tool in all_tools:
        tool_name = (tool.get("name") or "").lower()
        # 过滤 shell/bash 类工具
        if tool_name in MCP_TOOL_FILTER:
            continue
        # 子串匹配过滤
        blocked = any(
            kw in tool_name
            for kw in ["shell", "bash", "exec", "spawn", "terminal"]
        )
        if blocked:
            continue
        filtered.append(tool)
    return filtered

