
# -------------------------- 1. 基础模块导入 + 配置导入 --------------------------

import sys
import os
import time
import threading
import json
import requests
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
from prompt_toolkit import prompt
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PromptStyle
from prompt_toolkit.formatted_text import FormattedText
warnings.filterwarnings('ignore', category=requests.packages.urllib3.exceptions.InsecureRequestWarning)

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
    StreamingDisplay,
)

# ── 从子模块导入配置 / 密钥 / 情感 / URL / 语言等 ──
from .ai_lib.config import (
    ROOT_DIR, USER, USER_HOME_DIR,
    LANGUAGE_CONFIG_PATH, help_info_path, onyx_config_path,
    AI_KEY_DIR, AI_KEY_PATH, KEY_CONF_PATH, MOOD_PATH, SERVER_URL_FILE,
    _load_ai_models, _SUPPORTED_PLATFORMS,
    _obfuscate, _deobfuscate,
    load_key_conf, save_key_conf, _setup_key_conf_interactive,
    is_mood_enabled, init_mood, load_mood, save_mood,
    apply_mood_delta, apply_people_action, _render_edit_diff, mood_context,
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

# ── LSP 客户端 ──
from lib.lsp_client import LspManager, LspAction

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

# （解析函数已移至 bin/ai_lib/parsers.py）
from .ai_lib.parsers import parse_sse_structured_response, _parse_ai_raw_response, _parse_legacy_shell
from .ai_lib.api import call_ai_api_sse, process_ai_result_fields, extract_ai_commands, build_memory_context
from .ai_lib.lang import get_lang_text
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
# MCP 客户端（协议 + 服务器管理）
from .ai_lib import mcp_client

# 10.5 MCP (Model Context Protocol) 客户端模块
#    替代原 plugin_loader 插件系统，通过本地 MCP server 提供 AI 工具
#    - 出厂自动安装 @modelcontextprotocol/server-filesystem
#    - 用户可通过 ai -mcp install/remove/list 管理
#    - 工具列表中过滤 shell/bash 类工具（Onyx 已有 shell 接口）
#    - edit_file/write_file 在 mid 及以上模式允许（low 禁止）
#
#    v2.7 — Reasonix 风格重构：
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
            args = [home]
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

    # 快速诊断：等 2s 看进程是否立即崩溃
    _mcp_debug(f"Waiting 2s, checking liveness... pid={proc.pid}")
    time.sleep(2)
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
            if install_default_mcp_server(home, auto_extras=True):
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
    lines.append("All tools use standard function calling (tool_calls). Do NOT use [tool:...] text format.")
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
    lines.append("3. **Large file chunking**: Files >20KB: create skeleton with `write_file`, fill with multiple `edit_file` calls")
    lines.append("4. **Validate before edit**: Always call `validate_edit` before `edit_file`")
    lines.append("5. **Unique anchor**: `edit_file` old_string must be byte-exact and unique")
    lines.append("6. **Shell first**: use `@@SHELL` for ls/cat/grep/find when possible")
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
    lines.append("> TXT/ANALYSIS/PLAN/ASK/@@SHELL still use native markup language")
    return "\n".join(lines)


# ── 权限级别常量 ──
PERM_READONLY = "ReadOnly"           # 安全只读，自动放行
PERM_WORKSPACE_WRITE = "WorkspaceWrite"  # 修改工作区，需轻确认
PERM_DANGER_FULL = "DangerFullAccess"    # 危险操作，需显式批准


def _make_tool(name: str, description: str, properties: dict, required: list,
               permission: str = PERM_READONLY) -> Dict:
    """构建标准 OpenAI function calling 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
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
            "读取文件内容。支持行号范围。改文件前务必先读文件确认当前内容。",
            {
                "path": {"type": "string", "description": "文件路径"},
                "range": {"type": "string", "description": "可选行号范围，如 '10-30' 或 '42'（单行）"},
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
            "使用正则表达式搜索文件内容。支持上下文行、大小写控制。",
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
            "更新当前会话的任务列表。用于多步骤任务中跟踪进度。设置状态为 completed 表示该步骤完成。",
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
            "创建新文件或全量覆盖现有文件。仅用于新建文件或超过 70% 内容变动。局部修改优先用 edit_file。超过 20KB 的新文件应先用 write_file 创建骨架，再用多次 edit_file 填入实现。",
            {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整的文件内容"},
            },
            ["path", "content"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "edit_file",
            "SEARCH/REPLACE 精确替换。old_string 必须逐字节匹配文件内容且唯一。改前先用 validate_edit 校验。保留代码缩进。",
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
            "校验 SEARCH 文本在目标文件中存在且唯一。每次 edit_file 前务必先调用此工具校验。",
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
            "预览 edit_file 的 unified diff。确认修改正确后再执行编辑。",
            {
                "file_path": {"type": "string", "description": "目标文件路径"},
                "search": {"type": "string", "description": "要搜索的旧文本"},
                "replace": {"type": "string", "description": "替换后的新文本"},
            },
            ["file_path", "search", "replace"],
            PERM_READONLY,  # 预览是安全的
        ),
        _make_tool(
            "choose_ask",
            '当你不确定用户意图时，提供几个选项让用户选择。用户也可以选择「以上都不是」然后自由输入。',
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
            "提交多步骤执行计划给用户确认。steps 是结构化步骤数组，plan 是纯文本描述（二选一）。确认后才能开始执行。复杂任务必须先用此工具提交计划。",
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
            "进入计划模式。在此模式下禁止执行任何命令和文件修改，只能输出计划。调用 EnterPlanMode 后应紧接着用 submit_plan 提交计划。",
            {},
            [],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "ExitPlanMode",
            "退出计划模式，恢复正常执行模式。计划被确认后调用此工具退出 plan 模式并开始执行。",
            {},
            [],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "Config",
            "获取或设置 Onyx 配置。action='get' 返回当前配置，action='set' 设置指定键的值。",
            {
                "action": {"type": "string", "enum": ["get", "set"], "description": "操作类型"},
                "key": {"type": "string", "description": "配置键名"},
                "value": {"type": "string", "description": "配置值（set 时需要）"},
            },
            ["action", "key"],
            PERM_WORKSPACE_WRITE,
        ),

        # ═══════════════════════════════════════════
        # 情感工具（内部使用，不在 TXT 中提及）
        # ═══════════════════════════════════════════

        _make_tool(
            "set_mood",
            "调整情感状态。dimension: happy/angry, delta: -3~+3。此工具静默执行，不要在 TXT 回复中提及。",
            {
                "dimension": {"type": "string", "enum": ["happy", "angry"], "description": "情感维度"},
                "delta": {"type": "number", "description": "调整值（-3.0 ~ +3.0）"},
            },
            ["dimension", "delta"],
            PERM_READONLY,
        ),
        _make_tool(
            "update_people",
            "更新人物档案。此工具静默执行，不要在 TXT 回复中提及。",
            {
                "action": {"type": "string", "enum": ["add", "likeability", "perception"], "description": "操作类型"},
                "name": {"type": "string", "description": "人物名称"},
                "value": {"type": "string", "description": "likeability 时传数字，perception 时传描述"},
            },
            ["action", "name"],
            PERM_READONLY,
        ),

        # ═══════════════════════════════════════════
        # DangerFullAccess — 危险操作，需显式批准
        # ═══════════════════════════════════════════

        _make_tool(
            "Agent",
            "启动一个子代理处理独立任务。子代理在隔离环境中运行，返回结果后继续。用于并行探索或独立子任务。",
            {
                "description": {"type": "string", "description": "子代理任务描述"},
                "prompt": {"type": "string", "description": "子代理的完整指令"},
                "name": {"type": "string", "description": "可选子代理名称"},
            },
            ["description", "prompt"],
            PERM_DANGER_FULL,
        ),
        _make_tool(
            "WebFetch",
            "获取 URL 内容并转换为可读文本。需用户批准。",
            {
                "url": {"type": "string", "description": "要获取的 URL"},
                "prompt": {"type": "string", "description": "关于获取内容的具体问题"},
            },
            ["url", "prompt"],
            PERM_DANGER_FULL,
        ),
        _make_tool(
            "WebSearch",
            "搜索网络获取最新信息并返回引用结果。需用户批准。",
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
         "创建一个结构化任务。支持两种模式：1) 传 prompt 创建简单任务；2) 传 TaskPacket 字段（objective, scope, acceptance_criteria 等）创建完整任务包。返回任务 ID。",
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
    # LSP — 语言服务器协议工具
    # ═══════════════════════════════════════════

    native.append(_make_tool(
        "LspDiagnostics",
        "获取文件的诊断信息（错误、警告等）。自动根据文件扩展名启动对应的语言服务器。",
        {"path": {"type": "string", "description": "文件路径"}},
        ["path"], PERM_READONLY,
    ))
    native.append(_make_tool(
        "LspHover",
        "获取光标处的悬停提示信息（类型签名、文档等）。",
        {"path": {"type": "string", "description": "文件路径"},
         "line": {"type": "integer", "description": "行号（从 1 开始）"},
         "character": {"type": "integer", "description": "列号（从 0 开始）"}},
        ["path", "line", "character"], PERM_READONLY,
    ))
    native.append(_make_tool(
        "LspDefinition",
        "跳转到符号定义的位置。返回文件路径、行号和预览。",
        {"path": {"type": "string", "description": "文件路径"},
         "line": {"type": "integer", "description": "行号"},
         "character": {"type": "integer", "description": "列号"}},
        ["path", "line", "character"], PERM_READONLY,
    ))
    native.append(_make_tool(
        "LspReferences",
        "查找符号的所有引用位置。返回引用列表（文件路径 + 行号）。",
        {"path": {"type": "string", "description": "文件路径"},
         "line": {"type": "integer", "description": "行号"},
         "character": {"type": "integer", "description": "列号"}},
        ["path", "line", "character"], PERM_READONLY,
    ))
    native.append(_make_tool(
        "LspSymbols",
        "获取文件中的所有符号（函数、类、变量等）。返回符号名、类型、位置。",
        {"path": {"type": "string", "description": "文件路径"}},
        ["path"], PERM_READONLY,
    ))

    # ═══════════════════════════════════════════
    # Memory — 记忆查询工具（支持 range + context）
    # ═══════════════════════════════════════════

    native.append(_make_tool(
        "MemoryRead",
        "读取记忆文件内容，支持行号范围。记忆存储在 ~/.ai_s/chat/（对话记录）、~/.ai_s/library/（会话存档）、~/.ai_s/onyx_ai.md（持久提示）。查询结果会自动缓存，避免重复查询。",
        {
            "path": {"type": "string", "description": "记忆路径（如 chat/first, library/<uuid>, onyx_ai）"},
            "range": {"type": "string", "description": "可选行号范围，如 '1-30' 或 '50'（单行）"},
        },
        ["path"], PERM_READONLY,
    ))
    native.append(_make_tool(
        "MemorySearch",
        "在记忆文件中搜索关键字，默认显示匹配行上下各 3 行。搜索结果自动缓存，避免重复查询。",
        {
            "pattern": {"type": "string", "description": "搜索关键字或正则"},
            "path": {"type": "string", "description": "可选限制搜索范围（如 chat, library, onyx_ai）"},
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
                    "description": f"[MCP {name}] {mt.get('description', '')}",
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

    native.sort(key=lambda t: t.get("function", {}).get("name", ""))
    _mcp_debug_exit("build_native_tools", ok=len(native) > 0,
                    detail=f"{len(native)} native tools")
    return native


# ──────────────────── 内置分析工具执行器 ────────────────────

def _exec_validate_edit(file_path: str, search: str, replace: str) -> str:
    """校验 SEARCH/REPLACE 编辑。"""
    try:
        from lib.edit_engine import validate_edit, dry_run_edit
        ok, msg = validate_edit(file_path, search, replace)
        if ok:
            diff = dry_run_edit(file_path, search, replace)
            return f"✅ Edit valid\n\n{diff[:2000]}"
        return f"❌ {msg}"
    except Exception as e:
        return f"❌ validate_edit failed: {e}"


def _exec_preview_edit(file_path: str, search: str, replace: str) -> str:
    """预览 diff。"""
    try:
        from lib.edit_engine import dry_run_edit
        diff = dry_run_edit(file_path, search, replace)
        if diff.startswith("❌"):
            return diff
        return f"```diff\n{diff}\n```"
    except Exception as e:
        return f"❌ preview_edit failed: {e}"


def _exec_get_file_info(file_path: str) -> str:
    """获取文件基本信息。"""
    try:
        import os, datetime
        if not os.path.exists(file_path):
            return f"❌ File not found: {file_path}"
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
            f"  大小: {size_str}\n"
            f"  修改时间: {mtime}\n"
            f"  行数: {line_count if line_count >= 0 else 'binary/unknown'}\n"
            f"  类型: {ext}"
        )
    except Exception as e:
        return f"❌ get_file_info failed: {e}"


def _exec_read_file(file_path: str, range_str: str = None) -> str:
    """读取文件内容，支持行号范围。"""
    try:
        if not os.path.exists(file_path):
            return f"❌ File not found: {file_path}"
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
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
        if range_str:
            try:
                if "-" in range_str:
                    start, end = map(int, range_str.split("-", 1))
                    lines = content.split("\n")
                    lines = lines[max(0, start-1):end]
                    content = "\n".join(lines)
                else:
                    line_no = int(range_str)
                    lines = content.split("\n")
                    content = lines[min(line_no-1, len(lines)-1)]
            except (ValueError, IndexError):
                pass
        return content
    except Exception as e:
        return f"❌ read_file failed: {e}"


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
                return f"⏭️ 内容未变化，跳过写入 {file_path}"
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
            "result": f"✅ 已写入 {file_path}（{total_lines} 行）",
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
            console.print(f"  ✅ ✏️ 编辑 {file_path}")
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
                "result": f"✅ 编辑成功: {file_path}",
                "original_file": old_content,
                "file_path": file_path,
            }, ensure_ascii=False)
        _err_lower = msg.lower()
        if "not found" in _err_lower or "not unique" in _err_lower:
            return f"❌ {msg}\n提示：使用 validate_edit 先校验 SEARCH 文本"
        return f"❌ {msg}"
    except Exception as e:
        return f"❌ edit_file failed: {e}"


def _exec_set_mood(dimension: str, delta: float) -> str:
    """调整情感状态。"""
    try:
        apply_mood_delta(dimension, delta)
        return f"✅ mood {dimension} {delta:+.1f}"
    except Exception as e:
        return f"❌ set_mood failed: {e}"


def _exec_update_people(action: str, name: str, value: str = "") -> str:
    """更新人物档案。"""
    try:
        apply_people_action(action, name, value)
        return f"✅ people {action} {name}" + (f" {value}" if value else "")
    except Exception as e:
        return f"❌ update_people failed: {e}"


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
            return f"(no matches for '{pattern}' in {search_root})"
        # 限制返回数量
        total = len(matches)
        if total > 200:
            matches = matches[:200]
            return f"\n".join(matches) + f"\n... 以及 {total - 200} 个其他文件（共 {total} 个）"
        return "\n".join(matches)
    except Exception as e:
        return f"❌ glob_search failed: {e}"


def _exec_grep_search(pattern: str, path: str = None, glob: str = None,
                      context: int = 0, i: bool = False, head_limit: int = None) -> str:
    """使用正则表达式搜索文件内容。"""
    try:
        import subprocess as _sp
        cmd = ["grep", "-rn"]
        if i:
            cmd.append("-i")
        if context and context > 0:
            cmd.append(f"-C{context}")
        if glob:
            cmd.extend(["--include", glob])
        search_root = path or "."
        cmd.extend([pattern, search_root])
        result = _sp.run(cmd, capture_output=True, text=True, timeout=15)
        output = result.stdout.strip() or result.stderr.strip() or "(no matches)"
        lines = output.split("\n")
        if head_limit and len(lines) > head_limit:
            output = "\n".join(lines[:head_limit]) + f"\n…[共 {len(lines)} 行，仅显示前 {head_limit} 行]"
        if len(output) > 10000:
            output = output[:5000] + f"\n…[输出过长，截断至 5000 字符，共 {len(output)} 字符]"
        return output
    except subprocess.TimeoutExpired:
        return "❌ grep_search: 搜索超时（15s），请缩小搜索范围"
    except Exception as e:
        return f"❌ grep_search failed: {e}"


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
        # ═══ Claude Code 兼容 ═══
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
    """等待指定秒数。"""
    try:
        import time as _time
        seconds = max(1, min(seconds, 300))  # 限制 1-300 秒
        _time.sleep(seconds)
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
    """将记忆路径简写解析为完整文件路径。"""
    home = os.path.expanduser("~")
    base = os.path.join(home, ".ai_s")
    if path.startswith("chat/"):
        return os.path.join(base, "chat", path[5:] + ".json")
    if path.startswith("library/"):
        return os.path.join(base, "library", path[8:] + ".txt")
    if path == "onyx_ai":
        return os.path.join(base, "onyx_ai.md")
    if path == "mood":
        return os.path.join(base, "mood.json")
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


def _exec_lsp_diagnostics(path: str) -> str:
    try:
        client = _LSP_MANAGER.get_client(path)
        if not client:
            return f"⚠️ 未找到 {path} 对应的语言服务器"
        client.did_open(path)
        return f"✅ 已打开 {path}，诊断信息将在语言服务器返回后可用"
    except Exception as e:
        return f"❌ LspDiagnostics 失败: {e}"


def _exec_lsp_hover(path: str, line: int, character: int) -> str:
    try:
        client = _LSP_MANAGER.get_client(path)
        if not client:
            return f"⚠️ 未找到 {path} 对应的语言服务器"
        result = client.hover(path, line - 1, character)
        if not result:
            return f"ℹ️ `{path}:{line}:{character}` 无悬停信息"
        output = f"📖 悬停提示: `{path}:{line}:{character}`\n\n{result.content}"
        if result.language:
            output += f"\n\n语言: {result.language}"
        return output
    except Exception as e:
        return f"❌ LspHover 失败: {e}"


def _exec_lsp_definition(path: str, line: int, character: int) -> str:
    try:
        client = _LSP_MANAGER.get_client(path)
        if not client:
            return f"⚠️ 未找到 {path} 对应的语言服务器"
        locations = client.definition(path, line - 1, character)
        if not locations:
            return f"ℹ️ `{path}:{line}:{character}` 未找到定义"
        lines = [f"🎯 定义位置（共 {len(locations)} 处）:"]
        for loc in locations:
            preview = f" — {loc.preview}" if loc.preview else ""
            lines.append(f"  📄 `{loc.path}:{loc.line}:{loc.character}`{preview}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ LspDefinition 失败: {e}"


def _exec_lsp_references(path: str, line: int, character: int) -> str:
    try:
        client = _LSP_MANAGER.get_client(path)
        if not client:
            return f"⚠️ 未找到 {path} 对应的语言服务器"
        refs = client.references(path, line - 1, character)
        if not refs:
            return f"ℹ️ `{path}:{line}:{character}` 未找到引用"
        lines = [f"🔍 引用位置（共 {len(refs)} 处）:"]
        for ref in refs:
            lines.append(f"  📄 `{ref.path}:{ref.line}:{ref.character}`")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ LspReferences 失败: {e}"


def _exec_lsp_symbols(path: str) -> str:
    try:
        client = _LSP_MANAGER.get_client(path)
        if not client:
            return f"⚠️ 未找到 {path} 对应的语言服务器"
        symbols = client.symbols(path)
        if not symbols:
            return f"ℹ️ `{path}` 未找到符号"
        lines = [f"📋 符号表: `{path}`（共 {len(symbols)} 个）"]
        kind_icons = {
            "function": "ƒ", "method": "ƒ", "class": "◈", "interface": "◇",
            "module": "📦", "variable": "■", "constant": "🔶", "property": "◈",
            "enum": "📋", "namespace": "📁",
        }
        for sym in symbols:
            icon = kind_icons.get(sym.kind, "•")
            lines.append(f"  {icon} `{sym.name}` ({sym.kind}) at line {sym.line}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ LspSymbols 失败: {e}"


# ═══════════════════════════════════════════════════════════
# Memory — 记忆查询执行器
# ═══════════════════════════════════════════════════════════

def _exec_memory_read(path: str, range_str: str = None) -> str:
    """读取记忆文件，支持行号范围。"""
    try:
        file_path = _resolve_memory_path(path)
        if not os.path.exists(file_path):
            return f"❌ 记忆文件不存在: {path}（实际路径: {file_path}）"

        # 检查缓存
        cache_key = f"read:{file_path}:{range_str or 'full'}"
        if cache_key in _MEMORY_QUERY_CACHE:
            return _MEMORY_QUERY_CACHE[cache_key] + "\n\n_（缓存结果）_"

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if range_str:
            try:
                if "-" in range_str:
                    start, end = map(int, range_str.split("-", 1))
                    lines = content.split("\n")
                    lines = lines[max(0, start-1):end]
                    content = "\n".join(lines)
                else:
                    line_no = int(range_str)
                    lines = content.split("\n")
                    content = lines[min(line_no-1, len(lines)-1)]
            except (ValueError, IndexError):
                pass

        total_lines = content.count("\n") + (1 if content else 0)
        result = f"📄 {path}（{total_lines} 行）\n\n{content}"
        return _cache_query(cache_key, result)
    except Exception as e:
        return f"❌ MemoryRead 失败: {e}"


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


def _exec_memory_search(pattern: str, path: str = None, context: int = 3,
                        case_insensitive: bool = True) -> str:
    """在记忆文件中搜索，默认上下 3 行，结果按 UUID 分组标注。"""
    try:
        home = os.path.expanduser("~")
        base = os.path.join(home, ".ai_s")

        search_dir = base
        if path == "chat":
            search_dir = os.path.join(base, "chat")
        elif path == "library":
            search_dir = os.path.join(base, "library")

        if not os.path.exists(search_dir):
            return f"❌ 记忆目录不存在: {search_dir}"

        cache_key = f"search:{pattern}:{path or 'all'}:{context}:{case_insensitive}"
        if cache_key in _MEMORY_QUERY_CACHE:
            return _MEMORY_QUERY_CACHE[cache_key] + "\n\n_（缓存结果）_"

        import subprocess as _sp
        cmd = ["grep", "-rn"]
        if case_insensitive:
            cmd.append("-i")
        if context and context > 0:
            cmd.extend(["-C", str(context)])
        cmd.extend(["--", pattern, search_dir])

        proc = _sp.run(cmd, capture_output=True, text=True, timeout=30)
        raw = proc.stdout.strip()
        if not raw:
            return f"ℹ️ 在记忆文件中未找到匹配 '{pattern}' 的内容"

        # 按文件分组：遍历每一行，track 当前文件
        # grep -C n 输出:
        #   file:line:content     ← 匹配行
        #   file-line:context     ← 上下文（- 不是 :）
        #   --
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

            # 判断是否是匹配行：filepath:digits:content
            idx = line.find(":")
            if idx <= 0:
                current_block.append(line)
                continue
            maybe_path = line[:idx]
            rest = line[idx+1:]
            idx2 = rest.find(":")
            if idx2 <= 0:
                current_block.append(line)
                continue
            maybe_lineno = rest[:idx2]
            if not maybe_lineno.isdigit():
                current_block.append(line)
                continue

            # 新的匹配行，切换到对应文件
            if maybe_path != current_file:
                _flush_block()
                current_file = maybe_path
            current_block.append(line)

        _flush_block()

        # 构建带 UUID 标注的输出
        out = []
        first = True
        for fpath in file_order:
            lines = groups[fpath]
            uuid = _get_file_uuid(fpath)
            if not first:
                out.append("─" * 40)
            first = False
            out.append(f"📌 UUID: `{uuid}`")
            out.append(f"   路径: {fpath}")
            if fpath.endswith(".txt"):
                out.append(f"   💡 MemoryRead(\"library/{uuid}\") 查看完整会话")
            elif fpath.endswith(".json") and not uuid.startswith("chat/"):
                out.append(f"   💡 MemoryRead(\"library/{uuid}\") 查看完整会话")
            out.append("")
            out.extend(lines)
            out.append("")

        formatted = "\n".join(out)
        if len(formatted) > 20000:
            formatted = formatted[:20000] + "\n\n...（结果过长，已截断）"

        header = (f"🔍 记忆搜索: '{pattern}'"
                  f"\n   范围: {path or '全部'}"
                  f"\n   上下文: ±{context} 行"
                  f"\n   匹配文件: {len(groups)}")
        return _cache_query(cache_key, f"{header}\n\n{formatted}")
    except _sp.TimeoutExpired:
        return f"❌ 记忆搜索超时（30 秒）"
    except Exception as e:
        return f"❌ MemorySearch 失败: {e}"


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
            # 自由输入
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


def _exec_agent(description: str, prompt: str, name: str = "") -> str:
    """启动子代理。"""
    try:
        # 尝试通过 Skill/run_skill 工具执行子代理
        agent_name = name or description[:30]
        try:
            import json as _json
            # 尝试调用 run_skill（工具系统注册的顶层工具）
            from .ai_lib import mcp_registry as _mcp_reg
            result = f"[子代理 {agent_name}]\n任务: {prompt[:200]}...\n\n子代理系统已激活，请使用 Skill 工具或 explore/research 子代理工具执行具体任务。"
        except ImportError:
            result = f"[子代理 {agent_name}]\n请使用 explore 或 research 工具来执行此子任务。"
        return result
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

# ── LSP 客户端管理器 ──
_LSP_MANAGER = LspManager()

# ── 恢复配方 & 审批令牌 ──
_RECOVERY_CTX = RecoveryContext()
_APPROVAL_LEDGER = ApprovalTokenLedger()

# 线程局部存储
import threading as _threading_mod
_thread_locals = _threading_mod.local()


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

    # ── 内置分析工具（不经过 MCP，直接 Python 执行）──
    # 用剥离后的 raw_tool 匹配
    _BUILTIN_HANDLERS = {
        # ── 文件操作 ──
        "validate_edit": lambda p: _exec_validate_edit(p.get("file_path", ""), p.get("search", ""), p.get("replace", "")),
        "preview_edit": lambda p: _exec_preview_edit(p.get("file_path", ""), p.get("search", ""), p.get("replace", "")),
        "get_file_info": lambda p: _exec_get_file_info(p.get("path", "")),
        "read_file":    lambda p: _exec_read_file(p.get("path", ""), p.get("range", None)),
        "write_file":   lambda p: _exec_write_file(p.get("path", ""), p.get("content", "")),
        "edit_file":    lambda p: _exec_edit_file(p.get("path", ""), p.get("old_string", ""), p.get("new_string", "")),
        "glob_search":  lambda p: _exec_glob_search(p.get("pattern", ""), p.get("path", None)),
        "grep_search":  lambda p: _exec_grep_search(p.get("pattern", ""), p.get("path", None), p.get("glob", None),
                                                     p.get("context", 0), p.get("-i", False), p.get("head_limit", None)),
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
        # ── 配置 ──
        "Config":       lambda p: _exec_config(p.get("action", "get"), p.get("key", ""), p.get("value", None)),
        # ── 子代理与输出 ──
        "Agent":        lambda p: _exec_agent(p.get("description", ""), p.get("prompt", ""), p.get("name", "")),
        "StructuredOutput": lambda p: _exec_structured_output(p.get("format", "json"), p.get("data", "")),
        "Sleep":        lambda p: _exec_sleep(int(p.get("seconds", 1))),
        # ── Web ──
        "WebFetch":     lambda p: _exec_web_fetch(p.get("url", ""), p.get("prompt", "")),
        "WebSearch":    lambda p: _exec_web_search(p.get("query", ""), p.get("allowed_domains", None)),
        # ── 情感（内部） ──
        "set_mood":     lambda p: _exec_set_mood(p.get("dimension", ""), float(p.get("delta", 0))),
        "update_people": lambda p: _exec_update_people(p.get("action", ""), p.get("name", ""), p.get("value", "")),

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

        # ── LSP 语言服务 ──
        "LspDiagnostics": lambda p: _exec_lsp_diagnostics(p.get("path", "")),
        "LspHover":       lambda p: _exec_lsp_hover(p.get("path", ""), int(p.get("line", 1)), int(p.get("character", 0))),
        "LspDefinition":  lambda p: _exec_lsp_definition(p.get("path", ""), int(p.get("line", 1)), int(p.get("character", 0))),
        "LspReferences":  lambda p: _exec_lsp_references(p.get("path", ""), int(p.get("line", 1)), int(p.get("character", 0))),
        "LspSymbols":     lambda p: _exec_lsp_symbols(p.get("path", "")),

        # ── 记忆查询 ──
        "MemoryRead":     lambda p: _exec_memory_read(p.get("path", ""), p.get("range")),
        "MemorySearch":   lambda p: _exec_memory_search(
            p.get("pattern", ""), p.get("path"), int(p.get("context", 3)),
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
    }
    if raw_tool in _BUILTIN_HANDLERS:
        try:
            result = _BUILTIN_HANDLERS[raw_tool](params or {})
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


def parse_mcp_tool_calls(text: str) -> List[Dict[str, str]]:
    """
    从 AI 响应中解析 [tool:名称]JSON参数[tool:名称:done] 块（Reasonix 风格）。
    - 工具名: mcp__<server>__<tool> 格式
    - 块体为 JSON 参数字符串
    - 兼容旧格式：[tool:名 空格参数]...[tool:名:done]
    """
    calls = []
    # 新格式: [tool:mcp__server__tool]\n{json}\n[tool:mcp__server__tool:done]
    # 注意: 用 (.+?) 而非 (\{.*?\})，因为 JSON 内容中的 } 会导致非贪婪匹配提前截断
    pattern_new = r'\[tool:(mcp__\S+)\]\n(.+?)\n\[tool:\1:done\]'
    for m in re.findall(pattern_new, text, re.DOTALL):
        full_name = m[0]
        json_body = m[1].strip()
        # 解析 mcp__server__tool → server, tool
        server, tool = _parse_mcp_tool_name(full_name)
        calls.append({
            "name": tool,
            "server": server,
            "full_name": full_name,
            "params_str": json_body,
            "body": json_body,
        })
        continue

    # 兼容旧格式: [tool:名 空格参数]...[tool:名:done]
    pattern_old = r'\[tool:(\S+)\s+([^\]]*)\]\n?(.*?)\n?\[tool:\1:done\]'
    for m in re.findall(pattern_old, text, re.DOTALL):
        old_name = m[0]
        # 如果已经被新模式匹配过就跳过
        if any(c.get("full_name") == old_name for c in calls):
            continue
        # 尝试解析为 mcp__server__tool
        server, tool = _parse_mcp_tool_name(old_name)
        # 尝试将 body 解析为 JSON
        body_text = m[2].strip() if len(m) > 2 else ""
        params = m[1].strip()
        if body_text and body_text.startswith("{"):
            params = body_text  # JSON 在块体中
        calls.append({
            "name": tool,
            "server": server,
            "full_name": old_name,
            "params_str": params,
            "body": body_text if body_text else params,
        })

    return calls


def _parse_mcp_tool_name(full_name: str) -> tuple:
    """解析 mcp__server__tool → (server, tool_name)"""
    if full_name.startswith("mcp__"):
        parts = full_name.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
    return "filesystem", full_name


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
) -> None:
    from io import StringIO
    import sys as sys_module
    from contextlib import contextmanager

    # ── --debug 必须在最开头解析，否则 MCP 初始化卡住时没有追踪输出 ──
    # 每次 handle_ai 调用先复位，避免上次 --debug 残留
    global _MCP_DEBUG, _MCP_DEBUG_START
    _MCP_DEBUG = False
    _MCP_DEBUG_START = 0.0
    debug_mode = False
    if "--debug" in cmd_parts:
        debug_mode = True
        _MCP_DEBUG = True
        _MCP_DEBUG_START = time.time()
        cmd_parts.remove("--debug")
        # 用 stderr 输出确保立即可见（stdout 可能被 Live Panel 等捕获）
        sys_module.stderr.write(f"[{time.time()-_MCP_DEBUG_START:06.2f}s] 🔍 DEBUG 模式已启用 — 实时追踪每个函数调用和耗时\n")
        sys_module.stderr.flush()

    if user_home_dir is None:
        user_home_dir = USER_HOME_DIR
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
    _mcp_debug("── 初始化内置工具 ──")
    ai_tools_prompt = build_native_tools_prompt()
    native_tools = build_native_tools(user_home_dir)

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
            memories = list_chat_memories(user_home_dir)
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
            if switch_chat_memory(user_home_dir, chat_param):
                console.print(lang_text["chat_switched"].format(chat_param), style="bold green")
            else:
                console.print(lang_text["chat_not_found"].format(chat_param), style="bold red")
            return
        elif chat_action == "new":
            name = chat_param if chat_param else datetime.now().strftime('%Y%m%d_%H%M%S')
            if create_chat_memory(user_home_dir, name):
                switch_chat_memory(user_home_dir, name)
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

    # ── TUI 模式已移除（-tui 参数不再支持）──

    # Ctrl+C 打断思考：直接抛出 KeyboardInterrupt 向上传播
    import signal as _signal

    def _on_interrupt(signum, frame):
        global _AI_INTERRUPTED
        _AI_INTERRUPTED = True
        raise KeyboardInterrupt("User interrupted")

    _original_sigint = _signal.signal(_signal.SIGINT, _on_interrupt)

    # 重置中断标志（避免上次 Ctrl+C 残留导致本次立即中断）
    global _AI_INTERRUPTED
    _AI_INTERRUPTED = False

    current_session_id = request_id
    initial_question = content
    last_user_question = content  # 追踪最近一次用户输入，ESC 追问时更新
    continue_asking = True
    interaction_count = 0
    _pending_plan = ""  # 来自 submit_plan 工具调用的计划文本（跨循环持久化）
    plan_confirmed = False  # Plan 模式：计划是否已获用户确认
    referenced_memory_uuid = None
    current_chat_name = get_current_chat_name(user_home_dir)
    message_appended = False
    
    cleanup_output_cache()

    def _ensure_library_record():
        """确保 library 文件存在（plan 流程等提前 continue 可能跳过常规记录）"""
        nonlocal current_session_id
        record_path = os.path.join(
            get_ai_session_library_dir(user_home_dir), f"{current_session_id}.txt"
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
    conversation_history: List[Dict] = []
    import platform as _pf
    _env_info = (
        f"System: {_pf.system()} - {_pf.release()}\n"
        f"User: {os.environ.get('USER', '?')}\n"
        f"Working directory: {os.getcwd()}\n"
        f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"#AI tools\n{ai_tools_prompt}\n"
        f"{mood_context()}\n"
    )
    # 读取 onyx_ai.md 最高指示
    _onyx_prompt_path = os.path.join(user_home_dir, ".ai_s", "onyx_ai.md")
    _onyx_ai_prompt = ""
    if os.path.exists(_onyx_prompt_path):
        try:
            with open(_onyx_prompt_path, "r", encoding="utf-8") as _f:
                _onyx_ai_prompt = _f.read().strip()
        except Exception:
            pass
    if _onyx_ai_prompt:
        _env_info += f"\n#最高指示（持久记忆）\n{_onyx_ai_prompt}\n"

    # ── 项目上下文自动注入（git 状态 + 指令文件）──
    _project_context = ""
    try:
        _git_root = os.getcwd()
        # git status（简短）
        _git_status = _run_shell_cmd("git status --short 2>/dev/null | head -30")
        if _git_status:
            _project_context += f"#Git 状态\n{_git_status}\n"
            # git 当前分支
            _git_branch = _run_shell_cmd("git rev-parse --abbrev-ref HEAD 2>/dev/null")
            if _git_branch:
                _project_context = f"分支: {_git_branch}\n" + _project_context
            # git diff（前 30KB）
            _git_diff = _run_shell_cmd("git diff --no-color 2>/dev/null | head -500")
            if _git_diff and len(_git_diff) > 100:
                _diff_str = _git_diff[:30000]
                if len(_git_diff) > 30000:
                    _diff_str += f"\n…[diff 过长，截断至 30000 字符，共 {len(_git_diff)} 字符]"
                _project_context += f"#Git 变更\n{_diff_str}\n"
            # 最近 5 条 commit
            _git_log = _run_shell_cmd("git log --oneline -5 2>/dev/null")
            if _git_log:
                _project_context += f"#最近提交\n{_git_log}\n"
        # 指令文件自动发现（CLAUDE.md / AGENTS.md / .onyx/rules/*）
        _instruction_files = []
        for _root in [_git_root] if _git_status else [os.getcwd()]:
            for _fname in ["CLAUDE.md", "AGENTS.md", "CLAUDE.local.md"]:
                _fpath = os.path.join(_root, _fname)
                if os.path.exists(_fpath):
                    _instruction_files.append(_fpath)
            # .onyx/ 目录
            _onyx_dir = os.path.join(_root, ".onyx")
            if os.path.isdir(_onyx_dir):
                for _fname in ["CLAUDE.md", "instructions.md"]:
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

    # ── 加载核心系统提示词 agreement.md ──
    try:
        _agreement_paths = [
            os.path.join(ROOT_DIR, "onyx", "etc", "ai", "agreement.md"),
            os.path.join("etc", "ai", "agreement.md"),
        ]
        for _ap in _agreement_paths:
            if os.path.exists(_ap):
                with open(_ap, "r", encoding="utf-8") as _af:
                    _env_info = _af.read() + "\n\n" + _env_info
                break
    except Exception:
        pass

    _system_msg = {"role": "system", "content": _env_info}
    conversation_history.append(_system_msg)
    conversation_history.append({"role": "user", "content": initial_question})

    current_question = initial_question  # 用于日志/估算，API 实际走 conversation_history

    while continue_asking:
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
        
        existing_memory, memory_file = get_latest_ai_session(user_home_dir, current_session_id)
        if memory_file:
            check_session_file_size(memory_file)
        
        memory_section = build_memory_context(
            user_home_dir, current_chat_name, current_session_id,
            referenced_memory_uuid, (interaction_count == 1 and not message_appended), mode
        )

        # AI 引用记忆时显示提示（API 调用前，让用户提前看到）
        if referenced_memory_uuid:
            console.print(
                lang_text["memory_referenced"].format(referenced_memory_uuid[:24] + "..."),
                style="dim cyan"
            )
        
        no_memory_text = lang_text.get("no_memory", "No historical memory" if current_lang == "english" else "无历史记忆")
        # 记忆上下文注入：每次循环都从磁盘加载 library 记忆
        # 系统提示词（agreement/tools/mood）保持在最前面，记忆紧随其后
        if memory_section != no_memory_text:
            _memory_content = f"#聊天记忆\n{memory_section}"
            # 查找是否已有记忆 system 消息，有则更新
            _mem_idx = next((i for i, m in enumerate(conversation_history)
                            if m.get("role") == "system" and m.get("content", "").startswith("#聊天记忆")), None)
            if _mem_idx is not None:
                conversation_history[_mem_idx]["content"] = _memory_content
            else:
                # 插入到第一个 system 消息（核心提示词）之后，保持其最前位置
                _first_sys = next((i for i, m in enumerate(conversation_history)
                                  if m.get("role") == "system" and not m.get("content", "").startswith("#聊天记忆")), None)
                if _first_sys is not None:
                    conversation_history.insert(_first_sys + 1, {"role": "system", "content": _memory_content})
                else:
                    conversation_history.insert(0, {"role": "system", "content": _memory_content})
            
            # 后续循环：只移除已在 library 里的用户提问，不删 system 消息
            if interaction_count > 1:
                _first_asst = next((i for i, m in enumerate(conversation_history)
                                   if m.get("role") == "assistant"), None)
                if _first_asst is not None:
                    # 保留：所有 system 消息 + 第一个 assistant 及其之后的所有消息
                    conversation_history = [m for i, m in enumerate(conversation_history)
                                           if m.get("role") == "system" or i >= _first_asst]
                    # 清理后如果无 user 消息，插入系统信号（告诉 AI 命令已执行、结果在 library 里）
                    _has_user = any(m.get("role") == "user" for m in conversation_history)
                    if not _has_user:
                        if current_lang == "chinese":
                            _continue_prompt = (
                                "上一轮的命令已完成执行，执行结果已记录在上方 #聊天记忆 的 "
                                "当前会话记忆(library) 部分。请根据执行结果判断任务是否完成："
                                "若已完成则用 [ANSWER]yes 正常结束，若需继续则继续生成下一步命令。"
                            )
                        else:
                            _continue_prompt = (
                                "The previous round of commands has finished executing. "
                                "Results are recorded in the #聊天记忆 Current Session Memory (library) section above. "
                                "Please check the results and determine if the task is complete: "
                                "if done, set [ANSWER]yes; if more work is needed, continue with next commands."
                            )
                        conversation_history.append({"role": "user", "content": _continue_prompt})

        # Plan 模式前缀：告知 AI 当前处于 plan 模式，禁止执行命令和文件修改
        # mode=="plan"（用户 ai plan 命令）或 _PLAN_MODE_ACTIVE（AI 调用 EnterPlanMode）
        if mode == "plan" or _PLAN_MODE_ACTIVE:
            plan_warning = lang_text.get("plan_mode_warning",
                "⚠️ 当前处于 PLAN 模式。你只能生成计划，不能执行任何命令或修改文件。"
                "请使用 [plan]...[plan:done] 格式输出你的计划。"
                "等用户确认后，才能进入执行阶段。"
                "如果要退出 plan 模式，请调用 ExitPlanMode 工具。")
            conversation_history.append({"role": "system", "content": plan_warning})
        
        # 流式展示：Rich Live Panel — 实时更新 AI 回答
        from rich.live import Live
        from rich.panel import Panel
        from rich.box import ROUNDED
        
        # ── 多块流式状态机：每个字段类型独立缓冲区 + 独立 Panel ──
        stream_buffer = ""        # 累积原始流式文本
        txt_content = ""          # [TXT]...[TXT:DONE] 或 [TXT]:... 主回复内容
        analysis_content = ""     # [ANALYSIS]:... 或 [ANALYSIS]...[ANSWER] 分析内容
        plan_content = ""         # [plan]...[plan:done] 计划内容
        ask_content = ""          # [ASK]:... 追问内容
        answer_state = ""         # [ANSWER]:yes/no
        memory_uuid = ""          # [MEMORY]:uuid
        tag_val = ""              # [TAG]:value
        prompt_val = ""           # [PROMPT]:value — 写入 .ai_s/onyx_ai.md
        live_ref = [None]         # Live 对象引用
        loading_flag = [True]
        tool_results_display = []  # 工具执行结果（用于面板展示：名前10行灰色虚影）
        _txt_phase = "pre"        # "pre" | "in_txt" | "post_txt"

        _SAFE_MARGIN = 20  # 安全缓冲（覆盖最长标记 [TXT:DONE]=10, [plan:done]=11）

        def _strip_markers(text: str) -> str:
            """去除所有格式标记，只保留纯文本（行首标记 + @@SHELL 块）"""
            import re as _re
            # 多行块闭合标记（可能单独成行残留）
            text = _re.sub(r'\[TXT:DONE\]', '', text)
            text = _re.sub(r'\[ANALYSIS:DONE\]', '', text)
            text = _re.sub(r'\[PLAN:DONE\]', '', text)
            text = _re.sub(r'\[PROMPT:DONE\]', '', text)
            text = _re.sub(r'\[TAG:DONE\]', '', text)
            # 行首单行标记（只移除标记本身，保留标记后的内容）
            text = _re.sub(r'^\[TXT\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[ANALYSIS\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[ANSWER\]:?\w*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[ASK\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[TAG\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[CLASS[^\]]*\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[SLEEP\]:?\d*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[MEMORY\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[plan(?:\:done)?\]\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[tool:\S+\]?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[tool:\S+:done\]\s*', '', text, flags=_re.MULTILINE)
            # @@SHELL / @@CMD 命令块 — 独立成行 + 同行粘连都过滤
            text = _re.sub(r'^@@SHELL\s*$.*?(?=^@@|\Z)', '', text,
                           flags=_re.MULTILINE | _re.DOTALL)
            text = _re.sub(r'^.*@@SHELL.*$\n?', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^@@CMD\s*$.*?(?=^@@|\Z)', '', text,
                           flags=_re.MULTILINE | _re.DOTALL)
            text = _re.sub(r'^.*@@CMD.*$\n?', '', text, flags=_re.MULTILINE)
            # >>>>>>>>>> 分隔符 — 独立成行 + 同行粘连都过滤
            text = _re.sub(r'^>{8,}\s*$', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^.*>{8,}.*$\n?', '', text, flags=_re.MULTILINE)
            return text.strip()

        def _write_onyx_ai_prompt(content: str, home_dir: str = None) -> None:
            """将 AI 的 [PROMPT]: 内容追加写入 ~/.ai_s/onyx_ai.md（纯粹追加）"""
            if not content.strip():
                return
            prompt_dir = home_dir if home_dir else os.path.expanduser("~")
            prompt_file = os.path.join(prompt_dir, ".ai_s", "onyx_ai.md")
            try:
                os.makedirs(os.path.dirname(prompt_file), exist_ok=True)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                entry = f"\n\n> [{timestamp}]\n\n{content.strip()}\n"
                with open(prompt_file, "a", encoding="utf-8") as f:
                    f.write(entry)
                _mcp_debug(f"[PROMPT] 已追加到 {prompt_file}: {content[:80]}...")
                # 控制台可见确认（不用 debug 模式也能看到）
                try:
                    console.print(f"📝 最高指示已更新: {content[:60]}{'...' if len(content) > 60 else ''}", style="dim cyan")
                except Exception:
                    pass
            except Exception as e:
                _mcp_debug(f"[PROMPT] 写入失败: {e}")
                try:
                    console.print(f"⚠️ 最高指示写入失败: {e}", style="bold red")
                except Exception:
                    pass

        def _render_all_panels():
            """将所有已接收的内容块组合为复合 Panel"""
            from rich.console import Group
            from rich.markdown import Markdown
            from rich.text import Text

            parts = []

            # 流式文本：只在 TXT 块闭合后显示绿色面板，流式中显示灰色预览
            if txt_content.strip():
                cleaned = _strip_markers(txt_content)
                if cleaned.strip():
                    if _txt_phase == "post_txt":
                        # TXT 块已闭合 → 绿色正式面板
                        parts.append(Panel(Markdown(cleaned.strip()),
                                           title="💬 回复", border_style="green", box=ROUNDED))
                    else:
                        # 仍在流式接收 → 灰色预览（最后100字符）——避免刷屏
                        tail = cleaned.strip()[-100:] if len(cleaned.strip()) > 100 else cleaned.strip()
                        if tail:
                            parts.append(Text(tail, style="dim"))

            # 分析 Panel
            if analysis_content.strip():
                parts.append(Panel(Markdown(analysis_content.strip()),
                                   title="📊 分析", border_style="blue", box=ROUNDED))

            # 计划 Panel
            if plan_content.strip():
                parts.append(Panel(Markdown(plan_content.strip()),
                                   title="📋 计划", border_style="cyan", box=ROUNDED))

            # 追问 Panel
            if ask_content.strip():
                parts.append(Panel(ask_content.strip(),
                                   title="🤔 追问", border_style="yellow", box=ROUNDED))

            # MCP 工具执行结果（前4行）
            if tool_results_display:
                for tr in tool_results_display:
                    icon = "✅" if tr["ok"] else "❌"
                    style = "dim green" if tr["ok"] else "dim red"
                    header = f"{icon} {tr['name']}"
                    body = tr.get("preview", tr.get("output", "")[:100])
                    _total = len(tr.get("output", ""))
                    if _total > 100:
                        body += f"\n…(共 {_total} 字符，完整输出已保留)"
                    parts.append(Panel(body, title=header, border_style=style, box=ROUNDED,
                                       padding=(0, 1)))

            if not parts:
                return Panel(Spinner("dots", text=_mcp_t(" 思考中...", " Thinking..."),
                                     style="bold cyan"),
                            title="🤖 AI", border_style="green", box=ROUNDED)

            if len(parts) == 1:
                return parts[0]
            return Group(*parts)

        # ── MCP 路径安全校验器（桥接 Onyx 沙箱与 MCP 工具执行）──
        def _mcp_path_validator(tool: str, path: str) -> Tuple[bool, str]:
            """校验 MCP 工具操作的路径是否在 Onyx 沙箱允许范围内"""
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
                    "name": tool_name, "params": params_str[:80],
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
            # 取前100字符用于面板展示
            _preview = output[:100] + ("..." if len(output) > 100 else "")
            tool_results_display.append({
                "name": tool_name, "params": params_str[:80],
                "ok": ok, "output": output,
                "preview": _preview
            })

        def _try_extract_blocks() -> None:
            """从 stream_buffer 中扫描所有已知块类型，分发到对应缓冲区并实时执行工具"""
            import re as _re
            nonlocal stream_buffer, txt_content, analysis_content, plan_content
            nonlocal ask_content, answer_state, memory_uuid, tag_val, prompt_val
            nonlocal _txt_phase

            # 连续扫描直到无法再提取完整块
            max_iter = 50  # 安全上限，防止死循环
            for _ in range(max_iter):
                buf = stream_buffer
                if not buf:
                    break
                # 前导换行会让 _re.match 失效（[TXT] 块被 _re.search 消费后剩余 \n[ANSWER]...）
                # buf_match 用于 _re.match 模式，buf 用于 _re.search 模式
                buf_match = buf.lstrip('\n\r ')
                match_offset = len(buf) - len(buf_match)

                # ── [TXT]...[TXT:DONE] 多行块 ──
                # (?![:D]) 防止误匹配 [TXT]: 和 [TXT:DONE] 前缀
                # 不要求 \n 在 [TXT] 前，支持 [TXT]content 同行格式
                m = _re.search(r'\[TXT\](?![:D])(.*?)\[TXT:DONE\]', buf, _re.DOTALL)
                if m:
                    block_text = m.group(1)
                    txt_content += block_text  # 追加而非覆盖
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    _txt_phase = "post_txt"
                    # ── 扫描 TXT 块内是否嵌套 [ANSWER]yes/no（AI 可能违反格式规范）──
                    ans_inner = _re.search(r'\[ANSWER\](yes|no)', block_text)
                    if ans_inner and not answer_state:
                        answer_state = ans_inner.group(1)
                    continue

                # ── [TXT]: 单行（新格式，逐行提取）──
                m = _re.match(r'\[TXT\]:(.*?)(\n|$)', buf_match)
                if m:
                    txt_content += m.group(1) + "\n"
                    stream_buffer = buf[match_offset + m.end():]
                    _txt_phase = "in_txt"
                    continue

                # ── [PLAN]...[PLAN:DONE] 多行块（大写新格式，优先）──
                m = _re.search(r'\[PLAN\](.*?)\[PLAN:DONE\]', buf, _re.DOTALL)
                if m:
                    plan_content += m.group(1).strip()
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    continue

                # ── [plan]...[plan:done] 多行块（小写旧格式，兼容）──
                m = _re.search(r'\[plan\]\n(.*?)\[plan:done\]', buf, _re.DOTALL)
                if m:
                    plan_content += m.group(1)
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    continue

                # ── [ANALYSIS]...[ANALYSIS:DONE] 多行块（优先于单行格式）──
                m = _re.search(r'\[ANALYSIS\](?![:D])(.*?)\[ANALYSIS:DONE\]', buf, _re.DOTALL)
                if m:
                    analysis_content += m.group(1).strip()
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    continue

                # ── [ANALYSIS]: 单行（兼容）──
                m = _re.match(r'\[ANALYSIS\]:(.*?)(\n|$)', buf_match)
                if m:
                    analysis_content += m.group(1) + "\n"
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [ANALYSIS]\n...[下一个 [XXX] 标记] 多行块（兼容旧格式）──
                # 原版只认 [ANSWER] 终止，若 AI 输出 [ANALYSIS]\n内容\n[TXT] 会死锁
                # 改为任意下一行 [ 开头的标记均可终止
                m = _re.search(r'\[ANALYSIS\]\n(.*?)(?=\n\[)', buf, _re.DOTALL)
                if m:
                    analysis_content += m.group(1).strip()
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    continue

                # ── [ANSWER]:yes/no ──
                m = _re.match(r'\[ANSWER\]:(yes|no)', buf_match)
                if m:
                    answer_state = m.group(1)
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [ANSWER]yes/no（无冒号）──
                m = _re.match(r'\[ANSWER\](yes|no)', buf_match)
                if m:
                    answer_state = m.group(1)
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [ASK]:text ──
                m = _re.match(r'\[ASK\]:(.*?)(\n|$)', buf_match)
                if m:
                    ask_content = m.group(1).strip()
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [MEMORY]:uuid ──
                m = _re.match(r'\[MEMORY\]:(.*?)(\n|$)', buf_match)
                if m:
                    memory_uuid = m.group(1).strip()
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [TAG]:value ──
                m = _re.match(r'\[TAG\]:(.*?)(\n|$)', buf_match)
                if m:
                    tag_val = m.group(1).strip()
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [PROMPT]...[PROMPT:DONE] 多行块（优先于单行格式）──
                m = _re.search(r'\[PROMPT\](?![:D])(.*?)\[PROMPT:DONE\]', buf, _re.DOTALL)
                if m:
                    prompt_val = m.group(1).strip()
                    if prompt_val:
                        _write_onyx_ai_prompt(prompt_val, user_home_dir)
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    continue

                # ── [PROMPT]:value（单行兼容）──
                m = _re.match(r'\[PROMPT\]:(.*?)(\n|$)', buf_match)
                if m:
                    prompt_val = m.group(1).strip()
                    if prompt_val:
                        _write_onyx_ai_prompt(prompt_val, user_home_dir)
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [PROMPT]text（无冒号单行兼容）──
                m = _re.match(r'\[PROMPT\](.*?)(\n|$)', buf_match)
                if m:
                    prompt_val = m.group(1).strip()
                    if prompt_val:
                        _write_onyx_ai_prompt(prompt_val, user_home_dir)
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [mood]: happy +0.1 / [mood]: angry -0.2 ──
                m = _re.match(r'\[mood\]:\s*(\S+)\s+([+-]\d+(?:\.\d+)?)(?:\n|$)', buf_match)
                if m:
                    try:
                        apply_mood_delta(m.group(1), float(m.group(2)))
                    except ValueError:
                        pass
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [PEOPLE]:add/Likeability/Perception ──
                m = _re.match(r'\[PEOPLE\]:\s*(\S+)\s+(.+?)(?:\n|$)', buf_match)
                if m:
                    action = m.group(1)
                    rest = m.group(2).strip()
                    if action.lower() == "add":
                        apply_people_action("add", rest)
                    elif action.lower() == "likeability":
                        parts = rest.rsplit(None, 1)
                        if len(parts) == 2:
                            apply_people_action("likeability", parts[0], parts[1])
                    elif action.lower() == "perception":
                        parts = rest.split(None, 1)
                        if len(parts) == 2:
                            apply_people_action("perception", parts[0], parts[1])
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [CLASS]:N / [SLEEP]:N（元数据，静默消费）──
                m = _re.match(r'\[(?:CLASS|SLEEP)\]:(.*?)(\n|$)', buf_match)
                if m:
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [tool:name]\n{json}\n[tool:name:done] 新格式 ──
                # 注意: 用 (.+?) 而非 (\{.*?\})，因为 JSON 内容中的 } 会导致非贪婪匹配提前截断
                m = _re.search(r'\[tool:(\S+)\]\n(.+?)\n\[tool:\1:done\]', buf, _re.DOTALL)
                if m:
                    tool_name = m.group(1)
                    params_str = m.group(2).strip()
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    _execute_single_tool(tool_name, params_str)
                    continue

                # ── [tool:name 空格参数]...[tool:name:done] 旧格式 ──
                m = _re.search(r'\[tool:(\S+)\s+([^\]]*)\](.*?)\[tool:\1:done\]', buf, _re.DOTALL)
                if m:
                    tool_name = m.group(1)
                    params_str = m.group(2).strip()
                    body = m.group(3).strip() if m.lastindex and m.lastindex >= 3 else ""
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    params = _parse_tool_params(params_str, body)
                    import json as _json
                    ps = _json.dumps(params, ensure_ascii=False) if isinstance(params, dict) else str(params)
                    _execute_single_tool(tool_name, ps)
                    continue

                # ── [ANSWER]（无冒号，多行格式的结束标记，静默消费）──
                m = _re.match(r'\[ANSWER\]\s*(\n|$)', buf_match)
                if m:
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── 裸文本行（无 [ 前缀）→ pre/in_txt 阶段收集到 txt ──
                if _txt_phase in ("pre", "in_txt"):
                    m = _re.match(r'^([^\[]+)', buf_match)
                    if m:
                        raw = m.group(1)
                        # 只移除控制字符（回车/换行/空字节），保留空格和制表符以保持缩进
                        clean = raw.lstrip('\r\n\0')
                        if clean:
                            # 只保留安全部分：如果文本末尾可能是不完整的标记开始符，保守截断
                            to_take = clean
                            stream_buffer = buf[match_offset + len(raw):]
                            txt_content += to_take
                            continue
                        elif raw and raw != clean:
                            # 只有控制字符 → 丢弃它们
                            stream_buffer = buf[match_offset + len(raw):]
                            continue

                break  # 无法再提取任何完整块

        def on_stream_content(chunk: str) -> None:
            """实时流式回调：统一提取所有块类型并更新复合 Panel"""
            nonlocal stream_buffer, txt_content, _content_started
            _content_started = True  # 首次收到内容，切换到内容面板

            # 规范化换行符 + 去除原始回车符（防止 ^M 污染显示）
            chunk = chunk.replace('\r\n', '\n').replace('\r', '\n')

            stream_buffer += chunk

            # _try_extract_blocks 负责从 stream_buffer 提取文本并追加到 txt_content

            # 防止缓冲区无限增长（异常情况下丢旧数据）
            if len(stream_buffer) > 50000:
                stream_buffer = stream_buffer[-5000:]

            # 提取所有完整块（处理 [TXT]/[ANSWER]/[TAG] 等结构化标记）
            _try_extract_blocks()

            # 更新 Live Panel
            if live_ref[0]:
                live_ref[0].update(_render_all_panels())
        
        # 启动 Live Panel：动画 spinner + 流式展示
        from rich.spinner import Spinner
        spinner = Spinner("dots", text=_mcp_t(" 思考中...", " Thinking..."), style="bold cyan")
        initial_panel = Panel(spinner, title="🤖 AI", border_style="green", box=ROUNDED)
        
        ai_result = {}
        _live_shown = False  # 标记 Live Panel 是否已展示（避免重复 console.print）
        try:
            if log_info:
                log_info(lang_text["api_call"].format(current_question[:50]), current_session_id)

            with Live(initial_panel, console=console, refresh_per_second=15, transient=False) as live:
                live_ref[0] = live
                loading_flag[0] = False  # Live Panel 已接管展示
                
                # 使用SSE模式调用（带实时流式回调）
                _mcp_debug(f"调用 call_ai_api_sse(messages={len(conversation_history)}条)")
                # ── debug 模式：把 AI 真实看到的 conversation 写入 deb/{session_id}/round_N.txt ──
                if debug_mode:
                    _deb_session_dir = os.path.join(user_home_dir, ".ai_s", "deb", current_session_id)
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
                    # Plan 模式：未确认前只暴露计划相关工具，禁止 AI 探索/执行
                    if (mode == "plan" or _PLAN_MODE_ACTIVE) and not plan_confirmed:
                        _plan_only = {"submit_plan", "mark_step_complete", "ExitPlanMode", "choose_ask"}
                        _active_tools = [t for t in native_tools
                                         if t.get("function", {}).get("name") in _plan_only]
                    else:
                        _active_tools = native_tools
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
                    user_home_dir=user_home_dir,
                    tools=_active_tools,
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
                        parsed_txt = _strip_markers(parsed_txt)
                        live.update(render_ai_panel(parsed_txt))
                        _live_shown = True
                    elif api_error:
                        err_short = api_error[:200] + ("..." if len(api_error) > 200 else "")
                        live.update(Panel(f"❌ {err_short}", title="🤖 AI", border_style="red", box=ROUNDED))
                        _live_shown = True
            
            # SSE返回的已经是解析好的dict
            if isinstance(api_raw_result, dict):
                ai_result = api_raw_result
            else:
                ai_result = {"error": f"Format error: {str(api_raw_result)[:50]}", "answer": "no", "ask": ""}
                live_ref[0] = None
                
        except Exception as e:
            ai_result = {"error": f"SSE processing error: {str(e)}", "answer": "no", "ask": ""}
        finally:
            loading_flag[0] = False
            live_ref[0] = None
        
        ai_result = process_ai_result_fields(ai_result)

        # ── 流式解析的 answer_state 合并到 ai_result（流式解析能捕获 TXT 块内嵌套的 [ANSWER]）──
        if answer_state:
            ai_result["answer"] = answer_state

        # 处理 [PROMPT]: 字段 — 写入 .ai_s/onyx_ai.md 最高指示
        _prompt_from_result = ai_result.get("prompt", "") or prompt_val
        if _prompt_from_result.strip():
            _write_onyx_ai_prompt(_prompt_from_result, user_home_dir)

        was_interrupted = ai_result.get("_interrupted", False)
        if was_interrupted:
            continue_asking = False  # don't auto-loop, but still process any commands below
        
        has_error = "error" in ai_result and ai_result["error"]
        has_txt = ai_result.get("txt", "").strip() if ai_result.get("txt") else False
        answer = ai_result.get("answer", "no")
        ai_ask = ai_result.get("ask", "") or ""
        tag = ai_result.get("tag", "") or ""
        memory_uuid = ai_result.get("memory", "") or ""
        # 优先用 [PLAN] 文本标记，其次用 submit_plan 工具结果
        plan_text = ai_result.get("plan", "") or _pending_plan or ""
        tool_calls = ai_result.get("tool_calls", [])
        sleep_value = ai_result.get("sleep")
        class_level = ai_result.get("class", "1")
        
        sleep_seconds = 0
        if sleep_value is not None:
            try:
                sleep_seconds = int(sleep_value)
            except (ValueError, TypeError):
                sleep_seconds = 0
        
        if sleep_seconds > 0 and answer == "no":
            interrupted, waited_seconds = handle_sleep_wait(sleep_seconds, current_session_id, lang_text, log_info)
            
            _md = current_lang == "english"
            sleep_record = f"\n\n### {'Sleep' if _md else '休眠'} ({time.strftime('%H:%M:%S')})\n\n"
            if interrupted:
                sleep_record += f"- {'Interrupted after' if _md else '中断于'} {waited_seconds}/{sleep_seconds}s\n"
            else:
                sleep_record += f"- {'Completed' if _md else '完成'} {sleep_seconds}s\n"
            
            existing_content, record_path = get_latest_ai_session(user_home_dir, current_session_id)
            if record_path:
                try:
                    with open(record_path, "a", encoding="utf-8") as f:
                        f.write(sleep_record)
                except Exception:
                    pass
            
            continue
        
        if memory_uuid and not referenced_memory_uuid:
            referenced_memory_uuid = memory_uuid
            console.print(lang_text["memory_referenced"].format(memory_uuid[:8] + "..."), style="bold cyan")
        
        if has_error:
            error_str = str(ai_result["error"])
            if "Request failed" in error_str or "Connection" in error_str or "timeout" in error_str.lower():
                console.print(lang_text["api_conn_fail"], style="bold red")
            else:
                console.print(f"❌ {lang_text['api_error'].format(error_str)}", style="bold red")
            if log_error:
                log_error(f"AI error: {error_str}", current_session_id)
            continue_asking = False
            continue
        
        if not message_appended and (has_txt or ai_ask):
            message_id = append_message_to_chat(
                user_home_dir, current_chat_name, current_session_id,
                last_user_question, ai_result.get("txt", ""), tag, class_level
            )
            message_appended = True
            if debug_mode:
                debug_prefix = "[DEBUG] " if current_lang == "english" else "[DEBUG] "
                console.print(debug_prefix + f"Message appended: {message_id}", style="bold magenta")
        elif message_appended and tag:
            update_message_tag(user_home_dir, current_chat_name, current_session_id, tag, class_level)
            if debug_mode:
                debug_prefix = "[DEBUG] " if current_lang == "english" else "[DEBUG] "
                console.print(debug_prefix + f"Tag updated: {tag[:50]}...", style="bold magenta")
        elif message_appended and answer == "yes":
            update_message_tag(user_home_dir, current_chat_name, current_session_id, tag, class_level)
        
        if ai_ask.strip():
            # 如果已通过流式展示了 txt 内容，不再重复打印
            if has_txt and not txt_content.strip():
                console.print(lang_text["ai_answer"], style="bold green")
                console.print("-" * 50, style="white")
                for line in ai_result["txt"].strip().split('\n'):
                    console.print(line, style="white")
                console.print("-" * 50, style="white")
            
            # Rich Panel 展示 AI 提问
            console.print(Panel(
                ai_ask.strip(),
                title="🤔 " + lang_text.get("ai_ask", "AI 提问"),
                border_style="yellow",
                box=ROUNDED,
                padding=(1, 2),
            ))
            
            try:
                user_answer = ui_text_input("💬 You").strip()
                last_user_question = user_answer  # 记录追问，供聊天记忆使用
                message_appended = False           # 新输入 → 允许追加新消息
                # 标准 messages：AI 提问 + 用户回答
                _ask_msg = {"role": "assistant", "content": ai_ask.strip()}
                _ask_reasoning = ai_result.get("_reasoning", "")
                if _ask_reasoning:
                    _ask_msg["reasoning_content"] = _ask_reasoning
                conversation_history.append(_ask_msg)
                conversation_history.append({"role": "user", "content": user_answer})
                current_question = f"{current_question}\n\nUser answer: {user_answer}" if current_lang == "english" else f"{current_question}\n\n用户回答：{user_answer}"
                continue_asking = True
                
                if interaction_count == 1:
                    record_ai_session(user_home_dir, current_session_id, initial_question, ai_result, user_answer, {}, referenced_memory_uuid or "", native_results="")
                else:
                    existing_content, record_path = get_latest_ai_session(user_home_dir, current_session_id)
                    if existing_content and record_path:
                        _ts = time.strftime('%Y-%m-%d %H:%M:%S')
                        _md = current_lang == "english"
                        new_content = f"\n\n### {'Interaction' if _md else '交互'} #{interaction_count} ({_ts})\n\n"
                        new_content += f"- **{'AI Ask' if _md else 'AI询问'}**:\n  {ai_ask.strip()}\n"
                        new_content += f"- **{'User Answer' if _md else '用户回答'}**:\n  {user_answer}\n"
                        try:
                            with open(record_path, "a", encoding="utf-8") as f:
                                f.write(new_content)
                        except Exception:
                            pass
                
                continue
            except KeyboardInterrupt:
                console.print("\n^C", style="bold yellow")
                user_answer = "User cancelled the answer" if current_lang == "english" else "用户取消了回答"
                continue_asking = False
            except EOFError:
                console.print("\n^D", style="bold yellow")
                user_answer = "User terminated the session" if current_lang == "english" else "用户终止了会话"
                continue_asking = False
        
        # 如果已通过流式或 Live Panel 展示了 txt 内容，不再重复打印
        # _live_shown 在 Live 块内设为 True，避免 Live 结束后 console.print 再打一遍
        if has_txt and not ai_ask.strip() and not _live_shown:
            cleaned_txt = _strip_markers(ai_result["txt"])
            console.print(render_ai_panel(cleaned_txt.strip()))
        
        ai_commands = extract_ai_commands(ai_result)
        # 硬限制：最多执行 10 条命令，超出的丢弃并通知 AI
        if len(ai_commands) > 10:
            _discarded = ai_commands[10:]
            ai_commands = ai_commands[:10]
            _warn = lang_text.get("cmd_limit", "⚠️ 命令超过 10 条限制，已截断前 10 条执行") if False else "⚠️ 命令超过 10 条限制，已截断前 10 条执行"
            console.print(f"  [bold yellow]{_warn}[/]")
            conversation_history.append({"role": "system", "content": f"[SYSTEM] {_warn}。多余的 {len(_discarded)} 条命令被丢弃，请下一轮继续。"})
        analysis_content = (ai_result.get("analysis", "") or "").strip()
        
        if ai_commands and not analysis_content:
            analysis_content = lang_text["analysis_cmd_prefix"].format(len(ai_commands))
            for idx, cmd in enumerate(ai_commands, 1):
                analysis_content += f"{idx}. {cmd}\n"
        
        if analysis_content:
            console.print(render_analysis_panel(analysis_content))
        
        # ── Token usage stats (from stream_options.include_usage) ──
        _usage_info = ai_result.get("_usage")
        if _usage_info:
            _total = _usage_info.get("total_tokens", 0)
            _prompt = _usage_info.get("prompt_tokens", 0)
            _completion = _usage_info.get("completion_tokens", 0)
            _cache_hit = _usage_info.get("prompt_cache_hit_tokens", 0)
            _cache_miss = _usage_info.get("prompt_cache_miss_tokens", 0)
            # 存下精确 prompt_tokens（末尾显示用，纯磁盘架构不依赖内存 tracker）
            if _prompt:
                _thread_locals.last_prompt_tokens = _prompt
            parts = [f"⚡ {_total} tokens"]
            if _cache_hit:
                saved_pct = _cache_hit / (_cache_hit + _cache_miss) * 100 if (_cache_hit + _cache_miss) else 0
                parts.append(f"💰 cache {saved_pct:.0f}% hit")
            console.print(f"  [dim]{' · '.join(parts)}[/]")
        
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
                conversation_history.append({"role": "user", "content": "[用户摒弃了你的计划，请重新制定]"})
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
                    conversation_history.append({"role": "user", "content": f"[用户对计划的指导意见]:\n{guide_text}\n\n请根据指导意见修改计划。"})
                else:
                    conversation_history.append({"role": "user", "content": "[用户未提供具体意见，请简化或重新生成计划]"})
                _pending_plan = ""
                continue_asking = True
                continue

            elif plan_choice == "confirm":
                console.print(lang_text.get("plan_confirmed", "✅ 计划已确认，即将进入执行阶段"), style="bold green")
                # 将确认后的计划内容追加到 AI 的上下文
                conversation_history.append({"role": "user", "content": f"[用户已确认以下计划，请开始执行]:\n{_plan_display}"})
                _pending_plan = ""
                plan_confirmed = True
                continue_asking = True
                continue

        # Plan 模式安全限制：未确认计划前，拦截所有命令执行和工具调用
        # 既支持 mode=="plan"（用户输入 ai plan），也支持 _PLAN_MODE_ACTIVE（AI 调用 EnterPlanMode）
        if (mode == "plan" or _PLAN_MODE_ACTIVE) and not plan_confirmed:
            if ai_commands or tool_calls:
                console.print(lang_text.get("plan_blocked",
                    "⛔ Plan 模式：AI 命令/工具调用已被拦截。请先确认计划。"), style="bold red")
                # 告诉 AI 为什么被拦 + 应该怎么做
                conversation_history.append({
                    "role": "system",
                    "content": _mcp_t(
                        "[Plan 模式] 你的命令/工具调用已被拦截，因为你尚未提交计划。"
                        "请使用 submit_plan 工具或 [PLAN]...[PLAN:DONE] 格式提交你的计划。"
                        "用户会审核并确认后，才能进入执行阶段。",
                        "[Plan mode] Your commands/tools were blocked because you haven't submitted a plan yet."
                        " Please use submit_plan tool or [PLAN]...[PLAN:DONE] format to submit your plan."
                        " The user will review and confirm before execution is allowed."
                    )
                })
            ai_commands = []
            tool_calls = []
        
        # ── 工具结果收集器（仅 function calling）──
        tool_results = []

        # ── 工具结果追加到 conversation_history（让 AI 立刻看到）──
        if tool_results and not tool_calls:
            _native_feedback = "\n".join(tool_results)
            if _native_feedback.strip():
                conversation_history.append({"role": "system", "content": f"[NATIVE_RESULT]\n{_native_feedback.strip()}"})

        # 处理 AI 工具调用 ([tool:...] 格式)
        if tool_calls:
            try:
                tc = tool_calls[0]
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
                            for _t in build_native_tools():
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

                # 显示工具调用 + 关键参数（path、pattern 等）
                _param_preview = ""
                for _key in ("path", "pattern", "task_id", "cron_id", "team_id", "query", "url", "name", "prompt"):
                    _val = params.get(_key, "")
                    if _val:
                        _short_val = str(_val)[:80]
                        _param_preview = f" {_key}={_short_val}"
                        break
                _is_builtin = tool_name in (
                    "read_file","write_file","edit_file","get_file_info",
                    "glob_search","grep_search","validate_edit","preview_edit",
                    "ToolSearch","Skill","TodoWrite","Sleep","StructuredOutput",
                    "submit_plan","mark_step_complete","EnterPlanMode","ExitPlanMode",
                    "choose_ask","Config","Agent","WebFetch","WebSearch",
                    "TaskCreate","TaskList","TaskGet","TaskUpdate","TaskStop",
                    "TaskBoard","TaskRemove","TeamCreate","TeamList","TeamDelete",
                    "CronCreate","CronList","CronDisable","CronDelete",
                    "LspDiagnostics","LspHover","LspDefinition","LspReferences","LspSymbols",
                    "MemoryRead","MemorySearch","UndoLastEdit",
                )
                _tag = "" if _is_builtin else " [MCP]"
                console.print(f"  [bold green]🔧 {_tool_display_name}{_tag}[/]{_param_preview}")

                # 流式执行：用 Status spinner 展示工具运行过程
                from rich.status import Status as _RichStatus
                _status = _RichStatus(f"  [dim]⏳ {_tool_display_name} 运行中…[/]", spinner="dots", console=console)
                _status.start()
                try:
                    # 先尝试内置 handler，走不通再走 MCP
                    ok, output = execute_mcp_tool(tool_name, params, "filesystem", _current_user_mode,
                                                  path_validator=_mcp_path_validator)
                finally:
                    _status.stop()

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
                        conversation_history.append({"role": "system", "content": f"[STORM_WARNING] {_storm_warn}"})
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
                tc_ids = [f"call_{interaction_count}_{i}" for i in range(len(tool_calls))]
                import json as _json
                _tool_call_items = []
                for i, tc in enumerate(tool_calls):
                    _raw_args = tc.get("params_str", "{}")
                    try:
                        _parsed = _json.loads(_raw_args)
                        _args_str = _json.dumps(_parsed, ensure_ascii=False)
                    except (_json.JSONDecodeError, ValueError):
                        _args_str = _raw_args
                    _tool_call_items.append({
                        "id": tc_ids[i],
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": _args_str,
                        }
                    })
                _reasoning = ai_result.get("_reasoning", "")
                _assistant_msg = {
                    "role": "assistant",
                    "content": None,  # DeepSeek thinking mode 要求 tool_call 时 content 为 null
                    "tool_calls": _tool_call_items,
                }
                if _reasoning:
                    _assistant_msg["reasoning_content"] = _reasoning
                conversation_history.append(_assistant_msg)
                # tool role 结果消息
                # 安全垫：确保 tool_results 长度与 tool_calls 一致，避免 API 报错
                _tool_results_safe = list(tool_results)
                while len(_tool_results_safe) < len(tc_ids):
                    _tool_results_safe.append("⚠️ 该工具未执行（结果丢失）")
                for i, res in enumerate(_tool_results_safe):
                    conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tc_ids[i],
                        "content": res,
                    })
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
        if _ai_txt and not tool_calls:
            _assistant_msg = {"role": "assistant", "content": _ai_txt}
            _reasoning = ai_result.get("_reasoning", "")
            if _reasoning:
                _assistant_msg["reasoning_content"] = _reasoning
            conversation_history.append(_assistant_msg)

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
                                f"[用户拒绝了你的命令: {cmd[:200]}] 原因: {refuse_reason}。请换一种方式。",
                                f"[User rejected your command: {cmd[:200]}] Reason: {refuse_reason}. Please try a different approach."
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
                                f"[你的命令包含被禁止的语法，已被拦截: {cmd[:200]}]",
                                f"[Your command contains forbidden syntax and was blocked: {cmd[:200]}]"
                            )
                        })
                    else:
                        allowed_cmds.append(cmd)
                ai_commands = allowed_cmds
                if not ai_commands and original_cmd_count > 0:
                    console.print(lang_text["adv_code_all_rejected"], style="bold yellow")
                             
            save_ai_commands(user_home_dir, ai_commands)

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
                        conversation_history.append({"role": "system", "content": f"[STORM_WARNING] {_storm_warn}"})
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
                conversation_history.append({"role": "system", "content": f"[CMD_RESULT]\n{_cmd_feedback}"})
            
            if not ai_ask.strip():
                final_ai_result = ai_result.copy()
                if user_refuse_reasons:
                    refuse_summary = lang_text["user_refused_cmds"] + "\n".join(user_refuse_reasons)
                    if "txt" in final_ai_result:
                        final_ai_result["txt"] = (final_ai_result.get("txt") or "") + refuse_summary
                    else:
                        final_ai_result["txt"] = refuse_summary
                
                if interaction_count == 1:
                    record_ai_session(user_home_dir, current_session_id, initial_question, final_ai_result, "", cmd_results, referenced_memory_uuid or "", native_results="")
                else:
                    existing_content, record_path = get_latest_ai_session(user_home_dir, current_session_id)
                    if existing_content and record_path:
                        _ts = time.strftime('%Y-%m-%d %H:%M:%S')
                        _md = current_lang == "english"
                        new_content = f"\n\n### {'Interaction' if _md else '交互'} #{interaction_count} ({_ts})\n\n"
                        # 记录本轮的用户提问（对话历史中最后一个 user 消息）
                        _last_user_q = ""
                        for _m in reversed(conversation_history):
                            if _m.get("role") == "user":
                                _last_user_q = _m.get("content", "")
                                break
                        if _last_user_q:
                            new_content += f"- **{'User' if _md else '用户'}**: {_last_user_q[:200]}{'...' if len(_last_user_q) > 200 else ''}\n"
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
                    record_ai_session(user_home_dir, current_session_id, initial_question, final_ai_result, "", {}, referenced_memory_uuid or "", native_results="")
                else:
                    existing_content, record_path = get_latest_ai_session(user_home_dir, current_session_id)
                    if existing_content and record_path:
                        _ts = time.strftime('%Y-%m-%d %H:%M:%S')
                        _md = current_lang == "english"
                        new_content = f"\n\n### {'Interaction' if _md else '交互'} #{interaction_count} ({_ts})\n\n"
                        # 记录本轮的用户提问（对话历史中最后一个 user 消息）
                        _last_user_q = ""
                        for _m in reversed(conversation_history):
                            if _m.get("role") == "user":
                                _last_user_q = _m.get("content", "")
                                break
                        if _last_user_q:
                            new_content += f"- **{'User' if _md else '用户'}**: {_last_user_q[:200]}{'...' if len(_last_user_q) > 200 else ''}\n"
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
                update_message_tag(user_home_dir, current_chat_name, current_session_id, tag, class_level)
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
        
        # ── 自动判断是否继续循环（不再依赖 AI 的 [ANSWER] 标记）──
        # 规则：仅当响应中只有 txt/analysis 纯文本字段时才停止循环；
        #       但凡存在其他字段（memory/plan/ask/commands/本轮新工具调用），
        #       都需要回问 AI 以传递上下文反馈。
        has_pending = bool(
            memory_uuid or
            _commands_processed_this_round or
            _tool_calls_processed_this_round or
            ai_ask.strip() or
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
            # AI 可以主动用 [ANSWER]yes 表示完成。但如果 AI 写了 [ANSWER]no
            # 却没给任何命令/工具，说明它只是习惯性写 no，实际已无事可做。
            # 此时忽略 answer，直接停止循环 + 显示 ESC 门控让用户决定后续。
            # ── 显示 token 量 ──
            _pt = getattr(_thread_locals, "last_prompt_tokens", 0)
            if _pt:
                console.print(f"  [dim]📊 上下文 ~{_pt} tokens（API 精确值）[/]")
            elif conversation_history:
                _total_chars = sum(len(m.get("content", "") or "") for m in conversation_history)
                _est_tokens = _total_chars // 3 + 1500
                console.print(f"  [dim]📊 上下文 ~{_est_tokens} tokens（估算）[/]")
            continue_asking = False
            esc_pressed = [False]
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
                    conversation_history.append({"role": "user", "content": follow_up})
                    continue_asking = True

    # 恢复原始 SIGINT 处理器
    import signal as _signal
    _signal.signal(_signal.SIGINT, _original_sigint)
    cleanup_output_cache()
