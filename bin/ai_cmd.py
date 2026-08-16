
# -------------------------- 1. 基础模块导入 + 配置导入 --------------------------

import sys
import os
import time
import threading
import json
import gzip
import uuid
import ctypes
import warnings
import platform
import shutil
import shlex
import re
import secrets
from typing import List, Tuple, Optional, Dict, Any, Callable

# ── 自研文件编辑系统 ──
from datetime import datetime, timedelta
# prompt_toolkit 属重量级依赖（~1s），延迟到 handle_ai 内局部导入——启动提速，行为不变
import urllib3
warnings.filterwarnings('ignore', category=urllib3.exceptions.InsecureRequestWarning)

from rich.console import Console
from rich.text import Text as RichText
console = Console()

# AI 工具已切换为 MCP 协议（见下方 MCP 客户端模块），不再使用 plugin_loader
# 保留导入以兼容旧代码引用（后续可安全移除）
# UI 增强模块（Rich + InquirerPy，未安装 InquirerPy 时自动回退）
from .ai_lib.ui import (
    select_option,
    confirm_dangerous as ui_confirm_dangerous,
    text_input as ui_text_input,
    render_plan_panel,
    render_analysis_panel,
    render_warning_panel,
    render_ai_panel,
    render_tool_table,
    render_separator,
)

# ── 从子模块导入配置 / 密钥 / 情感 / URL / 语言等 ──
from .ai_lib.config import (
    ROOT_DIR, USER, USER_HOME_DIR,
    LANGUAGE_CONFIG_PATH, help_info_path, onyx_config_path,
    AI_KEY_DIR, AI_KEY_PATH, KEY_CONF_PATH, SERVER_URL_FILE,
    _load_ai_models, _SUPPORTED_PLATFORMS,
    _obfuscate, _deobfuscate,
    load_key_conf, save_key_conf, _setup_key_conf_interactive,
    _render_edit_diff,
    get_server_url, get_current_lang, get_prompt_text,
    load_ai_key, verify_ai_key,
    AI_KEY, SERVER_URL,
)

# （配置/密钥/情感/URL/语言/许可证等已移至 bin/ai_lib/config.py）

# （缓存 / 聊天记忆 / 会话记录 已移至 bin/ai_lib/storage.py）
from .ai_lib.storage import (
    get_ai_cmd_cache_path, save_ai_commands, clear_ai_cmd_cache,
    get_chat_json_path, get_current_chat_name, set_current_chat_name,
    load_chat_json, save_chat_json, get_class_retention_days,
    clean_expired_messages, append_message_to_chat, update_message_tag,
    get_previous_session_uuid, list_chat_memories, create_chat_memory,
    switch_chat_memory, load_chat_memory_for_context,
    get_ai_session_library_dir, get_latest_ai_session,
    load_memory_by_uuid, record_ai_session,
)

# ── 任务管理系统 ──
from lib.task_system import (
    TaskRegistry, TeamRegistry, CronRegistry,
    TaskPacket, TaskScope, TaskResource, TaskStatus,
    validate_packet, packet_to_dict, dict_to_packet,
)

# ── 恢复配方 ──
from lib.recovery_recipes import (
    RecoveryContext, classify_failure, get_recovery_message, record_attempt,
    FailureScenario, RecoveryAction,
)
from lib.approval_tokens import (
    ApprovalTokenLedger, ApprovalScope,
)

# （解析已统一走 bin/ai_lib/parsers.py，纯 Markdown 直通，无标记语言）
from .ai_lib.lang import get_lang_text
from .ai_lib.i18n import _ as _i18n  # 双语文本（中英）
from .ai_lib.tools import code_analysis  # 代码分析工具（py_*/Lsp*，独立工具包）
# 记忆工具执行器（MemoryRead/MemorySearch/remember/forget/memory/compact_stats 已
# 提取至 bin/ai_lib/memory_tools.py；记忆根 set/get_memory_home 亦在其中）
from .ai_lib.memory_tools import (
    set_memory_home, get_memory_home,
    _resolve_memory_path, _get_file_uuid, _cache_query,
    _exec_memory_read, _exec_memory_search,
    _exec_remember_session, _exec_forget_session,
    _exec_search_library, _exec_list_hippocampus,
    _exec_read_memory, _exec_compact_stats,
    _exec_list_timeline,
)
from .ai_lib.grep_utils import _run_grep_lines  # 文件搜索核心（MemorySearch/grep_search 共用）
from .ai_lib.helpers import (
    handle_sleep_wait, set_ai_thread_priority, confirm_plan,
    parse_arguments, show_loading,
    init_ai_dangerous_commands, load_ai_dangerous_commands,
    init_ai_extra_dangerous_commands, load_ai_extra_dangerous_commands,
    is_dangerous_command, is_extra_dangerous_command, confirm_dangerous_command, has_forbidden_syntax,
)
from .ai_lib.mcp_state import (
    _AI_INTERRUPTED, _MCP_DEBUG, _MCP_DEBUG_START,
    MCP_SERVER_PROCESSES, MCP_TOOLS_CACHE, MCP_TRANSPORTS,
    MCP_CONFIG_PATH, MCP_PRELOADED, MCP_PRELOAD_LOCK, MCP_INSTALL_LOCK,
    MCP_HEALTH_CHECK_INTERVAL, _MCP_LAST_HEALTH_CHECK,
    _MCP_STDERR_BUFFERS, _MCP_STDERR_LOCKS,
    _mcp_debug, _mcp_debug_enter, _mcp_debug_exit,
    _PLAN_MODE_ACTIVE, _thread_locals,
)
# _MANUAL_COMPACT_REQUESTED 通过 mcp_state 模块属性访问（避免 from-import 拷贝问题）
from .ai_lib import mcp_state as _mcp_shared
# （mcp_client.py 为历史遗留模块，当前无调用方；MCP 进程注册表由本文件自管，
#   见下方“旧版兼容变量”——避免与 mcp_state 双注册表）
# AI 虚拟沙盒（虚拟根 / 映射为 cwd，文件工具路径拦截与脱敏）
from .ai_lib import sandbox
from .ai_lib.sandbox import SandboxBlockError as _SandboxBlockError

# 10.5 MCP (Model Context Protocol) 客户端模块
#    替代原 plugin_loader 插件系统，通过本地 MCP server 提供 AI 工具
#    - 出厂自动安装 @modelcontextprotocol/server-filesystem
#    - 用户可通过 ai -mcp install/remove/list 管理
#    - 工具列表中过滤 shell/bash 类工具（Onyx 已有 shell 接口）
#    - edit_file/write_file 在 mid 及以上模式允许（low 禁止）
#
#    v2.7 — 架构重构：
#      - Transport 抽象层: bin/ai_lib/mcp_transport.py
#      - Registry 模式:    bin/ai_lib/mcp_registry.py
#      - Schema 缓存指纹:  加速冷启动
# ========================================================================

import subprocess
import signal

# ── 新版抽象层 ──
from .ai_lib.mcp_transport import (
    Transport, StdioTransport, create_transport,
)
from .ai_lib.mcp_registry import (
    MCPRegistry, MCPSchemaCache, get_registry, reset_registry,
)

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
                    except (OSError, BlockingIOError, ValueError):
                        break
            else:
                # 回退：TextIOWrapper 逐行读取
                for line in proc.stderr:
                    line = line.strip()
                    if line:
                        with lock:
                            _MCP_STDERR_BUFFERS[pid].append(line)
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
    if _MCP_DEBUG:
        import sys as _sys
        elapsed = (time.time() - _MCP_DEBUG_START) if _MCP_DEBUG_START else 0
        _sys.stderr.write(f"[{elapsed:06.2f}s] MCP {msg}\n")
        _sys.stderr.flush()


def _mcp_debug_enter(func_name: str) -> None:
    """函数进入时的 debug 追踪"""
    if _MCP_DEBUG:
        import sys as _sys
        elapsed = (time.time() - _MCP_DEBUG_START) if _MCP_DEBUG_START else 0
        _sys.stderr.write(f"[{elapsed:06.2f}s] → {func_name}\n")
        _sys.stderr.flush()


def _mcp_debug_exit(func_name: str, ok: bool = True, detail: str = "") -> None:
    """函数退出时的 debug 追踪"""
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
        # 自动修正：替换越界路径为 CWD
        fixed_args = []
        for a in server_info.get("args", []):
            if a.startswith("/") and not a.startswith("-"):
                fixed_args.append(fallback_dir)
            else:
                fixed_args.append(a)
        server_info["args"] = fixed_args

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
                MCP_SERVER_PROCESSES.pop(name, None)
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


def build_mcp_tools_prompt(lang: str = "chinese", user_home_dir: str = None) -> str:
    """
    构建注入给 AI 的工具说明提示词。
    文件操作已由原生标记语言覆盖，这里只展示非文件类 MCP 工具。
    """
    _mcp_debug_enter("build_mcp_tools_prompt")
    tools = get_mcp_tools(user_home_dir=user_home_dir)

    # ── 过滤掉 filesystem 工具（文件操作用原生标记语言）──
    non_file_tools = []
    for t in tools:
        name = t.get("name", "")
        # filesystem 工具的常见名
        if name in ("read_file", "write_file", "edit_file",
                     "create_directory", "list_directory",
                     "directory_tree", "move_file", "copy_file",
                     "delete_file", "delete_directory",
                     "get_file_info", "search_files", "search_content",
                     "glob", "find_on_path", "get_workspace_folders"):
            continue
        non_file_tools.append(t)

    _mcp_debug(f"get_mcp_tools 返回 {len(tools)} 个工具，过滤后 {len(non_file_tools)} 个")

    if not non_file_tools:
        # 没有非文件 MCP 工具，返回空字符串（不占用 prompt 空间）
        _mcp_debug_exit("build_mcp_tools_prompt", ok=True, detail="only file tools, skipped")
        return ""

    lines = []
    lines.append("## Non-file Tools (Function Calling)")
    lines.append("All tools use standard function calling (tool_calls) — call them directly through the API, never in text.")
    lines.append("The tools are already in your API function calling list — call them directly.")

    lines.append("")

    for tool in non_file_tools:
        raw_name = tool.get("name", "?")
        full_name = raw_name  # 不再加 mcp__filesystem__ 前缀
        desc = tool.get("description", "")
        schema = tool.get("inputSchema", {})
        props = schema.get("properties", {})
        required = schema.get("required", [])

        # 构建 JSON 参数说明
        param_entries = []
        for pname, pinfo in props.items():
            req_mark = " (required)" if pname in required else ""
            ptype = pinfo.get("type", "string")
            pdesc = pinfo.get("description", "")
            param_entries.append(f'    "{pname}": {{{{ {ptype} }}}}{req_mark} — {pdesc}')

        lines.append(f"- **{full_name}**: {desc}")
        if param_entries:
            lines.append("  params:")
            lines.extend(param_entries)

        lines.append("")

    result = "\n".join(lines)
    _mcp_debug_exit("build_mcp_tools_prompt", ok=len(tools) > 0, detail=f"{len(tools)} tools, {len(result)} chars")
    return result


def build_native_tools_prompt() -> str:
    """Build AI tool guide — pure English, function calling only."""
    lines = []
    lines.append("## File Operations (Function Calling)")
    lines.append("Use standard function calling tools for file read/write/edit.")
    lines.append("")
    lines.append("### Available Tools")
    lines.append("- `get_file_info(path)` — Get file info (size/lines/mtime)")
    lines.append("- `read_file(path, range?)` — Read file, range='10-30' for line range")
    lines.append("- `edit_file(path, old_string, new_string)` — SEARCH/REPLACE edit")
    lines.append("- `write_file(path, content)` — Create/overwrite file")
    lines.append("- `validate_edit(file_path, search, replace)` — Validate SEARCH exists and unique")
    lines.append("- `preview_edit(file_path, search, replace)` — Preview diff")
    lines.append("")
    lines.append("### Guidelines")
    lines.append("1. **Check first**: Call `get_file_info` then `read_file` before editing")
    lines.append("2. **Prefer edit_file**: Local changes → `edit_file`; new file or >70% change → `write_file`")
    lines.append("3. **Large file chunking — MUST**: Files >20KB: create a skeleton with `write_file`, then fill with multiple `edit_file` chunks (<200 lines each). NEVER write the full content of a >20KB file in one `write_file` — it truncates and corrupts. Read back to verify.")
    lines.append("4. **Validate before edit**: Always call `validate_edit` before `edit_file`")
    lines.append("5. **Unique anchor**: `edit_file` old_string must be byte-exact and unique")
    lines.append("6. **Shell**: use `RunCommand(command)` tool for shell commands — output is captured and returned")
    lines.append("")
    lines.append("### Planning Tools")
    lines.append("- `submit_plan(plan, steps?)` — Submit plan for user approval; steps can be structured")
    lines.append("- `mark_step_complete(step_id)` — Mark one step done after completion")
    lines.append("- `TodoWrite(todos)` — Track in-session task list for multi-step work")
    lines.append("")
    lines.append("### Communication Tools")
    lines.append("- `choose_ask(question, options)` — Present options to user when uncertain")
    lines.append("- `Skill(name, args?)` — Load a reusable skill playbook (e.g. debug, task-workflow, refactor)")
    lines.append("")
    lines.append("> Reply in plain Markdown only — your text is displayed to the user as-is. No wrappers, no special formats: just speak naturally in Markdown.")
    return "\n".join(lines)


# ── 权限级别常量 ──
PERM_READONLY = "ReadOnly"           # 安全只读，自动放行
PERM_WORKSPACE_WRITE = "WorkspaceWrite"  # 修改工作区，需轻确认
PERM_DANGER_FULL = "DangerFullAccess"    # 危险操作，需显式批准


def _make_tool(name: str, description: str, properties: dict, required: list,
               permission: str = PERM_READONLY) -> Dict:
    """构建标准 OpenAI function calling 工具定义。

    描述自动本地化：优先从 i18n 模块读取 tool_desc.<name> /
    tool_p.<name>.<param>（跟随当前 UI 语言），未收录时回退到代码内嵌的默认文本。
    单语言而非双语——双语拼接让每条描述体积翻倍（AI 侧信息完全重复）。
    """
    _tool_lang = get_current_lang()  # "chinese" / "english"，跟随 /lang 切换
    _desc = _i18n(f"tool_desc.{name}", _tool_lang)
    if _desc == f"tool_desc.{name}":
        _desc = description
    # ── 权限文案自动生成：描述与强制层保持一致，防止“承诺了但没强制”──
    # 仅当使用代码内嵌描述（无 i18n 覆盖）时追加权限声明。
    if _desc == description and permission in (PERM_WORKSPACE_WRITE, PERM_DANGER_FULL):
        _perm_hint = {
            PERM_WORKSPACE_WRITE: (
                "（写入工作区：自动放行，可用 UndoLastEdit 撤销）"
                if _tool_lang == "chinese"
                else " (workspace write: auto-approved, reversible via UndoLastEdit)"
            ),
            PERM_DANGER_FULL: (
                "（危险操作：需用户显式批准，low/mid 模式弹确认）"
                if _tool_lang == "chinese"
                else " (dangerous access: requires explicit user approval in low/mid mode)"
            ),
        }.get(permission, "")
        if _perm_hint:
            _desc = _desc.rstrip() + _perm_hint
    _props = {}
    for _pkey, _pval in properties.items():
        _pval = dict(_pval)
        _pdesc = _i18n(f"tool_p.{name}.{_pkey}", _tool_lang)
        if _pdesc != f"tool_p.{name}.{_pkey}":
            _pval["description"] = _pdesc
        # 嵌套参数（array items 的 properties）同样本地化
        _items = _pval.get("items")
        if isinstance(_items, dict):
            _items = dict(_items)
            _sub_props = _items.get("properties")
            if isinstance(_sub_props, dict):
                for _skey, _sval in list(_sub_props.items()):
                    _sdesc = _i18n(f"tool_p.{name}.{_pkey}.{_skey}", _tool_lang)
                    if _sdesc != f"tool_p.{name}.{_pkey}.{_skey}":
                        _sval = dict(_sval)
                        _sval["description"] = _sdesc
                        _sub_props[_skey] = _sval
            _pval["items"] = _items
        _props[_pkey] = _pval
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": _desc,
            "parameters": {
                "type": "object",
                "properties": _props,
                "required": required,
                "additionalProperties": False,
            },
        },
        "x_permission": permission,  # 自定义字段，用于执行时权限检查
    }


def build_native_tools(user_home_dir: str = None) -> List[Dict]:
    """Build OpenAI-compatible tools array — full Onyx native tool set.

    Permission levels: ReadOnly (auto), WorkspaceWrite (light confirm), DangerFullAccess (approval).
    Each tool has exact JSON Schema parameters (type, enum, required, additionalProperties=False).
    """
    _mcp_debug_enter("build_native_tools")

    native = [
        # ═══════════════════════════════════════════
        # ReadOnly — 安全只读，自动放行
        # ═══════════════════════════════════════════

        _make_tool(
            "get_file_info",
            "获取文件基本信息：大小、修改时间、行数、类型。修改文件前先调用此工具了解概况。",
            {"path": {"type": "string", "description": "文件路径"}},
            ["path"],
            PERM_READONLY,
        ),
        _make_tool(
            "read_file",
            "读取文件内容。支持行号范围 range、head、tail。超过 64 KiB 的大文件自动返回大纲模式（文件大小、前 80 行、符号大纲与钻取提示）。改文件前务必先读文件确认当前内容。",
            {
                "path": {"type": "string", "description": "文件路径"},
                "range": {"type": "string", "description": "可选行号范围，如 '10-30' 或 '42'（单行）"},
                "head": {"type": "integer", "description": "可选：只读前 N 行"},
                "tail": {"type": "integer", "description": "可选：只读末尾 N 行"},
            },
            ["path"],
            PERM_READONLY,
        ),
        _make_tool(
            "glob_search",
            "使用 glob 模式查找文件。如 'src/**/*.ts' 查找所有 TypeScript 文件。",
            {
                "pattern": {"type": "string", "description": "Glob 模式，如 'src/**/*.py'"},
                "path": {"type": "string", "description": "可选搜索根目录，默认当前工作目录"},
            },
            ["pattern"],
            PERM_READONLY,
        ),
        _make_tool(
            "grep_search",
            "用正则搜索文件内容，支持上下文行与大小写控制。",
            {
                "pattern": {"type": "string", "description": "搜索的正则表达式"},
                "path": {"type": "string", "description": "可选搜索根目录"},
                "glob": {"type": "string", "description": "可选文件过滤，如 '*.py'"},
                "context": {"type": "integer", "description": "可选上下各行数，默认 0"},
                "-i": {"type": "boolean", "description": "可选忽略大小写，默认 false"},
                "head_limit": {"type": "integer", "description": "可选结果数量上限"},
            },
            ["pattern"],
            PERM_READONLY,
        ),
        _make_tool(
            "search_file",
            "按文件名关键字在目录树中递归查找文件（自动跳过 node_modules/.git/__pycache__ 等依赖目录）。返回完整路径列表，不截断。",
            {
                "pattern": {"type": "string", "description": "文件名关键字（子串匹配，不区分大小写）或 glob 模式"},
                "path": {"type": "string", "description": "可选搜索根目录，默认当前工作目录"},
            },
            ["pattern"],
            PERM_READONLY,
        ),
        _make_tool(
            "ToolSearch",
            "搜索可用工具的名称或关键字。不知道用什么工具时调用此工具查找。",
            {"query": {"type": "string", "description": "搜索关键词，如 'file'、'search'、'web'"}},
            ["query"],
            PERM_READONLY,
        ),
        _make_tool(
            "Skill",
            "加载并执行一个技能剧本。技能是预定义的可复用操作流程。",
            {
                "skill": {"type": "string", "description": "技能名称"},
                "args": {"type": "string", "description": "可选参数"},
            },
            ["skill"],
            PERM_READONLY,
        ),
        _make_tool(
            "Sleep",
            "等待指定秒数。用于监控、等待异步操作等场景。",
            {"seconds": {"type": "integer", "minimum": 1, "description": "等待秒数"}},
            ["seconds"],
            PERM_READONLY,
        ),
        _make_tool(
            "StructuredOutput",
            "以请求的格式返回结构化数据。format='json'时返回 JSON 字符串。",
            {
                "format": {"type": "string", "enum": ["json"], "description": "输出格式"},
                "data": {"type": "string", "description": "要结构化的数据"},
            },
            ["format", "data"],
            PERM_READONLY,
        ),
        _make_tool(
            "TodoWrite",
            "更新会话任务列表，跟踪多步骤进度；status=completed 表示该步完成。",
            {
                "todos": {
                    "type": "array",
                    "description": "任务列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "任务描述"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"],
                                       "description": "任务状态"},
                            "activeForm": {"type": "string", "description": "进行中状态的动名词描述，如'正在分析架构'"},
                        },
                        "required": ["content", "status", "activeForm"],
                        "additionalProperties": False,
                    },
                }
            },
            ["todos"],
            PERM_WORKSPACE_WRITE,
        ),

        # ═══════════════════════════════════════════
        # WorkspaceWrite — 修改工作区，需轻确认
        # ═══════════════════════════════════════════

        _make_tool(
            "write_file",
            "创建新文件或全量覆盖（仅新建或 >70% 变动；局部修改用 edit_file）。>20KB 新文件先建骨架，再分多次 edit_file 填入。",
            {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整的文件内容"},
            },
            ["path", "content"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "edit_file",
            "SEARCH/REPLACE 精确替换；old_string 须逐字节匹配且唯一；改前先 validate_edit 校验；保留缩进。写入大文件必须分块：骨架 + 多次 edit_file（每块 <200 行），禁止一次性全量 write_file。",
            {
                "path": {"type": "string", "description": "目标文件路径"},
                "old_string": {"type": "string", "description": "要替换的旧文本（逐字节精确匹配，必须唯一）"},
                "new_string": {"type": "string", "description": "替换后的新文本"},
                "replace_all": {"type": "boolean", "description": "可选：是否替换所有匹配项（默认只替换第一个）"},
            },
            ["path", "old_string", "new_string"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "validate_edit",
            "校验 SEARCH 文本在目标文件中存在且唯一；每次 edit_file 前务必先调用。",
            {
                "file_path": {"type": "string", "description": "目标文件路径"},
                "search": {"type": "string", "description": "要搜索的旧文本（逐字节精确匹配）"},
                "replace": {"type": "string", "description": "替换后的新文本"},
            },
            ["file_path", "search", "replace"],
            PERM_READONLY,  # 校验是安全的
        ),
        _make_tool(
            "preview_edit",
            "预览 edit_file 的 unified diff，确认正确后再编辑。",
            {
                "file_path": {"type": "string", "description": "目标文件路径"},
                "search": {"type": "string", "description": "要搜索的旧文本"},
                "replace": {"type": "string", "description": "替换后的新文本"},
            },
            ["file_path", "search", "replace"],
            PERM_READONLY,  # 预览是安全的
        ),
        _make_tool(
            "remember",
            "标记 library 会话为重要（提升保留等级，不被压缩清理）。",
            {
                "session_id": {"type": "string", "description": "library 会话 UUID，如 abc123-def456"},
            },
            ["session_id"],
            PERM_READONLY,
        ),
        _make_tool(
            "forget",
            "归档 library 会话（移至 .archive/，可恢复）。",
            {
                "session_id": {"type": "string", "description": "library 会话 UUID"},
            },
            ["session_id"],
            PERM_READONLY,
        ),
        _make_tool(
            "memory",
            "操作 library 历史会话与时间线：search 按关键词搜索；list 列出活跃记忆，或传 day/month/year/start/end 查询时间线（当日任务/当月每日描述/当年每月描述/区间）；read 用 session_id 读完整记录。",
            {
                "operation": {"type": "string", "enum": ["search", "list", "read"], "description": "search/list/read"},
                "query": {"type": "string", "description": "搜索关键词（search 时必填）"},
                "session_id": {"type": "string", "description": "会话 UUID（read 时必填）"},
                "filter": {"type": "string", "description": "过滤 class 等级（list 时可选）"},
                "limit": {"type": "integer", "description": "返回结果数，默认 8，最大 20"},
                "day": {"type": "string", "description": "时间线：查询指定日 'YYYY-M-D'（如 2026-2-12）当日任务列表"},
                "month": {"type": "string", "description": "时间线：查询指定月 'YYYY-M'（如 2026-6）该月每日描述"},
                "year": {"type": "string", "description": "时间线：查询指定年 'YYYY'（如 2026）该年每月描述"},
                "start": {"type": "string", "description": "时间线：区间起始日 'YYYY-M-D'（配合 end 查询几日到几日的工作内容）"},
                "end": {"type": "string", "description": "时间线：区间结束日 'YYYY-M-D'"},
                "skill": {"type": "string", "description": "预留：按技能维度过滤时间线（当前版本仅透传）"},
            },
            ["operation"],
            PERM_READONLY,
        ),
        _make_tool(
            "compact_stats",
            "查看 library 压缩状态：活跃/归档数、估算 token、触发阈值。",
            {},
            [],
            PERM_READONLY,
        ),
        _make_tool(
            "choose_ask",
            '不确定用户意图时提供选项；用户可选「以上都不是」自由输入。',
            {
                "question": {"type": "string", "description": "向用户提出的问题"},
                "options": {
                    "type": "array",
                    "description": "选项列表（至少2个，最多6个）",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 6,
                },
            },
            ["question", "options"],
            PERM_READONLY,
        ),
        _make_tool(
            "submit_plan",
            "提交计划给用户确认（系统门禁：大型写操作——单次 >4KB 或本轮累计 ≥8KB——与破坏性操作（删除/移动/复制/建目录）在确认前会被拦截；小型修改可直接执行）。plan 与 steps 二选一；确认后按步骤执行。",
            {
                "plan": {"type": "string", "description": "Markdown 格式的计划描述"},
                "steps": {
                    "type": "array",
                    "description": "结构化步骤列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "步骤 ID，如 step-1"},
                            "title": {"type": "string", "description": "简短标题"},
                            "action": {"type": "string", "description": "具体操作描述"},
                            "risk": {"type": "string", "enum": ["low", "med", "high"], "description": "风险等级"},
                        },
                        "required": ["id", "title"],
                        "additionalProperties": False,
                    },
                },
            },
            ["plan"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "mark_step_complete",
            "标记一个步骤已完成。提交计划后每完成一步调用此工具更新进度。",
            {"step_id": {"type": "string", "description": "步骤 ID，如 step-1"}},
            ["step_id"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "EnterPlanMode",
            "进入计划模式（禁止命令与文件修改，只能输出计划）；进入后应立即用 submit_plan 提交计划。",
            {},
            [],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "ExitPlanMode",
            "退出计划模式，恢复正常执行；计划确认后调用并开始执行。",
            {},
            [],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "Config",
            "获取或设置 Onyx 配置：get 返回当前配置，set 设置键值。"
            "注意：set 会写入 ~/.config/onyx/config.json（cwd 沙盒之外），需要用户显式批准。",
            {
                "action": {"type": "string", "enum": ["get", "set"], "description": "操作类型"},
                "key": {"type": "string", "description": "配置键名"},
                "value": {"type": "string", "description": "配置值（set 时需要）"},
            },
            ["action", "key"],
            PERM_DANGER_FULL,
        ),

        # ═══════════════════════════════════════════
        # ═══════════════════════════════════════════
        # DangerFullAccess — 危险操作，需显式批准
        # ═══════════════════════════════════════════

        _make_tool(
            "Agent",
            "启动子代理（隔离上下文，总结后喂回主 AI）。类型：explore=只读调查；plan=规划（只读+git）；lint=代码分析；test=测试；web_search_agent=联网调研（web_search 多重混合搜索+抓页）。所有类型均可经安全管线执行命令（危险命令与 Onyx 内置命令如 exit/clear/ai 不可用）。explore/plan 自动执行无需用户确认；lint/test/web_search_agent 需显式批准。适合大规模只读调查或可并行子任务——主上下文只接收总结，注意不要滥用。可指定 1~5 个任务并行（最多 5 个同时运行）。mode=sync 阻塞等待总结；mode=async 立即返回，完成后结果自动注入会话。**并行调查多个主题时，请用 `tasks` 数组在一次调用中派发，不要多次调用本工具。**",
            {
                "description": {"type": "string", "description": "子代理任务描述"},
                "prompt": {"type": "string", "description": "子代理的完整指令；多任务时可用 '1. ...\\n2. ...' 编号或 --- 分隔，配合 count 并行"},
                "name": {"type": "string", "description": "可选子代理名称"},
                "type": {"type": "string", "enum": ["explore", "plan", "lint", "test", "web_search_agent"], "description": "子代理类型（默认 explore）"},
                "mode": {"type": "string", "enum": ["sync", "async"], "description": "sync=等待完成并返回总结；async=后台运行，完成自动注入（默认 sync）"},
                "model": {"type": "string", "description": "可选模型名覆盖；plan 类型未指定时默认自动升档到同系列更强模型（如 flash→pro）"},
                "count": {"type": "integer", "description": "并行子代理数量 1~5（默认 1；tasks 存在时按 tasks 长度）"},
                "tasks": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string", "description": "任务指令（使用调用级 type/model）"},
                            {"type": "object", "properties": {
                                "prompt": {"type": "string", "description": "该子代理的任务指令（必填）"},
                                "type": {"type": "string", "enum": ["explore", "plan", "lint", "test", "web_search_agent"], "description": "可选：该子代理角色（决定系统提示词与工具集），默认与调用级 type 一致"},
                                "model": {"type": "string", "description": "可选：该子代理使用的模型"},
                                "name": {"type": "string", "description": "可选：该子代理名称（显示用）"},
                            }, "required": ["prompt"]},
                        ]
                    },
                    "description": "可选：1~5 个子任务；每个元素可为字符串（指令）或对象（prompt + 可选 type/model/name）——每个子代理独立提示词/角色/模型，完全独立工作",
                },
            },
            ["description", "prompt"],
            PERM_DANGER_FULL,
        ),
        _make_tool(
            "web_search",
            "网络调研全能工具（唯一 web 工具，旧 WebSearch/WebFetch 已合并）。三模式：search=仅多引擎搜索；fetch=抓取指定 urls 的页面正文；mixed=搜索+自动抓页（默认）。用法：先 search 看 snippet 摘要判断相关性，需要正文细节再 fetch_pages/mixed；queries 建议 ≤3 个；权威站点用 allowed_domains 限定。支持多查询 × 多引擎、域名双向过滤、语言/地区/时效、安全搜索、正文长度控制、text/json 双输出。引擎可用性自动降级、结果带短时缓存，无需额外处理。可选 ai_assist=长文弱 AI 摘要开关（缺省跟随全局 web_ai_assist）。查资料、查文档、对比信息首选。自动执行。",
            {
                "action": {"type": "string", "enum": ["search", "fetch", "mixed"], "description": "操作模式：search=仅搜索；fetch=仅抓取 urls 指定页面；mixed=搜索+抓取（默认）"},
                "ai_assist": {"type": "boolean", "description": "长文弱 AI 摘要：true=长文完整交给辅助 AI 总结后返回摘要；false=关键行压缩；缺省=跟随全局开关 web_ai_assist（Config 工具设置）"},
                "query": {"type": "string", "description": "主搜索查询（action=search/mixed 必填；fetch 模式可省略）"},
                "queries": {"type": "array", "items": {"type": "string"}, "description": "附加查询列表（混合查资料：一次覆盖多个角度，最多 10 个）"},
                "topics": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "该主题的搜索查询（必填）"},
                            "engines": {"type": "array", "items": {"type": "string", "enum": ["duckduckgo", "bing"]}, "description": "该主题搜索引擎（默认继承顶层）"},
                            "max_results": {"type": "integer", "minimum": 1, "maximum": 15, "description": "该主题条数上限（默认继承顶层）"},
                            "fetch_pages": {"type": "boolean", "description": "该主题是否自动抓页（默认继承顶层）"},
                            "fetch_limit": {"type": "integer", "minimum": 1, "maximum": 5, "description": "该主题抓页上限（默认继承顶层）"},
                            "max_chars_per_page": {"type": "integer", "minimum": 500, "maximum": 8000, "description": "该主题单页字符上限（默认继承顶层）"},
                            "ai_assist": {"type": "boolean", "description": "该主题长文摘要开关（默认继承顶层）"},
                            "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "该主题域名白名单（默认继承顶层）"},
                            "exclude_domains": {"type": "array", "items": {"type": "string"}, "description": "该主题域名黑名单（默认继承顶层）"},
                        },
                        "required": ["query"],
                    },
                    "description": "批量独立主题（最多 5 个）：一次并行查询多个互不相关的主题，每主题独立搜索+抓页+分栏输出；与 queries 不同——queries 是同一主题的多角度，topics 是多个独立主题",
                },
                "urls": {"type": "array", "items": {"type": "string"}, "description": "指定 URL 列表直接抓取正文（action=fetch 必填；mixed 时追加抓取；同样过域名过滤与 SSRF 防护）"},
                "engines": {"type": "array", "items": {"type": "string", "enum": ["duckduckgo", "bing"]}, "description": "搜索引擎列表（默认两者都用）"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 15, "description": "每个查询每个引擎返回条数上限（默认 8）"},
                "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "仅保留这些域名下的结果（如 github.com）"},
                "exclude_domains": {"type": "array", "items": {"type": "string"}, "description": "排除这些域名下的结果"},
                "language": {"type": "string", "description": "语言偏好（如 zh/en，best-effort）"},
                "region": {"type": "string", "description": "地区偏好（如 cn-zh/us-en，best-effort）"},
                "time_range": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "时效过滤（best-effort，仅支持的引擎生效）"},
                "safe_search": {"type": "boolean", "description": "安全搜索：严格模式过滤成人内容（默认 false）"},
                "fetch_pages": {"type": "boolean", "description": "搜索后自动抓取排名靠前结果的页面正文（默认 false）"},
                "fetch_limit": {"type": "integer", "minimum": 1, "maximum": 5, "description": "自动抓取页数上限 1~5（默认 3）"},
                "max_chars_per_page": {"type": "integer", "minimum": 500, "maximum": 8000, "description": "单页正文最大字符数（默认 3000）"},
                "output_format": {"type": "string", "enum": ["text", "json"], "description": "输出格式：text=易读文本；json=结构化数据（默认 text）"},
                "timeout": {"type": "integer", "minimum": 5, "maximum": 60, "description": "单请求超时秒数（默认 15）"},
            },
            [],
            PERM_READONLY,
        ),
    ]

    # ═══════════════════════════════════════════
    # Task System — 任务管理（TaskPacket + 6态状态机 + 团队 + Cron）
    # ═══════════════════════════════════════════
    for _task_tool_def in [
        ("TaskCreate",
         "创建结构化任务：传 prompt 建简单任务，或传 TaskPacket 字段（objective/scope/acceptance_criteria 等）建完整任务包。返回任务 ID。",
         {
             "prompt": {"type": "string", "description": "任务描述（简单模式），或 TaskPacket.objective"},
             "description": {"type": "string", "description": "可选任务说明"},
             "scope": {"type": "string", "enum": ["workspace", "module", "single_file", "custom"],
                       "description": "任务作用域（默认 workspace）"},
             "scope_path": {"type": "string", "description": "作用域路径（module/single_file/custom 时需要）"},
             "acceptance_criteria": {"type": "array", "items": {"type": "string"},
                                      "description": "验收标准列表"},
             "acceptance_tests": {"type": "array", "items": {"type": "string"},
                                   "description": "验收测试命令列表"},
             "verification_plan": {"type": "array", "items": {"type": "string"},
                                    "description": "验证步骤"},
             "resources": {"type": "array", "items": {"type": "object",
                           "properties": {"kind": {"type": "string"}, "value": {"type": "string"}},
                           "additionalProperties": False},
                           "description": "允许访问的资源列表"},
             "model": {"type": "string", "description": "指定模型"},
             "provider": {"type": "string", "description": "模型提供商"},
             "commit_policy": {"type": "string", "description": "提交策略"},
             "branch_policy": {"type": "string", "description": "分支策略"},
             "reporting_contract": {"type": "string", "description": "报告合同"},
             "escalation_policy": {"type": "string", "description": "升级策略"},
             "recovery_policy": {"type": "string", "description": "恢复策略"},
         },
         ["prompt"], PERM_WORKSPACE_WRITE),

        ("TaskList",
         "列出任务列表，可选按状态过滤。状态值：created, running, blocked, completed, failed, stopped。",
         {"status_filter": {"type": "string", "description": "可选状态过滤"}},
         [], PERM_WORKSPACE_WRITE),

        ("TaskGet",
         "查看单个任务的详细信息，包括消息记录和输出。",
         {"task_id": {"type": "string", "description": "任务 ID"}},
         ["task_id"], PERM_WORKSPACE_WRITE),

        ("TaskUpdate",
         "更新任务状态或追加消息。status 可选值：created, running, blocked, completed, failed, stopped。",
         {"task_id": {"type": "string", "description": "任务 ID"},
          "status": {"type": "string", "description": "新状态"},
          "message": {"type": "string", "description": "可选追加的消息内容"}},
         ["task_id"], PERM_WORKSPACE_WRITE),

        ("TaskStop",
         "终止一个任务。只能终止非终态（completed/failed/stopped）的任务。",
         {"task_id": {"type": "string", "description": "任务 ID"}},
         ["task_id"], PERM_WORKSPACE_WRITE),

        ("TaskBoard",
         "查看看板视图 — 按 active（created/running）/ blocked / finished 三栏展示所有任务及其心跳状态。",
         {},
         [], PERM_READONLY),

        ("TaskRemove",
         "从注册表中删除一个任务。不可恢复。",
         {"task_id": {"type": "string", "description": "任务 ID"}},
         ["task_id"], PERM_WORKSPACE_WRITE),

        ("TeamCreate",
         "创建一个团队，可选择关联的任务 ID 列表。",
         {"name": {"type": "string", "description": "团队名称"},
          "task_ids": {"type": "array", "items": {"type": "string"},
                        "description": "可选关联任务 ID 列表"}},
         ["name"], PERM_WORKSPACE_WRITE),

        ("TeamList",
         "列出所有团队。",
         {}, [], PERM_READONLY),

        ("TeamDelete",
         "删除一个团队（软删除）。",
         {"team_id": {"type": "string", "description": "团队 ID"}},
         ["team_id"], PERM_WORKSPACE_WRITE),

        ("CronCreate",
         "创建一个定时任务条目。schedule 为 cron 表达式，如 '0 * * * *'（每小时）。"
         "注意：定时任务到点会以 shell 形式执行 prompt，创建需要用户显式批准。",
         {"schedule": {"type": "string", "description": "cron 表达式"},
          "prompt": {"type": "string", "description": "定时执行的任务描述"},
          "description": {"type": "string", "description": "可选说明"}},
         ["schedule", "prompt"], PERM_DANGER_FULL),

        ("CronList",
         "列出所有定时任务，可选仅显示启用的。",
         {"enabled_only": {"type": "boolean", "description": "是否只显示启用的条目（默认 false）"}},
         [], PERM_READONLY),

        ("CronDisable",
         "禁用一个定时任务，停止其调度执行。",
         {"cron_id": {"type": "string", "description": "定时任务 ID"}},
         ["cron_id"], PERM_WORKSPACE_WRITE),

        ("CronDelete",
         "删除一个定时任务。",
         {"cron_id": {"type": "string", "description": "定时任务 ID"}},
         ["cron_id"], PERM_WORKSPACE_WRITE),
    ]:
        native.append(_make_tool(*_task_tool_def))

    # ═══════════════════════════════════════════
    # ═══════════════════════════════════════════
    # 代码分析工具 — 定义位于 bin/ai_lib/tools/code_analysis.py
    # ═══════════════════════════════════════════

    native.extend(code_analysis.get_native_tools(_make_tool))

    # ═══════════════════════════════════════════
    # Memory — 记忆查询工具（支持 range + context）
    # ═══════════════════════════════════════════

    native.append(_make_tool(
        "MemoryRead",
        "读取记忆文件，支持行号范围。路径示例：chat/first、library/<uuid>、onyx_ai。结果自动缓存。",
        {
            "path": {"type": "string", "description": "记忆路径（如 chat/first, library/<uuid>, onyx_ai）"},
            "range": {"type": "string", "description": "可选行号范围，如 '1-30' 或 '50'（单行）"},
        },
        ["path"], PERM_READONLY,
    ))
    native.append(_make_tool(
        "MemorySearch",
        "在记忆文件中搜关键字，默认显示匹配行上下各 3 行（含行号）；uuid 指定单个会话或 all 全范围。结果自动缓存。",
        {
            "pattern": {"type": "string", "description": "搜索关键字或正则"},
            "uuid": {"type": "string", "description": "目标记忆 UUID，或 'all' 表示全范围查找（默认 all）"},
            "context": {"type": "integer", "description": "可选上下文行数，默认 3"},
            "-i": {"type": "boolean", "description": "可选忽略大小写，默认 true"},
        },
        ["pattern"], PERM_READONLY,
    ))

    native.append(_make_tool(
        "UndoLastEdit",
        "撤销上一次文件编辑或写入操作。将文件恢复为修改前的内容。只能在有可撤销记录时使用。",
        {},
        [], PERM_WORKSPACE_WRITE,
    ))

    # ── Include non-filesystem MCP tools (puppeteer/github/postgres etc.) ──
    mcp_tools = get_mcp_tools(user_home_dir=user_home_dir)
    if mcp_tools:
        seen_names = {t["function"]["name"] for t in native if "function" in t}
        for mt in mcp_tools:
            name = mt.get("name", "")
            if not name:
                continue
            # ── MCP 工具名归一化：mcp_registry 返回 "mcp__<server>__<tool>"，
            #    而 MCP_TOOLS_CACHE 回退路径返回 "<tool>"。两条路径若产出不同
            #    名称，tools 数组会随注册表填充状态变化（59↔68 个、单/双前缀），
            #    直接打断跨会话前缀缓存。统一取最后一段，保证工具表在任何
            #    状态下字节级一致。──
            if name.startswith("mcp__"):
                name = name.rsplit("__", 1)[-1]
            if not name or name in seen_names:
                continue
            if name in ("read_file", "write_file", "edit_file",
                         "create_directory", "list_directory",
                         "directory_tree", "move_file", "copy_file",
                         "delete_file", "delete_directory",
                         "get_file_info", "search_files", "search_content",
                         "glob", "find_on_path", "get_workspace_folders"):
                continue
            mcp_prefixed = f"mcp_{name}"
            native.append({
                "type": "function",
                "function": {
                    "name": mcp_prefixed,
                    "description": mt.get("description", ""),
                    "parameters": mt.get("inputSchema", {}),
                },
                "x_permission": PERM_READONLY,  # 2026-09 用户拍板：MCP 工具一律免手动确认（ReadOnly 全模式自动放行）
            })
            seen_names.add(mcp_prefixed)

    # ── 目录浏览工具 ──
    native.append(_make_tool(
        "ListDirectory",
        "List files and directories in a path. Returns one entry per line, directories marked with /.",
        {"path": {"type": "string", "description": "Directory path to list, defaults to current directory"}},
        [],
        PERM_READONLY,
    ))
    native.append(_make_tool(
        "DirectoryTree",
        "Recursively show directory tree structure. Dirs marked with /, max depth 2 by default.",
        {
            "path": {"type": "string", "description": "Root directory, defaults to current directory"},
            "maxDepth": {"type": "integer", "description": "Max recursion depth, default 2, max 5"},
        },
        [],
        PERM_READONLY,
    ))

    # ── Git 工具 ──
    native.append(_make_tool(
        "GitStatus",
        "显示 Git 工作区状态（相当于 git status --short）。返回已修改/新增/删除的文件列表。",
        {"path": {"type": "string", "description": "Git 仓库路径，默认当前目录"}},
        [],
        PERM_READONLY,
    ))
    native.append(_make_tool(
        "GitDiff",
        "显示 Git 未暂存的变更（相当于 git diff）。返回文件级别的 diff 内容。",
        {
            "path": {"type": "string", "description": "Git 仓库路径，默认当前目录"},
            "staged": {"type": "boolean", "description": "是否显示已暂存变更（git diff --staged），默认 false"},
        },
        [],
        PERM_READONLY,
    ))
    native.append(_make_tool(
        "GitLog",
        "查看 Git 提交历史（相当于 git log --oneline）。返回最近的提交记录。",
        {
            "path": {"type": "string", "description": "Git 仓库路径，默认当前目录"},
            "count": {"type": "integer", "description": "显示条数，默认 10"},
        },
        [],
        PERM_READONLY,
    ))
    native.append(_make_tool(
        "GitBranch",
        "查看 Git 分支信息（相当于 git branch -a）。返回所有本地和远程分支。",
        {"path": {"type": "string", "description": "Git 仓库路径，默认当前目录"}},
        [],
        PERM_READONLY,
    ))

    # ── Shell 命令执行（function calling）──
    # 命令经 Onyx 安全管线执行：危险命令弹用户确认、输出捕获后以 tool 结果
    # 回传。ReadOnly 权限仅用于跳过工具门控——真正的安全确认在 handler 内部
    # （is_dangerous_command → confirm_dangerous_command）。
    native.append(_make_tool(
        "EnvProbe",
        "只读环境探测（秒回）。type 按任务类型动态调整探测范围：deploy=部署/批量、network=网络、python=Python 环境、build=编译链、database=数据库客户端、web=Web/Node、permission=权限专项，缺省 general=全量报告。which 查询指定命令的路径与版本（空格/逗号分隔多个；仅传 which 时输出轻量摘要）。仅用于相对郑重的任务（部署、批量操作、跨平台命令、权限敏感操作）或对环境不确定时——在规划获批后、执行命令前探测，可避免平台差异、权限限制、工具缺失导致的失败；简单命令无需探测。",
        {
            "type": {"type": "string", "description": "任务类型（可选，默认 general 全量），支持逗号组合多个：general=全量 / deploy=部署批量 / network=网络渗透（扫描/爆破/嗅探/无线）/ python=Python 环境 / build=编译构建 / database=数据库客户端 / web=Web 渗透（目录/漏洞/指纹）与前端 / permission=权限专项。示例：'web,network' 同时探测两者"},
            "which": {"type": "string", "description": "可选：要查询的命令名，空格或逗号分隔多个，返回路径与版本；仅传此参数时输出轻量查询结果"},
        },
        [], PERM_READONLY,
    ))
    native.append(_make_tool(
        "RunCommand",
        "Execute a shell command through Onyx's security pipeline. Output (stdout+stderr) and exit code are captured and returned; dangerous commands require user confirmation. Command construction rules: (1) NEVER assume tools exist — run EnvProbe first, respect platform differences (Android/Termux lacks ip/ss → use ifconfig/netstat; Windows lacks grep/uname); (2) probe before relying: `which X` or `X --version` when unsure; (3) keep output bounded — append `2>&1 | tail -50` for long-output commands; (4) one logical operation per call; chain freely with &&/||/;. Non-root: nmap -O/-sU quit entirely — avoid them.",
        {"command": {"type": "string", "description": "Shell command to execute (single line)"}},
        ["command"], PERM_READONLY,
    ))

    native.sort(key=lambda t: t.get("function", {}).get("name", ""))
    _mcp_debug_exit("build_native_tools", ok=len(native) > 0,
                    detail=f"{len(native)} native tools")
    return native


# ──────────────────── 工具表冻结缓存（前缀缓存稳定性）────────────────────

# native_tools 若在每个 handle_ai 调用时重建，MCP registry 的异步填充会让
# 工具数组在 REPL 跨轮之间变化（59↔68 个）；tools 位于请求最前端（model
# 之后），一变即整轮前缀分叉 → 缓存 0% 命中。
# 模块级缓存：同一 (user_home_dir, mcp_enabled) 下首次构建后冻结，
# 只有 MCP 连接成功写入 registry 或 mcp 开关切换（key 变化）时才重建。
_NATIVE_TOOLS_CACHE: Dict[str, Any] = {"key": None, "tools": None, "prompt": None}

# 工具名 → x_permission 惰性映射（从冻结工具表构建一次；随 invalidate 失效）。
# execute_mcp_tool 的权限门禁查它——避免每次工具调用都全量重建 build_native_tools()
# （重建会重新遍历全部工具描述，且可能触发 MCP 发现/连接，是慢路径）。
_TOOL_PERMISSION_LOOKUP: Dict[str, str] = {}


def _get_tool_permission(tool_name: str) -> str:
    """从缓存工具表查 x_permission，缺失时回退 ReadOnly（安全默认）。"""
    if not _TOOL_PERMISSION_LOOKUP:
        try:
            _tools, _ = get_native_tools_cached(USER_HOME_DIR, True)
            for _t in _tools:
                _n = _t.get("function", {}).get("name", "")
                if _n:
                    _TOOL_PERMISSION_LOOKUP[_n] = _t.get("x_permission", PERM_READONLY)
        except Exception:
            pass
    return _TOOL_PERMISSION_LOOKUP.get(tool_name, PERM_READONLY)


def invalidate_native_tools_cache() -> None:
    """MCP 工具表变化（新连接/Registry 更新）后调用，强制下次重建。"""
    _NATIVE_TOOLS_CACHE["key"] = None
    _NATIVE_TOOLS_CACHE["tools"] = None
    _NATIVE_TOOLS_CACHE["prompt"] = None
    _TOOL_PERMISSION_LOOKUP.clear()


def get_native_tools_cached(user_home_dir: str, mcp_enabled: bool) -> tuple:
    """返回 (tools, tools_prompt)，跨 handle_ai 冻结，保证 tools 数组字节稳定。"""
    key = (user_home_dir, bool(mcp_enabled))
    if _NATIVE_TOOLS_CACHE["key"] == key and _NATIVE_TOOLS_CACHE["tools"] is not None:
        return _NATIVE_TOOLS_CACHE["tools"], _NATIVE_TOOLS_CACHE["prompt"]
    tools = build_native_tools(user_home_dir)
    prompt = build_native_tools_prompt()
    _NATIVE_TOOLS_CACHE["key"] = key
    _NATIVE_TOOLS_CACHE["tools"] = tools
    _NATIVE_TOOLS_CACHE["prompt"] = prompt
    _mcp_debug(f"native_tools 冻结缓存: {len(tools)} 个工具 (mcp_enabled={mcp_enabled})")
    return tools, prompt


# ──────────────────── 工具结果消费后压缩（缓存效率）────────────────────

# AI 已基于工具结果产出下一条回复后，旧结果就完成了使命；若继续原样回传，
# 每轮请求都会携带 32KB 级未缓存增量（工具结果 + reasoning 回传），持续拉低
# 命中率。这里把 keep_rounds 轮之前的 tool 结果替换为确定性短摘要（不调 LLM）。
# 压缩事件会改写历史中段 → 调用方应 bump_rewrite_version 归因为 log_rewrite。
_COMPACT_TOOL_RESULTS_KEEP_ROUNDS = 2      # 保留最近 N 轮工具调用完整结果
_COMPACT_TOOL_RESULTS_MIN_SAVINGS = 4096   # 节省少于 4KB 不压缩，避免无谓断裂
_COMPACT_TOOL_RESULTS_HEAD = 300           # 每条结果保留前 N 字符


def compact_consumed_tool_results(conversation_history: List[Dict]) -> bool:
    """把已被 AI 消费的旧工具结果压缩为摘要（保留最近 keep_rounds 轮完整）。

    Returns:
        True 表示发生了压缩（调用方应 bump_rewrite_version）。
    """
    # 定位保留区起点：从后往前数到第 keep_rounds 个带 tool_calls 的 assistant
    # 消息（最近 K 轮的起点），压缩它之前的 tool 结果；该 assistant 消息本身
    # 及之后的轮次完整保留。
    keep_start = 0
    seen = 0
    for i in range(len(conversation_history) - 1, -1, -1):
        m = conversation_history[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            seen += 1
            if seen >= _COMPACT_TOOL_RESULTS_KEEP_ROUNDS:
                keep_start = i
                break
    if keep_start <= 0:
        return False

    # 统计可压缩的 tool 消息（保留区之前的，且未压缩过）
    savings = 0
    compactable = []
    for i in range(keep_start):
        m = conversation_history[i]
        if m.get("role") == "tool" and not m.get("compacted"):
            content = m.get("content", "") or ""
            if len(content) > _COMPACT_TOOL_RESULTS_HEAD:
                compactable.append(i)
                savings += len(content) - _COMPACT_TOOL_RESULTS_HEAD
    if not compactable or savings < _COMPACT_TOOL_RESULTS_MIN_SAVINGS:
        return False

    for i in compactable:
        m = conversation_history[i]
        content = m.get("content", "") or ""
        m["content"] = (
            f"工具结果已压缩（原 {len(content)} 字符，AI 已消费），摘要如下：\n"
            f"{content[:_COMPACT_TOOL_RESULTS_HEAD]}…"
        )
        m["compacted"] = True
    return True


# ──────────────────── 内置分析工具执行器 ────────────────────

def _exec_validate_edit(file_path: str, search: str, replace: str) -> str:
    """校验 SEARCH/REPLACE 编辑。"""
    try:
        from lib.edit_engine import validate_edit, dry_run_edit
        ok, msg = validate_edit(file_path, search, replace)
        if ok:
            diff = dry_run_edit(file_path, search, replace)
            return _i18n("validate_ok", "bilingual") + f"\n\n{diff[:2000]}"
        return f"❌ {msg}"
    except Exception as e:
        return _i18n("validate_failed", "bilingual", err=e)


def _exec_preview_edit(file_path: str, search: str, replace: str) -> str:
    """预览 diff。"""
    try:
        from lib.edit_engine import dry_run_edit
        diff = dry_run_edit(file_path, search, replace)
        if diff.startswith("❌"):
            return diff
        return f"```diff\n{diff}\n```"
    except Exception as e:
        return _i18n("preview_failed", "bilingual", err=e)


def _exec_get_file_info(file_path: str) -> str:
    """获取文件基本信息。"""
    try:
        import os, datetime
        if not os.path.exists(file_path):
            return _i18n("finfo_not_found", "bilingual", path=file_path)
        stat = os.stat(file_path)
        size = stat.st_size
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        # 行数
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                line_count = sum(1 for _ in f)
        except Exception:
            line_count = -1
        size_str = f"{size:,} bytes"
        if size > 1024:
            size_str += f" ({size/1024:.1f} KB)"
        if size > 1024*1024:
            size_str += f" ({size/1024/1024:.1f} MB)"
        # 文件类型
        _, ext = os.path.splitext(file_path)
        ext = ext.lower() if ext else "(no extension)"
        return (
            f"📄 {file_path}\n"
            f"  {_i18n('finfo_size', 'bilingual')}: {size_str}\n"
            f"  {_i18n('finfo_mtime', 'bilingual')}: {mtime}\n"
            f"  {_i18n('finfo_lines', 'bilingual')}: {line_count if line_count >= 0 else 'binary/unknown'}\n"
            f"  {_i18n('finfo_type', 'bilingual')}: {ext}"
        )
    except Exception as e:
        return _i18n("finfo_failed", "bilingual", err=e)


# ── read_file 大纲模式（大文件自动折叠）──
READ_OUTLINE_THRESHOLD = 64 * 1024          # 超过 64 KiB 自动切大纲模式
READ_OUTLINE_HEAD = 80                      # 大纲模式返回前 N 行（方向感）

# 通用语言顶层定义扫描（Python 走 ast，其他语言用此正则兜底）
_SYMBOL_DEF_RE = re.compile(
    r"^\s*(?:(?:export|default|public|private|protected|static|abstract|"
    r"final|async|internal|extern|pub|global)\s+)*"
    r"(?:def\s+|class\s+|func\s+|function\s+|fn\s+|interface\s+|"
    r"struct\s+|enum\s+|trait\s+|type\s+)"
    r"[A-Za-z_][A-Za-z0-9_]*"
)


def _fmt_read_size(num_bytes: int) -> str:
    """人类可读文件大小，如 311.8 KiB"""
    b = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if b < 1024 or unit == "TiB":
            return f"{b:.0f} {unit}" if unit == "B" else f"{b:.1f} {unit}"
        b /= 1024
    return f"{num_bytes} B"


def _build_symbol_outline(file_path: str, lines: List[str], total_lines: int) -> str:
    """提取顶层函数/类符号大纲（带行号，无数量上限）。Python 用 ast，其余语言正则兜底。"""
    width = len(str(total_lines))
    out: List[str] = []

    if os.path.splitext(file_path)[1].lower() == ".py":
        try:
            import ast as _ast
            tree = _ast.parse("\n".join(lines))
            for node in tree.body:
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                    if isinstance(node, _ast.ClassDef):
                        head = f"class {node.name}"
                    else:
                        args = [a.arg for a in node.args.args[:6]]
                        if node.args.vararg:
                            args.append("*" + node.args.vararg.arg)
                        if node.args.kwarg:
                            args.append("**" + node.args.kwarg.arg)
                        prefix = "async def" if isinstance(node, _ast.AsyncFunctionDef) else "def"
                        head = f"{prefix} {node.name}({', '.join(args)})"
                    out.append(f"{node.lineno:>{width}}  │ {head}")
        except Exception:
            out = []  # 语法错误等 → 回退正则

    if not out:
        for i, line in enumerate(lines, 1):
            if _SYMBOL_DEF_RE.match(line):
                out.append(f"{i:>{width}}  │ {line.strip()[:120]}")
    return "\n".join(out)


def _exec_read_file(file_path: str, range_str: str = None, head: int = None, tail: int = None) -> str:
    """
    读取文件内容，支持行号范围 range / head / tail。
    超过 64 KiB 的大文件默认返回大纲模式（文件大小 + 前 80 行 + 符号大纲 + 钻取提示），
    避免整文件灌入上下文；需要细节时用 range / head / tail / grep_search 钻取。
    
    返回带行号前缀的内容（每行格式 "LINE │ 内容"），
    AI 可以精确引用行号而无需重读文件。
    同时记录到 library 时保留完整路径+行号+内容。
    """
    try:
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return _i18n("read_not_found", "bilingual", path=abs_path)
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            # 大文件分块读取，支持 Ctrl+C 中断
            f.seek(0, 2)
            file_size = f.tell()
            f.seek(0)
            if file_size < 1024 * 1024 * 4:  # 4MB 以下直接读
                content = f.read()
            else:
                parts = []
                while True:
                    if _AI_INTERRUPTED:
                        return "⏹ 用户中断"
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    parts.append(chunk)
                content = "".join(parts)
        
        lines = content.split("\n")
        total_lines = len(lines)
        
        # ── 行号范围处理 ──
        start_line = 1
        end_line = total_lines
        view_mode = "full"
        
        if range_str:
            try:
                if "-" in range_str:
                    start, end = map(int, range_str.split("-", 1))
                    start_line = max(1, start)
                    end_line = min(total_lines, end)
                    selected = lines[start_line - 1:end_line]
                    view_mode = f"range {start_line}-{end_line}"
                else:
                    line_no = int(range_str)
                    start_line = max(1, min(line_no, total_lines))
                    end_line = start_line
                    selected = [lines[start_line - 1]]
                    view_mode = f"line {start_line}"
            except (ValueError, IndexError):
                selected = lines
        elif head:
            n = max(1, min(int(head), total_lines))
            start_line, end_line = 1, n
            selected = lines[:n]
            view_mode = f"head {n}"
        elif tail:
            n = max(1, min(int(tail), total_lines))
            start_line = max(1, total_lines - n + 1)
            end_line = total_lines
            selected = lines[start_line - 1:]
            view_mode = f"tail {n}"
        elif file_size > READ_OUTLINE_THRESHOLD:
            # ── 大纲模式：大小 + 前 N 行 + 符号大纲 + 钻取提示 ──
            from lib.native_fs.panels import number_lines as _num_lines
            preview = _num_lines("\n".join(lines[:READ_OUTLINE_HEAD]), start=1)
            symbols = _build_symbol_outline(abs_path, lines, total_lines)
            count = symbols.count("\n") + 1 if symbols else 0
            return (
                f"📖 `{abs_path}` "
                + _i18n("read_outline_header", "bilingual",
                        total=total_lines, size=_fmt_read_size(file_size))
                + "\n\n" + _i18n("read_outline_preview", "bilingual", n=READ_OUTLINE_HEAD)
                + "\n" + preview
                + "\n\n" + _i18n("read_outline_symbols", "bilingual", count=count)
                + "\n" + (symbols or _i18n("read_outline_none", "bilingual"))
                + "\n\n" + _i18n("read_outline_hint", "bilingual")
            )
        else:
            selected = lines
        
        # ── 构建带行号前缀的输出 ──
        from lib.native_fs.panels import number_lines as _num_lines
        raw_selected = "\n".join(selected)
        numbered = _num_lines(raw_selected, start=start_line)
        
        # 构建返回文本：路径 + 行范围 + 行号内容
        header = (
            f"📖 `{abs_path}` "
            + _i18n("read_header", "bilingual", mode=view_mode, total=total_lines)
        )
        
        # 限制输出大小：最多 8000 字符
        if len(numbered) > 8000:
            numbered = numbered[:8000] + f"\n... (truncated, {len(numbered)} chars total)"
        
        return f"{header}\n\n{numbered}"
    except Exception as e:
        return _i18n("read_failed", "bilingual", err=e)


def _exec_write_file(file_path: str, content: str) -> str:
    """写入文件（全量覆盖）。返回中包含 original_file 供撤销。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        old_content = ""
        is_update = False
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                old_content = f.read()
            if old_content == content:
                return _i18n("write_unchanged", "bilingual", path=file_path)
            is_update = True
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        total_lines = content.count("\n") + (1 if content else 0)
        # 保存到全局撤销记录
        global _LAST_EDIT
        if is_update:
            _LAST_EDIT = {"path": file_path, "original": old_content, "action": "write"}
        else:
            _LAST_EDIT = {"path": file_path, "original": "", "action": "write"}
        return json.dumps({
            "result": _i18n("write_ok", "bilingual", path=file_path, lines=total_lines),
            "original_file": old_content if is_update else None,
            "file_path": file_path,
        }, ensure_ascii=False)
    except Exception as e:
        return f"❌ write_file failed: {e}"


def _exec_edit_file(file_path: str, old_string: str, new_string: str) -> str:
    """SEARCH/REPLACE 精确替换。返回中包含 original_file 供撤销。"""
    try:
        from lib.edit_engine import apply_edit
        # 读旧内容做 diff 预览 + 保存原始内容
        old_content = ""
        try:
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8", errors="replace") as _f:
                    old_content = _f.read()
        except Exception:
            old_content = ""
        if old_content and old_string in old_content:
            new_content = old_content.replace(old_string, new_string, 1)
            console.print("  " + _i18n("edit_console_ok", "bilingual", path=file_path))
            try:
                _render_edit_diff(old_content, new_content)
            except Exception:
                pass
        ok, msg = apply_edit(file_path, old_string, new_string)
        if ok:
            # 保存到全局撤销记录
            global _LAST_EDIT
            _LAST_EDIT = {"path": file_path, "original": old_content, "action": "edit"}
            return json.dumps({
                "result": _i18n("edit_ok", "bilingual", path=file_path),
                "original_file": old_content,
                "file_path": file_path,
            }, ensure_ascii=False)
        _err_lower = msg.lower()
        if "not found" in _err_lower or "not unique" in _err_lower:
            return f"❌ {msg}\n" + _i18n("edit_hint", "bilingual")
        return f"❌ {msg}"
    except Exception as e:
        return _i18n("edit_failed", "bilingual", err=e)





# ──────────────────── 新增工具执行器 ────────────────────

def _exec_glob_search(pattern: str, path: str = None) -> str:
    """使用 glob 模式查找文件。"""
    try:
        import glob as _glob
        search_root = path or "."
        matches = _glob.glob(pattern, root_dir=search_root, recursive=True) if hasattr(_glob, 'root_dir') else []
        if not matches:
            try:
                import subprocess as _sp
                if "**" in pattern:
                    result = _sp.run(["find", search_root, "-type", "f", "-name", pattern.split("/")[-1]],
                                     capture_output=True, text=True, timeout=5)
                    matches = [l.strip() for l in result.stdout.split("\n") if l.strip()]
                else:
                    result = _sp.run(["ls", "-1", os.path.join(search_root, pattern)],
                                     capture_output=True, text=True, timeout=5, shell=True)
                    matches = [l.strip() for l in result.stdout.split("\n") if l.strip()]
            except Exception:
                pass
        if not matches:
            return _i18n("glob_no_match", "bilingual", pattern=pattern, root=search_root)
        # 限制返回数量
        total = len(matches)
        if total > 200:
            matches = matches[:200]
            return "\n".join(matches) + _i18n("glob_more", "bilingual", extra=total - 200, total=total)
        return "\n".join(matches)
    except Exception as e:
        return _i18n("glob_failed", "bilingual", err=e)



def _exec_grep_search(pattern: str, path: str = None, glob: str = None,
                      context: int = 0, i: bool = False, head_limit: int = None) -> str:
    """使用正则表达式搜索文件内容。支持上下文行、大小写控制。"""
    try:
        search_root = path or "."
        output = _run_grep_lines(pattern, search_root, context=context,
                                 case_insensitive=i, glob=glob, timeout=15)
        if output is None:
            return _i18n("grep_timeout", "bilingual")
        if not output.strip():
            return _i18n("grep_no_match", "bilingual", pattern=pattern, root=search_root)
        lines = output.split("\n")
        if head_limit and len(lines) > head_limit:
            output = "\n".join(lines[:head_limit]) + _i18n("grep_head_limited", "bilingual", total=len(lines), shown=head_limit)
        if len(output) > 10000:
            output = output[:5000] + _i18n("grep_out_truncated", "bilingual", total=len(output))
        return output
    except Exception as e:
        return _i18n("grep_failed", "bilingual", err=e)


def _exec_search_file(pattern: str, path: str = None) -> str:
    """按文件名关键字递归查找文件（自动跳过依赖/构建目录），返回完整路径列表。"""
    try:
        import fnmatch as _fnmatch
        root = os.path.abspath(path) if path else os.getcwd()
        if not os.path.isdir(root):
            return _i18n("sf_root_missing", "bilingual", path=root)
        skip_dirs = {".git", "node_modules", "__pycache__", "dist", "build",
                     ".venv", "venv", "target", "out", ".cache", ".next", ".nuxt"}
        matches: list = []
        is_glob = any(ch in pattern for ch in "*?[")
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                if is_glob:
                    ok = _fnmatch.fnmatch(fname.lower(), pattern.lower())
                else:
                    ok = pattern.lower() in fname.lower()
                if ok:
                    matches.append(os.path.join(dirpath, fname))
                    if len(matches) >= 1000:
                        break
            if len(matches) >= 1000:
                break
        if not matches:
            return _i18n("sf_no_match", "bilingual", pattern=pattern, root=root)
        matches.sort()
        total = len(matches)
        if total > 200:
            shown = matches[:200]
            return "\n".join(shown) + _i18n("sf_more", "bilingual", extra=total - 200, total=total)
        return "\n".join(matches)
    except Exception as e:
        return _i18n("sf_failed", "bilingual", err=e)


def _exec_tool_search(query: str) -> str:
    """搜索可用工具。"""
    try:
        # 获取当前注册的工具列表
        from bin.ai_cmd import build_native_tools
        import inspect
        tools = build_native_tools()
        query_lower = query.lower()
        matches = []
        for t in tools:
            func = t.get("function", {})
            name = func.get("name", "")
            desc = func.get("description", "")
            if query_lower in name.lower() or query_lower in desc.lower():
                perm = t.get("x_permission", "Unknown")
                matches.append(f"- `{name}` [{perm}]\n  {desc[:120]}")
        if not matches:
            # 返回所有工具列表供参考
            all_tools = []
            for t in tools:
                func = t.get("function", {})
                name = func.get("name", "")
                perm = t.get("x_permission", "?")
                all_tools.append(f"- `{name}` [{perm}]")
            return f"未找到与 '{query}' 相关的工具。可用工具列表:\n" + "\n".join(all_tools)
        return f"找到 {len(matches)} 个相关工具:\n\n" + "\n\n".join(matches)
    except Exception as e:
        return f"❌ ToolSearch failed: {e}"


def _find_skill_file(skill_name: str) -> Tuple[Optional[str], str]:
    """在所有标准位置查找 SKILL.md 文件。
    
    查找路径（按优先级）:
      1. .onyx/skills/<name>/SKILL.md        ← Onyx 原生
      2. .onyx/commands/<name>.md
      3. .claude/skills/<name>/SKILL.md
      4. .claude/commands/<name>.md
      5. ~/.onyx/skills/<name>/SKILL.md
      6. ~/.claude/skills/<name>/SKILL.md
      7. .reasonix/skills/<name>/SKILL.md
      8. <name>.md (当前目录)
      9. skills/<name>.md (当前目录)
    """
    import glob as _glob
    _cwd = os.getcwd()
    _home = os.path.expanduser("~")

    _search_roots = [
        # ═══ Onyx 原生（最高优先级）═══
        os.path.join(_cwd, ".onyx", "skills"),
        os.path.join(_cwd, ".onyx", "commands"),
        # ═══ 第三方技能目录兼容（.claude 等）═══
        os.path.join(_cwd, ".claude", "skills"),
        os.path.join(_cwd, ".claude", "commands"),
        # ═══ 其他 ═══
        os.path.join(_cwd, ".reasonix", "skills"),
        os.path.join(_cwd, "skills"),
        # ═══ 用户 Home ═══
        os.path.join(_home, ".onyx", "skills"),
        os.path.join(_home, ".onyx", "commands"),

        os.path.join(_home, ".claude", "skills"),
        os.path.join(_home, ".claude", "commands"),
        os.path.join(_home, ".reasonix", "skills"),
        os.path.join(_home, ".ai_s", "skills"),
    ]

    _found = []

    for root in _search_roots:
        if not os.path.isdir(root):
            continue
        # 精确匹配: <root>/<name>/SKILL.md
        exact = os.path.join(root, skill_name, "SKILL.md")
        if os.path.isfile(exact):
            _found.append((exact, os.path.getmtime(exact)))

        # 精确匹配: <root>/<name>/<name>.md
        exact2 = os.path.join(root, skill_name, f"{skill_name}.md")
        if os.path.isfile(exact2):
            _found.append((exact2, os.path.getmtime(exact2)))

        # 精确匹配: <root>/<name>.md（commands 风格）
        exact3 = os.path.join(root, f"{skill_name}.md")
        if os.path.isfile(exact3):
            _found.append((exact3, os.path.getmtime(exact3)))

        # 精确匹配: <root>/SKILL.md（直接将 root 当作技能目录）
        direct = os.path.join(root, "SKILL.md")
        if os.path.isfile(direct) and os.path.basename(os.path.dirname(direct)).lower() == skill_name.lower():
            _found.append((direct, os.path.getmtime(direct)))

    # 按修改时间排序（最新的优先）
    _found.sort(key=lambda x: x[1], reverse=True)

    if _found:
        return _found[0][0], ""

    # 尝试在当前目录直接查找 <name>.md
    for ext in [".md", ".txt"]:
        _local = os.path.join(os.getcwd(), f"{skill_name}{ext}")
        if os.path.isfile(_local):
            return _local, ""

    # 如果找不到精确匹配，扫描所有 skill 目录做大小写不敏感匹配
    for root in _search_roots:
        if not os.path.isdir(root):
            continue
        try:
            for entry in os.listdir(root):
                entry_path = os.path.join(root, entry)
                if os.path.isdir(entry_path):
                    # 大小写不敏感比较目录名
                    if entry.lower() == skill_name.lower():
                        for _sf in ["SKILL.md", f"{entry}.md"]:
                            _skill_file = os.path.join(entry_path, _sf)
                            if os.path.isfile(_skill_file):
                                return _skill_file, ""
                    # 检查子目录中的 SKILL.md 的 frontmatter name
                    _sf = os.path.join(entry_path, "SKILL.md")
                    if os.path.isfile(_sf):
                        _fm_name = _parse_skill_name_from_file(_sf)
                        if _fm_name and _fm_name.lower() == skill_name.lower():
                            return _sf, ""
                # 也检查根目录下的 .md 文件
                elif entry.lower() == f"{skill_name.lower()}.md":
                    _full = os.path.join(root, entry)
                    if os.path.isfile(_full):
                        return _full, ""
        except PermissionError:
            continue

    return None, f"未找到技能 '{skill_name}'"


def _parse_skill_name_from_file(filepath: str) -> Optional[str]:
    """从 SKILL.md 的 YAML frontmatter 中提取 name 字段。"""
    try:
        with open(filepath, "r", encoding="utf-8") as _f:
            _content = _f.read()
        if _content.startswith("---"):
            _end = _content.find("---", 3)
            if _end > 0:
                _fm = _content[3:_end].strip()
                for _line in _fm.split("\n"):
                    if _line.strip().startswith("name:"):
                        _val = _line.split(":", 1)[1].strip().strip('"').strip("'")
                        if _val:
                            return _val
    except Exception:
        pass
    return None


def _exec_skill(skill: str, args: str = "") -> str:
    """加载并执行技能（Onyx Skill.md 发现系统）。"""
    try:
        skill_path, error = _find_skill_file(skill)
        if not skill_path:
            return f"⚠️ {error}\n\n支持的位置: .onyx/skills/<name>/SKILL.md, .claude/skills/<name>/SKILL.md, ~/.onyx/skills/<name>/SKILL.md"

        with open(skill_path, "r", encoding="utf-8") as _f:
            content = _f.read()

        # 解析 frontmatter
        description = ""
        skill_name = skill
        if content.startswith("---"):
            _end = content.find("---", 3)
            if _end > 0:
                _fm = content[3:_end].strip()
                for _line in _fm.split("\n"):
                    _line = _line.strip()
                    if _line.startswith("name:"):
                        skill_name = _line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif _line.startswith("description:"):
                        description = _line.split(":", 1)[1].strip().strip('"').strip("'")
                # 去掉 frontmatter 后的正文
                body = content[_end + 3:].strip()
        else:
            body = content.strip()
            # 尝试从首行提取 description
            _first_line = body.split("\n")[0] if body else ""
            if _first_line.startswith("description:"):
                description = _first_line.split(":", 1)[1].strip()
                body = "\n".join(body.split("\n")[1:]).strip()

        # 如果传了 args，追加到 body
        if args:
            body += f"\n\n## Arguments\n{args}"

        result_parts = [f"✅ 已加载技能: **{skill_name}**"]
        if description:
            result_parts.append(f"📝 {description}")
        result_parts.append(f"📂 {skill_path}")
        result_parts.append("")
        result_parts.append(body)

        return "\n".join(result_parts)

    except Exception as e:
        return f"❌ Skill '{skill}' 加载失败: {e}"


def _exec_sleep(seconds: int) -> str:
    """等待指定秒数（可被 Ctrl+C 中断）。"""
    try:
        import threading as _threading
        seconds = max(1, min(seconds, 300))  # 限制 1-300 秒
        _threading.Event().wait(seconds)  # 事件驱动可中断等待，替代 time.sleep
        return f"✅ 等待 {seconds} 秒完成"
    except Exception as e:
        return f"❌ Sleep failed: {e}"


def _exec_structured_output(format: str, data: str) -> str:
    """返回结构化数据。"""
    try:
        if format == "json":
            import json as _json
            # 尝试解析 data 是否为合法 JSON
            try:
                parsed = _json.loads(data)
                return _json.dumps(parsed, ensure_ascii=False, indent=2)
            except (_json.JSONDecodeError, ValueError):
                # data 不是 JSON，包装成 JSON
                return _json.dumps({"data": data}, ensure_ascii=False, indent=2)
        return data
    except Exception as e:
        return f"❌ StructuredOutput failed: {e}"


def _exec_todo_write(todos: list) -> str:
    """更新任务列表。"""
    try:
        if not todos:
            return "✅ 任务列表已清空"
        lines = []
        pending = sum(1 for t in todos if t.get("status") == "pending")
        in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
        completed = sum(1 for t in todos if t.get("status") == "completed")
        lines.append(f"📋 任务列表（共 {len(todos)} 项：⏳ {pending} 待办 · 🔄 {in_progress} 进行中 · ✅ {completed} 完成）")
        for t in todos:
            status = t.get("status", "pending")
            content = t.get("content", "")
            active = t.get("activeForm", "")
            icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}.get(status, "⏳")
            status_text = {"pending": "待办", "in_progress": active or "进行中", "completed": "完成"}.get(status, "")
            lines.append(f"{icon} {content} _{status_text}_")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ TodoWrite failed: {e}"


# ═══════════════════════════════════════════════════════════
# Task System — 任务管理器执行器
# ═══════════════════════════════════════════════════════════

def _exec_task_create(prompt: str, description: str = None,
                      scope: str = None, scope_path: str = None,
                      acceptance_criteria: list = None,
                      acceptance_tests: list = None,
                      verification_plan: list = None,
                      resources: list = None,
                      model: str = None, provider: str = None,
                      commit_policy: str = None, branch_policy: str = None,
                      reporting_contract: str = None,
                      escalation_policy: str = None,
                      recovery_policy: str = None) -> str:
    """创建任务。简单模式只传 prompt；高级模式传 TaskPacket 字段。"""
    try:
        # 判断是否为高级模式（有 TaskPacket 专属字段）
        if any([scope, acceptance_criteria, acceptance_tests,
                verification_plan, branch_policy, commit_policy,
                reporting_contract, escalation_policy]):
            packet = TaskPacket(
                objective=prompt,
                scope=TaskScope(scope) if scope else TaskScope.WORKSPACE,
                scope_path=scope_path,
                acceptance_criteria=acceptance_criteria or [],
                acceptance_tests=acceptance_tests or [],
                verification_plan=verification_plan or [],
                resources=[TaskResource(**r) if isinstance(r, dict) else r
                           for r in (resources or [])],
                model=model,
                provider=provider,
                commit_policy=commit_policy or "",
                branch_policy=branch_policy or "",
                reporting_contract=reporting_contract or "",
                escalation_policy=escalation_policy or "",
                recovery_policy=recovery_policy,
            )
            task = _TASK_REGISTRY.create_from_packet(packet)
            return (
                f"✅ 任务包已创建: `{task.task_id}`\n"
                f"   目标: {task.prompt}\n"
                f"   范围: {task.description or 'workspace'}\n"
                f"   状态: {task.status.value}"
            )
        else:
            task = _TASK_REGISTRY.create(prompt, description)
            return f"✅ 任务已创建: `{task.task_id}`\n   描述: {task.prompt}"
    except Exception as e:
        return f"❌ TaskCreate 失败: {e}"


def _exec_task_list(status_filter: str = None) -> str:
    """列任务。"""
    try:
        tasks = _TASK_REGISTRY.list(status_filter)
        if not tasks:
            return "📭 暂无任务"
        summary = _TASK_REGISTRY.summary()
        lines = [f"📋 任务列表（共 {summary['total']} 项：" +
                 f"🆕 {summary['created']} · 🔄 {summary['running']} · "
                 f"⛔ {summary['blocked']} · ✅ {summary['completed']} · "
                 f"❌ {summary['failed']} · ⏹ {summary['stopped']}）"]
        status_icons = {
            "created": "🆕", "running": "🔄", "blocked": "⛔",
            "completed": "✅", "failed": "❌", "stopped": "⏹",
        }
        for t in tasks:
            icon = status_icons.get(t.status.value, "📌")
            desc = t.description or ""
            lines.append(f"{icon} `{t.task_id}` {t.prompt} _{desc}_")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ TaskList 失败: {e}"


def _exec_task_get(task_id: str) -> str:
    """任务详情。"""
    try:
        task = _TASK_REGISTRY.get(task_id)
        if not task:
            return f"❌ 任务未找到: {task_id}"
        lines = [
            f"📌 任务详情: `{task.task_id}`",
            f"   描述: {task.prompt}",
            f"   状态: {task.status.value}",
            f"   说明: {task.description or '-'}",
            f"   创建于: {task.created_at:.1f}",
            f"   更新于: {task.updated_at:.1f}",
        ]
        if task.task_packet:
            p = task.task_packet
            lines.append(f"   范围: {p.scope.value} ({p.scope_path or '-'})")
            lines.append(f"   验收标准: {'; '.join(p.acceptance_criteria) if p.acceptance_criteria else '-'}")
            lines.append(f"   验证计划: {'; '.join(p.verification_plan) if p.verification_plan else '-'}")
        if task.team_id:
            lines.append(f"   团队: {task.team_id}")
        if task.messages:
            lines.append(f"   消息 ({len(task.messages)} 条):")
            for m in task.messages[-5:]:  # 最近 5 条
                lines.append(f"     [{m.role}] {m.content[:80]}")
        if task.output:
            lines.append(f"   输出 ({len(task.output)} 字符)")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ TaskGet 失败: {e}"


def _exec_task_update(task_id: str, status: str = None,
                      message: str = None) -> str:
    """更新任务。"""
    try:
        parts = []
        if status:
            _TASK_REGISTRY.set_status(task_id, status)
            parts.append(f"状态 → {status}")
        if message:
            _TASK_REGISTRY.update(task_id, message)
            parts.append("已追加消息")
        if not parts:
            return "⚠️ 未指定更新内容"
        return f"✅ 任务 `{task_id}` 已更新（{'，'.join(parts)}）"
    except KeyError as e:
        return f"❌ {e}"
    except ValueError as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ TaskUpdate 失败: {e}"


def _exec_task_stop(task_id: str) -> str:
    """终止任务。"""
    try:
        task = _TASK_REGISTRY.stop(task_id)
        return f"⏹ 任务 `{task_id}` 已终止（状态: {task.status.value}）"
    except KeyError as e:
        return f"❌ {e}"
    except ValueError as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ TaskStop 失败: {e}"


def _exec_task_board() -> str:
    """看板视图。"""
    try:
        board = _TASK_REGISTRY.lane_board()
        lines = [f"📊 任务看板（生成于 {board.generated_at:.1f}）"]
        status_icons = {
            "created": "🆕", "running": "🔄", "blocked": "⛔",
            "completed": "✅", "failed": "❌", "stopped": "⏹",
        }

        lines.append(f"\n── 🔄 Active（{len(board.active)}）──")
        for e in board.active:
            icon = status_icons.get(e.status.value, "📌")
            freshness = f" [{e.freshness.value}]" if e.freshness != "unknown" else ""
            lines.append(f"  {icon} `{e.task_id}` {e.prompt}{freshness}")

        lines.append(f"\n── ⛔ Blocked（{len(board.blocked)}）──")
        for e in board.blocked:
            lines.append(f"  ⛔ `{e.task_id}` {e.prompt}")

        lines.append(f"\n── ✅ Finished（{len(board.finished)}）──")
        for e in board.finished:
            icon = status_icons.get(e.status.value, "📌")
            lines.append(f"  {icon} `{e.task_id}` {e.prompt}")

        if not any([board.active, board.blocked, board.finished]):
            lines.append("\n📭 暂无任务")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ TaskBoard 失败: {e}"


def _exec_task_remove(task_id: str) -> str:
    """删除任务。"""
    try:
        task = _TASK_REGISTRY.remove(task_id)
        if task:
            return f"🗑 任务 `{task_id}`（{task.prompt}）已删除"
        return f"❌ 任务未找到: {task_id}"
    except Exception as e:
        return f"❌ TaskRemove 失败: {e}"


# ── 团队管理 ──

def _exec_team_create(name: str, task_ids: list = None) -> str:
    try:
        team = _TEAM_REGISTRY.create(name, task_ids or [])
        return f"✅ 团队已创建: `{team.team_id}`（{team.name}，{len(team.task_ids)} 个任务）"
    except Exception as e:
        return f"❌ TeamCreate 失败: {e}"


def _exec_team_list() -> str:
    try:
        teams = _TEAM_REGISTRY.list()
        if not teams:
            return "📭 暂无团队"
        lines = [f"📋 团队列表（共 {len(teams)} 个）"]
        for t in teams:
            status_icon = {"created": "🆕", "running": "🔄",
                           "completed": "✅", "deleted": "🗑"}.get(t.status.value, "📌")
            lines.append(f"  {status_icon} `{t.team_id}` {t.name}（{len(t.task_ids)} 个任务）")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ TeamList 失败: {e}"


def _exec_team_delete(team_id: str) -> str:
    try:
        team = _TEAM_REGISTRY.delete(team_id)
        return f"🗑 团队 `{team_id}`（{team.name}）已删除"
    except Exception as e:
        return f"❌ TeamDelete 失败: {e}"


# ── 定时任务 ──

def _exec_cron_create(schedule: str, prompt: str, description: str = None) -> str:
    try:
        cron = _CRON_REGISTRY.create(schedule, prompt, description)
        return f"✅ 定时任务已创建: `{cron.cron_id}`（{cron.schedule}）"
    except Exception as e:
        return f"❌ CronCreate 失败: {e}"


def _exec_cron_list(enabled_only: bool = False) -> str:
    try:
        entries = _CRON_REGISTRY.list(enabled_only)
        if not entries:
            return "📭 暂无定时任务"
        lines = [f"📋 定时任务（共 {len(entries)} 项）"]
        for e in entries:
            status = "✅" if e.enabled else "⏸"
            runs = f"（已执行 {e.run_count} 次）" if e.run_count else ""
            lines.append(f"  {status} `{e.cron_id}` {e.schedule} → {e.prompt} {runs}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ CronList 失败: {e}"


def _exec_cron_disable(cron_id: str) -> str:
    try:
        _CRON_REGISTRY.disable(cron_id)
        return f"⏸ 定时任务 `{cron_id}` 已禁用"
    except Exception as e:
        return f"❌ CronDisable 失败: {e}"


def _exec_cron_delete(cron_id: str) -> str:
    try:
        entry = _CRON_REGISTRY.delete(cron_id)
        return f"🗑 定时任务 `{cron_id}`（{entry.prompt}）已删除"
    except Exception as e:
        return f"❌ CronDelete 失败: {e}"


# ═══════════════════════════════════════════════════════════
# LSP — 语言服务器协议执行器
# ═══════════════════════════════════════════════════════════


def _exec_undo_last_edit() -> str:
    """撤销上一次文件编辑或写入操作。"""
    try:
        global _LAST_EDIT
        if not _LAST_EDIT or not _LAST_EDIT.get("path"):
            return "❌ 没有可撤销的编辑记录"
        path = _LAST_EDIT["path"]
        original = _LAST_EDIT["original"]
        action = _LAST_EDIT.get("action", "edit")
        if not original:
            # 新建文件，删除它
            if os.path.exists(path):
                os.remove(path)
                _LAST_EDIT = {}
                return f"🗑 已撤销: 删除新建文件 {path}（原文件不存在）"
            else:
                return f"ℹ️ 文件 {path} 已不存在，无需撤销"
        # 写回原始内容
        with open(path, "w", encoding="utf-8") as f:
            f.write(original)
        _LAST_EDIT = {}
        return f"↩️ 已撤销: {path} 已恢复为修改前的内容"
    except Exception as e:
        return f"❌ UndoLastEdit 失败: {e}"


# ──────────────────── 目录浏览工具执行器 ────────────────────

def _exec_list_directory(path: str = "") -> str:
    """列出目录内容。"""
    try:
        import os
        cwd = path or os.getcwd()
        if not os.path.isdir(cwd):
            return f"❌ 路径不存在或不是目录: {cwd}"
        entries = os.listdir(cwd)
        if not entries:
            return "(空目录)"
        lines = []
        for e in sorted(entries):
            full = os.path.join(cwd, e)
            if os.path.isdir(full):
                lines.append(f"{e}/")
            else:
                lines.append(e)
        return "```\n" + "\n".join(lines) + "\n```"
    except PermissionError:
        return f"❌ 无权限读取目录"
    except Exception as e:
        return f"❌ ListDirectory 错误: {e}"


def _exec_directory_tree(path: str = "", max_depth: int = 2) -> str:
    """递归显示目录树。"""
    try:
        import os
        cwd = path or os.getcwd()
        if not os.path.isdir(cwd):
            return f"❌ 路径不存在或不是目录: {cwd}"
        max_depth = max(1, min(max_depth, 5))
        lines = []
        root_name = os.path.basename(cwd) or cwd
        lines.append(root_name + "/")
        def _walk(dir_path, prefix, depth):
            if depth > max_depth:
                return
            try:
                entries = sorted(os.listdir(dir_path))
            except PermissionError:
                lines.append(prefix + "  [权限不足]")
                return
            for i, e in enumerate(entries):
                is_last = (i == len(entries) - 1)
                connector = "└── " if is_last else "├── "
                full = os.path.join(dir_path, e)
                if os.path.isdir(full):
                    lines.append(prefix + connector + e + "/")
                    sub_prefix = prefix + ("    " if is_last else "│   ")
                    _walk(full, sub_prefix, depth + 1)
                else:
                    lines.append(prefix + connector + e)
        _walk(cwd, "", 1)
        return "```\n" + "\n".join(lines) + "\n```"
    except PermissionError:
        return f"❌ 无权限读取目录"
    except Exception as e:
        return f"❌ DirectoryTree 错误: {e}"


# ──────────────────── Git 工具执行器 ────────────────────

def _exec_git_status(path: str = "") -> str:
    """执行 git status --short。"""
    try:
        import subprocess
        cwd = path or os.getcwd()
        result = subprocess.run(["git", "status", "--short"],
                                capture_output=True, text=True, timeout=10, cwd=cwd)
        if result.returncode != 0:
            return f"❌ git status 失败（可能不是 Git 仓库）:\n{result.stderr.strip()}"
        if not result.stdout.strip():
            return "✅ 工作区干净，无改动"
        files = result.stdout.strip().split("\n")
        summary = f"📊 {len(files)} 个文件已修改\n"
        return summary + "```\n" + result.stdout.strip() + "\n```"
    except FileNotFoundError:
        return "❌ git 未安装"
    except subprocess.TimeoutExpired:
        return "❌ git status 超时"
    except Exception as e:
        return f"❌ git status 错误: {e}"


def _exec_git_diff(path: str = "", staged: bool = False) -> str:
    """执行 git diff。"""
    try:
        import subprocess
        cwd = path or os.getcwd()
        cmd = ["git", "diff", "--no-color"]
        if staged:
            cmd.append("--staged")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=cwd)
        if result.returncode != 0:
            return f"❌ git diff 失败:\n{result.stderr.strip()}"
        if not result.stdout.strip():
            return "✅ 无未暂存的变更" if not staged else "✅ 无已暂存的变更"
        output = result.stdout.strip()
        # 截断超大 diff
        if len(output) > 10000:
            output = output[:5000] + f"\n\n…[diff 过长，截断至 5000 字符，共 {len(output)} 字符]…\n\n" + output[-5000:]
        return "```diff\n" + output + "\n```"
    except FileNotFoundError:
        return "❌ git 未安装"
    except subprocess.TimeoutExpired:
        return "❌ git diff 超时"
    except Exception as e:
        return f"❌ git diff 错误: {e}"


def _exec_git_log(path: str = "", count: int = 10) -> str:
    """执行 git log --oneline。"""
    try:
        import subprocess
        cwd = path or os.getcwd()
        count = max(1, min(count, 50))
        result = subprocess.run(
            ["git", "log", f"--max-count={count}", "--oneline", "--decorate"],
            capture_output=True, text=True, timeout=10, cwd=cwd)
        if result.returncode != 0:
            return f"❌ git log 失败（可能不是 Git 仓库）:\n{result.stderr.strip()}"
        if not result.stdout.strip():
            return "ℹ️ 无提交记录"
        return "```\n" + result.stdout.strip() + "\n```"
    except FileNotFoundError:
        return "❌ git 未安装"
    except subprocess.TimeoutExpired:
        return "❌ git log 超时"
    except Exception as e:
        return f"❌ git log 错误: {e}"


def _exec_git_branch(path: str = "") -> str:
    """执行 git branch -a。"""
    try:
        import subprocess
        cwd = path or os.getcwd()
        result = subprocess.run(["git", "branch", "-a"],
                                capture_output=True, text=True, timeout=10, cwd=cwd)
        if result.returncode != 0:
            return f"❌ git branch 失败（可能不是 Git 仓库）:\n{result.stderr.strip()}"
        if not result.stdout.strip():
            return "ℹ️ 无分支信息"
        return "```\n" + result.stdout.strip() + "\n```"
    except FileNotFoundError:
        return "❌ git 未安装"
    except subprocess.TimeoutExpired:
        return "❌ git branch 超时"
    except Exception as e:
        return f"❌ git branch 错误: {e}"


# ──────────────────── 环境探测工具执行器 ────────────────────

# EnvProbe 探测的常用工具清单（shutil.which 逐个确认，秒回）
_ENV_PROBE_TOOLS = [
    "python3", "python", "pip", "pip3", "git", "curl", "wget",
    "nmap", "netstat", "ss", "ping", "ifconfig", "ip", "arp", "lsof", "fuser",
    "tar", "unzip", "gzip", "gcc", "make", "node", "npm", "npx", "java", "go",
    "docker", "kubectl", "sqlite3", "redis-cli", "mysql", "psql", "dig",
    "nslookup", "host", "openssl", "base64", "xxd", "od", "hexdump", "jq", "nc",
    "socat", "tshark", "tcpdump", "msfconsole", "hydra", "sqlmap", "nikto",
    "gobuster", "ffuf", "john", "hashcat", "busybox", "toybox", "termux-info",
    "bash", "zsh", "fish", "sh",
]

# ── EnvProbe 任务类型：type 参数决定探测范围（sections）+ 工具子集 + 专属探测 ──
# sections 可选块：system / user / network / disk / tools；tools=None 表示全量清单；
# extra 为 (标签, 命令) 列表，命令失败静默跳过。
_ENV_PROBE_TYPES = {
    "general": {
        "sections": ["system", "user", "network", "disk", "tools"],
        "tools": None,
        "extra": [],
    },
    "deploy": {
        "sections": ["system", "user", "network", "disk", "tools"],
        "tools": ["python3", "pip", "git", "curl", "wget", "tar", "unzip", "gzip",
                  "docker", "kubectl", "sqlite3", "openssl", "bash", "node", "npm",
                  "go", "gcc", "make", "systemctl"],
        "extra": [("内存", "free -h 2>/dev/null | head -3"),
                  ("CPU 核数", "nproc 2>/dev/null")],
    },
    "network": {
        "sections": ["system", "user", "network", "tools"],
        "tools": ["curl", "wget", "nmap", "zenmap", "masscan", "netstat", "ss",
                  "ping", "ifconfig", "ip", "arp", "arp-scan", "netdiscover",
                  "lsof", "fuser", "ncat", "nc", "socat", "dig", "nslookup", "host",
                  "dnsenum", "dnsrecon", "fierce", "dnsmap", "theHarvester",
                  "subfinder", "amass", "nuclei", "tshark", "tcpdump", "wireshark",
                  "ettercap", "bettercap", "responder", "hydra", "medusa", "ncrack",
                  "patator", "snmpwalk", "onesixtyone", "nbtscan", "enum4linux",
                  "smbmap", "smbclient", "aircrack-ng", "airodump-ng", "aireplay-ng",
                  "reaver", "crunch", "wifite", "macchanger", "proxychains", "msfconsole"],
        "extra": [("监听端口", "ss -tln 2>/dev/null | head -10 || netstat -tln 2>/dev/null | head -10"),
                  ("无线接口", "iwconfig 2>/dev/null | head -6")],
    },
    "python": {
        "sections": ["system", "user", "tools"],
        "tools": ["python3", "python", "pip", "pip3", "uv", "poetry", "conda",
                  "pytest", "flake8", "mypy", "ruff"],
        "extra": [("pip", "python3 -m pip --version 2>/dev/null | head -1"),
                  ("关键包", "python3 -c \"import importlib.util as _i; print([m for m in ('flask','django','requests','rich','bs4','lxml','numpy','pandas') if _i.find_spec(m)] or '无')\" 2>/dev/null")],
    },
    "build": {
        "sections": ["system", "user", "disk", "tools"],
        "tools": ["gcc", "g++", "clang", "make", "cmake", "ninja", "go", "rustc",
                  "cargo", "node", "npm", "npx", "java", "ld", "meson", "pkg-config"],
        "extra": [("gcc", "gcc --version 2>/dev/null | head -1"),
                  ("go", "go version 2>/dev/null"),
                  ("node", "node --version 2>/dev/null"),
                  ("rustc", "rustc --version 2>/dev/null")],
    },
    "database": {
        "sections": ["system", "tools"],
        "tools": ["sqlite3", "mysql", "mysqld", "psql", "redis-cli", "mongod",
                  "mongo", "mongosh", "clickhouse-client", "duckdb"],
        "extra": [("sqlite3", "sqlite3 --version 2>/dev/null | head -1"),
                  ("mysql", "mysql --version 2>/dev/null"),
                  ("psql", "psql --version 2>/dev/null"),
                  ("redis", "redis-cli --version 2>/dev/null")],
    },
    "web": {
        "sections": ["system", "network", "tools"],
        "tools": ["node", "npm", "npx", "pnpm", "yarn", "bun", "curl", "wget",
                  "nginx", "apache2", "httpd", "php", "openssl", "sqlmap", "nikto",
                  "gobuster", "ffuf", "dirb", "dirsearch", "feroxbuster", "wpscan",
                  "whatweb", "wafw00f", "xsstrike", "commix", "dalfox", "arjun",
                  "paramspider", "jwt_tool", "nuclei", "httpx", "subfinder", "amass",
                  "katana", "gau", "burpsuite", "zaproxy", "beef-xss", "msfvenom",
                  "searchsploit", "msfconsole"],
        "extra": [("node", "node --version 2>/dev/null"),
                  ("npm", "npm --version 2>/dev/null"),
                  ("nginx", "nginx -v 2>&1 | head -1"),
                  ("php", "php --version 2>/dev/null | head -1"),
                  ("本地 Web 端口", "ss -tln 2>/dev/null | grep -E ':(80|443|8000|8080|3000|5000|8888|9000) ' | head -8 || netstat -tln 2>/dev/null | grep -E ':(80|443|8000|8080|3000|5000|8888|9000) ' | head -8")],
    },
    "permission": {
        "sections": ["system", "user", "tools"],
        "tools": ["sudo", "su", "doas", "chmod", "chown", "setfacl", "getfacl",
                  "openssl", "ssh", "gpg"],
        "extra": [("完整身份", "id 2>/dev/null"),
                  ("SELinux", "getenforce 2>/dev/null")],
    },
}


def _env_probe_run(cmd: str, timeout: int = 3) -> str:
    """EnvProbe 内部探测：subprocess 快速执行，失败静默。"""
    import subprocess as _sp
    try:
        _r = _sp.run(cmd, shell=True, capture_output=True, text=True,
                     errors="replace", timeout=timeout)
        return ((_r.stdout or "").strip() + "\n" + (_r.stderr or "").strip()).strip()
    except Exception:
        return ""


def _env_section_system() -> List[str]:
    import platform as _pf
    lines = ["### 系统", f"- OS: {_pf.system()} {_pf.release()}"]
    _ver = _pf.version() or ""
    if _ver:
        lines.append(f"- 版本: {_ver[:80]}")
    lines.append(f"- 架构: {_pf.machine()}")
    lines.append(f"- Python: {_pf.python_version()}")
    lines.append(f"- 解释器: {sys.executable}")
    _uname = _env_probe_run("uname -a")
    if _uname:
        lines.append(f"- uname: {_uname[:140]}")
    return lines


def _env_section_user() -> List[str]:
    import getpass as _gp
    lines = ["### 用户与权限"]
    try:
        lines.append(f"- 用户: {_gp.getuser()}")
    except Exception:
        pass
    _is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    lines.append(f"- 权限: {'✅ root（可执行 -O/-sU 等特权扫描）' if _is_root else '⚠️ 普通用户（非 root）：nmap -O/-sU 会直接退出、/proc/net/* 只读受限'}")
    lines.append(f"- 工作目录: {os.getcwd()}")
    lines.append(f"- 用户目录: {os.path.expanduser('~')}")
    lines.append(f"- Shell: {os.environ.get('SHELL') or os.environ.get('ComSpec') or '?'}")
    _lang = os.environ.get("LANG") or os.environ.get("LC_ALL") or ""
    lines.append(f"- locale: {_lang or '(未设置)'}")
    return lines


def _env_section_network() -> List[str]:
    lines = ["### 网络"]
    _iface = _env_probe_run("ip -o addr 2>/dev/null | grep -v ' lo ' | head -5") or \
             _env_probe_run("ifconfig 2>/dev/null | grep -E '^(eth|wlan|en|wl|br|docker|virbr)|inet ' | head -12")
    if _iface:
        lines.append(f"- 接口/地址:\n{_iface[:500]}")
    else:
        lines.append("- 接口: （无法枚举：无 ip/ifconfig 或权限受限）")
    _route = _env_probe_run("ip route 2>/dev/null | head -4") or \
             _env_probe_run("route -n 2>/dev/null | head -6")
    if _route:
        lines.append(f"- 路由:\n{_route[:300]}")
    else:
        lines.append("- 路由: （无法读取）")
    return lines


def _env_section_disk() -> List[str]:
    _df = _env_probe_run("df -h 2>/dev/null | head -6")
    if not _df:
        return []
    return ["### 磁盘", f"```\n{_df}\n```"]


def _env_section_tools(tools: Optional[List[str]] = None) -> List[str]:
    import shutil as _sh
    _list = tools if tools else _ENV_PROBE_TOOLS
    _avail, _missing = [], []
    for _t in _list:
        (_avail if _sh.which(_t) else _missing).append(_t)
    return ["### 命令可用性",
            f"- ✅ 可用 ({len(_avail)}): {', '.join(_avail)}",
            f"- ❌ 缺失 ({len(_missing)}): {', '.join(_missing)}"]


# which 参数允许的命令名字符（拒绝 shell 元字符，防注入）
_ENV_WHICH_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\.\+/]+$")


def _env_probe_parse_types(probe_type: str) -> List[str]:
    """解析逗号分隔的 type 列表：去重保序；非法项忽略，全非法或空 → ['general']。"""
    _ts = []
    for _t in re.split(r"[,，\s]+", probe_type or ""):
        _t = _t.strip().lower()
        if _t in _ENV_PROBE_TYPES and _t not in _ts:
            _ts.append(_t)
    return _ts or ["general"]


def _env_probe_which_lines(which: str) -> List[str]:
    """指定命令查询：shutil.which 找路径 + 无 shell 参数列表取版本（--version/-V/-v）。"""
    import shutil as _sh
    import subprocess as _sp
    _cmds = [c for c in re.split(r"[,，\s]+", which or "") if c.strip()]
    if not _cmds:
        return []
    lines = ["### 指定命令查询"]
    for _c in _cmds[:10]:  # 上限 10 个，防滥用
        if not _ENV_WHICH_NAME_RE.fullmatch(_c):
            lines.append(f"- ⚠️ {_c[:40]}: 非法命令名（仅支持单个命令名，不能带参数）")
            continue
        _p = _sh.which(_c)
        if not _p:
            lines.append(f"- ❌ {_c}: 未找到（PATH 中不存在）")
            continue
        _ver = ""
        for _flag in ("--version", "-V", "-v"):
            try:
                _r = _sp.run([_p, _flag], capture_output=True, text=True,
                             errors="replace", timeout=2)
                _out = ((_r.stdout or "").strip() + " " + (_r.stderr or "").strip()).strip()
                if _out:
                    _ver = _out.splitlines()[0][:80]
                    break
            except Exception:
                continue
        if _ver:
            lines.append(f"- ✅ {_c}: {_p}（{_ver}）")
        else:
            lines.append(f"- ✅ {_c}: {_p}")
    return lines


def _exec_env_probe(probe_type: str = "", which: str = "") -> str:
    """EnvProbe：按 AI 指定的任务类型动态探测环境（只读，秒回）。

    - type=general（缺省）：全量报告（OS/架构/内核/Python/权限/网络/磁盘/工具表）
    - type=deploy/network/python/build/database/web/permission：只探测相关块 +
      该类型专属命令（版本/端口等），省 token
    - type 支持逗号组合多个（如 'web,network'）：sections/tools/extra 取并集
    - which=cmd1,cmd2：查询指定命令的路径与版本；仅传 which（未显式给 type）时
      输出轻量结果（系统摘要 + 查询），不跑全量
    """
    _ts = _env_probe_parse_types(probe_type)
    _explicit = bool((probe_type or "").strip())

    # 轻量模式：只查命令（未显式指定 type）
    if (which or "").strip() and not _explicit:
        _lines = ["## 📡 环境探测（轻量查询）", ""] + _env_section_system()
        _lines.append("")
        _lines += _env_probe_which_lines(which)
        return "\n".join(_lines)

    lines = ["## 📡 环境探测报告", ""]
    _secs_order = ["system", "user", "network", "disk", "tools"]
    if "general" in _ts:
        # general 参与组合 → sections/tools 取全量，extra 取其余类型的并集
        _wanted = set(_secs_order)
        _tools = None
        _extra = []
        for _t in _ts:
            for _e in _ENV_PROBE_TYPES[_t].get("extra") or []:
                if _e not in _extra:
                    _extra.append(_e)
    else:
        _wanted = set()
        _tools = []
        _extra = []
        for _t in _ts:
            _cfg = _ENV_PROBE_TYPES[_t]
            _wanted.update(_cfg["sections"])
            for _tt in _cfg.get("tools") or []:
                if _tt not in _tools:
                    _tools.append(_tt)
            for _e in _cfg.get("extra") or []:
                if _e not in _extra:
                    _extra.append(_e)
        if not _tools:
            _tools = None

    _secs = {
        "system": _env_section_system,
        "user": _env_section_user,
        "network": _env_section_network,
        "disk": _env_section_disk,
        "tools": lambda: _env_section_tools(_tools),
    }
    for _s in _secs_order:
        if _s not in _wanted:
            continue
        _lines_block = _secs[_s]()
        if _lines_block:
            lines += _lines_block
            lines.append("")
    # 类型专属探测（多类型时取并集）
    if _extra:
        lines.append(f"### 专属探测（{','.join(_ts)}）")
        for _label, _cmd in _extra:
            _out = _env_probe_run(_cmd)
            if _out:
                lines.append(f"- {_label}:\n{_out[:300]}")
        lines.append("")
    # 附加指定命令查询
    if (which or "").strip():
        _w = _env_probe_which_lines(which)
        if _w:
            lines += _w
            lines.append("")
    # ── 动态反思要点：仅当探测到实际缺口时提示，避免每轮重复静态反思 ──
    import shutil as _sh_tip
    _tips = []
    if not _sh_tip.which("ss") and _sh_tip.which("netstat"):
        _tips.append("ss 缺失 → 端口/连接查询改用 netstat")
    if not _sh_tip.which("ip") and _sh_tip.which("ifconfig"):
        _tips.append("ip 缺失 → 接口/路由查询改用 ifconfig")
    if not _sh_tip.which("grep"):
        _tips.append("grep 缺失（Windows 环境）→ 用 findstr 替代")
    if _tips:
        lines.append("> 反思要点：" + "；".join(_tips))
    return "\n".join(lines)


def _exec_enter_plan_mode() -> str:
    """进入计划模式。通过修改全局标记实现。"""
    try:
        global _PLAN_MODE_ACTIVE
        _PLAN_MODE_ACTIVE = True
        return "✅ 已进入 Plan 模式。在此模式下禁止执行命令和修改文件。请输出计划并提交用户确认。"
    except Exception as e:
        return f"❌ EnterPlanMode failed: {e}"


def _exec_exit_plan_mode() -> str:
    """退出计划模式。"""
    try:
        global _PLAN_MODE_ACTIVE
        _PLAN_MODE_ACTIVE = False
        return "✅ 已退出 Plan 模式，恢复正常执行模式。"
    except Exception as e:
        return f"❌ ExitPlanMode failed: {e}"


def _exec_choose_ask(question: str, options: list) -> str:
    """向用户展示选项菜单，最后一个选项固定为"以上都不是"，选择后进入自由输入。"""
    try:
        from .ai_lib.ui import select_option, text_input as _text_input

        if not options or not isinstance(options, list):
            options = [_mcp_t("是", "Yes"), _mcp_t("否", "No")]

        # 固定添加"以上都不是"选项
        none_label = _mcp_t("以上都不是，我自己输入", "None of the above, I'll type")
        all_options = list(options) + [none_label]

        selected = select_option(
            message=question,
            options=all_options,
            default=all_options[0],
        )

        if selected == none_label:
            # 自由输入 — 先回显用户选择，建立视觉连续性（避免 InquirerPy select 清屏后出现空白断层）
            from rich.console import Console as _RC
            _rc = _RC()
            _rc.print(f"  → {selected}", style="dim")
            free_text = _text_input(_mcp_t("💬 请输入你的回答", "💬 Your answer:")).strip()
            if free_text:
                return f"__FREE_TEXT__:{free_text}"
            return _mcp_t("⏹ 用户未输入", "⏹ No input from user")
        return selected

    except (KeyboardInterrupt, EOFError):
        return _mcp_t("⏹ 用户取消", "⏹ Cancelled by user")
    except Exception as e:
        return f"❌ choose_ask failed: {e}"



def _exec_config(action: str, key: str, value: str = None) -> str:
    """获取或设置配置。"""
    try:
        config_path = os.path.join(os.path.expanduser("~"), ".config", "onyx", "config.json")
        if action == "get":
            if os.path.exists(config_path):
                import json as _json
                with open(config_path, "r", encoding="utf-8") as f:
                    config = _json.load(f)
                if key in config:
                    val = config[key]
                    return f"`{key}` = {_json.dumps(val, ensure_ascii=False)}"
                return f"`{key}` 未设置"
            return "配置文件不存在"
        elif action == "set":
            if value is None:
                return "❌ set 操作需要提供 value"
            import json as _json
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            config = {}
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    try:
                        config = _json.load(f)
                    except Exception:
                        config = {}
            # 尝试解析 value 为数字或布尔
            try:
                parsed = _json.loads(value)
                config[key] = parsed
            except Exception:
                config[key] = value
            with open(config_path, "w", encoding="utf-8") as f:
                _json.dump(config, f, ensure_ascii=False, indent=2)
            return f"✅ `{key}` 已设置为 {config[key]}"
        return f"❌ 未知操作: {action}"
    except Exception as e:
        return f"❌ Config failed: {e}"


# ── 子代理命令执行器（由 handle_ai 注入：子代理经同一安全管线执行命令）──
_SUBAGENT_COMMAND_EXECUTOR = None
_SUBAGENT_CMD_LOCK = threading.Lock()  # 全端子代理命令串行化（共享 PTY 防输出交错）
_SUBAGENT_STATUS = None                # 当前 Agent 工具的 Status spinner 引用（同步模式实时刷新）
# ── 子代理联网执行器（由 handle_ai 注入：web_search_agent 的 web 工具直接走底层实现，
#    不逐次弹确认——批准发生在 Agent 派发时；SSRF/协议/超时防线与主 AI 一致）──
_SUBAGENT_WEB_EXECUTOR = None

# ── 主 AI 命令执行器（由 handle_ai 注入：RunCommand 工具经完整安全管线执行，
#    危险命令弹用户确认。模块级 handler 通过 get_main_command_executor 获取）──
_MAIN_RUN_COMMAND_EXECUTOR = None


def set_main_command_executor(fn: Callable) -> None:
    """注入主 AI 的 RunCommand 执行器（handle_ai 内的闭包：危险确认 + capture + parse_and_execute）。"""
    global _MAIN_RUN_COMMAND_EXECUTOR
    _MAIN_RUN_COMMAND_EXECUTOR = fn


def get_main_command_executor() -> Optional[Callable]:
    return _MAIN_RUN_COMMAND_EXECUTOR




# ── library 工具结果采集白名单：文件/代码/Git/命令类工具执行后记录到 library ──
# 读工具记录内容；写工具记录 path + 状态（content 等大字段在格式化时排除）
LIB_CAPTURE_TOOLS = frozenset({
    # 读类
    "read_file", "grep_search", "glob_search", "get_file_info",
    "search_files", "search_content", "ListDirectory", "DirectoryTree",
    "MemoryRead", "MemorySearch",
    "py_diagnostics", "py_symbols", "LspDiagnostics", "LspSymbols",
    "LspHover", "LspDefinition", "LspReferences", "LspCompletion", "LspFormat",
    "GitStatus", "GitDiff", "GitLog", "GitBranch",
    # 写类
    "write_file", "edit_file", "validate_edit", "preview_edit",
    "delete_file", "delete_directory", "create_directory",
    "move_file", "copy_file", "UndoLastEdit",
    # 命令
    "RunCommand",
})


def _exec_run_command(command: str) -> str:
    """RunCommand 工具执行器（模块级分发，注册于 _BUILTIN_HANDLERS）。

    实际执行由 handle_ai 注入的闭包完成：危险命令确认、adv_code 语法检查、
    capture_command_output 捕获 + parse_and_execute 执行。
    未注入时（异常路径/测试环境）拒绝执行——绝不绕过安全管线。
    """
    executor = get_main_command_executor()
    if executor is None:
        return "⛔ RunCommand 不可用：主会话未初始化命令执行器（安全管线未注入）"
    try:
        return executor(command)
    except Exception as e:
        return f"命令执行失败: {e}"


def _format_run_command_result(cmd: str, rc, captured: str) -> str:
    """RunCommand 工具结果结构化：命令 / 退出码 / 执行结果。

    AI 可见（tool role 消息）与转录落盘共用；rc 为 None 表示命令未经过
    subprocess 执行（内置命令/被拦截），显示 "-"。
    """
    _rc = "-" if rc is None else str(rc)
    _out = (captured or "").strip() or "(无输出)"
    return f"命令: {cmd}\n退出码: {_rc}\n执行结果:\n{_out}"


def set_subagent_command_executor(fn: Callable) -> None:
    """注入命令执行器（handle_ai 内的闭包：capture + parse_and_execute + 危险命令拒绝）。"""
    global _SUBAGENT_COMMAND_EXECUTOR
    _SUBAGENT_COMMAND_EXECUTOR = fn


def get_subagent_command_executor() -> Optional[Callable]:
    return _SUBAGENT_COMMAND_EXECUTOR


def set_subagent_web_executor(fn: Callable) -> None:
    """注入子代理联网执行器（handle_ai 内的闭包：直接调用底层 web 实现，不弹确认）。"""
    global _SUBAGENT_WEB_EXECUTOR
    _SUBAGENT_WEB_EXECUTOR = fn


def get_subagent_web_executor() -> Optional[Callable]:
    return _SUBAGENT_WEB_EXECUTOR


def build_subagent_blocked_commands(builtin_commands: Optional[Dict] = None,
                                    root_dir: str = None) -> set:
    """子代理禁用的内置命令集合：Onyx BUILTIN_COMMANDS + other_terminal_cmd.json + cd。

    子代理只许执行真实系统/工具命令；内置命令（exit/clear/ai/manage/sado/source、
    export/sudo/...、cd）会篡改 REPL 状态（退出、清屏、切目录、改环境、提权），
    一律不暴露。root_dir 为 None 时跳过配置文件加载（调用方传 ROOT_DIR）。
    """
    _blocked: set = set()
    try:
        if builtin_commands:
            _blocked.update(str(k).lower() for k in builtin_commands.keys())
    except Exception:
        pass
    try:
        from lib.safe import load_other_terminal_commands as _load_otc
        _otc = _load_otc(root_dir)
        for _cmds in _otc.values():
            for _c in _cmds:
                _blocked.add(str(_c).lower())
    except Exception:
        pass
    try:
        from Onyx import BUILTIN_COMMANDS as _OB_C
        if _OB_C:
            _blocked.update(str(k).lower() for k in _OB_C.keys())
    except Exception:
        pass
    _blocked.add("cd")  # shell 内置 cd 会改共享 shell CWD（Onyx 会同步回 Python）
    _blocked.discard("")
    return _blocked


def extract_subagent_command_head(cmd: str) -> str:
    """提取子代理命令首行第一个词（小写），用于内置命令拦截。"""
    _first = (cmd or "").strip().splitlines()[0].strip() if (cmd or "").strip() else ""
    if not _first:
        return ""
    return _first.split(maxsplit=1)[0].strip().lower()


def _refresh_subagent_status(_sa_mod, final: bool = False) -> None:
    """把子代理最近活动（灰色）刷新到当前 Status spinner，证明没卡住。"""
    try:
        _status = _SUBAGENT_STATUS
        if _status is None:
            return
        if final:
            _status.update(_mcp_t("  [dim]🧩 子代理运行完成[/]", "  [dim]🧩 Subagents finished[/]"))
            return
        _act = _sa_mod.get_manager().format_activity(4)
        if _act:
            _status.update(_mcp_t("  [dim]🧩 子代理运行中…\n" + _act + "[/]", "  [dim]🧩 Subagents running…\n" + _act + "[/]"))
        else:
            _status.update(_mcp_t("  [dim]🧩 子代理运行中…[/]", "  [dim]🧩 Subagents running…[/]"))
    except Exception:
        pass


def _exec_agent(description: str, prompt: str, name: str = "",
                mode: str = "sync", model: str = "",
                count: int = 1, tasks: list = None,
                agent_type: str = "explore") -> str:
    """启动子代理（explore=探索 / plan=规划 / lint=代码分析 / test=测试 / web_search_agent=联网调研）。

    - 同步：阻塞等待完成，总结直接作为工具结果交还主 AI 上下文。
    - 异步：立即返回任务 ID；主 AI 继续其他工作，完成结果自动注入本会话。
    - 模型默认当前平台最便宜模型（「X Pro」= 最低价 AI），可用 model 覆盖。
    """
    try:
        from .ai_lib import subagent as _subagent_mod
        mode = (mode or "sync").lower()
        if mode not in ("sync", "async"):
            mode = "sync"
        agent_type = (agent_type or "explore").lower()
        if agent_type not in _subagent_mod.AGENT_TYPES:
            agent_type = "explore"
        _label = _subagent_mod.AGENT_TYPE_LABELS.get(agent_type, "探索")
        _tasks = _subagent_mod.run_agent(
            agent_type=agent_type,
            prompt=prompt,
            name=name or (description or "")[:30],
            mode=mode,
            model=model or None,
            count=count,
            tasks=tasks,
            wait=(mode != "sync"),   # sync 模式由本函数轮询等待（顺便实时刷新灰色活动尾行）
        )
        if not _tasks:
            return ("❌ Agent: 任务列表为空（请提供 prompt 或 tasks；"
                    "若指定了 count>1，请确保 prompt 可拆分为多个编号任务或 --- 分隔段）")
        if mode == "sync":
            # ── 等待期间实时刷新灰色活动尾行（告诉用户没卡住）──
            _deadline = time.time() + _subagent_mod.SYNC_TIMEOUT
            while any(t.status in ("pending", "running") for t in _tasks):
                if time.time() > _deadline:
                    break
                _refresh_subagent_status(_subagent_mod)
                _subagent_mod.get_manager().wait_any(timeout=0.3)  # 事件驱动等待（完成即醒）
            _refresh_subagent_status(_subagent_mod, final=True)
            # ── 快照状态 → 汇总 → 按同一快照排空 ──
            # 修复竞态：原实现 drain_done 与下方汇总各读一次 status（无同步），
            # 任务在两次读取之间完成会「双注入」（drain 时 running、汇总时 done）
            # 或「丢失」（汇总时 running、drain 时 done）。一次观察，两个消费者。
            _subagent_snap = [(t, t.status) for t in _tasks]
            _subagent_mod.get_manager().drain_ids(
                {t.id for t, _s in _subagent_snap if _s == "done"})
        if mode == "async":
            ids = ", ".join(t.id for t in _tasks)
            names = ", ".join(f"「{t.name}」" for t in _tasks)
            return (
                f"✅ 已异步启动 {len(_tasks)} 个{_label}子代理 {names}（任务ID: {ids}）。\n"
                f"主 AI 可继续其他工作；子代理完成总结后会自动注入本会话上下文，届时再整合结论。"
            )
        # 同步：汇总全部总结（基于快照 _subagent_snap，不再二次读状态）
        # 超时仍运行的任务不算失败：后台继续跑，完成后由收集器注入本会话
        lines = []
        for t, _snap_status in _subagent_snap:
            if _snap_status == "done" and t.summary:
                lines.append(f"【{_label}子代理「{t.name}」总结】\n{t.summary}")
            elif _snap_status in ("pending", "running"):
                lines.append(
                    f"【{_label}子代理「{t.name}」仍在运行】"
                    f"等待超过 {_subagent_mod.SYNC_TIMEOUT} 秒，主 AI 可继续其他工作；"
                    f"该子代理完成后总结会自动注入本会话上下文。"
                )
            else:
                lines.append(f"【{_label}子代理「{t.name}」失败】{t.error or t.status}")
        if len(lines) == 1:
            return lines[0]
        return "\n\n".join(lines)
    except Exception as e:
        return f"❌ Agent 执行失败: {e}"


def _is_private_ip(ip) -> bool:
    """判断 IP 是否为内网/回环/链路本地/保留地址（SSRF 防护）。"""
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _ssrf_block_reason(url: str) -> Optional[str]:
    """2026-09 加固（M3）：检查 URL 是否指向内网/保留地址。

    返回拒绝原因字符串；放行返回 None。域名会解析全部 A 记录，
    任一记录指向内网即拒绝（防 DNS rebinding 的常见变体）。
    """
    from urllib.parse import urlparse as _urlparse
    import ipaddress as _ipaddr
    import socket as _sock
    try:
        _u = _urlparse(url)
        if _u.scheme not in ("http", "https"):
            return "仅支持 http/https 协议"
        _host = _u.hostname
        if not _host:
            return "URL 缺少主机名"
        try:
            _ip = _ipaddr.ip_address(_host)
        except ValueError:
            try:
                _infos = _sock.getaddrinfo(_host, None)
            except Exception:
                return f"无法解析主机: {_host}"
            for _info in _infos:
                _ip_str = _info[4][0].split("%")[0]
                try:
                    _ip = _ipaddr.ip_address(_ip_str)
                except ValueError:
                    continue
                if _is_private_ip(_ip):
                    return f"域名 {_host} 解析到内网地址 {_ip_str}"
            return None
        if _is_private_ip(_ip):
            return f"目标地址是内网/保留地址: {_host}"
        return None
    except Exception as _e:
        return f"URL 校验失败: {_e}"


def _http_get_text(url: str, timeout: int, headers: dict) -> Tuple[Optional[str], str]:
    """GET 并返回页面文本：requests 优先（跟随重定向），requests 缺失时回退 curl。

    返回 (text, "") 成功；(None, 错误信息) 失败。供搜索引擎页面与抓取共用，
    保证 requests 库不可用时 web_search 依然可用（需系统有 curl）。
    """
    try:
        import requests as _req
    except ImportError:
        pass
    else:
        try:
            _resp = _req.get(url, timeout=timeout, headers=headers)
            return _resp.text, ""
        except Exception as _e:
            return None, f"requests 失败: {_e}"
    try:
        import subprocess as _sp
        _result = _sp.run(["curl", "-sL", "--max-time", str(timeout), url],
                          capture_output=True, text=True, timeout=timeout + 5)
        if _result.stdout:
            return _result.stdout, ""
        return None, f"curl 返回空: {_result.stderr[:200]}"
    except Exception as _e:
        return None, f"curl 失败: {_e}"


# ═══════════════════════════════════════════════════════════════
# web_search — 多重混合搜索（多查询 × 多引擎，去重/过滤/抓页）
# ═══════════════════════════════════════════════════════════════

_WEB_ENGINES: Tuple[str, ...] = ("duckduckgo", "bing")

# web_search 并行度：搜索阶段（查询 × 引擎任务池）与抓取阶段（页面池）。
# 串行实现总耗时 = 任务数 × 单请求；并行后 ≈ 最慢单个请求（弱 AI 摘要随抓取并行）。
_WEB_SEARCH_WORKERS = 6
_WEB_FETCH_WORKERS = 4

# 阶段总时间预算（秒）：到点收工，不等最慢任务（超时任务标注后跳过）。
_WEB_SEARCH_BUDGET = 12.0
_WEB_FETCH_BUDGET = 20.0

# topics 批量主题：单次调用并行查询多个独立主题（每主题独立搜索+抓页+分栏输出）
_WEB_TOPICS_MAX = 5
_WEB_TOPICS_WORKERS = 5
_WEB_TOPIC_OVERRIDES = ("query", "engines", "max_results", "fetch_pages", "fetch_limit",
                        "max_chars_per_page", "ai_assist", "allowed_domains", "exclude_domains",
                        "language", "region", "time_range", "safe_search", "output_format",
                        "timeout")

# ── 查询增强（无外部依赖的精度提升）──
# 英文长句去停用词生成关键词变体；中文含 ASCII 技术词时附加英文变体（技术文档英文更全）。
_WEB_STOPWORDS_EN = frozenset({
    "how", "to", "the", "a", "an", "is", "are", "was", "were", "what", "which", "why",
    "when", "where", "who", "do", "does", "did", "for", "with", "of", "in", "on",
    "at", "and", "or", "vs", "versus", "use", "using", "can", "should", "best",
})

# ── 结果重排信号（软加权，不埋没好结果）──
_WEB_AUTHORITY_DOMAINS = frozenset({
    "github.com", "gitlab.com", "readthedocs.io", "w3.org", "developer.mozilla.org",
    "python.org", "nodejs.org", "nginx.org", "apache.org", "kubernetes.io", "docker.com",
    "react.dev", "vuejs.org", "stackoverflow.com", "docs.python.org", "pypi.org",
    "npmjs.com", "crates.io", "docs.rs", "openai.com", "anthropic.com", "microsoft.com",
    "google.com", "apple.com", "ibm.com", "oracle.com", "cloudflare.com",
    "deepseek.com", "aliyun.com", "tencent.com", "baidu.com", "bytedance.com",
    "aws.amazon.com", "azure.microsoft.com", "developer.android.com",
    "zhihu.com", "segmentfault.com", "juejin.cn", "ruanyifeng.com", "cnblogs.com",
})
_WEB_JUNK_DOMAIN_HINTS = ("top10", "top-10", "best10", "rank", "coupon", "deals",
                          "vip", "free-", "-free", "download", "list")
_WEB_JUNK_TITLE_HINTS = ("top 10", "top10", "best 10", "coupon", "discount",
                         "免费下载", "优惠券", "福利")

# ── SERP 结果缓存（进程内 LRU）：同键查询 15 分钟内直接命中，零网络请求；
#    失败结果缓存 30 秒（防抖，避免反复打刚挂掉的引擎）。 ──
_WEB_CACHE_TTL = 900
_WEB_CACHE_FAIL_TTL = 30
_WEB_CACHE_MAX = 200
_WEB_CACHE: Dict[str, Tuple[float, float, Tuple[List[Dict], str]]] = {}  # key -> (ts, ttl, (items, err))
_WEB_CACHE_LOCK = threading.Lock()

# ── 引擎健康滑动窗口：连续 _ENGINE_DEGRADE_AFTER 次失败 → 降级 _ENGINE_DEGRADE_SECONDS
#    （降级期跳过请求；到期自动恢复试探）。 ──
_ENGINE_STATS_WINDOW = 10
_ENGINE_DEGRADE_AFTER = 3
_ENGINE_DEGRADE_SECONDS = 1800
_WEB_ENGINE_STATS: Dict[str, List[Tuple[float, bool]]] = {}   # engine -> [(ts, ok), ...]
_WEB_ENGINE_DEGRADED_UNTIL: Dict[str, float] = {}
_WEB_ENGINE_LOCK = threading.Lock()


def _ddg_url_normalize(href: str) -> str:
    """DuckDuckGo HTML 结果链接是 /l/?uddg=<目标> 跳转，解出真实 URL。"""
    if "uddg=" in href:
        from urllib.parse import unquote as _unquote
        m = re.search(r"[?&]uddg=([^&]+)", href)
        if m:
            return _unquote(m.group(1))
    if href.startswith("//"):
        href = "https:" + href
    return href


def _extract_ddg_results(html: str) -> List[Dict]:
    """解析 DuckDuckGo HTML 结果页（result__a 链接 + result__snippet 摘要）。"""
    import html as _htm
    results = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                         html, re.DOTALL):
        title = _htm.unescape(re.sub(r"<[^>]+>", "", m.group(2)).strip())
        if title:
            results.append({"title": title, "url": _ddg_url_normalize(m.group(1)), "snippet": ""})
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
    for i, s in enumerate(snips[: len(results)]):
        results[i]["snippet"] = _htm.unescape(re.sub(r"<[^>]+>", "", s).strip())
    return results


def _extract_lite_results(html: str) -> List[Dict]:
    """解析 DuckDuckGo lite 端点结果页（rel=nofollow 链接 + result-snippet 摘要）。"""
    import html as _htm
    results = []
    for m in re.finditer(r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                         html, re.DOTALL):
        href = m.group(1)
        if href.startswith("javascript") or "lite.duckduckgo.com" in href:
            continue
        title = _htm.unescape(re.sub(r"<[^>]+>", "", m.group(2)).strip())
        if title:
            results.append({"title": title, "url": _ddg_url_normalize(href), "snippet": ""})
    snips = re.findall(r'class="result-snippet"[^>]*>(.*?)</td>', html, re.DOTALL)
    for i, s in enumerate(snips[: len(results)]):
        results[i]["snippet"] = _htm.unescape(re.sub(r"<[^>]+>", "", s).strip())
    return results


def _extract_bing_results(html: str) -> List[Dict]:
    """解析 Bing 结果页（li.b_algo 内 h2>a 链接 + p 摘要）。"""
    import html as _htm
    results = []
    for m in re.finditer(r'<li class="b_algo".*?</li>', html, re.DOTALL):
        block = m.group(0)
        am = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not am:
            continue
        url = am.group(1)
        title = _htm.unescape(re.sub(r"<[^>]+>", "", am.group(2)).strip())
        pm = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
        snippet = _htm.unescape(re.sub(r"<[^>]+>", "", pm.group(1)).strip()) if pm else ""
        if url:
            results.append({"title": title, "url": url, "snippet": snippet})
    return results


def _web_parallel(fn, tasks: list, workers: int, budget: float = 0.0) -> list:
    """按输入顺序并行执行 fn(task)（线程池），返回与 tasks 同序的结果列表。

    - 任务 ≤ 1 时直接串行（避免无谓线程开销）；
    - budget > 0：总时间预算（秒），到点收工——未完成任务在结果中占位 None；
    - 中断/异常时不阻塞等待未完成请求（残留线程随各自请求超时自然结束）。
    """
    if len(tasks) <= 1:
        return [fn(t) for t in tasks]
    import concurrent.futures as _cf
    _ex = _cf.ThreadPoolExecutor(max_workers=max(1, min(workers, len(tasks))))
    try:
        _futs = {_ex.submit(fn, t): i for i, t in enumerate(tasks)}
        _out = [None] * len(tasks)
        if budget <= 0:
            for _f, _i in _futs.items():
                _out[_i] = _f.result()
            return _out
        _deadline = time.time() + budget
        for _f in _cf.as_completed(_futs):
            if time.time() >= _deadline:
                break
            _i = _futs[_f]
            try:
                _out[_i] = _f.result()
            except Exception:
                _out[_i] = None
        return _out
    finally:
        _ex.shutdown(wait=False, cancel_futures=True)


def _web_cache_key(engine: str, query: str, region: str, lang: str,
                   time_range: str, safe: bool) -> str:
    """SERP 缓存键：引擎 + 规范化查询 + 偏好参数。"""
    _q = " ".join((query or "").lower().split())
    return "|".join([engine, _q, region, lang, time_range, "1" if safe else "0"])


def _web_cache_get(key: str) -> Optional[Tuple[List[Dict], str]]:
    """读 SERP 缓存：命中且未过期 → (items, err)；否则 None（过期条目清除）。"""
    with _WEB_CACHE_LOCK:
        _hit = _WEB_CACHE.get(key)
        if not _hit:
            return None
        _ts, _ttl, _payload = _hit
        if time.time() - _ts > _ttl:
            _WEB_CACHE.pop(key, None)
            return None
        _items, _err = _payload
        return ([dict(i) for i in _items], _err)


def _web_cache_put(key: str, items: List[Dict], err: str, ttl: float) -> None:
    """写 SERP 缓存（浅拷贝防外部修改污染）；超上限淘汰最旧条目。"""
    with _WEB_CACHE_LOCK:
        if len(_WEB_CACHE) >= _WEB_CACHE_MAX and key not in _WEB_CACHE:
            try:
                _old = min(_WEB_CACHE, key=lambda _k: _WEB_CACHE[_k][0])
                del _WEB_CACHE[_old]
            except Exception:
                pass
        _WEB_CACHE[key] = (time.time(), ttl, ([dict(i) for i in items], err))


def _web_engine_report(engine: str, ok: bool) -> None:
    """更新引擎健康滑动窗口；最近连续 _ENGINE_DEGRADE_AFTER 次失败 → 降级。"""
    with _WEB_ENGINE_LOCK:
        _now = time.time()
        _win = _WEB_ENGINE_STATS.setdefault(engine, [])
        _win.append((_now, ok))
        while len(_win) > _ENGINE_STATS_WINDOW:
            _win.pop(0)
        _tail = _win[-_ENGINE_DEGRADE_AFTER:]
        if len(_tail) >= _ENGINE_DEGRADE_AFTER and not any(_o for _, _o in _tail):
            _WEB_ENGINE_DEGRADED_UNTIL[engine] = _now + _ENGINE_DEGRADE_SECONDS


def _web_engine_degraded(engine: str) -> bool:
    """引擎是否处于降级期（调用方跳过其请求）；过期自动恢复。"""
    with _WEB_ENGINE_LOCK:
        _until = _WEB_ENGINE_DEGRADED_UNTIL.get(engine, 0.0)
        if time.time() >= _until:
            _WEB_ENGINE_DEGRADED_UNTIL.pop(engine, None)
            return False
        return True


def _web_result_relevant(query: str, title: str, url: str, snippet: str = "") -> bool:
    """抓页前相关性预筛（保守：只拦「明显不相关」的 SERP 结果）。

    - snippet 是引擎按查询返回的摘要，最可靠信号：命中查询词即相关；
    - 标题命中查询词 → 相关；URL 含核心词（≥4 字符）→ 相关；
    - 标题过短且无摘要（如纯编号/单字母）→ 信息不足，宁抓勿漏；
    - 仅当标题、摘要、URL 均无任何查询词痕迹时判为低相关（跳过自动抓取；
      不影响手动指定 urls）。中文等无空白语言整句参与匹配。
    """
    if not query:
        return True
    _words = [w for w in re.split(r"[^\w]+", query.lower()) if len(w) >= 2]
    if not _words:
        return True
    _title = (title or "").lower().strip()
    _snip = (snippet or "").lower()
    if any(w in _snip for w in _words):
        return True
    if any(w in _title for w in _words):
        return True
    _core = [w for w in _words if len(w) >= 4]
    if _core and any(w in (url or "").lower() for w in _core):
        return True
    if len(_title) < 8 and not _snip:
        return True
    return False


def _web_query_enhance(query: str) -> List[str]:
    """查询规范化与扩展：返回候选查询列表（原查询 + 变体，最多 3 个）。

    - 英文长句（≥4 词且停用词占比高）→ 去停用词的关键词短语变体；
    - 中文查询含 ASCII 技术词 → 附加纯英文变体（技术文档英文更全更准）；
    - 简单短查询不扩展（请求量不变）；变体去重、去空。
    """
    _q = (query or "").strip()
    if not _q:
        return []
    _cands = [_q]
    _ascii_words = [w for w in re.split(r"[^\w]+", _q)
                    if re.search(r"[A-Za-z0-9]", w)]
    # 1) 英文长句：去停用词生成关键词短语
    if len(_ascii_words) >= 4:
        _kept = [w for w in _ascii_words if w.lower() not in _WEB_STOPWORDS_EN]
        if 2 <= len(_kept) < len(_ascii_words):
            _cands.append(" ".join(_kept))
    # 2) 中文含 ASCII 技术词 → 英文变体
    _has_cjk = any("\u4e00" <= c <= "\u9fff" for c in _q)
    if _has_cjk and _ascii_words:
        _en = " ".join(w for w in _ascii_words if len(w) >= 2)[:100]
        if _en:
            _cands.append(_en)
    # 去重去空
    _seen: set = set()
    _out: List[str] = []
    for _c in _cands:
        _cc = re.sub(r"\s+", " ", _c).strip()
        if _cc and _cc.lower() not in _seen:
            _seen.add(_cc.lower())
            _out.append(_cc)
    return _out[:3]


def _web_rerank(results: List[Dict]) -> List[Dict]:
    """结果重排（按查询分组，组内打分降序；查询主序保留）。

    信号（软加权，稳定排序不埋没好结果）：
      - 标题命中查询词 +2/词、摘要命中 +1/词；
      - 权威域（官方文档/知名社区）+4；
      - SEO 垃圾域（top10/best/free 等）−3、垃圾标题词 −2。
    """
    from urllib.parse import urlparse as _urlparse
    _groups: Dict[str, List[Dict]] = {}
    _order: List[str] = []
    for _r in results:
        _q = _r.get("query", "")
        if _q not in _groups:
            _groups[_q] = []
            _order.append(_q)
        _groups[_q].append(_r)
    _out: List[Dict] = []
    for _q in _order:
        _words = [w for w in re.split(r"[^\w]+", _q.lower()) if len(w) >= 2]
        _scored = []
        for _r in _groups[_q]:
            _s = 0.0
            _title = (_r.get("title") or "").lower()
            _snip = (_r.get("snippet") or "").lower()
            _host = (_urlparse(_r.get("url", "")).netloc or "").lower().split(":")[0]
            for _w in _words:
                if _w in _title:
                    _s += 2.0
                elif _w in _snip:
                    _s += 1.0
            if any(_host == d or _host.endswith("." + d) for d in _WEB_AUTHORITY_DOMAINS):
                _s += 4.0
            elif any(_h in _host for _h in _WEB_JUNK_DOMAIN_HINTS):
                _s -= 3.0
            if any(_t in _title for _t in _WEB_JUNK_TITLE_HINTS):
                _s -= 2.0
            _scored.append((_s, _r))
        _scored.sort(key=lambda _x: -_x[0])  # 稳定排序：同分保持引擎原序
        _out.extend(_r for _s, _r in _scored)
    return _out


def _web_search_one(query: str, engine: str, timeout: int, region: str, lang: str,
                    time_range: str, safe: bool, max_results: int) -> Tuple[List[Dict], str]:
    """单查询 × 单引擎搜索：缓存优先，失败/空结果自动重试，健康上报。

    - 缓存：同键（引擎+规范化查询+偏好）15 分钟内直接返回上次结果，零网络请求；
      失败结果缓存 30 秒（防抖，避免反复打刚挂掉的引擎）。
    - DDG：html 端点请求成功但零结果/反爬空页时回退 lite 端点（更轻、更容忍 bot）；
      传输层失败（域名不可达/DNS 超时）不回退——同一域名的 lite 几乎必然同样不可达，
      避免双倍超时等待。检测 anomaly/challenge 拦截页并给出明确错误。
    - Bing：请求成功但零结果（空白/consent 页）重试一次；传输层失败不重试。
    - 健康上报：传输失败/反爬拦截计为引擎故障；正常响应（含零结果）计为可用，
      连续故障触发降级（见 _web_engine_degraded）。
    返回 (结果列表, 错误信息)；错误为空表示成功（结果可为空列表）。
    """
    _key = _web_cache_key(engine, query, region, lang, time_range, safe)
    _hit = _web_cache_get(_key)
    if _hit is not None:
        return _hit
    _items: List[Dict] = []
    _err = ""
    try:
        from urllib.parse import quote as _quote
        _UA = "Mozilla/5.0 (X11; Linux x86_64) Onyx-AI/1.0"
        _headers = {"User-Agent": _UA}
        if lang:
            _headers["Accept-Language"] = f"{lang};q=0.9,en;q=0.6"
        if engine == "duckduckgo":
            _headers["Referer"] = "https://duckduckgo.com/"
            _url = f"https://html.duckduckgo.com/html/?q={_quote(query)}"
            if region:
                _url += f"&kl={_quote(region)}"
            if lang:
                _url += f"&l={_quote(lang)}"
            if time_range in ("day", "week", "month", "year"):
                _url += f"&df={time_range[0]}"
            if safe:
                _url += "&kp=1"
            _html, _err = _http_get_text(_url, timeout, _headers)
            _items = _extract_ddg_results(_html) if _html is not None else []
            if not _items and _html is not None:
                # 请求成功但零结果/反爬空页 → 回退 lite 端点（更轻、更容忍 bot）；
                # 传输层失败不回退（同一域名几乎必然同样不可达，避免双倍超时）
                _lite = f"https://lite.duckduckgo.com/lite/?q={_quote(query)}"
                if region:
                    _lite += f"&kl={_quote(region)}"
                if safe:
                    _lite += "&kp=1"
                _html2, _err2 = _http_get_text(_lite, timeout, _headers)
                _items = _extract_lite_results(_html2) if _html2 is not None else []
            if not _items:
                if _html and ("anomaly" in _html.lower() or "challenge" in _html.lower()):
                    _err = f"duckduckgo:{query[:30]} → 反爬拦截（anomaly/challenge 页）"
                else:
                    _err = f"duckduckgo:{query[:30]} → {_err or _err2 or '无结果'}"
            else:
                _err = ""
        elif engine == "bing":
            # 瞬时失败/空白/consent 页 → 重试一次；传输层失败不重试
            _url = f"https://www.bing.com/search?q={_quote(query)}"
            if lang:
                _url += f"&setlang={_quote(lang)}"
            if region:
                _url += f"&cc={_quote(region)}"
            if safe:
                _url += "&adlt=strict"
            _html, _err = _http_get_text(_url, timeout, _headers)
            _items = _extract_bing_results(_html) if _html is not None else []
            if not _items and _html is not None:
                _html2, _err2 = _http_get_text(_url, timeout, _headers)
                _items = _extract_bing_results(_html2) if _html2 is not None else []
            if not _items:
                _err = f"bing:{query[:30]} → {_err or _err2 or '无结果'}"
            else:
                _err = ""
        else:
            _err = f"{engine}:{query[:30]} → 未知引擎"
    except Exception as _e:
        _err = f"{engine}:{query[:30]} → {_e}"
    if _err:
        _web_cache_put(_key, [], _err, _WEB_CACHE_FAIL_TTL)
        _web_engine_report(engine, False)
        return [], _err
    _web_cache_put(_key, _items, "", _WEB_CACHE_TTL)
    _web_engine_report(engine, True)
    return _items[:max_results], ""


def _extract_page_title(html: str) -> str:
    """提取 <title> 标签文本（用于正文前标注页面标题）。"""
    import html as _htm
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    if not m:
        return ""
    return _htm.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip())


def _extract_page_text(html: str, max_chars: int) -> str:
    """粗抽取正文：优先 article/main 区块，去 script/style/nav 等噪音 → 去标签 → 折叠空白。

    优先取 <article>/<main> 内容可跳过大量站点级导航/侧栏/页脚噪音；
    再整体剔除脚本、样式、表单、悬浮层等无正文价值标签与 HTML 注释。
    """
    _m = re.search(r"(?is)<(article|main)[^>]*>(.*?)</\1>", html)
    if _m:
        html = _m.group(2)
    _text = re.sub(r"(?is)<!--.*?-->", " ", html)
    _text = re.sub(
        r"(?is)<(script|style|nav|footer|header|aside|form|noscript|iframe|svg|canvas"
        r"|template|figure|select|button|input|textarea|dialog|menu)[^>]*>.*?</\1>",
        " ", _text)
    _text = re.sub(r"<[^>]+>", " ", _text)
    _text = re.sub(r"\s+", " ", _text).strip()
    return _text[:max_chars]


def _fetch_page_text(url: str, timeout: int, max_chars: int = 3000) -> Tuple[bool, str]:
    """抓取页面并抽取正文（SSRF 防护 + 逐跳校验重定向 + title 提取 + curl 回退）。"""
    try:
        from urllib.parse import urlparse as _urlparse
        _scheme = (_urlparse(url).scheme or "").lower()
        if _scheme not in ("http", "https"):
            return False, f"非 http/https 协议: {url}"
        try:
            import requests as _req
        except ImportError:
            _req = None
        _html = None
        if _req is not None:
            _target = url
            _resp = None
            for _hop in range(6):
                _reason = _ssrf_block_reason(_target)
                if _reason:
                    return False, f"已拒绝 {_target}（{_reason}）"
                try:
                    _resp = _req.get(
                        _target, timeout=timeout,
                        headers={"User-Agent": "Mozilla/5.0 Onyx-AI/1.0"},
                        allow_redirects=False,
                    )
                except Exception as _e:
                    return False, f"抓取失败: {_e}"
                if _resp.status_code in (301, 302, 303, 307, 308):
                    _loc = _resp.headers.get("Location")
                    if not _loc:
                        break
                    _target = _req.compat.urljoin(_target, _loc)
                    continue
                break
            if _resp is not None:
                _html = _resp.text
        if _html is None:
            # 回退：curl（同样过 SSRF 检查；不跟随重定向）
            _reason = _ssrf_block_reason(url)
            if _reason:
                return False, f"已拒绝 {url}（{_reason}）"
            import subprocess as _sp
            _result = _sp.run(["curl", "-s", "--max-redirs", "0",
                               "--max-time", str(timeout), url],
                              capture_output=True, text=True, timeout=timeout + 5)
            _html = _result.stdout or ""
        _title = _extract_page_title(_html)
        _text = _extract_page_text(_html, max_chars)
        if not _text:
            return False, f"页面无正文: {url}"
        if _title:
            _text = f"📄 {_title}\n{_text}"
        return True, _text
    except Exception as _e:
        return False, f"抓取失败: {_e}"


# ── 弱 AI 长文摘要 + 关键行压缩（web_search ai_assist 模式）──
_WEB_ASSIST_CONFIG_KEY = "web_ai_assist"   # 全局开关：~/.config/onyx/config.json（Config 工具可读写）
_WEB_ASSIST_MIN_CHARS = 1200              # 正文超过该长度才触发辅助 AI 摘要
_WEB_ASSIST_FETCH_CAP = 12000             # 辅助 AI 模式单页抓取上限（喂给弱 AI 的完整正文）
_WEB_ASSIST_SUMMARY_MAX = 1200            # 摘要输出防御性截断


def _load_web_ai_assist_flag() -> bool:
    """读取全局 AI 辅助开关（web_ai_assist，缺省关闭）。"""
    try:
        import json as _json
        _p = os.path.join(os.path.expanduser("~"), ".config", "onyx", "config.json")
        with open(_p, "r", encoding="utf-8") as _f:
            _cfg = _json.load(_f)
        return bool(_cfg.get(_WEB_ASSIST_CONFIG_KEY, False))
    except Exception:
        return False


def _web_assist_model(platform: str, current: str) -> str:
    """辅助 AI 用当前平台最便宜的模型（弱 AI = 低单价）：价格表取最低 input 价，
    无价格表时 deepseek 回退 flash，其余回退当前模型。"""
    _info = _SUPPORTED_PLATFORMS.get(platform or "")
    if not _info:
        return current or ""
    _models = _info.get("models") or []
    _prices = _info.get("price_per_million_tokens") or {}
    _cheapest, _best = None, None
    for _m in _models:
        _in_p = (_prices.get(_m) or {}).get("input")
        if _in_p is None:
            continue
        if _best is None or _in_p < _best:
            _best, _cheapest = _in_p, _m
    if _cheapest:
        return _cheapest
    if platform == "deepseek" and "deepseek-v4-flash" in _models:
        return "deepseek-v4-flash"
    return current or ""


def _web_assist_summarize(text: str, query: str) -> Optional[str]:
    """弱 AI 长文摘要：把完整正文交给当前平台最便宜模型总结，返回摘要。

    失败 / 中断 / 无密钥返回 None（调用方回退关键行压缩，工具不阻塞）；
    成功时同步把本次调用写入 cost.json（与压缩/子代理成本入账一致）。
    """
    try:
        from .ai_lib.api import call_ai_api_sse
        from .ai_lib.config import load_key_conf
        from .ai_lib.cost import append_cost_record
        import hashlib as _hl
        _conf = load_key_conf() or {}
        _plat = _conf.get("platform", "deepseek")
        _model = _web_assist_model(_plat, _conf.get("model", ""))
        _sys = (
            "You are a web article summarizer. Given a fetched page (title + body), "
            "output a concise summary in the SAME language as the article (Chinese stays Chinese). "
            "Keep key facts: names, numbers, dates, versions, URLs, conclusions, comparisons. "
            "Aim under 500 characters. No preamble, no bracket markers, no label. "
            "If the body is mostly navigation/ads noise, say in one sentence what the page is about."
        )
        _result = call_ai_api_sse(
            question="",
            messages=[
                {"role": "system", "content": _sys},
                {"role": "user", "content": f"查询主题: {query}\n\n页面正文:\n{text}"},
            ],
            tools=[],
            ai_tools_prompt="",
            user_home_dir=None,
            memory_block="",
            session_id="webassist_" + _hl.md5((query + text[:500]).encode("utf-8", "ignore")).hexdigest()[:12],
            model_override=_model,
            platform_override=_plat,
        )
        if _result.get("_interrupted") or _result.get("error"):
            return None
        _txt = (_result.get("txt") or "").strip()
        if not _txt:
            return None
        try:
            _u = _result.get("_usage") or {}
            _pt, _ct = _u.get("prompt_tokens") or 0, _u.get("completion_tokens") or 0
            if _pt or _ct:
                append_cost_record(os.path.expanduser("~"), _plat, _model, _pt, _ct)
        except Exception:
            pass
        return _txt[:_WEB_ASSIST_SUMMARY_MAX]
    except Exception:
        return None


def _compress_text_key_lines(text: str, terms: List[str], max_chars: int) -> str:
    """关键行压缩：按句子切分 → 查询词命中/位置打分 → 取高分句至预算上限。

    有查询词命中时优先保留命中句（解决"只抓开头/首页噪音"导致的失真）；
    无命中时按原文顺序取前部句子（标题自然保留）。输出保证 ≤ max_chars。
    """
    _clean = re.sub(r"\s+", " ", text).strip()
    if not _clean or len(_clean) <= max_chars:
        return _clean
    _sents = [s.strip() for s in re.split(r"(?<=[。！？；.!?])\s*|\n", _clean) if s.strip()]
    _kw = [t.lower() for t in terms if t and len(t) >= 2]
    _scored = []
    for _i, _s in enumerate(_sents):
        _score = 0.0
        _low = _s.lower()
        for _k in _kw:
            if _k in _low:
                _score += 3.0
        if _i == 0:
            _score += 1.5
        if len(_s) < 10:
            _score -= 1.0
        if len(_s) > 400:
            _score -= 0.5
        _scored.append((_score, _i, _s))
    if any(_sc[0] > 0 for _sc in _scored):
        _scored.sort(key=lambda _x: (-_x[0], _x[1]))
    else:
        _scored.sort(key=lambda _x: _x[1])
    _out: List[str] = []
    _used = 0
    for _sc in _scored:
        _s = _sc[2]
        if _used + len(_s) + 1 > max_chars:
            if _out:
                break
            _s = _s[:max_chars]
        _out.append(_s)
        _used += len(_s) + 1
    return " ".join(_out)[:max_chars]


def _web_fetch_one(url: str, timeout: int, fetch_cap: int, assist_on: bool,
                   max_chars: int, queries: List[str], query: str) -> Dict:
    """抓取单页（worker 线程内完成正文抽取 / 弱 AI 摘要 / 关键行压缩）。

    SSRF/协议/超时防线复用 _fetch_page_text；摘要与压缩逻辑与主循环一致，
    但移入 worker → 多页抓取与弱 AI 摘要并行，总耗时 ≈ 最慢单页。
    """
    _ok, _txt = _fetch_page_text(url, timeout, fetch_cap)
    if _ok and assist_on and len(_txt) > _WEB_ASSIST_MIN_CHARS:
        # 长文 + 辅助 AI 开启：完整正文交弱 AI 总结（失败回退关键行压缩）
        _sum = _web_assist_summarize(_txt, query or "web")
        if _sum:
            return {"url": url, "ok": True, "text": _sum, "mode": "summary"}
    if _ok and len(_txt) > max_chars:
        # 关键行压缩：查询词命中句优先，替代机械截取前 N 字符
        return {"url": url, "ok": True,
                "text": _compress_text_key_lines(_txt, queries, max_chars),
                "mode": "compress"}
    return {"url": url, "ok": _ok, "text": _txt, "mode": "raw"}


def _exec_web_search_multi(params: dict) -> str:
    """web_search 入口分发器。

    - 无 topics → 单主题完整链路（_exec_web_search_one_topic，原有行为不变）；
    - 有 topics[] → 批量独立主题：每个主题继承顶层参数、可覆盖，全部并行执行，
      输出按主题分栏（text）或结构化数组（json）。空 query 主题忽略，最多 5 个。
    """
    _p = params or {}
    _topics = [t for t in (_p.get("topics") or [])
               if isinstance(t, dict) and str(t.get("query") or "").strip()]
    _topics = _topics[:_WEB_TOPICS_MAX]
    if not _topics:
        return _exec_web_search_one_topic(_p)
    _merged = []
    for _t in _topics:
        _tp = {k: v for k, v in _p.items() if k != "topics"}
        for _k in _WEB_TOPIC_OVERRIDES:
            if _k in _t and _t[_k] is not None:
                _tp[_k] = _t[_k]
        _merged.append(_tp)
    _fmt = str(_p.get("output_format") or "text").lower()
    _outs = _web_parallel(lambda _tp: _exec_web_search_one_topic(_tp), _merged,
                          _WEB_TOPICS_WORKERS, budget=_WEB_SEARCH_BUDGET * 2)
    if _fmt == "json":
        return json.dumps({
            "action": "topics", "topic_count": len(_merged),
            "topics": [
                {"query": _t.get("query", ""),
                 "output": _o or "❌ 主题超时未完成（预算内未返回）"}
                for _t, _o in zip(_merged, _outs)
            ],
        }, ensure_ascii=False, indent=1)
    _lines = [f"🔎 web_search(topics): {len(_merged)} 个独立主题并行查询"]
    for _i, (_t, _o) in enumerate(zip(_merged, _outs), 1):
        _lines.append("")
        _lines.append("─" * 24)
        _lines.append(f"## 主题 {_i}: {str(_t.get('query', ''))[:60]}")
        _lines.append(_o or "❌ 主题超时未完成（预算内未返回）")
    return "\n".join(_lines)


def _exec_web_search_one_topic(params: dict) -> str:
    """web_search 单主题完整链路（search / fetch / mixed 三模式）。

    第一性原则设计，覆盖完整调研链路（旧 WebSearch/WebFetch 能力已合并）：
      action: search=仅搜索；fetch=仅抓取 urls 指定页面；mixed=搜索+自动抓页（默认）
      query / queries[]: 主查询 + 附加查询（最多 10 个，一次覆盖多个角度）
      urls[]: 指定 URL 直接抓正文（fetch 必填 / mixed 追加；同样过域名过滤与 SSRF 防护）
      engines[]: duckduckgo / bing；max_results: 每查询每引擎条数
      allowed_domains[] / exclude_domains[]: 域名双向过滤（对结果与 urls 都生效）
      language / region / time_range / safe_search: 搜索偏好（best-effort）
      fetch_pages / fetch_limit: 搜索后自动抓取排名靠前页
      max_chars_per_page: 单页正文截断；output_format: text / json；timeout: 单请求超时
    """
    try:
        from urllib.parse import urlparse as _urlparse, quote as _quote

        _p = params or {}
        _action = str(_p.get("action") or "mixed").lower()
        if _action not in ("search", "fetch", "mixed"):
            _action = "mixed"
        _query = str(_p.get("query", "") or "").strip()
        _urls = [str(u).strip() for u in (_p.get("urls") or []) if str(u).strip()]
        if _action in ("search", "mixed") and not _query:
            return "❌ web_search: action=search/mixed 需要 query 参数"
        if _action == "fetch" and not _urls:
            return "❌ web_search: action=fetch 需要 urls 参数（要抓取的 URL 列表）"
        _queries = [_query] if _query else []
        for _q in (_p.get("queries") or [])[:10]:
            _q = str(_q or "").strip()
            if _q and _q not in _queries:
                _queries.append(_q)
        _queries = _queries[:10]
        _engines = [str(e).lower() for e in (_p.get("engines") or _WEB_ENGINES)]
        _engines = [e for e in _engines if e in _WEB_ENGINES] or list(_WEB_ENGINES)
        try:
            _max_results = max(1, min(int(_p.get("max_results") or 8), 15))
        except Exception:
            _max_results = 8
        _allowed = [str(d).lower() for d in (_p.get("allowed_domains") or []) if str(d).strip()]
        _excluded = [str(d).lower() for d in (_p.get("exclude_domains") or []) if str(d).strip()]
        _lang = str(_p.get("language") or "").strip()
        _region = str(_p.get("region") or "").strip()
        _time = str(_p.get("time_range") or "").strip().lower()
        _safe = bool(_p.get("safe_search", False))
        _fetch_pages = bool(_p.get("fetch_pages", False))
        try:
            _fetch_limit = max(1, min(int(_p.get("fetch_limit") or 3), 5))
        except Exception:
            _fetch_limit = 3
        try:
            _max_chars = max(500, min(int(_p.get("max_chars_per_page") or 3000), 8000))
        except Exception:
            _max_chars = 3000
        _fmt = str(_p.get("output_format") or "text").lower()
        try:
            _timeout = max(5, min(int(_p.get("timeout") or 15), 60))
        except Exception:
            _timeout = 15
        # ── 弱 AI 长文摘要：per-call ai_assist 覆盖全局开关（web_ai_assist）──
        _assist_flag = _p.get("ai_assist")
        if isinstance(_assist_flag, bool):
            _assist_on = _assist_flag
        elif str(_assist_flag).lower() in ("true", "1", "yes", "on"):
            _assist_on = True
        elif str(_assist_flag).lower() in ("false", "0", "no", "off"):
            _assist_on = False
        else:
            _assist_on = _load_web_ai_assist_flag()
        # 抓取上限无条件放大：max_chars_per_page 作为输出预算，关键行压缩/弱 AI 摘要
        # 需要读到比输出更多的正文才有筛选余地（解决"只抓开头/首页噪音"失真）
        _fetch_cap = max(_max_chars, _WEB_ASSIST_FETCH_CAP)

        _results: List[Dict] = []
        _errors: List[str] = []

        # ── 1. 搜索阶段（search/mixed）——多查询 × 多引擎并行（线程池）──
        # 原串行实现总耗时 = 查询数 × 引擎数 × 单请求；并行后 ≈ 最慢单个请求
        # （含失败重试）。结果按「查询 → 引擎」原顺序收集，输出确定性不变。
        if _action in ("search", "mixed") and _queries:
            # 引擎健康降级：连续失败的引擎跳过请求（到期自动恢复）
            _engines_active = [e for e in _engines if not _web_engine_degraded(e)]
            if len(_engines_active) < len(_engines):
                _errors.append("引擎降级跳过: " + ", ".join(
                    sorted(set(_engines) - set(_engines_active))))
            if not _engines_active:
                _errors.append("所有引擎均处于降级状态，本次搜索跳过")
            else:
                # 查询增强：英文长句去停用词、中文技术查询加英文变体 → 多候选并行
                # （简单短查询不扩展，请求量基本不变；候选结果统一按原查询标注）
                _query_cands = [_web_query_enhance(_q) for _q in _queries]
                _tasks = [(qi, ci, ei)
                          for qi, _cands in enumerate(_query_cands)
                          for ci, _cand in enumerate(_cands)
                          for ei, _eng in enumerate(_engines_active)]
                # 总时间预算：到点收工，不等最慢引擎（超时任务标注后跳过）
                _outs = _web_parallel(
                    lambda _t: _web_search_one(_query_cands[_t[0]][_t[1]], _engines_active[_t[2]],
                                               _timeout, _region, _lang, _time,
                                               _safe, _max_results),
                    _tasks, _WEB_SEARCH_WORKERS, budget=_WEB_SEARCH_BUDGET)
                for (_qi, _ci, _ei), _res in zip(_tasks, _outs):
                    if _res is None:
                        _errors.append(f"{_engines_active[_ei]}:{_queries[_qi][:30]} → 超时未完成")
                        continue
                    _items, _werr = _res
                    if _werr:
                        _errors.append(_werr)
                        continue
                    _q, _eng = _queries[_qi], _engines_active[_ei]
                    for _it in _items:
                        _it["query"] = _q
                        _it["engine"] = _eng
                        _results.append(_it)

            # ── 去重（URL 规范化） + 域名过滤 ──
            _seen: set = set()
            _deduped: List[Dict] = []
            for _r in _results:
                _host = (_urlparse(_r["url"]).netloc or "").lower().split(":")[0]
                if not _host:
                    continue
                if _allowed and not any(_host == d or _host.endswith("." + d) for d in _allowed):
                    continue
                if any(_host == d or _host.endswith("." + d) for d in _excluded):
                    continue
                _key = _r["url"].lower().rstrip("/")
                if _key in _seen:
                    continue
                _seen.add(_key)
                _deduped.append(_r)
            # 组内重排（按查询分组）：权威域/词命中前置、SEO 垃圾信号降权，
            # 查询主序保留——展示顺序与预筛抓页（取前 N）都受益
            _results = _web_rerank(_deduped)

        # ── 2. 抓取阶段（fetch/mixed）：指定 urls + 搜索排名靠前页（同样过域名过滤与 SSRF）──
        _pages: List[Dict] = []
        _fetch_targets: List[str] = []
        _skipped: List[str] = []   # 相关性预筛跳过的 SERP 页（避免浪费抓取名额在无关页）
        if _action == "fetch":
            _fetch_targets = list(_urls)
        elif _action == "mixed":
            if _fetch_pages and _results:
                _picked = 0
                for _r in _results:
                    if _picked >= _fetch_limit:
                        break
                    if _web_result_relevant(_query, _r.get("title", ""), _r["url"],
                                             _r.get("snippet", "")):
                        _fetch_targets.append(_r["url"])
                        _picked += 1
                    else:
                        _skipped.append(_r["url"])
            _fetch_targets += _urls
        if _fetch_targets:
            _seen_urls: set = set()
            _entries: List[Dict] = []          # 按输入顺序占位；None = 待抓取（并行任务）
            _jobs: List[Tuple[int, str]] = []
            for _u in _fetch_targets:
                _host = (_urlparse(_u).netloc or "").lower().split(":")[0]
                if not _host:
                    _entries.append({"url": _u, "ok": False, "text": "无效 URL"})
                    continue
                if _allowed and not any(_host == d or _host.endswith("." + d) for d in _allowed):
                    _entries.append({"url": _u, "ok": False, "text": "被 allowed_domains 过滤"})
                    continue
                if any(_host == d or _host.endswith("." + d) for d in _excluded):
                    _entries.append({"url": _u, "ok": False, "text": "被 exclude_domains 过滤"})
                    continue
                _key = _u.lower().rstrip("/")
                if _key in _seen_urls:
                    continue
                _seen_urls.add(_key)
                _entries.append(None)
                _jobs.append((len(_entries) - 1, _u))
            if _jobs:
                # 并行抓取：正文抽取 / 长文摘要 / 关键行压缩均在 worker 内完成
                _outs = _web_parallel(
                    lambda _t: _web_fetch_one(_t[1], _timeout, _fetch_cap, _assist_on,
                                              _max_chars, _queries, _query),
                    _jobs, _WEB_FETCH_WORKERS, budget=_WEB_FETCH_BUDGET)
                for (_i, _u), _pg in zip(_jobs, _outs):
                    if _pg is None:
                        _pg = {"url": _u, "ok": False, "text": "超时未完成（预算内未返回）"}
                    _entries[_i] = _pg
            _pages = [e for e in _entries if e is not None]

        # ── 3. 输出 ──
        if _fmt == "json":
            return json.dumps({
                "action": _action, "query": _query, "queries": _queries, "engines": _engines,
                "ai_assist": _assist_on, "result_count": len(_results),
                "results": _results, "pages": _pages, "errors": _errors, "skipped": _skipped,
            }, ensure_ascii=False, indent=1)

        _lines: List[str] = []
        if _action == "fetch":
            _lines.append(f"📄 web_search(fetch): 抓取 {len(_pages)} 个指定 URL")
        else:
            _lines.append(f"🔎 web_search({_action}): {len(_queries)} 个查询 × {len(_engines)} 个引擎，"
                          f"共 {len(_results)} 个唯一结果")
        if _errors:
            _lines.append("⚠️ 部分请求失败: " + "；".join(_errors[:3]))
        if _skipped:
            _lines.append("⏭️ 低相关跳过（未命中查询词）: " + "；".join(_skipped[:3]))
        for _i, _r in enumerate(_results, 1):
            _lines.append(f"{_i}. {_r['title'] or '(无标题)'}")
            _lines.append(f"   URL: {_r['url']}  （{_r['engine']}，查询: {_r['query'][:40]}）")
            if _r.get("snippet"):
                _lines.append(f"   {_r['snippet'][:400]}")
        if _pages:
            if _action != "fetch" and _results:
                _lines.append("")
            _lines.append(f"📄 已抓取 {len(_pages)} 个页面正文：")
            for _pg in _pages:
                _lines.append(f"--- {_pg['url']}")
                if not _pg["ok"]:
                    _lines.append("❌ " + _pg["text"])
                elif _pg.get("mode") == "summary":
                    _lines.append("🤖 AI 摘要: " + _pg["text"])
                elif _pg.get("mode") == "compress":
                    _lines.append("🔑 关键行: " + _pg["text"])
                else:
                    _lines.append("✅ " + _pg["text"])
        if _action != "fetch" and not _results:
            _lines.append("(无结果；可换关键词、减少 allowed_domains 限制或换引擎)")
        return "\n".join(_lines)
    except Exception as _e:
        return f"❌ web_search 执行失败: {_e}"


# 计划系统已简化为纯引导模式（不再跟踪步骤状态）
_PLAN_MODE_ACTIVE = False  # 全局 plan 模式标记

# ── 任务管理系统全局注册表 ──
_TASK_STORAGE_DIR = os.path.join(os.path.expanduser("~"), ".ai_s", "tasks")
_TASK_REGISTRY = TaskRegistry(_TASK_STORAGE_DIR)
_TEAM_REGISTRY = TeamRegistry(_TASK_STORAGE_DIR)
_CRON_REGISTRY = CronRegistry(_TASK_STORAGE_DIR)

# ── 最后编辑记录（供 UndoLastEdit 使用）──
_LAST_EDIT: dict = {}  # {"path": str, "original": str, "action": "edit"|"write"}

# ── 恢复配方 & 审批令牌 ──
_RECOVERY_CTX = RecoveryContext()
_APPROVAL_LEDGER = ApprovalTokenLedger()

# 线程局部存储（使用 mcp_state 统一实例，保证 /tokens 等命令可读）
# _thread_locals 已在文件顶部从 mcp_state 导入，此处不再重新定义


# ── 大小感知规划门禁（正式任务必须先规划；小型修改直接放行）──
# 破坏性操作（删除/移动/复制/建目录）始终要求计划确认；
# write_file/edit_file 单次 > 4KB 或 本轮累计 ≥ 8KB 时要求计划确认。
_PLAN_GATE_DESTRUCTIVE = frozenset({
    "delete_file", "delete_directory", "move_file", "copy_file",
    "create_directory", "delete_files", "rename", "replace_in_file",
})
_PLAN_GATE_SINGLE_WRITE_BYTES = 4 * 1024
_PLAN_GATE_CUMULATIVE_BYTES = 8 * 1024


def plan_gate_blocked(tool_name: str, params: Dict, plan_confirmed: bool,
                      write_budget: int, mode: str = "normal") -> Tuple[bool, int]:
    """大小感知规划门禁判定（纯函数，可单测）。

    返回 (是否拦截, 更新后的累计写入字节)。
    拦截条件（未确认计划且非 plan 模式时）：
      - 破坏性工具 → 始终拦截；
      - write_file/edit_file → 单次 > 4KB 或 累计 ≥ 8KB 拦截。
    UndoLastEdit / RunCommand 豁免（撤销与危险命令已有自身确认机制）。
    """
    if plan_confirmed or mode == "plan":
        return False, write_budget
    if tool_name in _PLAN_GATE_DESTRUCTIVE:
        return True, write_budget
    if tool_name in ("write_file", "edit_file"):
        if tool_name == "write_file":
            _size = len(str(params.get("content") or ""))
        else:
            _size = len(str(params.get("new_string") or "")) + len(str(params.get("old_string") or ""))
        if _size > _PLAN_GATE_SINGLE_WRITE_BYTES:
            return True, write_budget
        write_budget += _size
        if write_budget >= _PLAN_GATE_CUMULATIVE_BYTES:
            return True, write_budget
    return False, write_budget


def _unescape_json_fragment(s: str) -> str:
    """单遍左到右反转义 JSON 字符串片段（破损 JSON 容错路径用）。

    正确处理字面量 \\n（JSON 中编码为 \\\\n）与真实换行 \\n 的区别；
    未知转义保留原样，\\uXXXX 按码点解码。
    """
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "n":
                out.append("\n"); i += 2; continue
            if nxt == "t":
                out.append("\t"); i += 2; continue
            if nxt == "r":
                out.append("\r"); i += 2; continue
            if nxt == '"':
                out.append('"'); i += 2; continue
            if nxt == "\\":
                out.append("\\"); i += 2; continue
            if nxt == "/":
                out.append("/"); i += 2; continue
            if nxt == "u" and i + 5 < n:
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def execute_mcp_tool(tool_name: str, params: Dict, name: str = "filesystem",
                     user_mode: str = "low", user_home_dir: str = None,
                     path_validator: Callable = None) -> Tuple[bool, str]:
    """
    执行工具调用。
    优先级：内置 handler → MCP 协议（外部 server）

    工具名规则：
      - 裸名（read_file）→ 先查内置 handler，再查 MCP server
      - mcp_xxx（mcp_puppeteer_navigate）→ 路由到 MCP server，调用 xxx
      - mcp__server__xxx（旧格式）→ 兼容旧版 MCP 调用
    """
    # ── 工具名解析 ──
    # mcp_xxx → MCP 工具，去掉 mcp_ 后路由到 server
    # 裸名 → 先查内置 handler，再查 MCP server（默认走 filesystem）
    raw_tool = tool_name
    mcp_server = name  # 保留调用者指定的 server 名
    # mcp_xxx（单下划线）→ 新版 MCP 前缀，去掉 mcp_ 后路由到 server
    if raw_tool.startswith("mcp_") and not raw_tool.startswith("mcp__"):
        raw_tool = raw_tool[4:]
        mcp_server = None
        try:
            _registry = get_registry()
            for _srv in _registry.server_names():
                if _registry.get(f"mcp__{_srv}__{raw_tool}"):
                    mcp_server = _srv
                    break
        except Exception:
            pass
    # mcp__server__xxx（旧格式）→ 取最后一段工具名
    if raw_tool.startswith("mcp__"):
        raw_tool = raw_tool.rsplit("__", 1)[-1]

    # ── AI 虚拟沙盒：文件类工具路径参数（虚拟 → 物理，越界拦截）──
    # 将 AI 视角的虚拟根 / 映射为用户 cwd；../ 逃逸等越界路径直接拒绝。
    if raw_tool in AI_FILE_TOOLS and sandbox.is_active():
        try:
            sandbox.resolve_many(params or {})
        except _SandboxBlockError as _sbe:
            return False, _sbe.message

    # ── 内置分析工具（不经过 MCP，直接 Python 执行）──
    # 用剥离后的 raw_tool 匹配
    _BUILTIN_HANDLERS = {
        # ── 文件操作 ──
        "validate_edit": lambda p: _exec_validate_edit(p.get("file_path", ""), p.get("search", ""), p.get("replace", "")),
        "preview_edit": lambda p: _exec_preview_edit(p.get("file_path", ""), p.get("search", ""), p.get("replace", "")),
        "get_file_info": lambda p: _exec_get_file_info(p.get("path", "")),
        "read_file":    lambda p: _exec_read_file(p.get("path", ""), p.get("range", None), p.get("head", None), p.get("tail", None)),
        "write_file":   lambda p: _exec_write_file(p.get("path", ""), p.get("content", "")),
        "edit_file":    lambda p: _exec_edit_file(p.get("path", ""), p.get("old_string", ""), p.get("new_string", "")),
        "glob_search":  lambda p: _exec_glob_search(p.get("pattern", ""), p.get("path", None)),
        "grep_search":  lambda p: _exec_grep_search(p.get("pattern", ""), p.get("path", None), p.get("glob", None),
                                                     p.get("context", 0), p.get("-i", False), p.get("head_limit", None)),
        "search_file":  lambda p: _exec_search_file(p.get("pattern", ""), p.get("path", None)),
        # ── 搜索与发现 ──
        "ToolSearch":   lambda p: _exec_tool_search(p.get("query", "")),
        "EnvProbe":     lambda p: _exec_env_probe(p.get("type", ""), p.get("which", "")),
        "Skill":        lambda p: _exec_skill(p.get("skill", ""), p.get("args", "")),
        # ── 计划与任务 ──
        "submit_plan":   lambda p: json.dumps({"plan": p.get("plan", ""), "steps": p.get("steps", [])}, ensure_ascii=False),
        "mark_step_complete": lambda p: p.get("step_id", ""),
        "TodoWrite":    lambda p: _exec_todo_write(p.get("todos", [])),
        "EnterPlanMode": lambda p: _exec_enter_plan_mode(),
        "ExitPlanMode":  lambda p: _exec_exit_plan_mode(),
        # ── 用户选择提问 ──
        "choose_ask":    lambda p: _exec_choose_ask(p.get("question", ""), p.get("options", [])),
        # ── Library 记忆管理 ──
        "remember":     lambda p: _exec_remember_session(p.get("session_id", "")),
        "forget":       lambda p: _exec_forget_session(p.get("session_id", "")),
        "memory":       lambda p: (
            _exec_search_library(p.get("query", ""), p.get("limit", 8))
            if p.get("operation", "search") == "search"
            else _exec_read_memory(p.get("session_id", ""))
            if p.get("operation") == "read"
            else _exec_list_timeline(
                p.get("day", ""), p.get("month", ""), p.get("year", ""),
                p.get("start", ""), p.get("end", ""), p.get("skill", ""))
            if p.get("operation") == "list" and (
                p.get("day") or p.get("month") or p.get("year")
                or p.get("start") or p.get("end"))
            else _exec_list_hippocampus(p.get("filter"), p.get("limit", 30))
        ),
        "compact_stats": lambda p: _exec_compact_stats(),
        # ── 配置 ──
        "Config":       lambda p: _exec_config(p.get("action", "get"), p.get("key", ""), p.get("value", None)),
        # ── 子代理与输出 ──
        "Agent":        lambda p: _exec_agent(p.get("description", ""), p.get("prompt", ""), p.get("name", ""),
                                              p.get("mode", "sync"), p.get("model", ""),
                                              p.get("count", 1), p.get("tasks", None),
                                              p.get("type", "explore")),
        "StructuredOutput": lambda p: _exec_structured_output(p.get("format", "json"), p.get("data", "")),
        "Sleep":        lambda p: _exec_sleep(int(p.get("seconds", 1))),
        # ── Web ──
        "web_search":   lambda p: _exec_web_search_multi(p),
        # ── 任务系统 ──
        "TaskCreate":   lambda p: _exec_task_create(
            p.get("prompt", ""), p.get("description"),
            p.get("scope"), p.get("scope_path"),
            p.get("acceptance_criteria"), p.get("acceptance_tests"),
            p.get("verification_plan"), p.get("resources"),
            p.get("model"), p.get("provider"),
            p.get("commit_policy"), p.get("branch_policy"),
            p.get("reporting_contract"), p.get("escalation_policy"),
            p.get("recovery_policy")),
        "TaskList":     lambda p: _exec_task_list(p.get("status_filter")),
        "TaskGet":      lambda p: _exec_task_get(p.get("task_id", "")),
        "TaskUpdate":   lambda p: _exec_task_update(
            p.get("task_id", ""), p.get("status"), p.get("message")),
        "TaskStop":     lambda p: _exec_task_stop(p.get("task_id", "")),
        "TaskBoard":    lambda p: _exec_task_board(),
        "TaskRemove":   lambda p: _exec_task_remove(p.get("task_id", "")),
        "TeamCreate":   lambda p: _exec_team_create(p.get("name", ""), p.get("task_ids")),
        "TeamList":     lambda p: _exec_team_list(),
        "TeamDelete":   lambda p: _exec_team_delete(p.get("team_id", "")),
        "CronCreate":   lambda p: _exec_cron_create(
            p.get("schedule", ""), p.get("prompt", ""), p.get("description")),
        "CronList":     lambda p: _exec_cron_list(p.get("enabled_only", False)),
        "CronDisable":  lambda p: _exec_cron_disable(p.get("cron_id", "")),
        "CronDelete":   lambda p: _exec_cron_delete(p.get("cron_id", "")),

        # ── 代码分析工具（bin/ai_lib/tools/code_analysis.py） ──
        "py_diagnostics": lambda p: code_analysis.exec_py_diagnostics(p.get("path", "")),
        "py_symbols":     lambda p: code_analysis.exec_py_symbols(p.get("path", "")),
        "LspDiagnostics": lambda p: code_analysis.exec_lsp_diagnostics(p.get("path", "")),
        "LspSymbols":     lambda p: code_analysis.exec_lsp_symbols(p.get("path", "")),
        "LspHover":       lambda p: code_analysis.exec_lsp_hover(
            p.get("path", ""), p.get("line", 0), p.get("character", 0)),
        "LspDefinition":  lambda p: code_analysis.exec_lsp_definition(
            p.get("path", ""), p.get("line", 0), p.get("character", 0)),
        "LspReferences":  lambda p: code_analysis.exec_lsp_references(
            p.get("path", ""), p.get("line", 0), p.get("character", 0)),
        "LspCompletion":  lambda p: code_analysis.exec_lsp_completion(
            p.get("path", ""), p.get("line", 0), p.get("character", 0)),
        "LspFormat":      lambda p: code_analysis.exec_lsp_format(p.get("path", "")),

        # ── 记忆查询 ──
        "MemoryRead":     lambda p: _exec_memory_read(p.get("path", ""), p.get("range")),
        "MemorySearch":   lambda p: _exec_memory_search(
            p.get("pattern", ""), p.get("uuid", "all"), int(p.get("context", 3)),
            p.get("-i", True)),
        "UndoLastEdit":   lambda p: _exec_undo_last_edit(),

        # ── 目录浏览工具 ──
        "ListDirectory": lambda p: _exec_list_directory(p.get("path", "")),
        "DirectoryTree":  lambda p: _exec_directory_tree(p.get("path", ""), int(p.get("maxDepth", 2))),

        # ── Git 工具 ──
        "GitStatus":  lambda p: _exec_git_status(p.get("path", "")),
        "GitDiff":    lambda p: _exec_git_diff(p.get("path", ""), p.get("staged", False)),
        "GitLog":     lambda p: _exec_git_log(p.get("path", ""), int(p.get("count", 10))),
        "GitBranch":  lambda p: _exec_git_branch(p.get("path", "")),
        # ── Shell 命令执行（function calling）──
        "RunCommand": lambda p: _exec_run_command(p.get("command", "")),
    }

    # ── write_file 容错：如果参数被 _parse_tool_params 回退成 range_str，尝试从原始 JSON 中抠出 path 和 content ──
    # （位于统一门禁之前：write_file 是内置工具，容错必须在分发前修复参数）
    if raw_tool == "write_file" and "content" not in params and "range_str" in params:
        _raw = str(params.get("range_str", ""))
        if _raw.startswith("{"):
            import re as _re
            # 尝试从破损 JSON 中提取 path
            _pm = _re.search(r'"path"\s*:\s*"([^"]*)"', _raw)
            if _pm:
                params["path"] = _pm.group(1)
            # 提取 content：从 "content": " 到文件末尾（JSON 可能被截断，取到最后一个 "） )
            _cm = _re.search(r'"content"\s*:\s*"(.+)', _raw, _re.DOTALL)
            if _cm:
                _raw_content = _cm.group(1)
                # 去掉末尾可能多出的 `"}` 残留
                _raw_content = _raw_content.rstrip('"').rstrip('}').rstrip('"').rstrip('}')
                # 反转义（单遍左到右，正确处理字面量 \\n）
                _raw_content = _unescape_json_fragment(_raw_content)
                params["content"] = _raw_content
                params.pop("range_str", None)
                if _pm and not _raw_content.endswith("\n"):
                    params["content"] += "\n"
                _mcp_debug(f"write_file 容错: path={params.get('path', '?')}, content_len={len(params.get('content', ''))}")

    # ── 统一权限门控（前置：内置与 MCP 工具一视同仁，杜绝内置 handler 绕过）──
    # 从 build_native_tools() 查找当前工具的权限级别
    # 2026-09 修复：MCP 工具在工具表中注册为 mcp_<name>（见 build_native_tools），
    # 而 raw_tool 已剥离 mcp_ 前缀——此前用裸名查表永远 miss → 全部落 PERM_READONLY，
    # DangerFullAccess 形同虚设。现同时匹配裸名与 mcp_ 前缀名。
    _tool_permission = PERM_READONLY  # 默认只读安全
    try:
        _tool_permission = _get_tool_permission(raw_tool)
        if _tool_permission == PERM_READONLY:
            # MCP 工具注册为 mcp_<name>，裸名 miss 时再查前缀名
            _tool_permission = _get_tool_permission(f"mcp_{raw_tool}")
    except Exception:
        pass

    # ── Agent 工具分级：explore/plan 完全只读 → 自动放行（等同 ReadOnly）──
    # lint/test/web_search_agent 可经安全管线跑命令/联网 → 保持 DangerFullAccess（显式批准）
    if raw_tool == "Agent":
        _agent_type = str((params or {}).get("type", "explore")).lower()
        if _agent_type in ("explore", "plan"):
            _tool_permission = PERM_READONLY

    if _tool_permission == PERM_DANGER_FULL and user_mode != "adv":
        # DangerFullAccess：显式用户批准 + 审批令牌。
        # 跟随用户当前模式：low/mid 需确认，adv 自动放行（user_mode 由调用方
        # 传 handle_ai 的 _current_user_mode = user_mode.current_mode 实时值）。
        _lang = get_current_lang()
        _prompt = (_lang == "chinese" and "🔴 工具 '{tool}' 需要危险权限，确认执行？(y/N): " or
                   "🔴 Tool '{tool}' requires dangerous access, confirm? (y/N): ").format(tool=raw_tool)
        # 确认框走真实终端（防 stdout 被捕获流替换导致提示不可见）
        from .ai_lib.ui import real_terminal_io as _rtio
        with _rtio():
            try:
                _confirm = input(f"  {_prompt}").strip().lower()
            except (KeyboardInterrupt, EOFError):
                console.print()
                return False, _mcp_t("⛔ 用户取消了危险操作", "⛔ Dangerous operation cancelled by user")
        if _confirm not in ("y", "yes"):
            return False, _mcp_t("⛔ 用户拒绝了危险操作", "⛔ Dangerous operation refused by user")
        # 创建审批令牌
        _scope = ApprovalScope(action=raw_tool, policy="dangerous_write")
        _token_grant = _APPROVAL_LEDGER.create(
            scope=_scope, approving_actor="user",
            approved_executor="ai", max_uses=1, ttl_seconds=60,
        )
        console.print(_mcp_t(f"  [dim]✓ 已授权（令牌: {_token_grant.token[:12]}...）[/]",
                            f"  [dim]✓ Authorized (token: {_token_grant.token[:12]}...)[/]"))
    # WorkspaceWrite / ReadOnly：全模式自动放行
    # （可逆操作：UndoLastEdit + git + 终端实时可见性提供安全；确认弹窗留给不可逆动作）

    # ---- 路径安全校验（所有工具执行前必须经过 Onyx 沙箱检查） ----
    if path_validator is not None:
        arguments = dict(params) if params else {}
        file_tool_paths = _extract_paths_from_tool(raw_tool, arguments)
        for p in file_tool_paths:
            ok, err_msg = path_validator(raw_tool, p)
            if not ok:
                return False, err_msg

    # ── 内置分析工具（不经过 MCP，直接 Python 执行；已通过上述统一门禁）──
    if raw_tool in _BUILTIN_HANDLERS:
        try:
            result = _BUILTIN_HANDLERS[raw_tool](params or {})
            # AI 虚拟沙盒：文件类工具输出中的物理路径 → 虚拟路径（隐藏真实 cwd）
            if raw_tool in AI_FILE_TOOLS and sandbox.is_active():
                result = sandbox.display_text(result)
            return True, result
        except Exception as e:
            return False, f"Builtin tool error: {e}"

    # ── 未知名兜底路由闸门（2026-09 修复 C3）──
    # 裸名既不在内置 handler、也不在注册表 MCP 工具、又不在只读白名单时，
    # 拒绝直达 filesystem——防模型幻觉输出 delete_file 等被剔除的破坏性名字
    # 直接落 MCP 执行（此前无任何确认）。
    if raw_tool not in _BUILTIN_HANDLERS:
        _known_mcp_tool = False
        try:
            _reg = get_registry()
            for _srv in _reg.server_names():
                if _reg.get(f"mcp__{_srv}__{raw_tool}"):
                    _known_mcp_tool = True
                    break
        except Exception:
            pass
        if not _known_mcp_tool and raw_tool not in _MCP_BARE_READONLY_TOOLS:
            return False, (
                f"未知工具 `{tool_name}`：工具表中不存在该名称，已拒绝路由到 MCP。"
                f"破坏性文件操作请使用工具列表中注册的 mcp_ 前缀工具名。"
            )

    proc = connect_mcp_server(name, user_home_dir)
    if proc is None:
        return False, f"MCP server '{name}' not connected"

    # 构建 MCP call_tool arguments
    arguments = dict(params) if params else {}

    # edit_file: old_string/new_string → MCP edits[].oldText/.newText
    if raw_tool == "edit_file":
        old_str = arguments.pop("old_string", None) or arguments.pop("old_str", None)
        new_str = arguments.pop("new_string", None) or arguments.pop("new_str", None)
        if old_str is not None:
            arguments["edits"] = [{"oldText": old_str, "newText": new_str or ""}]
        # 移除旧的 range_str/operation（兼容旧格式）
        arguments.pop("range_str", None)
        arguments.pop("operation", None)

    call_params = {
        "name": raw_tool,
        "arguments": arguments,
    }

    result = _mcp_request(proc, "tools/call", call_params, msg_id=int(time.time() * 1000) % 1000000)

    if result is None:
        return False, "MCP tool call timeout"

    if "error" in result:
        return False, f"MCP error: {result['error']}"

    # 提取 content
    content = result.get("result", {}).get("content", [])
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                text_parts.append(item)
        output = "\n".join(text_parts)
    elif isinstance(content, str):
        output = content
    else:
        output = str(content)

    # AI 虚拟沙盒：文件类 MCP 工具输出中的物理路径 → 虚拟路径（隐藏真实 cwd）
    if raw_tool in AI_FILE_TOOLS and sandbox.is_active():
        output = sandbox.display_text(output)

    return True, output


def _extract_paths_from_tool(tool_name: str, arguments: Dict) -> List[str]:
    """从 MCP 工具参数中提取所有文件路径，用于安全校验"""
    paths = []
    # 常见的路径参数名
    path_keys = {"path", "paths", "source", "destination", "file_path",
                 "directory", "dir_path", "target", "file", "dir"}

    for key in path_keys:
        val = arguments.get(key)
        if isinstance(val, str) and val:
            paths.append(val)

    # edit_file 特殊处理：edits 中可能含路径引用
    if tool_name == "edit_file":
        edits = arguments.get("edits", [])
        if isinstance(edits, list):
            for edit in edits:
                if isinstance(edit, dict):
                    for k in path_keys:
                        v = edit.get(k)
                        if isinstance(v, str) and v:
                            paths.append(v)

    return paths


# ── AI 虚拟沙盒：涉及 path 参数的文件类工具白名单 ──
# 这些工具的路径参数在执行前会经 sandbox.resolve() 做虚拟→物理转换（越界拦截），
# 输出中的物理路径再由各 _exec_* 经 sandbox.display() 反向映射回虚拟路径。
AI_FILE_TOOLS = frozenset({
    # ── 内置文件工具 ──
    "validate_edit", "preview_edit", "get_file_info", "read_file",
    "write_file", "edit_file", "glob_search", "grep_search",
    "search_file", "ListDirectory", "DirectoryTree",
    # ── 代码分析工具（bin/ai_lib/tools/code_analysis.py）──
    "py_diagnostics", "py_symbols", "LspDiagnostics", "LspSymbols",
    "LspHover", "LspDefinition", "LspReferences", "LspCompletion", "LspFormat",
    # ── Git 工具 ──
    "GitStatus", "GitDiff", "GitLog", "GitBranch",
    # ── MCP filesystem 工具（走外部 server，参数同样先转物理）──
    "read_text_file", "read_multiple_files", "read_media_file",
    "list_directory", "directory_tree", "list_directory_with_sizes",
    "search_files", "search_content", "create_directory",
    "move_file", "copy_file", "delete_file", "delete_directory",
    "list_allowed_directories", "get_workspace_folders",
})

# ── 裸名直达 MCP filesystem 的只读白名单（2026-09 修复 C3）──
# 破坏性 filesystem 工具（delete_file/move_file/copy_file/create_directory 等）
# 已从可见工具表剔除，且不允许以裸名直达——模型幻觉输出这些名字时直接拒绝，
# 必须走 mcp_ 前缀（挂有权限门禁 + 沙盒校验）才能调用。
_MCP_BARE_READONLY_TOOLS = frozenset({
    "read_text_file", "read_multiple_files", "read_media_file",
    "list_directory", "directory_tree", "list_directory_with_sizes",
    "search_files", "search_content",
    "list_allowed_directories", "get_workspace_folders",
})


def _parse_mcp_tool_name(full_name: str) -> tuple:
    """解析 mcp__server__tool → (server, tool_name)"""
    if full_name.startswith("mcp__"):
        parts = full_name.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
    return "filesystem", full_name


def _display_tool_params(params_str: str) -> str:
    """从工具调用原始参数中提取关键路径/参数用于面板展示（路径绝不截断）。

    优先展示 path/pattern/uuid 等关键字段的完整值；无法解析时回退为原始参数
    （限制长度并显式省略，避免把 write_file 的完整 content 刷进面板）。
    """
    try:
        if params_str.strip().startswith("{"):
            data = json.loads(params_str)
            for key in ("path", "pattern", "uuid", "task_id", "cron_id",
                        "team_id", "query", "url", "name"):
                val = data.get(key)
                if val:
                    return f"{key}={val}"
    except Exception:
        pass
    if len(params_str) <= 200:
        return params_str
    return params_str[:200] + "…"


def _parse_tool_params(params_str: str, body: str) -> Dict:
    """
    解析工具参数：JSON 优先，回退到旧空格分隔格式。
    - 新格式: params_str 是 JSON，直接解析
    - 兼容: body 是 JSON（放在块体中）
    - 旧格式: "path 10-30" 空格分隔
    """
    # 尝试 JSON
    for candidate in (params_str, body):
        if candidate and candidate.strip().startswith("{"):
            try:
                return json.loads(candidate.strip())
            except (json.JSONDecodeError, ValueError) as _je:
                _mcp_debug(f"_parse_tool_params JSON decode failed: {_je}")
                pass

    # 回退：旧空格分隔格式 "path [operation] [range]"
    params = {"range_str": params_str, "content": body}
    if params_str and not params_str.startswith("{"):
        parts = params_str.split(None, 1)
        params["path"] = parts[0]
        if len(parts) > 1:
            rest = parts[1]
            if rest in ("replace", "insert", "delete", "append"):
                params["operation"] = rest
            else:
                params["range_str"] = rest
    return params


def list_mcp_servers() -> str:
    """列出已注册的 MCP 服务器及状态"""
    config = _load_mcp_config()
    servers = config.get("servers", {})
    if not servers:
        return _mcp_t("没有已注册的 MCP 服务器", "No MCP servers registered")

    lines = ["📋 MCP 服务器列表:", ""]
    for sname, sinfo in servers.items():
        installed = "✅" if sinfo.get("installed") else "❌"
        running = "🟢" if is_mcp_server_running(sname) else "⚫"
        desc = sinfo.get("description", "")
        lines.append(f"  {running} {installed} {sname}: {desc}")
    return "\n".join(lines)


def install_mcp_server_cmd(name: str, package: str = None) -> str:
    """
    安装并注册一个 MCP 服务器
    ai -mcp install <name> [package]
    默认 package = @modelcontextprotocol/server-<name>
    """
    if package is None:
        package = f"@modelcontextprotocol/server-{name}"

    console.print(_mcp_t(f"📦 正在安装 {package}...", f"📦 Installing {package}..."), style="cyan")

    # 构建 env（Termux 上需重定向到内部存储，避免 FUSE symlink 错误）
    env = os.environ.copy()
    try:
        from lib.get_lib_path import _is_termux_environment
        if _is_termux_environment():
            from lib.get_lib_path import TERMUX_PREFIX, TERMUX_HOME
            termux_cache = os.path.join(TERMUX_PREFIX, "tmp", "npm_cache")

            # 彻底删除整个 npm cache（包括 _cacache 和 _npx）
            if os.path.exists(termux_cache):
                try:
                    shutil.rmtree(termux_cache)
                except Exception:
                    pass
            _ensure_dir(termux_cache)

            env["NPM_CONFIG_CACHE"] = termux_cache
            env["npm_config_cache"] = termux_cache
            env["HOME"] = TERMUX_HOME
            console.print(f"📱 Termux: npm cache → {termux_cache}", style="dim")
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["npm", "install", "-g", package],
            capture_output=True, text=True, timeout=120,
            env=env
        )
        if result.returncode != 0:
            return _mcp_t(f"❌ 安装失败: {result.stderr[:300]}", f"❌ Install failed: {result.stderr[:300]}")
    except FileNotFoundError:
        return _mcp_t("❌ npm 未找到，请先安装 Node.js", "❌ npm not found, please install Node.js")
    except subprocess.TimeoutExpired:
        return _mcp_t("❌ 安装超时（120s）", "❌ Install timed out (120s)")

    # 注册到配置文件
    config = _load_mcp_config()
    config.setdefault("servers", {})[name] = {
        "name": name,
        "description": f"MCP server: {package}",
        "command": "npx",
        "args": ["-y", package, "/"],
        "auto_start": False,
        "installed": True,
    }
    _save_mcp_config(config)

    return f"✅ MCP server '{name}' 安装并注册成功\n   包: {package}\n   使用 ai -mcp list 查看状态"


def remove_mcp_server_cmd(name: str) -> str:
    """从注册表移除 MCP 服务器"""
    if name == "filesystem":
        return "❌ 默认 filesystem MCP server 不可移除"

    # 先关闭进程
    if name in MCP_SERVER_PROCESSES:
        proc = MCP_SERVER_PROCESSES.pop(name)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    MCP_TOOLS_CACHE.pop(name, None)

    config = _load_mcp_config()
    if name in config.get("servers", {}):
        del config["servers"][name]
        _save_mcp_config(config)
        return f"✅ MCP server '{name}' 已移除"
    return f"⚠️ MCP server '{name}' 未在注册表中"


def handle_mcp_command(subcommand: str, args: List[str]) -> None:
    """
    处理 ai -mcp <subcommand> 子命令
    在 handle_ai 入口处调用
    """
    if subcommand == "list":
        result = list_mcp_servers()
        console.print(result, style="white")
    elif subcommand == "install":
        mcp_name = args[0] if args else None
        mcp_pkg = args[1] if len(args) > 1 else None
        if not mcp_name:
            console.print(_mcp_t("用法: ai -mcp install <name> [package]", "Usage: ai -mcp install <name> [package]"), style="bold yellow")
            return
        result = install_mcp_server_cmd(mcp_name, mcp_pkg)
        console.print(result, style="white")
    elif subcommand == "remove":
        mcp_name = args[0] if args else None
        if not mcp_name:
            console.print(_mcp_t("用法: ai -mcp remove <name>", "Usage: ai -mcp remove <name>"), style="bold yellow")
            return
        result = remove_mcp_server_cmd(mcp_name)
        console.print(result, style="white")
    else:
        console.print(
            "用法: ai -mcp <install|list|remove> [args]",
            style="bold yellow"
        )


# ── Shell 命令快速执行器（用于项目上下文采集）──
def _run_shell_cmd(cmd: str, timeout: int = 10) -> str:
    """执行 shell 命令并返回 stdout 文本。静默失败返回空字符串。"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True,
                                text=True, timeout=timeout)
        return result.stdout.strip()
    except Exception:
        return ""


# ========================================================================

# -------------------------- 11. handle_ai 核心函数（SSE模式）-------------------------

# ── 对话压缩管道（/compact 与自动压缩共用）──
# 自动压缩阈值：估算 token 数（含 reasoning_content），超过即触发。
# 压缩会重置缓存前缀（一次性 miss），换来后续注意力集中与更长的有效记忆窗口。
_AUTO_COMPACT_TOKEN_THRESHOLD = 600 * 1024

# 工具 schema 的固定 token 开销：校准 tokPerChar 时从真实 prompt tokens 中扣除。
# 回退值 22000（约 55 个内置工具 + 描述）；首次使用时按实际工具 JSON 字节实测。
_TOOL_SCHEMA_TOKEN_OVERHEAD = 22_000
_TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE: Optional[int] = None


def _measured_tool_schema_overhead() -> int:
    """实测当前工具集的 schema 字节开销（估算 token），失败回退 22000。"""
    global _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE
    if _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE is not None:
        return _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE
    try:
        _tools, _ = get_native_tools_cached(USER_HOME_DIR, True)
        _bytes = len(json.dumps(_tools, ensure_ascii=False).encode("utf-8"))
        _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE = max(int(_bytes / 3.2), 5000)
    except Exception:
        _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE = _TOOL_SCHEMA_TOKEN_OVERHEAD
    return _TOOL_SCHEMA_TOKEN_OVERHEAD_CACHE

# ── 分层压缩状态 ──
# Layer 2 / TimeBased：闲置超过 60 分钟无交互 → 清理已被 AI 消费的旧工具结果
_IDLE_COMPACT_SECONDS = 60 * 60
_last_ai_interaction_ts = time.time()
# Layer 3 熔断器：连续压缩后仍 ≥90% 阈值达 3 次 → 本会话停止自动压缩，避免反复烧 token
_COMPACT_BREAKER_COUNTS: Dict[str, int] = {}
_COMPACT_BREAKER_DISABLED: Dict[str, bool] = {}

# ── 窗口感知阈值（trigger = 窗口 − 13K 安全缓冲；400 报错实测值可覆盖）──
_WINDOW_SAFETY_BUFFER = 13_000
_SESSION_CONTEXT_WINDOWS: Dict[str, int] = {}


def _persist_compact_to_library(summary: str, saved: int, superseded: int,
                                old_len: int, trident_stats: dict,
                                user_home_dir: str = None, session_id: str = "") -> None:
    """把会话压缩摘要追加到当前 session 的 library 记录（便于人工核对压缩是否失真）。

    2026-09 用户需求：AutoCompact / /compact / 400 超限重试三条路径共用本函数，
    压缩一发生就把摘要 + 统计写进 ~/.ai_s/library/<session_id>.txt。

    防御：session_id 必须为单段文件名（防路径穿越）；任何失败静默，不影响主流程。
    """
    if not summary or not user_home_dir or not session_id:
        return
    if "/" in session_id or "\\" in session_id or session_id in (".", ".."):
        return
    try:
        import datetime as _dt
        from .ai_lib.storage import get_ai_session_library_dir
        from .ai_lib.memory_compact import format_compact_summary
        lib_dir = get_ai_session_library_dir(user_home_dir)
        fpath = os.path.join(lib_dir, f"{session_id}.txt")
        _formatted = format_compact_summary(summary)
        _stats = trident_stats or {}
        _block = (
            f"## 🔄 对话压缩 — {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"- 压缩范围: {old_len} 条旧消息 → 摘要 + 最近原文（约省 {saved} 条）\n"
            f"- Superseded: {superseded}；Trident: {_stats}\n\n"
            f"{_formatted}\n"
        )
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n\n{_block}")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def _compact_conversation_history(conversation_history: List[Dict], keep_last: int = 8,
                                  user_home_dir: str = None, session_id: str = ""):
    """新一代压缩：轮级分区（用户原话/错误轮/旧摘要保留）+ LLM 保真摘要（失败回退正则）。

    管道：
      1. 边界保护：不切断 tool_calls → tool_result 配对
      2. 轮级分区 partition_rounds_keep_fold：keep 原样保留，fold 进摘要
      3. Stage1 Supersede：同文件先 VIEW 后 EDIT/WRITE → 去重过时 VIEW
      4. LLM 分块并行摘要（七段式简报，用户原话不折叠）；失败逐块回退正则
      5. 组装：摘要 system 消息 + kept 原话 + 最近原文

    Returns:
        (new_history, saved_count, superseded_count, old_len, trident_stats)
        new_history 为 None 时表示无可安全压缩的旧消息。
    """
    from .ai_lib.memory_compact import (
        summarize_messages, stage1_supersede, get_compact_continuation_message,
        partition_rounds_keep_fold, llm_summarize_messages,
        run_trident_stages, merge_compact_summaries,
        extract_summary_from_compact_message, compress_summary,
    )
    _total = len(conversation_history)

    # ── Guard: 不切断 tool_calls → tool_result 配对 ──
    # 扫描 ALL tool_calls 块，累积最小安全边界。
    # 任何 tool 结果跨越压缩边界的 tool_calls 块整体保留。
    _min_recent_idx = _total - keep_last
    for _j in range(_total - 1, -1, -1):
        _m = conversation_history[_j]
        if _m.get("tool_calls"):
            _tool_end = _j + 1
            while _tool_end < _total and conversation_history[_tool_end].get("role") == "tool":
                _tool_end += 1
            # 如果此块的 tool 结果触及 _recent，则整块纳入 _recent
            if _tool_end > _min_recent_idx:
                _min_recent_idx = min(_min_recent_idx, _j)
    keep_last = max(keep_last, _total - _min_recent_idx)
    keep_last = min(keep_last, _total - 1)  # 至少保留 1 条在 _old

    _old = conversation_history[:-keep_last] if keep_last < _total else []
    _recent = conversation_history[-keep_last:] if keep_last > 0 else []

    if not _old:
        return None, 0, 0, 0, {}

    # ── 轮级分区：keep（用户原话/错误轮/系统消息/旧摘要）原样保留；
    #    fold（assistant 文本轮/无错误工具轮）进摘要 ──
    _kept, _fold = partition_rounds_keep_fold(_old)

    if not _fold:
        # 无可折叠内容 → 全部原样保留，不压缩
        return None, 0, 0, len(_old), {}

    # ── 提取 kept 中的旧压缩摘要并合并（merge 防嵌套膨胀，单摘要出口）──
    _existing_summaries = []
    _kept_wo_compact = []
    for _m in _kept:
        if _m.get("role") == "system":
            _old_sum = extract_summary_from_compact_message(_m.get("content", ""))
            if _old_sum:
                _existing_summaries.append(_old_sum)
                continue
        _kept_wo_compact.append(_m)
    _kept = _kept_wo_compact

    # ── fold 部分 → entries（Trident 预缩减 + LLM 摘要的输入）──
    _old_entries = []
    for _i, _m in enumerate(_fold):
        _role = _m.get("role", "?")
        _content = _m.get("content", "") or ""
        if not isinstance(_content, str):
            try:
                _content = json.dumps(_content, ensure_ascii=False)
            except Exception:
                _content = str(_content)
        _tc = _m.get("tool_calls")
        _rc = _m.get("reasoning_content", "")
        _body = _content
        if _tc:
            _tc_names = [t.get("function", {}).get("name", "?") for t in _tc]
            try:
                _args = " | ".join(
                    (t.get("function", {}).get("arguments", "") or "")[:300] for t in _tc)
            except Exception:
                _args = ""
            _body = f"[tool_calls: {', '.join(_tc_names)}]{(' ' + _args) if _args else ''}\n{_content}"
        if _rc:
            _body = f"[reasoning]\n{_rc}\n\n{_body}"
        _old_entries.append({
            "session_id": f"turn_{_i}",
            "content": f"### {_role.upper()}\n{_body}",
            "time": "",
        })

    # ── 三段式预缩减：supersede → collapse → cluster ──
    _deduped, _trident_stats = run_trident_stages(_old_entries)
    _superseded = _trident_stats.get("superseded", 0)
    # ── LLM 保真摘要（分块并行；失败自动回退正则）──
    _summary, _used_llm, _chunk_count = llm_summarize_messages(
        _deduped if _deduped else _old_entries,
        user_home_dir=user_home_dir,
        session_id=session_id,
    )
    if not _summary:
        _summary = summarize_messages(_deduped if _deduped else _old_entries)
    if not _summary:
        return None, 0, 0, len(_old), {}

    # ── 合并旧摘要（merge_compact_summaries：展平 prior，追加新内容）──
    for _old_sum in _existing_summaries:
        _summary = merge_compact_summaries(_old_sum, _summary)

    _compact_msg = {
        "role": "system",
        "content": get_compact_continuation_message(_summary),
    }
    _saved = max(0, len(_old) - len(_kept) - 1)
    # ── 压缩摘要持久化到 library（2026-09：便于人工核对压缩是否失真）──
    try:
        _persist_compact_to_library(
            _summary, _saved, _superseded, len(_old), _trident_stats,
            user_home_dir, session_id,
        )
    except Exception:
        pass
    return [_compact_msg] + _kept + _recent, _saved, _superseded, len(_old), _trident_stats


def _is_context_too_long_error(error_str: str) -> bool:
    """检测上下文超限类 API 报错（DeepSeek/OpenAI/Anthropic 常见签名）。"""
    _s = (error_str or "").lower()
    _sigs = (
        "context_length_exceeded",
        "maximum context length",
        "context length exceeded",
        "prompt is too long",
        "too many tokens",
        "context is too long",
        "max context",
    )
    return any(sig in _s for sig in _sigs)


def _parse_context_window_from_error(error_str: str) -> Optional[int]:
    """从 400 报错里解析实测上下文窗口（服务器返回窗口 → 重设触发阈值）。"""
    try:
        _m = re.search(r"maximum context length is (\d+)", error_str, re.IGNORECASE)
        if _m:
            return int(_m.group(1))
        _m = re.search(r"(\d+) tokens[^>]*>?\s*(\d+)\s*maximum", error_str, re.IGNORECASE)
        if _m:
            return max(int(_m.group(1)), int(_m.group(2)))
        _m = re.search(r"(\d+)\s*tokens(?:[^)]{0,40})", error_str, re.IGNORECASE)
        if _m and "context" in error_str.lower():
            return int(_m.group(1))
    except Exception:
        pass
    return None


def _platform_context_window() -> int:
    """按平台取默认上下文窗口（可被 400 报错实测值覆盖）。"""
    try:
        from .ai_lib.config import load_key_conf
        _conf = load_key_conf() or {}
        _plat = _conf.get("platform", "deepseek")
        _map = {"deepseek": 1_000_000, "anthropic": 200_000,
                "openai": 128_000, "custom": 128_000}
        return _map.get(_plat, 1_000_000)
    except Exception:
        return 1_000_000


def _effective_compact_threshold(session_id: str = "") -> int:
    """自动压缩生效阈值 = min(用户 600K, 实测/默认窗口 − 13K 安全缓冲)。"""
    _win = _SESSION_CONTEXT_WINDOWS.get(session_id) or _platform_context_window()
    _thr = min(_AUTO_COMPACT_TOKEN_THRESHOLD, _win - _WINDOW_SAFETY_BUFFER)
    return max(_thr, 32 * 1024)


def _should_append_reply_assistant(ai_txt: str, tool_calls: List) -> bool:
    """纯文本回复是否写入对话历史：正文为空（纯思考轮）不写。

    思考被截断（finish_reason=length）时模型可能只输出 reasoning_content——
    content=None 且无 tool_calls 的 assistant 消息回传会被 API 以
    400 "Invalid assistant message" 拒绝，导致会话卡死。
    """
    return bool(ai_txt and ai_txt.strip()) and not tool_calls


def _estimate_conversation_tokens(conversation_history: List[Dict], session_id: str = "") -> int:
    """估算整段对话历史（含 reasoning_content / tool_calls 参数）的 token 数。

    优先用上一轮真实 usage 校准 tokPerChar（扣除工具 schema 固定开销），
    无历史数据时回退 memory_compact 的 CJK 感知估算。

    session_id: 校准字符数按会话隔离读取。子代理（explore_*）/压缩摘要
                （compact_*）的请求在后台运行，若不隔离，主会话会拿它们的
                字符数做分母 → tokPerChar 虚高 → AutoCompact 提前触发（缓存断裂）。
    """
    from .ai_lib.memory_compact import estimate_tokens
    _total = 0
    _chars = 0
    for _m in conversation_history:
        _c = _m.get("content") or ""
        if not isinstance(_c, str):
            try:
                _c = json.dumps(_c, ensure_ascii=False)
            except Exception:
                _c = str(_c)
        _total += estimate_tokens(_c)
        _chars += len(_c)
        _rc = _m.get("reasoning_content") or ""
        if isinstance(_rc, str) and _rc:
            _total += estimate_tokens(_rc)
            _chars += len(_rc)
        for _tc in _m.get("tool_calls") or []:
            _args = _tc.get("function", {}).get("arguments", "") if isinstance(_tc, dict) else ""
            if isinstance(_args, str) and _args:
                _total += estimate_tokens(_args)
                _chars += len(_args)
    # ── 真实 usage 校准：tokPerChar = (上轮 prompt tokens − 工具 schema 开销) / 上轮字符数 ──
    try:
        _last_prompt = getattr(_thread_locals, "last_prompt_tokens", 0) or 0
        if _last_prompt > 0 and _chars > 0:
            from .ai_lib.api import get_last_request_chars as _glrc
            _last_chars = _glrc(session_id) or 0
            if _last_chars > 0:
                _ratio = (_last_prompt - _measured_tool_schema_overhead()) / _last_chars
                _ratio = min(max(_ratio, 0.10), 0.80)
                return max(int(_chars * _ratio), _total)
    except Exception:
        pass
    return _total


def _reset_ai_interrupt_flags() -> None:
    """复位 AI 中断标志（ai_cmd 与 mcp_state 双份）。

    Ctrl+C 在 SSE 阶段由 _interrupt_handler 置位的是 mcp_state 副本
    （api.py 流式循环检查它），而旧复位点只清 ai_cmd 模块变量——
    mcp_state 标志一旦置位永不复位 → 后续提问 API 一启动就中断。
    """
    global _AI_INTERRUPTED
    _AI_INTERRUPTED = False
    try:
        from .ai_lib import mcp_state as _msp
        _msp._AI_INTERRUPTED = False
    except Exception:
        pass


def handle_ai(
    cmd_parts: List[str],
    request_id: str,
    onyx_module=None,
    user_home_dir: str = None,
    global_config: Dict[str, Any] = None,
    user_info: Dict[str, Any] = None,
    user_mode=None,
    AI_TOOL_OUTPUT_CACHE: Dict[str, str] = None,
    BUILTIN_COMMANDS: Dict[str, Callable] = None,
    CMD_MAPPING_CACHE: Dict[str, Any] = None,
    current_sys_cmds: Dict[str, List[str]] = None,
    sys_type: str = None,
    get_cached_cmd: Callable = None,
    parse_and_execute: Callable = None,
    get_current_lang_func: Callable = None,
    log_info: Callable = None,
    log_error: Callable = None,
    log_warning: Callable = None,
    security_log: Callable = None,
    _in_repl: bool = False,
    conversation_history: List[Dict] = None,
    memory_base_dir: str = None,
    plus_think: str = None,
) -> None:
    from io import StringIO
    import sys as sys_module
    # api 模块（含 requests 重依赖）延迟到首次调用时导入——启动提速，行为不变
    from .ai_lib.api import (call_ai_api_sse, process_ai_result_fields,
                             extract_ai_commands, build_memory_context,
                             build_stable_prefix)
    from contextlib import contextmanager

    # ── --debug 必须在最开头解析，否则 MCP 初始化卡住时没有追踪输出 ──
    # 每次 handle_ai 调用先复位，避免上次 --debug 残留（_PERSIST_DEBUG 除外）
    global _MCP_DEBUG, _MCP_DEBUG_START, _PERSIST_DEBUG, _PLAN_MODE_ACTIVE
    _MCP_DEBUG = False
    _MCP_DEBUG_START = 0.0
    debug_mode = False
    if "--debug" in cmd_parts:
        debug_mode = True
        _MCP_DEBUG = True
        _MCP_DEBUG_START = time.time()
        # 不移除 --debug，留给 parse_arguments 处理（用于触发 interactive 模式）
        # 用 stderr 输出确保立即可见（stdout 可能被 Live Panel 等捕获）
        sys_module.stderr.write(f"[{time.time()-_MCP_DEBUG_START:06.2f}s] 🔍 DEBUG 模式已启用 — 实时追踪每个函数调用和耗时\n")
        sys_module.stderr.flush()
    elif _PERSIST_DEBUG:
        # ai --debug 进入 REPL 后，后续 handle_ai 调用自动继承 debug 模式
        debug_mode = True
        _MCP_DEBUG = True
        if _MCP_DEBUG_START == 0.0:
            _MCP_DEBUG_START = time.time()

    if user_home_dir is None:
        user_home_dir = USER_HOME_DIR
    # ── 记忆根目录：记忆模式（global=user_home_dir / project=<项目专属文件夹>）──
    # memory_base_dir 由 ai_interactive 传入；为空则跟随 user_home_dir（兼容旧调用）
    _mem_home = memory_base_dir or user_home_dir
    # ── 注入模块级记忆根：MemoryRead/MemorySearch 路径解析与最高指示读写跟随记忆模式 ──
    try:
        set_memory_home(_mem_home)
    except Exception:
        pass
    # ── Explore 子代理记忆根跟随主会话（cost.json 记录位置一致）──
    try:
        from .ai_lib import subagent as _subagent_hook
        _subagent_hook.set_mem_home(_mem_home)
    except Exception:
        pass
    # ── AI 虚拟沙盒：把虚拟根 / 映射为当前 cwd（AI 文件工具路径拦截 + 输出脱敏）──
    # 每次 AI 会话启动时初始化，工具执行前路径自动经 sandbox.resolve() 转换。
    try:
        sandbox.init(os.getcwd(), user_home_dir or USER_HOME_DIR)
    except Exception:
        pass
    if AI_TOOL_OUTPUT_CACHE is None:
        AI_TOOL_OUTPUT_CACHE = {}
    if global_config is None:
        global_config = {"display_info": {"language": {"current": "chinese"}}}
    if user_info is None:
        user_info = {"name": "default", "session_id": request_id}
    if get_current_lang_func is None:
        get_current_lang_func = get_current_lang
    
    current_lang = get_current_lang_func()
    lang_text = get_lang_text(current_lang)
    
    MAX_CACHE_SIZE = 10000
    MAX_SESSION_FILE_SIZE = 10 * 1024 * 1024
    
    # CMD之间等待时间（秒）
    CMD_WAIT_INTERVAL = 1.5
    
    init_ai_dangerous_commands(user_home_dir, log_info)
    dangerous_commands = load_ai_dangerous_commands(user_home_dir, log_info)
    # 特别高危清单（rm -rf / sudo rm / dd 等毁灭型命令）：任何上下文下都强制人工确认
    init_ai_extra_dangerous_commands(user_home_dir, log_info)
    extra_dangerous_commands = load_ai_extra_dangerous_commands(user_home_dir, log_info)
    
    # ── 子代理命令执行器：经与主 AI 相同的安全管线（capture + parse_and_execute）──
    # 危险命令直接拒绝（子代理无法弹用户确认框）；全端子代理命令串行化防共享 PTY 输出交错。
    # 内置命令一律拒绝（见 build_subagent_blocked_commands）：子代理只许执行真实
    # 系统/工具命令——Onyx 内置命令（exit/clear/ai/manage/sado/source 等）、
    # other_terminal_cmd.json（export/sudo/...）、shell 内置 cd（会改共享 shell CWD
    # 并同步回主会话），都不暴露给子代理。
    _subagent_blocked_cmds = build_subagent_blocked_commands(BUILTIN_COMMANDS, ROOT_DIR)

    def _subagent_run_command(_cmd: str) -> str:
        """子代理 RunCommand 执行：危险命令拒绝 + 内置命令拒绝 + 串行化 + 输出捕获。"""
        try:
            _is_danger, _cmd_name = is_dangerous_command(_cmd, dangerous_commands)
            if _is_danger:
                return f"⛔ 命令被拒绝（危险命令 [{_cmd_name}]，子代理无权执行）"
            # 内置命令拦截：取首行第一个词（忽略前导空白），防止子代理篡改 REPL 状态
            _head = extract_subagent_command_head(_cmd)
            if _head and _head in _subagent_blocked_cmds:
                return (f"⛔ 命令被拒绝：`{_head}` 是 Onyx/终端内置命令，子代理不可用。"
                        f"子代理只能执行真实系统/工具命令（git/python/pytest 等）。")
            with _SUBAGENT_CMD_LOCK:
                _captured = ""
                _rc = None
                with capture_command_output() as (_out_catcher, _err_catcher):
                    _out_catcher._ai_triggered = True
                    _exe_mod = sys.modules.get('lib.terminal.exe')
                    if _exe_mod:
                        _exe_mod.AI_EXECUTION_MODE = True
                        _exe_mod.AI_LAST_EXIT_CODE = None
                    try:
                        if parse_and_execute:
                            parse_and_execute(_cmd)
                    finally:
                        if _exe_mod:
                            _exe_mod.AI_EXECUTION_MODE = False
                            _rc = getattr(_exe_mod, "AI_LAST_EXIT_CODE", None)
                    _captured = (_out_catcher.get_output() + "\n" + _err_catcher.get_output()).strip()
            return _format_run_command_result(_cmd, _rc, _captured)
        except Exception as _e:
            return f"命令执行失败: {_e}"
    try:
        set_subagent_command_executor(_subagent_run_command)
    except Exception:
        pass

    # ── web_search_agent 子代理联网执行器：与主 AI 同一底层实现（web_search
    # 三模式调研），但不逐次弹用户确认——批准发生在 Agent 派发时；
    # 危险面一致：SSRF 防护、http/https 限制、超时与输出截断全部复用主实现。
    def _subagent_web_tool(_name: str, _params: dict) -> str:
        try:
            if _name == "web_search":
                return _exec_web_search_multi(_params or {})
            return f"⛔ 未知联网工具: {_name}"
        except Exception as _e:
            return f"❌ {_name} 执行失败: {_e}"
    try:
        set_subagent_web_executor(_subagent_web_tool)
    except Exception:
        pass

    # ── 主 AI RunCommand 执行器：走同一安全管线 ──
    # 危险命令弹用户确认（confirm_dangerous_command）、adv_code 模式语法拦截、
    # capture_command_output 捕获 + parse_and_execute 执行。
    # 供 RunCommand 内置工具（function calling）使用：结果以 tool role 消息回传，
    # 模型明确感知"已调用工具并拿到结果"。
    def _main_run_command(_cmd: str) -> str:
        """主 AI RunCommand 执行：危险确认 + adv_code 语法检查 + 串行化 + 输出捕获。"""
        try:
            _cmd = (_cmd or "").strip()
            if not _cmd:
                return "⛔ 命令为空"
            _is_danger, _cmd_name = is_dangerous_command(_cmd, dangerous_commands)
            if _is_danger:
                # 上下文分级 + 特别高危清单：决定强制确认 / 超时放行 / 直接放行
                _extra_danger, _extra_pattern = is_extra_dangerous_command(_cmd, extra_dangerous_commands)
                try:
                    _ctx_tokens = _estimate_conversation_tokens(conversation_history, current_session_id)
                except Exception:
                    _ctx_tokens = 0
                _confirmed, _u_resp, _refuse_reason = confirm_dangerous_command(
                    _cmd, _cmd_name, lang_text, current_session_id,
                    initial_question, interaction_count, log_info,
                    context_tokens=_ctx_tokens,
                    extra_dangerous=_extra_danger,
                )
                if not _confirmed:
                    return (f"⛔ 用户拒绝了危险命令 [{_cmd_name}]：{_cmd}\n"
                            f"拒绝原因: {_refuse_reason or '未提供'}")
            # adv_code 模式：禁止语法拦截
            if _current_user_mode == "adv_code" and has_forbidden_syntax(_cmd):
                return f"⛔ 命令包含被禁止的语法，已被拦截：{_cmd[:200]}"
            with _SUBAGENT_CMD_LOCK:
                _captured = ""
                _rc = None
                with capture_command_output() as (_out_catcher, _err_catcher):
                    _out_catcher._ai_triggered = True
                    _exe_mod = sys.modules.get('lib.terminal.exe')
                    if _exe_mod:
                        _exe_mod.AI_EXECUTION_MODE = True
                        _exe_mod.AI_LAST_EXIT_CODE = None
                    try:
                        if parse_and_execute:
                            parse_and_execute(_cmd)
                    finally:
                        if _exe_mod:
                            _exe_mod.AI_EXECUTION_MODE = False
                            _rc = getattr(_exe_mod, "AI_LAST_EXIT_CODE", None)
                    _captured = (_out_catcher.get_output() + "\n" + _err_catcher.get_output()).strip()
            return _format_run_command_result(_cmd, _rc, _captured)
        except Exception as _e:
            return f"命令执行失败: {_e}"
    try:
        set_main_command_executor(_main_run_command)
    except Exception:
        pass
    
    # 提取当前用户模式字符串（用于安全限制）
    # 兜底 "low" 仅在调用方未传 user_mode 时生效（安全方向）；
    # 正常路径跟随 user_mode.current_mode 实时值（activite 切换原地生效）
    _current_user_mode = "low"
    if user_mode is not None:
        if hasattr(user_mode, 'current_mode'):
            _current_user_mode = str(user_mode.current_mode).lower()
        else:
            _current_user_mode = str(user_mode).lower()

    # ── 注入子代理：子代理工具权限决策跟随主会话当前模式 ──
    try:
        from .ai_lib import subagent as _subagent_mode_hook
        _subagent_mode_hook.set_user_mode(_current_user_mode)
    except Exception:
        pass

    # 检查 MCP 是否启用（manage set mcp true/false；默认关闭——零默认 MCP）
    _mcp_enabled = False
    _mcp_enabled_path = os.path.join(user_home_dir, ".config", "onyx", "mcp_enabled")
    try:
        if os.path.exists(_mcp_enabled_path) and os.path.isfile(_mcp_enabled_path):
            with open(_mcp_enabled_path, "r") as f:
                _mcp_enabled = f.read().strip().lower() in ("true", "1", "yes", "on")
    except Exception:
        pass

    # ── 初始化内置工具系统 ──
    # 默认零 MCP，只加载本地内置工具
    # 只有用户主动安装了外部 MCP server（如 puppeteer/github）才会带 mcp_ 前缀
    _mcp_debug("── 初始化内置工具（冻结缓存）──")
    native_tools, ai_tools_prompt = get_native_tools_cached(user_home_dir, _mcp_enabled)

    # 如果用户显式启用了 MCP（安装了非 filesystem 的外部 server），再加载
    if _mcp_enabled:
        _migrate_mcp_config_if_needed(user_home_dir)
        registry = get_registry()
        # 只加载非 filesystem 的 MCP server（puppeteer/github/postgres 等）
        for _srv_name in registry.server_names():
            if _srv_name == "filesystem":
                continue
            _mcp_debug(f"检测到外部 MCP server: {_srv_name}")
        # 后台异步连接已有 MCP server
        _schedule_mcp_health_check(user_home_dir)
        # 注意：不要清空 ai_tools_prompt 和 native_tools
        # build_native_tools() 内部已通过 get_mcp_tools() 自动追加 MCP 工具
    _mcp_debug(f"── MCP 初始化完成, tools_prompt 长度={len(ai_tools_prompt)} ──")
    
    # ANSI 转义序列正则（颜色码、光标控制等）
    _RE_ANSI = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][0-9;]*[^\x07]*\x07|\x1b\(B')

    class RealTimeOutputCatcher:
        def __init__(self, stream_type):
            self.stream_type = stream_type
            self.buffer = []
            self._closed = False
            self._line_count = 0        # 累计行数
            self._ai_triggered = False  # AI 触发时限制显示行数
        
        def write(self, message):
            if self._closed:
                return
            # 剥离 ANSI 颜色码后再存入 buffer（AI 上下文需要干净文本）
            cleaned = _RE_ANSI.sub('', message) if message else ''
            if cleaned:
                self.buffer.append(cleaned)
            # 显示策略：AI 触发 → 前10行实时显示后截断；用户触发 → 全量
            if self.stream_type == "stdout":
                self._line_count += message.count('\n')
                if self._ai_triggered and self._line_count > 10:
                    return  # AI 模式超过10行，停止实时显示
                sys_module.__stdout__.write(message)
                sys_module.__stdout__.flush()
            else:
                sys_module.__stderr__.write(message)
                sys_module.__stderr__.flush()
        
        def flush(self):
            if self._closed:
                return
            if self.stream_type == "stdout":
                sys_module.__stdout__.flush()
            else:
                sys_module.__stderr__.flush()
        
        def isatty(self):
            return False
        
        def close(self):
            self._closed = True
        
        def get_output(self):
            return "".join(self.buffer)
    
    @contextmanager
    def capture_command_output():
        original_stdout = sys_module.stdout
        original_stderr = sys_module.stderr
        stdout_catcher = RealTimeOutputCatcher("stdout")
        stderr_catcher = RealTimeOutputCatcher("stderr")
        
        try:
            sys_module.stdout = stdout_catcher
            sys_module.stderr = stderr_catcher
            yield stdout_catcher, stderr_catcher
        except Exception as e:
            if log_error:
                log_error(f"Command execution capture exception: {str(e)}", request_id)
            raise
        finally:
            sys_module.stdout = original_stdout
            sys_module.stderr = original_stderr
            stdout_catcher.close()
            stderr_catcher.close()
    
    def cleanup_output_cache():
        if len(AI_TOOL_OUTPUT_CACHE) > MAX_CACHE_SIZE:
            items = list(AI_TOOL_OUTPUT_CACHE.items())
            for k, _ in items[:len(items)//5]:
                AI_TOOL_OUTPUT_CACHE.pop(k, None)
    
    def check_session_file_size(file_path: str) -> bool:
        if not os.path.exists(file_path):
            return True
        try:
            if os.path.getsize(file_path) > MAX_SESSION_FILE_SIZE:
                backup_path = f"{file_path}.{int(time.time())}.bak"
                os.rename(file_path, backup_path)
                if log_info:
                    log_info(f"Session file exceeded size limit, rotated: {os.path.basename(backup_path)}", request_id)
                return False
        except Exception as e:
            if log_error:
                log_error(f"Failed to check session file size: {str(e)}", request_id)
        return True

    # ── --debug 无附加参数时进入交互式 REPL，而非报错 ──
    if debug_mode and len(cmd_parts) <= 1:
        _PERSIST_DEBUG = True  # 跨 handle_ai 调用保持 debug 模式（已在函数顶部声明 global）
        from bin.ai_interactive import ai_interactive_session as _repl
        _repl(
            user_home_dir=user_home_dir,
            onyx_module=onyx_module,
            global_config=global_config,
            user_info=user_info,
            user_mode=user_mode,
            parse_and_execute=parse_and_execute,
        )
        _PERSIST_DEBUG = False  # REPL 退出时复位
        return

    parse_result = parse_arguments(cmd_parts, lang_text, onyx_module)
    if len(parse_result) == 9:
        content_type, content, extra_info, auto_exec, new_key, chat_action, chat_param, mode, times = parse_result
    elif len(parse_result) == 7:
        content_type, content, extra_info, auto_exec, new_key, chat_action, chat_param = parse_result
        mode = "normal"
        times = 1
    else:
        content_type, content, extra_info, auto_exec, new_key = parse_result
        chat_action, chat_param = None, None
        mode = "normal"
        times = 1
        
    if content_type == "mcp_command":
        # ai -mcp <install|list|remove> [args]
        handle_mcp_command(content, extra_info if isinstance(extra_info, list) else [])
        return

    if content_type == "model_command":
        # ai -model [name] — view or switch model
        import json as _json
        conf = load_key_conf()
        if not conf:
            console.print(_mcp_t("[yellow]No API key configured. Run 'ai -key <key>' first.[/]", "[yellow]No API key configured. Run 'ai -key <key>' first.[/]"))
            return
        platform = conf.get("platform", "deepseek")
        current_model = conf.get("model", "")
        is_custom = (platform == "custom")
        plat_name = "Custom" if is_custom else _SUPPORTED_PLATFORMS.get(platform, {}).get("name", platform)
        if not content:
            # List current model + effort
            effort = conf.get("params", {}).get("reasoning_effort", "") or _SUPPORTED_PLATFORMS.get(platform, {}).get("reasoning_effort", "")
            console.print(_mcp_t(f"[dim]平台: {plat_name}  模型: {current_model or '?'}  推理强度: {effort or '—'}[/]",
                                 f"[dim]Platform: {plat_name}  Model: {current_model or '?'}  Effort: {effort or '—'}[/]"))
            if not is_custom:
                models = _SUPPORTED_PLATFORMS.get(platform, {}).get("models", [])
                console.print(_mcp_t("可用模型:", "Available models:"))
                for m in models:
                    marker = "  ←" if m == current_model else ""
                    console.print(f"  {m}{marker}")
                console.print(_mcp_t("\n用法: ai -model <名称>\n       ai -effort high|max", "\nUsage: ai -model <name>\n       ai -effort high|max"))
            return
        # Switch model
        new_model = content.strip()
        conf["model"] = new_model
        # 混淆 api_key 后写入
        key_conf_path = os.path.join(user_home_dir, ".config", "onyx", "ai", "key.json")
        os.makedirs(os.path.dirname(key_conf_path), exist_ok=True)
        _write_conf = dict(conf)
        if "api_key" in _write_conf and isinstance(_write_conf["api_key"], str):
            _write_conf["api_key"] = _obfuscate(_write_conf["api_key"])
        with open(key_conf_path, "w", encoding="utf-8") as f:
            _json.dump(_write_conf, f, ensure_ascii=False, indent=2)
        os.chmod(key_conf_path, 0o600)
        console.print(_mcp_t(f"[green]✅ 已切换到模型: {new_model}[/]", f"[green]✅ Switched to model: {new_model}[/]"))
        return

    if content_type == "effort_command":
        # ai -effort [high|max] — view or set reasoning effort
        import json as _json
        conf = load_key_conf()
        if not conf:
            console.print(_mcp_t("[yellow]未配置 API key。[/]", "[yellow]No API key configured.[/]"))
            return
        if not content:
            current_effort = conf.get("params", {}).get("reasoning_effort", "") or _SUPPORTED_PLATFORMS.get(conf.get("platform", ""), {}).get("reasoning_effort", "high")
            console.print(_mcp_t(f"[dim]当前推理强度: {current_effort}[/]", f"[dim]Current reasoning effort: {current_effort}[/]"))
            console.print(_mcp_t("可用: high, max", "Available: high, max"))
            console.print(_mcp_t("用法: ai -effort high  |  ai -effort max", "Usage: ai -effort high  |  ai -effort max"))
            return
        effort_val = content.strip().lower()
        if effort_val not in ("high", "max"):
            console.print(_mcp_t("[yellow]无效的推理强度。请使用: high 或 max[/]", "[yellow]Invalid effort. Use: high or max[/]"))
            return
        params = conf.get("params", {})
        if not isinstance(params, dict):
            params = {}
        params["reasoning_effort"] = effort_val
        conf["params"] = params
        # 混淆 api_key 后写入
        key_conf_path = os.path.join(user_home_dir, ".config", "onyx", "ai", "key.json")
        os.makedirs(os.path.dirname(key_conf_path), exist_ok=True)
        _write_conf = dict(conf)
        if "api_key" in _write_conf and isinstance(_write_conf["api_key"], str):
            _write_conf["api_key"] = _obfuscate(_write_conf["api_key"])
        with open(key_conf_path, "w", encoding="utf-8") as f:
            _json.dump(_write_conf, f, ensure_ascii=False, indent=2)
        os.chmod(key_conf_path, 0o600)
        console.print(_mcp_t(f"[green]✅ 推理强度已设置为: {effort_val}[/]", f"[green]✅ Reasoning effort set to: {effort_val}[/]"))
        return

    if content_type == "deep_aff_mode":
        # ai -mode deep-aff <true|false> — 深情模式
        enable = content.lower() in ("true", "1", "yes")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if enable:
            try:
                # 加载插件
                from bin.plugin_loader import load_plugin, verify
                ok, reason, payload = verify("deep_aff")
                if not ok:
                    console.print(_mcp_t(f"❌ 深情模式插件验证失败: {reason}", f"❌ Deep Affection plugin verification failed: {reason}"), style="bold red")
                    return
                lib = load_plugin("deep_aff")
                if not lib:
                    console.print(_mcp_t("❌ 无法加载深情模式插件", "❌ Failed to load Deep Affection plugin"), style="bold red")
                    return
                # 调用 C 模块初始化
                validation_key = payload.get("binary_hash", "deep_aff_key")[:32]
                lib.deep_aff_init.argtypes = [ctypes.c_char_p]
                lib.deep_aff_init.restype = ctypes.c_int
                ret = lib.deep_aff_init(validation_key.encode())
                if ret != 0:
                    console.print(_mcp_t("❌ 深情模式授权失败", "❌ Deep Affection authorization failed"), style="bold red")
                    return
                # 获取提示词
                lib.deep_aff_get_prompt.argtypes = []
                lib.deep_aff_get_prompt.restype = ctypes.c_char_p
                lib.deep_aff_free.argtypes = [ctypes.c_char_p]
                prompt_ptr = lib.deep_aff_get_prompt()
                if not prompt_ptr:
                    console.print(_mcp_t("❌ 无法获取深情模式提示词", "❌ Failed to get Deep Affection prompt"), style="bold red")
                    return
                prompt_text = ctypes.c_char_p(prompt_ptr).value.decode("utf-8")
                lib.deep_aff_free(prompt_ptr)
                # 保存提示词到文件（后续 AI 调用时会读取）
                deep_aff_path = os.path.join(user_home_dir, ".ai_s", "deep_aff_prompt.txt")
                os.makedirs(os.path.dirname(deep_aff_path), exist_ok=True)
                with open(deep_aff_path, "w", encoding="utf-8") as f:
                    f.write(prompt_text)
                console.print(_mcp_t("💕 深情模式已激活", "💕 Deep Affection mode activated"), style="bold magenta")
                console.print(_mcp_t(f"   提示词已保存: {len(prompt_text)} 字", f"   Prompt saved: {len(prompt_text)} chars"), style="dim")
            except Exception as e:
                console.print(_mcp_t(f"❌ 深情模式启动失败: {e}", f"❌ Deep Affection mode startup failed: {e}"), style="bold red")
                import traceback
                traceback.print_exc()
        else:
            # 关闭深情模式
            deep_aff_path = os.path.join(user_home_dir, ".ai_s", "deep_aff_prompt.txt")
            if os.path.exists(deep_aff_path):
                os.remove(deep_aff_path)
            console.print(_mcp_t("💕 深情模式已关闭", "💕 Deep Affection mode disabled"), style="dim")
        return

    if content_type == "machine_id_command":
        # ai -mid / ai -machine-id — show current device fingerprint
        try:
            from bin.plugin_loader import get_machine_id
            mid = get_machine_id()
            console.print(_mcp_t(f"机器 ID: [bold]{mid}[/]", f"Machine ID: [bold]{mid}[/]"))
        except Exception as e:
            console.print(_mcp_t(f"[red]获取机器 ID 失败: {e}[/]", f"[red]Failed to get machine ID: {e}[/]"))
        return

    if content_type == "plugin_command":
        # ai -plugin <list|load|sign|verify|compile> [args]
        sub = content  # "list", "load", "sign", "verify", "compile"
        args = extra_info if isinstance(extra_info, list) else []
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if sub == "list":
            import subprocess as _sp
            _sp.run([sys.executable, os.path.join(root, "plugin_loader.py"), "list"])
        elif sub == "load" and args:
            import subprocess as _sp
            _sp.run([sys.executable, os.path.join(root, "plugin_loader.py"), "load", args[0]])
        elif sub == "verify" and args:
            import subprocess as _sp
            _sp.run([sys.executable, os.path.join(root, "plugin_loader.py"), "verify", args[0]])
        elif sub == "sign" and args:
            import subprocess as _sp
            cmd = [sys.executable, os.path.join(root, "plugin_loader.py"), "sign"] + args
            _sp.run(cmd)
        elif sub == "compile" and args:
            import subprocess as _sp
            _sp.run([sys.executable, os.path.join(root, "plugin_compile.py"), args[0]])
        else:
            console.print(_mcp_t("用法: ai -plugin list | load <名称> | verify <名称> | sign <名称> [版本] | compile <文件.c>",
                                 "Usage: ai -plugin list | load <name> | verify <name> | sign <name> [ver] | compile <file.c>"))
        return

    if content_type == "chat_only":
        if chat_action == "list":
            memories = list_chat_memories(_mem_home)
            console.print(lang_text["chat_list_header"], style="bold cyan")
            current = get_current_chat_name(user_home_dir)
            for mem in memories:
                if mem == current:
                    current_label = " (current)" if current_lang == "english" else " (当前)"
                    console.print(f"  * {mem}{current_label}", style="bold green")
                else:
                    console.print(f"    {mem}", style="white")
            return
        elif chat_action == "switch":
            if not chat_param:
                console.print(lang_text["chat_switch_usage"], style="bold red")
                return
            if switch_chat_memory(_mem_home, chat_param):
                console.print(lang_text["chat_switched"].format(chat_param), style="bold green")
            else:
                console.print(lang_text["chat_not_found"].format(chat_param), style="bold red")
            return
        elif chat_action == "new":
            name = chat_param if chat_param else datetime.now().strftime('%Y%m%d_%H%M%S')
            if create_chat_memory(_mem_home, name):
                switch_chat_memory(_mem_home, name)
                console.print(lang_text["chat_created"].format(name), style="bold green")
            else:
                console.print(lang_text["chat_already_exists"].format(name), style="bold yellow")
            return
        else:
            console.print(_mcp_t(f"未知 -c 操作: {chat_action}", f"Unknown -c action: {chat_action}"), style="bold red")
            return
    
    if content_type == "key_only":
        result = call_ai_api_sse(question="", new_key=new_key, debug_mode=debug_mode, onyx_module=onyx_module, user_home_dir=user_home_dir)
        if "error" in result:
            console.print(f"❌ {result['error']}", style="bold red")
        elif "key_set" in result and result["key_set"]:
            console.print(lang_text["key_set_success"], style="bold green")
            return
        return
    
    if content_type == "error":
        console.print(f"❌ {content}", style="bold red")
        if log_error:
            log_error(f"AI parameter error: {content}", request_id)
        return

    if content_type == "interactive":
        # 裸模式切换（ai plan / ai normal）→ 进入交互式 REPL
        from bin.ai_interactive import ai_interactive_session as _repl
        _repl(
            user_home_dir=user_home_dir,
            onyx_module=onyx_module,
            global_config=global_config,
            user_info=user_info,
            user_mode=user_mode,
            parse_and_execute=parse_and_execute,
        )
        return

    # ── TUI 模式已移除（-tui 参数不再支持）──

    # Ctrl+C 打断思考：直接抛出 KeyboardInterrupt 向上传播
    import signal as _signal
    _original_sigint = _signal.getsignal(_signal.SIGINT)

    def _on_interrupt(signum, frame):
        global _AI_INTERRUPTED
        _AI_INTERRUPTED = True
        # 立即恢复原始 SIGINT 处理器（避免泄漏）
        _signal.signal(_signal.SIGINT, _original_sigint)
        raise KeyboardInterrupt("User interrupted")

    _signal.signal(_signal.SIGINT, _on_interrupt)

    # 重置中断标志（避免上次 Ctrl+C 残留导致本次立即中断；ai_cmd 与 mcp_state 双份）
    # 重置中断标志（避免上次 Ctrl+C 残留导致本次立即中断；ai_cmd 与 mcp_state 双份）。
    # ⚠️ 必须保留函数级 global 声明：7279 行工具中断分支会对 _AI_INTERRUPTED 赋值，
    # 无声明时 Python 视其为函数局部变量 → 6338 读取处 UnboundLocalError。
    global _AI_INTERRUPTED
    _reset_ai_interrupt_flags()

    # _MANUAL_COMPACT_REQUESTED 通过 _mcp_shared 模块属性访问，无需 global

    current_session_id = request_id
    initial_question = content
    last_user_question = content  # 追踪最近一次用户输入，ESC 追问时更新
    continue_asking = True
    _user_input_round = False  # 本轮是否有真正的用户输入（library 记录去重用）
    interaction_count = 0
    _pending_plan = ""  # 来自 submit_plan 工具调用的计划文本（跨循环持久化）
    _pending_tool_logs: List[str] = []  # 待落盘的工具结果记录（交互记录写完后按顺序 flush，保证顺序正确）
    plan_confirmed = False  # Plan 模式：计划是否已获用户确认
    _plan_warned = False          # Plan 模式警告是否已注入记忆（每会话仅一次，避免每轮重复插入段落）
    _plan_block_count = 0         # Plan 模式连续拦截计数（>=2 时直接询问用户，防止无提示无限循环）
    _plan_free_mode_chosen = False  # 用户通过 choose_ask 选择了切换到自由模式
    referenced_memory_uuid = None
    current_chat_name = get_current_chat_name(_mem_home)
    message_appended = False
    
    cleanup_output_cache()

    def _ensure_library_record():
        """确保 library 文件存在（plan 流程等提前 continue 可能跳过常规记录）"""
        nonlocal current_session_id
        record_path = os.path.join(
            get_ai_session_library_dir(_mem_home), f"{current_session_id}.txt"
        )
        if not os.path.exists(record_path):
            with open(record_path, "w", encoding="utf-8") as f:
                f.write(f"Session ID: {current_session_id}\n"
                        f"Record time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"{'=' * 60}\n")

    def _flush_pending_tool_logs():
        """把缓存的工具结果记录追加到 library 文件（在交互记录落盘之后，保持时间顺序）。"""
        if not _pending_tool_logs:
            return
        try:
            from .ai_lib.storage import get_ai_session_library_dir as _gld
            _rp = os.path.join(_gld(_mem_home), f"{current_session_id}.txt")
            with open(_rp, "a", encoding="utf-8") as _f:
                for _seg in _pending_tool_logs:
                    _f.write(f"\n\n{_seg}\n")
            _pending_tool_logs.clear()
        except Exception:
            pass
    
    # ── last_prompt_tokens 清零（每场对话独立） ──
    _thread_locals.last_prompt_tokens = 0

    current_times = 1
    # ── 风暴检测（只拦连续失败，不拦重复成功）──
    _MAX_TOOL_OUTPUT = 32 * 1024   # 单次工具结果最大字节数，超长截断防上下文撑爆
    _storm_counter = {}          # error_signature → count: 连续相同错误次数，>=3 时触发换策略
    _repeat_success = {}         # 操作签名 → 成功次数：>=3 时触发重复警告

    # ── 标准对话历史（messages 结构）──
    # REPL 模式：conversation_history 由外部维护，跨 handle_ai 调用持久保留
    _external_history = conversation_history is not None
    if not _external_history:
        conversation_history: List[Dict] = []
    import platform as _pf

    # ── 提前加载海马体索引 + agreement（供 _env_info 和 .prompt 使用）──
    _hippocampus_index = build_stable_prefix(_mem_home)
    # ── agreement 三件套拼接：普通模式 = self.md + skill.md（固定前缀，缓存命中）；
    #    plus 模式思考段 = 仅 self.md（最短），干活段 = self.md + skill.md + plus 思考结果。
    #    旧 agreement.md 保留为兼容入口（仅说明，不承载正文）。
    _agreement_text = ""
    try:
        _ai_dir_candidates = [
            os.path.join(ROOT_DIR, "onyx", "etc", "ai"),
            os.path.join("etc", "ai"),
        ]
        _ai_dir = next((d for d in _ai_dir_candidates if os.path.isdir(d)), None)
        if _ai_dir:
            _parts = []
            for _name in ("self.md", "skill.md"):
                _p = os.path.join(_ai_dir, _name)
                if os.path.exists(_p):
                    with open(_p, "r", encoding="utf-8") as _af:
                        _parts.append(_af.read())
            if _parts:
                _agreement_text = "\n\n".join(_parts)
        if not _agreement_text:
            # 回退：旧 agreement.md（兼容旧部署）
            for _ap in (
                os.path.join(ROOT_DIR, "onyx", "etc", "ai", "agreement.md"),
                os.path.join("etc", "ai", "agreement.md"),
            ):
                if os.path.exists(_ap):
                    with open(_ap, "r", encoding="utf-8") as _af:
                        _agreement_text = _af.read()
                    break
    except Exception:
        pass

    if not _external_history or len(conversation_history) == 0:
        # ── 首次进入：构建静态环境信息（动态部分 cwd/time 由 api.py 追加到消息末尾）──
        _env_info = (
            f"System: {_pf.system()} - {_pf.release()}\n"
            f"User: {os.environ.get('USER', '?')}\n"
        )

        # ── 项目上下文自动注入（git 状态 → 动态尾部；指令文件 → 静态 messages[0]）──
        _project_context = ""
        _git_context = ""   # git 状态为动态内容，放对话末尾避免前缀缓存断裂
        try:
            _git_root = os.getcwd()
            # git status（简短）
            _git_status = _run_shell_cmd("git status --short 2>/dev/null | head -30")
            if _git_status:
                _git_context += f"#Git 状态\n{_git_status}\n"
                # 分支 / diff（前 30KB）/ 最近 5 条 commit —— 合并为单次子进程调用（启动提速，输出不变）
                _git_rest = _run_shell_cmd(
                    "echo '@@ONYX_GIT_BRANCH@@'; git rev-parse --abbrev-ref HEAD 2>/dev/null; "
                    "echo '@@ONYX_GIT_DIFF@@'; git diff --no-color 2>/dev/null | head -500; "
                    "echo '@@ONYX_GIT_LOG@@'; git log --oneline -5 2>/dev/null"
                )
                _git_branch = ""
                _git_diff = ""
                _git_log = ""
                try:
                    _secs = _git_rest.split("@@ONYX_GIT_BRANCH@@")[-1].split("@@ONYX_GIT_DIFF@@")
                    _git_branch = _secs[0].strip()
                    _secs = _secs[1].split("@@ONYX_GIT_LOG@@")
                    _git_diff = _secs[0].strip()
                    _git_log = _secs[1].strip()
                except Exception:
                    pass
                if _git_branch:
                    _git_context = f"分支: {_git_branch}\n" + _git_context
                if _git_diff and len(_git_diff) > 100:
                    _diff_str = _git_diff[:30000]
                    if len(_git_diff) > 30000:
                        _diff_str += f"\n…[diff 过长，截断至 30000 字符，共 {len(_git_diff)} 字符]"
                    _git_context += f"#Git 变更\n{_diff_str}\n"
                if _git_log:
                    _git_context += f"#最近提交\n{_git_log}\n"
            # 指令文件自动发现（onyx_s.md / CLAUDE.md / AGENTS.md / .onyx/rules/*）
            _instruction_files = []
            for _root in [_git_root] if _git_status else [os.getcwd()]:
                for _fname in ["onyx_s.md", "CLAUDE.md", "AGENTS.md", "CLAUDE.local.md"]:
                    _fpath = os.path.join(_root, _fname)
                    if os.path.exists(_fpath):
                        _instruction_files.append(_fpath)
                # .onyx/ 目录
                _onyx_dir = os.path.join(_root, ".onyx")
                if os.path.isdir(_onyx_dir):
                    for _fname in ["onyx_s.md", "CLAUDE.md", "instructions.md"]:
                        _fpath = os.path.join(_onyx_dir, _fname)
                        if os.path.exists(_fpath):
                            _instruction_files.append(_fpath)
                    _rules_dir = os.path.join(_onyx_dir, "rules")
                    if os.path.isdir(_rules_dir):
                        for _rf in sorted(os.listdir(_rules_dir)):
                            if _rf.endswith((".md", ".txt", ".mdc")):
                                _instruction_files.append(os.path.join(_rules_dir, _rf))
            # 读取指令文件内容（限制每个 4KB，总 12KB）
            _total_inst_chars = 0
            _inst_lines = []
            for _fpath in _instruction_files:
                if _total_inst_chars > 12000:
                    break
                try:
                    with open(_fpath, "r", encoding="utf-8") as _f:
                        _content = _f.read()[:4000]
                    _rel = os.path.relpath(_fpath, _git_root) if _git_root else _fpath
                    _inst_lines.append(f"### {_rel}\n{_content}")
                    _total_inst_chars += len(_content)
                except Exception:
                    pass
            if _inst_lines:
                _project_context += "#项目指令\n" + "\n\n".join(_inst_lines) + "\n"
        except Exception:
            pass

        if _project_context:
            _env_info = _project_context + "\n" + _env_info

        # ── 读取 onyx_ai.md 最高指示（追加到 _env_info 末尾，不进入前缀缓存）──
        _onyx_prompt_path = os.path.join(_mem_home, ".ai_s", "onyx_ai.md")
        _onyx_ai_prompt = ""
        if os.path.exists(_onyx_prompt_path):
            try:
                with open(_onyx_prompt_path, "r", encoding="utf-8") as _f:
                    _onyx_ai_prompt = _f.read().strip()
            except Exception:
                pass
        if _onyx_ai_prompt:
            _env_info += f"\n#最高指示（持久记忆）\n{_onyx_ai_prompt}\n"

        # ── 动态环境信息（git 状态 + 历史索引）→ 对话末尾，不进入 messages[0] ──
        # 关键：messages[0] 必须保持静态，否则 git 状态/索引每次变化
        # 会让 DeepSeek 前缀缓存从第一个字符就 miss（执行命令改文件后尤其明显）。
        _dynamic_env_parts = []
        if _git_context:
            _dynamic_env_parts.append(_git_context.rstrip())
        if _hippocampus_index:
            _dynamic_env_parts.append(f"#历史会话索引\n{_hippocampus_index}".rstrip())
        _dynamic_env = "\n\n".join(_dynamic_env_parts) if _dynamic_env_parts else ""

        _system_msg = {"role": "system", "content": _env_info}
        conversation_history.append(_system_msg)
        _time_tag = f"\n\n[⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}]"
        conversation_history.append({"role": "user", "content": initial_question + _time_tag})
        _user_input_round = True  # 首轮：首次提问已入历史
    else:
        # ── 已有上下文：直接追加新用户问题，不重建系统消息 ──
        # 动态环境已在首轮注入（REPL 场景复用会话历史，不重复采集）
        _dynamic_env = ""
        _time_tag = f"\n\n[⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}]"
        conversation_history.append({"role": "user", "content": initial_question + _time_tag})
        _user_input_round = True  # REPL 新输入：本次提问已入历史

    current_question = initial_question  # 用于日志/估算，API 实际走 conversation_history

    # ── 统一前缀缓存：仅 agreement.md + 工具描述（真正 100% 静态）──
    # 海马体索引已移至 _env_info 末尾（动态部分，不参与前缀缓存）
    from .ai_lib.prompt_cache import (
        build_prompt_file, delete_prompt_file, refresh_prompt_tmp,
        track_and_rotate, get_prompt_prefix, format_hit_rate_summary,
        ensure_tmp_dir, get_hit_rate_log_path,
    )
    # ── 统一前缀：仅 agreement.md + 工具描述（真正 100% 静态）──
    # 海马体索引不进入前缀——它每轮微增会导致 API 缓存断裂，
    # 改为追加到 _env_info 末尾（动态部分，不影响前缀缓存）
    ensure_tmp_dir(_mem_home)
    delete_prompt_file(_mem_home)
    build_prompt_file(
        home_dir=_mem_home,
        system_prompt=_agreement_text,
        # 工具描述已由 function schema 提供，不再注入文本清单（避免三份重复）
        tools_prompt="",
    )
    refresh_prompt_tmp(_mem_home, current_session_id)

    # _stable_prefix 指向统一前缀（来自 2.tmp，按 session_id 隔离）
    _stable_prefix = get_prompt_prefix(_mem_home, current_session_id)

    # ── 会话级记忆上下文缓存：仅在首轮从磁盘加载，后续轮次复用 ──
    # 当前会话的 library 记录在对话过程中持续增长（每轮追加写入），
    # 若每轮重读会导致 #聊天记忆 内容变化 → DeepSeek 前缀缓存在此处断裂。
    # 会话内 AI 已通过 conversation_history 持有全部上下文，无需每轮刷新。
    _cached_memory_section = build_memory_context(
        _mem_home, current_chat_name, current_session_id,
        referenced_memory_uuid, True, mode, plus_think=plus_think
    )

    # ── Layer 2 / TimeBased 闲置压缩：挂机 >60 分钟回来 → 清理已消费的旧工具结果 ──
    # 只在 REPL 长会话生效；静默、无 LLM 调用、只动已被 AI 消费的 tool 输出。
    try:
        if _external_history and (time.time() - _last_ai_interaction_ts) > _IDLE_COMPACT_SECONDS:
            if compact_consumed_tool_results(conversation_history):
                from .ai_lib.api import bump_rewrite_version as _bump_idle
                _bump_idle(current_session_id)
                console.print(_mcp_t("[dim]📦 闲置压缩: 已清理过期的工具输出[/]", "[dim]📦 Idle compact: cleaned up expired tool outputs[/]"))
    except Exception:
        pass
    _last_ai_interaction_ts = time.time()

    # ── Layer 4 / Reactive 响应式兜底：上下文超限报错时允许强制压缩一次 ──
    _reactive_compact_done = False

    while continue_asking:
        # ── 异步 Explore 子代理：每轮开始时把已完成任务的结果注入上下文 ──
        try:
            from .ai_lib import subagent as _subagent_mod
            _explore_done = _subagent_mod.get_manager().collect_done()
            for _et in _explore_done:
                if _et.status == "done" and _et.summary:
                    _inject = f"子代理结果：{_et.label}任务「{_et.name}」完成：\n{_et.summary}"
                    console.print(_mcp_t(f"  [bold cyan]🧩 {_et.label}子代理「{_et.name}」完成，结果已注入上下文[/]",
                                         f"  [bold cyan]🧩 {_et.label} subagent「{_et.name}」done, result injected into context[/]"))
                else:
                    _inject = f"子代理任务失败：{_et.label}任务「{_et.name}」失败：{_et.error or _et.status}"
                    console.print(_mcp_t(f"  [bold red]🧩 {_et.label}子代理「{_et.name}」失败[/]",
                                         f"  [bold red]🧩 {_et.label} subagent「{_et.name}」failed[/]"))
                conversation_history.append({"role": "system", "content": _inject})
            # 运行中的子代理：灰色显示最近活动尾行（告诉用户没卡住）
            if _subagent_mod.get_manager().has_pending():
                _act_tail = _subagent_mod.get_manager().format_activity(4)
                if _act_tail:
                    console.print(_act_tail, style="dim")
        except Exception:
            pass
        _tool_calls_processed_this_round = False
        _commands_processed_this_round = False
        if _AI_INTERRUPTED:
            console.print(_mcp_t("\n⏹ 已中断", "\n⏹ Interrupted"), style="yellow")
            break
        interaction_count += 1
        user_answer = ""
        user_refuse_reasons = []
        
        # ── 确保 library 磁盘记录存在（工具结果依赖它持久化）──
        _ensure_library_record()
        
        existing_memory, memory_file = get_latest_ai_session(_mem_home, current_session_id)
        if memory_file:
            check_session_file_size(memory_file)
        
        # AI 引用记忆时显示提示（API 调用前，让用户提前看到）
        if referenced_memory_uuid:
            console.print(
                lang_text["memory_referenced"].format(referenced_memory_uuid[:24] + "..."),
                style="dim cyan"
            )
        
        no_memory_text = lang_text.get("no_memory", "No historical memory" if current_lang == "english" else "无历史记忆")
        # 记忆上下文：仅单次模式（ai "question"）注入。
        # 对话模式（ai 进入 REPL）不注入 — conversation_history 已持有全量上下文，
        # 注入会导致 API 请求体变化 → DeepSeek 前缀缓存断裂。
        if _cached_memory_section != no_memory_text and not _external_history and interaction_count == 1:
            _memory_content = f"#聊天记忆\n{_cached_memory_section}"
            conversation_history.append({"role": "system", "content": _memory_content})

        # Plan 模式前缀：告知 AI 当前处于 plan 模式，禁止执行命令和文件修改
        # mode=="plan"（用户 ai plan 命令）或 _PLAN_MODE_ACTIVE（AI 调用 EnterPlanMode）
        # ⚠️ 每会话仅注入一次：原逻辑在 while 循环内每轮都追加同一段 system 消息，
        # 一轮任务会在记忆中插入 N 份重复段落（记忆膨胀且干扰前缀缓存），改为内容去重 + 标志位。
        if (mode == "plan" or _PLAN_MODE_ACTIVE) and not _plan_warned:
            plan_warning = lang_text.get("plan_mode_warning",
                "⚠️ 当前处于 PLAN 模式。你只能生成计划，不能执行任何命令或修改文件。"
                "请通过 submit_plan 工具提交你的计划。"
                "等用户确认后，才能进入执行阶段。"
                "如果要退出 plan 模式，请调用 ExitPlanMode 工具。")
            # REPL 跨会话复用 history 时防重复：内容已存在则跳过
            _already_has_warning = any(
                _m.get("role") == "system" and _m.get("content") == plan_warning
                for _m in conversation_history
            )
            if not _already_has_warning:
                conversation_history.append({"role": "system", "content": plan_warning})
            _plan_warned = True
        
        # 流式展示：Rich Live Panel — 实时更新 AI 回答
        from rich.live import Live
        from rich.panel import Panel
        from rich.box import ROUNDED
        
        # ── 纯 Markdown 直通：单一流式文本缓冲（标记语言已彻底移除）──
        stream_text = ""          # 累积流式 Markdown 文本
        live_ref = [None]         # Live 对象引用
        loading_flag = [True]
        tool_results_display = []  # 工具执行结果（用于面板展示：名前10行灰色虚影）

        # _strip_markers 已删除：标记语言彻底移除，AI 输出即纯 Markdown，无需剥离

        # _write_onyx_ai_prompt 已删除：[PROMPT] 标记随标记语言一并移除

        def _render_all_panels():
            """将所有已接收的内容块组合为复合 Panel"""
            from rich.console import Group
            from rich.markdown import Markdown
            from rich.text import Text

            parts = []

            # 纯 Markdown 直通：流式文本原样渲染为回复面板（无标记语言）
            if stream_text.strip():
                parts.append(Panel(Markdown(stream_text.strip()),
                                   title="💬 回复", border_style="green", box=ROUNDED))

            # MCP 工具执行结果（前4行）
            if tool_results_display:
                for tr in tool_results_display:
                    icon = "✅" if tr["ok"] else "❌"
                    style = "dim green" if tr["ok"] else "dim red"
                    header = f"{icon} {tr['name']}"
                    body = tr.get("preview", tr.get("output", "")[:100])
                    _total = len(tr.get("output", ""))
                    if _total > 100:
                        body += "\n…" + _i18n("panel_output_kept", "bilingual", total=_total)
                    parts.append(Panel(body, title=header, border_style=style, box=ROUNDED,
                                       padding=(0, 1)))

            if not parts:
                return Panel(Spinner("dots", text=_i18n("thinking", "bilingual"),
                                     style="bold cyan"),
                            title="🤖 AI", border_style="green", box=ROUNDED)

            if len(parts) == 1:
                return parts[0]
            return Group(*parts)

        # ── MCP 路径安全校验器（桥接 AI 虚拟沙盒与 MCP 工具执行）──
        def _mcp_path_validator(tool: str, path: str) -> Tuple[bool, str]:
            """校验 MCP 工具操作的路径是否在 AI 沙盒（cwd）允许范围内"""
            # AI 虚拟沙盒优先：激活时仅允许 cwd 内（虚拟根 / 映射为 cwd）
            if sandbox.is_active():
                if sandbox.is_within(path):
                    return True, ""
                lang = get_current_lang()
                if lang == "chinese":
                    return False, f"⛔ 沙箱拦截：MCP 工具 '{tool}' 无权访问路径 '{path}'（超出当前工作目录）"
                return False, f"⛔ Sandbox blocked: MCP tool '{tool}' cannot access path '{path}' (outside cwd)"
            # 尝试通过 onyx_module 调用 check_sandbox_path
            if onyx_module and hasattr(onyx_module, "check_sandbox_path"):
                try:
                    if not onyx_module.check_sandbox_path(path, request_id):
                        lang = get_current_lang()
                        if lang == "chinese":
                            return False, f"⛔ 沙箱拦截：MCP 工具 '{tool}' 无权访问路径 '{path}'"
                        return False, f"⛔ Sandbox blocked: MCP tool '{tool}' cannot access path '{path}'"
                except Exception as e:
                    if log_warning:
                        log_warning(f"MCP path check exception for '{path}': {e}", request_id)

            # 回退：检查是否在用户主目录内
            home = user_home_dir or USER_HOME_DIR
            try:
                real_path = os.path.realpath(path) if os.path.exists(path) else os.path.abspath(path)
                real_home = os.path.realpath(home)
                if real_path == real_home or real_path.startswith(real_home + os.sep):
                    return True, ""
            except Exception:
                pass

            # 最终回退：放行（非 Termux 环境）
            if not os.path.exists('/data/data/com.termux'):
                return True, ""

            lang = get_current_lang()
            if lang == "chinese":
                return False, f"⛔ 路径越界：MCP 工具 '{tool}' 尝试访问 '{path}'，超出用户主目录范围"
            return False, f"⛔ Path out of bounds: MCP tool '{tool}' attempted to access '{path}'"


        def on_stream_content(chunk: str) -> None:
            """实时流式回调：纯 Markdown 直通累积 + 更新复合 Panel"""
            nonlocal stream_text, _content_started
            _content_started = True  # 首次收到内容，切换到内容面板

            # 规范化换行符 + 去除原始回车符（防止 ^M 污染显示）
            chunk = chunk.replace('\r\n', '\n').replace('\r', '\n')

            stream_text += chunk

            # 防止缓冲区无限增长（异常情况下丢旧数据）
            if len(stream_text) > 100000:
                stream_text = stream_text[-20000:]

            # 更新 Live Panel
            if live_ref[0]:
                live_ref[0].update(_render_all_panels())
        
        # 启动 Live Panel：动画 spinner + 流式展示
        from rich.spinner import Spinner
        
        # ── SIGINT 强制中断处理：安装专用 handler 让 Ctrl+C 能立即起作用 ──
        import signal as _signal
        _original_sigint = _signal.getsignal(_signal.SIGINT)
        _original_sigint_for_close = _original_sigint
        from .ai_lib import mcp_state as _mcp_state_mod
        def _interrupt_handler(sig, frame):
            """Ctrl+C 强制中断：立即设置标记 + close HTTP 连接 + 显示提示"""
            _mcp_state_mod._AI_INTERRUPTED = True
            import sys as _sys
            _sys.stderr.write("\n⚠️ 强制中断中...\n")
            _sys.stderr.flush()
            # 如果有活动的 HTTP 请求，关闭连接以减少响应延迟（仅当前会话的）
            try:
                from .ai_lib.api import abort_active_response
                abort_active_response(current_session_id)
            except Exception:
                pass
        _signal.signal(_signal.SIGINT, _interrupt_handler)
        
        spinner = Spinner("dots", text=_mcp_t(" 思考中...", " Thinking..."), style="bold cyan")
        initial_panel = Panel(spinner, title="🤖 AI", border_style="green", box=ROUNDED)
        
        ai_result = {}
        _live_shown = False  # 标记 Live Panel 是否已展示（避免重复 console.print）
        try:
            if log_info:
                log_info(lang_text["api_call"].format(current_question[:50]), current_session_id)

            # ── 手动对话压缩：仅 /compact 命令触发（与自动压缩共用同一 Trident 管道）──
            if _mcp_shared._MANUAL_COMPACT_REQUESTED and interaction_count > 2:
                try:
                    _new_hist, _saved, _superseded, _old_len, _trident_stats = _compact_conversation_history(
                        conversation_history,
                        user_home_dir=_mem_home,
                        session_id=current_session_id,
                    )
                    if _new_hist is None:
                        console.print(_mcp_t("[dim]📦 对话压缩: 无可安全压缩的旧消息（tool_calls 链覆盖全部）[/]",
                                             "[dim]📦 Conversation compact: no safely compressible old messages (tool_calls chain covers all)[/]"))
                    else:
                        conversation_history = _new_hist
                        # 通知缓存诊断：rewrite 版本号 +1，归因缓存断裂为日志重写
                        from .ai_lib.api import bump_rewrite_version as _bump
                        _bump(current_session_id)
                        console.print(
                            _mcp_t(
                                f"[dim]📦 对话压缩: {_old_len} 条 → 摘要 "
                                f"({_saved} 条节省, {_superseded} 条去重"
                                f", {_trident_stats.get('collapsed_msgs', 0)} 折叠"
                                f", {_trident_stats.get('clustered_msgs', 0)} 聚类)[/]",
                                f"[dim]📦 Conversation compact: {_old_len} msgs → summary "
                                f"({_saved} saved, {_superseded} deduped"
                                f", {_trident_stats.get('collapsed_msgs', 0)} collapsed"
                                f", {_trident_stats.get('clustered_msgs', 0)} clustered)[/]"
                            )
                        )
                except Exception:
                    pass
                finally:
                    _mcp_shared._MANUAL_COMPACT_REQUESTED = False  # 单次触发，执行后复位

            # ── 自动对话压缩：上下文超过阈值时自动触发（无需用户干预）──
            # 旧轮 → LLM 保真摘要（保留用户原话 + 最近 8 条原文 + 不切断 tool 配对）
            if (not _mcp_shared._MANUAL_COMPACT_REQUESTED and interaction_count > 2
                    and not _COMPACT_BREAKER_DISABLED.get(current_session_id)):
                try:
                    _eff_thr = _effective_compact_threshold(current_session_id)
                    if _estimate_conversation_tokens(conversation_history, current_session_id) >= _eff_thr:
                        _new_hist, _saved, _superseded, _old_len, _trident_stats = _compact_conversation_history(
                            conversation_history,
                            user_home_dir=_mem_home,
                            session_id=current_session_id,
                        )
                        if _new_hist is not None:
                            conversation_history = _new_hist
                            from .ai_lib.api import bump_rewrite_version as _bump
                            _bump(current_session_id)
                            console.print(
                                _mcp_t(
                                    f"[dim]📦 自动压缩: ~{_eff_thr // 1024}K tokens 上下文 "
                                    f"→ 摘要 ({_saved} 条节省, {_superseded} 条去重"
                                    f", {_trident_stats.get('collapsed_msgs', 0)} 折叠"
                                    f", {_trident_stats.get('clustered_msgs', 0)} 聚类)[/]",
                                    f"[dim]📦 Auto compact: ~{_eff_thr // 1024}K tokens context "
                                    f"→ summary ({_saved} saved, {_superseded} deduped"
                                    f", {_trident_stats.get('collapsed_msgs', 0)} collapsed"
                                    f", {_trident_stats.get('clustered_msgs', 0)} clustered)[/]"
                                )
                            )
                            # ── 熔断器：压缩后仍 ≥90% 阈值 → 计数；连续 3 次 → 本会话停用自动压缩 ──
                            _after = _estimate_conversation_tokens(conversation_history, current_session_id)
                            if _after >= int(_eff_thr * 0.9):
                                _COMPACT_BREAKER_COUNTS[current_session_id] = (
                                    _COMPACT_BREAKER_COUNTS.get(current_session_id, 0) + 1)
                                if _COMPACT_BREAKER_COUNTS[current_session_id] >= 3:
                                    _COMPACT_BREAKER_DISABLED[current_session_id] = True
                                    console.print(
                                        _mcp_t(
                                            "[bold yellow]⚠️ 连续 3 次压缩后上下文仍接近阈值，"
                                            "本会话已停止自动压缩。请用 /compact 手动压缩，"
                                            f"或调大 {_eff_thr // 1024}K 阈值。[/]",
                                            "[bold yellow]⚠️ Context still near threshold after 3 compactions, "
                                            "auto-compact disabled for this session. Use /compact manually, "
                                            f"or raise the {_eff_thr // 1024}K threshold.[/]"
                                        )
                                    )
                            else:
                                _COMPACT_BREAKER_COUNTS[current_session_id] = 0
                except Exception:
                    pass

            with Live(initial_panel, console=console, refresh_per_second=15, transient=False) as live:
                live_ref[0] = live
                loading_flag[0] = False  # Live Panel 已接管展示
                
                # 使用SSE模式调用（带实时流式回调）
                # 每轮 API 调用前复位中断标志：复位权已从 call_ai_api_sse 收归主循环，
                # 防止并发子代理请求把用户 Ctrl+C 置位的标志清零导致中断失效。
                _reset_ai_interrupt_flags()
                _mcp_debug(f"调用 call_ai_api_sse(messages={len(conversation_history)}条)")
                # ── debug 模式：把 AI 真实看到的 conversation 写入 deb/{session_id}/round_N.txt ──
                if debug_mode:
                    _deb_session_dir = os.path.join(_mem_home, ".ai_s", "deb", current_session_id)
                    os.makedirs(_deb_session_dir, exist_ok=True)
                    _conv_path = os.path.join(_deb_session_dir, f"round_{interaction_count}.txt")
                    try:
                        _lines = []
                        for _idx, _msg in enumerate(conversation_history):
                            _role = _msg.get("role", "?")
                            _content = _msg.get("content", "") or ""
                            _lines.append(f"╔══ [{_idx}] {_role.upper()} ══╗")
                            _lines.append(_content.rstrip())
                            _lines.append("")
                        with open(_conv_path, "w", encoding="utf-8") as _cf:
                            _cf.write("\n".join(_lines))
                        _mcp_debug(f"conversation saved: {_conv_path} ({len(conversation_history)} msgs)")
                    except Exception as _ce:
                        _mcp_debug(f"conversation save failed: {_ce}")
                try:
                    _reasoning_buffer = []
                    _content_started = False
                    def _on_reasoning(chunk: str) -> None:
                        """流式显示 AI 思考过程"""
                        nonlocal _reasoning_buffer, _content_started
                        if _content_started:
                            return  # 已切换到内容显示，不再更新思考面板
                        _reasoning_buffer.append(chunk)
                        _text = "".join(_reasoning_buffer[-100:])
                        live.update(Panel(
                            RichText(_text, style="dim italic"),
                            title="🤖 AI 思考中...",
                            border_style="bright_black",
                            box=ROUNDED,
                        ))
                    def _on_tool_call(tool_name: str) -> None:
                        """流式检测到工具调用时立即更新面板"""
                        # 不展示工具调用信息给用户，保持界面清爽
                    # Plan 模式：工具定义保持完整，保证前缀缓存稳定
                    # 原逻辑第 3 轮起把 tools 列表收窄为仅 plan 工具、确认后再恢复——
                    # tools schema 是请求前缀的一部分，中途收窄/恢复会让 DeepSeek
                    # 前缀缓存从工具定义处整体 miss。安全拦截由下方 Plan 拦截逻辑
                    # 在调用点执行（5560 附近），不需要也不应该通过改 tools 列表实现。
                    _active_tools = native_tools
                    # ── 动态环境信息（git 状态/历史索引）→ 对话末尾，保证前缀缓存稳定 ──
                    # messages[0] 保持静态；动态内容只更新末尾一条 system 消息，
                    # 即使 git 状态变化也不影响 DeepSeek 前缀缓存命中。
                    # 去重：原逻辑只在 [-1] 是 dyn_env 时替换，工具轮后 [-1] 是
                    # tool 消息 → 每轮 append 新条，一次会话实测累积 19 条重复
                    # 海马体索引。改为查找历史中最后一条 dyn_env 原位更新：
                    # 会话内内容不变 → 字节不变，前缀零影响；REPL 跨轮内容变化
                    # → 只影响末尾附近，不再膨胀请求体。
                    if _dynamic_env:
                        _dyn_sys = {"role": "system", "content": _dynamic_env, "_dyn_env": True}
                        _dyn_found = False
                        for _di in range(len(conversation_history) - 1, -1, -1):
                            if conversation_history[_di].get("_dyn_env"):
                                conversation_history[_di] = _dyn_sys
                                _dyn_found = True
                                break
                        if not _dyn_found:
                            conversation_history.append(_dyn_sys)
                    # ── 前缀缓存命中率追踪：记录本轮与上一轮的前缀匹配率 ──
                    _hit_rate_this_round = track_and_rotate(_mem_home, current_session_id)
                    api_raw_result = call_ai_api_sse(
                        question="", 
                        messages=conversation_history,
                        new_key=new_key, 
                        debug_mode=debug_mode, 
                    onyx_module=onyx_module,
                    mode=mode,
                    times=current_times,
                    ai_tools_prompt=ai_tools_prompt,
                    on_content=on_stream_content,
                    on_tool_call=_on_tool_call,
                    on_reasoning=_on_reasoning,
                    user_home_dir=_mem_home,
                    tools=_active_tools,
                    memory_block=_stable_prefix,
                    session_id=current_session_id,
                    )
                    _mcp_debug(f"call_ai_api_sse 返回: {'interrupted' if (api_raw_result or {}).get('_interrupted') else 'OK' if api_raw_result else 'None'}")
                except Exception as _api_exc:
                    import traceback as _tb
                    import sys as _sys
                    _tb.print_exc(file=_sys.stderr)
                    _mcp_debug(f"call_ai_api_sse 异常: {type(_api_exc).__name__}: {_api_exc}")
                    console.print(_mcp_t(f"[red]API 调用异常: {_api_exc}[/]", f"[red]API call error: {_api_exc}[/]"))
                    continue_asking = False
                    break
                current_times += 1
                
                # Live Panel 最终更新
                if (api_raw_result or {}).get("_interrupted"):
                    live.update(Panel(_mcp_t("⏹ 已中断", "⏹ Interrupted"), title="🤖 AI", border_style="yellow", box=ROUNDED))
                else:
                    parsed_txt = (api_raw_result or {}).get("txt", "").strip()
                    api_error = (api_raw_result or {}).get("error", "")
                    if parsed_txt:
                        live.update(render_ai_panel(parsed_txt))
                        _live_shown = True
                    elif api_error:
                        err_short = api_error[:200] + ("..." if len(api_error) > 200 else "")
                        live.update(Panel(f"❌ {err_short}", title="🤖 AI", border_style="red", box=ROUNDED))
                        _live_shown = True
            
            # SSE返回的已经是解析好的dict
            if 'api_raw_result' not in locals() or not isinstance(api_raw_result, dict):
                # 防御：API 调用段异常时兜底，避免 UnboundLocalError 上抛
                ai_result = {"error": "SSE processing error: API 调用未完成", "answer": "no", "ask": ""}
                live_ref[0] = None
            else:
                ai_result = api_raw_result
                
        except Exception as e:
            import traceback as _tb
            _tb.print_exc(file=sys.stderr)
            ai_result = {"error": f"SSE processing error: {type(e).__name__}: {e}", "answer": "no", "ask": ""}
        finally:
            loading_flag[0] = False
            live_ref[0] = None
            # 恢复原始 SIGINT 处理器（避免影响后续操作）
            try:
                import signal as _sig_res
                _sig_res.signal(_sig_res.SIGINT, _original_sigint)
            except Exception:
                pass
        
        ai_result = process_ai_result_fields(ai_result)

        was_interrupted = ai_result.get("_interrupted", False)
        if was_interrupted:
            continue_asking = False  # don't auto-loop, but still process any commands below
        
        has_error = "error" in ai_result and ai_result["error"]
        has_txt = ai_result.get("txt", "").strip() if ai_result.get("txt") else False
        answer = ai_result.get("answer", "no")
        ai_ask = ai_result.get("ask", "") or ""
        tag = ai_result.get("tag", "") or ""
        memory_uuid = ai_result.get("memory", "") or ""
        # 计划文本来自 submit_plan 工具结果（标记语言已移除）
        plan_text = _pending_plan or ""
        tool_calls = ai_result.get("tool_calls", [])
        sleep_value = ai_result.get("sleep")
        class_level = ai_result.get("class", "1")

        # （标记语言已移除：无 [VIEW:]/[EDIT:] 文件标记执行、无 [SLEEP] 休眠、无 [MEMORY] 引用）
        
        if has_error:
            error_str = str(ai_result["error"])
            if "Request failed" in error_str or "Connection" in error_str or "timeout" in error_str.lower():
                console.print(lang_text["api_conn_fail"], style="bold red")
            else:
                console.print(f"❌ {lang_text['api_error'].format(error_str)}", style="bold red")
            if log_error:
                log_error(f"AI error: {error_str}", current_session_id)
            # ── 自愈：400 Invalid assistant message → 剔除坏消息并重试 ──
            # 思考截断轮可能留下 content 与 tool_calls 均为空的 assistant 消息，
            # 回传即被服务器拒绝（"content or tool_calls must be set"）。
            # 剔除后会话无需重启即可继续。
            if "Invalid assistant message" in error_str:
                try:
                    from .ai_lib.api import strip_empty_assistant_messages as _strip_empty_asst
                    _hist_healed, _healed_n = _strip_empty_asst(conversation_history)
                except Exception:
                    _hist_healed, _healed_n = conversation_history, 0
                if _healed_n:
                    conversation_history = _hist_healed
                    console.print(_mcp_t(
                        f"[bold yellow]🩹 自愈: 已剔除 {_healed_n} 条空 assistant 消息并重试[/]",
                        f"[bold yellow]🩹 Self-healed: removed {_healed_n} empty assistant message(s), retrying[/]"))
                    conversation_history.append({
                        "role": "system",
                        "content": _mcp_t(
                            "⚠️ 上轮请求因 assistant 消息缺少 content 被拒，系统已剔除坏消息并重试。继续当前任务。",
                            "⚠️ The previous request was rejected for an empty assistant message; "
                            "the system removed it and is retrying. Continue the task."),
                    })
                    continue_asking = True
                    continue
            # ── Layer 4 / Reactive：上下文超限报错 → 强制压缩一次并重试（最后保命兜底）──
            if _is_context_too_long_error(error_str) and not _reactive_compact_done:
                _reactive_compact_done = True
                # 从报错里学习实测窗口（服务器 400 返回窗口 → 重设阈值）
                _win_parsed = _parse_context_window_from_error(error_str)
                if _win_parsed:
                    _SESSION_CONTEXT_WINDOWS[current_session_id] = _win_parsed
                    console.print(
                        f"[dim]📏 已记录实测上下文窗口: {_win_parsed:,} tokens "
                        f"(自动压缩阈值 → {_effective_compact_threshold(current_session_id) // 1024}K)[/]")
                try:
                    _new_hist, _saved, _superseded, _old_len = _compact_conversation_history(
                        conversation_history,
                        user_home_dir=_mem_home,
                        session_id=current_session_id,
                    )
                    if _new_hist is not None:
                        conversation_history = _new_hist
                        from .ai_lib.api import bump_rewrite_version as _bump_react
                        _bump_react(current_session_id)
                        conversation_history.append({
                            "role": "system",
                            "content": _mcp_t(
                                "⚠️ 上轮请求因上下文超限失败，系统已强制压缩历史并重试。继续当前任务。",
                                "⚠️ The previous request failed with a context-length error; "
                                "the system force-compacted history and is retrying. Continue the task."),
                        })
                        console.print(
                            _mcp_t("[bold yellow]📦 应急压缩: 上下文超限 → 已强制压缩并重试[/]",
                                   "[bold yellow]📦 Emergency compact: context limit exceeded → force-compacted and retrying[/]"))
                        continue_asking = True
                        continue
                except Exception:
                    pass
            # ── 失败轮也记录提问（history 命令可查）：AI 报错≠问题没发生 ──
            if not message_appended and (last_user_question or "").strip():
                try:
                    append_message_to_chat(
                        _mem_home, current_chat_name, current_session_id,
                        last_user_question,
                        f"（AI 调用失败：{error_str[:200]}）", "", "1")
                    message_appended = True
                except Exception:
                    pass
            continue_asking = False
            continue
        
        if not message_appended and (has_txt or ai_ask):
            message_id = append_message_to_chat(
                _mem_home, current_chat_name, current_session_id,
                last_user_question, ai_result.get("txt", ""), tag, class_level
            )
            message_appended = True
            if debug_mode:
                debug_prefix = "[DEBUG] " if current_lang == "english" else "[DEBUG] "
                console.print(debug_prefix + f"Message appended: {message_id}", style="bold magenta")
        elif message_appended and tag:
            update_message_tag(_mem_home, current_chat_name, current_session_id, tag, class_level)
            if debug_mode:
                debug_prefix = "[DEBUG] " if current_lang == "english" else "[DEBUG] "
                console.print(debug_prefix + f"Tag updated: {tag[:50]}...", style="bold magenta")
        elif message_appended and answer == "yes":
            update_message_tag(_mem_home, current_chat_name, current_session_id, tag, class_level)
        
        # （[ASK] 标记交互流已移除：AI 提问走 choose_ask 工具，或作为普通文本由用户下一轮回复）
        
        # 如果已通过流式或 Live Panel 展示了 txt 内容，不再重复打印
        # _live_shown 在 Live 块内设为 True，避免 Live 结束后 console.print 再打一遍
        if has_txt and not _live_shown:
            console.print(render_ai_panel(ai_result["txt"].strip()))
        
        ai_commands = extract_ai_commands(ai_result)
        # 硬限制：最多执行 10 条命令，超出的丢弃并通知 AI
        if len(ai_commands) > 10:
            _discarded = ai_commands[10:]
            ai_commands = ai_commands[:10]
            _warn = _mcp_t("⚠️ 命令超过 10 条限制，已截断前 10 条执行",
                           "⚠️ Command limit of 10 exceeded, truncated to first 10")
            console.print(f"  [bold yellow]{_warn}[/]")
            conversation_history.append({"role": "system", "content": f"{_warn}。多余的 {len(_discarded)} 条命令被丢弃，请下一轮继续。"})
        # 命令执行摘要面板（[ANALYSIS] 标记已移除 — AI 分析直接走正文 Markdown）
        commands_summary = ""
        
        if ai_commands and not commands_summary:
            commands_summary = lang_text["analysis_cmd_prefix"].format(len(ai_commands))
            for idx, cmd in enumerate(ai_commands, 1):
                commands_summary += f"{idx}. {cmd}\n"
        
        if commands_summary:
            console.print(render_analysis_panel(commands_summary))
        
        # ── Token usage stats (from stream_options.include_usage) ──
        _usage_info = ai_result.get("_usage") if isinstance(ai_result, dict) else None
        if _usage_info:
            _total = _usage_info.get("total_tokens", 0)
            _prompt = _usage_info.get("prompt_tokens", 0)
            _completion = _usage_info.get("completion_tokens", 0)
            # ── Anthropic 兼容：字段名不同（input_tokens / output_tokens）──
            _input_t = _usage_info.get("input_tokens", 0)
            _output_t = _usage_info.get("output_tokens", 0)
            if not _prompt and _input_t:
                _prompt = _input_t
            if not _total:
                _total = _prompt + _completion
            if not _completion and _output_t:
                _completion = _output_t
                _total = _prompt + _completion
            # 兼容多种 API 的 cache token 字段名（DeepSeek / OpenAI / Anthropic）
            _cache_hit = (
                _usage_info.get("prompt_cache_hit_tokens", 0)
                or _usage_info.get("cache_hit_tokens", 0)
                or _usage_info.get("cache_read_input_tokens", 0)
                or _usage_info.get("prompt_tokens_details", {}).get("cached_tokens", 0)
            )
            _cache_miss = (
                _usage_info.get("prompt_cache_miss_tokens", 0)
                or _usage_info.get("cache_miss_tokens", 0)
                or _usage_info.get("cache_creation_input_tokens", 0)
            )
            if not _cache_miss and _prompt:
                _cache_miss = _prompt - _cache_hit
            # 存下精确 token 值（末尾显示用，纯磁盘架构不依赖内存 tracker）
            if _prompt:
                _thread_locals.last_prompt_tokens = _prompt
            _thread_locals.last_completion_tokens = _completion or 0
            _thread_locals.last_cache_hit = _cache_hit or 0
            _thread_locals.last_cache_miss = _cache_miss or 0
            # 累计 token（供交互式会话 /tokens /stats 使用）
            if not hasattr(_thread_locals, 'session_total_prompt'):
                _thread_locals.session_total_prompt = 0
                _thread_locals.session_total_completion = 0
                _thread_locals.session_total_cache_hit = 0
            _thread_locals.session_total_prompt += _prompt
            _thread_locals.session_total_completion += _completion
            _thread_locals.session_total_cache_hit += _cache_hit
            _thread_locals.session_round_count = getattr(_thread_locals, 'session_round_count', 0) + 1
            _cache_supported = ai_result.get("_cache_supported", True) if isinstance(ai_result, dict) else True
            parts = [f"⚡ {_total} tokens"]
            if _cache_supported:
                if _cache_hit:
                    saved_pct = _cache_hit / (_cache_hit + _cache_miss) * 100 if (_cache_hit + _cache_miss) else 0
                    _cached_str = f"{_cache_hit:,} cached / {_cache_miss:,} new"
                    parts.append(f"💰 cache {saved_pct:.1f}% hit ({_cached_str})")
                else:
                    parts.append("💰 cache 0% hit")
            else:
                parts.append("💰 cache n/a (platform)")
            console.print(f"  [dim]{' · '.join(parts)}[/]")
        else:
            # 无 _usage 时回退：估算 token 数
            _parts = []
            if conversation_history:
                _total_chars = sum(len(str(m.get("content", ""))) for m in conversation_history)
                _est = _total_chars // 3 + 1500
                _parts.append(f"⚡ ~{_est} tokens")
            if _parts:
                console.print(f"  [dim]{' · '.join(_parts)}[/]")
        
        # ---- Plan 确认流程（纯引导模式）----
        if plan_text and plan_text.strip():
            _plan_display = plan_text
            try:
                _plan_json = json.loads(plan_text)
                if isinstance(_plan_json, dict):
                    _plan_display = _plan_json.get("plan", plan_text)
            except (json.JSONDecodeError, TypeError):
                pass

            _ensure_library_record()
            plan_choice = confirm_plan(_plan_display, lang_text)

            if plan_choice == "discard":
                console.print(lang_text.get("plan_discarded", "🗑️ 计划已摒弃，将通知 AI 重新规划"), style="bold yellow")
                conversation_history.append({"role": "user", "content": "用户摒弃了你的计划，请重新制定"})
                _user_input_round = True  # 用户操作了计划
                _pending_plan = ""
                continue_asking = True
                continue

            elif plan_choice == "guide":
                console.print(lang_text.get("plan_guide_prompt",
                    "💡 请输入你对计划的修改意见：" if current_lang == "chinese" else "💡 Enter your revision to the plan:"), style="bold cyan")
                try:
                    guide_text = ui_text_input("💡 修改意见" if current_lang == "chinese" else "💡 Revision").strip()
                except (KeyboardInterrupt, EOFError):
                    guide_text = ""
                    console.print()
                if guide_text:
                    conversation_history.append({"role": "user", "content": f"用户对计划的指导意见：\n{guide_text}\n\n请根据指导意见修改计划。"})
                else:
                    conversation_history.append({"role": "user", "content": "用户未提供具体意见，请简化或重新生成计划"})
                _user_input_round = True  # 用户输入了计划意见
                _pending_plan = ""
                continue_asking = True
                continue

            elif plan_choice == "confirm":
                console.print(lang_text.get("plan_confirmed", "✅ 计划已确认，即将进入执行阶段"), style="bold green")
                _write_budget = 0  # 计划已确认：写入预算清零，规划门禁放行
                conversation_history.append({"role": "user", "content": "用户已确认计划，请按步骤开始执行"})
                _user_input_round = True  # 用户确认了计划
                # ── 不再截断 submit_plan 工具结果 ──
                # 原逻辑确认后把历史中部的计划正文改写为短标记以省 token，
                # 但中间消息内容变化会让 DeepSeek 前缀缓存从该处起整体 miss，
                # 后续每轮重新发送其后全部消息（成本远高于计划正文按 cache-hit 价回传）。
                # 计划正文保留在历史中，每轮以缓存命中价（约 1/10）回传，前缀保持稳定。
                _pending_plan = ""
                plan_confirmed = True
                continue_asking = True
                continue

        # Plan 模式安全限制：未确认计划前，拦截非计划类命令和工具调用
        # 既支持 mode=="plan"（用户输入 ai plan），也支持 _PLAN_MODE_ACTIVE（AI 调用 EnterPlanMode）
        # 前 2 轮交互不拦截，让 AI 有机会探索代码库并生成计划
        # ⚠️ 注意：submit_plan / mark_step_complete / ExitPlanMode / choose_ask
        # 是 AI 在 plan 模式下唯一能用的工具，不能拦截它们
        _plan_tools = {"submit_plan", "mark_step_complete", "ExitPlanMode", "choose_ask"}
        if (mode == "plan" or _PLAN_MODE_ACTIVE) and not plan_confirmed and interaction_count > 2:
            # 只拦截 ai_commands（shell 命令），不拦截 plan 类工具调用
            _non_plan_calls = [tc for tc in tool_calls if tc.get("name", "") not in _plan_tools]
            if ai_commands or _non_plan_calls:
                console.print(lang_text.get("plan_blocked",
                    "⛔ Plan 模式：AI 命令/工具调用已被拦截。请先确认计划。"), style="bold red")
                ai_commands = []
                tool_calls = []
                # ── 连续拦截计数：>=2 次时直接询问用户，杜绝无提示的无限 API 循环 ──
                # 原逻辑每轮只追加一条 system 消息后 continue 重新调 API，
                # AI 若坚持调用被拦截的工具会无限循环（记忆每轮膨胀 + 白烧 token）。
                _plan_block_count += 1
                if _plan_block_count >= 2:
                    from .ai_lib.ui import select_option as _plan_select
                    try:
                        _fm_choice = _plan_select(
                            message=_mcp_t(
                                "⚠️ AI 在 Plan 模式下反复尝试执行被拦截的命令/工具。是否手动切换至自由模式（正常执行模式）？",
                                "⚠️ AI keeps attempting blocked commands/tools in Plan mode. Switch to free mode (normal execution)?",
                            ),
                            options=[
                                _mcp_t("🔄 切换至自由模式", "🔄 Switch to free mode"),
                                _mcp_t("📋 留在 Plan 模式", "📋 Stay in Plan mode"),
                            ],
                            default=_mcp_t("🔄 切换至自由模式", "🔄 Switch to free mode"),
                        )
                    except Exception:
                        _fm_choice = ""
                    _fm_exit = bool(_fm_choice) and (
                        "自由模式" in _fm_choice or "free mode" in _fm_choice.lower())
                    if _fm_exit:
                        # 用户确认切换 → 系统直接解除 plan 模式（mode=="plan" 时
                        # ExitPlanMode 工具无效，只能由系统切换局部变量）
                        _PLAN_MODE_ACTIVE = False
                        mode = "normal"
                        console.print(_mcp_t(
                            "🔄 已切换到自由模式，解除 Plan 模式拦截。",
                            "🔄 Switched to free mode, plan mode restrictions lifted."),
                            style="bold green")
                        conversation_history.append({"role": "user", "content": _mcp_t(
                            "用户已手动切换至自由模式，请继续执行任务。",
                            "User manually switched to free mode. Please continue the task.")})
                        _user_input_round = True  # 用户切换了模式
                        _plan_block_count = 0
                        continue  # 下一轮 mode==normal，不再拦截
                    # 留在 Plan 模式：重置计数并再次明确要求 AI 只提交计划
                    _plan_block_count = 0
                    conversation_history.append({"role": "user", "content": _mcp_t(
                        "用户选择留在 Plan 模式。请不要调用任何工具，立即用 submit_plan 提交计划。",
                        "User chose to stay in Plan mode. Do not call any tools; submit your plan with submit_plan now.")})
                    _user_input_round = True  # 用户选择了留在 plan 模式
                # 告诉 AI 为什么被拦 + 应该怎么做，然后自动继续让 AI 响应
                # 引导 AI 先调用 choose_ask 询问用户是否手动切换到自由模式
                conversation_history.append({
                    "role": "system",
                    "content": _mcp_t(
                        "[Plan 模式] 你的命令/工具调用已被拦截，因为你尚未提交计划。"
                        "请立即调用 choose_ask 询问用户：是否手动切换到自由模式（正常执行模式）？"
                        "选项必须是 [\"继续 Plan 模式并提交计划\", \"切换到自由模式\"]。"
                        "如果用户选择切换到自由模式，系统会自动解除 plan 模式；"
                        "否则请使用 submit_plan 工具提交你的计划。"
                        "用户会审核并确认后，才能进入执行阶段。不要再次调用被拦截的命令/工具。",
                        "[Plan mode] Your commands/tools were blocked because you haven't submitted a plan yet. "
                        "Immediately call choose_ask to ask the user: do you want to manually switch to free mode (normal execution mode)? "
                        "Options must be [\"Stay in Plan mode and submit a plan\", \"Switch to free mode\"]. "
                        "If the user chooses to switch to free mode, the system will lift plan mode automatically; "
                        "otherwise submit your plan with the submit_plan tool. "
                        "The user will review and confirm before execution is allowed. "
                        "Do NOT retry blocked commands/tools."
                    )
                })
                # 清空流式状态，准备下一轮 API 调用（让 AI 看到拦截消息并响应）
                continue  # 跳回循环顶部，用更新后的 conversation_history 重新调 API
        
        # ── 工具结果收集器（仅 function calling）──
        tool_results = []

        # ── 工具结果追加到 conversation_history（让 AI 立刻看到）──
        if tool_results and not tool_calls:
            _native_feedback = "\n".join(tool_results)
            if _native_feedback.strip():
                conversation_history.append({"role": "system", "content": f"工具执行结果：\n{_native_feedback.strip()}"})

        # 处理 AI 工具调用（原生 function calling）
        if tool_calls:
            try:
                # ── Agent / web_search 并行预派发：同一轮多个独立调用先全部丢进后台线程 ──
                # 主 AI 常发多个独立的 Agent 或 web_search 调用（各自 sync），逐个执行会
                # 严格串行（第一个阻塞到完成，第二个才开始）。预派发后主循环 join 取结果，
                # 总耗时 = max(各调用) 而非 sum；结果仍按 tool_calls 顺序回填。
                # web_search 为 ReadOnly（自动放行），后台线程执行无确认交互风险。
                _agent_futures = {}
                _agent_out = {}
                for _ai, _atc in enumerate(tool_calls):
                    if _atc.get("name") not in ("Agent", "web_search"):
                        continue
                    _ap_str = _atc.get("params_str", "")
                    _ap = {}
                    try:
                        if _ap_str.strip().startswith("{"):
                            _ap = json.loads(_ap_str.strip())
                        else:
                            _ap = _parse_tool_params(_ap_str, _atc.get("body", ""))
                    except Exception:
                        _ap = {}

                    def _agent_worker(_idx, _name, _params):
                        try:
                            _a_ok, _a_out = execute_mcp_tool(
                                _name, _params, "filesystem", _current_user_mode,
                                path_validator=_mcp_path_validator)
                        except Exception as _e:
                            _a_ok, _a_out = False, f"tool execution error: {_e}"
                        _agent_out[_idx] = (_a_ok, _a_out)

                    _t = threading.Thread(target=_agent_worker,
                                          args=(_ai, _atc.get("name"), _ap), daemon=True)
                    _t.start()
                    _agent_futures[_ai] = _t
                _tc_pending = list(tool_calls)
                _write_budget = 0  # 规划门禁：本轮累计写入字节（plan 确认后清零）
                while _tc_pending:
                    tc = _tc_pending.pop(0)
                    _tc_i = len(tool_calls) - len(_tc_pending) - 1
                    tool_name = tc.get("name", "")
                    tool_params_str = tc.get("params_str", "")
                    tool_body = tc.get("body", "")

                    # ── 中断检查：如果 Ctrl+C 已按下，跳过工具执行 ──
                    if _AI_INTERRUPTED:
                        raise KeyboardInterrupt("User interrupted")

                    # 每次工具调用都重新执行（去掉缓存去重，避免 get_file_info 等读操作返回过期结果）
                    # 显示绿色工具调用提示（去前缀）
                    _tool_display_name = tool_name
                    if _tool_display_name.startswith("mcp__"):
                        _tool_display_name = _tool_display_name.rsplit("__", 1)[-1]
                    elif _tool_display_name.startswith("mcp_"):
                        _tool_display_name = _tool_display_name[4:]
                    # 解析参数（JSON优先 → _parse_tool_params 回退）
                    if tool_params_str.strip().startswith("{"):
                        try:
                            params = json.loads(tool_params_str.strip())
                        except (json.JSONDecodeError, ValueError):
                            # JSON 非法 → 反馈 schema 引导 AI 重发
                            # 查找该工具期望的参数 schema
                            _tool_schema_hint = ""
                            try:
                                _schema_tools, _ = get_native_tools_cached(user_home_dir, _mcp_enabled)
                                for _t in _schema_tools:
                                    if _t.get("function", {}).get("name", "") == tool_name:
                                        _props = _t["function"]["parameters"].get("properties", {})
                                        _req = _t["function"]["parameters"].get("required", [])
                                        _hint_items = []
                                        for _pk, _pv in _props.items():
                                            _req_flag = ("(必填)" if _pk in _req else "(可选)") if current_lang == "chinese" else ("(required)" if _pk in _req else "(optional)")
                                            _p_type = _pv.get("type", "string")
                                            _hint_items.append(f"  \"{_pk}\": <{_p_type}> {_req_flag}")
                                        if _hint_items:
                                            _tool_schema_hint = "\n期望参数:\n" + "\n".join(_hint_items)
                                        break
                            except Exception:
                                pass
                            _err_schema = f"❌ JSON parse failed for {tool_name}. Arguments must be valid JSON. 原因可能是内容过长被截断，或者字符串中含有未转义的大括号 {{{{}}}}/}}}}。请缩短 content 后重试，或检查 JSON 格式。{_tool_schema_hint}"
                            tool_results.append(_err_schema)
                            console.print(f"   {_err_schema}", style="bold red")
                            continue
                    else:
                        params = _parse_tool_params(tool_params_str, tool_body)

                    # 显示工具调用 + 关键参数（path、pattern、command 等）
                    _param_preview = ""
                    for _key in ("path", "pattern", "uuid", "task_id", "cron_id", "team_id", "query", "url", "name", "prompt", "command"):
                        _val = params.get(_key, "")
                        if _val:
                            # 路径/参数完整显示，绝不截断 —— 用户需要看到具体改的是哪个文件
                            _param_preview = f" {_key}={_val}"
                            break
                    # Agent 多任务：一次调用可并行派发 N 个子代理（tasks 数组或 count>1），
                    # 在工具名上标出数量，避免视觉上像只有一个。
                    _agent_n = 0
                    if tool_name == "Agent":
                        _t_list = params.get("tasks")
                        if isinstance(_t_list, list) and _t_list:
                            _agent_n = len(_t_list)
                        else:
                            try:
                                _agent_n = max(1, int(params.get("count", 1) or 1))
                            except (TypeError, ValueError):
                                _agent_n = 1
                    # 只有工具名带 mcp_/mcp__ 前缀的才是真正的 MCP 工具
                    # （build_native_tools 统一命名为 mcp_<tool>，见 mcp_prefixed）。
                    # 其余一律是内置工具，不标 MCP。
                    _tag = " [MCP]" if tool_name.startswith("mcp_") else ""
                    _agent_mark = f" ×{_agent_n}" if _agent_n > 1 else ""
                    console.print(f"  [bold green]🔧 {_tool_display_name}{_agent_mark}{_tag}[/]{_param_preview}")

                    # 流式执行：用 Status spinner 展示工具运行过程
                    # 交互式工具（choose_ask 选项菜单 + 自由输入框）不包 spinner：
                    # Status 持续重绘会与 InquirerPy 的终端输入界面冲突，输入框被
                    # 转圈覆盖，用户只能看到"⏳ 运行中…"而看不到真正的输入框。
                    # 交互工具直接执行，让菜单和输入框独立渲染。
                    # RunCommand 同样不包：危险命令需弹确认框，spinner 会遮挡 y/N 提示。
                    _status_started = False
                    if tool_name not in ("choose_ask", "RunCommand"):
                        from rich.status import Status as _RichStatus
                        _status = _RichStatus(_mcp_t(f"  [dim]⏳ {_tool_display_name} 运行中…[/]", f"  [dim]⏳ {_tool_display_name} running…[/]"), spinner="dots", console=console)
                        _status.start()
                        _status_started = True

                    # ── 规划门禁（大小感知）：未确认计划时拦截大型/破坏性操作，引导 submit_plan ──
                    _gate_msg = ""
                    if not plan_confirmed and mode != "plan":
                        _gated_flag, _write_budget = plan_gate_blocked(
                            tool_name, params, plan_confirmed, _write_budget, mode)
                        if _gated_flag:
                            _gate_msg = (
                                "⛔ 规划门禁：该操作需要先提交计划并获用户确认"
                                "（大型写操作或破坏性操作）。请调用 submit_plan 提交计划。"
                            )
                            console.print(f"  [bold yellow]🔒 {_gate_msg}[/]")
                    try:
                        # Agent 工具：挂载 Status 引用，供 _exec_agent 同步等待时刷新灰色活动尾行
                        if tool_name == "Agent":
                            global _SUBAGENT_STATUS
                            _SUBAGENT_STATUS = _status if _status_started else None
                        if _gate_msg:
                            # 规划门禁拦截：不执行，结果回传 AI 引导规划
                            ok, output = False, _gate_msg
                        elif tool_name == "Agent" and _tc_i in _agent_futures:
                            # Agent：join 预派发线程取并行结果（顺序不变）
                            try:
                                _agent_futures[_tc_i].join()
                            except Exception:
                                pass
                            ok, output = _agent_out.get(_tc_i, (False, "Agent 结果丢失"))
                        else:
                            # 先尝试内置 handler，走不通再走 MCP
                            ok, output = execute_mcp_tool(tool_name, params, "filesystem", _current_user_mode,
                                                          path_validator=_mcp_path_validator)
                        # ── 采集工具结果 ──
                        if ok and tool_name in LIB_CAPTURE_TOOLS:
                            try:
                                from .ai_lib.storage import capture_tool_result
                                capture_tool_result(tool_name, params, output)
                            except Exception:
                                pass
                    finally:
                        if _status_started:
                            _status.stop()
                        if tool_name == "Agent":
                            _SUBAGENT_STATUS = None

                    # ── Plan 模式：用户通过 choose_ask 选择切换到自由模式 → 记录待系统处理 ──
                    # mode=="plan" 时 ExitPlanMode 工具无法解除（mode 是局部变量），
                    # 用户明确选择后由系统在工具结果追加完成后统一切换，避免在
                    # assistant(tool_calls) 与 tool 结果之间插入 system 消息破坏消息序列。
                    if ok and tool_name == "choose_ask" and (
                            "自由模式" in output or "free mode" in output.lower()):
                        _plan_free_mode_chosen = True

                    # ── 风暴检测 + 恢复配方 ──
                    _tc_key = f"mcp:{tool_name}:{tool_params_str[:80]}"
                    if not ok:
                        _storm_counter[_tc_key] = _storm_counter.get(_tc_key, 0) + 1
                        _repeat_success.pop(_tc_key, None)
                        _fail_count = _storm_counter[_tc_key]
                        if _fail_count >= 2:
                            # 分类故障 + 生成恢复建议
                            _scenario = classify_failure(tool_name, output)
                            _recovery_msg = get_recovery_message(_scenario, _RECOVERY_CTX)
                            if _recovery_msg:
                                conversation_history.append({"role": "system", "content": _recovery_msg})
                                console.print(f"  [bold yellow]🔁 {_recovery_msg}[/]")
                                record_attempt(_RECOVERY_CTX, _scenario, RecoveryAction.SWITCH_STRATEGY, False)
                        if _fail_count >= 3:
                            _storm_warn = _mcp_t(
                                f"⚠️ 风暴检测：{tool_name} 连续失败 {_fail_count} 次，AI 应更换策略",
                                f"⚠️ Storm detected: {tool_name} failed {_fail_count}x, AI should switch strategy"
                            )
                            console.print(f"  [bold red]{_storm_warn}[/]")
                            conversation_history.append({"role": "system", "content": _storm_warn})
                    else:
                        _storm_counter.pop(_tc_key, None)

                    if ok:
                        # 截断超大工具结果，防止上下文撑爆
                        if len(output) > _MAX_TOOL_OUTPUT:
                            output = output[:_MAX_TOOL_OUTPUT // 2] + f"\n\n…[truncated {len(output) - _MAX_TOOL_OUTPUT} bytes of {len(output)} total]…\n\n" + output[-_MAX_TOOL_OUTPUT // 2:]
                        tool_results.append(output)
                        # 灰字显示简短结果
                        if tool_name == "Agent" and ("】总结】" in output or "】失败】" in output):
                            # 多任务 Agent：按「【…子代理「…」总结/失败】」分栏逐条显示，
                            # 避免多个子代理的结果挤在一个 100 字符截断里，视觉上像只有一个。
                            _blocks = re.split(r"(?=\n?【[^】]*子代理「[^」]*」(?:总结|失败)】)", output)
                            for _blk in _blocks:
                                _blk = _blk.strip()
                                if not _blk:
                                    continue
                                _blk_short = _blk[:100] + ("..." if len(_blk) > 100 else "")
                                console.print(f"   → {_blk_short}", style="dim")
                        else:
                            short = output[:100] + ("..." if len(output) > 100 else "")
                            console.print(f"   → {short}", style="dim")
                    else:
                        err_msg = _mcp_t(f"❌ 工具执行失败: {output}", f"❌ Tool execution failed: {output}")
                        tool_results.append(err_msg)
                        console.print(f"   {err_msg}", style="bold red")

            except KeyboardInterrupt:
                # Ctrl+C 强制打断工具执行
                _AI_INTERRUPTED = True
                console.print(_mcp_t("\n  [bold red]⏹ 用户中断工具执行[/]", "\n  [bold red]⏹ Tool execution interrupted by user[/]"))
                # 终止所有 MCP 子进程并同步清理注册表（防 stale；健康检查下次直接重建）
                for _name, _proc in list(MCP_SERVER_PROCESSES.items()):
                    try:
                        _proc.terminate()
                    except Exception:
                        pass
                    MCP_SERVER_PROCESSES.pop(_name, None)
                # 补齐 tool_results 长度，确保与 tool_calls 一一对应
                # 避免 "assistant 有 tool_calls 但缺少 tool 消息" 的 API 错误
                while len(tool_results) < len(tool_calls):
                    tool_results.append("⏹ 用户中断，该工具未执行")
                # 用户要求：Ctrl+C 只打断当前工具，不停止 AI 闭环——
                # 复位中断标志并把"工具被中断"结果回传 AI，让 AI 调整策略继续
                _reset_ai_interrupt_flags()
                continue_asking = True
                _tool_calls_processed_this_round = True

            # ── 提取 submit_plan / mark_step_complete 结果 ──
            # submit_plan 完整内容保留在 conversation_history 中（AI 首次需要看到），
            # 同时存入 _pending_plan 供面板显示。确认后由 plan 流程截断。
            for _tc_idx, _tc in enumerate(tool_calls):
                _tc_name = _tc.get("name", "")
                if _tc_name.endswith("submit_plan"):
                    if _tc_idx < len(tool_results) and tool_results[_tc_idx]:
                        _pending_plan = tool_results[_tc_idx]
                    break

            # ── 追加工具调用结果到 conversation_history ──
            # 无论来源是 MCP tool_calls，
            # 结果必须回传给 AI，否则 AI 不知道自己操作已生效，会反复重试
            if tool_calls:
                # 标准 OpenAI/DeepSeek 工具调用格式：
                # assistant: tool_calls → tool: 结果
                # 缓存字节一致性：id 与 arguments 必须原样回传 API 原始字节。
                # 重写 id / load→dumps 重排 arguments 会让回显消息与上一轮
                # API 输出不一致 → DeepSeek 完整前缀单元无法命中 → 缓存断裂。
                import json as _json
                tc_ids = []
                _tool_call_items = []
                for i, tc in enumerate(tool_calls):
                    # fallback id 用 uuid：interaction_count 在 REPL 跨 turn 会重置，
                    # 固定前缀会生成重复 id（assistant tool_calls 与 tool 消息引用冲突）
                    _raw_id = tc.get("id") or f"call_{uuid.uuid4().hex[:10]}"
                    tc_ids.append(_raw_id)
                    _raw_args = tc.get("raw_arguments")
                    if not _raw_args:
                        _raw_args = tc.get("params_str", "{}")
                    _tool_call_items.append({
                        "id": _raw_id,
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": _raw_args,
                        }
                    })
                _reasoning = ai_result.get("_reasoning", "")
                _assistant_msg = {
                    "role": "assistant",
                    "content": None,  # DeepSeek thinking mode 要求 tool_call 时 content 为 null
                    "tool_calls": _tool_call_items,
                    "reasoning_content": _reasoning,  # thinking 模式必须回传，空串也保留
                }
                conversation_history.append(_assistant_msg)
                # tool role 结果消息
                # 安全垫：确保 tool_results 长度与 tool_calls 一致，避免 API 报错
                _tool_results_safe = list(tool_results)
                while len(_tool_results_safe) < len(tc_ids):
                    _tool_results_safe.append("⚠️ 该工具未执行（结果丢失）")
                for i, res in enumerate(_tool_results_safe):
                    # ── 工具结果截断（32KB head+tail） ──
                    try:
                        from .ai_lib.tool_results import truncate_tool_output, is_error_result
                        _is_err = is_error_result(res)
                        _trunc = truncate_tool_output(res, 32 * 1024)
                        if _is_err:
                            _trunc = "error: " + _trunc
                    except Exception:
                        _trunc = res
                        _is_err = False
                    _is_plan = tool_calls[i].get("name", "").endswith("submit_plan") if i < len(tool_calls) else False
                    conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tc_ids[i],
                        "content": _trunc,
                        "is_error": _is_err,
                        "is_plan_result": _is_plan,
                    })

            # ── Plan 模式：用户已选择切换自由模式 → 系统解除 plan 模式并通知 AI ──
            if _plan_free_mode_chosen:
                _plan_free_mode_chosen = False
                _PLAN_MODE_ACTIVE = False
                mode = "normal"
                console.print(_mcp_t(
                    "🔄 用户已选择切换到自由模式，Plan 模式已解除。",
                    "🔄 User chose free mode; Plan mode lifted."), style="bold green")
                conversation_history.append({"role": "system", "content": _mcp_t(
                    "用户已选择切换到自由模式，plan 模式已解除。现在可以正常执行命令和工具。",
                    "User chose to switch to free mode; plan mode lifted. "
                    "You may execute commands and tools normally.")})
            # 原生标记语言结果由 library 记忆系统持久化（record_ai_session），
            # 下一轮 build_memory_context 从磁盘加载注入提示词。
            # 不嵌入 AI 回复，保持流式输出纯净。

            # 写入 library 磁盘（Markdown格式，仅记录用途）
            if tool_results:
                _now_str = datetime.now().strftime('%H:%M:%S')
                _log_lines = [f"### 第 {interaction_count} 轮工具调用 ({_now_str})", ""]
                _res_idx = 0
                for tc in tool_calls:
                    _tn = tc.get("name", "?")
                    _res = tool_results[_res_idx] if _res_idx < len(tool_results) else "(无结果)"
                    _res_idx += 1
                    # MemoryRead/MemorySearch 结果只保留摘要行，正文不嵌入（防止多层套娃）
                    if _tn in ("MemoryRead", "MemorySearch") and _res:
                        _lines = _res.split("\n")
                        _summary_lines = []
                        for _l in _lines:
                            if _l.startswith("📄") or _l.startswith("📋") or _l.startswith("🔍"):
                                _summary_lines.append(_l)
                                break
                        if _summary_lines:
                            _res = _summary_lines[0] + "\n_(正文已省略，使用 MemoryRead 重新查询)_"
                    # RunCommand 结构化展示：命令 / 退出码 / 执行结果 分栏（普通人可读）
                    if _tn == "RunCommand":
                        _cmd_shown = ""
                        try:
                            _tc_params = json.loads(tc.get("params_str") or "{}")
                            _cmd_shown = str(_tc_params.get("command", "")).strip()
                        except Exception:
                            _cmd_shown = ""
                        _rc_shown, _out_shown = "", _res
                        if isinstance(_res, str) and _res.startswith("命令: "):
                            _n1 = _res.find("\n")
                            _n2 = _res.find("\n", _n1 + 1) if _n1 >= 0 else -1
                            if _n1 > 0 and _n2 > 0 and _res[_n1 + 1:_n2].startswith("退出码: "):
                                _rc_shown = _res[_n1 + 1:_n2][len("退出码: "):].strip()
                                _out_shown = _res[_n2 + 1:]
                                if _out_shown.startswith("执行结果:"):
                                    _out_shown = _out_shown[len("执行结果:"):].lstrip("\n")
                                _out_shown = _out_shown.strip("\n")
                        _log_lines.append(f"- **工具**: `{_tn}`")
                        if _cmd_shown:
                            _log_lines.append(f"  - **命令**: `{_cmd_shown}`")
                        if _rc_shown:
                            _log_lines.append(f"  - **退出码**: {_rc_shown}")
                        _log_lines.append(f"  - **执行结果**:")
                        _log_lines.append(f"    ```")
                        _out_body = _out_shown or "(无输出)"
                        for _l in _out_body.split("\n"):
                            _log_lines.append(f"    {_l}")
                        _log_lines.append(f"    ```")
                        continue
                    # web_search 结构化展示：查询参数一目了然（用户能看到 AI 查了什么）
                    if _tn == "web_search":
                        try:
                            _tc_params = json.loads(tc.get("params_str") or "{}")
                        except Exception:
                            _tc_params = {}
                        _parts = []
                        _act = str(_tc_params.get("action") or "mixed")
                        _q = str(_tc_params.get("query", "") or "").strip()
                        if _q:
                            _parts.append(f"查询: {_q}")
                        _qs = [str(x) for x in (_tc_params.get("queries") or []) if str(x).strip()]
                        if _qs:
                            _parts.append(f"附加查询 {len(_qs)} 个")
                        _urls = [str(u) for u in (_tc_params.get("urls") or []) if str(u).strip()]
                        if _urls:
                            _parts.append(f"抓取 URL {len(_urls)} 个")
                        _eng = _tc_params.get("engines") or ["duckduckgo", "bing"]
                        _parts.append(f"引擎: {','.join(str(e) for e in _eng)}")
                        if _tc_params.get("allowed_domains"):
                            _parts.append(f"仅限: {','.join(str(d) for d in _tc_params['allowed_domains'])}")
                        if _tc_params.get("exclude_domains"):
                            _parts.append(f"排除: {','.join(str(d) for d in _tc_params['exclude_domains'])}")
                        if _tc_params.get("time_range"):
                            _parts.append(f"时效: {_tc_params['time_range']}")
                        if _tc_params.get("safe_search"):
                            _parts.append("安全搜索")
                        if _tc_params.get("fetch_pages"):
                            _parts.append(f"抓页({_tc_params.get('fetch_limit', 3)})")
                        if _tc_params.get("max_results"):
                            _parts.append(f"每引擎{_tc_params['max_results']}条")
                        _log_lines.append(f"- **工具**: `{_tn}`")
                        if _parts:
                            _log_lines.append(f"  - **参数**: " + "；".join(_parts))
                        _log_lines.append(f"  - **执行结果**:")
                        _log_lines.append(f"    ```")
                        _res_body = _res if isinstance(_res, str) else str(_res or "(无结果)")
                        for _l in _res_body.split("\n")[:60]:
                            _log_lines.append(f"    {_l}")
                        if len(_res_body.split("\n")) > 60:
                            _log_lines.append(f"    …(结果过长，已截断)")
                        _log_lines.append(f"    ```")
                        continue
                    _log_lines.append(f"- **工具**: `{_tn}`")
                    # 通用调用参数摘要（让用户看到 AI 调用的参数，避免"不知道查了什么"）
                    _param_summary = ""
                    try:
                        _p_show = json.loads(tc.get("params_str") or "{}")
                        if _p_show:
                            _param_summary = json.dumps(_p_show, ensure_ascii=False)[:150]
                    except Exception:
                        _param_summary = ""
                    if _param_summary:
                        _log_lines.append(f"  - **调用参数**: `{_param_summary}`")
                    _log_lines.append(f"  ```")
                    _log_lines.append(f"  {_res}")
                    _log_lines.append(f"  ```")
                _log_text = "\n".join(_log_lines)
                # 顺序策略：工具结果先缓存，等「交互记录」（用户提问+AI回复）落盘后再 flush，
                # 保证文件里顺序为「交互记录 → 该次交互的过程（工具结果）」，而非倒序。
                _pending_tool_logs.append(_log_text)

        # ── AI 纯文本回复 → 追加 assistant 消息 ──
        _ai_txt = (ai_result.get("txt", "") or "").strip()
        _reasoning = ai_result.get("_reasoning", "")
        if _should_append_reply_assistant(_ai_txt, tool_calls):
            _assistant_msg = {
                "role": "assistant",
                "content": _ai_txt or None,
                "reasoning_content": _reasoning,  # thinking 模式必须回传
            }
            conversation_history.append(_assistant_msg)
            # ── 消费后压缩：AI 已看到工具结果并产出回复 → 旧结果压缩为摘要 ──
            # 减少每轮请求携带的未缓存增量（32KB 级 → KB 级）；压缩会改写
            # 历史中段，归因为 log_rewrite 而非"异常断裂"。
            try:
                if compact_consumed_tool_results(conversation_history):
                    from .ai_lib.api import bump_rewrite_version as _bump_compact
                    _bump_compact(current_session_id)
            except Exception:
                pass

        # ── 标记本轮已处理工具调用（命令的标记在 cmd_results 之后设置）──
        _tool_calls_processed_this_round = bool(tool_calls)
        
        cmd_results = {}
        
        if ai_commands and auto_exec:
            dangerous_cmds_found = []
            for cmd in ai_commands:
                is_danger, cmd_name = is_dangerous_command(cmd, dangerous_commands)
                if is_danger:
                    dangerous_cmds_found.append((cmd, cmd_name))
            
            original_cmd_count = len(ai_commands)
            
            if dangerous_cmds_found:
                # 上下文分级 + 特别高危清单（批量场景与单条 RunCommand 同一策略）
                try:
                    _batch_ctx_tokens = _estimate_conversation_tokens(conversation_history, current_session_id)
                except Exception:
                    _batch_ctx_tokens = 0
                confirmed_commands = []
                for cmd, cmd_name in dangerous_cmds_found:
                    _extra_danger, _extra_pattern = is_extra_dangerous_command(cmd, extra_dangerous_commands)
                    confirmed, user_response, refuse_reason = confirm_dangerous_command(
                        cmd, cmd_name, lang_text, current_session_id, initial_question, interaction_count, log_info,
                        context_tokens=_batch_ctx_tokens,
                        extra_dangerous=_extra_danger,
                    )
                    if confirmed:
                        confirmed_commands.append(cmd)
                    else:
                        if log_info:
                            log_info(f"Dangerous command rejected by user: {cmd}, reason: {refuse_reason}", current_session_id)
                        refuse_prefix = "❌ User rejected dangerous command" if current_lang == "english" else "❌ 用户拒绝执行危险命令"
                        user_refuse_reasons.append(f"{refuse_prefix} [{cmd_name}]: {cmd}\n   Rejection reason: {refuse_reason}" if current_lang == "english" else f"❌ 用户拒绝执行危险命令 [{cmd_name}]: {cmd}\n   拒绝原因: {refuse_reason}")
                        # 告知 AI 用户拒绝了它的命令
                        conversation_history.append({
                            "role": "system",
                            "content": _mcp_t(
                                f"用户拒绝了你的命令：{cmd[:200]} 原因：{refuse_reason}。请换一种方式。",
                                f"User rejected your command: {cmd[:200]} Reason: {refuse_reason}. Please try a different approach."
                            )
                        })
                
                safe_commands = [cmd for cmd in ai_commands if not is_dangerous_command(cmd, dangerous_commands)[0]]
                ai_commands = confirmed_commands + safe_commands
            
            if mode == "adv_code" and ai_commands:
                allowed_cmds = []
                for cmd in ai_commands:
                    if has_forbidden_syntax(cmd):
                        refuse_reason = lang_text["adv_code_rejected_syntax"].format(cmd)
                        user_refuse_reasons.append(refuse_reason)
                        if log_info:
                            log_info(f"Adv_code mode rejected command with forbidden syntax: {cmd}", current_session_id)
                        console.print(f"⚠️ {refuse_reason}", style="bold yellow")
                        # 告知 AI 命令因语法被拒
                        conversation_history.append({
                            "role": "system",
                            "content": _mcp_t(
                                f"你的命令包含被禁止的语法，已被拦截：{cmd[:200]}",
                                f"Your command contains forbidden syntax and was blocked: {cmd[:200]}"
                            )
                        })
                    else:
                        allowed_cmds.append(cmd)
                ai_commands = allowed_cmds
                if not ai_commands and original_cmd_count > 0:
                    console.print(lang_text["adv_code_all_rejected"], style="bold yellow")
                             
            save_ai_commands(_mem_home, ai_commands)

            # 过滤自然语言：字母数字占比 < 10% 的"命令"弹确认框
            filtered_commands = []
            for cmd in ai_commands:
                alpha_num = sum(1 for c in cmd if c.isascii() and (c.isalpha() or c.isdigit()))
                ratio = alpha_num / max(len(cmd), 1)
                if ratio < 0.1:
                    warn = _mcp_t(
                        f"⚠️ 疑似非命令文本（字母/数字占比 {ratio:.0%}）：\n  {cmd[:120]}",
                        f"⚠️ Possibly not a command (alphanum ratio {ratio:.0%}):\n  {cmd[:120]}"
                    )
                    console.print(warn, style="bold yellow")
                    # 提示词直达真实终端（防 stdout 被捕获流替换）
                    from .ai_lib.ui import real_terminal_io as _rtio2
                    with _rtio2():
                        try:
                            confirm = input(_mcp_t("  确认执行？(y/N): ", "  Execute anyway? (y/N): ")).strip().lower()
                        except (KeyboardInterrupt, EOFError):
                            confirm = 'n'
                    if confirm != 'y':
                        console.print(_mcp_t("  已跳过", "  Skipped"), style="dim")
                        continue
                filtered_commands.append(cmd)
            ai_commands = filtered_commands

            console.print(lang_text["cmd_exec_enabled"], style="bold cyan")

            total_commands = len(ai_commands)
            for idx, cmd in enumerate(ai_commands, 1):
                console.print("\n" + lang_text["cmd_exec_item"].format(idx, cmd), style="bold cyan")
                
                cmd_start_time = time.time()
                cmd_output = []
                cmd_request_id = str(uuid.uuid4())
                
                try:
                    cmd_parts_cmd = shlex.split(cmd)
                    is_tool_cmd = False
                    
                    if cmd_parts_cmd and get_cached_cmd:
                        cache_result = get_cached_cmd(cmd_parts_cmd[0].lower())
                        cmd_type, _ = cache_result
                        is_tool_cmd = (cmd_type == "tools")
                    
                    captured_output = ""
                    _output_line_count = 0
                    # 与子代理 RunCommand 共享串行锁：capture_command_output 全局替换
                    # sys.stdout，并发执行会互相覆盖、恢复顺序颠倒（输出污染 AI 上下文）
                    with _SUBAGENT_CMD_LOCK:
                        with capture_command_output() as (stdout_catcher, stderr_catcher):
                            stdout_catcher._ai_triggered = True  # AI 执行 → 限制显示
                            # 标记为 AI 执行模式（exe.py 据此启用超时弹窗）
                            _exe_module = sys.modules.get('lib.terminal.exe')
                            if _exe_module:
                                _exe_module.AI_EXECUTION_MODE = True
                            try:
                                if parse_and_execute:
                                    parse_and_execute(cmd)
                            finally:
                                if _exe_module:
                                    _exe_module.AI_EXECUTION_MODE = False
                            
                            full_output = stdout_catcher.get_output() + "\n" + stderr_catcher.get_output()
                            captured_output = full_output.strip()
                            _output_line_count = stdout_catcher._line_count
                    
                    # ── 在 capture 外面显示输出（capture 内 sys.stdout 被替换了）──
                    if captured_output:
                        if _output_line_count <= 10:
                            console.print(captured_output, style="dim white")
                        else:
                            trunc_note = _mcp_t(
                                f'… 以下省略 {_output_line_count - 10} 行（完整输出已保留）',
                                f'… {_output_line_count - 10} more lines omitted (full output retained)'
                            )
                            console.print(f"[dim]{trunc_note}[/]")
                        
                        if is_tool_cmd:
                            cached_tool_output = AI_TOOL_OUTPUT_CACHE.get(cmd_request_id)
                            if captured_output:
                                cmd_output.append(captured_output)
                            elif cached_tool_output:
                                cmd_output.append(lang_text["tool_output_cache"].format(cached_tool_output))
                                AI_TOOL_OUTPUT_CACHE.pop(cmd_request_id, None)
                            else:
                                cmd_output.append(lang_text["no_output"])
                        else:
                            if captured_output:
                                cmd_output.append(captured_output)
                            else:
                                cmd_output.append(lang_text["no_output"])
                
                except KeyboardInterrupt:
                    cmd_output.append(lang_text["command_interrupted"])
                    console.print("\n^C", style="bold yellow")
                    if log_error:
                        log_error(f"Command interrupted: {cmd}", current_session_id)
                except Exception as e:
                    error_msg = lang_text["command_error"].format(str(e))
                    cmd_output.append(error_msg)
                    console.print(error_msg, style="bold red")
                    if log_error:
                        log_error(f"Command execution failed: {cmd} -> {str(e)}", current_session_id)
                
                cmd_exec_time = time.time() - cmd_start_time
                
                if cmd_output:
                    time_label = lang_text.get("execution_time", "Execution time" if current_lang == "english" else "执行时间")
                    output_label = lang_text.get("output_content", "Output content" if current_lang == "english" else "输出内容")
                    cmd_result = f"{time_label}: {cmd_exec_time:.2f} seconds\n{output_label}:\n{''.join(cmd_output)}"
                else:
                    time_label = lang_text.get("execution_time", "Execution time" if current_lang == "english" else "执行时间")
                    output_label = lang_text.get("output_content", "Output content" if current_lang == "english" else "输出内容")
                    no_output_detail = "Command executed successfully, but no output returned" if current_lang == "english" else "命令执行成功，但未返回任何输出"
                    cmd_result = f"{time_label}: {cmd_exec_time:.2f} seconds\n{output_label}: {no_output_detail}"
                
                cmd_results[cmd] = cmd_result
            
                # ── 风暴检测：记录命令执行结果 ──
                _cmd_key = f"cmd:{cmd}"
                if "失败" in cmd_result or "error" in cmd_result.lower() or "exception" in cmd_result.lower():
                    _storm_counter[_cmd_key] = _storm_counter.get(_cmd_key, 0) + 1
                    _repeat_success.pop(_cmd_key, None)
                    if _storm_counter[_cmd_key] >= 2:
                        # 恢复配方：命令连续失败
                        _scenario = classify_failure("bash", cmd)
                        _recovery_msg = get_recovery_message(_scenario, _RECOVERY_CTX)
                        if _recovery_msg:
                            conversation_history.append({"role": "system", "content": _recovery_msg})
                    if _storm_counter[_cmd_key] >= 3:
                        _storm_warn = _mcp_t(
                            f"⚠️ 风暴检测：命令「{cmd}」连续失败 {_storm_counter[_cmd_key]} 次，AI 应更换策略",
                            f"⚠️ Storm detected: cmd「{cmd}」failed {_storm_counter[_cmd_key]}x, AI should switch strategy"
                        )
                        console.print(f"  [bold red]{_storm_warn}[/]")
                        conversation_history.append({"role": "system", "content": _storm_warn})
                else:
                    _storm_counter.pop(_cmd_key, None)
                    _repeat_success[_cmd_key] = _repeat_success.get(_cmd_key, 0) + 1
            
            # 标记本轮已处理的命令（基于实际执行结果）
            _commands_processed_this_round = bool(cmd_results)
            
            # ── 命令执行结果立即喂回给 AI ──
            if cmd_results:
                _cmd_feedback_lines = []
                for _cmd, _result in cmd_results.items():
                    _cmd_feedback_lines.append(f"$ {_cmd}\n{_result}")
                _cmd_feedback = "\n\n".join(_cmd_feedback_lines)
                conversation_history.append({"role": "system", "content": f"命令执行结果：\n{_cmd_feedback}"})
            
            if not ai_ask.strip():
                final_ai_result = ai_result.copy()
                if user_refuse_reasons:
                    refuse_summary = lang_text["user_refused_cmds"] + "\n".join(user_refuse_reasons)
                    if "txt" in final_ai_result:
                        final_ai_result["txt"] = (final_ai_result.get("txt") or "") + refuse_summary
                    else:
                        final_ai_result["txt"] = refuse_summary
                
                if interaction_count == 1:
                    record_ai_session(_mem_home, current_session_id, initial_question, final_ai_result, "", cmd_results, referenced_memory_uuid or "")
                    _user_input_round = False  # 首轮提问已由 record_ai_session 记录，消费标记
                    _flush_pending_tool_logs()  # 交互记录落盘后按顺序补写工具结果
                else:
                    existing_content, record_path = get_latest_ai_session(_mem_home, current_session_id)
                    if existing_content and record_path:
                        _ts = time.strftime('%Y-%m-%d %H:%M:%S')
                        _md = current_lang == "english"
                        new_content = f"\n\n### {'Interaction' if _md else '交互'} #{interaction_count} ({_ts})\n\n"
                        # 记录本轮的用户提问（对话历史中最后一个 user 消息）——
                        # 仅真正的用户输入轮记录；AI 自动循环轮不重复记录同一问题
                        if _user_input_round:
                            _last_user_q = ""
                            for _m in reversed(conversation_history):
                                if _m.get("role") == "user":
                                    _last_user_q = _m.get("content", "")
                                    break
                            if _last_user_q:
                                new_content += f"- **{'User' if _md else '用户'}**: {_last_user_q[:200]}{'...' if len(_last_user_q) > 200 else ''}\n"
                        else:
                            new_content += f"- **{'User' if _md else '用户'}**: _（AI 自动执行中，无新用户输入）_\n"
                        # ── 已消费本轮用户输入标记：立即重置 ──
                        # 防止后续 AI 自动循环轮把同一个问题/同一句"用户已确认计划"重复写入 library
                        _user_input_round = False
                        _resp = (final_ai_result.get('txt', '') or '').strip()
                        if _resp:
                            new_content += f"- **{'AI Response' if _md else 'AI回答'}**:\n  {_resp}\n"
                        if ai_commands:
                            new_content += f"- **{'Commands' if _md else '命令'}**:\n"
                            for idx_cmd, cmd in enumerate(ai_commands, 1):
                                cmd_result_val = cmd_results.get(cmd, "Not executed or execution failed" if _md else "未执行或执行失败")
                                new_content += f"  {idx_cmd}. `{cmd}`\n"
                                new_content += f"  - {'Output' if _md else '输出'}: {cmd_result_val[:200]}{'...' if len(cmd_result_val) > 200 else ''}\n"
                        try:
                            with open(record_path, "a", encoding="utf-8") as f:
                                f.write(new_content)
                        except Exception:
                            pass
                        _flush_pending_tool_logs()  # 交互记录落盘后按顺序补写工具结果
        else:
            if not ai_ask.strip():
                final_ai_result = ai_result.copy()
                if user_refuse_reasons:
                    refuse_summary = lang_text["user_refused_cmds"] + "\n".join(user_refuse_reasons)
                    if "txt" in final_ai_result:
                        final_ai_result["txt"] = (final_ai_result.get("txt") or "") + refuse_summary
                    else:
                        final_ai_result["txt"] = refuse_summary
                
                if interaction_count == 1:
                    record_ai_session(_mem_home, current_session_id, initial_question, final_ai_result, "", {}, referenced_memory_uuid or "")
                    _user_input_round = False  # 首轮提问已由 record_ai_session 记录，消费标记
                    _flush_pending_tool_logs()  # 交互记录落盘后按顺序补写工具结果
                else:
                    existing_content, record_path = get_latest_ai_session(_mem_home, current_session_id)
                    if existing_content and record_path:
                        _ts = time.strftime('%Y-%m-%d %H:%M:%S')
                        _md = current_lang == "english"
                        new_content = f"\n\n### {'Interaction' if _md else '交互'} #{interaction_count} ({_ts})\n\n"
                        # 记录本轮的用户提问（对话历史中最后一个 user 消息）——
                        # 仅真正的用户输入轮记录；AI 自动循环轮不重复记录同一问题
                        if _user_input_round:
                            _last_user_q = ""
                            for _m in reversed(conversation_history):
                                if _m.get("role") == "user":
                                    _last_user_q = _m.get("content", "")
                                    break
                            if _last_user_q:
                                new_content += f"- **{'User' if _md else '用户'}**: {_last_user_q[:200]}{'...' if len(_last_user_q) > 200 else ''}\n"
                        else:
                            new_content += f"- **{'User' if _md else '用户'}**: _（AI 自动执行中，无新用户输入）_\n"
                        # ── 已消费本轮用户输入标记：立即重置 ──
                        # 防止后续 AI 自动循环轮把同一个问题/同一句"用户已确认计划"重复写入 library
                        _user_input_round = False
                        _resp = (final_ai_result.get('txt', '') or '').strip()
                        if _resp:
                            new_content += f"- **{'AI Response' if _md else 'AI回答'}**:\n  {_resp}\n"
                        try:
                            with open(record_path, "a", encoding="utf-8") as f:
                                f.write(new_content)
                        except Exception:
                            pass
                        _flush_pending_tool_logs()  # 交互记录落盘后按顺序补写工具结果
        
        if not ai_ask.strip():
            if tag:
                update_message_tag(_mem_home, current_chat_name, current_session_id, tag, class_level)
            # answer=yes → AI 主动表示完成；answer=no → AI 认为还需继续
            # 但 answer 是可选信号，有挂起项时优先处理挂起项

        # Debug 面板：debug 模式下用 dim Panel 展示 SSE 原始响应
        debug_info = ai_result.get("_debug", "")
        if debug_info and debug_info.strip():
            from rich.panel import Panel as DebugPanel
            from rich.box import ROUNDED as DebugBox
            console.print(DebugPanel(
                debug_info.strip(),
                title="🔧 Debug",
                border_style="dim",
                box=DebugBox,
            ))
        
        # ── 自动判断是否继续循环（纯 Markdown 回复，无标记语言）──
        # 规则：仅当响应中只有纯文本且无挂起项时才停止循环；
        #       存在待执行命令/工具调用/计划/被拒原因时，回问 AI 传递上下文反馈。
        has_pending = bool(
            _commands_processed_this_round or
            _tool_calls_processed_this_round or
            plan_text.strip() or
            user_refuse_reasons  # 有被拒绝的命令 → 让 AI 看到反馈后重新尝试
        )

        if has_pending and not was_interrupted:
            # 有待执行项 → 自动继续下一轮
            # 但如果被 ESC 中断过，不自动循环，把控制权交还给用户
            continue_asking = True
        elif _in_repl:
            # REPL 模式 → 直接退出，由外层 REPL 接管
            continue_asking = False
        else:
            # ═══ 无待执行项 ═══
            # 纯文本回复，任务已完成，直接停止循环 + 显示 ESC 门控让用户决定后续。
            # ── 显示 token 量（优先 API 精确值，其次估算）──
            _pt = getattr(_thread_locals, "last_prompt_tokens", 0)
            _ct = getattr(_thread_locals, "last_completion_tokens", 0)
            if _pt or _ct:
                console.print(f"  [dim]📊 本轮 prompt {_pt} + completion {_ct} = {_pt + _ct} tokens（API 精确值）[/]")
            elif conversation_history:
                _total_chars = sum(len(m.get("content", "") or "") for m in conversation_history)
                _est_tokens = _total_chars // 3 + 1500
                console.print(f"  [dim]📊 上下文 ~{_est_tokens} tokens（估算）[/]")

            # ── 异步 Explore 子代理：有待完成任务时等待完成并注入，再续一轮 ──
            try:
                from .ai_lib import subagent as _subagent_wait
                if _subagent_wait.get_manager().has_pending() and not was_interrupted:
                    console.print(_mcp_t("  [bold cyan]🧩 等待子代理完成总结…[/]", "  [bold cyan]🧩 Waiting for subagent summaries…[/]"))
                    from rich.status import Status as _ExploreStatus
                    with _ExploreStatus(_mcp_t("  [dim]🧩 子代理运行中…[/]", "  [dim]🧩 Subagents running…[/]"), spinner="dots", console=console) as _st:
                        _deadline = time.time() + 600
                        while _subagent_wait.get_manager().has_pending() and time.time() < _deadline:
                            _act_tail = _subagent_wait.get_manager().format_activity(4)
                            if _act_tail:
                                _st.update(_mcp_t("  [dim]🧩 子代理运行中…\n" + _act_tail + "[/]", "  [dim]🧩 Subagents running…\n" + _act_tail + "[/]"))
                            _subagent_wait.get_manager().wait_any(timeout=0.4)  # 事件驱动等待（完成即醒）
                    _waited = _subagent_wait.get_manager().collect_done()
                    for _et in _waited:
                        if _et.status == "done" and _et.summary:
                            _inject = f"探索子代理结果：任务「{_et.name}」完成：\n{_et.summary}"
                            console.print(_mcp_t(f"  [bold cyan]🧩 Explore 子代理「{_et.name}」完成，结果已注入上下文[/]",
                                                 f"  [bold cyan]🧩 Explore subagent「{_et.name}」done, result injected[/]"))
                        else:
                            _inject = f"探索子代理任务失败：任务「{_et.name}」失败：{_et.error or _et.status}"
                            console.print(_mcp_t(f"  [bold red]🧩 Explore 子代理「{_et.name}」失败[/]",
                                                 f"  [bold red]🧩 Explore subagent「{_et.name}」failed[/]"))
                        conversation_history.append({"role": "system", "content": _inject})
                    if _waited:
                        continue_asking = True
                        continue
            except Exception:
                pass
            continue_asking = False
            # 延迟导入 prompt_toolkit（ESC 追问仅在本块使用）——避免模块级加载 ~1s
            from prompt_toolkit import prompt
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.styles import Style as PromptStyle
            kb_esc = KeyBindings()

            @kb_esc.add('escape')
            def on_esc(event):
                # ESC = 直接停止（与 Ctrl+C 一致）：结束本回合，不再追问
                event.app.exit(exception=KeyboardInterrupt())

            hint = lang_text.get("esc_hint",
                "Press ESC to exit, Enter to exit") if current_lang == "chinese" else \
                lang_text.get("esc_hint", "Press ESC to exit, Enter to exit")
            try:
                follow_up = prompt(
                    [('class:dim', hint + ' ')],
                    key_bindings=kb_esc,
                    style=PromptStyle.from_dict({'dim': 'dim'}),
                ).strip()
            except (KeyboardInterrupt, EOFError):
                console.print()
                console.print(lang_text.get("user_exit",
                    "Goodbye!" if current_lang == "english" else "再见！"), style="dim")
                continue

    # 恢复原始 SIGINT 处理器
    import signal as _signal
    _signal.signal(_signal.SIGINT, _original_sigint)
    _reset_ai_interrupt_flags()  # 兜底复位双份中断标志（防残留导致下次提问立即中断）
    cleanup_output_cache()
    _flush_pending_tool_logs()  # 兜底：确保工具结果记录落盘（中断/提前退出路径）
