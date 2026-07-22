# -*- coding: utf-8 -*-
"""
Onyx AI 存储模块 — 命令缓存、聊天记忆、会话记录

从 bin/ai_cmd.py 提取，零功能变更。
"""

import os
import json
import secrets
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

from .config import get_current_lang


# ── 命令缓存 ──

def get_ai_cmd_cache_path(user_home_dir: str) -> str:
    cache_dir = os.path.join(user_home_dir, ".cache", "onyx", "ai")
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir, mode=0o755)
    return os.path.join(cache_dir, "cmd.json")

def save_ai_commands(user_home_dir: str, commands: List[str]) -> None:
    cache_path = get_ai_cmd_cache_path(user_home_dir)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"commands": commands, "triggered_by_ai": True}, f, ensure_ascii=False, indent=2)

def clear_ai_cmd_cache(user_home_dir: str) -> None:
    cache_path = get_ai_cmd_cache_path(user_home_dir)
    if os.path.exists(cache_path):
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"commands": [], "triggered_by_ai": False}, f, ensure_ascii=False, indent=2)


# ── 聊天记忆 ──

def get_chat_json_path(home_dir: str, chat_name: str) -> str:
    chat_dir = os.path.join(home_dir, ".ai_s", "chat")
    os.makedirs(chat_dir, exist_ok=True)
    return os.path.join(chat_dir, f"{chat_name}.json")

def get_current_chat_name(home_dir: str) -> str:
    chat_config_path = os.path.join(home_dir, ".ai_s", "chat.txt")
    if os.path.exists(chat_config_path):
        try:
            with open(chat_config_path, "r", encoding="utf-8") as f:
                name = f.read().strip()
                if name:
                    return name
        except Exception:
            pass
    return "first"

def set_current_chat_name(home_dir: str, name: str) -> None:
    chat_config_path = os.path.join(home_dir, ".ai_s", "chat.txt")
    os.makedirs(os.path.dirname(chat_config_path), exist_ok=True)
    with open(chat_config_path, "w", encoding="utf-8") as f:
        f.write(name)

def load_chat_json(home_dir: str, chat_name: str) -> Dict[str, Any]:
    json_path = get_chat_json_path(home_dir, chat_name)
    if not os.path.exists(json_path):
        return {
            "name": chat_name,
            "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "messages": []
        }
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "name": chat_name,
            "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "messages": []
        }

def save_chat_json(home_dir: str, chat_name: str, chat_data: Dict[str, Any]) -> None:
    """原子写入：先写临时文件，再 rename。崩溃不会损坏海马体。"""
    json_path = get_chat_json_path(home_dir, chat_name)
    tmp_path = json_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(chat_data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, json_path)  # 原子 rename
    except Exception:
        # 清理临时文件
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise

def get_class_retention_days(class_level: str) -> int:
    """根据class等级返回保留天数，-1表示永久保留"""
    try:
        level = int(class_level)
    except (ValueError, TypeError):
        return 7
    
    if level == 1:
        return 7
    elif level == 2:
        return 30
    elif level == 3:
        return 100
    elif level >= 10:
        return -1
    elif level <= 6:
        return 100 + (level - 3) * 50
    elif level <= 9:
        base = 300 + 100
        return base + (level - 7) * 100
    else:
        return -1

def clean_expired_messages(chat_data: Dict[str, Any]) -> Dict[str, Any]:
    """清理过期的消息"""
    now = datetime.now()
    messages = chat_data.get("messages", [])
    cleaned_messages = []
    
    for msg in messages:
        class_level = msg.get("class", "1")
        retention_days = get_class_retention_days(class_level)
        
        if retention_days == -1:
            cleaned_messages.append(msg)
            continue
        
        try:
            msg_time = datetime.strptime(msg["timestamp"], '%Y-%m-%d %H:%M:%S')
            days_passed = (now - msg_time).days
            
            if days_passed <= retention_days:
                cleaned_messages.append(msg)
            else:
                if 7 <= int(class_level) <= 9:
                    truncated_msg = msg.copy()
                    truncated_msg["user_question"] = truncated_msg["user_question"][:100] + "..."
                    truncated_msg["ai_response"] = truncated_msg["ai_response"][:100] + "..."
                    truncated_msg["tag"] = truncated_msg.get("tag", "")[:50] + "..."
                    cleaned_messages.append(truncated_msg)
        except (ValueError, KeyError):
            cleaned_messages.append(msg)
    
    chat_data["messages"] = cleaned_messages
    return chat_data

def append_message_to_chat(home_dir: str, chat_name: str, session_uuid: str, 
                           user_question: str, ai_response: str, tag: str = "", 
                           class_level: str = "1") -> str:
    """追加新消息，返回消息ID"""
    chat_data = load_chat_json(home_dir, chat_name)
    message_id = secrets.token_hex(4)
    new_message = {
        "id": message_id,
        "session_uuid": session_uuid,
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "user_question": user_question[:5000] if user_question else "",
        "ai_response": ai_response[:5000] if ai_response else "",
        "tag": tag,
        "class": class_level
    }
    chat_data["messages"].append(new_message)
    chat_data = clean_expired_messages(chat_data)
    save_chat_json(home_dir, chat_name, chat_data)
    return message_id

def update_message_tag(home_dir: str, chat_name: str, session_uuid: str, tag: str, class_level: str = None) -> bool:
    """更新指定session_uuid的消息tag和class"""
    chat_data = load_chat_json(home_dir, chat_name)
    for msg in reversed(chat_data["messages"]):
        if msg["session_uuid"] == session_uuid:
            msg["tag"] = tag
            if class_level is not None:
                msg["class"] = class_level
            save_chat_json(home_dir, chat_name, chat_data)
            return True
    return False

def get_previous_session_uuid(home_dir: str, chat_name: str, current_session_uuid: str, is_first_interaction: bool) -> Optional[str]:
    """获取上一次的session_uuid"""
    chat_data = load_chat_json(home_dir, chat_name)
    messages = chat_data["messages"]
    
    if not messages:
        return None
    
    if is_first_interaction:
        return messages[-1]["session_uuid"]
    else:
        if len(messages) >= 2:
            return messages[-2]["session_uuid"]
        return None

def list_chat_memories(home_dir: str) -> List[str]:
    chat_dir = os.path.join(home_dir, ".ai_s", "chat")
    memories = []
    if not os.path.exists(chat_dir):
        return memories
    for file in os.listdir(chat_dir):
        if file.endswith(".json"):
            memories.append(file[:-5])
    return sorted(memories)

def create_chat_memory(home_dir: str, name: str) -> bool:
    if not name or not name.strip():
        name = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    json_path = get_chat_json_path(home_dir, name)
    if os.path.exists(json_path):
        return False
    
    chat_data = {
        "name": name,
        "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "messages": []
    }
    save_chat_json(home_dir, name, chat_data)
    return True

def switch_chat_memory(home_dir: str, name: str) -> bool:
    json_path = get_chat_json_path(home_dir, name)
    if not os.path.exists(json_path):
        return False
    
    set_current_chat_name(home_dir, name)
    return True

def load_chat_memory_for_context(home_dir: str, chat_name: str) -> str:
    """加载chat记忆用于AI上下文 — 每条消息用 {id:xxx} 标记，可被 MEMORY 字段按 UUID 精确引用"""
    chat_data = load_chat_json(home_dir, chat_name)
    chat_data = clean_expired_messages(chat_data)
    save_chat_json(home_dir, chat_name, chat_data)
    
    messages = chat_data.get("messages", [])
    
    if not messages:
        return ""
    
    lang = get_current_lang()
    context_lines = []
    is_en = lang == "english"
    header = ("{chat_history_summary} — archived context, each entry tagged with id for MEMORY lookup."
              if is_en else "{chat_history_summary} — 摘要历史，每条带 id 标记，可通过 MEMORY 字段按 UUID 精确查询。")
    context_lines.append(header)
    context_lines.append("")
    
    for msg in messages:
        msg_id = msg.get("id", "?")
        session_uuid = msg.get("session_uuid", "?")
        time_str = msg.get("timestamp", "")
        user_q = msg.get("user_question", "")
        ai_r = msg.get("ai_response", "")
        tag = msg.get("tag", "")
        class_level = msg.get("class", "1")
        
        context_lines.append("{")
        context_lines.append(f"  id: {msg_id}")
        context_lines.append(f"  session: {session_uuid}")
        context_lines.append(f"  time: {time_str}")
        context_lines.append(f"  class: {class_level}")
        context_lines.append(f"  user: {user_q}")
        context_lines.append(f"  ai: {ai_r[:200]}{'...' if len(ai_r) > 200 else ''}")
        if tag:
            context_lines.append(f"  tag: {tag}")
        context_lines.append("}")
        context_lines.append("")
    
    return "\n".join(context_lines)


# ── 会话管理 ──

def get_ai_session_library_dir(home_dir: str) -> str:
    library_dir = os.path.join(home_dir, ".ai_s", "library")
    os.makedirs(library_dir, exist_ok=True)
    return library_dir

def get_latest_ai_session(home_dir: str, session_id: str) -> Tuple[str, str]:
    library_dir = get_ai_session_library_dir(home_dir)
    target_file = os.path.join(library_dir, f"{session_id}.txt")
    if os.path.exists(target_file):
        with open(target_file, "r", encoding="utf-8") as f:
            content = f.read()
        return content, target_file
    
    old_file = os.path.join(home_dir, ".ai_s", f"{session_id}.txt")
    if os.path.exists(old_file):
        try:
            os.makedirs(library_dir, exist_ok=True)
            shutil.move(old_file, target_file)
            return get_latest_ai_session(home_dir, session_id)
        except Exception:
            with open(old_file, "r", encoding="utf-8") as f:
                content = f.read()
            return content, old_file
    
    return "", ""

def load_memory_by_uuid(home_dir: str, memory_uuid: str) -> str:
    library_dir = get_ai_session_library_dir(home_dir)
    memory_path = os.path.join(library_dir, f"{memory_uuid}.txt")
    
    if os.path.exists(memory_path):
        try:
            with open(memory_path, "r", encoding="utf-8") as f:
                content = f.read()
                return content
        except Exception:
            return ""
    return ""

def record_ai_session(home_dir: str, session_id: str, user_question: str, 
                      ai_result: Dict[str, Any], user_answer: str = "", 
                      cmd_results: Dict[str, str] = None, referenced_memory: str = "",
                      native_results: str = "",
                      markup_results: List[Dict] = None) -> None:
    """
    记录 AI 会话到 library。

    Args:
        native_results: 预格式化的字符串（向后兼容）
        markup_results: 标记块执行结果列表（BlockResult.to_dict()），
                        自动格式化为带完整路径+行号+内容的 Markdown
    """
    cmd_results = cmd_results or {}
    library_dir = get_ai_session_library_dir(home_dir)
    record_path = os.path.join(library_dir, f"{session_id}.txt")
    lang = get_current_lang()
    
    first_return = ai_result.get("txt", "") or ""
    strategy = ai_result.get("analysis", "") or ""
    ai_ask = ai_result.get("ask", "") or ""
    # 惰性导入避免循环引用
    from .api import extract_ai_commands
    commands = extract_ai_commands(ai_result)
    answer = ai_result.get("answer", "no")
    tag = ai_result.get("tag", "") or ""
    plan = ai_result.get("plan", "") or ""
    memory_uuid = ai_result.get("memory", "") or ""
    tool_calls = ai_result.get("tool_calls", [])
    
    # ── 合并 native_results：优先用 markup_results 自动格式化 ──
    if markup_results and not native_results:
        native_results = _format_native_results(markup_results)
    
    current_time = datetime.now()
    time_str = current_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    
    md = lang == "english"
    
    content = [
        f"## {'Interaction' if md else '交互记录'} — {time_str}",
        "",
        f"- **{'Session ID' if md else '会话ID'}**: {session_id}",
        f"- **{'Time' if md else '时间'}**: {time_str}",
        "",
    ]
    
    # 用户提问
    content.append(f"### {'Question' if md else '用户提问'}")
    content.append((user_question or "").strip() or (f"*No specific question*" if md else "*无明确提问*"))
    content.append("")
    
    # AI 追问 + 用户回答
    if ai_ask.strip():
        content.append(f"### {'AI Ask' if md else 'AI追问'}")
        content.append(ai_ask.strip())
        content.append("")
        if user_answer.strip():
            content.append(f"**{'User Answer' if md else '用户回答'}**:")
            content.append(user_answer.strip())
            content.append("")
    
    # 标签
    if tag.strip():
        content.append(f"- **{'Tag' if md else '标签'}**: `{tag.strip()}`")
        content.append("")
    
    # 记忆 UUID
    if memory_uuid.strip():
        content.append(f"- **{'Memory UUID' if md else '记忆UUID'}**: `{memory_uuid.strip()}`")
        content.append("")
    
    # 策略分析
    if strategy.strip():
        content.append(f"### {'Analysis' if md else '策略分析'}")
        content.append(strategy.strip())
        content.append("")
    
    # 计划
    if plan.strip():
        content.append(f"### {'Plan' if md else '计划'}")
        content.append(plan.strip())
        content.append("")
    
    # AI 文本回答
    content.append(f"### {'AI Response' if md else 'AI回答'}")
    content.append(first_return.strip() if first_return else (f"*No text response*" if md else "*无文本回答*"))
    content.append("")
    
    # 引用记忆
    if referenced_memory:
        content.append(f"- **{'Referenced Memory' if md else '引用记忆'}**: `{referenced_memory}`")
        content.append("")
    
    # 命令执行
    for idx, cmd in enumerate(commands, 1):
        cmd_result = cmd_results.get(cmd, "Not executed or execution failed" if md else "未执行或执行失败")
        if cmd_result and "STDERR:" in cmd_result and "STDOUT:" in cmd_result:
            stdout_part = cmd_result.split("STDERR:")[0].replace("STDOUT:", "").strip()
            stderr_part = cmd_result.split("STDERR:")[1].strip()
            filtered_result = []
            if stdout_part:
                filtered_result.append(f"**{'Output' if md else '输出'}**:\n```\n{stdout_part}\n```")
            if stderr_part:
                filtered_result.append(f"**{'Error' if md else '错误'}**:\n```\n{stderr_part}\n```")
            cmd_result = "\n".join(filtered_result) if filtered_result else (f"*{('No output' if md else '无输出')}*")
        content.append(f"#### {'Command' if md else '命令'} #{idx}: `{cmd}`")
        content.append(cmd_result or f"*{('No output' if md else '无输出')}*")
        content.append("")
    
    # 工具调用（完整路径，不截断）
    if tool_calls:
        content.append(f"### {'Tool Calls' if md else '工具调用'}")
        for tc in tool_calls:
            if isinstance(tc, dict):
                tc_name = tc.get("name", "?")
                tc_params = tc.get("params_str", "")
                _param_summary = ""
                if tc_params:
                    try:
                        _parsed = json.loads(tc_params)
                        _key_keys = ["path", "pattern", "name", "query", "url", "prompt", "file_path", "source", "destination"]
                        _parts = []
                        for _k in _key_keys:
                            _v = _parsed.get(_k, "")
                            if _v:
                                # 完整路径不截断（仅限制单参数最长 500 字符防注入）
                                _val_str = str(_v)[:500]
                                _parts.append(f"{_k}={_val_str}")
                        if _parts:
                            _param_summary = " (" + ", ".join(_parts) + ")"
                    except (json.JSONDecodeError, ValueError):
                        _param_summary = f" params={tc_params[:200]}"
                content.append(f"- `{tc_name}{_param_summary}`")
            else:
                content.append(f"- `{str(tc)[:200]}`")
        content.append("")
    
    # 原生标记语言操作记录（VIEW/EDIT/WRITE 等）
    if native_results:
        content.append(f"### {'File Operations' if md else '文件操作记录'}")
        content.append(native_results)
        content.append("")
    
    # 写入文件（Markdown 格式，append → 崩溃安全）
    file_exists = os.path.exists(record_path)
    file_has_content = file_exists and os.path.getsize(record_path) > 0
    with open(record_path, "a", encoding="utf-8") as f:
        if file_has_content:
            f.write(f"\n\n---\n\n")
        f.write("\n".join(content).rstrip("\n"))
        f.flush()
        os.fsync(f.fileno())
    
    # ── 触发记忆压缩（如需要）──
    # 每次记录后惰性检查，不阻塞主流程
    try:
        maybe_compact_library(home_dir)
    except Exception:
        pass  # 压缩失败不影响主流程


# ── 文件操作结果格式化 ──

def _format_native_results(markup_results: List[Dict]) -> str:
    """
    将标记块执行结果列表格式化为结构化 Markdown，
    每个操作包含：完整绝对路径、行号范围、操作类型、实际内容。
    
    格式:
        #### 📖 VIEW — `/absolute/path` (lines 10-30 of 150)
        ```python
        10  │ code...
        ```
        
        #### ✏️ EDIT — `/absolute/path`
        - **Search:** `old`
        - **Replace:** `new`
    """
    if not markup_results:
        return ""
    
    lines = []
    
    for r in markup_results:
        op_type = r.get("type", "unknown")
        path = r.get("path", "")
        abs_path = os.path.abspath(path) if path else ""
        success = r.get("success", False)
        icon = "✅" if success else "❌"
        message = r.get("message", "")
        
        if op_type == "view":
            total_lines = r.get("total_lines", "")
            start_line = r.get("start_line")
            end_line = r.get("end_line")
            search_kw = r.get("search", "")
            
            if search_kw:
                # 搜索模式：明确标注搜索关键词
                view_label = f"SEARCH(\"{search_kw}\")"
                range_str = f" (matched in {total_lines} lines)" if total_lines else ""
            elif start_line and end_line:
                view_label = "RANGE"
                range_str = f" (lines {start_line}-{end_line} of {total_lines})" if total_lines else f" (lines {start_line}-{end_line})"
            elif start_line:
                view_label = "LINE"
                range_str = f" (line {start_line} of {total_lines})" if total_lines else f" (line {start_line})"
            else:
                view_label = "FULL"
                range_str = f" (full file, {total_lines} lines)" if total_lines else ""
            
            lines.append(f"#### {icon} 📖 VIEW:{view_label} — `{abs_path}`{range_str}")
            
            # 带行号的完整内容（AI 可直接使用，无需重读文件）
            content = r.get("content", "") or r.get("raw_content", "")
            if content:
                # 尝试检测语言用于语法高亮标记
                ext = os.path.splitext(path)[1].lstrip(".") if path else ""
                lang_map = {"py": "python", "js": "javascript", "ts": "typescript", "go": "go",
                            "rs": "rust", "java": "java", "cpp": "cpp", "c": "c", "h": "c",
                            "sh": "bash", "json": "json", "yaml": "yaml", "yml": "yaml",
                            "toml": "toml", "md": "markdown", "html": "html", "css": "css"}
                lang = lang_map.get(ext, "")
                lines.append(f"```{lang}")
                lines.append(content[:5000])  # 单文件最多 5000 字符
                if len(content) > 5000:
                    lines.append(f"... (truncated, {len(content)} chars total)")
                lines.append("```")
            else:
                lines.append(f"  {message}")
        
        elif op_type in ("edit", "edit_range"):
            search = r.get("search", "")
            replace = r.get("replace", "") or r.get("content", "")
            old_content = r.get("old_content", "")
            
            lines.append(f"#### {icon} ✏️ EDIT — `{abs_path}`")
            if search:
                search_display = search[:1000]
                lines.append(f"- **Search:**")
                lines.append(f"  ```")
                lines.append(search_display)
                if len(search) > 1000:
                    lines.append(f"  ... (truncated, {len(search)} chars total)")
                lines.append(f"  ```")
            if replace:
                replace_display = replace[:1000]
                lines.append(f"- **Replace:**")
                lines.append(f"  ```")
                lines.append(replace_display)
                if len(replace) > 1000:
                    lines.append(f"  ... (truncated, {len(replace)} chars total)")
                lines.append(f"  ```")
            if old_content and not search:
                lines.append(f"- **Old content:** `{old_content[:200]}`")
            lines.append(f"  {message}")
        
        elif op_type == "write":
            content = r.get("content", "")
            lines.append(f"#### {icon} 📝 WRITE — `{abs_path}`")
            lines.append(f"  {message}")
            if content:
                ext = os.path.splitext(path)[1].lstrip(".") if path else ""
                lang_map = {"py": "python", "js": "javascript", "ts": "typescript", "go": "go",
                            "rs": "rust", "java": "java", "cpp": "cpp", "c": "c", "sh": "bash",
                            "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml",
                            "md": "markdown", "html": "html", "css": "css"}
                lang = lang_map.get(ext, "")
                lines.append(f"```{lang}")
                lines.append(content[:3000])
                if len(content) > 3000:
                    lines.append(f"... (truncated, {len(content)} chars total)")
                lines.append("```")
        
        elif op_type == "delete" or op_type == "delete_by_content":
            old_content = r.get("old_content", "")
            lines.append(f"#### {icon} 🗑️ DELETE — `{abs_path}`")
            if old_content:
                lines.append(f"- **Deleted content:**")
                lines.append(f"  ```")
                lines.append(str(old_content)[:500])
                lines.append(f"  ```")
            lines.append(f"  {message}")
        
        elif op_type == "append":
            content = r.get("content", "")
            lines.append(f"#### {icon} ➕ APPEND — `{abs_path}`")
            lines.append(f"  {message}")
            if content:
                lines.append(f"```")
                lines.append(content[:1000])
                if len(content) > 1000:
                    lines.append(f"... (truncated, {len(content)} chars total)")
                lines.append("```")
        
        elif op_type == "insert":
            content = r.get("content", "")
            line_no = r.get("line", "")
            lines.append(f"#### {icon} 📌 INSERT — `{abs_path}` (after line {line_no})")
            lines.append(f"  {message}")
            if content:
                lines.append(f"```")
                lines.append(content[:1000])
                if len(content) > 1000:
                    lines.append(f"... (truncated, {len(content)} chars total)")
                lines.append("```")
        
        elif op_type == "batch":
            sub_blocks = r.get("blocks", [])
            lines.append(f"#### {icon} 📦 BATCH — {len(sub_blocks)} operations")
            lines.append(f"  {message}")
        
        elif op_type == "replace_all":
            lines.append(f"#### {icon} 🔄 REPLACE_ALL — `{r.get('glob', '')}`")
            lines.append(f"  {message}")
        
        else:
            lines.append(f"#### {icon} `{op_type}` — `{abs_path}`")
            lines.append(f"  {message}")
        
        lines.append("")
    
    return "\n".join(lines)


# ── 海马体索引（替代 LIBRARY.md）──
# 海马体本身就是 library 的索引层：每条消息存 {id, session_uuid, question, response, tag, class}
# 通过 session_uuid 指向 library 完整记录，无需额外索引文件。

def _get_hippocampus_index(home_dir: str, chat_name: str = None) -> List[Dict]:
    """
    读取海马体作为 library 索引。
    海马体消息已包含 id/session_uuid/question/response/tag/class，
    是天然的结构化索引。
    """
    if chat_name is None:
        chat_name = get_current_chat_name(home_dir)
    data = load_chat_json(home_dir, chat_name)
    return data.get("messages", [])


def load_hippocampus_index(home_dir: str, chat_name: str = None) -> str:
    """
    加载海马体索引文本（用于 AI 上下文）。
    比 LIBRARY.md 更好：结构化字段，AI 可按 uuid 精确引用。
    """
    messages = _get_hippocampus_index(home_dir, chat_name)
    if not messages:
        return ""
    
    # 过滤已过期（class=0 表示已归档）
    active = [m for m in messages if m.get("class", "1") != "0"]
    if not active:
        return ""
    
    lines = ["# Hippocampus Index (cache-stable)"]
    for msg in active[-30:]:  # 最近 30 条
        mid = msg.get("id", "?")
        sid = msg.get("session_uuid", "?")
        tag = msg.get("tag", "")
        cls = msg.get("class", "1")
        q = (msg.get("user_question", "") or "")[:60]
        tag_str = f" [{tag}]" if tag else ""
        cls_str = f" (class={cls})" if cls != "1" else ""
        lines.append(f"- [{mid}]({sid}.txt){tag_str}{cls_str} — {q}")
    
    return "\n".join(lines)


def estimate_session_tokens(markdown_content: str) -> int:
    """粗略估算 Markdown 内容的 token 数（用于压缩阈值判断）"""
    return max(len(markdown_content) // 4, len(markdown_content.encode("utf-8")) // 3)


def get_library_total_tokens(home_dir: str) -> int:
    """估算 library 总 token 数"""
    library_dir = get_ai_session_library_dir(home_dir)
    total = 0
    if not os.path.exists(library_dir):
        return 0
    for fname in os.listdir(library_dir):
        if fname.endswith(".txt"):
            fpath = os.path.join(library_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    total += estimate_session_tokens(f.read())
            except Exception:
                pass
    return total


# ── 记忆压缩 ──

def maybe_compact_library(home_dir: str) -> Optional[str]:
    """
    检查 library 是否需要压缩，需要则执行 Trident 压缩。
    
    压缩时：
      1. 加载所有 library 条目内容
      2. 运行 Trident 三阶段压缩
      3. 将被压缩的旧条目移到 .archive/
      4. 写入压缩摘要为新条目
      5. 重建 LIBRARY.md 索引
    
    Returns:
        压缩摘要文本，如果不需要压缩则返回 None
    """
    try:
        from .memory_compact import (
            CompactConfig, should_compact, compact_library_entries, estimate_tokens,
            summarize_messages, merge_compact_summaries, extract_existing_compacted_summary,
            format_compact_summary, get_compact_continuation_message,
        )
    except ImportError:
        return None
    
    library_dir = get_ai_session_library_dir(home_dir)
    if not os.path.exists(library_dir):
        return None
    
    # 收集所有条目的完整信息
    entries = []
    for fname in sorted(os.listdir(library_dir)):
        if not fname.endswith(".txt"):
            continue
        fpath = os.path.join(library_dir, fname)
        session_id = fname[:-4]
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            time_str = session_id
            entries.append({
                "session_id": session_id,
                "content": content,
                "time": time_str,
                "path": fpath,
            })
        except Exception:
            continue
    
    if not entries:
        return None
    
    config = CompactConfig()
    if not should_compact(entries, config):
        return None
    
    # ── 检测已有压缩摘要（重压缩时合并）──
    existing_summary = extract_existing_compacted_summary(entries)
    
    # 执行 Trident 三阶段压缩
    result = compact_library_entries(entries, config)
    
    # ── 使用完整 Claw Code summary 格式 ──
    # 对被压缩的条目生成完整 <summary>（含 Scope/Tools/KeyFiles/PendingWork/KeyTimeline）
    removed_entries = [e for e in entries 
                       if not any(ke.get("session_id") == e.get("session_id") 
                                  for ke in result.entries)]
    if removed_entries:
        raw_summary = summarize_messages(removed_entries)
        # 重压缩时合并已有摘要
        if existing_summary:
            raw_summary = merge_compact_summaries(existing_summary, raw_summary)
        result.summary = raw_summary
    
    if not result.summary:
        return None
    
    # 归档被压缩的旧条目
    archive_dir = os.path.join(library_dir, ".archive")
    os.makedirs(archive_dir, exist_ok=True)
    
    for entry in entries:
        sid = entry.get("session_id", "")
        fpath = entry.get("path", "")
        if not fpath or not os.path.exists(fpath):
            continue
        
        kept = any(e.get("session_id") == sid for e in result.entries)
        if not kept and not entry.get("_compacted"):
            archive_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{sid}.txt"
            archive_path = os.path.join(archive_dir, archive_name)
            try:
                shutil.move(fpath, archive_path)
            except Exception:
                pass
    
    # ── 同步海马体：归档的 session 标记 class=0 ──
    _sync_hippocampus_after_compact(home_dir, removed_entries)
    
    # ── 海马体自身压缩（如超 50 条活跃消息）──
    try:
        compact_hippocampus(home_dir, max_messages=50)
    except Exception:
        pass
    
    # ── 写入可恢复会话消息（Claw Code 格式）──
    compact_session_id = f"compact_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    compact_path = os.path.join(library_dir, f"{compact_session_id}.txt")
    
    continuation_msg = get_compact_continuation_message(result.summary)
    compact_content = f"""## Session Compaction — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{continuation_msg}

---
*Compacted from {result.original_count} entries → {result.final_count}.
*Superseded: {result.superseded_count}, Collapsed: {result.messages_collapsed}, Clustered: {result.messages_clustered}.
*Est. tokens saved: ~{result.tokens_saved_estimate}.
"""
    
    try:
        with open(compact_path, "w", encoding="utf-8") as f:
            f.write(compact_content)
    except Exception:
        pass
    



# ── 手动压缩触发 ──

def force_compact_library(home_dir: str) -> str:
    """
    手动触发 library 压缩（不受阈值限制，强制执行）。
    可通过 AI 工具或用户命令调用。
    
    Returns:
        压缩结果报告
    """
    from .memory_compact import CompactConfig, compact_library_entries
    from .memory_compact import summarize_messages, extract_existing_compacted_summary
    from .memory_compact import merge_compact_summaries, get_compact_continuation_message
    
    library_dir = get_ai_session_library_dir(home_dir)
    if not os.path.exists(library_dir):
        return "Library directory not found."
    
    # 收集所有条目
    entries = []
    for fname in sorted(os.listdir(library_dir)):
        if not fname.endswith(".txt"):
            continue
        fpath = os.path.join(library_dir, fname)
        session_id = fname[:-4]
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            entries.append({
                "session_id": session_id,
                "content": content,
                "time": session_id,
                "path": fpath,
            })
        except Exception:
            continue
    
    if not entries:
        return "No entries to compact."
    
    # 强制压缩（preserve_recent=1，确保即使少量条目也压缩）
    config = CompactConfig(preserve_recent=1, max_entries=1, max_tokens=1)
    existing_summary = extract_existing_compacted_summary(entries)
    
    result = compact_library_entries(entries, config)
    
    removed_entries = [e for e in entries
                       if not any(ke.get("session_id") == e.get("session_id")
                                  for ke in result.entries)]
    
    if not removed_entries:
        return "Nothing to compact — all entries are recent or unique."
    
    # 生成完整 summary
    raw_summary = summarize_messages(removed_entries)
    if existing_summary:
        raw_summary = merge_compact_summaries(existing_summary, raw_summary)
    
    # 归档旧条目
    archive_dir = os.path.join(library_dir, ".archive")
    os.makedirs(archive_dir, exist_ok=True)
    
    for entry in removed_entries:
        fpath = entry.get("path", "")
        sid = entry.get("session_id", "")
        if fpath and os.path.exists(fpath):
            archive_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{sid}.txt"
            try:
                shutil.move(fpath, os.path.join(archive_dir, archive_name))
            except Exception:
                pass
    
    # 写入压缩条目
    compact_session_id = f"compact_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    compact_path = os.path.join(library_dir, f"{compact_session_id}.txt")
    
    continuation_msg = get_compact_continuation_message(raw_summary)
    compact_content = f"""## Manual Compaction — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{continuation_msg}

---
*Force-compacted: {len(removed_entries)} entries → summary.
*Superseded: {result.superseded_count}, Collapsed: {result.messages_collapsed}, Clustered: {result.messages_clustered}.
"""
    
    try:
        with open(compact_path, "w", encoding="utf-8") as f:
            f.write(compact_content)
    except Exception:
        pass
    
    
    return (
        f"✅ Force compaction complete.\n"
        f"  Removed: {len(removed_entries)} entries → archive\n"
        f"  Superseded: {result.superseded_count}\n"
        f"  Collapsed: {result.messages_collapsed} messages in {result.collapsed_chains} chains\n"
        f"  Clustered: {result.messages_clustered} messages in {result.clusters_found} groups\n"
        f"  Final: {result.final_count} entries (was {result.original_count})\n"
        f"  Tokens saved: ~{result.tokens_saved_estimate}"
    )


def get_compaction_stats(home_dir: str) -> str:
    """
    返回 library 压缩状态报告。
    """
    library_dir = get_ai_session_library_dir(home_dir)
    if not os.path.exists(library_dir):
        return "Library not found."
    
    entries = [f for f in os.listdir(library_dir) if f.endswith(".txt")]
    archive_dir = os.path.join(library_dir, ".archive")
    archived = len([f for f in os.listdir(archive_dir) if f.endswith(".txt")]) \
        if os.path.exists(archive_dir) else 0
    
    total_tokens = get_library_total_tokens(home_dir)
    compacted_entries = sum(1 for f in entries if f.startswith("compact_") or f.startswith("collapsed_") or f.startswith("clustered_"))
    
    return (
        f"Library stats:\n"
        f"  Active entries: {len(entries)} ({compacted_entries} compacted)\n"
        f"  Archived: {archived}\n"
        f"  Est. tokens: ~{total_tokens}\n"
        f"  Auto-compact triggers at: >20 entries or >10K tokens"
    )


# ── remember / forget / recall 工具（取自 Reasonix 思路）──

def mark_session_important(home_dir: str, session_id: str, chat_name: str = None) -> str:
    """
    标记会话为重要（Reasonix `remember` 等价物）。
    1. Library 文件插入 frontmatter importance:high
    2. 海马体对应消息 class=10（永久保留，不低于 class 10 不会被压缩）
    
    Returns:
        操作结果
    """
    library_dir = get_ai_session_library_dir(home_dir)
    file_path = os.path.join(library_dir, f"{session_id}.txt")
    
    parts = []
    
    # 1. Library frontmatter
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            if not content.startswith("---"):
                importance_block = (
                    "---\n"
                    f"importance: high\n"
                    f"marked_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    "---\n\n"
                )
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(importance_block + content)
            else:
                parts_existing = content.split("---", 2)
                if len(parts_existing) >= 3:
                    fm = parts_existing[1]
                    if "importance:" not in fm:
                        fm += f"\nimportance: high\nmarked_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    parts_existing[1] = fm
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write("---".join(parts_existing))
            parts.append("Library marked importance:high")
        except Exception as e:
            parts.append(f"Library mark failed: {e}")
    else:
        parts.append(f"Library file not found: {session_id}.txt")
    
    # 2. 海马体 class → 10（永久保留）
    try:
        if chat_name is None:
            chat_name = get_current_chat_name(home_dir)
        chat_data = load_chat_json(home_dir, chat_name)
        found = False
        for msg in chat_data.get("messages", []):
            if msg.get("session_uuid") == session_id:
                current = int(msg.get("class", "1"))
                msg["class"] = str(max(current, 10))  # 至少 class 10
                found = True
        if found:
            save_chat_json(home_dir, chat_name, chat_data)
            parts.append("Hippocampus class→10 (permanent)")
        else:
            parts.append("Hippocampus: session not in index")
    except Exception as e:
        parts.append(f"Hippocampus update failed: {e}")
    
    return "✅ " + "; ".join(parts)


def archive_session(home_dir: str, session_id: str, chat_name: str = None) -> str:
    """
    归档会话（Reasonix `forget` 等价物）。
    1. Library 文件移到 .archive/
    2. 海马体对应消息 class=0（立即过期，从索引消失）
    
    Returns:
        操作结果
    """
    library_dir = get_ai_session_library_dir(home_dir)
    file_path = os.path.join(library_dir, f"{session_id}.txt")
    
    parts = []
    
    # 1. 归档 library 文件
    if os.path.exists(file_path):
        archive_dir = os.path.join(library_dir, ".archive")
        os.makedirs(archive_dir, exist_ok=True)
        archive_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{session_id}.txt"
        archive_path = os.path.join(archive_dir, archive_name)
        try:
            shutil.move(file_path, archive_path)
            parts.append(f"Library archived → {archive_name}")
        except Exception as e:
            parts.append(f"Library archive failed: {e}")
    else:
        parts.append(f"Library file not found: {session_id}.txt")
    
    # 2. 海马体标记 class=0
    try:
        if chat_name is None:
            chat_name = get_current_chat_name(home_dir)
        chat_data = load_chat_json(home_dir, chat_name)
        found = False
        for msg in chat_data.get("messages", []):
            if msg.get("session_uuid") == session_id:
                msg["class"] = "0"
                found = True
        if found:
            save_chat_json(home_dir, chat_name, chat_data)
            parts.append("Hippocampus marked class=0")
        else:
            parts.append("Hippocampus: session not found in index")
    except Exception as e:
        parts.append(f"Hippocampus update failed: {e}")
    
    return "✅ " + "; ".join(parts)


def search_library(home_dir: str, query: str, limit: int = 8, chat_name: str = None) -> str:
    """
    搜索海马体索引（Reasonix `recall` 等价物）。
    
    直接搜索海马体的 {question, response, tag} 字段，比搜索 library 纯文本文件
    更快更准——海马体已是结构化索引。
    
    Args:
        query: 搜索关键词
        limit: 返回结果数（默认 8，最大 20）
    """
    import math
    from collections import Counter
    
    limit = max(1, min(limit, 20))
    
    if chat_name is None:
        chat_name = get_current_chat_name(home_dir)
    
    messages = _get_hippocampus_index(home_dir, chat_name)
    if not messages:
        return "Hippocampus is empty."
    
    # 过滤已归档
    active = [m for m in messages if m.get("class", "1") != "0"]
    if not active:
        return "No active entries in hippocampus."
    
    # ── BM25 在海马体消息上搜索 ──
    def tokenize(text: str) -> List[str]:
        import re as _re
        return _re.findall(r'[a-zA-Z0-9_]+|[\u4e00-\u9fff]', text.lower())
    
    k1, b_val = 1.5, 0.75
    
    # 构建文档（每条海马体消息 = 一个文档）
    docs = []
    for msg in active:
        text = f"{msg.get('user_question', '')} {msg.get('ai_response', '')} {msg.get('tag', '')}"
        docs.append({
            "msg": msg,
            "tokens": tokenize(text),
        })
    
    N = len(docs)
    query_tokens = tokenize(query)
    if not query_tokens:
        return f"No valid query terms in: {query}"
    
    # 文档频率
    df = Counter()
    for doc in docs:
        for term in set(doc["tokens"]):
            df[term] += 1
    
    avgdl = sum(len(d["tokens"]) for d in docs) / N if N > 0 else 0
    
    # BM25 评分
    results = []
    for doc in docs:
        score = 0.0
        doc_len = len(doc["tokens"])
        term_counts = Counter(doc["tokens"])
        for term in query_tokens:
            if term not in term_counts:
                continue
            tf = term_counts[term]
            idf = math.log(1 + (N - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b_val + b_val * doc_len / avgdl))
        if score > 0:
            m = doc["msg"]
            results.append({
                "id": m.get("id", "?"),
                "session_id": m.get("session_uuid", "?"),
                "question": (m.get("user_question", "") or "")[:120],
                "tag": m.get("tag", ""),
                "class": m.get("class", "1"),
                "score": round(score, 3),
            })
    
    results.sort(key=lambda r: r["score"], reverse=True)
    results = results[:limit]
    
    if not results:
        return f'No results for "{query}". Try different terms.'
    
    lines = [f'Search results for "{query}" (hippocampus):']
    for i, r in enumerate(results, 1):
        tag_str = f" [{r['tag']}]" if r['tag'] else ""
        cls_str = f" class={r['class']}" if r['class'] != "1" else ""
        lines.append(
            f"\n{i}. score={r['score']} id={r['id']}{tag_str}{cls_str}\n"
            f"   session: {r['session_id']}\n"
            f"   {r['question']}"
        )
    lines.append("\nUse load_memory_by_uuid(session_id) to read full library entry.")
    
    return "\n".join(lines)


def _sync_hippocampus_after_compact(home_dir: str, removed_entries: List[Dict]) -> None:
    """
    Library 压缩后同步海马体：将被归档的 session 对应的海马体消息标记 class=0。
    """
    if not removed_entries:
        return
    try:
        chat_name = get_current_chat_name(home_dir)
        chat_data = load_chat_json(home_dir, chat_name)
        removed_ids = {e.get("session_id", "") for e in removed_entries if e.get("session_id")}
        
        changed = False
        for msg in chat_data.get("messages", []):
            if msg.get("session_uuid") in removed_ids:
                msg["class"] = "0"
                changed = True
        
        if changed:
            save_chat_json(home_dir, chat_name, chat_data)
    except Exception:
        pass  # 海马体同步失败不影响主流程


def compact_hippocampus(home_dir: str, chat_name: str = None, max_messages: int = 50) -> str:
    """
    海马体消息压缩：当活跃消息 > max_messages 时，将最旧的 N 条合并为一条摘要。
    
    Returns:
        压缩结果描述
    """
    if chat_name is None:
        chat_name = get_current_chat_name(home_dir)
    
    chat_data = load_chat_json(home_dir, chat_name)
    messages = chat_data.get("messages", [])
    active = [m for m in messages if m.get("class", "1") != "0"]
    
    if len(active) <= max_messages:
        return f"Hippocampus: {len(active)} active messages (threshold: {max_messages}), no compaction needed."
    
    # 最旧的超出部分
    overflow = len(active) - max_messages
    to_compact = active[:overflow]
    
    # 生成摘要
    topics = []
    for msg in to_compact:
        q = (msg.get("user_question", "") or "")[:60]
        if q:
            topics.append(q)
    
    summary_entry = {
        "id": f"compacted_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "session_uuid": f"compacted_{len(to_compact)}",
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "user_question": f"[Compacted {len(to_compact)} messages]",
        "ai_response": " | ".join(topics[:10]),
        "tag": "hippocampus-compaction",
        "class": "5",
    }
    
    # 重建消息列表：压缩摘要 + 保留的
    new_messages = [summary_entry] + active[overflow:]
    # 保留已归档的
    archived = [m for m in messages if m.get("class", "1") == "0"]
    chat_data["messages"] = new_messages + archived
    save_chat_json(home_dir, chat_name, chat_data)
    
    return f"Hippocampus compacted: {len(to_compact)} → 1 summary ({len(active) - overflow} remaining)."


def list_hippocampus(home_dir: str, chat_name: str = None, 
                     filter_type: str = None, limit: int = 30) -> str:
    """
    列出海马体活跃记忆（Reasonix `memory list` 等价物）。
    
    Args:
        filter_type: 可选过滤 class 等级（如 "10" 只列永久保留的）
        limit: 最大返回条数
    """
    if chat_name is None:
        chat_name = get_current_chat_name(home_dir)
    
    messages = _get_hippocampus_index(home_dir, chat_name)
    active = [m for m in messages if m.get("class", "1") != "0"]
    
    if filter_type:
        active = [m for m in active if m.get("class") == filter_type]
    
    if not active:
        return "No active memories."
    
    active = active[-limit:]
    
    lines = [f"Active memories ({len(active)}):"]
    for msg in active:
        mid = msg.get("id", "?")
        sid = msg.get("session_uuid", "?")
        tag = f" [{msg.get('tag', '')}]" if msg.get("tag") else ""
        cls = msg.get("class", "1")
        q = (msg.get("user_question", "") or "")[:80]
        lines.append(f"- [{mid}]({sid}.txt){tag} class={cls} — {q}")
    
    return "\n".join(lines)
