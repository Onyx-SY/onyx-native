
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

# ── 记忆查询缓存（避免重复查询）──
_MEMORY_QUERY_CACHE: dict[str, str] = {}
_MEMORY_CACHE_MAX = 50

# （解析已统一走 bin/ai_lib/parsers.py，纯 Markdown 直通，无标记语言）
from .ai_lib.lang import get_lang_text
from .ai_lib.i18n import _ as _i18n  # 双语文本（中英）
from .ai_lib.tools import code_analysis  # 代码分析工具（py_*/Lsp*，独立工具包）
from .ai_lib.helpers import (
    handle_sleep_wait, set_ai_thread_priority, confirm_plan,
    parse_arguments, show_loading,
    init_ai_dangerous_commands, load_ai_dangerous_commands,
    is_dangerous_command, confirm_dangerous_command, has_forbidden_syntax,
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
# MCP 客户端（协议 + 服务器管理）
from .ai_lib import mcp_client
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
    default_config = {
        "_comment": "Onyx MCP server registry — per-user config",
        "servers": {
            "filesystem": {
                "name": "filesystem",
                "description": "文件系统操作 (read/write/edit/list/search)",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "{CWD}"],
                "auto_start": False,
                "installed": False
            }
        }
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


def _ensure_npx_available() -> bool:
    """检查 npx 是否可用"""
    try:
        result = subprocess.run(
            ["npx", "--version"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


# ── 推荐自动安装的 MCP 服务器列表 ──
_AUTO_INSTALL_MCP = [
    {"name": "fetch", "desc": "网页抓取/HTTP API"},
]

def install_default_mcp_server(user_home_dir: str = None, auto_extras: bool = False) -> bool:
    """标记 filesystem 为已安装。auto_extras=True 时同时安装推荐 MCP 模块。"""
    home = user_home_dir or USER_HOME_DIR
    config = _load_mcp_config(home)
    fs_config = config.get("servers", {}).get("filesystem", {})

    if not _ensure_npx_available():
        return False

    # 1. 确保 filesystem 已标记
    with MCP_INSTALL_LOCK:
        config2 = _load_mcp_config(home)
        fs2 = config2.get("servers", {}).get("filesystem", {})
        if not fs2.get("installed", False):
            fs_config["installed"] = True
            config.setdefault("servers", {})["filesystem"] = fs_config
            _save_mcp_config(config, home)

    # 2. 自动安装推荐 MCP（仅在 preload 时触发，避免阻塞 AI 调用）
    if auto_extras:
        for mcp_info in _AUTO_INSTALL_MCP:
            mcp_name = mcp_info["name"]
            mcp_desc = mcp_info["desc"]
            cfg = _load_mcp_config(home)
            if mcp_name in cfg.get("servers", {}):
                continue
            pkg = f"@modelcontextprotocol/server-{mcp_name}"
            console.print(_mcp_t(f"📦 自动安装 {mcp_name} ({mcp_desc})...", f"📦 Auto-installing {mcp_name} ({mcp_desc})..."), style="dim")
            result = install_mcp_server_cmd(mcp_name, pkg)
            if "✅" in result:
                try:
                    connect_mcp_server(mcp_name, home)
                except Exception:
                    pass

    return True


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
            # 默认不自动安装推荐 MCP（fetch 等）：只有用户显式配置的 server 才会连接。
            # auto_extras=False → 跳过 _AUTO_INSTALL_MCP 安装，仅保证 filesystem 可用。
            if install_default_mcp_server(home, auto_extras=False):
                connect_mcp_server("filesystem", home)
                tools = _discover_mcp_tools("filesystem", home)
                if tools:
                    console.print(_mcp_t(
                        f"✅ MCP 预加载: {len(tools)} 个工具就绪",
                        f"✅ MCP preload: {len(tools)} tools ready"
                    ), style="dim")
                    # 标记预加载已完成，后续启动跳过
                    try:
                        flag_path = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "mcp_preloaded.flag")
                        _ensure_dir(os.path.dirname(flag_path))
                        with open(flag_path, "w") as _f:
                            _f.write(str(time.time()))
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
            "操作 library 历史会话：search 按关键词搜索；list 列出活跃记忆；read 用 session_id 读完整记录。",
            {
                "operation": {"type": "string", "enum": ["search", "list", "read"], "description": "search/list/read"},
                "query": {"type": "string", "description": "搜索关键词（search 时必填）"},
                "session_id": {"type": "string", "description": "会话 UUID（read 时必填）"},
                "filter": {"type": "string", "description": "过滤 class 等级（list 时可选）"},
                "limit": {"type": "integer", "description": "返回结果数，默认 8，最大 20"},
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
            "提交多步骤计划给用户确认；plan 与 steps 二选一。确认后才可执行；复杂任务必须先提交计划。",
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
            "获取或设置 Onyx 配置：get 返回当前配置，set 设置键值。",
            {
                "action": {"type": "string", "enum": ["get", "set"], "description": "操作类型"},
                "key": {"type": "string", "description": "配置键名"},
                "value": {"type": "string", "description": "配置值（set 时需要）"},
            },
            ["action", "key"],
            PERM_WORKSPACE_WRITE,
        ),

        # ═══════════════════════════════════════════
        # ═══════════════════════════════════════════
        # DangerFullAccess — 危险操作，需显式批准
        # ═══════════════════════════════════════════

        _make_tool(
            "Agent",
            "启动子代理（隔离上下文，总结后喂回主 AI）。类型：explore=只读调查；plan=规划（只读+git）；lint=代码分析（可经安全管线跑分析命令）；test=测试（可经安全管线跑测试）。explore/plan 完全只读、自动执行无需用户确认；lint/test 需显式批准。适合大规模只读调查或可并行子任务——主上下文只接收总结，注意不要滥用。可指定 1~3 个任务并行。mode=sync 阻塞等待总结；mode=async 立即返回，完成后结果自动注入会话。",
            {
                "description": {"type": "string", "description": "子代理任务描述"},
                "prompt": {"type": "string", "description": "子代理的完整指令；多任务时可用 '1. ...\\n2. ...' 编号或 --- 分隔，配合 count 并行"},
                "name": {"type": "string", "description": "可选子代理名称"},
                "type": {"type": "string", "enum": ["explore", "plan", "lint", "test"], "description": "子代理类型（默认 explore）"},
                "mode": {"type": "string", "enum": ["sync", "async"], "description": "sync=等待完成并返回总结；async=后台运行，完成自动注入（默认 sync）"},
                "model": {"type": "string", "description": "可选模型名覆盖（默认与主 AI 相同模型）"},
                "count": {"type": "integer", "description": "并行子代理数量 1~3（默认 1；tasks 存在时按 tasks 长度）"},
                "tasks": {"type": "array", "items": {"type": "string"}, "description": "可选：1~3 个子任务数组，每个元素启动一个子代理"},
            },
            ["description", "prompt"],
            PERM_DANGER_FULL,
        ),
        _make_tool(
            "WebFetch",
            "获取 URL 并转为可读文本。需用户批准。",
            {
                "url": {"type": "string", "description": "要获取的 URL"},
                "prompt": {"type": "string", "description": "关于获取内容的具体问题"},
            },
            ["url", "prompt"],
            PERM_DANGER_FULL,
        ),
        _make_tool(
            "WebSearch",
            "搜索网络获取最新信息并返回引用。需用户批准。",
            {
                "query": {"type": "string", "minLength": 2, "description": "搜索关键词"},
                "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "可选限制搜索域名"},
            },
            ["query"],
            PERM_DANGER_FULL,
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
         "创建一个定时任务条目。schedule 为 cron 表达式，如 '0 * * * *'（每小时）。",
         {"schedule": {"type": "string", "description": "cron 表达式"},
          "prompt": {"type": "string", "description": "定时执行的任务描述"},
          "description": {"type": "string", "description": "可选说明"}},
         ["schedule", "prompt"], PERM_WORKSPACE_WRITE),

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
                "x_permission": PERM_DANGER_FULL,  # MCP 工具默认危险
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
        "RunCommand",
        "Execute a shell command through Onyx's security pipeline. Output is captured and returned to you; dangerous commands require user confirmation.",
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


def invalidate_native_tools_cache() -> None:
    """MCP 工具表变化（新连接/Registry 更新）后调用，强制下次重建。"""
    _NATIVE_TOOLS_CACHE["key"] = None
    _NATIVE_TOOLS_CACHE["tools"] = None
    _NATIVE_TOOLS_CACHE["prompt"] = None


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


def _run_grep_lines(pattern: str, search_paths, context: int = 0,
                    case_insensitive: bool = False, glob: str = None,
                    timeout: int = 15) -> Optional[str]:
    """grep -rn 核心文件搜索逻辑：返回含 file:line:content 的原始匹配文本。

    超时返回 None；无匹配返回空字符串。MemorySearch 与 grep_search 共用此逻辑。
    """
    cmd = ["grep", "-rn", "-H"]  # -H：单文件搜索时也输出文件名（MemorySearch 分组依赖）
    if case_insensitive:
        cmd.append("-i")
    if context and context > 0:
        cmd.append(f"-C{context}")
    if glob:
        cmd.extend(["--include", glob])
    cmd.append("--")
    cmd.append(pattern)
    if isinstance(search_paths, str):
        search_paths = [search_paths]
    cmd.extend(search_paths)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0 or result.returncode == 1:
            return result.stdout
        return result.stderr.strip() or None
    except subprocess.TimeoutExpired:
        return None


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

def _resolve_memory_path(path: str) -> str:
    """将记忆路径简写解析为完整文件路径。

    接受格式:
      library/<uuid>       → ~/.ai_s/library/<uuid>.txt
      library/<uuid>.txt   → ~/.ai_s/library/<uuid>.txt  (兼容旧格式)
      chat/<name>          → ~/.ai_s/chat/<name>.json
      onyx_ai              → ~/.ai_s/onyx_ai.md
    记忆根跟随 get_memory_home()（project 模式 → ~/.ai_s/projects/<id>/）
    """
    home = get_memory_home()
    base = os.path.join(home, ".ai_s")
    if path.startswith("chat/"):
        name = path[5:]
        if name.endswith(".json"):
            name = name[:-5]
        return os.path.join(base, "chat", name + ".json")
    if path.startswith("library/"):
        uuid_part = path[8:]
        if uuid_part.endswith(".txt"):
            uuid_part = uuid_part[:-4]
        return os.path.join(base, "library", uuid_part + ".txt")
    if path == "onyx_ai" or path == "onyx_ai.md":
        return os.path.join(base, "onyx_ai.md")

    # 完整路径直接返回
    if os.path.isabs(path):
        return path
    return os.path.join(base, path)


def _cache_query(key: str, result: str) -> str:
    """缓存查询结果。"""
    global _MEMORY_QUERY_CACHE
    if len(_MEMORY_QUERY_CACHE) >= _MEMORY_CACHE_MAX:
        # 淘汰最旧的
        old_key = next(iter(_MEMORY_QUERY_CACHE))
        _MEMORY_QUERY_CACHE.pop(old_key, None)
    _MEMORY_QUERY_CACHE[key] = result
    return result



# ═══════════════════════════════════════════════════════════
# Memory — 记忆查询执行器
# ═══════════════════════════════════════════════════════════

def _exec_memory_read(path: str, range_str: str = None) -> str:
    """读取记忆文件，支持行号范围。返回带行号前缀的内容。"""
    try:
        file_path = _resolve_memory_path(path)
        if not os.path.exists(file_path):
            return _i18n("mem_read_not_found", "bilingual", path=path, file_path=file_path)

        # 检查缓存
        cache_key = f"read:{file_path}:{range_str or 'full'}"
        if cache_key in _MEMORY_QUERY_CACHE:
            return _MEMORY_QUERY_CACHE[cache_key] + "\n\n" + _i18n("cached_hint", "bilingual")

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        all_lines = content.split("\n")
        total_lines = len(all_lines)
        start_line = 1
        view_mode = "full"

        if range_str:
            try:
                if "-" in range_str:
                    start, end = map(int, range_str.split("-", 1))
                    start_line = max(1, start)
                    end_line = min(total_lines, end)
                    selected = all_lines[start_line - 1:end_line]
                    view_mode = f"range {start_line}-{end_line}"
                else:
                    line_no = int(range_str)
                    start_line = max(1, min(line_no, total_lines))
                    selected = [all_lines[start_line - 1]]
                    view_mode = f"line {start_line}"
            except (ValueError, IndexError):
                selected = all_lines
        else:
            selected = all_lines

        # ── 添加行号（与 read_file 一致）──
        from lib.native_fs.panels import number_lines as _num_lines
        raw = "\n".join(selected)
        numbered = _num_lines(raw, start=start_line)

        # 不在此处截断 — AI 显式调用 MemoryRead 需要完整内容。
        # 上层 _MAX_TOOL_OUTPUT (32KB) 统一切断，保证不撑爆上下文。
        header = f"📄 `{path}` " + _i18n("mem_read_header", "bilingual", mode=view_mode, total=total_lines)
        result = f"{header}\n\n{numbered}"
        return _cache_query(cache_key, result)
    except Exception as e:
        return _i18n("mem_read_failed", "bilingual", err=e)


def _get_file_uuid(file_path: str) -> str:
    """从记忆文件路径提取 UUID。"""
    base = os.path.basename(file_path)
    name, ext = os.path.splitext(base)
    if ext == ".txt":
        return name  # library 文件：文件名就是 UUID
    elif ext == ".json":
        # chat 文件：尝试提取 session_uuid
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for m in data.get("messages", []):
                suuid = m.get("session_uuid", "")
                if suuid:
                    return suuid
        except Exception:
            pass
        return f"chat/{name}"
    return base


def _exec_memory_search(pattern: str, uuid: str = "all", context: int = 3,
                        case_insensitive: bool = True) -> str:
    """在记忆文件中搜索关键字。

    uuid 参数：真实 UUID → 只搜 ~/.ai_s/library/<uuid>.txt；
              'all'（默认）→ 全范围查找（chat/ + library/ + onyx_ai.md）。
    本质是文件搜索：复用 grep 文件搜索逻辑（_run_grep_lines），结果带行号
    （file:line:content）。
    """
    try:
        home = os.path.expanduser("~")
        base = os.path.join(home, ".ai_s")

        # ── 解析 uuid → 搜索目标 ──
        scope_label = uuid or "all"
        if uuid and uuid != "all":
            uuid_part = uuid
            if uuid_part.startswith("library/"):
                uuid_part = uuid_part[8:]
            if uuid_part.endswith(".txt"):
                uuid_part = uuid_part[:-4]
            file_path = os.path.join(base, "library", uuid_part + ".txt")
            if not os.path.exists(file_path):
                return _i18n("mem_search_uuid_missing", "bilingual", uuid=uuid, path=file_path)
            search_targets = [file_path]
        else:
            if not os.path.isdir(base):
                return _i18n("mem_search_dir_missing", "bilingual", path=base)
            search_targets = [base]
            scope_label = "all"

        cache_key = f"search:{pattern}:{scope_label}:{context}:{case_insensitive}"
        if cache_key in _MEMORY_QUERY_CACHE:
            return _MEMORY_QUERY_CACHE[cache_key] + "\n\n" + _i18n("cached_hint", "bilingual")

        # ── 复用文件搜索逻辑（grep -rn，结果含行号）──
        raw = _run_grep_lines(pattern, search_targets, context=context,
                              case_insensitive=case_insensitive, timeout=30)
        if raw is None:
            return _i18n("mem_search_timeout", "bilingual")
        if not raw.strip():
            return _i18n("mem_search_no_match", "bilingual", pattern=pattern)

        # 按文件分组 + UUID 标注（保留 file:line 行号信息）
        groups: dict[str, list[str]] = {}
        file_order: list[str] = []
        current_file = None
        current_block: list[str] = []

        def _flush_block():
            nonlocal current_file, current_block
            if current_file and current_block:
                if current_file not in groups:
                    groups[current_file] = []
                    file_order.append(current_file)
                groups[current_file].extend(current_block)
            current_block = []

        for line in raw.split("\n"):
            if line == "--":
                _flush_block()
                current_file = None
                continue
            if not line:
                continue
            idx = line.find(":")
            if idx <= 0:
                current_block.append(line)
                continue
            maybe_path = line[:idx]
            rest = line[idx + 1:]
            idx2 = rest.find(":")
            if idx2 <= 0:
                current_block.append(line)
                continue
            maybe_lineno = rest[:idx2]
            if not maybe_lineno.isdigit():
                current_block.append(line)
                continue
            if maybe_path != current_file:
                _flush_block()
                current_file = maybe_path
            current_block.append(line)

        _flush_block()

        out = []
        first = True
        for fpath in file_order:
            lines = groups[fpath]
            uuid_label = _get_file_uuid(fpath)
            if not first:
                out.append("─" * 40)
            first = False
            out.append(f"📌 UUID: `{uuid_label}`")
            out.append(f"   {_i18n('mem_search_path', 'bilingual')}: {fpath}")
            if fpath.endswith(".txt") or (fpath.endswith(".json") and not uuid_label.startswith("chat/")):
                out.append(f"   💡 {_i18n('mem_search_hint', 'bilingual', uuid=uuid_label)}")
            out.append("")
            out.extend(lines)
            out.append("")

        formatted = "\n".join(out)
        if len(formatted) > 20000:
            formatted = formatted[:20000] + "\n\n" + _i18n("mem_search_truncated", "bilingual")

        header = _i18n("mem_search_header", "bilingual", pattern=pattern,
                       scope=scope_label, ctx=context, files=len(groups))
        return _cache_query(cache_key, f"{header}\n\n{formatted}")
    except Exception as e:
        return _i18n("mem_search_failed", "bilingual", err=e)


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


def _exec_remember_session(session_id: str) -> str:
    """标记 library 会话为重要"""
    try:
        from .ai_lib.storage import mark_session_important
        home_dir = os.path.expanduser("~")
        return mark_session_important(home_dir, session_id)
    except Exception as e:
        return f"❌ remember failed: {e}"


def _exec_forget_session(session_id: str) -> str:
    """归档 library 会话"""
    try:
        from .ai_lib.storage import archive_session
        home_dir = os.path.expanduser("~")
        return archive_session(home_dir, session_id)
    except Exception as e:
        return f"❌ forget failed: {e}"


def _exec_search_library(query: str, limit: int = 8) -> str:
    """BM25 搜索海马体"""
    try:
        from .ai_lib.storage import search_library
        home_dir = os.path.expanduser("~")
        return search_library(home_dir, query, limit)
    except Exception as e:
        return f"❌ memory search failed: {e}"


def _exec_list_hippocampus(filter_type: str = None, limit: int = 30) -> str:
    """列出海马体活跃记忆"""
    try:
        from .ai_lib.storage import list_hippocampus
        home_dir = os.path.expanduser("~")
        return list_hippocampus(home_dir, filter_type=filter_type, limit=limit)
    except Exception as e:
        return f"❌ memory list failed: {e}"


def _exec_read_memory(session_id: str) -> str:
    """用 UUID 直接读取 library 完整记录"""
    try:
        from .ai_lib.storage import load_memory_by_uuid
        home_dir = os.path.expanduser("~")
        content = load_memory_by_uuid(home_dir, session_id)
        if not content:
            return f"Session {session_id} not found in library."
        # 限制长度防止上下文溢出
        if len(content) > 8000:
            content = content[:8000] + f"\n\n... (truncated, {len(content)} chars total)"
        return content
    except Exception as e:
        return f"❌ memory read failed: {e}"


def _exec_compact_stats() -> str:
    """查看压缩状态"""
    try:
        from .ai_lib.storage import get_compaction_stats
        home_dir = os.path.expanduser("~")
        return get_compaction_stats(home_dir)
    except Exception as e:
        return f"❌ compact_stats failed: {e}"


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


# ── 子代理命令执行器（由 handle_ai 注入：lint/test 子代理经同一安全管线执行命令）──
_SUBAGENT_COMMAND_EXECUTOR = None
_SUBAGENT_CMD_LOCK = threading.Lock()  # 全端子代理命令串行化（共享 PTY 防输出交错）
_SUBAGENT_STATUS = None                # 当前 Agent 工具的 Status spinner 引用（同步模式实时刷新）

# ── 主 AI 命令执行器（由 handle_ai 注入：RunCommand 工具经完整安全管线执行，
#    危险命令弹用户确认。模块级 handler 通过 get_main_command_executor 获取）──
_MAIN_RUN_COMMAND_EXECUTOR = None


def set_main_command_executor(fn: Callable) -> None:
    """注入主 AI 的 RunCommand 执行器（handle_ai 内的闭包：危险确认 + capture + parse_and_execute）。"""
    global _MAIN_RUN_COMMAND_EXECUTOR
    _MAIN_RUN_COMMAND_EXECUTOR = fn


def get_main_command_executor() -> Optional[Callable]:
    return _MAIN_RUN_COMMAND_EXECUTOR


# ── 模块级记忆根（由 handle_ai 注入 _mem_home：MemoryRead/MemorySearch 等
#    路径解析跟随记忆模式 global/project，未注入时回落用户主目录）──
_MEM_HOME = None


def set_memory_home(home_dir: str) -> None:
    """注入当前会话记忆根目录（handle_ai 内 _mem_home）。"""
    global _MEM_HOME
    _MEM_HOME = home_dir


def get_memory_home() -> str:
    """返回当前记忆根目录；未注入时回落用户主目录（兼容旧调用）。"""
    return _MEM_HOME or os.path.expanduser("~")


# ── library 工具结果采集白名单：文件/代码/Git/命令类工具执行后记录到 library ──
# 读工具记录内容；写工具记录 path + 状态（content 等大字段在格式化时排除）
LIB_CAPTURE_TOOLS = frozenset({
    # 读类
    "read_file", "grep_search", "glob_search", "get_file_info",
    "search_files", "search_content", "ListDirectory", "DirectoryTree",
    "MemoryRead", "MemorySearch",
    "py_diagnostics", "py_symbols", "LspDiagnostics", "LspSymbols",
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


def set_subagent_command_executor(fn: Callable) -> None:
    """注入命令执行器（handle_ai 内的闭包：capture + parse_and_execute + 危险命令拒绝）。"""
    global _SUBAGENT_COMMAND_EXECUTOR
    _SUBAGENT_COMMAND_EXECUTOR = fn


def get_subagent_command_executor() -> Optional[Callable]:
    return _SUBAGENT_COMMAND_EXECUTOR


def _refresh_subagent_status(_sa_mod, final: bool = False) -> None:
    """把子代理最近活动（灰色）刷新到当前 Status spinner，证明没卡住。"""
    try:
        _status = _SUBAGENT_STATUS
        if _status is None:
            return
        if final:
            _status.update("  [dim]🧩 子代理运行完成[/]")
            return
        _act = _sa_mod.get_manager().format_activity(4)
        if _act:
            _status.update("  [dim]🧩 子代理运行中…\n" + _act + "[/]")
        else:
            _status.update("  [dim]🧩 子代理运行中…[/]")
    except Exception:
        pass


def _exec_agent(description: str, prompt: str, name: str = "",
                mode: str = "sync", model: str = "",
                count: int = 1, tasks: list = None,
                agent_type: str = "explore") -> str:
    """启动子代理（explore=探索 / plan=规划 / lint=代码分析 / test=测试）。

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
            return "❌ Agent: 任务列表为空（请提供 prompt 或 tasks）"
        if mode == "sync":
            # ── 等待期间实时刷新灰色活动尾行（告诉用户没卡住）──
            _deadline = time.time() + _subagent_mod.SYNC_TIMEOUT
            while any(t.status in ("pending", "running") for t in _tasks):
                if time.time() > _deadline:
                    break
                _refresh_subagent_status(_subagent_mod)
                _subagent_mod.get_manager().wait_any(timeout=0.3)  # 事件驱动等待（完成即醒）
            _refresh_subagent_status(_subagent_mod, final=True)
        if mode == "async":
            ids = ", ".join(t.id for t in _tasks)
            names = ", ".join(f"「{t.name}」" for t in _tasks)
            return (
                f"✅ 已异步启动 {len(_tasks)} 个{_label}子代理 {names}（任务ID: {ids}）。\n"
                f"主 AI 可继续其他工作；子代理完成总结后会自动注入本会话上下文，届时再整合结论。"
            )
        # 同步：汇总全部总结
        lines = []
        for t in _tasks:
            if t.status == "done" and t.summary:
                lines.append(f"【{_label}子代理「{t.name}」总结】\n{t.summary}")
            else:
                lines.append(f"【{_label}子代理「{t.name}」失败】{t.error or t.status}")
        if len(lines) == 1:
            return lines[0]
        return "\n\n".join(lines)
    except Exception as e:
        return f"❌ Agent 执行失败: {e}"


def _exec_web_fetch(url: str, prompt: str) -> str:
    """获取 URL 内容。"""
    try:
        # 尝试使用 requests 获取
        import requests as _req
        resp = _req.get(url, timeout=15, headers={"User-Agent": "Onyx-AI/1.0"})
        text = resp.text[:5000]
        return f"✅ 已获取 {url} ({len(resp.text)} bytes)\n\n{text[:3000]}"
    except ImportError:
        pass
    except Exception as e:
        return f"❌ WebFetch '{url}' 失败: {e}"

    # 回退：通过 shell curl
    try:
        import subprocess as _sp
        result = _sp.run(["curl", "-sL", "--max-time", "10", url], capture_output=True, text=True, timeout=15)
        if result.stdout:
            text = result.stdout[:5000]
            return f"✅ 已获取 {url}\n\n{text[:3000]}"
        return f"⚠️ curl 返回空: {result.stderr[:200]}"
    except Exception as e:
        return f"❌ WebFetch '{url}' 全部方法失败: {e}"


def _exec_web_search(query: str, allowed_domains: list = None) -> str:
    """搜索网络。"""
    try:
        # 尝试通过 requests + DuckDuckGo 轻搜索
        import requests as _req
        import re as _re
        search_url = f"https://html.duckduckgo.com/html/?q={_req.utils.quote(query)}"
        resp = _req.get(search_url, timeout=15, headers={"User-Agent": "Onyx-AI/1.0"})
        # 简单提取结果
        snippets = _re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', resp.text, _re.DOTALL)
        if snippets:
            results = []
            for s in snippets[:5]:
                clean = _re.sub(r'<[^>]+>', '', s).strip()
                results.append(f"- {clean}")
            return "搜索结果:\n" + "\n".join(results)
        return f"WebSearch 返回 {len(resp.text)} bytes，请使用更精确的查询"
    except ImportError:
        pass
    except Exception as e:
        return f"❌ WebSearch '{query}' 失败: {e}"

    return "⚠️ WebSearch 不可用（需要安装 requests 库）"


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
        "WebFetch":     lambda p: _exec_web_fetch(p.get("url", ""), p.get("prompt", "")),
        "WebSearch":    lambda p: _exec_web_search(p.get("query", ""), p.get("allowed_domains", None)),
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
    if raw_tool in _BUILTIN_HANDLERS:
        try:
            result = _BUILTIN_HANDLERS[raw_tool](params or {})
            # AI 虚拟沙盒：文件类工具输出中的物理路径 → 虚拟路径（隐藏真实 cwd）
            if raw_tool in AI_FILE_TOOLS and sandbox.is_active():
                result = sandbox.display_text(result)
            return True, result
        except Exception as e:
            return False, f"Builtin tool error: {e}"

    # ── write_file 容错：如果参数被 _parse_tool_params 回退成 range_str，尝试从原始 JSON 中抠出 path 和 content ──
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
                # 反转义
                _raw_content = _raw_content.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                params["content"] = _raw_content
                params.pop("range_str", None)
                if _pm and not _raw_content.endswith("\n"):
                    params["content"] += "\n"
                _mcp_debug(f"write_file 容错: path={params.get('path', '?')}, content_len={len(params.get('content', ''))}")

    # ── 权限门控：根据 x_permission 级别决定是否需要用户确认 ──
    # 从 build_native_tools() 查找当前工具的权限级别
    _tool_permission = PERM_READONLY  # 默认只读安全
    try:
        _all_tools = build_native_tools()
        for _t in _all_tools:
            if _t.get("function", {}).get("name", "") == raw_tool:
                _tool_permission = _t.get("x_permission", PERM_READONLY)
                break
    except Exception:
        pass

    # ── Agent 工具分级：explore/plan 完全只读 → 自动放行（等同 ReadOnly）──
    # lint/test 可经安全管线跑命令 → 保持 DangerFullAccess（显式批准）
    if raw_tool == "Agent":
        _agent_type = str((params or {}).get("type", "explore")).lower()
        if _agent_type in ("explore", "plan"):
            _tool_permission = PERM_READONLY

    if _tool_permission == PERM_DANGER_FULL:
        # DangerFullAccess：显式用户批准 + 审批令牌
        _lang = get_current_lang()
        _prompt = (_lang == "chinese" and "🔴 工具 '{tool}' 需要危险权限，确认执行？(y/N): " or
                   "🔴 Tool '{tool}' requires dangerous access, confirm? (y/N): ").format(tool=raw_tool)
        try:
            _confirm = input(f"  {_prompt}").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return False, "⛔ 用户取消了危险操作"
        if _confirm not in ("y", "yes"):
            return False, "⛔ 用户拒绝了危险操作"
        # 创建审批令牌
        _scope = ApprovalScope(action=raw_tool, policy="dangerous_write")
        _token_grant = _APPROVAL_LEDGER.create(
            scope=_scope, approving_actor="user",
            approved_executor="ai", max_uses=1, ttl_seconds=60,
        )
        console.print(f"  [dim]✓ 已授权（令牌: {_token_grant.token[:12]}...）[/]")

    elif _tool_permission == PERM_WORKSPACE_WRITE and user_mode == "low":
        # WorkspaceWrite + low 模式：轻确认
        _lang = get_current_lang()
        _prompt = (_lang == "chinese" and "✏️ 工具 '{tool}' 将修改工作区，确认？(Y/n): " or
                   "✏️ Tool '{tool}' will modify workspace, confirm? (Y/n): ").format(tool=raw_tool)
        try:
            _confirm = input(f"  {_prompt}").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return False, "⛔ 用户取消了操作"
        if _confirm == "n":
            return False, "⛔ 用户拒绝了修改操作"
        console.print(f"  [dim]✓ 已授权[/]")
    # ReadOnly & WorkspaceWrite+mid/adv → 自动放行

    # ---- 安全限制：写入类工具仅 mid 及以上模式可用（low 禁止） ----
    write_tools = {"edit_file", "write_file", "create_file", "delete_file",
                   "delete_files", "move_file", "rename", "replace_in_file"}
    if raw_tool.lower() in write_tools and user_mode == "low":
        lang = get_current_lang()
        if lang == "chinese":
            return False, (
                f"⛔ 权限不足：'{raw_tool}' 需要 mid 模式才能执行。\n"
                f"请先执行 activite -m mid 提升权限后再重试。"
            )
        return False, (
            f"⛔ Permission denied: '{raw_tool}' requires mid mode.\n"
            f"Run: activite -m mid"
        )

    # ---- 路径安全校验（MCP 工具执行前必须经过 Onyx 沙箱检查） ----
    if path_validator is not None:
        arguments = dict(params) if params else {}
        file_tool_paths = _extract_paths_from_tool(raw_tool, arguments)
        for p in file_tool_paths:
            ok, err_msg = path_validator(raw_tool, p)
            if not ok:
                return False, err_msg

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
    # ── Git 工具 ──
    "GitStatus", "GitDiff", "GitLog", "GitBranch",
    # ── MCP filesystem 工具（走外部 server，参数同样先转物理）──
    "read_text_file", "read_multiple_files", "read_media_file",
    "list_directory", "directory_tree", "list_directory_with_sizes",
    "search_files", "search_content", "create_directory",
    "move_file", "copy_file", "delete_file", "delete_directory",
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
        return "没有已注册的 MCP 服务器"

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
        return "❌ 安装超时（120s）"

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
_AUTO_COMPACT_TOKEN_THRESHOLD = 300 * 1024

# 工具 schema 的固定 token 开销：校准 tokPerChar 时从真实 prompt tokens 中扣除。
# 约 55 个内置工具 + 描述，实测约 2 万余 token。
_TOOL_SCHEMA_TOKEN_OVERHEAD = 22_000

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
    """自动压缩生效阈值 = min(用户 300K, 实测/默认窗口 − 13K 安全缓冲)。"""
    _win = _SESSION_CONTEXT_WINDOWS.get(session_id) or _platform_context_window()
    _thr = min(_AUTO_COMPACT_TOKEN_THRESHOLD, _win - _WINDOW_SAFETY_BUFFER)
    return max(_thr, 32 * 1024)


def _estimate_conversation_tokens(conversation_history: List[Dict]) -> int:
    """估算整段对话历史（含 reasoning_content / tool_calls 参数）的 token 数。

    优先用上一轮真实 usage 校准 tokPerChar（扣除工具 schema 固定开销），
    无历史数据时回退 memory_compact 的 CJK 感知估算。
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
            _last_chars = _glrc() or 0
            if _last_chars > 0:
                _ratio = (_last_prompt - _TOOL_SCHEMA_TOKEN_OVERHEAD) / _last_chars
                _ratio = min(max(_ratio, 0.10), 0.80)
                return max(int(_chars * _ratio), _total)
    except Exception:
        pass
    return _total


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
    
    # ── lint/test 子代理命令执行器：经与主 AI 相同的安全管线（capture + parse_and_execute）──
    # 危险命令直接拒绝（子代理无法弹用户确认框）；全端子代理命令串行化防共享 PTY 输出交错。
    def _subagent_run_command(_cmd: str) -> str:
        """子代理 RunCommand 执行：危险命令拒绝 + 串行化 + 输出捕获。"""
        try:
            _is_danger, _cmd_name = is_dangerous_command(_cmd, dangerous_commands)
            if _is_danger:
                return f"⛔ 命令被拒绝（危险命令 [{_cmd_name}]，子代理无权执行）"
            with _SUBAGENT_CMD_LOCK:
                _captured = ""
                with capture_command_output() as (_out_catcher, _err_catcher):
                    _out_catcher._ai_triggered = True
                    _exe_mod = sys.modules.get('lib.terminal.exe')
                    if _exe_mod:
                        _exe_mod.AI_EXECUTION_MODE = True
                    try:
                        if parse_and_execute:
                            parse_and_execute(_cmd)
                    finally:
                        if _exe_mod:
                            _exe_mod.AI_EXECUTION_MODE = False
                    _captured = (_out_catcher.get_output() + "\n" + _err_catcher.get_output()).strip()
            return _captured if _captured else "(无输出)"
        except Exception as _e:
            return f"命令执行失败: {_e}"
    try:
        set_subagent_command_executor(_subagent_run_command)
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
                _confirmed, _u_resp, _refuse_reason = confirm_dangerous_command(
                    _cmd, _cmd_name, lang_text, current_session_id,
                    initial_question, interaction_count, log_info
                )
                if not _confirmed:
                    return (f"⛔ 用户拒绝了危险命令 [{_cmd_name}]：{_cmd}\n"
                            f"拒绝原因: {_refuse_reason or '未提供'}")
            # adv_code 模式：禁止语法拦截
            if _current_user_mode == "adv_code" and has_forbidden_syntax(_cmd):
                return f"⛔ 命令包含被禁止的语法，已被拦截：{_cmd[:200]}"
            with _SUBAGENT_CMD_LOCK:
                _captured = ""
                with capture_command_output() as (_out_catcher, _err_catcher):
                    _out_catcher._ai_triggered = True
                    _exe_mod = sys.modules.get('lib.terminal.exe')
                    if _exe_mod:
                        _exe_mod.AI_EXECUTION_MODE = True
                    try:
                        if parse_and_execute:
                            parse_and_execute(_cmd)
                    finally:
                        if _exe_mod:
                            _exe_mod.AI_EXECUTION_MODE = False
                    _captured = (_out_catcher.get_output() + "\n" + _err_catcher.get_output()).strip()
            return _captured if _captured else "(无输出)"
        except Exception as _e:
            return f"命令执行失败: {_e}"
    try:
        set_main_command_executor(_main_run_command)
    except Exception:
        pass
    
    # 提取当前用户模式字符串（用于安全限制）
    _current_user_mode = "low"
    if user_mode is not None:
        if hasattr(user_mode, 'current_mode'):
            _current_user_mode = str(user_mode.current_mode).lower()
        else:
            _current_user_mode = str(user_mode).lower()

    # 检查 MCP 是否启用（manage set mcp false/true）
    _mcp_enabled = True
    _mcp_enabled_path = os.path.join(user_home_dir, ".config", "onyx", "mcp_enabled")
    try:
        if os.path.exists(_mcp_enabled_path) and os.path.isfile(_mcp_enabled_path):
            with open(_mcp_enabled_path, "r") as f:
                _mcp_enabled = f.read().strip().lower() != "false"
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
            console.print("[yellow]No API key configured. Run 'ai -key <key>' first.[/]")
            return
        platform = conf.get("platform", "deepseek")
        current_model = conf.get("model", "")
        is_custom = (platform == "custom")
        plat_name = "Custom" if is_custom else _SUPPORTED_PLATFORMS.get(platform, {}).get("name", platform)
        if not content:
            # List current model + effort
            effort = conf.get("params", {}).get("reasoning_effort", "") or _SUPPORTED_PLATFORMS.get(platform, {}).get("reasoning_effort", "")
            console.print(f"[dim]Platform: {plat_name}  Model: {current_model or '?'}  Effort: {effort or '—'}[/]")
            if not is_custom:
                models = _SUPPORTED_PLATFORMS.get(platform, {}).get("models", [])
                console.print("Available models:")
                for m in models:
                    marker = "  ←" if m == current_model else ""
                    console.print(f"  {m}{marker}")
                console.print("\nUsage: ai -model <name>\n       ai -effort high|max")
            return
        # Switch model
        new_model = content.strip()
        conf["model"] = new_model
        # 混淆 api_key 后写入
        key_conf_path = os.path.join(user_home_dir, ".config", "onyx", "ai", "key.conf")
        os.makedirs(os.path.dirname(key_conf_path), exist_ok=True)
        _write_conf = dict(conf)
        if "api_key" in _write_conf and isinstance(_write_conf["api_key"], str):
            _write_conf["api_key"] = _obfuscate(_write_conf["api_key"])
        with open(key_conf_path, "w", encoding="utf-8") as f:
            _json.dump(_write_conf, f, ensure_ascii=False, indent=2)
        os.chmod(key_conf_path, 0o600)
        console.print(f"[green]✅ Switched to model: {new_model}[/]")
        return

    if content_type == "effort_command":
        # ai -effort [high|max] — view or set reasoning effort
        import json as _json
        conf = load_key_conf()
        if not conf:
            console.print("[yellow]No API key configured.[/]")
            return
        if not content:
            current_effort = conf.get("params", {}).get("reasoning_effort", "") or _SUPPORTED_PLATFORMS.get(conf.get("platform", ""), {}).get("reasoning_effort", "high")
            console.print(f"[dim]Current reasoning effort: {current_effort}[/]")
            console.print("Available: high, max")
            console.print("Usage: ai -effort high  |  ai -effort max")
            return
        effort_val = content.strip().lower()
        if effort_val not in ("high", "max"):
            console.print("[yellow]Invalid effort. Use: high or max[/]")
            return
        params = conf.get("params", {})
        if not isinstance(params, dict):
            params = {}
        params["reasoning_effort"] = effort_val
        conf["params"] = params
        # 混淆 api_key 后写入
        key_conf_path = os.path.join(user_home_dir, ".config", "onyx", "ai", "key.conf")
        os.makedirs(os.path.dirname(key_conf_path), exist_ok=True)
        _write_conf = dict(conf)
        if "api_key" in _write_conf and isinstance(_write_conf["api_key"], str):
            _write_conf["api_key"] = _obfuscate(_write_conf["api_key"])
        with open(key_conf_path, "w", encoding="utf-8") as f:
            _json.dump(_write_conf, f, ensure_ascii=False, indent=2)
        os.chmod(key_conf_path, 0o600)
        console.print(f"[green]✅ Reasoning effort set to: {effort_val}[/]")
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
                    console.print(f"❌ 深情模式插件验证失败: {reason}", style="bold red")
                    return
                lib = load_plugin("deep_aff")
                if not lib:
                    console.print("❌ 无法加载深情模式插件", style="bold red")
                    return
                # 调用 C 模块初始化
                validation_key = payload.get("binary_hash", "deep_aff_key")[:32]
                lib.deep_aff_init.argtypes = [ctypes.c_char_p]
                lib.deep_aff_init.restype = ctypes.c_int
                ret = lib.deep_aff_init(validation_key.encode())
                if ret != 0:
                    console.print("❌ 深情模式授权失败", style="bold red")
                    return
                # 获取提示词
                lib.deep_aff_get_prompt.argtypes = []
                lib.deep_aff_get_prompt.restype = ctypes.c_char_p
                lib.deep_aff_free.argtypes = [ctypes.c_char_p]
                prompt_ptr = lib.deep_aff_get_prompt()
                if not prompt_ptr:
                    console.print("❌ 无法获取深情模式提示词", style="bold red")
                    return
                prompt_text = ctypes.c_char_p(prompt_ptr).value.decode("utf-8")
                lib.deep_aff_free(prompt_ptr)
                # 保存提示词到文件（后续 AI 调用时会读取）
                deep_aff_path = os.path.join(user_home_dir, ".ai_s", "deep_aff_prompt.txt")
                os.makedirs(os.path.dirname(deep_aff_path), exist_ok=True)
                with open(deep_aff_path, "w", encoding="utf-8") as f:
                    f.write(prompt_text)
                console.print("💕 深情模式已激活", style="bold magenta")
                console.print(f"   提示词已保存: {len(prompt_text)} 字", style="dim")
            except Exception as e:
                console.print(f"❌ 深情模式启动失败: {e}", style="bold red")
                import traceback
                traceback.print_exc()
        else:
            # 关闭深情模式
            deep_aff_path = os.path.join(user_home_dir, ".ai_s", "deep_aff_prompt.txt")
            if os.path.exists(deep_aff_path):
                os.remove(deep_aff_path)
            console.print("💕 深情模式已关闭", style="dim")
        return

    if content_type == "machine_id_command":
        # ai -mid / ai -machine-id — show current device fingerprint
        try:
            from bin.plugin_loader import get_machine_id
            mid = get_machine_id()
            console.print(f"Machine ID: [bold]{mid}[/]")
        except Exception as e:
            console.print(f"[red]Failed to get machine ID: {e}[/]")
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
            console.print("Usage: ai -plugin list | load <name> | verify <name> | sign <name> [ver] | compile <file.c>")
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
            console.print(f"Unknown -c action: {chat_action}", style="bold red")
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

    # 重置中断标志（避免上次 Ctrl+C 残留导致本次立即中断）
    global _AI_INTERRUPTED
    _AI_INTERRUPTED = False

    # _MANUAL_COMPACT_REQUESTED 通过 _mcp_shared 模块属性访问，无需 global

    current_session_id = request_id
    initial_question = content
    last_user_question = content  # 追踪最近一次用户输入，ESC 追问时更新
    continue_asking = True
    _user_input_round = False  # 本轮是否有真正的用户输入（library 记录去重用）
    interaction_count = 0
    _pending_plan = ""  # 来自 submit_plan 工具调用的计划文本（跨循环持久化）
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
    _agreement_text = ""
    try:
        _agreement_paths = [
            os.path.join(ROOT_DIR, "onyx", "etc", "ai", "agreement.md"),
            os.path.join("etc", "ai", "agreement.md"),
        ]
        for _ap in _agreement_paths:
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
        referenced_memory_uuid, True, mode
    )

    # ── Layer 2 / TimeBased 闲置压缩：挂机 >60 分钟回来 → 清理已消费的旧工具结果 ──
    # 只在 REPL 长会话生效；静默、无 LLM 调用、只动已被 AI 消费的 tool 输出。
    try:
        if _external_history and (time.time() - _last_ai_interaction_ts) > _IDLE_COMPACT_SECONDS:
            if compact_consumed_tool_results(conversation_history):
                from .ai_lib.api import bump_rewrite_version as _bump_idle
                _bump_idle(current_session_id)
                console.print("[dim]📦 闲置压缩: 已清理过期的工具输出[/]")
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
                    console.print(f"  [bold cyan]🧩 {_et.label}子代理「{_et.name}」完成，结果已注入上下文[/]")
                else:
                    _inject = f"子代理任务失败：{_et.label}任务「{_et.name}」失败：{_et.error or _et.status}"
                    console.print(f"  [bold red]🧩 {_et.label}子代理「{_et.name}」失败[/]")
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

        def _execute_single_tool(tool_name: str, params_str: str = "") -> None:
            """执行单个 MCP 工具并将结果追加到面板展示列表（每次重新执行，无缓存）"""
            import json as _json

            # Plan 模式未确认 → 跳过
            if mode == "plan" and not plan_confirmed:
                tool_results_display.append({
                    "name": tool_name, "params": _display_tool_params(params_str),
                    "ok": False, "output": _mcp_t("Plan 模式: 已跳过", "Plan mode: skipped"),
                    "lines": []
                })
                return

            try:
                if params_str.strip():
                    params = _json.loads(params_str)
                else:
                    params = {}
            except _json.JSONDecodeError:
                params = _parse_tool_params(params_str, "")

            ok, output = execute_mcp_tool(tool_name, params, "filesystem", _current_user_mode,
                                          path_validator=_mcp_path_validator)
            # ── 采集工具结果（供 library 记录）──
            if ok and tool_name in LIB_CAPTURE_TOOLS:
                try:
                    from .ai_lib.storage import capture_tool_result
                    capture_tool_result(tool_name, params, output)
                except Exception:
                    pass
            # 取前100字符用于面板展示
            _preview = output[:100] + ("..." if len(output) > 100 else "")
            tool_results_display.append({
                "name": tool_name, "params": _display_tool_params(params_str),
                "ok": ok, "output": output,
                "preview": _preview
            })


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
            # 如果有活动的 HTTP 请求，关闭连接以减少响应延迟
            try:
                from .ai_lib.api import _ACTIVE_RESPONSE
                if _ACTIVE_RESPONSE is not None:
                    _ACTIVE_RESPONSE.close()
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
                        console.print("[dim]📦 对话压缩: 无可安全压缩的旧消息（tool_calls 链覆盖全部）[/]")
                    else:
                        conversation_history = _new_hist
                        # 通知缓存诊断：rewrite 版本号 +1，归因缓存断裂为日志重写
                        from .ai_lib.api import bump_rewrite_version as _bump
                        _bump(current_session_id)
                        console.print(
                            f"[dim]📦 对话压缩: {_old_len} 条 → 摘要 "
                            f"({_saved} 条节省, {_superseded} 条去重"
                            f", {_trident_stats.get('collapsed_msgs', 0)} 折叠"
                            f", {_trident_stats.get('clustered_msgs', 0)} 聚类)[/]"
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
                    if _estimate_conversation_tokens(conversation_history) >= _eff_thr:
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
                                f"[dim]📦 自动压缩: ~{_eff_thr // 1024}K tokens 上下文 "
                                f"→ 摘要 ({_saved} 条节省, {_superseded} 条去重"
                                f", {_trident_stats.get('collapsed_msgs', 0)} 折叠"
                                f", {_trident_stats.get('clustered_msgs', 0)} 聚类)[/]"
                            )
                            # ── 熔断器：压缩后仍 ≥90% 阈值 → 计数；连续 3 次 → 本会话停用自动压缩 ──
                            _after = _estimate_conversation_tokens(conversation_history)
                            if _after >= int(_eff_thr * 0.9):
                                _COMPACT_BREAKER_COUNTS[current_session_id] = (
                                    _COMPACT_BREAKER_COUNTS.get(current_session_id, 0) + 1)
                                if _COMPACT_BREAKER_COUNTS[current_session_id] >= 3:
                                    _COMPACT_BREAKER_DISABLED[current_session_id] = True
                                    console.print(
                                        "[bold yellow]⚠️ 连续 3 次压缩后上下文仍接近阈值，"
                                        "本会话已停止自动压缩。请用 /compact 手动压缩，"
                                        f"或调大 {_eff_thr // 1024}K 阈值。[/]")
                            else:
                                _COMPACT_BREAKER_COUNTS[current_session_id] = 0
                except Exception:
                    pass

            with Live(initial_panel, console=console, refresh_per_second=15, transient=False) as live:
                live_ref[0] = live
                loading_flag[0] = False  # Live Panel 已接管展示
                
                # 使用SSE模式调用（带实时流式回调）
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
                    console.print(f"[red]API 调用异常: {_api_exc}[/]")
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
                            "[bold yellow]📦 应急压缩: 上下文超限 → 已强制压缩并重试[/]")
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
            _warn = lang_text.get("cmd_limit", "⚠️ 命令超过 10 条限制，已截断前 10 条执行") if False else "⚠️ 命令超过 10 条限制，已截断前 10 条执行"
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
                console.print(lang_text.get("plan_guide_prompt", "💡 请输入你对计划的修改意见："), style="bold cyan")
                try:
                    guide_text = ui_text_input("💡 修改意见").strip()
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
                _tc_pending = list(tool_calls)
                while _tc_pending:
                    tc = _tc_pending.pop(0)
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
                                            _req_flag = "(必填)" if _pk in _req else "(可选)"
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
                    # 只有工具名带 mcp_/mcp__ 前缀的才是真正的 MCP 工具
                    # （build_native_tools 统一命名为 mcp_<tool>，见 mcp_prefixed）。
                    # 其余一律是内置工具，不标 MCP。
                    _tag = " [MCP]" if tool_name.startswith("mcp_") else ""
                    console.print(f"  [bold green]🔧 {_tool_display_name}{_tag}[/]{_param_preview}")

                    # 流式执行：用 Status spinner 展示工具运行过程
                    # 交互式工具（choose_ask 选项菜单 + 自由输入框）不包 spinner：
                    # Status 持续重绘会与 InquirerPy 的终端输入界面冲突，输入框被
                    # 转圈覆盖，用户只能看到"⏳ 运行中…"而看不到真正的输入框。
                    # 交互工具直接执行，让菜单和输入框独立渲染。
                    # RunCommand 同样不包：危险命令需弹确认框，spinner 会遮挡 y/N 提示。
                    _status_started = False
                    if tool_name not in ("choose_ask", "RunCommand"):
                        from rich.status import Status as _RichStatus
                        _status = _RichStatus(f"  [dim]⏳ {_tool_display_name} 运行中…[/]", spinner="dots", console=console)
                        _status.start()
                        _status_started = True
                    try:
                        # Agent 工具：挂载 Status 引用，供 _exec_agent 同步等待时刷新灰色活动尾行
                        if tool_name == "Agent":
                            global _SUBAGENT_STATUS
                            _SUBAGENT_STATUS = _status if _status_started else None
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
                        short = output[:100] + ("..." if len(output) > 100 else "")
                        console.print(f"   → {short}", style="dim")
                    else:
                        err_msg = f"❌ 工具执行失败: {output}"
                        tool_results.append(err_msg)
                        console.print(f"   {err_msg}", style="bold red")

            except KeyboardInterrupt:
                # Ctrl+C 强制打断工具执行
                _AI_INTERRUPTED = True
                console.print("\n  [bold red]⏹ 用户中断工具执行[/]")
                # 终止所有 MCP 子进程
                for _proc in MCP_SERVER_PROCESSES.values():
                    try:
                        _proc.terminate()
                    except Exception:
                        pass
                # 补齐 tool_results 长度，确保与 tool_calls 一一对应
                # 避免 "assistant 有 tool_calls 但缺少 tool 消息" 的 API 错误
                while len(tool_results) < len(tool_calls):
                    tool_results.append("⏹ 用户中断，该工具未执行")
                continue_asking = False

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
                    _raw_id = tc.get("id") or f"call_{interaction_count}_{i}"
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
                    _log_lines.append(f"- **工具**: `{_tn}`")
                    _log_lines.append(f"  ```")
                    _log_lines.append(f"  {_res}")
                    _log_lines.append(f"  ```")
                _log_text = "\n".join(_log_lines)
                _, record_path = get_latest_ai_session(user_home_dir, current_session_id)
                if record_path:
                    try:
                        with open(record_path, "a", encoding="utf-8") as f:
                            f.write(f"\n\n{_log_text}\n")
                    except Exception:
                        pass

        # ── AI 纯文本回复 → 追加 assistant 消息 ──
        _ai_txt = (ai_result.get("txt", "") or "").strip()
        _reasoning = ai_result.get("_reasoning", "")
        if (_ai_txt or _reasoning) and not tool_calls:
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
                confirmed_commands = []
                for cmd, cmd_name in dangerous_cmds_found:
                    confirmed, user_response, refuse_reason = confirm_dangerous_command(
                        cmd, cmd_name, lang_text, current_session_id, initial_question, interaction_count, log_info
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
                    console.print("  [bold cyan]🧩 等待子代理完成总结…[/]")
                    from rich.status import Status as _ExploreStatus
                    with _ExploreStatus("  [dim]🧩 子代理运行中…[/]", spinner="dots", console=console) as _st:
                        _deadline = time.time() + 600
                        while _subagent_wait.get_manager().has_pending() and time.time() < _deadline:
                            _act_tail = _subagent_wait.get_manager().format_activity(4)
                            if _act_tail:
                                _st.update("  [dim]🧩 子代理运行中…\n" + _act_tail + "[/]")
                            _subagent_wait.get_manager().wait_any(timeout=0.4)  # 事件驱动等待（完成即醒）
                    _waited = _subagent_wait.get_manager().collect_done()
                    for _et in _waited:
                        if _et.status == "done" and _et.summary:
                            _inject = f"探索子代理结果：任务「{_et.name}」完成：\n{_et.summary}"
                            console.print(f"  [bold cyan]🧩 Explore 子代理「{_et.name}」完成，结果已注入上下文[/]")
                        else:
                            _inject = f"探索子代理任务失败：任务「{_et.name}」失败：{_et.error or _et.status}"
                            console.print(f"  [bold red]🧩 Explore 子代理「{_et.name}」失败[/]")
                        conversation_history.append({"role": "system", "content": _inject})
                    if _waited:
                        continue_asking = True
                        continue
            except Exception:
                pass
            continue_asking = False
            esc_pressed = [False]
            # 延迟导入 prompt_toolkit（ESC 追问仅在本块使用）——避免模块级加载 ~1s
            from prompt_toolkit import prompt
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.styles import Style as PromptStyle
            kb_esc = KeyBindings()

            @kb_esc.add('escape')
            def on_esc(event):
                esc_pressed[0] = True
                event.app.exit(result='')

            hint = lang_text.get("esc_hint",
                "Press ESC to ask, Enter to exit") if current_lang == "chinese" else \
                lang_text.get("esc_hint", "Press ESC to ask, Enter to exit")
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

            if esc_pressed[0]:
                console.print()
                console.print(lang_text.get("esc_ask",
                    "Any questions?" if current_lang == "english" else "有什么问题吗？"), style="dim")
                try:
                    follow_up = prompt("> ").strip()
                except (KeyboardInterrupt, EOFError):
                    console.print()
                    console.print(lang_text.get("user_exit",
                        "Goodbye!" if current_lang == "english" else "再见！"), style="dim")
                    continue

                if follow_up:
                    last_user_question = follow_up
                    message_appended = False
                    current_question = follow_up
                    _time_tag = f"\n\n[⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}]"
                    conversation_history.append({"role": "user", "content": follow_up + _time_tag})
                    _user_input_round = True  # 用户 ESC 追问
                    continue_asking = True

    # 恢复原始 SIGINT 处理器
    import signal as _signal
    _signal.signal(_signal.SIGINT, _original_sigint)
    cleanup_output_cache()
