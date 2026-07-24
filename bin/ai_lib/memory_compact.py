# -*- coding: utf-8 -*-
"""
memory_compact.py — Trident 三阶段记忆压缩引擎

Onyx library 记忆压缩引擎。

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
    """
    估算文本 token 数（保守估计，实际偏差 ±20%）。
    
    DeepSeek tokenizer 特征：
      - 英文单词 ≈ 1-1.5 tokens/词
      - 中文字 ≈ 1.5-2 tokens/字
      - 代码符号 ≈ 0.3-0.5 tokens/字符
    
    本实现：中英文分开计数后加权，对混合内容比纯 char/4 准 2-3 倍。
    """
    if not text:
        return 0
    import re
    # 英文单词（含下划线、数字）
    eng_words = len(re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', text))
    # 中文字符
    cjk_chars = len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', text))
    # 剩余字符（标点、空格、换行等）
    remaining = len(text) - eng_words * 4 - cjk_chars  # 粗略扣除
    
    # 加权估算：英文 ~1.3 tokens/词，中文 ~1.8 tokens/字，其余 ~0.25 tokens/字符
    est = int(eng_words * 1.3 + cjk_chars * 1.8 + max(remaining, 0) * 0.25)
    
    # 保守下界：纯英文 char/4，纯中文 bytes/3
    lower = max(len(text) // 4, len(text.encode("utf-8")) // 3)
    
    return max(est, lower)


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


# ── 会话摘要格式 ──

# 可恢复会话的前导/尾部指令
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
    生成完整的会话摘要。
    
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
    """摘要一条目的内容块"""
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


# ── 摘要格式化与合并 ──

def format_compact_summary(summary: str) -> str:
    """
    规范化压缩摘要为用户可读格式。
    规范化压缩摘要为用户可读格式：剥离 analysis 标签、提取 summary、合并空行。
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
    生成压缩后可恢复会话的系统消息。
    """
    formatted = format_compact_summary(summary)
    parts = [_COMPACT_CONTINUATION_PREAMBLE + formatted]
    parts.append(_COMPACT_RECENT_MESSAGES_NOTE)
    parts.append(_COMPACT_DIRECT_RESUME_INSTRUCTION)
    return "\n\n".join(parts)


def merge_compact_summaries(existing_summary: Optional[str], new_summary: str) -> str:
    """
    合并已有压缩摘要和新摘要（重压缩时使用）。
    合并已有压缩摘要和新摘要：展平 prior highlights、新内容追加、时间线只保留新的。
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


# ── Summary 后压缩 ──

class SummaryCompressionBudget:
    """摘要压缩预算"""
    def __init__(self, max_chars: int = 1200, max_lines: int = 24, max_line_chars: int = 160):
        self.max_chars = max_chars
        self.max_lines = max_lines
        self.max_line_chars = max_line_chars


def compress_summary(summary: str, budget: SummaryCompressionBudget = None) -> str:
    """
    优先级行选择压缩摘要。
    优先级行选择压缩摘要：Priority 0 核心详情、1 节标题、2 列表项、3 其他。
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
    """确定行的优先级"""
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

# ── 压缩计数追踪 ──

_COMPACTION_COUNT_FILE = ".compaction_count"

def _read_compaction_count(library_dir: str) -> int:
    """读取已压缩次数"""
    try:
        with open(os.path.join(library_dir, _COMPACTION_COUNT_FILE), "r") as f:
            return int(f.read().strip())
    except Exception:
        return 0


def _write_compaction_count(library_dir: str, count: int) -> None:
    """写入压缩次数"""
    try:
        with open(os.path.join(library_dir, _COMPACTION_COUNT_FILE), "w") as f:
            f.write(str(count))
    except Exception:
        pass


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
        self.compaction_count: int = 0  # 累计压缩次数


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
    result.compaction_count = 0
    
    if not entries:
        return result
    
    # 读取已有压缩计数（需要 library dir，通过 entry path 推断）
    if entries and entries[0].get("path"):
        lib_dir = os.path.dirname(entries[0]["path"])
        result.compaction_count = _read_compaction_count(lib_dir) + 1
        _write_compaction_count(lib_dir, result.compaction_count)
    
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


# ── Live Conversation Compaction ──
# 直接压缩 current_question 字符串（而非 library 文件）。
# 参考 Claw-Code compact.rs 的 structured summary + resume-directly 设计。

_COMPACT_LIVE_PREAMBLE = (
    "## Earlier conversation (compacted to save context)\n"
)
_COMPACT_RESUME_INSTRUCTION = (
    "Continue directly — do not acknowledge this summary.\n"
)


def compact_live_conversation(
    current_question: str,
    keep_last_rounds: int = 3,
    max_bytes: int = 50 * 1024,
) -> str:
    """
    将 current_question 中过旧的工具调用轮次替换为结构化摘要，
    保留最近 N 轮完整内容。

    设计目标（参考 Claw-Code）：
      - 旧轮次 → 紧凑结构化摘要（Goal / Decisions / Files / Errors / Next）
      - 新轮次 → 保留原文（保证即时上下文不丢失）
      - 注入 resume-directly 指令（防止 AI 浪费 token 确认摘要）
      - 摘要放在顶部（AI 先看到摘要建立全局认知，再看到最近细节）

    Args:
        current_question: 完整的 current_question 字符串
        keep_last_rounds: 保留最近 N 轮工具调用完整内容
        max_bytes: 超过此阈值才触发压缩

    Returns:
        压缩后的 current_question 字符串
    """
    if len(current_question.encode("utf-8")) <= max_bytes:
        return current_question

    # 按工具调用轮次切分
    round_marker = r"### 第 \d+ 轮工具调用"
    parts = re.split(f"({round_marker} [^\n]*)", current_question)

    # 重组：每个轮次 = header + body
    rounds = []
    preamble = ""
    i = 0
    # 跳过第一个轮次标记之前的内容（初始 question + memory 等）
    while i < len(parts) and not re.match(round_marker, parts[i]):
        preamble += parts[i]
        i += 1

    while i < len(parts):
        header = parts[i]  # "### 第 N 轮工具调用 (...)"
        body = parts[i + 1] if i + 1 < len(parts) else ""
        rounds.append((header, body))
        i += 2

    if len(rounds) <= keep_last_rounds:
        return current_question

    # 拆分：旧轮次 → 摘要，新轮次 → 保留原文
    old_rounds = rounds[:-keep_last_rounds]
    recent_rounds = rounds[-keep_last_rounds:]

    # 生成结构化摘要（只摘要旧轮次的关键信息）
    summary = _generate_live_summary(old_rounds)

    # 组装输出：摘要 → 最近轮次原文
    result_parts = [preamble.rstrip()]
    result_parts.append("")
    result_parts.append(_COMPACT_LIVE_PREAMBLE + summary)
    result_parts.append("")
    result_parts.append(_COMPACT_RESUME_INSTRUCTION)
    result_parts.append("")

    for header, body in recent_rounds:
        result_parts.append(header)
        result_parts.append(body)

    return "\n".join(result_parts)


def _generate_live_summary(rounds: List[Tuple[str, str]]) -> str:
    """
    从旧工具调用轮次中提取结构化摘要。
    
    输出格式（优先级从高到低，帮助 AI 注意力聚焦）：
      ## Goal & Status     — 当前目标和进度
      ## Decisions Made    — 已做的关键决策
      ## Files Touched     — 涉及的文件
      ## Errors & Fixes    — 遇到的错误及修复
      ## Pending           — 待完成事项
    """
    # 收集所有轮次的文本
    all_text = "\n".join(body for _, body in rounds)
    all_text_lower = all_text.lower()

    lines = []

    # ── Goal & Status ──
    goals = _extract_goals(all_text)
    if goals:
        lines.append("## Goal & Status")
        for g in goals[:3]:
            lines.append(f"- {g}")
        lines.append("")

    # ── Files Touched ──
    files = set()
    for _, body in rounds:
        for match in re.finditer(r'`([^`]+\.(?:py|js|ts|go|rs|java|cpp|c|h|sh|json|yaml|yml|toml|md|html|css))`', body):
            files.add(match.group(1))
        # Also catch tool names with paths
        for match in re.finditer(r'(?:read_file|write_file|edit_file|glob|search)\s+[`"]?([^\s`"]+)[`"]?', body):
            candidate = match.group(1)
            if "/" in candidate and "." in candidate.split("/")[-1]:
                files.add(candidate)
    if files:
        lines.append("## Files Touched")
        for f in sorted(files)[:10]:
            lines.append(f"- `{f}`")
        lines.append("")

    # ── Errors & Fixes ──
    errors = []
    for _, body in rounds:
        for match in re.finditer(
            r'(?:error:|Error:|❌|Traceback|Exception|failed|失败|错误)[^\n]{0,200}',
            body,
        ):
            err_text = match.group(0).strip()[:150]
            if err_text not in errors:
                errors.append(err_text)
    if errors:
        lines.append("## Errors & Fixes")
        for e in errors[:5]:
            lines.append(f"- {e}")
        lines.append("")

    # ── Decisions Made ──
    decisions = []
    for _, body in rounds:
        for kw in ["decided", "chose", "opted", "went with", "决定", "选择", "采用"]:
            idx = body.lower().find(kw)
            if idx >= 0:
                snippet = body[max(0, idx - 20):idx + 150].strip()
                snippet = snippet.replace("\n", " ")[:160]
                if snippet not in decisions:
                    decisions.append(snippet)
    if decisions:
        lines.append("## Decisions Made")
        for d in decisions[:5]:
            lines.append(f"- {d}")
        lines.append("")

    # ── Pending ──
    pending = []
    for _, body in rounds:
        for kw in ["todo", "next", "pending", "remaining", "待办", "下一步", "剩余"]:
            idx = body.lower().find(kw)
            if idx >= 0:
                snippet = body[max(0, idx - 10):idx + 150].strip()
                snippet = snippet.replace("\n", " ")[:160]
                if snippet not in pending:
                    pending.append(snippet)
    if pending:
        lines.append("## Pending")
        for p in pending[:5]:
            lines.append(f"- {p}")
        lines.append("")

    if not lines:
        return f"({len(rounds)} earlier tool-call rounds compacted — no significant patterns detected.)"

    return "\n".join(lines)


def _extract_goals(text: str) -> List[str]:
    """从文本中提取目标描述"""
    goals = []
    # Look for task/question lines in the preamble
    for match in re.finditer(
        r'(?:#Task|任务|Goal|目标|要做|需要)[：:\s]*([^\n]{10,200})',
        text,
        re.IGNORECASE,
    ):
        g = match.group(1).strip()
        if len(g) > 10 and g not in goals:
            goals.append(g)
    # Also catch user questions as implicit goals
    for match in re.finditer(
        r'(?:用户提问|Question)[：:\s]*\n?([^\n]{15,200})',
        text,
    ):
        g = match.group(1).strip()
        if g not in goals:
            goals.append(g)
    return goals


# ── Tool Pair Boundary Protection ──

def compact_tool_pairs_safe(
    messages: List[Dict],
    preserve_recent: int = 4
) -> List[Dict]:
    """
    安全压缩：确保不在 ToolUse/ToolResult 对中间切断。
    
    如果保留区的第一条消息是 ToolResult，但其前的 Assistant(ToolUse) 
    在压缩区，则将边界回退以包含完整的工具对。
    
    Args:
        messages: 消息列表，每条含 {"role": str, "content": str, ...}
        preserve_recent: 保留最近 N 条完整消息
    
    Returns:
        调整后的消息列表（保持工具对完整性）
    """
    if len(messages) <= preserve_recent:
        return messages
    
    keep_from = max(0, len(messages) - preserve_recent)
    
    # 回退边界直到不撕裂工具对
    while keep_from > 0 and keep_from < len(messages):
        first_preserved = messages[keep_from]
        
        # 如果保留区第一条是 tool 角色
        if first_preserved.get("role") == "tool":
            preceding = messages[keep_from - 1]
            # 前一条是 assistant 且有 tool_calls → 对完整，回退一条包含 assistant
            if preceding.get("role") == "assistant" and preceding.get("tool_calls"):
                keep_from -= 1
                break
            # 否则继续回退
            keep_from -= 1
        # 如果保留区第一条是 assistant 且有 tool_calls
        elif (first_preserved.get("role") == "assistant" 
              and first_preserved.get("tool_calls")):
            # 检查后面的 tool 消息是否也都在保留区
            # 如果助手调用了工具，工具结果也必须保留
            call_ids = {tc.get("id") for tc in first_preserved.get("tool_calls", [])}
            for j in range(keep_from + 1, len(messages)):
                msg = messages[j]
                if msg.get("role") == "tool" and msg.get("tool_call_id") in call_ids:
                    call_ids.discard(msg.get("tool_call_id"))
                elif msg.get("role") != "tool":
                    break
            # 所有工具结果都在保留区 → OK
            if not call_ids:
                break
            # 有孤儿工具结果 → 继续回退
            keep_from -= 1
        else:
            break
    
    return messages


# ── Keep Policy ──

_KEEP_MARKERS = [
    "[[keep]]", "[keep]", "<keep>", "<!-- keep -->",
    "[[KEEP]]", "[KEEP]", "<KEEP>", "<!-- KEEP -->",
]


def has_keep_marker(message: Dict) -> bool:
    """
    检测用户是否用 [[keep]] 标记了此消息。
    
    匹配格式（大小写不敏感）：
      - [[keep]] / [keep] / <keep> / <!-- keep -->
      - 支持中英混合：[[保留]] / [保留]
    """
    content = message.get("content", "")
    if message.get("role") != "user":
        return False
    
    content_lower = content.strip().lower()
    for marker in _KEEP_MARKERS:
        if content_lower.startswith(marker.lower()):
            return True
    
    # 中文标记
    cn_markers = ["[[保留]]", "[保留]", "[[重要]]", "[重要]"]
    for marker in cn_markers:
        if content.strip().startswith(marker):
            return True
    
    return False


def is_error_message(message: Dict) -> bool:
    """
    检测是否为错误消息（tool result 以 error: 或 blocked: 开头）。
    错误消息默认保留，防止压缩丢失关键诊断信息。
    """
    if message.get("role") != "tool":
        return False
    content = message.get("content", "").strip().lower()
    return content.startswith("error:") or content.startswith("blocked:")


def partition_keep_fold(
    messages: List[Dict],
    keep_errors: bool = True,
    keep_user_marked: bool = True
) -> Tuple[List[Dict], List[Dict]]:
    """
    将消息列表分为 keep（保留）和 fold（折叠）两部分。
    
    Keep 策略：
      - 系统消息（role=system）→ keep
      - 用户标记 [[keep]] → keep（如果 keep_user_marked=True）
      - 错误消息（error:/blocked:）→ keep（如果 keep_errors=True）
      - 先前压缩摘要（compaction-summary）→ keep
      - 小规模用户消息（< 1500 tokens）→ keep
      - 其余 → fold
    
    Returns:
        (kept_messages, fold_messages)
    """
    kept = []
    fold = []
    
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        
        # 系统消息永远保留
        if role == "system":
            kept.append(msg)
            continue
        
        # 压缩摘要标记 ← 保留
        if "<compaction-summary>" in content or "<session_compact_summary>" in content:
            kept.append(msg)
            continue
        
        # 用户 [[keep]] 标记
        if keep_user_marked and has_keep_marker(msg):
            kept.append(msg)
            continue
        
        # 错误消息
        if keep_errors and is_error_message(msg):
            kept.append(msg)
            continue
        
        # 小规模用户消息（< 1500 tokens）默认保留
        if role == "user" and estimate_tokens(content) < 1500:
            kept.append(msg)
            continue
        
        # 其余 → 折叠
        fold.append(msg)
    
    return kept, fold
