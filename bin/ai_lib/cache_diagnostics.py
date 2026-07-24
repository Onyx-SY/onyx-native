# -*- coding: utf-8 -*-
"""
cache_diagnostics.py — Prefix-Stable Cache Diagnostics

基于 Reasonix cache_shape.go 的设计，提供：
  1. PrefixShape — 对 API 请求前缀做 SHA256 哈希快照
  2. CompareShape — 跨 turn 对比，归因缓存失效原因
  3. CacheBreakEvent — 结构化缓存断裂事件
  4. format_cache_report — 人类可读的缓存报告

设计目标：
  - 让 Onyx 用户知道每次 API 调用的缓存命中情况
  - 归因缓存失效的根因（system/tools/messages/rewrite）
  - 辅助调试 [PROMPT]: 导致的 prefix 污染问题
"""

import hashlib
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ── Data Classes ──

@dataclass
class PrefixShape:
    """API 请求前缀的快照哈希"""
    system_hash: str = ""
    tools_hash: str = ""
    messages_hash: str = ""
    prefix_hash: str = ""          # 综合哈希 (system + tools + first N messages)
    rewrite_version: int = 0       # 压缩/重写次数
    tool_schema_tokens: int = 0    # 工具 schema 估算 token 数
    captured_at: float = 0.0       # 捕获时间戳

    def to_dict(self) -> Dict:
        return {
            "system_hash": self.system_hash,
            "tools_hash": self.tools_hash,
            "messages_hash": self.messages_hash,
            "prefix_hash": self.prefix_hash,
            "rewrite_version": self.rewrite_version,
            "tool_schema_tokens": self.tool_schema_tokens,
            "captured_at": self.captured_at,
        }


@dataclass
class CacheDiagnostics:
    """缓存诊断结果"""
    prefix_hash: str = ""
    prefix_changed: bool = False
    prefix_change_reasons: List[str] = field(default_factory=list)
    system_hash: str = ""
    tools_hash: str = ""
    rewrite_version: int = 0
    tool_schema_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_hit_rate: float = 0.0   # 0.0 ~ 1.0


@dataclass
class CacheBreakEvent:
    """缓存断裂事件"""
    unexpected: bool = False       # True = 前缀未变但 tokens 下降（可能是 TTL 过期）
    reason: str = ""
    previous_cache_read_tokens: int = 0
    current_cache_read_tokens: int = 0
    token_drop: int = 0


@dataclass
class SessionCacheStats:
    """会话级缓存统计（跨 turn 累积）"""
    total_turns: int = 0
    total_hit_tokens: int = 0
    total_miss_tokens: int = 0
    total_expected_breaks: int = 0
    total_unexpected_breaks: int = 0
    breaks: List[CacheBreakEvent] = field(default_factory=list)

    @property
    def aggregate_hit_rate(self) -> float:
        total = self.total_hit_tokens + self.total_miss_tokens
        if total == 0:
            return 0.0
        return self.total_hit_tokens / total

    def record(self, diagnostics: CacheDiagnostics, break_event: Optional[CacheBreakEvent] = None):
        self.total_turns += 1
        self.total_hit_tokens += diagnostics.cache_hit_tokens
        self.total_miss_tokens += diagnostics.cache_miss_tokens
        if break_event:
            if break_event.unexpected:
                self.total_unexpected_breaks += 1
            else:
                self.total_expected_breaks += 1
            self.breaks.append(break_event)


# ── Hashing ──

def _short_hash(value) -> str:
    """SHA256 前 16 字符（8 字节 hex）"""
    if isinstance(value, str):
        data = value.encode("utf-8")
    elif isinstance(value, (list, dict)):
        data = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    else:
        data = str(value).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def _normalize_tools(tools: List[Dict]) -> List[Dict]:
    """归一化工具列表（排序保证哈希稳定）"""
    if not tools:
        return []
    normalized = []
    for t in tools:
        # 只保留影响缓存的字段
        normalized.append({
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "parameters": t.get("parameters", t.get("input_schema", {})),
        })
    normalized.sort(key=lambda t: (t["name"], t["description"]))
    return normalized


def _estimate_tokens(text: str) -> int:
    """粗略 token 估算（char/4）"""
    if not text:
        return 0
    return max(1, len(text) // 4)


# ── Core API ──

def capture_prefix_shape(
    system_prompt: str,
    tools: List[Dict],
    messages_prefix: str,
    rewrite_version: int = 0
) -> PrefixShape:
    """
    捕获当前 API 请求前缀的形状快照。

    Args:
        system_prompt: 系统提示词全文
        tools: 工具定义列表（OpenAI 格式）
        messages_prefix: 消息前缀（用户消息中稳定不变的部分）
        rewrite_version: 压缩/改写计数器

    Returns:
        PrefixShape
    """
    normalized_tools = _normalize_tools(tools)
    tools_json = json.dumps(normalized_tools, sort_keys=True, ensure_ascii=False)

    shape = PrefixShape(
        system_hash=_short_hash(system_prompt),
        tools_hash=_short_hash(tools_json),
        messages_hash=_short_hash(messages_prefix),
        prefix_hash=_short_hash({
            "system": system_prompt,
            "tools": tools_json,
            "messages": messages_prefix,
        }),
        rewrite_version=rewrite_version,
        tool_schema_tokens=_estimate_tokens(tools_json),
        captured_at=time.time(),
    )
    return shape


def compare_shapes(
    prev: PrefixShape,
    cur: PrefixShape,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0
) -> CacheDiagnostics:
    """
    比较前后两次前缀形状，返回缓存诊断结果。

    Args:
        prev: 上一次的前缀快照
        cur: 当前前缀快照
        cache_hit_tokens: API 返回的 cache_read_input_tokens
        cache_miss_tokens: API 返回的 cache_creation_input_tokens

    Returns:
        CacheDiagnostics 含变更原因和命中率
    """
    reasons = []

    if prev.system_hash and prev.system_hash != cur.system_hash:
        reasons.append("system_prompt_changed")

    if prev.tools_hash and prev.tools_hash != cur.tools_hash:
        reasons.append("tool_definitions_changed")

    if prev.messages_hash and prev.messages_hash != cur.messages_hash:
        reasons.append("message_prefix_changed")

    if prev.rewrite_version != cur.rewrite_version:
        reasons.append("log_rewrite")

    total = cache_hit_tokens + cache_miss_tokens
    hit_rate = cache_hit_tokens / total if total > 0 else 0.0

    return CacheDiagnostics(
        prefix_hash=cur.prefix_hash,
        prefix_changed=len(reasons) > 0,
        prefix_change_reasons=reasons,
        system_hash=cur.system_hash,
        tools_hash=cur.tools_hash,
        rewrite_version=cur.rewrite_version,
        tool_schema_tokens=cur.tool_schema_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        cache_hit_rate=hit_rate,
    )


def detect_cache_break(
    previous_diag: Optional[CacheDiagnostics],
    current_diag: CacheDiagnostics,
    break_min_drop: int = 2000,
    prompt_ttl_seconds: int = 300
) -> Optional[CacheBreakEvent]:
    """
    检测缓存是否意外断裂。

    逻辑：
      1. 若前缀未变但 cache_read_tokens 下降 > break_min_drop → unexpected break
      2. 若前缀已变 → expected break
      3. 若前缀未变但时间超 TTL → possible TTL expiry（expected）

    Args:
        previous_diag: 上一轮的诊断
        current_diag: 当前轮的诊断
        break_min_drop: token 下降阈值（低于此值忽略）
        prompt_ttl_seconds: 缓存 TTL 秒数

    Returns:
        CacheBreakEvent 或 None（无断裂）
    """
    if previous_diag is None:
        return None

    token_drop = previous_diag.cache_hit_tokens - current_diag.cache_hit_tokens
    if token_drop < break_min_drop:
        return None  # 下降太少，忽略

    if current_diag.prefix_changed:
        return CacheBreakEvent(
            unexpected=False,
            reason="prefix_changed: " + ", ".join(current_diag.prefix_change_reasons),
            previous_cache_read_tokens=previous_diag.cache_hit_tokens,
            current_cache_read_tokens=current_diag.cache_hit_tokens,
            token_drop=token_drop,
        )

    # 前缀未变但 tokens 下降 → 意外
    return CacheBreakEvent(
        unexpected=True,
        reason="cache_read_tokens dropped while prefix remained stable",
        previous_cache_read_tokens=previous_diag.cache_hit_tokens,
        current_cache_read_tokens=current_diag.cache_hit_tokens,
        token_drop=token_drop,
    )


# ── Formatting ──

def format_cache_report(diag: CacheDiagnostics) -> str:
    """
    格式化缓存诊断报告（单行，适合状态栏）。

    Example output:
      "cache: 85% hit (8500 cached / 1500 new) ∅ changes"
      "cache: 0% hit (0 cached / 8000 new) ⚠ system_prompt_changed"
    """
    hit = diag.cache_hit_tokens
    miss = diag.cache_miss_tokens
    rate = diag.cache_hit_rate

    pct = f"{rate:.0%}" if rate < 1 else "100%"

    if not diag.prefix_changed:
        status = "∅ stable"
    else:
        short_reasons = []
        for r in diag.prefix_change_reasons:
            short = {
                "system_prompt_changed": "sys",
                "tool_definitions_changed": "tools",
                "message_prefix_changed": "msg",
                "log_rewrite": "rewrite",
            }.get(r, r[:8])
            short_reasons.append(short)
        status = "⚠ " + ",".join(short_reasons)

    return f"cache: {pct} hit ({hit} cached / {miss} new) {status}"


def format_cache_summary(stats: SessionCacheStats) -> str:
    """
    格式化会话级缓存摘要。

    Example:
      Session cache: 78% aggregate hit rate across 12 turns
        Expected breaks: 2 (system_prompt_changed, message_prefix_changed)
        Unexpected breaks: 0
    """
    lines = [
        f"Session cache: {stats.aggregate_hit_rate:.0%} aggregate hit rate "
        f"across {stats.total_turns} turns",
    ]
    if stats.total_expected_breaks > 0:
        reasons = list(set(b.reason for b in stats.breaks if not b.unexpected))
        lines.append(f"  Expected breaks: {stats.total_expected_breaks} "
                     f"({', '.join(reasons[:3])})")
    if stats.total_unexpected_breaks > 0:
        lines.append(f"  ⚠ Unexpected breaks: {stats.total_unexpected_breaks}")
    lines.append(f"  Total: {stats.total_hit_tokens} cached / "
                 f"{stats.total_miss_tokens} new tokens")
    return "\n".join(lines)


# ── Integration Helper ──

def extract_cache_tokens_from_usage(usage: Dict) -> Tuple[int, int]:
    """
    从 API 响应的 usage 中提取缓存 token 数据。

    DeepSeek API 格式:
      usage.cache_hit_tokens  → cache_read_input_tokens
      usage.cache_miss_tokens → cache_creation_input_tokens

    OpenAI 兼容格式:
      usage.prompt_tokens_details.cached_tokens → cache_read_input_tokens

    Args:
        usage: API 响应的 usage dict

    Returns:
        (cache_hit_tokens, cache_miss_tokens)
    """
    # DeepSeek 格式
    hit = usage.get("cache_hit_tokens", 0) or usage.get("cache_read_input_tokens", 0)
    miss = usage.get("cache_miss_tokens", 0) or usage.get("cache_creation_input_tokens", 0)

    # OpenAI 格式（fallback）
    if hit == 0 and miss == 0:
        details = usage.get("prompt_tokens_details", {})
        hit = details.get("cached_tokens", 0)
        total_prompt = usage.get("prompt_tokens", 0)
        miss = total_prompt - hit

    # 如果都没有，从 input_tokens 估算
    if hit == 0 and miss == 0:
        total = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        miss = total

    return hit, miss
