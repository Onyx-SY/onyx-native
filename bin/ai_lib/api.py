# -*- coding: utf-8 -*-
"""
Onyx AI API 调用模块 — SSE 流式调用、结果处理、记忆上下文

从 bin/ai_cmd.py 提取，零功能变更。
"""

import os
import json
import time
import hashlib
import platform
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable, Tuple

import requests
from rich.console import Console
console = Console()

from .config import (
    get_current_lang, get_prompt_text, load_key_conf,
    _SUPPORTED_PLATFORMS, ROOT_DIR, USER,

)
from .i18n import _ as _i18n  # 双语文本（中英）
from .parsers import parse_sse_structured_response
from .storage import (
    load_chat_memory_for_context, get_previous_session_uuid,
    load_memory_by_uuid, get_latest_ai_session,
)
from .mcp_state import _MCP_DEBUG_START
from .mcp_state import _mcp_debug as _mcp_debug_fn

# ── 当前活跃 HTTP 响应（用于 Ctrl+C 强制关闭）──
_ACTIVE_RESPONSE = None

# ── 持久记忆缓存（模块级，避免每轮读盘）──
_ONYX_AI_PROMPT_CACHE: Optional[Tuple[str, float]] = None  # (content, mtime)

# ── 缓存诊断持久化状态（模块级，按 session_id 隔离）──
# 替换原 threading.local() 临时对象，确保 prev_shape/stats 跨 turn 正确累积
_CACHE_DIAG_STATE: Dict[str, Dict[str, Any]] = {}


def _get_cache_diag_slot(session_id: str) -> Dict[str, Any]:
    """获取或创建当前 session 的缓存诊断槽位。"""
    key = session_id or "_default"
    slot = _CACHE_DIAG_STATE.get(key)
    if slot is None:
        slot = {"prev_shape": None, "stats": None, "rewrite_version": 0}
        _CACHE_DIAG_STATE[key] = slot
    return slot


def clear_cache_diag_state(session_id: str = "") -> None:
    """会话结束时清理缓存诊断状态，避免长期运行的进程无限增长。"""
    key = session_id or "_default"
    _CACHE_DIAG_STATE.pop(key, None)


def bump_rewrite_version(session_id: str = "") -> None:
    """压缩/对话重写后调用，让 cache_diagnostics 正确归因缓存断裂。"""
    slot = _get_cache_diag_slot(session_id)
    slot["rewrite_version"] = slot.get("rewrite_version", 0) + 1


# ── 载荷哈希追踪（模块级，按 session_id 隔离）──
# 用于诊断 DeepSeek 前缀缓存行为：追踪每轮 API 调用的实际 JSON 载荷字节序列，
# 计算与上一轮的字节级前缀匹配率，帮助判断缓存命中率是否符合预期。
_PAYLOAD_HASH_STATE: Dict[str, Dict[str, Any]] = {}


def _track_payload_hash(session_id: str, payload: dict, deb_dir: str = "") -> Dict[str, Any]:
    """计算当前载荷与上一轮的字节级前缀匹配率，返回诊断信息。

    返回 dict 包含：match_pct（匹配百分比）、match_bytes、total_bytes、
    payload_hash[:16]、round_number。
    无上一轮数据时 match_pct 为 None。
    """
    key = session_id or "_default"
    slot = _PAYLOAD_HASH_STATE.get(key)
    if slot is None:
        slot = {"prev_hash": None, "prev_bytes": None, "round": 0}
        _PAYLOAD_HASH_STATE[key] = slot

    slot["round"] += 1
    _payload_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=False).encode('utf-8')
    _payload_hash = hashlib.sha256(_payload_bytes).hexdigest()[:16]
    _prev_bytes = slot.get("prev_bytes")

    info = {
        "round": slot["round"],
        "payload_hash": _payload_hash,
        "total_bytes": len(_payload_bytes),
        "match_bytes": 0,
        "match_pct": None,
    }

    if _prev_bytes is not None:
        # 逐字节比较，找到第一个差异位置
        for i, (a, b) in enumerate(zip(_payload_bytes, _prev_bytes)):
            if a == b:
                info["match_bytes"] = i + 1
            else:
                break
        if info["match_bytes"] == 0 and _payload_bytes == _prev_bytes:
            info["match_bytes"] = len(_payload_bytes)
        info["match_pct"] = (info["match_bytes"] / max(len(_payload_bytes), 1)) * 100

        # 写入调试文件供离线 diff 分析
        if deb_dir:
            try:
                _rstr = f"round_{slot['round']:03d}"
                _pfile = os.path.join(deb_dir, f"payload_{_rstr}.json")
                with open(_pfile, "w", encoding="utf-8") as _f:
                    _f.write(_payload_bytes.decode('utf-8'))
                info["payload_file"] = _pfile
            except Exception:
                pass

    # 更新状态
    slot["prev_hash"] = _payload_hash
    slot["prev_bytes"] = _payload_bytes

    return info


def clear_payload_hash_state(session_id: str = "") -> None:
    """清理会话的载荷哈希状态。"""
    key = session_id or "_default"
    _PAYLOAD_HASH_STATE.pop(key, None)


from . import mcp_state as _mcp_state


def _convert_tools_for_anthropic(openai_tools: list) -> list:
    """将 OpenAI function calling 格式转换为 Anthropic tool use 格式。"""
    result = []
    for t in openai_tools:
        func = t.get("function", t)
        result.append({
            "name": func.get("name", ""),
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {}),
        })
    return result


def call_ai_api_sse(question: str = "", type: Optional[str] = None,
                    new_key: Optional[str] = None,
                    debug_mode: bool = False, onyx_module=None,
                    mode: str = "normal", times: int = 1,
                    ai_tools_prompt: str = "",
                    on_content: Optional[Callable[[str], None]] = None,
                    on_tool_call: Optional[Callable[[str], None]] = None,
                    on_reasoning: Optional[Callable[[str], None]] = None,
                    user_home_dir: str = None,
                    tools: Optional[List[Dict]] = None,
                    messages: Optional[List[Dict]] = None,
                    memory_block: str = "",
                    session_id: str = "",
                    model_override: str = "",
                    platform_override: str = "") -> Dict[str, Any]:
    """
    memory_block: 缓存稳定前缀（build_stable_prefix 输出），
                  注入 system prompt 末尾。同值返回相同 → DeepSeek 前缀缓存命中。

    session_id: 当前会话 UUID，用于隔离缓存诊断状态和前缀比较文件。

    model_override / platform_override: 覆盖当前 key.conf 的模型/平台
                  （Explore 子代理用「X Pro」= 当前系列最便宜模型）。
    """
    # 惰性导入避免循环引用
    from .config import get_current_lang, get_prompt_text, load_key_conf

    lang = get_current_lang()
    prompts = get_prompt_text(lang)

    # ── 加载直连配置 ──
    conf = load_key_conf()
    if not conf or not conf.get("api_key"):
        return {"error": prompts.get("license_invalid_or_quota", "未配置 API 密钥，请重新运行 ai 命令"), "answer": "no", "ask": "", "txt": "", "analysis": ""}
    plat_key = platform_override or conf.get("platform", "deepseek")
    api_key = conf["api_key"]
    if plat_key == "custom":
        plat_info = {
            "name": "Custom",
            "api_url": conf.get("api_url", "https://api.openai.com/v1/chat/completions"),
            "stream_format": "openai",
            "models": [conf.get("model", "gpt-4")],
            "default_model": conf.get("model", "gpt-4"),
            "params": {"temperature": 0.1, "max_tokens": 4096},
        }
    else:
        plat_info = _SUPPORTED_PLATFORMS.get(plat_key, _SUPPORTED_PLATFORMS["deepseek"])
    model = model_override or conf.get("model", "") or plat_info.get("default_model", "")
    # 模型别名解析（opus/sonnet/haiku → 平台具体模型）
    try:
        from .config import resolve_model_alias as _resolve_alias
        model = _resolve_alias(plat_key, model)
    except Exception:
        pass
    user_params = conf.get("params", {})

    tool_list = []
    if onyx_module and hasattr(onyx_module, "TOOL_INDEX_CACHE"):
        try:
            if isinstance(onyx_module.TOOL_INDEX_CACHE, dict) and onyx_module.TOOL_INDEX_CACHE:
                tool_list = [
                    f"- {os.path.basename(os.path.dirname(info.path))}"
                    for info in onyx_module.TOOL_INDEX_CACHE.values()
                    if hasattr(info, 'path') and info.path
                ]
                tool_list = sorted(set(tool_list))
            else:
                tool_list = ["- No available tools (tool cache is empty)" if lang == "english" else "- 无可用工具（工具缓存为空）"]
        except Exception:
            tool_list = ["- No available tools (failed to read)" if lang == "english" else "- 无可用工具（读取失败）"]
    else:
        tool_list = ["- No available tools (not initialized)" if lang == "english" else "- 无可用工具（未初始化）"]

    def detect_system_env() -> Tuple[bool, str, str]:
        try:
            if platform.system() == "Windows":
                return False, "Windows", "Windows"
            if platform.system() == "Darwin":
                return False, "macOS", "macOS"
            if platform.system() == "Linux":
                if os.environ.get('ANDROID_ROOT') or (os.environ.get('PREFIX') and '/com.termux' in os.environ.get('PREFIX', '')):
                    return True, "Linux", "Termux"
                if os.path.exists('/etc/kali_version') or 'kali' in platform.release().lower() or 'kali' in platform.version().lower():
                    return False, "Linux", "Kali"
                dist = ""
                if hasattr(platform, 'linux_distribution'):
                    dist = platform.linux_distribution()[0]
                else:
                    dist = platform.release().split('-')[0] if '-' in platform.release() else "Linux"
                return False, "Linux", dist if dist else "Unknown Linux"
            return False, platform.system(), "Unknown"
        except Exception:
            return False, platform.system(), "Unknown"

    is_termux, sys_main_type, sys_sub_type = detect_system_env()
    termux_type = sys_sub_type if is_termux else "Unknown"
    prompt_items = ["- No available tools (tool cache is empty)" if lang == "english" else "- 无可用工具（工具缓存为空）",
                    "- No available tools (import failed or not initialized)" if lang == "english" else "- 无可用工具（导入失败或未初始化）"]
    tool_count = len(tool_list) if tool_list and tool_list[0] not in prompt_items else 0

    system_label = _i18n("env_system", "bilingual")
    env_label = _i18n("env_env", "bilingual")
    user_label = _i18n("env_user", "bilingual")
    permission_label = "Permission"
    language_label = _i18n("env_language", "bilingual")
    time_label = "Current time"
    tools_label = _i18n("env_tools", "bilingual")
    task_label = _i18n("env_task", "bilingual")

    permission_value = "root administrator" if USER == "root" else "regular user"
    current_shell = os.environ.get("SHELL", "unknown")
    onyx_mode = "unknown"
    if onyx_module and hasattr(onyx_module, "user_mode"):
        onyx_mode = onyx_module.user_mode.current_mode

    # 加载 .ai_s/onyx_ai.md（最高指示/持久记忆）— 模块级缓存，避免每轮读盘
    onyx_ai_prompt = ""
    global _ONYX_AI_PROMPT_CACHE
    try:
        _prompt_home = user_home_dir if user_home_dir else os.path.expanduser("~")
        ai_prompt_file = os.path.join(_prompt_home, ".ai_s", "onyx_ai.md")
        if os.path.exists(ai_prompt_file):
            _file_mtime = os.path.getmtime(ai_prompt_file)
            if _ONYX_AI_PROMPT_CACHE and _ONYX_AI_PROMPT_CACHE[1] == _file_mtime:
                onyx_ai_prompt = _ONYX_AI_PROMPT_CACHE[0]
            else:
                with open(ai_prompt_file, "r", encoding="utf-8") as _apf:
                    onyx_ai_prompt = _apf.read().strip()
                _ONYX_AI_PROMPT_CACHE = (onyx_ai_prompt, _file_mtime)
    except Exception:
        pass

    _stable_env = f"""{system_label}: {sys_main_type} - {sys_sub_type}
{env_label}: {'Termux' if is_termux else 'PC'}
{user_label}: {USER}
Shell: {current_shell}
Onyx Mode: {onyx_mode}
{language_label}: {get_current_lang()}
{tools_label} ({tool_count})
{chr(10).join(tool_list)}
{ai_tools_prompt}
{_i18n('env_persistent_memory', 'bilingual')}
{onyx_ai_prompt if onyx_ai_prompt else _i18n('env_none', 'bilingual')}"""

    _dynamic_suffix = f"""
{task_label}
{question}"""

    env_info = _stable_env + "\n" + _dynamic_suffix

    # ── 加载系统提示词 etc/ai/agreement.md ──
    system_prompt = ""
    try:
        _agreement_paths = [
            os.path.join(ROOT_DIR, "onyx", "etc", "ai", "agreement.md"),
            os.path.join("etc", "ai", "agreement.md"),
        ]
        for _ap in _agreement_paths:
            if os.path.exists(_ap):
                with open(_ap, "r", encoding="utf-8") as _af:
                    system_prompt = _af.read()
                break
    except Exception:
        pass

    # ── 深情模式提示词（如果已激活） ──
    _deep_aff_path = os.path.join(user_home_dir or os.path.expanduser("~"), ".ai_s", "deep_aff_prompt.txt")
    if os.path.exists(_deep_aff_path):
        try:
            with open(_deep_aff_path, "r", encoding="utf-8") as _df:
                _deep_aff = _df.read().strip()
            if _deep_aff:
                system_prompt = _deep_aff + "\n\n" + system_prompt
        except Exception:
            pass

    # ── 构建 messages ──
    # 缓存策略（合并系统消息）:
    #   [system] 统一前缀 = memory_block + env_info
    #             ↑ memory_block 100% 静态，env_info 会话内稳定
    #             ↑ 合并为单个 system 消息，避免连续 role:"system" 导致 DeepSeek 缓存断裂
    #   messages  ← 对话历史，append-only，前缀稳定
    if messages is None:
        _messages = []
        # 单次模式：如果提供了 memory_block（统一前缀），优先使用
        if memory_block:
            # memory_block 已包含 agreement.md + tools + hippocampus + separator
            # system_prompt 不再单独注入，避免重复
            _messages.append({"role": "system", "content": memory_block})
        elif system_prompt:
            _messages.append({"role": "system", "content": system_prompt})
        _messages.append({"role": "user", "content": env_info})
    else:
        # 对话模式 — 静态前缀 + 动态后置：
        #   [0] system: memory_block          ← 100% 静态，跨会话缓存命中（~3584 tokens）
        #   [1] user:   env_info + question   ← env_info 含 cwd/git/海马体，会变但不污染前缀
        #   [2+] assistant/user...            ← 对话历史，append-only
        # 关键：env_info 不合并进 memory_block，而是合并进第一条 user 消息。
        #       这样 env_info 变化时只影响 [1]，不影响 [0] 的前缀缓存。
        _messages = []
        if memory_block:
            _messages.append({"role": "system", "content": memory_block})
            if messages and messages[0].get("role") == "system" and len(messages) > 1:
                # env_info (messages[0]) + 第一条 user 消息 (messages[1]) → 合并为一条 user 消息
                _merged_user = messages[0]["content"] + "\n\n" + str(messages[1].get("content", ""))
                _messages.append({"role": "user", "content": _merged_user})
                _messages.extend(messages[2:])
            elif messages and messages[0].get("role") == "system":
                # 只有 env_info 无 user 消息（极端情况）
                _messages.append({"role": "user", "content": messages[0]["content"]})
                _messages.extend(messages[1:])
            else:
                _messages.extend(messages)
        else:
            _messages.extend(messages)

    # 保留 reasoning_content（DeepSeek thinking 模式要求回传）
    # 仅对不支持 thinking 的平台剥离该字段
    if not plat_info.get("thinking"):
        _messages = [{k: v for k, v in m.items() if k != "reasoning_content"} for m in _messages]

    headers = {
        "Content-Type": "application/json",
    }
    headers["Accept"] = "text/event-stream"

    if plat_key == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    # ── 合并参数 ──
    default_params = dict(plat_info.get("params", {"temperature": 0.1, "top_p": 0.2, "max_tokens": 4096}))
    model_overrides = plat_info.get("model_params", {}).get(model, {})
    p = {**default_params, **model_overrides, **user_params}

    payload: dict
    if plat_key == "anthropic":
        # ── 将 OpenAI 格式 _messages 正确转换为 Anthropic 格式 ──
        system_parts = []
        anthropic_msgs = []

        for m in _messages:
            role = m.get("role", "")
            if role == "system":
                system_parts.append(m.get("content", ""))
                continue

            if role == "user":
                anthropic_msgs.append({"role": "user", "content": m.get("content", "")})

            elif role == "assistant":
                content_text = m.get("content", "")
                tool_calls = m.get("tool_calls")
                if tool_calls:
                    blocks = []
                    if content_text:
                        blocks.append({"type": "text", "text": content_text})
                    for tc in tool_calls:
                        try:
                            args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                        except (json.JSONDecodeError, ValueError, TypeError):
                            args = {}
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": tc.get("function", {}).get("name", ""),
                            "input": args,
                        })
                    anthropic_msgs.append({"role": "assistant", "content": blocks})
                else:
                    anthropic_msgs.append({"role": "assistant", "content": content_text})

            elif role == "tool":
                anthropic_msgs.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    }],
                })

        # 合并连续 user 消息（Anthropic 要求 user/assistant 交替）
        merged = []
        for msg in anthropic_msgs:
            if merged and merged[-1]["role"] == "user" and msg["role"] == "user":
                prev = merged[-1]["content"]
                curr = msg["content"]
                if isinstance(prev, str) and isinstance(curr, str):
                    merged[-1]["content"] = prev + "\n\n" + curr
                elif isinstance(prev, list) and isinstance(curr, str):
                    prev.append({"type": "text", "text": curr})
                elif isinstance(prev, str) and isinstance(curr, list):
                    merged[-1]["content"] = [{"type": "text", "text": prev}] + curr
                else:
                    merged[-1]["content"] = prev + curr
            else:
                merged.append(msg)

        system_content = "\n\n".join(system_parts)
        payload = {
            "model": model,
            "max_tokens": p.get("max_tokens", 4096),
            "system": system_content if system_content else None,
            "messages": merged if merged else [{"role": "user", "content": ""}],
            "stream": True,
        }
        if p.get("temperature") is not None:
            payload["temperature"] = p["temperature"]
        if p.get("top_p") is not None:
            payload["top_p"] = p["top_p"]
        if tools:
            payload["tools"] = _convert_tools_for_anthropic(tools)
    else:
        # ── OpenAI/DeepSeek 分支：tools 置于 messages 之前（前缀缓存优化）──
        # DeepSeek 缓存按"完整前缀单元"匹配，且公共前缀检测基于请求 token 流：
        # tools 定义（约 2 万余 token）若排在 messages 之后，公共前缀在第一条
        # user 消息（env+问题）处就分叉，tools 永远进不了公共前缀单元 →
        # 新会话首轮、快速连发轮次都只能命中 messages[0] 静态前缀。
        # tools 提前后，公共前缀 = model + tools + messages[0]（三者跨会话
        # 100% 静态），一旦被公共前缀检测持久化，所有轮次命中率回到 90%+。
        # JSON 字段顺序不影响 API 语义。
        payload = {}
        payload["model"] = model
        if tools:
            payload["tools"] = tools
        payload["messages"] = _messages
        payload["stream"] = True
        payload["max_tokens"] = p.get("max_tokens", 4096)
        if p.get("temperature") is not None:
            payload["temperature"] = p["temperature"]
        if p.get("top_p") is not None:
            payload["top_p"] = p["top_p"]

    if plat_info.get("thinking"):
        payload["thinking"] = plat_info["thinking"]
    _effort = user_params.get("reasoning_effort") or plat_info.get("reasoning_effort")
    if _effort:
        payload["reasoning_effort"] = _effort

    if tools:
        payload["tools"] = tools

    payload["stream_options"] = {"include_usage": True}

    # ── 写入 AI 真实看到的完整内容到 <home>/.ai_s/tmp/（每次覆盖）──
    try:
        _tmp_dir = os.path.join(user_home_dir or os.path.expanduser("~"), ".ai_s", "tmp")
        os.makedirs(_tmp_dir, exist_ok=True)

        # ── ai_request.txt：人类可读的消息全文 ──
        _req_path = os.path.join(_tmp_dir, "ai_request.txt")
        _lines = [f"═══ API REQUEST ({plat_key}/{model}) — {datetime.now().strftime('%H:%M:%S')} ═══", ""]
        for _idx, _m in enumerate(_messages):
            _role = _m.get("role", "?")
            _content = _m.get("content", "")
            _tc = _m.get("tool_calls")
            _lines.append(f"── [{_idx}] {_role.upper()} ──")
            if _tc:
                _tc_names = [t.get("function", {}).get("name", "?") for t in _tc]
                _lines.append(f"[tool_calls: {', '.join(_tc_names)}]")
            _lines.append(str(_content))
            _lines.append("")
        _lines.append(f"── TOOLS: {len(tools) if tools else 0} functions ──")
        if tools:
            for _t in tools:
                _fn = _t.get("function", {})
                _lines.append(f"  {_fn.get('name','?')}: {_fn.get('description','')[:120]}")
        _lines.append("")
        with open(_req_path, "w", encoding="utf-8") as _rf:
            _rf.write("\n".join(_lines))

        # ── ai_payload.json：原始 JSON 请求体（供 diff 对比两轮差异）──
        _payload_path = os.path.join(_tmp_dir, "ai_payload.json")
        with open(_payload_path, "w", encoding="utf-8") as _pf:
            json.dump(payload, _pf, ensure_ascii=False, indent=2)
    except Exception:
        pass

    api_url = plat_info["api_url"]
    stream_fmt = plat_info["stream_format"]

    max_retries = 3
    base_delay = 2
    last_error = None

    # 重置中断标志（使用模块引用以让信号处理器的修改可见）
    _mcp_state._AI_INTERRUPTED = False

    # ── 载荷哈希诊断：追踪每轮 API 请求的字节级前缀变化 ──
    _deb_dir = ""
    if debug_mode:
        try:
            _deb_dir = os.path.join(user_home_dir or os.path.expanduser("~"), ".ai_s", "deb", session_id or "default")
            os.makedirs(_deb_dir, exist_ok=True)
        except Exception:
            pass
    _payload_hash_info = _track_payload_hash(session_id, payload, _deb_dir)

    for retry in range(max_retries):
        try:
            _mcp_debug_fn(f"HTTP POST {api_url} (attempt {retry+1}/{max_retries})")
            response = requests.post(
                api_url, headers=headers, json=payload,
                timeout=120, stream=True
            )
            _mcp_debug_fn(f"HTTP response: {response.status_code}")

            if response.status_code in (400, 422):
                _detail = response.text[:2000]
                # ── 写入调试文件：完整请求 payload + 响应 body ──
                _deb_path = ""
                try:
                    _deb_dir = os.path.join(user_home_dir or os.path.expanduser("~"), ".ai_s", "deb")
                    os.makedirs(_deb_dir, exist_ok=True)
                    _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    _deb_path = os.path.join(_deb_dir, f"http_{response.status_code}_{_ts}.json")
                    _safe_payload = {}
                    for _k, _v in payload.items():
                        if _k == "messages":
                            _safe_payload[_k] = [
                                {**{mk: mv for mk, mv in m.items() if mk != "content"},
                                 "content": str(m.get("content", ""))[:200] + ("..." if len(str(m.get("content", ""))) > 200 else "")}
                                for m in _v
                            ]
                        else:
                            _safe_payload[_k] = _v
                    with open(_deb_path, "w", encoding="utf-8") as _df:
                        _df.write(f"── HTTP {response.status_code} Request ──\n")
                        json.dump(_safe_payload, _df, ensure_ascii=False, indent=2)
                        _df.write(f"\n\n── Response Body ──\n{response.text[:10000]}")
                except Exception:
                    pass
                console.print(f"[red]❌ API 请求错误 ({response.status_code})[/]")
                console.print(f"[dim]   调试文件: {_deb_path}[/]")
                if _detail:
                    console.print(f"[red]   {_detail[:300]}[/]")
                return {
                    "error": f"请求参数错误 ({response.status_code}): {_detail[:500]}",
                    "txt": f"❌ **API 请求失败 (HTTP {response.status_code})**\n\n{_detail[:500]}",
                    "analysis": f"HTTP {response.status_code} 表示请求参数有问题（如 API Key、模型名或消息格式错误）。这不是临时故障，重试也无法解决，请检查配置。\n调试文件: {_deb_path}",
                    "answer": "yes",
                    "ask": ""
                }
            if response.status_code == 401:
                return {"error": "API key 无效 (401)", "answer": "no", "ask": "", "txt": "", "analysis": ""}
            if response.status_code == 402:
                return {"error": "⚠️ API 余额不足 (402)，请充值后重试 | Insufficient balance, please top up", "answer": "no", "ask": "", "txt": "", "analysis": ""}
            if response.status_code == 429:
                last_error = "请求过于频繁 (429)"
                if retry < max_retries - 1:
                    _wait = base_delay * (retry + 1) * 2
                    console.print(f"[yellow]⚠️ API 限流 (429)，{_wait}秒后重试 (第 {retry+1}/{max_retries} 次)...[/]")
                    time.sleep(_wait)
                    continue
                return {"error": "请求过于频繁 (429)，请稍后再试 | Rate limit reached, please retry later", "answer": "no", "ask": "", "txt": "", "analysis": ""}
            if response.status_code in (500, 502, 503):
                last_error = f"AI 服务暂时不可用 ({response.status_code})"
                if retry < max_retries - 1:
                    _wait = base_delay * (retry + 1) * 3
                    console.print(f"[yellow]⚠️ AI 服务暂时不可用 ({response.status_code})，{_wait}秒后重试 (第 {retry+1}/{max_retries} 次)...[/]")
                    time.sleep(_wait)
                    continue
                return {"error": f"AI 服务暂时不可用 ({response.status_code})，请稍后再试", "answer": "no", "ask": "", "txt": "", "analysis": ""}
            response.raise_for_status()

            response.encoding = 'utf-8'
            full_content = ""
            debug_lines = []
            _usage = {}
            _tool_calls_acc: Dict[int, Dict] = {}
            _anthropic_tool_acc: Dict[int, Dict] = {}
            _reasoning_display: List[str] = []

            # 保存活跃 response 引用（允许 Ctrl+C 强制关闭）
            global _ACTIVE_RESPONSE
            _ACTIVE_RESPONSE = response

            if stream_fmt == "openai":
                for line in response.iter_lines(decode_unicode=True):
                    if _mcp_state._AI_INTERRUPTED:
                        response.close()
                        _ACTIVE_RESPONSE = None
                        return {"txt": "", "analysis": "", "answer": "yes", "ask": "", "_interrupted": True}
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        if not isinstance(chunk, dict):
                            continue
                        # ── 捕获 usage（可能出现在空 choices 的 chunk 或最后一帧同时带 choices）──
                        _chunk_usage = chunk.get("usage")
                        if _chunk_usage:
                            _usage = _chunk_usage
                        if not chunk.get("choices"):
                            continue
                        choices = chunk.get("choices", [])
                        if not choices or not isinstance(choices[0], dict):
                            continue
                        delta = choices[0].get("delta", {})
                        if not isinstance(delta, dict):
                            continue
                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            _reasoning_display.append(reasoning)
                            if on_reasoning:
                                on_reasoning(reasoning)
                        content = delta.get("content")
                        if content:
                            full_content += content
                            if on_content:
                                on_content(content)
                        tc_delta = delta.get("tool_calls")
                        if tc_delta and isinstance(tc_delta, list):
                            for tc_chunk in tc_delta:
                                if not isinstance(tc_chunk, dict):
                                    continue
                                tc_idx = tc_chunk.get("index", 0)
                                _is_new = tc_idx not in _tool_calls_acc
                                if _is_new:
                                    _tool_calls_acc[tc_idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                                    _tc_name = tc_chunk.get("function", {}).get("name", "")
                                    if _tc_name and on_tool_call:
                                        on_tool_call(_tc_name)
                                tcc = _tool_calls_acc[tc_idx]
                                if tc_chunk.get("id"):
                                    tcc["id"] = tc_chunk["id"]
                                if tc_chunk.get("type"):
                                    tcc["type"] = tc_chunk["type"]
                                func_delta = tc_chunk.get("function", {})
                                if func_delta.get("name"):
                                    tcc["function"]["name"] = func_delta["name"]
                                if func_delta.get("arguments"):
                                    tcc["function"]["arguments"] += func_delta["arguments"]
                    except json.JSONDecodeError:
                        continue
            else:
                # Anthropic SSE 格式解析，支持 tool_use
                for line in response.iter_lines(decode_unicode=True):
                    if _mcp_state._AI_INTERRUPTED:
                        response.close()
                        return {"txt": "", "analysis": "", "answer": "yes", "ask": "", "_interrupted": True}
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    try:
                        chunk = json.loads(data_str)
                        if not isinstance(chunk, dict):
                            continue
                        ctype = chunk.get("type", "")

                        if ctype == "message_start":
                            # Anthropic 的 cache_read_input_tokens / cache_creation_input_tokens
                            # 只出现在 message_start 事件中，必须在此采集
                            _msg_obj = chunk.get("message", {})
                            _start_usage = _msg_obj.get("usage")
                            if isinstance(_start_usage, dict):
                                _usage.update(_start_usage)

                        elif ctype == "content_block_start":
                            cb = chunk.get("content_block", {})
                            if not isinstance(cb, dict):
                                continue
                            if cb.get("type") == "tool_use":
                                idx = chunk.get("index", 0)
                                _anthropic_tool_acc[idx] = {
                                    "id": cb.get("id", ""),
                                    "name": cb.get("name", ""),
                                    "input_json": "",
                                }
                                if on_tool_call:
                                    on_tool_call(cb.get("name", ""))
                            elif cb.get("type") == "text":
                                text = cb.get("text", "")
                                if text:
                                    full_content += text
                                    if on_content:
                                        on_content(text)

                        elif ctype == "content_block_delta":
                            delta = chunk.get("delta", {})
                            if not isinstance(delta, dict):
                                continue
                            dtype = delta.get("type", "")
                            if dtype == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    full_content += text
                                    if on_content:
                                        on_content(text)
                            elif dtype == "input_json_delta":
                                idx = chunk.get("index", 0)
                                partial = delta.get("partial_json", "")
                                if idx in _anthropic_tool_acc:
                                    _anthropic_tool_acc[idx]["input_json"] += partial

                        elif ctype == "content_block_stop":
                            pass

                        elif ctype == "message_delta":
                            _mdelta = chunk.get("delta", {})
                            if isinstance(_mdelta, dict) and _mdelta.get("stop_reason") == "tool_use":
                                pass  # 工具调用将在循环结束后处理
                            usage_info = chunk.get("usage")
                            if usage_info:
                                # merge 而非覆盖，保留 message_start 采集到的缓存字段
                                _usage.update(usage_info)

                        elif ctype == "message_stop":
                            break

                    except json.JSONDecodeError:
                        continue

            # 流式读取完毕，清除活跃 response 引用
            _ACTIVE_RESPONSE = None

            raw_full = full_content
            if full_content:
                import re as _re
                full_content = _re.sub(
                    r'(?<!\n)(\[TXT\](?![:D])|\[TXT:DONE\]|\[ANALYSIS\](?![:D])|\[ANALYSIS:DONE\]|@@SHELL|>>>>>>>>>>|\[ANSWER\]|\[ASK\]|\[PLAN\]|\[PLAN:DONE\]|\[PROMPT\]|\[PROMPT:DONE\]|\[TAG\]|\[TAG:DONE\]|\[MEMORY\]|\[CLASS\]|\[SLEEP\])',
                    r'\n\1', full_content
                )

            result = parse_sse_structured_response(full_content)

            try:
                from lib.native_fs.markup_parser import parse_markup as _parse_markup
                result["markup_blocks"] = _parse_markup(raw_full if raw_full else full_content)
            except Exception:
                result["markup_blocks"] = []

            if _tool_calls_acc:
                native_tools = []
                for idx in sorted(_tool_calls_acc.keys()):
                    tc = _tool_calls_acc[idx]
                    try:
                        args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
                    except (json.JSONDecodeError, ValueError):
                        args = tc["function"]["arguments"]
                    native_tools.append({
                        "name": tc['function']['name'],
                        "params_str": json.dumps(args) if isinstance(args, dict) else str(args),
                        # 缓存字节一致性：id 与 arguments 必须保留 API 原始字节。
                        # DeepSeek 按完整前缀单元匹配缓存，下一轮请求回显的
                        # assistant tool_calls 若与 API 输出不一致（重写 id、
                        # 重排 arguments），"模型输出端单元"无法完整命中 → 缓存断裂。
                        "id": tc.get("id", ""),
                        "raw_arguments": tc.get("function", {}).get("arguments", ""),
                        "_native": True,
                    })
                existing = result.get("tool_calls", [])
                if not isinstance(existing, list):
                    existing = []
                result["tool_calls"] = existing + native_tools

            # 同样处理 Anthropic tool_use 格式的累积结果
            if _anthropic_tool_acc:
                native_tools = []
                for idx in sorted(_anthropic_tool_acc.keys()):
                    tc = _anthropic_tool_acc[idx]
                    try:
                        args = json.loads(tc["input_json"]) if tc["input_json"] else {}
                    except (json.JSONDecodeError, ValueError):
                        args = tc["input_json"]
                    native_tools.append({
                        "name": tc["name"],
                        "params_str": json.dumps(args) if isinstance(args, dict) else str(args),
                        # 缓存字节一致性：同 OpenAI 分支，保留原始 id / input_json 字节
                        "id": tc.get("id", ""),
                        "raw_arguments": tc.get("input_json", ""),
                        "_native": True,
                    })
                existing = result.get("tool_calls", [])
                if not isinstance(existing, list):
                    existing = []
                result["tool_calls"] = existing + native_tools

            if debug_mode:
                import re as _re
                deb_dir = os.path.join(user_home_dir or os.path.expanduser("~"), ".ai_s", "deb")
                os.makedirs(deb_dir, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                raw_path = os.path.join(deb_dir, f"{ts}_raw.txt")
                with open(raw_path, "w", encoding="utf-8") as _df:
                    _df.write(f"── Raw API Response ({plat_key}, model={model}) ──\n")
                    _df.write(raw_full)
                    _df.write("\n── End Raw ──\n")
                parsed_path = os.path.join(deb_dir, f"{ts}_parsed.json")
                with open(parsed_path, "w", encoding="utf-8") as _df:
                    json.dump(result, _df, ensure_ascii=False, indent=2)
                debug_lines.append(f"── Raw ({plat_key}) ──")
                debug_lines.append(raw_full[:2000])
                debug_lines.append("── End Raw ──")
                debug_lines.append(f"── 完整日志: {raw_path} ──")
                debug_lines.append(f"── 解析结果: {parsed_path} ──")
                debug_lines.append("── Parsed ──")
                debug_lines.append(json.dumps(result, ensure_ascii=False, indent=2)[:2000])

            # ── 透传平台缓存支持标记 ──
            result["_cache_supported"] = plat_info.get("supports_prompt_cache", False)

            if _usage:
                result["_usage"] = _usage
                # ── 缓存诊断（模块级持久化，按 session_id 隔离）──
                try:
                    from .cache_diagnostics import (
                        capture_prefix_shape, compare_shapes,
                        extract_cache_tokens_from_usage, format_cache_report,
                        SessionCacheStats,
                    )
                    _slot = _get_cache_diag_slot(session_id)
                    _prev = _slot["prev_shape"]
                    _stats = _slot["stats"]
                    if _stats is None:
                        _stats = SessionCacheStats()
                        _slot["stats"] = _stats
                    _cur = capture_prefix_shape(
                        system_prompt=system_prompt or "",
                        tools=tools or [],
                        messages_prefix=memory_block or "",
                        rewrite_version=_slot.get("rewrite_version", 0),
                    )
                    hit, miss = extract_cache_tokens_from_usage(_usage)
                    _diag = compare_shapes(_prev if _prev else _cur, _cur, hit, miss)
                    _stats.record(_diag)
                    _slot["prev_shape"] = _cur
                    result["_cache_report"] = format_cache_report(_diag)
                    if debug_mode:
                        console.print(f"[dim]📊 {result['_cache_report']}[/]")
                        # ── 载荷字节级前缀匹配诊断 ──
                        if _payload_hash_info.get("match_pct") is not None:
                            _mb = _payload_hash_info["match_bytes"]
                            _tb = _payload_hash_info["total_bytes"]
                            _mp = _payload_hash_info["match_pct"]
                            _icon = "✅" if _mp > 90 else ("⚠️" if _mp > 50 else "❌")
                            _hint = (
                                "← 仅 messages[0] 命中，后续被截断"
                                if _mp < 50 else ""
                            )
                            console.print(
                                f"[dim]🔍 载荷字节匹配: {_mp:.1f}% ({_mb:,}/{_tb:,} bytes) "
                                f"{_icon} {_hint}[/]"
                            )
                        # ── 展示本次载荷哈希（短标识）──
                        _ph = _payload_hash_info.get("payload_hash", "?")
                        console.print(f"[dim]🔑 载荷哈希: {_ph} (round #{_payload_hash_info.get('round',0)})[/]")
                except Exception:
                    pass  # best-effort
            if _reasoning_display:
                result["_reasoning"] = "".join(_reasoning_display)
            result["_debug"] = "\n".join(debug_lines) if debug_lines else ""
            return result

        except KeyboardInterrupt:
            _mcp_state._AI_INTERRUPTED = True
            _ACTIVE_RESPONSE = None
            try:
                response.close()
            except Exception:
                pass
            return {"txt": "", "analysis": "", "answer": "yes", "ask": "", "_interrupted": True}
        except requests.exceptions.Timeout:
            last_error = prompts["ai_request_timeout"]
        except requests.exceptions.ConnectionError:
            last_error = prompts["connection_failed"]
        except requests.exceptions.RequestException as e:
            last_error = prompts["request_failed"].format(str(e))
        except Exception as e:
            last_error = prompts["unknown_error"].format(str(e))

        if retry < max_retries - 1:
            delay = base_delay * (2 ** retry)
            retry_msg = prompts.get("retrying", "Retrying ({}/{}) in {}s...").format(retry + 1, max_retries, delay)
            console.print(retry_msg, style="dim")
            time.sleep(delay)

    return {"error": last_error or "Max retries exceeded", "analysis": "", "txt": "", "answer": "no", "ask": ""}


def process_ai_result_fields(ai_result: Dict[str, Any]) -> Dict[str, Any]:
    """处理AI返回的所有字段，确保默认值"""
    result = ai_result.copy()
    if "answer" not in result:
        result["answer"] = "no"
    if "ask" not in result:
        result["ask"] = ""
    if "tag" not in result:
        result["tag"] = ""
    if "memory" not in result:
        result["memory"] = ""
    if "analysis" not in result:
        result["analysis"] = ""
    if "txt" not in result:
        result["txt"] = ""
    if "plan" not in result:
        result["plan"] = ""
    if "tool_calls" not in result:
        result["tool_calls"] = []
    if "sleep" not in result:
        result["sleep"] = None
    if "class" not in result:
        result["class"] = "1"
    if "markup_blocks" not in result:
        result["markup_blocks"] = []
    return result


def extract_ai_commands(ai_result: Dict[str, Any]) -> List[str]:
    """提取AI返回的命令"""
    commands = []
    for key, cmd in ai_result.items():
        if key.startswith("cmd") and key[3:].isdigit() and cmd and str(cmd).strip():
            commands.append(str(cmd).strip())
    return commands


def build_stable_prefix(home_dir: str, chat_name: str = None) -> str:
    """
    构建缓存稳定的记忆前缀（确定性输出，同输入→同输出）。
    
    使用海马体（chat JSON）作为 library 索引——海马体天然的
    {id, session_uuid, question, tag, class} 结构比 LIBRARY.md 更精确。
    
    此函数的结果注入 system prompt，DeepSeek 自动前缀缓存命中。
    每次会话只计算一次，中途绝不变化。
    """
    from .storage import load_hippocampus_index as _load_idx
    
    hippocampus = _load_idx(home_dir, chat_name)
    return hippocampus if hippocampus else ""


def build_memory_context(home_dir: str, chat_name: str, current_session_id: str,
                         referenced_memory_uuid: Optional[str], is_first_interaction: bool,
                         mode: str = "normal") -> str:
    """
    构建瞬态记忆上下文（按需查询型 — 不注入全量内容，由 AI 通过 MemoryRead/MemorySearch 按需拉取）。
    
    仅注入：UUID 链索引（精简）| 当前会话尾部摘要（最近 2000 字符）| 引用记忆 ID
    
    全量历史内容通过 MemoryRead("library/<uuid>") / MemorySearch 按需查询，
    避免每轮把不断增长的 library 全量注入为 cache miss。
    """
    lang = get_current_lang()
    is_en = lang == "english"
    parts = []
    _MAX_CURRENT_SESSION_CHARS = 2000  # 当前会话最多注入 2000 字符的尾部摘要

    def _strip_oversized_fences(text: str, max_fence: int = 600) -> str:
        """剥离超长 ``` 代码块原文（工具输出污染），保留短代码块（如小段配置）。"""
        import re as _re
        def _repl(m):
            inner = m.group(2)
            if len(inner) <= max_fence:
                return m.group(0)
            return m.group(1) + f"[elided {len(inner)} chars — MemoryRead for full]\n" + m.group(3)
        return _re.sub(r"(```[^\n]*\n)(.*?)(```)", _repl, text, flags=_re.DOTALL)

    if mode == "normal":
        # ── UUID 链：仅索引（id/session_uuid/question 摘要），非全量内容 ──
        chat_memory = load_chat_memory_for_context(home_dir, chat_name)
        if chat_memory:
            header = (
                "═══════════════════════════════════════\n"
                " 历史会话索引 — 使用 MemoryRead(\"library/<uuid>\") 查看完整记录\n"
                "═══════════════════════════════════════"
            ) if is_en else (
                "═══════════════════════════════════════\n"
                " 历史会话索引 — 使用 MemoryRead(\"library/<uuid>\") 查看完整记录\n"
                "═══════════════════════════════════════"
            )
            parts.append(header + "\n" + chat_memory)

        previous_uuid = get_previous_session_uuid(home_dir, chat_name, current_session_id, is_first_interaction)
        if previous_uuid:
            if is_en:
                parts.append(f"💡 上一会话: library/{previous_uuid}.txt — MemoryRead 可查询")
            else:
                parts.append(f"💡 上一会话: library/{previous_uuid}.txt — MemoryRead 可查询")

        # ── 当前会话：仅注入尾部摘要（最近 2000 字符）──
        existing_memory, _ = get_latest_ai_session(home_dir, current_session_id)
        if existing_memory and existing_memory.strip():
            # 先剥离超长代码块（原始工具输出），再截尾部 → 注入内容高信号化
            _trimmed = _strip_oversized_fences(existing_memory.strip())
            if len(_trimmed) > _MAX_CURRENT_SESSION_CHARS:
                _trimmed = "…(earlier content omitted — use MemoryRead for full)\n\n" + _trimmed[-_MAX_CURRENT_SESSION_CHARS:]
            header = (
                "═══════════════════════════════════════\n"
                f" 当前会话 — library/{current_session_id}.txt （尾部摘要，MemoryRead 查全文）\n"
                "═══════════════════════════════════════"
            ) if is_en else (
                "═══════════════════════════════════════\n"
                f" 当前会话 — library/{current_session_id}.txt （尾部摘要，MemoryRead 查全文）\n"
                "═══════════════════════════════════════"
            )
            parts.append(header + "\n" + _trimmed)

        if referenced_memory_uuid:
            if is_en:
                parts.append(f"💡 引用记忆: [MEMORY:{referenced_memory_uuid}] — MemoryRead(\"library/{referenced_memory_uuid}\") 查询")
            else:
                parts.append(f"💡 引用记忆: [MEMORY:{referenced_memory_uuid}] — MemoryRead(\"library/{referenced_memory_uuid}\") 查询")

    elif mode in ["adv_code", "adv_terminal"]:
        existing_memory, _ = get_latest_ai_session(home_dir, current_session_id)
        if existing_memory and existing_memory.strip():
            # 先剥离超长代码块（原始工具输出），再截尾部
            _trimmed = _strip_oversized_fences(existing_memory.strip())
            if len(_trimmed) > _MAX_CURRENT_SESSION_CHARS:
                _trimmed = "…(earlier omitted — MemoryRead for full)\n\n" + _trimmed[-_MAX_CURRENT_SESSION_CHARS:]
            header = (
                "═══════════════════════════════════════\n"
                f" Current Session (library/{current_session_id}.txt) — tail summary\n"
                "═══════════════════════════════════════"
            ) if is_en else (
                "═══════════════════════════════════════\n"
                f" 当前会话 (library/{current_session_id}.txt) — 尾部摘要\n"
                "═══════════════════════════════════════"
            )
            parts.append(header + "\n" + _trimmed)

    return "\n\n".join(parts) if parts else ("No historical memory" if is_en else "无历史记忆")
