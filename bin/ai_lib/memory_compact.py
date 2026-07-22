# -*- coding: utf-8 -*-
"""
memory_compact.py — Trident 三阶段记忆压缩引擎

从 Claw Code (Rust) 移植到 Python，适配 Onyx 的 library 记忆系统。

三阶段：
  1. Supersede  — 文件操作去重：同文件先 VIEW 后 EDIT/WRITE → 标记 VIEW 过时
  2. Collapse   — 闲聊折叠：连续短消息合并为摘要
  3. Cluster    — 相似聚类：同工具+同文件的操作合并

触发条件（自动）：
  - library 条目数 > MAX_ENTRIES (默认 20)
  - 总 token 估算 > MAX_TOKENS (默认 10,000)
"""

import os
import re
from typing import Dict, List, Optional, Tuple
from collections import defaultdict


# ── 配置 ──

class CompactConfig:
    """压缩配置"""
    def __init__(self,
                 max_entries: int = 20,
                 max_tokens: int = 10_000,
                 preserve_recent: int = 4,
                 collapse_threshold: int = 4,
                 cluster_min_size: int = 3,
                 cluster_similarity_threshold: float = 0.6):
        self.max_entries = max_entries
        self.max_tokens = max_tokens
        self.preserve_recent = preserve_recent          # 保留最近 N 条完整
        self.collapse_threshold = collapse_threshold     # 连续闲聊 >= N 条触发折叠
        self.cluster_min_size = cluster_min_size         # 聚类最小大小
        self.cluster_similarity_threshold = cluster_similarity_threshold


# ── Token 估算 ──

def estimate_tokens(text: str) -> int:
    """粗略估算文本 token 数：4 字符 ≈ 1 token (英文) / 1.5 字符 ≈ 1 token (混合)"""
    if not text:
        return 0
    return max(len(text) // 4, len(text.encode("utf-8")) // 3)


# ── Stage 1: Supersede — 文件操作去重 ──

def stage1_supersede(entries: List[Dict]) -> Tuple[List[Dict], int]:
    """
    同一文件先 VIEW 后 EDIT/WRITE → 标记 VIEW 过时。
    不删除条目，只打标记，后续压缩时跳过。
    
    Args:
        entries: [{"session_id": str, "content": str, "time": str}, ...]
    
    Returns:
        (filtered_entries, superseded_count)
    """
    # 按时间排序（最旧在前）
    sorted_entries = sorted(entries, key=lambda e: e.get("time", ""))
    
    # 追踪每个文件的最后写操作时间
    file_last_write: Dict[str, int] = {}  # path → entry_index
    
    # 第一遍：记录每个文件的最后写操作
    for i, entry in enumerate(sorted_entries):
        content = entry.get("content", "")
        file_ops = _extract_file_operations(content)
        for op in file_ops:
            if op["type"] in ("edit", "write", "edit_range", "delete"):
                file_last_write[op["path"]] = i
    
    # 第二遍：标记被取代的 VIEW
    superseded_indices = set()
    for i, entry in enumerate(sorted_entries):
        content = entry.get("content", "")
        file_ops = _extract_file_operations(content)
        for op in file_ops:
            if op["type"] == "view":
                last_write_idx = file_last_write.get(op["path"])
                if last_write_idx is not None and last_write_idx > i:
                    # 这个 VIEW 被后来的写操作取代了
                    superseded_indices.add(i)
    
    # 过滤
    kept = [e for i, e in enumerate(sorted_entries) if i not in superseded_indices]
    return kept, len(superseded_indices)


def _extract_file_operations(content: str) -> List[Dict]:
    """
    从 library 条目的 Markdown 内容中提取文件操作。
    
    匹配格式：
      #### ✅ 📖 VIEW — `/path/to/file` (lines 10-30)
      #### ✅ ✏️ EDIT — `/path/to/file`
      #### ✅ 📝 WRITE — `/path/to/file`
    """
    ops = []
    # 匹配文件操作标题行（支持 VIEW:FULL / VIEW:RANGE / VIEW:SEARCH 等子类型标记）
    pattern = re.compile(
        r'####\s+[✅❌]\s+([📖✏️📝🗑️➕📌🔄📦]+)\s+(\w+(?::\w+)?)\s+[—–-]\s+`([^`]+)`',
        re.UNICODE
    )
    for match in pattern.finditer(content):
        icon = match.group(1)
        op_name = match.group(2)
        path = match.group(3)
        
        # 映射操作类型（VIEW:FULL → view, EDIT → edit, 等）
        type_map = {
            "📖": "view", "VIEW": "view",
            "✏️": "edit", "EDIT": "edit",
            "📝": "write", "WRITE": "write",
            "🗑️": "delete", "DELETE": "delete",
            "➕": "append", "APPEND": "append",
            "📌": "insert", "INSERT": "insert",
        }
        # 去除子类型后缀：VIEW:FULL → VIEW, VIEW:RANGE → VIEW
        base_op_name = op_name.split(":")[0] if ":" in op_name else op_name
        op_type = type_map.get(icon, type_map.get(base_op_name, base_op_name.lower()))
        ops.append({"type": op_type, "path": path})
    
    return ops


# ── Stage 2: Collapse — 闲聊折叠 ──

def stage2_collapse(entries: List[Dict], threshold: int = 4) -> Tuple[List[Dict], int, int]:
    """
    连续短消息（< 200 字符，无工具调用/文件操作）合并为摘要。
    
    Returns:
        (collapsed_entries, chains_found, messages_collapsed)
    """
    if len(entries) < threshold:
        return entries, 0, 0
    
    result = []
    buffer = []
    chains_found = 0
    messages_collapsed = 0
    
    for entry in entries:
        if _is_chatty_entry(entry):
            buffer.append(entry)
        else:
            if len(buffer) >= threshold:
                summary = _generate_collapse_summary(buffer)
                chains_found += 1
                messages_collapsed += len(buffer)
                result.append({
                    "session_id": f"collapsed_{chains_found}",
                    "time": buffer[0].get("time", ""),
                    "content": f"[Collapsed Conversation]\n{summary}",
                    "_compacted": True,
                })
            else:
                result.extend(buffer)
            buffer.clear()
            result.append(entry)
    
    # 处理尾部缓冲区
    if len(buffer) >= threshold:
        summary = _generate_collapse_summary(buffer)
        chains_found += 1
        messages_collapsed += len(buffer)
        result.append({
            "session_id": f"collapsed_{chains_found}",
            "time": buffer[0].get("time", ""),
            "content": f"[Collapsed Conversation]\n{summary}",
            "_compacted": True,
        })
    else:
        result.extend(buffer)
    
    return result, chains_found, messages_collapsed


def _is_chatty_entry(entry: Dict) -> bool:
    """判断是否为闲聊条目（短消息，无工具调用，无文件操作）"""
    content = entry.get("content", "")
    
    # 太短 → 不是闲聊，是正常短交互
    if len(content) < 50:
        return False
    
    # 包含工具调用或文件操作 → 不是闲聊
    if re.search(r'### (Tool Calls|工具调用|File Operations|文件操作记录)', content):
        return False
    
    # 包含命令执行 → 不是闲聊
    if re.search(r'#### (Command|命令) #', content):
        return False
    
    # < 200 字符的纯文本交互 → 闲聊
    return len(content) < 200


def _generate_collapse_summary(entries: List[Dict]) -> str:
    """为折叠的闲聊生成摘要"""
    user_msgs = []
    ai_msgs = []
    
    for entry in entries:
        content = entry.get("content", "")
        # 提取用户提问
        q_match = re.search(r'### (?:Question|用户提问)\n(.*?)(?:\n\n|\n###|\n####)', content, re.DOTALL)
        if q_match:
            user_msgs.append(q_match.group(1).strip()[:80])
        # 提取 AI 回答
        a_match = re.search(r'### (?:AI Response|AI回答)\n(.*?)(?:\n\n|\n###|\n####)', content, re.DOTALL)
        if a_match:
            ai_msgs.append(a_match.group(1).strip()[:80])
    
    lines = [
        f"Collapsed {len(entries)} short exchanges.",
    ]
    
    if user_msgs:
        # 去重后显示
        unique_msgs = list(dict.fromkeys(user_msgs))[:5]
        lines.append("Topics:")
        for msg in unique_msgs:
            lines.append(f"  - {msg}")
    
    return "\n".join(lines)


# ── Stage 3: Cluster — 相似聚类 ──

def stage3_cluster(entries: List[Dict], min_size: int = 3,
                   similarity_threshold: float = 0.6) -> Tuple[List[Dict], int, int]:
    """
    相似消息（同工具+同文件）聚类合并。
    
    Returns:
        (clustered_entries, clusters_found, messages_clustered)
    """
    if len(entries) < min_size:
        return entries, 0, 0
    
    # 构建指纹
    fingerprints = []
    for i, entry in enumerate(entries):
        fp = _fingerprint_entry(i, entry)
        if fp:
            fingerprints.append(fp)
    
    if len(fingerprints) < min_size:
        return entries, 0, 0
    
    # 聚类
    cluster_assignments = {}  # entry_index → cluster_id
    cluster_id = 0
    
    for i in range(len(fingerprints)):
        if fingerprints[i]["index"] in cluster_assignments:
            continue
        
        cluster_members = [fingerprints[i]["index"]]
        
        for j in range(i + 1, len(fingerprints)):
            if fingerprints[j]["index"] in cluster_assignments:
                continue
            similarity = _compute_similarity(fingerprints[i], fingerprints[j])
            if similarity >= similarity_threshold:
                cluster_members.append(fingerprints[j]["index"])
        
        if len(cluster_members) >= min_size:
            for member_idx in cluster_members:
                cluster_assignments[member_idx] = cluster_id
            cluster_id += 1
    
    if not cluster_assignments:
        return entries, 0, 0
    
    # 按 cluster 分组输出
    total_clustered = len(cluster_assignments)
    clusters_found = cluster_id
    
    # 收集每个 cluster 的条目
    cluster_buffers = defaultdict(list)
    for entry_idx, cid in cluster_assignments.items():
        cluster_buffers[cid].append(entry_idx)
    
    result = []
    for i, entry in enumerate(entries):
        if i in cluster_assignments:
            cid = cluster_assignments[i]
            # 只在第一个成员处输出聚类摘要
            if cluster_buffers[cid] and cluster_buffers[cid][0] == i:
                cluster_entries = [entries[idx] for idx in cluster_buffers[cid]]
                summary = _generate_cluster_summary(cluster_entries)
                result.append({
                    "session_id": f"clustered_{cid}",
                    "time": cluster_entries[0].get("time", ""),
                    "content": f"[Clustered {len(cluster_entries)} messages]\n{summary}",
                    "_compacted": True,
                })
        else:
            result.append(entry)
    
    return result, clusters_found, total_clustered


def _fingerprint_entry(index: int, entry: Dict) -> Optional[Dict]:
    """为条目构建指纹"""
    content = entry.get("content", "")
    if not content:
        return None
    
    tool_names = set()
    file_paths = set()
    
    # 提取工具调用
    for match in re.finditer(r'^- `(\w+)', content, re.MULTILINE):
        tool_names.add(match.group(1))
    
    # 提取文件路径
    for match in re.finditer(r'`([^`]+\.(?:py|js|ts|go|rs|java|cpp|c|h|sh|json|yaml|yml|toml|md|html|css))`', content):
        file_paths.add(match.group(1))
    
    return {
        "index": index,
        "tool_names": tool_names,
        "file_paths": file_paths,
        "text_length": len(content),
    }


def _compute_similarity(a: Dict, b: Dict) -> float:
    """计算两个指纹的 Jaccard 相似度"""
    # 工具名相似度 (40%)
    if not a["tool_names"] and not b["tool_names"]:
        tool_overlap = 1.0
    elif not a["tool_names"] or not b["tool_names"]:
        tool_overlap = 0.0
    else:
        intersection = len(a["tool_names"] & b["tool_names"])
        union = len(a["tool_names"] | b["tool_names"])
        tool_overlap = intersection / union if union > 0 else 0.0
    
    # 文件路径相似度 (40%)
    if not a["file_paths"] and not b["file_paths"]:
        file_overlap = 1.0
    elif not a["file_paths"] or not b["file_paths"]:
        file_overlap = 0.0
    else:
        intersection = len(a["file_paths"] & b["file_paths"])
        union = len(a["file_paths"] | b["file_paths"])
        file_overlap = intersection / union if union > 0 else 0.0
    
    # 文本长度相似度 (20%)
    if a["text_length"] == 0 and b["text_length"] == 0:
        length_sim = 1.0
    elif a["text_length"] == 0 or b["text_length"] == 0:
        length_sim = 0.0
    else:
        min_len = min(a["text_length"], b["text_length"])
        max_len = max(a["text_length"], b["text_length"])
        length_sim = min_len / max_len
    
    return 0.4 * tool_overlap + 0.4 * file_overlap + 0.2 * length_sim


def _generate_cluster_summary(entries: List[Dict]) -> str:
    """为聚类条目生成摘要"""
    tool_names = set()
    file_paths = set()
    
    for entry in entries:
        content = entry.get("content", "")
        for match in re.finditer(r'^- `(\w+)', content, re.MULTILINE):
            tool_names.add(match.group(1))
        for match in re.finditer(r'`([^`]+\.(?:py|js|ts|go|rs|java|cpp|c|h|sh|json|yaml|yml|toml|md|html|css))`', content):
            file_paths.add(match.group(1))
    
    lines = [f"{len(entries)} similar messages grouped."]
    
    if tool_names:
        lines.append(f"Tools: {', '.join(sorted(tool_names))}.")
    
    if file_paths:
        paths = sorted(file_paths)[:5]
        lines.append(f"Files: {', '.join(paths)}.")
    
    return "\n".join(lines)


# ── 完整 summary 格式（取自 Claw Code compact.rs）──

# 可恢复会话的前导/尾部指令（与 Claw Code 对齐）
_COMPACT_CONTINUATION_PREAMBLE = (
    "This session is being continued from a previous conversation that ran out of context. "
    "The summary below covers the earlier portion of the conversation.\n\n"
)
_COMPACT_RECENT_MESSAGES_NOTE = "Recent messages are preserved verbatim."
_COMPACT_DIRECT_RESUME_INSTRUCTION = (
    "Continue the conversation from where it left off without asking the user any further questions. "
    "Resume directly — do not acknowledge the summary, do not recap what was happening, "
    "and do not preface with continuation text."
)


def summarize_messages(entries: List[Dict]) -> str:
    """
    对一批条目生成完整的 <summary> XML 摘要。
    完全对齐 Claw Code compact.rs::summarize_messages()。
    
    包含: Scope, Tools mentioned, Recent user requests, 
          Pending work, Key files, Current work, Key timeline
    """
    user_count = 0
    assistant_count = 0
    tool_names_set = set()
    key_files_set = set()
    
    for entry in entries:
        content = entry.get("content", "")
        # 角色计数（从内容推断）
        if re.search(r'### (?:Question|用户提问)', content):
            user_count += 1
        if re.search(r'### (?:AI Response|AI回答)', content):
            assistant_count += 1
        
        # 提取工具名
        for match in re.finditer(r'^- `(\w+)', content, re.MULTILINE):
            tool_names_set.add(match.group(1))
        
        # 提取文件路径
        for match in re.finditer(r'`([^`]+\.(?:py|js|ts|go|rs|java|cpp|c|h|sh|json|yaml|yml|toml|md|html|css))`', content):
            key_files_set.add(match.group(1))
    
    lines = [
        "<summary>",
        "Conversation summary:",
        f"- Scope: {len(entries)} earlier entries compacted "
        f"(user={user_count}, assistant={assistant_count}).",
    ]
    
    if tool_names_set:
        lines.append(f"- Tools mentioned: {', '.join(sorted(tool_names_set))}.")
    
    # Recent user requests（最近 3 条用户提问）
    recent = _collect_recent_user_requests(entries, limit=3)
    if recent:
        lines.append("- Recent user requests:")
        for r in recent:
            lines.append(f"  - {r}")
    
    # Pending work
    pending = _infer_pending_work(entries)
    if pending:
        lines.append("- Pending work:")
        for p in pending:
            lines.append(f"  - {p}")
    
    # Key files (最多 8 个)
    if key_files_set:
        top_files = sorted(key_files_set)[:8]
        lines.append(f"- Key files referenced: {', '.join(top_files)}.")
    
    # Current work（最近一条非空文本）
    current = _infer_current_work(entries)
    if current:
        lines.append(f"- Current work: {current}")
    
    # Key timeline（每条消息的 role + block 摘要）
    lines.append("- Key timeline:")
    for entry in entries:
        role = _infer_entry_role(entry)
        summary = _summarize_entry_blocks(entry)
        lines.append(f"  - {role}: {summary}")
    
    lines.append("</summary>")
    return "\n".join(lines)


def _collect_recent_user_requests(entries: List[Dict], limit: int = 3) -> List[str]:
    """提取最近 N 条用户提问（倒序取，再正序输出）"""
    requests = []
    for entry in reversed(entries):
        content = entry.get("content", "")
        m = re.search(r'### (?:Question|用户提问)\n(.*?)(?:\n###|\n####|\n---|\Z)', content, re.DOTALL)
        if m:
            text = m.group(1).strip()
            requests.append(_truncate_summary(text, 160))
            if len(requests) >= limit:
                break
    return list(reversed(requests))


def _infer_pending_work(entries: List[Dict]) -> List[str]:
    """推断待办工作（含 todo/next/pending/follow up/remaining 关键词的消息）"""
    keywords = ["todo", "next", "pending", "follow up", "remaining",
                "待办", "下一步", "剩余", "跟进"]
    items = []
    for entry in reversed(entries):
        content = entry.get("content", "").lower()
        if any(kw in content for kw in keywords):
            m = re.search(r'### (?:AI Response|AI回答)\n(.*?)(?:\n###|\n####|\n---|\Z)', 
                         entry.get("content", ""), re.DOTALL)
            if m:
                text = m.group(1).strip()
                items.append(_truncate_summary(text, 160))
            if len(items) >= 3:
                break
    return list(reversed(items))


def _infer_current_work(entries: List[Dict]) -> Optional[str]:
    """推断当前工作（最近一条非空文本）"""
    for entry in reversed(entries):
        content = entry.get("content", "")
        for section in ["AI Response", "AI回答", "Question", "用户提问"]:
            m = re.search(rf'### (?:{section})\n(.*?)(?:\n###|\n####|\n---|\Z)', content, re.DOTALL)
            if m:
                text = m.group(1).strip()
                if text:
                    return _truncate_summary(text, 200)
    return None


def _infer_entry_role(entry: Dict) -> str:
    """推断条目角色"""
    content = entry.get("content", "")
    if "_compacted" in entry:
        return "system"
    if re.search(r'### (?:Question|用户提问)', content):
        return "user"
    if re.search(r'### (?:AI Response|AI回答)', content):
        return "assistant"
    return "system"


def _summarize_entry_blocks(entry: Dict) -> str:
    """摘要一条目的内容块（对齐 Claw Code summarize_block()）"""
    content = entry.get("content", "")
    
    # 文件操作
    file_ops = _extract_file_operations(content)
    if file_ops:
        op_names = [f"{op['type']}({os.path.basename(op['path'])})" for op in file_ops]
        return _truncate_summary(" | ".join(op_names[:3]), 160)
    
    # 工具调用
    tool_match = re.findall(r'^- `(\w+)', content, re.MULTILINE)
    if tool_match:
        return _truncate_summary("tool_calls: " + ", ".join(tool_match[:3]), 160)
    
    # 文本内容
    text_match = re.search(r'### (?:AI Response|AI回答|Question|用户提问)\n(.*?)(?:\n###|\n####|\n---|\Z)', content, re.DOTALL)
    if text_match:
        return _truncate_summary(text_match.group(1).strip(), 160)
    
    return _truncate_summary(content[:80], 160)


def _truncate_summary(text: str, max_chars: int) -> str:
    """截断文本到 max_chars 字符"""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


# ── 摘要格式化与合并（取自 Claw Code compact.rs）──

def format_compact_summary(summary: str) -> str:
    """
    规范化压缩摘要为用户可读格式。
    对齐 Claw Code format_compact_summary():
      - 剥离 <analysis> 标签
      - <summary> 内容提取为 "Summary:\n..."
      - 合并连续空行
    """
    # 剥离 <analysis>
    result = _strip_tag_block(summary, "analysis")
    
    # <summary> 内容格式化
    inner = _extract_tag_block(result, "summary")
    if inner:
        result = result.replace(
            f"<summary>{inner}</summary>",
            f"Summary:\n{inner.strip()}"
        )
    
    # 合并连续空行
    lines = []
    last_blank = False
    for line in result.split("\n"):
        is_blank = not line.strip()
        if is_blank and last_blank:
            continue
        lines.append(line)
        last_blank = is_blank
    
    return "\n".join(lines).strip()


def get_compact_continuation_message(summary: str) -> str:
    """
    生成压缩后可恢复会话的系统消息。
    对齐 Claw Code get_compact_continuation_message().
    """
    formatted = format_compact_summary(summary)
    parts = [_COMPACT_CONTINUATION_PREAMBLE + formatted]
    parts.append(_COMPACT_RECENT_MESSAGES_NOTE)
    parts.append(_COMPACT_DIRECT_RESUME_INSTRUCTION)
    return "\n\n".join(parts)


def merge_compact_summaries(existing_summary: Optional[str], new_summary: str) -> str:
    """
    合并已有压缩摘要和新摘要（重压缩时使用）。
    对齐 Claw Code merge_compact_summaries():
      - 展平 prior highlights（不嵌套）
      - 新内容追加到 "Newly compacted context:" 下
      - Key timeline 仅保留新的
    """
    if not existing_summary:
        return new_summary
    
    previous_highlights = _extract_summary_highlights(existing_summary)
    new_formatted = format_compact_summary(new_summary)
    new_highlights = _extract_summary_highlights(new_formatted)
    new_timeline = _extract_summary_timeline(new_formatted)
    
    lines = ["<summary>", "Conversation summary:"]
    
    # 展平 prior highlights（直接列出，不嵌套）
    for line in previous_highlights:
        lines.append(f"- {line}")
    
    if new_highlights:
        lines.append("- Newly compacted context:")
        for line in new_highlights:
            lines.append(f"  {line}")
    
    if new_timeline:
        lines.append("- Key timeline:")
        for line in new_timeline:
            lines.append(f"  {line}")
    
    lines.append("</summary>")
    return "\n".join(lines)


def _extract_tag_block(content: str, tag: str) -> Optional[str]:
    """提取 XML 标签内容"""
    start_tag = f"<{tag}>"
    end_tag = f"</{tag}>"
    si = content.find(start_tag)
    if si == -1:
        return None
    si += len(start_tag)
    ei = content.find(end_tag, si)
    if ei == -1:
        return None
    return content[si:ei]


def _strip_tag_block(content: str, tag: str) -> str:
    """剥离 XML 标签"""
    start_tag = f"<{tag}>"
    end_tag = f"</{tag}>"
    si = content.find(start_tag)
    ei = content.find(end_tag)
    if si != -1 and ei != -1:
        return content[:si] + content[ei + len(end_tag):]
    return content


def _extract_summary_highlights(summary: str) -> List[str]:
    """提取摘要高亮行（排除 timeline 和空行）"""
    formatted = format_compact_summary(summary)
    lines = []
    in_timeline = False
    for line in formatted.split("\n"):
        trimmed = line.strip()
        if not trimmed or trimmed in ("Summary:", "Conversation summary:"):
            continue
        if trimmed == "- Key timeline:":
            in_timeline = True
            continue
        if in_timeline:
            continue
        lines.append(trimmed)
    return lines


def _extract_summary_timeline(summary: str) -> List[str]:
    """提取摘要中的 Key timeline 部分"""
    formatted = format_compact_summary(summary)
    lines = []
    in_timeline = False
    for line in formatted.split("\n"):
        trimmed = line.strip()
        if trimmed == "- Key timeline:":
            in_timeline = True
            continue
        if not in_timeline:
            continue
        if not trimmed:
            break
        lines.append(trimmed)
    return lines


def extract_existing_compacted_summary(entries: List[Dict]) -> Optional[str]:
    """
    从条目列表中检测已存在的压缩摘要。
    返回第一个 <session_compact_summary> 或 <summary> 内容。
    """
    for entry in entries:
        content = entry.get("content", "")
        inner = _extract_tag_block(content, "session_compact_summary")
        if inner:
            return inner
        inner = _extract_tag_block(content, "summary")
        if inner:
            return f"<summary>{inner}</summary>"
    return None


# ── Summary 后压缩（取自 Claw Code summary_compression.rs）──

class SummaryCompressionBudget:
    """摘要压缩预算"""
    def __init__(self, max_chars: int = 1200, max_lines: int = 24, max_line_chars: int = 160):
        self.max_chars = max_chars
        self.max_lines = max_lines
        self.max_line_chars = max_line_chars


def compress_summary(summary: str, budget: SummaryCompressionBudget = None) -> str:
    """
    优先级行选择压缩摘要。
    对齐 Claw Code summary_compression.rs:
      Priority 0: Summary:/Conversation summary:/核心详情行
      Priority 1: 节标题（以 : 结尾）
      Priority 2: 列表项（- 或空格开头）
      Priority 3: 其他
    """
    if budget is None:
        budget = SummaryCompressionBudget()
    
    # 1. Normalize（去重 + 截断长行）
    lines = _normalize_summary_lines(summary, budget.max_line_chars)
    if not lines or budget.max_chars == 0 or budget.max_lines == 0:
        return ""
    
    # 2. 优先级选择
    selected = _select_lines_by_priority(lines, budget)
    if not selected:
        selected = [lines[0][:budget.max_chars]]
    
    # 3. 省略提示
    omitted = len(lines) - len(selected)
    if omitted > 0:
        notice = f"- … {omitted} additional line(s) omitted."
        candidate = selected + [notice]
        if len(candidate) <= budget.max_lines:
            joined = "\n".join(candidate)
            if len(joined) <= budget.max_chars:
                selected.append(notice)
    
    return "\n".join(selected)


def _normalize_summary_lines(summary: str, max_line_chars: int) -> List[str]:
    """标准化摘要行：折叠空白、去重（大小写不敏感）、截断"""
    seen = set()
    result = []
    for raw_line in summary.split("\n"):
        # 折叠行内空白
        normalized = " ".join(raw_line.split())
        if not normalized:
            continue
        # 截断超长行
        if len(normalized) > max_line_chars:
            normalized = normalized[:max_line_chars - 1] + "…"
        # 去重（大小写不敏感）
        dedupe_key = normalized.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append(normalized)
    return result


def _select_lines_by_priority(lines: List[str], budget: SummaryCompressionBudget) -> List[str]:
    """按优先级选择行，遵守预算限制"""
    selected = []
    
    for priority in range(4):
        for i, line in enumerate(lines):
            if i in _selected_indices(selected, lines):
                continue
            if _line_priority(line) != priority:
                continue
            
            # 尝试加入候选
            candidate = selected + [line]
            if len(candidate) > budget.max_lines:
                continue
            candidate_text = "\n".join(candidate)
            if len(candidate_text) > budget.max_chars:
                continue
            
            selected.append(line)
    
    return selected


def _selected_indices(selected: List[str], lines: List[str]) -> set:
    """返回已选中行的索引集合"""
    indices = set()
    for s in selected:
        try:
            indices.add(lines.index(s))
        except ValueError:
            pass
    return indices


def _line_priority(line: str) -> int:
    """确定行的优先级（对齐 Claw Code line_priority()）"""
    if line in ("Summary:", "Conversation summary:") or _is_core_detail(line):
        return 0
    if _is_section_header(line):
        return 1
    if line.startswith("- ") or line.startswith("  - "):
        return 2
    return 3


def _is_core_detail(line: str) -> bool:
    """判断是否为核心详情行"""
    prefixes = [
        "- Scope:", "- Current work:", "- Pending work:",
        "- Key files referenced:", "- Tools mentioned:",
        "- Recent user requests:", "- Previously compacted context:",
        "- Newly compacted context:",
    ]
    return any(line.startswith(p) for p in prefixes)


def _is_section_header(line: str) -> bool:
    """判断是否为节标题（以 : 结尾）"""
    return line.endswith(":")


# ── 汇总压缩 ──

class CompactResult:
    """压缩结果"""
    def __init__(self):
        self.entries: List[Dict] = []
        self.superseded_count: int = 0
        self.collapsed_chains: int = 0
        self.messages_collapsed: int = 0
        self.clusters_found: int = 0
        self.messages_clustered: int = 0
        self.original_count: int = 0
        self.final_count: int = 0
        self.tokens_saved_estimate: int = 0
        self.summary: str = ""


def compact_library_entries(entries: List[Dict],
                            config: CompactConfig = None) -> CompactResult:
    """
    对 library 条目执行完整 Trident 压缩管道。
    
    Args:
        entries: library 条目列表，每个条目包含 content, time, session_id
        config: 压缩配置
    
    Returns:
        CompactResult
    """
    if config is None:
        config = CompactConfig()
    
    result = CompactResult()
    result.original_count = len(entries)
    
    if not entries:
        return result
    
    # 计算原始 token
    original_tokens = sum(estimate_tokens(e.get("content", "")) for e in entries)
    
    # 保留最近 N 条不参与压缩
    preserve_count = min(config.preserve_recent, len(entries))
    to_compact = entries[:-preserve_count] if preserve_count > 0 else entries
    preserved = entries[-preserve_count:] if preserve_count > 0 else []
    
    if not to_compact:
        result.entries = entries
        result.final_count = len(entries)
        return result
    
    working = list(to_compact)
    
    # Stage 1: Supersede
    working, result.superseded_count = stage1_supersede(working)
    
    # Stage 2: Collapse
    working, result.collapsed_chains, result.messages_collapsed = stage2_collapse(
        working, config.collapse_threshold
    )
    
    # Stage 3: Cluster
    working, result.clusters_found, result.messages_clustered = stage3_cluster(
        working, config.cluster_min_size, config.cluster_similarity_threshold
    )
    
    # 合并保留的最近条目
    result.entries = working + preserved
    result.final_count = len(result.entries)
    
    # Token 节省估算
    final_tokens = sum(estimate_tokens(e.get("content", "")) for e in result.entries)
    result.tokens_saved_estimate = max(0, original_tokens - final_tokens)
    
    # 生成压缩摘要
    if result.superseded_count > 0 or result.collapsed_chains > 0 or result.clusters_found > 0:
        result.summary = _generate_compaction_summary(result)
    
    return result


def _generate_compaction_summary(result: CompactResult) -> str:
    """生成压缩操作摘要（存入 library）"""
    compression = result.original_count / result.final_count if result.final_count > 0 else 1.0
    
    lines = [
        "<session_compact_summary>",
        "Conversation summary:",
        f"- Scope: {result.original_count} entries compacted to {result.final_count} ({compression:.1f}x).",
    ]
    
    if result.superseded_count > 0:
        lines.append(f"- File operation dedup: {result.superseded_count} obsolete VIEWs removed.")
    
    if result.collapsed_chains > 0:
        lines.append(f"- Chat collapse: {result.collapsed_chains} chains → {result.messages_collapsed} messages folded.")
    
    if result.clusters_found > 0:
        lines.append(f"- Clustering: {result.clusters_found} groups found ({result.messages_clustered} messages).")
    
    if result.tokens_saved_estimate > 0:
        lines.append(f"- Est. tokens saved: ~{result.tokens_saved_estimate}")
    
    # Key files referenced
    all_files = set()
    for entry in result.entries:
        for match in re.finditer(r'`([^`]+\.(?:py|js|ts|go|rs|java|cpp|c|h|sh|json|yaml|yml|toml|md|html|css))`', entry.get("content", "")):
            all_files.add(match.group(1))
    if all_files:
        top_files = sorted(all_files)[:8]
        lines.append(f"- Key files referenced: {', '.join(top_files)}.")
    
    lines.append("</session_compact_summary>")
    
    return "\n".join(lines)


def should_compact(entries: List[Dict], config: CompactConfig = None) -> bool:
    """判断是否需要触发压缩"""
    if config is None:
        config = CompactConfig()
    
    if len(entries) <= config.preserve_recent:
        return False
    
    if len(entries) > config.max_entries:
        return True
    
    total_tokens = sum(estimate_tokens(e.get("content", "")) for e in entries)
    return total_tokens >= config.max_tokens
