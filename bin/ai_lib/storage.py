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
    json_path = get_chat_json_path(home_dir, chat_name)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(chat_data, f, ensure_ascii=False, indent=2)

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
                      native_results: str = "") -> None:
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
    
    # 工具调用
    if tool_calls:
        content.append(f"### {'Tool Calls' if md else '工具调用'}")
        for tc in tool_calls:
            if isinstance(tc, dict):
                tc_name = tc.get("name", "?")
                content.append(f"- `{tc_name}`")
            else:
                content.append(f"- `{str(tc)[:80]}`")
        content.append("")
    
    # 原生标记语言操作记录（VIEW/EDIT/WRITE 等）
    if native_results:
        content.append(f"### {'File Operations' if md else '文件操作记录'}")
        content.append(native_results)
        content.append("")
    
    # 写入文件（Markdown 格式）
    file_exists = os.path.exists(record_path)
    file_has_content = file_exists and os.path.getsize(record_path) > 0
    with open(record_path, "a", encoding="utf-8") as f:
        if file_has_content:
            f.write(f"\n\n---\n\n")
        f.write("\n".join(content).rstrip("\n"))
