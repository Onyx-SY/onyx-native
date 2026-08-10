# -*- coding: utf-8 -*-
"""
tool_results.py — Tool Result Handling

基于 Reasonix 的 truncateToolOutput + PruneStaleToolResults 设计，提供：
  1. truncate_tool_output()  — 32KB head+tail 截断
  2. prune_stale_results()   — 剪枝超出保护区的旧工具结果
  3. is_error_result()       — 检测错误输出
  4. format_tool_result()    — 格式化工具结果为 AI 可读格式
  5. estimate_result_tokens()— 估算工具输出的 token 数
"""

import re
from typing import Dict, List, Optional, Tuple


# ── Constants ──

MAX_TOOL_OUTPUT_BYTES = 32 * 1024          # 32KB — 单工具输出上限
MIN_PRUNE_BYTES = 1024                      # 1KB — 低于此值不剪枝
PRUNED_MARKER = "[elided tool result — "    # 剪枝标记前缀
TRUNC_MARKER = "\n...[truncated {removed} of {total} bytes]..."  # 截断标记

# Error detection patterns (case-insensitive)
ERROR_PATTERNS = [
    r"^error:",
    r"^blocked:",
    r"^denied:",
    r"^traceback\s*\(",
    r"^exception:",
    r"^fatal:",
    r"^\s*Traceback\s",
]


# ── Truncation ──

def truncate_tool_output(output: str, max_bytes: int = MAX_TOOL_OUTPUT_BYTES) -> str:
    """
    截断工具输出到 max_bytes。
    采用 head+tail 策略：保留前半 + 后半各一半，中间插入截断标记。

    小输出（≤ max_bytes）原样返回。

    Args:
        output: 工具原始输出
        max_bytes: 输出上限（字节）

    Returns:
        截断后的输出
    """
    output_bytes = output.encode("utf-8")
    if len(output_bytes) <= max_bytes:
        return output

    half = max_bytes // 2

    # 前半部分（按字符边界截断）
    head_bytes = output_bytes[:half]
    head = head_bytes.decode("utf-8", errors="replace")

    # 后半部分
    tail_bytes = output_bytes[-half:]
    tail = tail_bytes.decode("utf-8", errors="replace")

    removed = len(output_bytes) - max_bytes
    total = len(output_bytes)

    marker = TRUNC_MARKER.format(removed=removed, total=total)
    return head + marker + "\n" + tail


# ── Pruning ──

def prune_stale_results(
    messages: List[Dict],
    context_window: int = 0,
    keep_errors: bool = True,
    keep_user_marked: bool = True
) -> Tuple[List[Dict], int, int]:
    """
    剪枝（elide）保护区之外的旧工具结果。
    将工具结果替换为 [elided tool result — <name>, N bytes dropped] 标记。

    剪枝规则：
      - 仅处理 role=tool 的消息
      - 输出 < MIN_PRUNE_BYTES → 跳过
      - 已剪枝的消息（内容以 PRUNED_MARKER 开头）→ 跳过
      - 错误消息 → 如果 keep_errors=True 则跳过
      - 用户标记 [[keep]] → 如果 keep_user_marked=True 则跳过
      - 超出 context_window 保护区的 → 剪枝

    Args:
        messages: 消息列表
        context_window: 上下文窗口大小（0=禁用剪枝）
        keep_errors: 是否保留错误消息
        keep_user_marked: 是否保留 [[keep]] 标记的消息

    Returns:
        (pruned_messages, pruned_count, saved_chars)
    """
    if context_window <= 0:
        return messages, 0, 0

    # 保护区：最近 N 条消息
    protected = max(2, min(len(messages), context_window // 4000))

    pruned = list(messages)
    pruned_count = 0
    saved_chars = 0

    for i in range(len(pruned) - protected):
        msg = pruned[i]

        if msg.get("role") != "tool":
            continue

        content = msg.get("content", "")
        if len(content) < MIN_PRUNE_BYTES:
            continue
        if content.startswith(PRUNED_MARKER):
            continue

        # 错误消息保留
        if keep_errors and is_error_result(msg):
            continue

        # 用户标记保留
        if keep_user_marked and _has_keep_marker(msg):
            continue

        tool_name = msg.get("name", msg.get("tool_name", "unknown"))
        placeholder = (
            f"{PRUNED_MARKER}{tool_name}, "
            f"{len(content)} bytes dropped to save context; "
            f"re-run the tool if the data is needed again]"
        )

        saved_chars += len(content) - len(placeholder)
        pruned[i] = {**msg, "content": placeholder, "_pruned": True}
        pruned_count += 1

    return pruned, pruned_count, saved_chars


# ── Error Detection ──

def is_error_result(message_or_content) -> bool:
    """
    检测工具结果是否为错误输出。

    匹配规则（大小写不敏感）：
      - 以 "error:" / "blocked:" / "denied:" 开头
      - 包含 "Traceback (most recent call last):"
      - 以 "Exception:" / "Fatal:" 开头

    Args:
        message_or_content: Dict（消息对象）或 str（纯文本）

    Returns:
        bool
    """
    if isinstance(message_or_content, dict):
        # 检查 is_error 标志
        if message_or_content.get("is_error"):
            return True
        content = message_or_content.get("content", "")
    else:
        content = str(message_or_content)

    content_lower = content.strip().lower()

    for pattern in ERROR_PATTERNS:
        if re.match(pattern, content, re.IGNORECASE):
            return True
        if re.match(pattern, content_lower):
            return True

    return False


# ── Formatting ──

def format_tool_result_for_api(
    tool_name: str,
    output: str,
    is_error: bool = False,
    max_bytes: int = MAX_TOOL_OUTPUT_BYTES
) -> Dict:
    """
    将工具执行结果格式化为 API 消息。

    Args:
        tool_name: 工具名
        output: 工具输出
        is_error: 是否为错误
        max_bytes: 输出上限

    Returns:
        格式化后的消息 dict (role=tool)
    """
    truncated = truncate_tool_output(output, max_bytes)

    # 添加错误标记
    if is_error:
        prefix = "error: "
    elif truncated != output:
        prefix = ""
    else:
        prefix = ""

    return {
        "role": "tool",
        "tool_call_id": "",  # caller fills
        "name": tool_name,
        "content": prefix + truncated,
        "is_error": is_error,
        "_truncated": truncated != output,
        "_original_bytes": len(output.encode("utf-8")),
    }


def format_tool_results_for_context(
    tool_calls: List[Dict],
    tool_results: List[Dict],
    max_per_result: int = MAX_TOOL_OUTPUT_BYTES
) -> str:
    """
    将工具调用和结果格式化为上下文文本（注入到下一轮 AI 请求）。

    Args:
        tool_calls: 工具调用列表 [{"name": str, "params_str": str, ...}]
        tool_results: 工具结果列表 [{"output": str, "ok": bool, ...}]
        max_per_result: 每个结果的输出上限

    Returns:
        格式化的 Markdown 文本
    """
    import datetime

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"### Tool Calls — Round ({now})", ""]

    for tc, tr in zip(tool_calls, tool_results):
        name = tc.get("name", "unknown")
        ok = tr.get("ok", True)
        output = tr.get("output", "")

        icon = "✅" if ok else "❌"
        lines.append(f"- **{icon} {name}**")

        # 截断 + 错误标记
        truncated = truncate_tool_output(output, max_per_result)
        is_err = is_error_result(output) or not ok

        if is_err:
            lines.append(f"  ```error")
        else:
            lines.append(f"  ```")

        lines.append(f"  {truncated}")
        lines.append(f"  ```")

        if truncated != output:
            lines.append(f"  *(output truncated: "
                         f"{len(output.encode('utf-8'))} → "
                         f"{len(truncated.encode('utf-8'))} bytes)*")

    lines.append("")
    return "\n".join(lines)


# ── Token Estimation ──

def estimate_result_tokens(output: str) -> int:
    """
    估算工具输出的 token 数。
    使用保守估计：char/4，至少 1。
    """
    if not output:
        return 0
    return max(1, len(output) // 4)


# ── Helpers ──

def _has_keep_marker(msg: Dict) -> bool:
    """检测消息是否包含 [[keep]] 标记（委托给 memory_compact.has_keep_marker）"""
    try:
        from bin.ai_lib.memory_compact import has_keep_marker
        return has_keep_marker(msg)
    except ImportError:
        # Fallback: inline check
        if msg.get("role") != "user":
            return False
        content = msg.get("content", "").strip().lower()
        markers = ["[[keep]]", "[keep]", "<keep>", "<!-- keep -->", "[[保留]]", "[保留]"]
        return any(content.startswith(m) for m in markers)
