# -*- coding: utf-8 -*-
"""
Onyx AI API 调用模块 — SSE 流式调用、结果处理、记忆上下文

从 bin/ai_cmd.py 提取，零功能变更。
"""

import os
import json
import time
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
                    memory_block: str = "") -> Dict[str, Any]:
    """
    memory_block: 缓存稳定前缀（build_stable_prefix 输出），
                  注入 system prompt 末尾。同值返回相同 → DeepSeek 前缀缓存命中。
    """
    # 惰性导入避免循环引用
    from .config import get_current_lang, get_prompt_text, load_key_conf

    lang = get_current_lang()
    prompts = get_prompt_text(lang)

    # ── 加载直连配置 ──
    conf = load_key_conf()
    if not conf or not conf.get("api_key"):
        return {"error": prompts.get("license_invalid_or_quota", "未配置 API 密钥，请重新运行 ai 命令"), "answer": "no", "ask": "", "txt": "", "analysis": ""}
    plat_key = conf.get("platform", "deepseek")
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
    model = conf.get("model", "") or plat_info.get("default_model", "")
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

    system_label = "System"
    env_label = "Environment"
    user_label = "User"
    permission_label = "Permission"
    workdir_label = "Working directory"
    language_label = "Language"
    time_label = "Current time"
    tools_label = "Available tools"
    task_label = "Task"

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
#Available tools ({tool_count})
{chr(10).join(tool_list)}
{ai_tools_prompt}"""

    _dynamic_suffix = f"""#Working directory: {os.getcwd()}
#Persistent memory
{onyx_ai_prompt if onyx_ai_prompt else '(none)'}

#{task_label}
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
    if messages is None:
        _messages = []
        if system_prompt:
            sp_content = system_prompt
            # 注入缓存稳定前缀到 system prompt 末尾
            if memory_block:
                sp_content = sp_content.rstrip() + "\n\n# Persistent Memory (cache-stable)\n" + memory_block
            _messages.append({"role": "system", "content": sp_content})
        elif memory_block:
            _messages.append({"role": "system", "content": "# Persistent Memory (cache-stable)\n" + memory_block})
        _messages.append({"role": "user", "content": env_info})
    else:
        # messages 已预构建：将 memory_block 作为独立的 system 消息前置
        # 关键：独立消息 → 第一条不变 → DeepSeek 前缀缓存命中
        if memory_block:
            _messages = [{"role": "system", "content": "# Persistent Memory (cache-stable)\n" + memory_block}] + list(messages)
        else:
            _messages = list(messages)

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
        payload = {
            "model": model,
            "messages": _messages,
            "stream": True,
            "max_tokens": p.get("max_tokens", 4096),
        }
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

    api_url = plat_info["api_url"]
    stream_fmt = plat_info["stream_format"]

    max_retries = 3
    base_delay = 2
    last_error = None

    # 重置中断标志（使用模块引用以让信号处理器的修改可见）
    _mcp_state._AI_INTERRUPTED = False

    for retry in range(max_retries):
        try:
            _mcp_debug_fn(f"HTTP POST {api_url} (attempt {retry+1}/{max_retries})")
            response = requests.post(
                api_url, headers=headers, json=payload,
                timeout=120, stream=True
            )
            _mcp_debug_fn(f"HTTP response: {response.status_code}")

            if response.status_code in (400, 422):
                _detail = response.text[:500]
                console.print(f"[red]❌ API 请求错误 ({response.status_code}): {_detail[:200]}[/]")
                return {
                    "error": f"请求参数错误 ({response.status_code}): {_detail}",
                    "txt": f"❌ **API 请求失败 (HTTP {response.status_code})**\n\n{_detail[:500]}",
                    "analysis": f"HTTP {response.status_code} 表示请求参数有问题（如 API Key、模型名或消息格式错误）。这不是临时故障，重试也无法解决，请检查配置。",
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
                        if not chunk.get("choices"):
                            usage_info = chunk.get("usage")
                            if usage_info:
                                _usage = usage_info
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

                        if ctype == "content_block_start":
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
                                _usage = usage_info

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

            if _usage:
                result["_usage"] = _usage
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
    构建瞬态记忆上下文（每轮变化，不参与前缀缓存）。
    
    包含：UUID 链历史 | 当前会话实时内容 | 引用记忆
    
    注意：LIBRARY.md 索引已移至 build_stable_prefix() 作为缓存稳定前缀。
    """
    lang = get_current_lang()
    is_en = lang == "english"
    parts = []

    if mode == "normal":
        # ── 瞬态记忆：UUID 链 + 当前会话（每轮变化，不参与缓存前缀）──
        chat_memory = load_chat_memory_for_context(home_dir, chat_name)
        uuid_chain = []
        if chat_memory:
            uuid_chain.append(chat_memory)

        previous_uuid = get_previous_session_uuid(home_dir, chat_name, current_session_id, is_first_interaction)
        if previous_uuid:
            prev_memory = load_memory_by_uuid(home_dir, previous_uuid)
            if prev_memory:
                prev_block = (
                    f"\n--- {{UUID链: {previous_uuid}}} ---\n"
                    f"{prev_memory.strip()}"
                ) if is_en else (
                    f"\n--- {{UUID链: {previous_uuid}}} ---\n"
                    f"{prev_memory.strip()}"
                )
                uuid_chain.append(prev_block)

        if uuid_chain:
            header = (
                "═══════════════════════════════════════\n"
                " UUID 链 — 历史参考（非当前对话，按 id/session 标记可精确引用）\n"
                "═══════════════════════════════════════"
            ) if is_en else (
                "═══════════════════════════════════════\n"
                " UUID 链 — 历史参考（非当前对话，每条带独立 id，可按 MEMORY 精确引用）\n"
                "═══════════════════════════════════════"
            )
            parts.append(header + "\n" + "\n\n".join(uuid_chain))

        existing_memory, _ = get_latest_ai_session(home_dir, current_session_id)
        if existing_memory and existing_memory.strip():
            header = (
                "═══════════════════════════════════════\n"
                f" 当前会话（ongoing）— library/{current_session_id}.txt （实时更新）\n"
                "═══════════════════════════════════════"
            ) if is_en else (
                "═══════════════════════════════════════\n"
                f" 当前会话（ongoing）— library/{current_session_id}.txt （实时更新）\n"
                "═══════════════════════════════════════"
            )
            parts.append(header + "\n" + existing_memory.strip())

        if referenced_memory_uuid:
            ref_memory = load_memory_by_uuid(home_dir, referenced_memory_uuid)
            if ref_memory:
                header = (
                    "═══════════════════════════════════════\n"
                    f" 引用记忆 — [MEMORY:{referenced_memory_uuid}]\n"
                    "═══════════════════════════════════════"
                ) if is_en else (
                    "═══════════════════════════════════════\n"
                    f" 引用记忆 — [MEMORY:{referenced_memory_uuid}]\n"
                    "═══════════════════════════════════════"
                )
                parts.append(header + "\n" + ref_memory.strip())

    elif mode in ["adv_code", "adv_terminal"]:
        existing_memory, _ = get_latest_ai_session(home_dir, current_session_id)
        if existing_memory and existing_memory.strip():
            header = (
                "═══════════════════════════════════════\n"
                f" Current Session (library/{current_session_id}.txt)\n"
                "═══════════════════════════════════════"
            ) if is_en else (
                "═══════════════════════════════════════\n"
                f" 当前会话 (library/{current_session_id}.txt)\n"
                "═══════════════════════════════════════"
            )
            parts.append(header + "\n" + existing_memory.strip())

    return "\n\n".join(parts) if parts else ("No historical memory" if is_en else "无历史记忆")
