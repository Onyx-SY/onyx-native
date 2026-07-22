"""LSP 客户端 — 语言服务器协议支持

支持通过 stdio JSON-RPC 与语言服务器通信，提供以下能力：
- 诊断 (diagnostics)
- 悬停提示 (hover)
- 跳转到定义 (definition)
- 查找引用 (references)
- 代码补全 (completion)
- 文档符号 (symbols)
- 格式化 (format)

自动根据文件扩展名选择对应的语言服务器。
"""

from __future__ import annotations
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class LspAction(str, Enum):
    DIAGNOSTICS = "diagnostics"
    HOVER = "hover"
    DEFINITION = "definition"
    REFERENCES = "references"
    COMPLETION = "completion"
    SYMBOLS = "symbols"
    FORMAT = "format"


class LspServerStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    STARTING = "starting"
    ERROR = "error"


@dataclass
class LspPosition:
    line: int
    character: int


@dataclass
class LspRange:
    start: LspPosition
    end: LspPosition


@dataclass
class LspDiagnostic:
    path: str
    line: int
    character: int
    severity: str
    message: str
    source: Optional[str] = None


@dataclass
class LspLocation:
    path: str
    line: int
    character: int
    end_line: Optional[int] = None
    end_character: Optional[int] = None
    preview: Optional[str] = None


@dataclass
class LspHoverResult:
    content: str
    language: Optional[str] = None


@dataclass
class LspCompletionItem:
    label: str
    kind: Optional[str] = None
    detail: Optional[str] = None
    insert_text: Optional[str] = None


@dataclass
class LspSymbol:
    name: str
    kind: str
    path: str
    line: int
    character: int


@dataclass
class LspServerState:
    language: str
    status: LspServerStatus
    root_path: Optional[str] = None
    capabilities: list[str] = field(default_factory=list)
    diagnostics: list[LspDiagnostic] = field(default_factory=list)


# ── 语言服务器映射：扩展名 → (语言, 命令) ──
LSP_SERVER_MAP: dict[str, tuple[str, list[str]]] = {
    # Python
    ".py":   ("python", ["pyright-langserver", "--stdio"]),
    ".pyi":  ("python", ["pyright-langserver", "--stdio"]),
    # JavaScript / TypeScript
    ".js":   ("javascript", ["typescript-language-server", "--stdio"]),
    ".jsx":  ("javascript", ["typescript-language-server", "--stdio"]),
    ".ts":   ("typescript", ["typescript-language-server", "--stdio"]),
    ".tsx":  ("typescript", ["typescript-language-server", "--stdio"]),
    # Rust
    ".rs":   ("rust", ["rust-analyzer"]),
    # Go
    ".go":   ("go", ["gopls"]),
    # C / C++
    ".c":    ("c", ["clangd"]),
    ".cpp":  ("cpp", ["clangd"]),
    ".h":    ("c", ["clangd"]),
    ".hpp":  ("cpp", ["clangd"]),
    # Java
    ".java": ("java", ["jdtls"]),
    # Ruby
    ".rb":   ("ruby", ["solargraph", "stdio"]),
    # PHP
    ".php":  ("php", ["phpactor", "language-server"]),
}


def _detect_language(file_path: str) -> Optional[tuple[str, list[str]]]:
    """根据文件扩展名检测语言和对应的语言服务器命令。"""
    _, ext = os.path.splitext(file_path)
    return LSP_SERVER_MAP.get(ext.lower())


class LspClient:
    """单个语言服务器客户端的 JSON-RPC 通信封装。"""

    def __init__(self, language: str, cmd: list[str], root_path: str):
        self.language = language
        self.cmd = cmd
        self.root_path = root_path
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._req_id = 0
        self._pending: dict[int, threading.Event] = {}
        self._responses: dict[int, dict] = {}
        self._buf = ""
        self._reader_thread: Optional[threading.Thread] = None
        self._capabilities: dict = {}
        self._status = LspServerStatus.DISCONNECTED
        self._shutdown = False

    def start(self) -> bool:
        """启动语言服务器进程并完成 initialize 握手。"""
        if self._proc:
            return True
        try:
            # Windows 隐藏子进程控制台窗口
            _startupinfo = None
            _creationflags = 0
            if sys.platform == "win32":
                _startupinfo = subprocess.STARTUPINFO()
                _startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                _creationflags = subprocess.CREATE_NO_WINDOW
            self._proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.root_path,
                startupinfo=_startupinfo,
                creationflags=_creationflags,
            )
            self._status = LspServerStatus.STARTING
            self._reader_thread = threading.Thread(
                target=self._reader_loop, daemon=True, name=f"lsp-{self.language}"
            )
            self._reader_thread.start()
            # 发送 initialize 请求
            result = self._request("initialize", {
                "processId": os.getpid(),
                "rootUri": f"file://{self.root_path}",
                "capabilities": {
                    "textDocument": {
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                        "completion": {"completionItem": {"snippetSupport": True}},
                        "definition": {},
                        "references": {},
                        "documentSymbol": {},
                        "formatting": {},
                    },
                },
            })
            if result is None:
                self._status = LspServerStatus.ERROR
                return False
            self._capabilities = result.get("capabilities", {})
            # initialized 通知
            self._notify("initialized", {})
            self._status = LspServerStatus.CONNECTED
            return True
        except Exception as e:
            self._status = LspServerStatus.ERROR
            return False

    def _send(self, message: dict):
        """发送 JSON-RPC 消息（HTTP 头 + JSON 体）。"""
        if not self._proc or not self._proc.stdin:
            return
        body = json.dumps(message)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        with self._lock:
            self._proc.stdin.write(header.encode())
            self._proc.stdin.write(body.encode())
            self._proc.stdin.flush()

    def _request(self, method: str, params: dict, retries: int = 3) -> Optional[dict]:
        """发送请求，等待响应。支持 ContentModified (-32801) 自动重试。"""
        for attempt in range(retries):
            with self._lock:
                self._req_id += 1
                req_id = self._req_id
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }
            event = threading.Event()
            with self._lock:
                self._pending[req_id] = event
            self._send(msg)
            event.wait(timeout=10)
            with self._lock:
                self._pending.pop(req_id, None)
                result = self._responses.pop(req_id, None)
            # 检查是否 ContentModified（服务器索引未就绪）
            if result is None and attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            return result

    def _notify(self, method: str, params: dict):
        """发送通知（无需响应）。"""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._send(msg)

    def _reader_loop(self):
        """后台线程：持续读取 stdout，解析 JSON-RPC 响应。"""
        while not self._shutdown and self._proc and self._proc.stdout:
            try:
                # 读取 Content-Length 头
                headers = ""
                while True:
                    line = self._proc.stdout.readline()
                    if not line:
                        return
                    line = line.decode("utf-8", errors="replace").strip()
                    if not line:
                        break
                    headers += line + "\n"
                # 解析 Content-Length
                import re
                m = re.search(r"Content-Length:\s*(\d+)", headers)
                if not m:
                    continue
                length = int(m.group(1))
                # 读取 JSON 体
                body = self._proc.stdout.read(length).decode("utf-8", errors="replace")
                if not body:
                    continue
                data = json.loads(body)
                # 处理响应或通知
                if "id" in data:
                    with self._lock:
                        event = self._pending.get(data["id"])
                        if event:
                            self._responses[data["id"]] = data.get("result")
                            event.set()
                        else:
                            self._responses[data["id"]] = data.get("result")
                elif "method" in data:
                    method = data.get("method")
                    params = data.get("params", {})
                    if method == "textDocument/publishDiagnostics":
                        # 监听诊断推送通知
                        self._handle_diagnostics_notification(params)
            except Exception as e:
                import sys as _sys
                _sys.stderr.write(f"[LSP] reader error: {e}\n")

    def _handle_diagnostics_notification(self, params: dict):
        """处理 textDocument/publishDiagnostics 通知。"""
        uri = params.get("uri", "")
        path = uri.replace("file://", "") if uri.startswith("file://") else uri
        # URL 解码路径
        from urllib.parse import unquote
        path = unquote(path)
        diagnostics = params.get("diagnostics", [])
        parsed = []
        for d in diagnostics:
            r = d.get("range", {})
            start = r.get("start", {})
            sev = d.get("severity", 0)
            sev_map = {1: "error", 2: "warning", 3: "info", 4: "hint"}
            parsed.append(LspDiagnostic(
                path=path,
                line=start.get("line", 0) + 1,
                character=start.get("character", 0),
                severity=sev_map.get(sev, "unknown"),
                message=d.get("message", ""),
                source=d.get("source"),
            ))
        with self._lock:
            # 按路径缓存诊断结果
            if not hasattr(self, '_diagnostics_cache'):
                self._diagnostics_cache = {}
            self._diagnostics_cache[path] = parsed

    def did_open(self, file_path: str, text: str = None):
        """textDocument/didOpen 通知。"""
        uri = f"file://{os.path.abspath(file_path)}"
        params = {
            "textDocument": {
                "uri": uri,
                "languageId": self.language,
                "version": 1,
                "text": text or "",
            }
        }
        if text is None:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    params["textDocument"]["text"] = f.read()
            except Exception:
                params["textDocument"]["text"] = ""
        self._notify("textDocument/didOpen", params)
        return uri

    def did_change(self, file_path: str, text: str):
        """textDocument/didChange 通知。"""
        uri = f"file://{os.path.abspath(file_path)}"
        self._notify("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": text}],
        })

    # ── LSP 操作 ──

    def diagnostics(self, file_path: str) -> list[LspDiagnostic]:
        """获取文件诊断（通过 didOpen 触发服务器推送诊断）。"""
        uri = self.did_open(file_path)
        from urllib.parse import unquote
        abs_path = os.path.abspath(file_path)
        # 发送 semanticTokens/full 请求以触发推送（服务器不支持则静默失败）
        self._request("textDocument/semanticTokens/full", {
            "textDocument": {"uri": uri},
        })
        time.sleep(0.5)  # 给服务器一点时间推送诊断
        cache = getattr(self, '_diagnostics_cache', {})
        return cache.get(unquote(abs_path), [])

    def hover(self, file_path: str, line: int, character: int) -> Optional[LspHoverResult]:
        """获取悬停提示。"""
        uri = f"file://{os.path.abspath(file_path)}"
        result = self._request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if not result:
            return None
        contents = result.get("contents", {})
        if isinstance(contents, str):
            return LspHoverResult(content=contents)
        if isinstance(contents, dict):
            lang = contents.get("language")
            value = contents.get("value", "")
            return LspHoverResult(content=value, language=lang)
        if isinstance(contents, list):
            text = "\n".join(
                c.get("value", str(c)) if isinstance(c, dict) else str(c)
                for c in contents
            )
            return LspHoverResult(content=text)
        return None

    def definition(self, file_path: str, line: int, character: int) -> Optional[list[LspLocation]]:
        """跳转到定义。"""
        uri = f"file://{os.path.abspath(file_path)}"
        result = self._request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if not result:
            return None
        locations = result if isinstance(result, list) else [result]
        def _parse_loc(loc: dict) -> LspLocation:
            target_uri = loc.get("uri", "")
            target_path = self._uri_to_path(target_uri)
            r = loc.get("range", {})
            start = r.get("start", {})
            end = r.get("end", {})
            preview = None
            if os.path.exists(target_path):
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    if start.get("line", 0) < len(lines):
                        preview = lines[start["line"]].strip()
                except Exception:
                    pass
            return LspLocation(
                path=target_path,
                line=start.get("line", 0) + 1,
                character=start.get("character", 0),
                end_line=end.get("line", 0) + 1 if end else None,
                end_character=end.get("character", 0) if end else None,
                preview=preview,
            )
        return [_parse_loc(loc) for loc in locations]

    def references(self, file_path: str, line: int, character: int) -> Optional[list[LspLocation]]:
        """查找引用。"""
        uri = f"file://{os.path.abspath(file_path)}"
        result = self._request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": True},
        })
        if not result:
            return None
        def _parse_loc(loc: dict) -> LspLocation:
            target_uri = loc.get("uri", "")
            target_path = self._uri_to_path(target_uri)
            r = loc.get("range", {})
            start = r.get("start", {})
            end = r.get("end", {})
            return LspLocation(
                path=target_path,
                line=start.get("line", 0) + 1,
                character=start.get("character", 0),
                end_line=end.get("line", 0) + 1 if end else None,
                end_character=end.get("character", 0) if end else None,
            )
        return [_parse_loc(loc) for loc in result]

    def completion(self, file_path: str, line: int, character: int) -> Optional[list[LspCompletionItem]]:
        """代码补全。"""
        uri = f"file://{os.path.abspath(file_path)}"
        result = self._request("textDocument/completion", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if not result:
            return None
        items = result if isinstance(result, list) else result.get("items", [])
        return [
            LspCompletionItem(
                label=item.get("label", ""),
                kind=item.get("kind"),
                detail=item.get("detail"),
                insert_text=item.get("insertText") or item.get("textEdit", {}).get("newText"),
            )
            for item in items
        ]

    def symbols(self, file_path: str) -> Optional[list[LspSymbol]]:
        """文档符号。"""
        uri = f"file://{os.path.abspath(file_path)}"
        result = self._request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        })
        if not result:
            return None
        symbols = []
        for item in (result if isinstance(result, list) else []):
            if "children" in item:
                # 嵌套符号（DocumentSymbol 类型）：合并子符号
                symbols.extend(self._flatten_symbol(item, file_path))
            else:
                # 扁平符号（SymbolInformation 类型）
                loc = item.get("location", {})
                r = loc.get("range", {})
                start = r.get("start", {})
                symbols.append(LspSymbol(
                    name=item.get("name", ""),
                    kind=item.get("kind", ""),
                    path=file_path,
                    line=start.get("line", 0) + 1,
                    character=start.get("character", 0),
                ))
        return symbols

    def _flatten_symbol(self, item: dict, file_path: str, depth: int = 0) -> list[LspSymbol]:
        """展平嵌套的 DocumentSymbol。"""
        result = []
        r = item.get("range", {})
        start = r.get("start", {})
        result.append(LspSymbol(
            name=item.get("name", ""),
            kind=item.get("kind", ""),
            path=file_path,
            line=start.get("line", 0) + 1,
            character=start.get("character", 0),
        ))
        for child in item.get("children", []):
            result.extend(self._flatten_symbol(child, file_path, depth + 1))
        return result

    @staticmethod
    def _uri_to_path(uri: str) -> str:
        """将 file:// URI 转换为本地路径，处理 URL 编码。"""
        from urllib.parse import unquote, urlparse
        if uri.startswith("file://"):
            parsed = urlparse(uri)
            path = parsed.path
            if path.startswith("/") and len(path) > 2 and path[2] == ":":
                path = path[1:]
            return unquote(path)
        return unquote(uri)

    def format(self, file_path: str) -> Optional[str]:
        """格式化文档。"""
        uri = f"file://{os.path.abspath(file_path)}"
        result = self._request("textDocument/formatting", {
            "textDocument": {"uri": uri},
            "options": {"tabSize": 4, "insertSpaces": True},
        })
        if not result:
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            lines = text.split("\n")
            edits = sorted(result, key=lambda e: (
                e.get("range", {}).get("start", {}).get("line", 0),
                e.get("range", {}).get("start", {}).get("character", 0),
            ), reverse=True)
            for edit in edits:
                r = edit.get("range", {})
                start = r.get("start", {})
                end = r.get("end", {})
                sl = start.get("line", 0)
                sc = start.get("character", 0)
                el = end.get("line", 0)
                ec = end.get("character", 0)
                new_text = edit.get("newText", "")
                if sl == el:
                    if sl < len(lines):
                        old = lines[sl]
                        lines[sl] = old[:sc] + new_text + old[ec:]
                else:
                    before = lines[:sl]
                    middle = new_text.split("\n")
                    after = lines[el + 1:] if el + 1 < len(lines) else []
                    first_part = lines[sl][:sc] if sl < len(lines) else ""
                    last_part = lines[el][ec:] if el < len(lines) else ""
                    if middle:
                        middle[0] = first_part + middle[0]
                        middle[-1] = middle[-1] + last_part
                    else:
                        middle = [first_part + last_part]
                    lines = before + middle + after
            return "\n".join(lines)
        except Exception as e:
            import sys as _sys
            _sys.stderr.write(f"[LSP] format error: {e}\n")
            return None

    def shutdown(self):
        """关闭语言服务器。"""
        self._shutdown = True
        try:
            self._request("shutdown", {})
            self._notify("exit", {})
        except Exception:
            pass
        if self._proc:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        self._status = LspServerStatus.DISCONNECTED

    @property
    def status(self) -> LspServerStatus:
        return self._status


class LspManager:
    """LSP 客户端管理器 — 按语言缓存客户端实例。"""

    def __init__(self):
        self._clients: dict[str, LspClient] = {}
        self._lock = threading.Lock()

    def _get_server_cmd(self, file_path: str) -> Optional[tuple[str, list[str]]]:
        """获取文件对应的语言服务器命令。"""
        return _detect_language(file_path)

    def get_client(self, file_path: str, root_path: str = None) -> Optional[LspClient]:
        """获取或创建文件对应的 LSP 客户端。"""
        info = self._get_server_cmd(file_path)
        if not info:
            return None
        language, cmd = info
        root = root_path or os.path.dirname(os.path.abspath(file_path))
        cache_key = f"{language}:{root}"
        with self._lock:
            client = self._clients.get(cache_key)
            if client and client.status != LspServerStatus.CONNECTED:
                client.shutdown()
                client = None
            if not client:
                client = LspClient(language, cmd, root)
                if not client.start():
                    return None
                self._clients[cache_key] = client
            return client

    def shutdown_all(self):
        """关闭所有语言服务器。"""
        with self._lock:
            for c in self._clients.values():
                c.shutdown()
            self._clients.clear()
