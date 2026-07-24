#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP 传输层抽象

定义 Transport 抽象基类，解耦 JSON-RPC 通信与业务逻辑。
支持两种传输：
  - StdioTransport:  通过子进程 stdin/stdout 发送换行分隔的 JSON-RPC 2.0
  - HttpTransport:   通过 HTTP POST 发送 JSON-RPC（预留）
"""

from __future__ import annotations

import os
import sys
import json
import time
import subprocess
import threading
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Tuple


# ─────────────────────────── 抽象基类 ───────────────────────────

class Transport(ABC):
    """MCP 传输层抽象接口"""

    @abstractmethod
    def call(self, method: str, params: dict, msg_id: int = None,
             timeout: float = 30.0) -> Optional[dict]:
        """
        发送 JSON-RPC 请求并等待响应。
        返回完整响应 dict，超时返回 None。
        """
        ...

    @abstractmethod
    def notify(self, method: str, params: dict) -> None:
        """发送 JSON-RPC 通知（无响应）"""
        ...

    @abstractmethod
    def close(self) -> None:
        """关闭传输，回收资源"""
        ...

    @abstractmethod
    def is_alive(self) -> bool:
        """检查传输是否仍然活跃"""
        ...


# ─────────────────────────── Stdio 实现 ───────────────────────────

class StdioTransport(Transport):
    """
    通过子进程 stdin/stdout 的 JSON-RPC 2.0 传输。

    设计要点：
      - callMu 序列化请求，因为共享管道一次只能一个请求
      - 专属 read_loop goroutine（Python 用线程）拥有 stdout，按 id 派发响应
      - 双 context 分离：lifeCtx（会话级）vs callCtx（每次调用带 timeout）
      - close() 优雅关闭 stdin → kill 进程树 → wait 回收
    """

    # 协议常量
    JSONRPC_VERSION = "2.0"
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, command: str, args: list = None, env: dict = None,
                 cwd: str = None, startup_timeout: float = 10.0):
        """
        初始化并启动子进程。

        Args:
            command:  可执行文件路径或命令名
            args:     命令行参数列表
            env:      环境变量（默认继承当前进程）
            cwd:      工作目录
            startup_timeout: 启动超时（秒）
        """
        self.command = command
        self.args = args or []
        self.env = env
        self.cwd = cwd
        self.startup_timeout = startup_timeout

        # 内部状态
        self._proc: Optional[subprocess.Popen] = None
        self._call_lock = threading.Lock()
        self._pending: Dict[int, threading.Event] = {}      # msg_id → Event
        self._responses: Dict[int, dict] = {}               # msg_id → response
        self._msg_counter = 0
        self._reader_thread: Optional[threading.Thread] = None
        self._closed = False
        self._startup_error: Optional[str] = None

        self._start()

    # ── 生命周期 ──

    def _start(self) -> None:
        """启动子进程并完成 MCP 握手"""
        try:
            # 构建完整命令行
            full_cmd = [self.command] + self.args

            # Termux 环境特殊处理：使用 PTY 避免管道全缓冲
            is_termux = self._is_termux()
            if is_termux:
                self._proc = self._spawn_pty(full_cmd)
            else:
                self._proc = subprocess.Popen(
                    full_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self.env,
                    cwd=self.cwd,
                    text=False,  # bytes 模式，手动编解码
                )

            # 启动 stdout 读取线程
            self._reader_thread = threading.Thread(
                target=self._read_loop,
                daemon=True,
                name=f"mcp-reader-{os.path.basename(self.command)}"
            )
            self._reader_thread.start()

            # MCP 握手：initialize
            init_resp = self.call(
                "initialize",
                {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "onyx", "version": "2.7.0"}
                },
                timeout=self.startup_timeout
            )

            if init_resp is None:
                self._startup_error = "initialize timeout"
                self.close()
                return

            if "error" in init_resp:
                self._startup_error = f"initialize error: {init_resp['error']}"
                self.close()
                return

            # 发送 initialized 通知
            self.notify("notifications/initialized", {})

        except FileNotFoundError:
            self._startup_error = f"Command not found: {self.command}"
        except PermissionError:
            self._startup_error = f"Permission denied: {self.command}"
        except Exception as e:
            self._startup_error = f"Startup failed: {e}"

    def _spawn_pty(self, full_cmd: list) -> subprocess.Popen:
        """Termux 环境使用 PTY 避免管道全缓冲"""
        import pty
        import tty
        master_fd, slave_fd = pty.openpty()
        try:
            tty.setraw(master_fd)
        except Exception:
            pass

        proc = subprocess.Popen(
            full_cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=self.env,
            cwd=self.cwd,
            close_fds=True,
        )
        # 保存 master_fd 用于读写
        self._pty_fd = master_fd
        return proc

    def close(self) -> None:
        """优雅关闭：stdin → kill → wait"""
        if self._closed:
            return
        self._closed = True

        proc = self._proc
        if proc is None:
            return

        try:
            # 1. 关闭 stdin
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

            # 2. 终止进程
            try:
                proc.terminate()
            except Exception:
                pass

            # 3. 等待 5 秒，超时则 kill
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._proc = None

        # 唤醒所有等待者
        for evt in self._pending.values():
            evt.set()

    def is_alive(self) -> bool:
        """检查子进程是否存活"""
        if self._closed:
            return False
        proc = self._proc
        if proc is None:
            return False
        return proc.poll() is None

    @property
    def startup_error(self) -> Optional[str]:
        """启动失败的错误信息"""
        return self._startup_error

    # ── Transport 接口实现 ──

    def call(self, method: str, params: dict, msg_id: int = None,
             timeout: float = 30.0) -> Optional[dict]:
        """
        发送 JSON-RPC 请求并等待响应。

        线程安全：_call_lock 序列化请求，保证同一时刻只有一个请求在管道上。
        """
        if self._closed or not self._proc:
            return None

        with self._call_lock:
            if msg_id is None:
                self._msg_counter += 1
                msg_id = self._msg_counter

            # 注册等待
            evt = threading.Event()
            self._pending[msg_id] = evt

            try:
                # 发送请求
                msg = {
                    "jsonrpc": self.JSONRPC_VERSION,
                    "id": msg_id,
                    "method": method,
                    "params": params,
                }
                self._send(msg)

                # 等待响应
                if not evt.wait(timeout=timeout):
                    return None  # 超时

                return self._responses.pop(msg_id, None)
            finally:
                self._pending.pop(msg_id, None)

    def notify(self, method: str, params: dict) -> None:
        """发送 JSON-RPC 通知（fire-and-forget）"""
        if self._closed or not self._proc:
            return

        msg = {
            "jsonrpc": self.JSONRPC_VERSION,
            "method": method,
            "params": params,
        }
        self._send(msg)

    # ── 内部方法 ──

    def _send(self, msg: dict) -> None:
        """通过 stdin 发送 JSON-RPC 消息（换行分隔）"""
        proc = self._proc
        if proc is None or self._closed:
            return

        try:
            line = json.dumps(msg, ensure_ascii=False) + "\n"

            if hasattr(self, '_pty_fd'):
                # PTY 模式
                os.write(self._pty_fd, line.encode("utf-8"))
            elif proc.stdin:
                proc.stdin.write(line.encode("utf-8"))
                proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._closed = True

    def _read_loop(self) -> None:
        """
        专属读取线程：拥有 stdout，按 id 派发响应到等待 channel。
        读完或出错后关闭。
        """
        proc = self._proc
        if proc is None:
            return

        try:
            if hasattr(self, '_pty_fd'):
                # PTY 模式：逐行读取
                stream = os.fdopen(self._pty_fd, 'rb', buffering=0)
            elif proc.stdout:
                stream = proc.stdout
            else:
                return

            buffer = b""
            while not self._closed:
                try:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    buffer += chunk

                    # 按换行符拆分
                    while b"\n" in buffer:
                        line_bytes, buffer = buffer.split(b"\n", 1)
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        if line:
                            self._dispatch_line(line)
                except Exception:
                    break
        except Exception:
            pass
        finally:
            self._closed = True
            # 唤醒所有等待者
            for evt in self._pending.values():
                evt.set()

    def _dispatch_line(self, line: str) -> None:
        """解析一行 JSON-RPC 响应并派发到对应的等待者"""
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return

        msg_id = msg.get("id")
        if msg_id is not None and msg_id in self._pending:
            self._responses[msg_id] = msg
            self._pending[msg_id].set()

    @staticmethod
    def _is_termux() -> bool:
        """判断是否为 Termux 环境"""
        return (
            "termux" in sys.prefix.lower() or
            os.path.exists("/data/data/com.termux")
        )


# ─────────────────────────── HTTP 实现 ───────────────────────────

class HttpTransport(Transport):
    """
    通过 HTTP POST 的 JSON-RPC 2.0 传输。

    用于连接远程 MCP 服务器（如 SSE / Streamable HTTP 端点）。
    每次 call() 发起一个独立的 HTTP POST 请求，无状态。
    """

    def __init__(self, url: str, timeout: float = 30.0, headers: dict = None):
        """
        Args:
            url:      MCP 服务器 HTTP 端点
            timeout:  请求超时秒数（默认 30s）
            headers:  附加 HTTP 头（如 Authorization）
        """
        self._url = url.rstrip("/")
        self._timeout = timeout
        self._headers = headers or {}
        self._closed = False
        self._msg_id = 0
        self._lock = threading.Lock()

    def call(self, method: str, params: dict, msg_id: int = None, timeout: float = None) -> Optional[dict]:
        """发送 JSON-RPC 请求，返回响应结果部分。"""
        import requests as _requests
        if self._closed:
            return None
        with self._lock:
            self._msg_id += 1
            rid = msg_id if msg_id is not None else self._msg_id
        payload = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params,
        }
        try:
            resp = _requests.post(
                self._url,
                json=payload,
                headers={**self._headers, "Content-Type": "application/json"},
                timeout=timeout or self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"JSON-RPC error: {data['error']}")
            return data.get("result")
        except Exception:
            return None

    def notify(self, method: str, params: dict) -> None:
        """发送 JSON-RPC 通知（无响应）。"""
        import requests as _requests
        if self._closed:
            return
        with self._lock:
            self._msg_id += 1
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        try:
            _requests.post(
                self._url,
                json=payload,
                headers={**self._headers, "Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except Exception:
            pass

    def close(self) -> None:
        """关闭传输。"""
        self._closed = True

    def is_alive(self) -> bool:
        """检查传输是否活跃（简单检查未关闭）。"""
        return not self._closed


# ─────────────────────────── 工厂函数 ───────────────────────────

def create_transport(server_config: dict, startup_timeout: float = 10.0) -> Transport:
    """
    根据服务器配置创建对应的 Transport 实例。

    server_config 格式：
    {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"],
        "env": {"NODE_ENV": "production"},    // 可选
        "cwd": "/working/dir",                // 可选
        "url": "http://localhost:8080/mcp"    // HTTP 模式（预留）
    }
    """
    transport_type = server_config.get("type", "stdio")

    if transport_type == "http" or "url" in server_config:
        # HTTP 模式
        url = server_config.get("url", "")
        if not url:
            raise ValueError("HTTP transport requires a 'url' in server_config")
        timeout = server_config.get("timeout", 30.0)
        headers = {}
        # 从 server_config 提取 Authorization 等头
        for h in ("Authorization", "X-API-Key", "User-Agent"):
            if h.lower() in {k.lower() for k in server_config}:
                val = next(server_config[k] for k in server_config if k.lower() == h.lower())
                headers[h] = str(val)
        return HttpTransport(url=url, timeout=timeout, headers=headers)

    # stdio 模式
    command = server_config.get("command", "npx")
    args = server_config.get("args", [])
    env = server_config.get("env")
    cwd = server_config.get("cwd")

    return StdioTransport(
        command=command,
        args=args,
        env=env,
        cwd=cwd,
        startup_timeout=startup_timeout,
    )
