"""
token_budget.py — Token 预算管理器

对标 Claude Code 的上下文预算管理。

能力:
  - estimate_tokens(text) → int: 快速 token 估算
  - trim_context(messages, max_tokens) → list: 超过上限自动裁历史
  - ContextTracker: 追踪每轮 token 消耗

用法:
  from lib.token_budget import estimate_tokens, trim_context, ContextTracker

  # 估算
  tokens = estimate_tokens("Hello, world!")

  # 裁剪历史（保留最近）
  trimmed = trim_context(messages, max_tokens=8000)

  # 追踪器
  tracker = ContextTracker(max_tokens=8000)
  tracker.add_turn(user_msg, assistant_msg)
  if tracker.over_limit():
      tracker.trim()
"""

from typing import List, Dict, Any, Optional
import re
import threading


# ──────────────────────────── Token 估算 ──────────────────────

# 基于 tokenizer 统计的平均值
# 英文: ~4 chars/token, 中文: ~1.5 chars/token
_CHAR_RATIO_EN = 4.0
_CHAR_RATIO_CN = 1.5

# 结构开销（角色标记、消息包裹等）
_STRUCTURAL_OVERHEAD = 8  # 每条消息的额外 token


def _contains_cjk(text: str) -> bool:
    """检测是否包含中日韩字符。"""
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3000-\u303f]', text))


def estimate_tokens(text: str) -> int:
    """
    快速 token 估算（无需 tokenizer 模型）。

    对混合中英文做了简单启发式。
    """
    if not text:
        return 0
    if _contains_cjk(text):
        # 中英文混合：逐字符估算
        cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        ascii_count = len(text) - cjk_count
        return int(cjk_count / _CHAR_RATIO_CN + ascii_count / _CHAR_RATIO_EN) + 2
    else:
        return int(len(text) / _CHAR_RATIO_EN) + 2


def estimate_message_tokens(msg: Dict[str, str]) -> int:
    """估算单条消息的 token 数（含结构开销）。"""
    total = _STRUCTURAL_OVERHEAD
    for key in ("role", "content", "name"):
        val = msg.get(key, "")
        if val:
            total += estimate_tokens(str(val))
    return total


def estimate_messages_tokens(messages: List[Dict[str, str]]) -> int:
    """估算消息列表的总 token 数。"""
    return sum(estimate_message_tokens(m) for m in messages)


# ──────────────────────────── 上下文裁剪 ──────────────────────

def trim_context(
    messages: List[Dict[str, str]],
    max_tokens: int,
    preserve_first: int = 1,
) -> List[Dict[str, str]]:
    """
    超过 max_tokens 时自动裁剪历史消息。

    策略:
      1. 保留前 preserve_first 条（通常是系统提示词）
      2. 从旧到新移除消息，直到低于上限

    返回: 裁剪后的消息列表
    """
    if not messages:
        return []

    # 计算总 token
    total = estimate_messages_tokens(messages)
    if total <= max_tokens:
        return messages

    # 保留前 N 条（系统提示词等）
    preserved = messages[:preserve_first]
    candidates = messages[preserve_first:]

    while candidates and estimate_messages_tokens(preserved + candidates) > max_tokens:
        # 从最早的消息开始移除
        removed = candidates.pop(0)
        # 如果只剩一条还超限，保留系统提示词 + 最后一条
        if not candidates:
            candidates = messages[-1:] if len(messages) > preserve_first else []
            if estimate_messages_tokens(preserved + candidates) > max_tokens:
                candidates = []
            break

    return preserved + candidates


def compress_message(msg: Dict[str, str], max_content_len: int = 2000) -> Dict[str, str]:
    """压缩单条消息的 content 字段（截断 + 摘要）。"""
    result = dict(msg)
    content = msg.get("content", "")
    if len(content) > max_content_len:
        result["content"] = content[:max_content_len] + (
            f"\n\n[...truncated, original {len(content)} chars]"
        )
    return result


# ──────────────────────────── ContextTracker ──────────────────

class ContextTracker:
    """
    对话上下文 Token 追踪器。

    用法:
      tracker = ContextTracker(max_tokens=8000)
      tracker.add_turn({"role": "user", "content": "..."},
                       {"role": "assistant", "content": "..."})
      if tracker.over_limit():
          tracker.trim()
      print(f"Usage: {tracker.usage_pct:.1f}%")
    """

    def __init__(self, max_tokens: int = 8192, warn_ratio: float = 0.8):
        self.max_tokens = max_tokens
        self.warn_threshold = int(max_tokens * warn_ratio)
        self._messages: List[Dict[str, str]] = []
        self._lock = threading.Lock()

    @property
    def total_tokens(self) -> int:
        return estimate_messages_tokens(self._messages)

    @property
    def usage_pct(self) -> float:
        if self.max_tokens <= 0:
            return 0.0
        return min(100.0, self.total_tokens / self.max_tokens * 100)

    @property
    def is_over_limit(self) -> bool:
        return self.total_tokens > self.max_tokens

    @property
    def is_warning(self) -> bool:
        return self.total_tokens > self.warn_threshold

    def add_message(self, msg: Dict[str, str]) -> None:
        """添加单条消息。"""
        with self._lock:
            self._messages.append(msg)

    def add_turn(self, user_msg: Dict[str, str], assistant_msg: Dict[str, str]) -> None:
        """添加一轮对话（用户 + AI）。"""
        with self._lock:
            self._messages.append(user_msg)
            self._messages.append(assistant_msg)

    def trim(self, preserve_first: int = 1) -> List[Dict[str, str]]:
        """裁剪历史到 max_tokens 以下。"""
        with self._lock:
            self._messages = trim_context(self._messages, self.max_tokens, preserve_first)
        return self._messages

    def get_messages(self) -> List[Dict[str, str]]:
        return list(self._messages)

    def summary(self) -> str:
        """返回纯 token 用量文本（不含百分比，因为模型上下文窗口差异大）。"""
        total = self.total_tokens
        return f"~{total} tokens（{len(self._messages)} 条消息）"
