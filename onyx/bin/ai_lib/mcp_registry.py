#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP 工具注册表

替代全局 MCP_TOOLS_CACHE dict，提供线程安全的工具注册/查找/导出。
支持：
  - 按 server 注册工具 schema
  - 按前缀批量移除（mcp__<server>__ 热移除）
  - 导出给 LLM 的 schemas 列表
  - Schema 缓存指纹（SHA-256），加速冷启动
"""

from __future__ import annotations

import os
import json
import hashlib
import threading
from typing import Dict, List, Optional, Any


class MCPRegistry:
    """
    线程安全的 MCP 工具注册表。

    Registry 设计：
      - Add(tool)     → 注册一个工具
      - RemovePrefix  → 按前缀移除
      - Schemas()     → 导出给 LLM Provider
    """

    def __init__(self):
        self._lock = threading.RLock()  # RLock: 可重入，避免 replace_server → remove_server → remove_prefix 递归死锁
        self._tools: Dict[str, Dict] = {}          # full_name → tool_schema
        self._server_tools: Dict[str, List[str]] = {}  # server_name → [full_names]

    # ── 注册 / 移除 ──

    def register(self, server_name: str, tool_def: dict) -> str:
        """
        注册一个 MCP 工具。

        Args:
            server_name: MCP 服务器名（如 "filesystem"）
            tool_def:    tools/list 返回的单个工具定义
                         {name, description, inputSchema, annotations?}

        Returns:
            完整的工具名（mcp__<server>__<tool>）
        """
        tool_name = tool_def.get("name", "unknown")
        full_name = f"mcp__{server_name}__{tool_name}"

        with self._lock:
            self._tools[full_name] = {
                "name": full_name,
                "description": tool_def.get("description", ""),
                "inputSchema": tool_def.get("inputSchema", {}),
                "annotations": tool_def.get("annotations", {}),
                "_server": server_name,
                "_raw_name": tool_name,
            }

            if server_name not in self._server_tools:
                self._server_tools[server_name] = []
            if full_name not in self._server_tools[server_name]:
                self._server_tools[server_name].append(full_name)

        return full_name

    def register_batch(self, server_name: str, tools: List[dict]) -> List[str]:
        """批量注册工具，返回完整工具名列表"""
        names = []
        for tool_def in tools:
            names.append(self.register(server_name, tool_def))
        return names

    def remove_prefix(self, prefix: str) -> int:
        """
        按前缀移除工具（如 "mcp__filesystem__"）。

        Returns:
            移除的工具数量
        """
        with self._lock:
            to_remove = [name for name in self._tools if name.startswith(prefix)]
            for name in to_remove:
                server = self._tools[name].get("_server", "")
                if server in self._server_tools:
                    try:
                        self._server_tools[server].remove(name)
                    except ValueError:
                        pass
                    if not self._server_tools[server]:
                        del self._server_tools[server]
                del self._tools[name]
            return len(to_remove)

    def remove_server(self, server_name: str) -> int:
        """移除指定服务器的所有工具"""
        return self.remove_prefix(f"mcp__{server_name}__")

    # ── 查询 ──

    def get(self, full_name: str) -> Optional[dict]:
        """获取单个工具的 schema"""
        with self._lock:
            return self._tools.get(full_name)

    def get_by_server(self, server_name: str) -> List[dict]:
        """获取指定服务器的所有工具"""
        with self._lock:
            names = self._server_tools.get(server_name, [])
            return [self._tools[n] for n in names if n in self._tools]

    def schemas(self) -> List[dict]:
        """导出所有工具 schema（给 LLM Provider 注入 system prompt）"""
        with self._lock:
            return list(self._tools.values())

    def server_names(self) -> List[str]:
        """列出所有已注册的服务器名"""
        with self._lock:
            return list(self._server_tools.keys())

    def tool_count(self) -> int:
        """已注册工具总数"""
        with self._lock:
            return len(self._tools)

    def has_server(self, server_name: str) -> bool:
        """检查服务器是否已注册"""
        with self._lock:
            return server_name in self._server_tools

    # ── 替换（用于 lazy 加载的原子替换）──

    def replace_server(self, server_name: str, tools: List[dict]) -> List[str]:
        """
        原子替换指定服务器的所有工具（先移除再注册）。

        用于 lazy 加载：先用缓存 schema 注册占位符，
        handshake 完成后用真实 schema 原子替换。
        """
        with self._lock:
            self.remove_server(server_name)
            return self.register_batch(server_name, tools)


# ─────────────────────────── Schema 缓存 ───────────────────────────

class MCPSchemaCache:
    """
    MCP 工具 Schema 持久化缓存。

    Cache 设计：
      - 对 server 配置（command/args/env/url）做 SHA-256 指纹
      - 成功 handshake 后缓存 tools/prompts/resources schema
      - 下次启动指纹匹配则直接使用缓存，跳过握手
    """

    def __init__(self, cache_dir: str = None):
        if cache_dir is None:
            cache_dir = os.path.join(
                os.path.expanduser("~"), ".cache", "onyx", "mcp_schemas"
            )
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    @staticmethod
    def fingerprint(server_config: dict) -> str:
        """
        对服务器配置做 SHA-256 指纹。

        用于检测配置变更，决定是否需要重新握手。
        """
        # 选取影响工具 schema 的关键字段
        key_fields = {
            "command": server_config.get("command", ""),
            "args": json.dumps(server_config.get("args", []), sort_keys=True),
            "env": json.dumps(server_config.get("env", {}), sort_keys=True),
            "url": server_config.get("url", ""),
        }
        raw = json.dumps(key_fields, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _cache_path(self, server_name: str) -> str:
        """获取缓存文件路径"""
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in server_name)
        return os.path.join(self.cache_dir, f"{safe_name}.json")

    def get(self, server_name: str, fingerprint: str) -> Optional[List[dict]]:
        """
        读取缓存。指纹匹配才返回，否则返回 None。
        """
        path = self._cache_path(server_name)
        if not os.path.exists(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("_fingerprint") == fingerprint:
                return data.get("tools", [])
        except Exception:
            pass

        return None

    def put(self, server_name: str, fingerprint: str, tools: List[dict]) -> None:
        """写入缓存"""
        path = self._cache_path(server_name)
        try:
            data = {
                "_fingerprint": fingerprint,
                "_updated": __import__('time').strftime("%Y-%m-%dT%H:%M:%S"),
                "tools": tools,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def invalidate(self, server_name: str) -> None:
        """删除指定服务器的缓存"""
        path = self._cache_path(server_name)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


# ─────────────────────────── 全局单例 ───────────────────────────

# 替代旧的 MCP_TOOLS_CACHE dict
_global_registry: Optional[MCPRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> MCPRegistry:
    """获取全局 MCP 工具注册表单例"""
    global _global_registry
    if _global_registry is None:
        with _registry_lock:
            if _global_registry is None:
                _global_registry = MCPRegistry()
    return _global_registry


def reset_registry() -> None:
    """重置全局注册表（用于测试/重连）"""
    global _global_registry
    with _registry_lock:
        _global_registry = MCPRegistry()
