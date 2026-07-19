# -*- coding: utf-8 -*-
"""
MCP 客户端 — MCP 协议实现 + 服务器生命周期管理

从 bin/ai_cmd.py 提取，结构化到独立模块。
所有共享可变状态引用 bin/ai_lib/mcp_state。
"""

import os
import sys
import json
import time
import select
import threading
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple, Any, Callable

# ── 共享状态（从 mcp_state 导入，避免重复定义）──
from .mcp_state import (
    _AI_INTERRUPTED, _MCP_DEBUG, _MCP_DEBUG_START,
    MCP_SERVER_PROCESSES, MCP_TOOLS_CACHE, MCP_TRANSPORTS,
    MCP_CONFIG_PATH, MCP_PRELOADED, MCP_PRELOAD_LOCK, MCP_INSTALL_LOCK,
    MCP_HEALTH_CHECK_INTERVAL, _MCP_LAST_HEALTH_CHECK,
    _MCP_STDERR_BUFFERS, _MCP_STDERR_LOCKS,
    _thread_locals,
)

# ── MCP 工具过滤（这些内置工具不走 MCP）──
MCP_TOOL_FILTER = {
    "read_file", "write_file", "edit_file", "create_directory",
    "list_directory", "directory_tree", "move_file", "copy_file",
    "delete_file", "delete_directory", "get_file_info",
    "search_files", "search_content", "glob", "find_on_path",
    "get_workspace_folders",
}


# ────────────────── Debug 日志 ──────────────────

def _mcp_debug(msg: str) -> None:
    if _MCP_DEBUG:
        elapsed = (time.time() - _MCP_DEBUG_START) if _MCP_DEBUG_START else 0
        sys.stderr.write(f"[{elapsed:06.2f}s] MCP {msg}\n")
        sys.stderr.flush()


def _mcp_debug_enter(func_name: str) -> None:
    if _MCP_DEBUG:
        elapsed = (time.time() - _MCP_DEBUG_START) if _MCP_DEBUG_START else 0
        sys.stderr.write(f"[{elapsed:06.2f}s] → {func_name}\n")
        sys.stderr.flush()


def _mcp_debug_exit(func_name: str, ok: bool = True, detail: str = "") -> None:
    if _MCP_DEBUG:
        elapsed = (time.time() - _MCP_DEBUG_START) if _MCP_DEBUG_START else 0
        status = "OK" if ok else "FAIL"
        extra = f" ({detail})" if detail else ""
        sys.stderr.write(f"[{elapsed:06.2f}s] ← {func_name} {status}{extra}\n")
        sys.stderr.flush()


# ────────────────── 核心 MCP 协议 ──────────────────

def _mcp_send(proc: subprocess.Popen, msg: Dict) -> None:
    """通过 stdin 发送 JSON-RPC 消息（换行分隔 JSON）"""
    body = json.dumps(msg, ensure_ascii=False) + "\n"
    method = msg.get("method", "?")
    _mcp_debug_enter(f"_mcp_send({method})")
    try:
        proc.stdin.write(body)
        proc.stdin.flush()
        _mcp_debug_exit("_mcp_send", ok=True)
    except (BrokenPipeError, OSError) as e:
        _mcp_debug_exit("_mcp_send", ok=False, detail=str(e))
        raise ConnectionError(f"MCP server disconnected: {e}")


def _mcp_recv(proc: subprocess.Popen, timeout: float = 30.0) -> Optional[Dict]:
    """通过 stdout 接收 JSON-RPC 消息（换行分隔 JSON）
    
    用 os.read 直接读原始 fd，避免 TextIOWrapper 缓冲导致的 select 超时问题。
    """
    deadline = time.time() + timeout
    fd = proc.stdout.fileno() if hasattr(proc.stdout, 'fileno') else proc.stdout
    line_bytes = b""
    while True:
        if _AI_INTERRUPTED:
            return None
        remaining = deadline - time.time()
        if remaining <= 0:
            _mcp_debug(f"RECV TIMEOUT after {len(line_bytes)} bytes")
            return None
        if select.select([fd], [], [], min(remaining, 1.0))[0]:
            try:
                ch = os.read(fd, 1)
            except (OSError, BlockingIOError):
                return None
            if not ch:
                return None
            if ch == b'\n':
                break
            line_bytes += ch
    line = line_bytes.decode('utf-8').strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _mcp_request(proc: subprocess.Popen, method: str, params: Dict = None,
                 msg_id: int = None) -> Optional[Dict]:
    """发送 JSON-RPC 请求并等待响应"""
    if msg_id is None:
        msg_id = int(time.time() * 1000) % 1000000
    _mcp_send(proc, {
        "jsonrpc": "2.0", "id": msg_id,
        "method": method, "params": params or {},
    })
    return _mcp_recv(proc)


def _mcp_notification(proc: subprocess.Popen, method: str, params: Dict = None) -> None:
    """发送 JSON-RPC 通知（无响应期待）"""
    _mcp_send(proc, {
        "jsonrpc": "2.0", "method": method, "params": params or {},
    })


# ────────────────── 服务器生命周期 ──────────────────

def is_mcp_server_running(name: str) -> bool:
    return name in MCP_SERVER_PROCESSES and MCP_SERVER_PROCESSES[name].poll() is None


def _ensure_npx_available() -> Tuple[bool, str]:
    """确保 npx 可用（MCP 服务器依赖 Node.js）"""
    npx_path = shutil.which("npx") or shutil.which("npx.cmd")
    if npx_path:
        return True, npx_path
    node_path = shutil.which("node") or shutil.which("node.exe")
    if node_path:
        return True, os.path.join(os.path.dirname(node_path), "npx")
    return False, "npx not found — install Node.js to use MCP servers"


def connect_mcp_server(name: str = "filesystem",
                       user_home_dir: str = None) -> Optional[subprocess.Popen]:
    """启动并初始化 MCP 服务器（同步阻塞直到 initialize 完成）"""
    if is_mcp_server_running(name):
        return MCP_SERVER_PROCESSES[name]

    _mcp_debug_enter(f"connect_mcp_server({name})")

    if name == "filesystem":
        # 内置 filesystem server
        ok, npx = _ensure_npx_available()
        if not ok:
            _mcp_debug_exit("connect_mcp_server", ok=False, detail="npx missing")
            return None
        home = user_home_dir or os.path.expanduser("~")
        config_path = _get_mcp_config_path(user_home_dir)
        try:
            proc = subprocess.Popen(
                [npx, "-y", "@modelcontextprotocol/server-filesystem", home],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "NODE_OPTIONS": "--max-old-space-size=256"},
            )
        except FileNotFoundError:
            _mcp_debug_exit("connect_mcp_server", ok=False, detail="npx not found")
            return None
    else:
        # 外部 MCP server（从配置读取）
        config = _load_mcp_config(user_home_dir)
        srv_config = config.get(name, {})
        srv_cmd = srv_config.get("command", "")
        srv_args = srv_config.get("args", [])
        if not srv_cmd:
            _mcp_debug_exit("connect_mcp_server", ok=False, detail=f"no config for '{name}'")
            return None
        try:
            proc = subprocess.Popen(
                [srv_cmd] + srv_args,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            _mcp_debug_exit("connect_mcp_server", ok=False, detail=f"command not found: {srv_cmd}")
            return None

    # 等待 initialize 响应（MCP 握手）
    init_result = _mcp_request(proc, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "onyx", "version": "1.0"},
    })
    if not init_result:
        _mcp_debug_exit("connect_mcp_server", ok=False, detail="initialize failed")
        try:
            stderr_out = proc.stderr.read(500).decode('utf-8', errors='replace')
            _mcp_debug(f"STDERR: {stderr_out}")
        except Exception:
            pass
        proc.terminate()
        return None

    _mcp_notification(proc, "initialized")
    MCP_SERVER_PROCESSES[name] = proc
    MCP_TOOLS_CACHE.pop(name, None)
    _mcp_debug_exit("connect_mcp_server", ok=True, detail=f"pid={proc.pid}")
    return proc


def disconnect_mcp_server(name: str) -> bool:
    """断开 MCP 服务器连接"""
    if name in MCP_SERVER_PROCESSES:
        proc = MCP_SERVER_PROCESSES.pop(name)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        MCP_TOOLS_CACHE.pop(name, None)
        return True
    return False


def preload_mcp_servers(user_home_dir: str = None) -> None:
    """预加载所有已配置的 MCP 服务器"""
    global MCP_PRELOADED
    if MCP_PRELOADED:
        return
    if not MCP_PRELOAD_LOCK.acquire(blocking=False):
        return
    try:
        config = _load_mcp_config(user_home_dir)
        for name in config:
            if name == "filesystem":
                continue
            threading.Thread(target=connect_mcp_server,
                           args=(name, user_home_dir), daemon=True).start()
        MCP_PRELOADED = True
    finally:
        MCP_PRELOAD_LOCK.release()


def health_check_mcp(user_home_dir: str = None) -> None:
    """定期健康检查：重连已断开服务器 + 增量更新工具缓存"""
    global _MCP_LAST_HEALTH_CHECK
    now = time.time()
    if now - _MCP_LAST_HEALTH_CHECK < MCP_HEALTH_CHECK_INTERVAL:
        return
    _MCP_LAST_HEALTH_CHECK = now

    for name in list(MCP_SERVER_PROCESSES.keys()):
        proc = MCP_SERVER_PROCESSES[name]
        if proc.poll() is not None:
            MCP_SERVER_PROCESSES.pop(name, None)
            connect_mcp_server(name, user_home_dir)

    for name in list(MCP_SERVER_PROCESSES.keys()):
        old_tools = MCP_TOOLS_CACHE.get(name, [])
        new_tools = _discover_mcp_tools(name, user_home_dir)
        if new_tools and new_tools != old_tools:
            MCP_TOOLS_CACHE[name] = new_tools


def _schedule_mcp_health_check(user_home_dir: str = None) -> None:
    """调度一次延迟健康检查（后台线程）"""
    def _do_check():
        time.sleep(30)
        health_check_mcp(user_home_dir)
    threading.Thread(target=_do_check, daemon=True).start()


# ────────────────── 工具发现 ──────────────────

def _discover_mcp_tools(name: str, user_home_dir: str = None) -> List[Dict]:
    """从 MCP 服务器发现可用工具列表"""
    proc = connect_mcp_server(name, user_home_dir)
    if proc is None:
        return []
    result = _mcp_request(proc, "tools/list")
    if not result or "error" in result:
        return []
    tools = result.get("result", {}).get("tools", [])
    return tools


def get_mcp_tools(user_home_dir: str = None) -> List[Dict]:
    """获取 MCP 工具列表（优先从 Registry，回退缓存）"""
    try:
        from .mcp_registry import get_registry
        registry = get_registry()
        all_schemas = registry.schemas()
        if all_schemas:
            return all_schemas
    except Exception:
        pass

    if "filesystem" in MCP_TOOLS_CACHE:
        return MCP_TOOLS_CACHE["filesystem"]

    proc = connect_mcp_server("filesystem", user_home_dir)
    if proc is None:
        return []
    tools = _discover_mcp_tools("filesystem", user_home_dir)
    if tools:
        MCP_TOOLS_CACHE["filesystem"] = tools
    return tools


def build_mcp_tools_prompt(lang: str = "chinese") -> str:
    """构造 MCP 工具说明文本（注入给 AI 的提示词）"""
    tools = get_mcp_tools()
    if not tools:
        return ""
    lines = []
    lines.append("# MCP 扩展工具" if lang == "chinese" else "# MCP Extension Tools")
    for t in tools:
        name = t.get("name", "?")
        desc = t.get("description", "")
        lines.append(f"- `{name}`: {desc[:100]}")
    return "\n".join(lines)


# ────────────────── 工具调用（外部调用入口）──

def call_mcp_tool(name: str, tool_name: str, arguments: Dict,
                  user_home_dir: str = None) -> Tuple[bool, str]:
    """调用 MCP 服务器上的工具"""
    mcp_name = "filesystem" if name == "filesystem" else name
    proc = connect_mcp_server(mcp_name, user_home_dir)
    if proc is None:
        return False, f"MCP server '{mcp_name}' not connected"

    mcp_tool = tool_name
    if mcp_name != "filesystem":
        mcp_tool = tool_name

    result = _mcp_request(proc, "tools/call", {
        "name": mcp_tool,
        "arguments": arguments,
    })
    if result is None:
        return False, f"MCP call '{mcp_tool}' failed (no response)"
    if "error" in result:
        err = result["error"]
        return False, f"MCP error: {err.get('message', str(err))}"

    content = result.get("result", {}).get("content", [])
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    output = "\n".join(texts) if texts else str(result.get("result", {}))
    return True, output


# ────────────────── 配置管理 ──────────────────

def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _get_mcp_config_dir(user_home_dir: str = None) -> str:
    home = user_home_dir or os.path.expanduser("~")
    return os.path.join(home, ".config", "onyx", "mcp")


def _get_mcp_config_path(user_home_dir: str = None) -> str:
    return os.path.join(_get_mcp_config_dir(user_home_dir), "config.json")


def _load_mcp_config(user_home_dir: str = None) -> Dict:
    path = _get_mcp_config_path(user_home_dir)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_mcp_config(config: Dict, user_home_dir: str = None) -> bool:
    try:
        path = _get_mcp_config_path(user_home_dir)
        _ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


# ────────────────── 辅助 ──────────────────

def _start_stderr_reader(proc: subprocess.Popen) -> None:
    """启动 stderr 读取线程（防止 stderr 缓冲区满导致进程卡死）"""
    pid = proc.pid

    def _reader():
        buf = []
        try:
            for line in proc.stderr:
                decoded = line.decode('utf-8', errors='replace').rstrip()
                buf.append(decoded)
        except (OSError, ValueError):
            pass
        finally:
            _MCP_STDERR_BUFFERS[pid] = buf

    t = threading.Thread(target=_reader, daemon=True)
    t.start()


def _get_stderr_lines(pid: int, max_lines: int = 20) -> List[str]:
    return _MCP_STDERR_BUFFERS.get(pid, [])[:max_lines]


# ────────────────── 路径安全校验 ──────────────────

def _validate_mcp_mount_path(path: str, user_home_dir: str = None) -> Tuple[bool, str]:
    """校验 MCP 挂载路径是否在允许范围内"""
    home = user_home_dir or os.path.expanduser("~")
    try:
        real_path = os.path.realpath(path) if os.path.exists(path) else os.path.abspath(path)
        real_home = os.path.realpath(home)
        if real_path == real_home or real_path.startswith(real_home + os.sep):
            return True, ""
    except Exception:
        pass
    return False, f"Path '{path}' is outside user home directory"


# ────────────────── MCP 管理命令 ──────────────────

def list_mcp_servers(user_home_dir: str = None) -> List[Dict]:
    """列出所有 MCP 服务器及其状态"""
    config = _load_mcp_config(user_home_dir)
    result = []
    for name in config:
        running = is_mcp_server_running(name)
        tools = MCP_TOOLS_CACHE.get(name, [])
        result.append({"name": name, "running": running, "tools": len(tools)})
    # 始终显示 filesystem
    result.insert(0, {
        "name": "filesystem",
        "running": is_mcp_server_running("filesystem"),
        "tools": len(MCP_TOOLS_CACHE.get("filesystem", [])),
    })
    return result


def install_mcp_server_cmd(mcp_name: str, pkg: str,
                          user_home_dir: str = None) -> bool:
    """安装 MCP 服务器（记录到配置）"""
    config = _load_mcp_config(user_home_dir)
    config[mcp_name] = {"command": "npx", "args": ["-y", pkg]}
    return _save_mcp_config(config, user_home_dir)


def remove_mcp_server_cmd(mcp_name: str, user_home_dir: str = None) -> bool:
    """移除 MCP 服务器配置"""
    disconnect_mcp_server(mcp_name)
    config = _load_mcp_config(user_home_dir)
    if mcp_name in config:
        del config[mcp_name]
        return _save_mcp_config(config, user_home_dir)
    return False
