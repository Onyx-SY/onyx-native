
# -------------------------- 1. 基础模块导入 --------------------------

import sys
import os
import time
import threading
import json
import requests
import gzip
import uuid
import ctypes
import warnings
import platform
import shutil
import shlex
import re
import secrets
from typing import List, Tuple, Optional, Dict, Any, Callable

# ── 自研文件编辑系统 ──
from lib.native_fs.markup_parser import parse_markup as _parse_markup
from lib.native_fs import process_blocks as _process_native_blocks
from datetime import datetime, timedelta
from prompt_toolkit import prompt
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PromptStyle
from prompt_toolkit.formatted_text import FormattedText
from datetime import datetime  
warnings.filterwarnings('ignore', category=requests.packages.urllib3.exceptions.InsecureRequestWarning)

from rich.console import Console
from rich.text import Text as RichText
console = Console()

# AI 工具已切换为 MCP 协议（见下方 MCP 客户端模块），不再使用 plugin_loader
# 保留导入以兼容旧代码引用（后续可安全移除）
# UI 增强模块（Rich + InquirerPy，未安装 InquirerPy 时自动回退）
from .ai_lib.ui import (
    select_option,
    confirm_dangerous as ui_confirm_dangerous,
    text_input as ui_text_input,
    render_plan_panel,
    render_analysis_panel,
    render_warning_panel,
    render_ai_panel,
    render_tool_table,
    render_separator,
    StreamingDisplay,
)
# 核心路径配置
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
USER = os.getlogin() if hasattr(os, "getlogin") else os.getenv("USER", "default")
USER_HOME_DIR = os.path.join(ROOT_DIR, "root") if USER == "root" else os.path.join(ROOT_DIR, "home", USER)
LANGUAGE_CONFIG_PATH = os.path.join(USER_HOME_DIR, ".config", "onyx", "language")
help_info_path = os.path.join(ROOT_DIR, "onyx", "bin", "help", "help_info.json")
onyx_config_path = os.path.join(ROOT_DIR, "onyx", "etc", "config.json")
AI_KEY_DIR = os.path.join(USER_HOME_DIR, ".config", "onyx", "ai")
AI_KEY_PATH = os.path.join(AI_KEY_DIR, "key.key")
KEY_CONF_PATH = os.path.join(USER_HOME_DIR, ".config", "onyx", "ai", "key.conf")
MOOD_PATH = os.path.join(USER_HOME_DIR, ".ai_s", "mood.json")
SERVER_URL_FILE = os.path.join(ROOT_DIR, "onyx", "etc", ".url")

# 延迟初始化
AI_KEY = None
SERVER_URL = None

# ──────────────────── AI 模型列表（从 etc/ai/models.json 加载）───────
def _load_ai_models() -> dict:
    """Load AI platform configs from etc/ai/models.json.
    
    Returns a dict keyed by platform id.  Falls back to a hardcoded
    copy when the JSON file is missing or unparseable.
    """
    models_path = os.path.join(ROOT_DIR, "onyx", "etc", "ai", "models.json")
    try:
        with open(models_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if isinstance(v, dict) and "api_url" in v}
    except Exception:
        pass
    # ── Hardcoded fallback (kept in sync with models.json) ──
    return {
        "deepseek": {
            "name": "深度求索DeepSeek",
            "api_url": "https://api.deepseek.com/v1/chat/completions",
            "stream_format": "openai",
            "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
            "default_model": "deepseek-v4-flash",
            "params": {"temperature": 0.1, "top_p": 0.2, "max_tokens": 8192},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
        "openai": {
            "name": "OpenAI",
            "api_url": "https://api.openai.com/v1/chat/completions",
            "stream_format": "openai",
            "models": ["gpt-5.5", "gpt-5.5-instant", "gpt-5.5-pro"],
            "default_model": "gpt-5.5-instant",
            "params": {"temperature": 0.1, "top_p": 0.2, "max_tokens": 4096},
        },
        "anthropic": {
            "name": "Anthropic",
            "api_url": "https://api.anthropic.com/v1/messages",
            "stream_format": "anthropic",
            "models": ["claude-sonnet-4-6", "claude-opus-4-8"],
            "default_model": "claude-sonnet-4-6",
            "params": {"max_tokens": 4096},
        },
    }

_SUPPORTED_PLATFORMS = _load_ai_models()

# ── API Key 简单混淆（防意外明文泄露，非加密）──
# 前缀 ~ 标记混淆版本，向后兼容未混淆的旧 key.conf
_KEY_OBFUSCATE_PREFIX = "~"

def _obfuscate(plain: str) -> str:
    """简单 XOR + base64 混淆，返回带前缀的编码字符串"""
    import base64
    key = 0xA7
    data = plain.encode("utf-8")
    xored = bytes(b ^ key for b in data)
    return _KEY_OBFUSCATE_PREFIX + base64.b64encode(xored).decode()

def _deobfuscate(encoded: str) -> str:
    """解码混淆字符串，若无前缀则视为明文（向后兼容）"""
    import base64
    if not encoded.startswith(_KEY_OBFUSCATE_PREFIX):
        return encoded  # 旧格式明文
    key = 0xA7
    raw = base64.b64decode(encoded[len(_KEY_OBFUSCATE_PREFIX):])
    return bytes(b ^ key for b in raw).decode("utf-8")

def load_key_conf() -> dict:
    """读取 key.conf，返回 {platform, api_key, model, params} 或空 dict"""
    if not os.path.exists(KEY_CONF_PATH):
        return {}
    try:
        with open(KEY_CONF_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # 自动解码混淆的 API Key
        if "api_key" in data and isinstance(data["api_key"], str):
            data["api_key"] = _deobfuscate(data["api_key"])
        return data
    except Exception:
        return {}

def save_key_conf(platform: str, api_key: str, model: str = "", params: dict = None,
                  api_url: str = "") -> None:
    """写入 key.conf（API Key 自动混淆存储）"""
    os.makedirs(os.path.dirname(KEY_CONF_PATH), exist_ok=True)
    data = {"platform": platform, "api_key": _obfuscate(api_key), "model": model}
    if params:
        data["params"] = params
    if api_url:
        data["api_url"] = api_url
    with open(KEY_CONF_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(KEY_CONF_PATH, 0o600)

def _setup_key_conf_interactive(lang: str = "chinese") -> dict:
    """交互式配置 API 平台和密钥，使用箭头键选择，返回配置 dict 或空"""
    platforms = list(_SUPPORTED_PLATFORMS.keys())
    plat_labels = [_SUPPORTED_PLATFORMS[p]["name"] for p in platforms]

    title = "🔑 选择 AI 平台" if lang == "chinese" else "🔑 Choose AI platform"
    choice = select_option(title, plat_labels, default=plat_labels[0], lang=lang)
    if not choice:
        return {}

    idx = plat_labels.index(choice) if choice in plat_labels else 0
    platform = platforms[idx]
    info = _SUPPORTED_PLATFORMS[platform]

    # 输入 API Key
    key_prompt = f"🔑 输入 {info['name']} API Key" if lang == "chinese" else f"🔑 Enter {info['name']} API Key"
    key = ui_text_input(key_prompt, "", lang)
    if not key:
        return {}

    # 选择模型
    model_prompt = f"选择模型（默认 {info['default_model']}）" if lang == "chinese" else f"Select model (default: {info['default_model']})"
    model_choice = select_option(model_prompt, info["models"], default=info["default_model"], lang=lang)
    model = model_choice if model_choice else info["default_model"]

    # 参数（可选自定义）
    params = dict(info["params"])
    tune = input("自定义参数？(y/N): " if lang == "chinese" else "Customize params? (y/N): ").strip().lower()
    if tune == "y":
        try:
            t = input(f"  temperature [{params.get('temperature', 0.1)}]: ").strip()
            if t:
                params["temperature"] = float(t)
            tp = input(f"  top_p [{params.get('top_p', 0.2)}]: ").strip()
            if tp:
                params["top_p"] = float(tp)
            mt = input(f"  max_tokens [{params.get('max_tokens', 4096)}]: ").strip()
            if mt:
                params["max_tokens"] = int(mt)
        except (ValueError, KeyboardInterrupt, EOFError):
            pass

    save_key_conf(platform, key, model, params)
    console.print(f"✅ {info['name']} — {model}" + (" (自定义参数)" if tune == "y" else ""), style="bold green")
    return {"platform": platform, "api_key": key, "model": model, "params": params}

# ──────────────────── mood.json 情感模拟 ────────────────────
_MOOD_DECAY_HOURS = 10
_MOOD_DIMS = {"happy": "开心", "angry": "愤怒"}
_MOOD_DEFAULT = 0.0
_MOOD_ENABLED_PATH = os.path.join(USER_HOME_DIR, ".config", "onyx", "mood_enabled")

def is_mood_enabled() -> bool:
    """检查情感模块是否启用（默认启用，文件内容为 'false' 时禁用）"""
    try:
        if os.path.exists(_MOOD_ENABLED_PATH):
            with open(_MOOD_ENABLED_PATH, "r") as f:
                return f.read().strip().lower() != "false"
        return True  # 文件不存在默认启用
    except Exception:
        return True

def init_mood():
    """初始化 mood.json（维度均设 0.0，表示初始平稳状态）"""
    os.makedirs(os.path.dirname(MOOD_PATH), exist_ok=True)
    if not os.path.exists(MOOD_PATH):
        dims = {k: _MOOD_DEFAULT for k in _MOOD_DIMS}
        save_mood({"mood": dims, "people": {}})

def load_mood() -> dict:
    """读取 mood.json，10h 无变动自动归零"""
    try:
        with open(MOOD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"mood": {k: _MOOD_DEFAULT for k in _MOOD_DIMS}, "people": {}}
        last_ts = data.get("_updated", 0)
        if last_ts and time.time() - last_ts > _MOOD_DECAY_HOURS * 3600:
            dims = data.get("mood", {})
            for k in _MOOD_DIMS:
                dims[k] = _MOOD_DEFAULT
            data["mood"] = dims
        return data
    except Exception:
        return {"mood": {k: _MOOD_DEFAULT for k in _MOOD_DIMS}, "people": {}}

def save_mood(data: dict):
    """写入 mood.json"""
    os.makedirs(os.path.dirname(MOOD_PATH), exist_ok=True)
    data["_updated"] = time.time()
    with open(MOOD_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def apply_mood_delta(dimension: str, delta: float):
    """调整指定维度 ±N（1~10）"""
    dimension = dimension.lower()
    if dimension not in _MOOD_DIMS:
        return
    data = load_mood()
    dims = data.setdefault("mood", {k: _MOOD_DEFAULT for k in _MOOD_DIMS})
    old = dims.get(dimension, _MOOD_DEFAULT)
    dims[dimension] = round(max(-10.0, min(10.0, old + delta)), 1)
    save_mood(data)

def apply_people_action(action: str, name: str, value: str = ""):
    """处理 [People]: add / Likeability ±N / Perception 描述"""
    data = load_mood()
    people = data.setdefault("people", {})
    if action.lower() == "add":
        if name not in people:
            people[name] = {"likability": 0.0, "perception": ""}
    elif action.lower() == "likeability":
        try:
            delta = float(value)
            p = people.setdefault(name, {"likability": 0.0, "perception": ""})
            p["likability"] = round(max(-10.0, min(10.0, p.get("likability", 0.0) + delta)), 1)
        except ValueError:
            pass
    elif action.lower() == "perception":
        p = people.setdefault(name, {"likability": 0.0, "perception": ""})
        p["perception"] = value
    save_mood(data)

def _render_edit_diff(old_text: str, new_text: str, context_lines: int = 2):
    """渲染彩色 diff：整行底色变色"""
    import difflib, shutil
    old_lines = old_text.split("\n")
    new_lines = new_text.split("\n")
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    _w = shutil.get_terminal_size().columns - 2

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            total = i2 - i1
            if total <= context_lines * 2 + 1:
                show_range = range(i1, i2)
            else:
                show_range = list(range(i1, i1 + context_lines)) + list(range(i2 - context_lines, i2))
                console.print(f"       [dim white]... {total - context_lines * 2} 行未变化 ...[/]")
            for idx in show_range:
                console.print((f"  {idx + 1:>4} │ {old_lines[idx]}").ljust(_w), style="dim white")
            if total > context_lines * 2 + 1:
                continue
        elif op == "delete":
            for idx in range(i1, i2):
                console.print((f"  {idx + 1:>4} │ {old_lines[idx]}").ljust(_w), style="white on red")
        elif op == "replace":
            for idx in range(i1, i2):
                console.print((f"  {idx + 1:>4} │ {old_lines[idx]}").ljust(_w), style="white on blue")
            for idx in range(j1, j2):
                console.print((f"  {idx + 1:>4} │ {new_lines[idx]}").ljust(_w), style="black on yellow")
        elif op == "insert":
            for idx in range(j1, j2):
                console.print((f"  {idx + 1:>4} │ {new_lines[idx]}").ljust(_w), style="black on green")
    console.print(f"  [dim white]────────────────[/]")


def mood_context() -> str:
    """生成 mood 上下文，注入 AI 请求。情感模块禁用时返回空字符串。"""
    if not is_mood_enabled():
        return ""
    data = load_mood()
    dims = data.get("mood", {k: _MOOD_DEFAULT for k in _MOOD_DIMS})
    if not isinstance(dims, dict):
        dims = {k: _MOOD_DEFAULT for k in _MOOD_DIMS}
    people = data.get("people", {})
    if not isinstance(people, dict):
        people = {}
    lines = ["#当前情感状态（0=基线，正数=积极/愤怒，负数极少见）"]
    for k, cn in _MOOD_DIMS.items():
        v = dims.get(k, _MOOD_DEFAULT)
        v_clamped = max(0, min(10, int(v)))
        bar = "█" * v_clamped + "░" * (10 - v_clamped)
        lines.append(f"  {cn}({k}): [{bar}] {v}/10")
    lines.append("  （默默用 [mood]: 维度 ±N 调整，不要在回复中提及）")
    if people:
        lines.append("#人物档案")
        for name, info in people.items():
            if not isinstance(info, dict):
                continue
            l = info.get("likability", 0)
            p = info.get("perception", "")
            lines.append(f"- {name}: 好感度 {l:+} {', ' + p if p else ''}")
    return "\n".join(lines)

# -------------------------- 辅助函数：获取服务器地址 --------------------------
def get_server_url() -> str:
    global SERVER_URL
    if SERVER_URL is not None:
        return SERVER_URL
    
    default_url = "http://localhost:8000"
    try:
        if os.path.exists(SERVER_URL_FILE):
            with open(SERVER_URL_FILE, "r", encoding="utf-8") as f:
                url = f.read().strip()
                if url:
                    SERVER_URL = url.rstrip('/')
                    return SERVER_URL
    except Exception as e:
        lang = get_current_lang()
        prompts = get_prompt_text(lang)
        console.print(prompts["server_url_read_fail"].format(str(e)), style="bold yellow")
    
    SERVER_URL = default_url
    return SERVER_URL

# -------------------------- 辅助函数：获取当前语言配置 --------------------------
def get_current_lang() -> str:
    if os.path.exists(LANGUAGE_CONFIG_PATH):
        try:
            with open(LANGUAGE_CONFIG_PATH, 'r', encoding='utf-8') as f:
                lang = f.read().strip().lower()
            return lang if lang in ["english", "chinese"] else "chinese"
        except Exception:
            return "chinese"
    return "chinese"

def get_prompt_text(lang: str) -> Dict[str, str]:
    if lang == "english":
        return {
            "no_key_found": "⚠️ AI license key not found",
            "set_key_prompt": "Do you want to set the AI license key now? (y/n)：",
            "no_set_exit": "❌ License key not set, program cannot run",
            "input_key_prompt": "Please enter 32-bit AI license key：",
            "key_format_error": "❌ Invalid key format! Must be 32-character string",
            "key_save_success": "✅ License key saved successfully",
            "save_key_fail": "❌ Failed to save key：{}",
            "invalid_input": "❌ Invalid input! Please enter y or n",
            "retry_set_prompt": "Do you want to re-set the AI license key? (y/n)：",
            "key_update_success": "✅ License key updated successfully",
            "verify_fail_retry": "❌ New key is still invalid!",
            "read_key_fail": "❌ Failed to read license: {}",
            "server_url_read_fail": "⚠️ Failed to read server address: {}，using default address",
            "license_verification_fail": "❌ License verification failed: Server returned {}",
            "verification_network_error": "❌ Verification network error：{}",
            "ai_service_not_found": "AI service not found (endpoint {} not available)",
            "license_invalid_or_quota": "AI license invalid or quota exceeded",
            "request_too_frequent": "Request too frequent, please try again later",
            "ai_request_timeout": "AI request timeout (60s)",
            "connection_failed": "Connection failed, check network",
            "retrying": "⚠️ Retrying ({}/{}) in {}s...",
            "esc_ask": "Task complete. Any questions? (Press Enter to continue, Ctrl+C to exit)",
            "esc_hint": "ESC to ask, Enter to continue",
            "user_exit": "Goodbye!",
            "request_failed": "Request failed: {}",
            "parse_response_failed": "Parse response failed: {}",
            "unknown_error": "Unknown error: {}",
            "only_adv_mode_key": "Only adv mode can use -key parameter",
            "incorrect_adv_password": "Incorrect adv password",
        }
    return {
        "no_key_found": "⚠️ 未找到AI许可证密钥",
        "set_key_prompt": "是否立即设置AI许可证密钥？(y/n)：",
        "no_set_exit": "❌ 未设置许可证密钥，程序无法运行",
        "input_key_prompt": "请输入32位AI许可证密钥：",
        "key_format_error": "❌ 密钥格式错误！必须是32位字符串",
        "key_save_success": "✅ 许可证密钥已保存",
        "save_key_fail": "❌ 保存密钥失败：{}",
        "invalid_input": "❌ 无效输入！请输入y或n",
        "retry_set_prompt": "是否重新设置AI许可证密钥？(y/n)：",
        "key_update_success": "✅ 许可证密钥已更新",
        "verify_fail_retry": "❌ 新密钥仍无效！",
        "read_key_fail": "❌ 读取许可证失败：{}",
        "server_url_read_fail": "⚠️ 读取服务器地址失败：{}，使用默认地址",
        "license_verification_fail": "❌ 许可证验证失败：服务器返回{}",
        "verification_network_error": "❌ 验证网络错误：{}",
        "ai_service_not_found": "AI服务未找到（接口 {} 不可用）",
        "license_invalid_or_quota": "AI许可证无效或额度已用完",
        "request_too_frequent": "请求过于频繁，请稍后再试",
        "ai_request_timeout": "AI请求超时 (60秒)",
        "connection_failed": "连接失败，请检查网络",
        "retrying": "⚠️ 正在重试 ({}/{})，{}秒后...",
        "esc_ask": "任务完成。有什么问题吗？(按 Enter 继续，Ctrl+C 退出)",
        "esc_hint": "ESC 提问, Enter 继续",
        "user_exit": "再见！",
        "request_failed": "请求失败：{}",
        "parse_response_failed": "解析响应失败：{}",
        "unknown_error": "未知错误：{}",
        "only_adv_mode_key": "仅adv模式可使用 -key 参数",
        "incorrect_adv_password": "adv密码错误",
    }

# -------------------------- 2. 许可证验证系统 --------------------------
def load_ai_key() -> Optional[str]:
    lang = get_current_lang()
    prompts = get_prompt_text(lang)
    
    if not os.path.exists(AI_KEY_PATH):
        console.print(prompts["no_key_found"], style="bold yellow")
        while True:
            choice = input(prompts["set_key_prompt"] + " ").strip().lower()
            if choice == "n":
                console.print(prompts["no_set_exit"], style="bold red")
                sys.exit(1)
            elif choice == "y":
                key = input(prompts["input_key_prompt"] + " ").strip()
                if len(key) != 32:
                    console.print(prompts["key_format_error"], style="bold red")
                    continue
                os.makedirs(os.path.dirname(AI_KEY_PATH), exist_ok=True)
                try:
                    with open(AI_KEY_PATH, "w", encoding="utf-8") as f:
                        f.write(key)
                    os.chmod(AI_KEY_PATH, 0o400)
                    console.print(prompts["key_save_success"], style="bold green")
                    return key
                except Exception as e:
                    console.print(prompts["save_key_fail"].format(str(e)), style="bold red")
                    sys.exit(1)
            else:
                console.print(prompts["invalid_input"], style="bold red")
    
    try:
        with open(AI_KEY_PATH, "r", encoding="utf-8") as f:
            key = f.read().strip()
        if len(key) != 32:
            console.print(prompts["key_format_error"], style="bold red")
            while True:
                choice = input(prompts["retry_set_prompt"] + " ").strip().lower()
                if choice == "n":
                    sys.exit(1)
                elif choice == "y":
                    new_key = input(prompts["input_key_prompt"] + " ").strip()
                    if len(new_key) == 32:
                        with open(AI_KEY_PATH, "w", encoding="utf-8") as f:
                            f.write(new_key)
                        os.chmod(AI_KEY_PATH, 0o400)
                        console.print(prompts["key_update_success"], style="bold green")
                        return new_key
                    else:
                        console.print(prompts["key_format_error"], style="bold red")
                else:
                    console.print(prompts["invalid_input"], style="bold red")
        return key
    except Exception as e:
        console.print(prompts["read_key_fail"].format(str(e)), style="bold red")
        sys.exit(1)

def verify_ai_key(key: str) -> bool:
    server_url = get_server_url()
    lang = get_current_lang()
    prompts = get_prompt_text(lang)
    
    try:
        headers = {"X-AI-Key": key}
        response = requests.get(
            f"{server_url}/api/ai/verify",
            headers=headers,
            timeout=80
        )
        if response.status_code == 200:
            return response.json().get("valid", False)
        else:
            err_msg = prompts["license_verification_fail"].format(response.status_code)
            console.print(err_msg, style="bold red")
            return False
    except Exception as e:
        err_msg = prompts["verification_network_error"].format(str(e))
        console.print(err_msg, style="bold red")
        while True:
            choice = input(prompts["retry_set_prompt"] + " ").strip().lower()
            if choice == "n":
                console.print(prompts["no_set_exit"], style="bold red")
                sys.exit(1)
            elif choice == "y":
                new_key = input(prompts["input_key_prompt"] + " ").strip()
                if len(new_key) != 32:
                    console.print(prompts["key_format_error"], style="bold red")
                    continue
                os.makedirs(os.path.dirname(AI_KEY_PATH), exist_ok=True)
                with open(AI_KEY_PATH, "w", encoding="utf-8") as f:
                    f.write(new_key)
                console.print(prompts["key_update_success"], style="bold green")
                os.chmod(AI_KEY_PATH, 0o400)
                return verify_ai_key(new_key)
            else:
                console.print(prompts["invalid_input"], style="bold red")

# -------------------------- 3. 命令缓存配置 --------------------------
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

# -------------------------- 4. 聊天记忆管理函数（JSON格式）--------------------------
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

# -------------------------- 5. 会话管理函数 --------------------------
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

# -------------------------- 6. 解析SSE结构化响应（新版：支持[TXT]...[TXT:DONE] / [plan]...[plan:done] / [tool:...]...[tool:...:done]）--------------------------
def parse_sse_structured_response(sse_text: str) -> Dict[str, Any]:
    """
    解析服务端返回的SSE结构化文本。

    新版格式（无 @@SHELL，直接 SSE 事件）:
      [TXT]:内容行         — 服务端逐行包装的 AI 回答（去掉 [TXT]: 前缀后重组）
      [ANSWER]:yes/no      — 服务端直接给出的 answer 字段
      [ANALYSIS]:文本      — 服务端直接给出的分析
      [plan]...[plan:done] — 多行计划块
      [tool:NAME PARAMS]...[tool:NAME:done] — 工具调用块

    兼容旧版 @@SHELL 格式:
      @@SHELL
      >>>>>>>>>>
      CMD1
      >>>>>>>>>>
      [ANALYSIS]:...
      [TXT]...[TXT:DONE]
    """
    result = {
        "answer": "no",
        "ask": "",
        "analysis": "",
        "txt": "",
        "tag": "",
        "memory": "",
        "plan": "",
        "sleep": None,
        "class": "1",
        "prompt": "",  # [PROMPT]: 写入 .ai_s/onyx_ai.md 最高指示
        "tool_calls": [],
    }
    commands = []

    lines = sse_text.split('\n')

    # ── 第一遍：收集 [TXT]: 行，识别直接字段 ──
    txt_raw_lines = []        # 去掉 [TXT]: 前缀后的 AI 原始内容
    direct_fields = {}        # 服务端直接给出的字段
    has_txt_wrapped = False   # 是否检测到新版 [TXT]: 包装格式
    plan_lines_raw = []       # [plan] 块内容
    in_plan = False

    i = 0
    while i < len(lines):
        stripped = lines[i].rstrip('\r').strip()
        i += 1

        if not stripped:
            # 空行：如果在 plan 块中则保留
            if in_plan:
                plan_lines_raw.append('')
            continue

        # ── 跳过 SSE 协议行和调试噪音 ──
        if stripped.startswith('event: '):
            continue
        if stripped.startswith('[STATUS]:'):
            continue
        if stripped.startswith('[DEBUG]:'):
            continue

        # ── 新版 [TXT]: 逐行包装 ──
        # 注意: [TXT:DONE] 也以 [TXT]: 开头，必须排除
        if stripped.startswith('[TXT]:') and not stripped.startswith('[TXT:DONE]'):
            has_txt_wrapped = True
            content = stripped[6:]  # 去掉 "[TXT]:" 前缀（6字符: [ T X T ] : ）
            txt_raw_lines.append(content)
            continue

        # ── [TXT:DONE] 终止标记（结束 [TXT]: 或 [TXT] 块，拆分粘连字段）──
        if stripped.startswith('[TXT:DONE]'):
            remainder = stripped[10:]  # 去掉 "[TXT:DONE]"（10 字符）
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── 原始 [TXT]...[TXT:DONE] 块（服务端不包装前缀时的原始 AI 格式）──
        # 支持多种变体：
        #   1. [TXT] 独占一行 → 后续行直到 [TXT:DONE]
        #   2. [TXT]content 同行 → content 作为首行，后续行直到 [TXT:DONE]
        #   3. [TXT]content[TXT:DONE] 同行 → 整行拆分
        #   4. [TXT:DONE] 与其他标记粘连（如 [TXT:DONE][ANSWER]yes）→ 拆分重注
        if stripped == '[TXT]' or (stripped.startswith('[TXT]') and not stripped.startswith('[TXT:DONE]') and not stripped.startswith('[TXT]:')):
            has_txt_wrapped = True
            if stripped != '[TXT]':
                inline_content = stripped[5:]  # 去掉 [TXT] 前缀
                # 检查同行是否包含 [TXT:DONE]
                done_pos = inline_content.find('[TXT:DONE]')
                if done_pos >= 0:
                    # 同行包含 [TXT:DONE] → 拆分
                    txt_part = inline_content[:done_pos]
                    remainder = inline_content[done_pos + 10:]  # 去掉 "[TXT:DONE]" (10 chars)
                    if txt_part:
                        txt_raw_lines.append(txt_part)
                    if remainder:
                        # 粘连的字段标记插入回列表
                        lines.insert(i, remainder)
                else:
                    # 不含 [TXT:DONE] → 首行内容，继续扫描后续行
                    if inline_content:
                        txt_raw_lines.append(inline_content)
                    while i < len(lines):
                        next_line = lines[i].rstrip('\r').strip()
                        i += 1
                        done_pos = next_line.find('[TXT:DONE]')
                        if done_pos >= 0:
                            # [TXT:DONE] 在行内（可能在开头、中间或末尾）
                            if done_pos > 0:
                                txt_raw_lines.append(next_line[:done_pos])
                            remainder = next_line[done_pos + 10:]
                            if remainder:
                                lines.insert(i, remainder)
                            break
                        txt_raw_lines.append(next_line)
            else:
                # [TXT] 独占一行 → 扫描后续行
                while i < len(lines):
                    next_line = lines[i].rstrip('\r').strip()
                    i += 1
                    done_pos = next_line.find('[TXT:DONE]')
                    if done_pos >= 0:
                        if done_pos > 0:
                            txt_raw_lines.append(next_line[:done_pos])
                        remainder = next_line[done_pos + 10:]
                        if remainder:
                            lines.insert(i, remainder)
                        break
                    txt_raw_lines.append(next_line)
            continue

        # ── [PROMPT]...[PROMPT:DONE] 多行块（仅当 [PROMPT] 独占一行或同行含 [PROMPT:DONE]）──
        if stripped == '[PROMPT]':
            prompt_lines = []
            while i < len(lines):
                next_line = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_line.find('[PROMPT:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        prompt_lines.append(next_line[:done_pos])
                    remainder = next_line[done_pos + 13:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                prompt_lines.append(next_line)
            direct_fields['PROMPT'] = '\n'.join(prompt_lines).strip()
            continue
        if stripped.startswith('[PROMPT]') and not stripped.startswith('[PROMPT:DONE]') and not stripped.startswith('[PROMPT]:') and '[PROMPT:DONE]' in stripped:
            # 同行内联块：[PROMPT]content[PROMPT:DONE][...]
            inline = stripped[8:]
            done_pos = inline.find('[PROMPT:DONE]')
            if done_pos > 0:
                direct_fields['PROMPT'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 13:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [TAG]...[TAG:DONE] 多行块 ──
        if stripped == '[TAG]':
            tag_lines = []
            while i < len(lines):
                next_line = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_line.find('[TAG:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        tag_lines.append(next_line[:done_pos])
                    remainder = next_line[done_pos + 10:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                tag_lines.append(next_line)
            direct_fields['TAG'] = '\n'.join(tag_lines).strip()
            continue
        if stripped.startswith('[TAG]') and not stripped.startswith('[TAG:DONE]') and not stripped.startswith('[TAG]:') and '[TAG:DONE]' in stripped:
            inline = stripped[4:]
            done_pos = inline.find('[TAG:DONE]')
            if done_pos > 0:
                direct_fields['TAG'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 10:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [ANALYSIS]...[ANALYSIS:DONE] 多行块 ──
        if stripped == '[ANALYSIS]':
            analysis_lines = []
            while i < len(lines):
                next_line = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_line.find('[ANALYSIS:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        analysis_lines.append(next_line[:done_pos])
                    remainder = next_line[done_pos + 15:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                analysis_lines.append(next_line)
            direct_fields['ANALYSIS'] = '\n'.join(analysis_lines).strip()
            continue
        if stripped.startswith('[ANALYSIS]') and not stripped.startswith('[ANALYSIS:DONE]') and not stripped.startswith('[ANALYSIS]:') and '[ANALYSIS:DONE]' in stripped:
            inline = stripped[10:]
            done_pos = inline.find('[ANALYSIS:DONE]')
            if done_pos > 0:
                direct_fields['ANALYSIS'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 15:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [PLAN]...[PLAN:DONE] 多行块（新大写格式）──
        if stripped == '[PLAN]':
            plan_lines_new = []
            while i < len(lines):
                next_line = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_line.find('[PLAN:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        plan_lines_new.append(next_line[:done_pos])
                    remainder = next_line[done_pos + 11:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                plan_lines_new.append(next_line)
            result['plan'] = '\n'.join(plan_lines_new).strip()
            continue
        if stripped.startswith('[PLAN]') and not stripped.startswith('[PLAN:DONE]') and '[PLAN:DONE]' in stripped:
            inline = stripped[6:]
            done_pos = inline.find('[PLAN:DONE]')
            if done_pos > 0:
                result['plan'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 11:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [PROMPT:DONE] / [ANALYSIS:DONE] / [PLAN:DONE] / [TAG:DONE] 独立终止标记 ──
        if stripped.startswith('[PROMPT:DONE]'):
            remainder = stripped[13:]
            if remainder:
                lines.insert(i, remainder)
            continue
        if stripped.startswith('[ANALYSIS:DONE]'):
            remainder = stripped[15:]
            if remainder:
                lines.insert(i, remainder)
            continue
        if stripped.startswith('[PLAN:DONE]'):
            remainder = stripped[11:]
            if remainder:
                lines.insert(i, remainder)
            continue
        if stripped.startswith('[TAG:DONE]'):
            remainder = stripped[10:]
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [mood]: happy +0.1 / [mood]: angry -0.2 ──
        mood_match = re.match(r'^\[mood\]:\s*(\S+)\s+([+-]\d+(?:\.\d+)?)', stripped)
        if mood_match:
            try:
                apply_mood_delta(mood_match.group(1), float(mood_match.group(2)))
            except ValueError:
                pass
            continue

        # ── [PEOPLE]:add 人名 / Likeability 人名 ±N / Perception 人名 描述 ──
        people_match = re.match(r'^\[PEOPLE\]:\s*(\S+)\s+(.+)', stripped)
        if people_match:
            action = people_match.group(1)
            rest = people_match.group(2).strip()
            if action.lower() == "add":
                apply_people_action("add", rest)
            elif action.lower() == "likeability":
                parts = rest.rsplit(None, 1)
                if len(parts) == 2:
                    apply_people_action("likeability", parts[0], parts[1])
            elif action.lower() == "perception":
                parts = rest.split(None, 1)
                if len(parts) == 2:
                    apply_people_action("perception", parts[0], parts[1])
            continue

        # ── 直接字段标记 [ANSWER]: / [ANALYSIS]: / ... ──
        field_match = re.match(r'^\[(ANSWER|ANALYSIS|ASK|MEMORY|TAG|CLASS|SLEEP|PROMPT)\]:', stripped)
        if field_match:
            field_name = field_match.group(1)
            field_value = stripped[field_match.end():].strip()
            direct_fields[field_name] = field_value
            continue

        # ── 无冒号字段标记：[ANSWER]yes / [PROMPT]text / [TAG]text ──
        # 处理 AI 输出 [FIELD]value 同行格式（省略冒号的情况）
        field_no_colon = re.match(r'^\[(ANSWER|ANALYSIS|PROMPT|TAG|MEMORY|CLASS|SLEEP)\]\s*(.*)', stripped)
        if field_no_colon:
            field_name = field_no_colon.group(1)
            field_value = field_no_colon.group(2).strip()
            if field_value:
                direct_fields[field_name] = field_value
                continue
            # 无值 → 当作多行字段开始标记，交给下面的多行字段处理器
            # (注意：不 continue，让代码自然流入多行字段检测)

        # ── 多行字段（无冒号）：[FIELD]\nvalue 格式 ──
        field_multi = re.match(r'^\[(ANALYSIS|ANSWER|TAG|MEMORY|CLASS|SLEEP|PROMPT)\]$', stripped)
        if field_multi:
            field_name = field_multi.group(1)
            value_lines = []
            while i < len(lines):
                next_stripped = lines[i].rstrip('\r').strip()
                if not next_stripped or next_stripped.startswith('['):
                    break
                value_lines.append(lines[i])
                i += 1
            direct_fields[field_name] = '\n'.join(value_lines).strip()
            continue

        # ── [plan] 多行块 ──
        if stripped == '[plan]':
            in_plan = True
            continue
        if in_plan:
            if stripped == '[plan:done]':
                in_plan = False
                result['plan'] = '\n'.join(plan_lines_raw).strip()
                continue
            plan_lines_raw.append(stripped)
            continue

        # ── [tool:NAME PARAMS] 多行块 ──
        tool_match = re.match(r'^\[tool:(\S+)\s*(.*)\]$', stripped)
        if tool_match:
            tool_name = tool_match.group(1)
            tool_params = tool_match.group(2).strip()
            tool_body_lines = []
            done_marker = f'[tool:{tool_name}:done]'
            while i < len(lines) and lines[i].rstrip('\r').strip() != done_marker:
                tool_body_lines.append(lines[i])
                i += 1
            result["tool_calls"].append({
                "name": tool_name,
                "params_str": tool_params,
                "body": '\n'.join(tool_body_lines).strip(),
            })
            i += 1  # 跳过 done marker
            continue

        # ── 旧版 @@SHELL 兼容 ──
        if stripped.startswith('@@SHELL'):
            # 进入旧版解析模式
            # 处理同行粘连：@@SHELL>>>>>>>>>>cmd>>>>>>>>>> → 拆分剩余
            remainder = stripped[7:]  # 去掉 "@@SHELL"
            if remainder:
                lines.insert(i, remainder)
            legacy = _parse_legacy_shell(lines, i)
            commands.extend(legacy.get('commands', []))
            for k, v in legacy.get('fields', {}).items():
                if k not in direct_fields:
                    direct_fields[k] = v
            result["tool_calls"].extend(legacy.get('tool_calls', []))
            if legacy.get('plan'):
                result['plan'] = legacy['plan']
            break  # @@SHELL 之后的内容由旧版解析器处理完毕

        # ── 旧版分隔符（跳过） ──
        if stripped == '>>>>>>>>>>':
            continue

        # ── 未匹配任何已知模式的行 → 归入文本 ──
        # 包括裸文本行和以 [ 开头但不属于任何已知标记的行（如 [TXT:DONE] 粘连残留）
        if stripped and not stripped.startswith('event:'):
            txt_raw_lines.append(stripped)

    # ── 第二遍：从 [TXT]: 包装内容中解析 AI 原始响应 ──
    if has_txt_wrapped and txt_raw_lines:
        raw_text = '\n'.join(txt_raw_lines)
        inner = _parse_ai_raw_response(raw_text)
        # AI 内嵌的字段作为默认值，服务端直接字段会覆盖
        if inner.get('txt') and not result['txt']:
            result['txt'] = inner['txt']
        # 兜底：AI 未按任何格式标记输出时（纯文本），raw_text 直接作为 txt
        if not result['txt'] and raw_text.strip():
            result['txt'] = raw_text.strip()
        if inner.get('analysis') and not direct_fields.get('ANALYSIS'):
            result['analysis'] = inner['analysis']
        if inner.get('answer') and not direct_fields.get('ANSWER'):
            result['answer'] = inner['answer']
        # ── 兜底：raw_text 中可能包含非行首的 [ANSWER]yes/no（AI 常见错误）──
        if not direct_fields.get('ANSWER') and not result.get('answer'):
            m_ans = re.search(r'\[ANSWER\](yes|no)', raw_text)
            if m_ans:
                result['answer'] = m_ans.group(1)
        if inner.get('ask') and not direct_fields.get('ASK'):
            result['ask'] = inner['ask']
        if inner.get('tag') and not direct_fields.get('TAG'):
            result['tag'] = inner['tag']
        if inner.get('memory') and not direct_fields.get('MEMORY'):
            result['memory'] = inner['memory']
        if inner.get('plan') and not result['plan']:
            result['plan'] = inner['plan']
        if inner.get('class') and not direct_fields.get('CLASS'):
            result['class'] = inner['class']
        result["tool_calls"].extend(inner.get('tool_calls', []))

    # ── 纯文本兜底：AI 完全没输出任何格式标记时，全部文本当作 TXT ──
    if not result['txt'] and txt_raw_lines:
        raw_text = '\n'.join(txt_raw_lines).strip()
        if raw_text:
            result['txt'] = raw_text
    # 如果连 txt_raw_lines 都没有（比如只有 @@SHELL），尝试从 full_content 提取
    if not result['txt'] and not txt_raw_lines:
        bare = '\n'.join(
            l for l in sse_text.split('\n')
            if l.strip() and not l.strip().startswith('[') and not l.strip().startswith('@@')
            and not l.strip().startswith('event:') and l.strip() != '>>>>>>>>>>'
        ).strip()
        if bare:
            result['txt'] = bare

    # ── ANSWER 兜底：如果 AI 没有显式输出 [ANSWER]，根据内容推断 ──
    # 规则：有命令或工具调用 → no（需要继续执行）；纯文本 → yes（对话结束）
    if not direct_fields.get('ANSWER') and not result.get('answer'):
        has_pending = bool(commands or result.get('tool_calls') or result.get('plan') or result.get('ask'))
        result['answer'] = 'no' if has_pending else 'yes'

    # ── 填充直接字段（服务端直接给出的优先级最高） ──
    field_mapping = {
        'ANSWER': 'answer',
        'ANALYSIS': 'analysis',
        'ASK': 'ask',
        'MEMORY': 'memory',
        'TAG': 'tag',
        'CLASS': 'class',
        'SLEEP': 'sleep',
        'PROMPT': 'prompt',
    }
    for sse_field, result_key in field_mapping.items():
        if sse_field in direct_fields:
            val = direct_fields[sse_field]
            if sse_field == 'SLEEP':
                try:
                    result[result_key] = int(val)
                except (ValueError, TypeError):
                    result[result_key] = None
            else:
                result[result_key] = val

    # ── 旧版命令兼容 ──
    for idx, cmd in enumerate(commands, 1):
        result[f"cmd{idx}"] = cmd

    return result


def _parse_ai_raw_response(raw_text: str) -> Dict[str, Any]:
    """
    解析 AI 原始响应文本，提取 [TXT]...[TXT:DONE]、[ANALYSIS]:、
    [ANSWER]:、[plan]...[plan:done]、[tool:...] 等内嵌标记。
    """
    result = {
        "answer": "",
        "ask": "",
        "analysis": "",
        "txt": "",
        "tag": "",
        "memory": "",
        "plan": "",
        "class": "",
        "tool_calls": [],
    }

    lines = raw_text.split('\n')
    i = 0
    while i < len(lines):
        stripped = lines[i].rstrip('\r').strip()
        i += 1

        # [TXT]...[TXT:DONE] 块（支持 [TXT]content 同行、[TXT:DONE] 行内/行首/粘连）
        if stripped == '[TXT]' or (stripped.startswith('[TXT]') and not stripped.startswith('[TXT:DONE]') and not stripped.startswith('[TXT]:')):
            txt_lines = []
            if stripped != '[TXT]':
                inline = stripped[5:]
                done_pos = inline.find('[TXT:DONE]')
                if done_pos >= 0:
                    # 同行包含 [TXT:DONE] → 拆分
                    if done_pos > 0:
                        txt_lines.append(inline[:done_pos])
                    remainder = inline[done_pos + 10:]
                    if remainder:
                        lines.insert(i, remainder)
                else:
                    if inline:
                        txt_lines.append(inline)
                    while i < len(lines):
                        next_stripped = lines[i].rstrip('\r').strip()
                        i += 1
                        done_pos = next_stripped.find('[TXT:DONE]')
                        if done_pos >= 0:
                            if done_pos > 0:
                                txt_lines.append(next_stripped[:done_pos])
                            remainder = next_stripped[done_pos + 10:]
                            if remainder:
                                lines.insert(i, remainder)
                            break
                        txt_lines.append(lines[i - 1])
            else:
                while i < len(lines):
                    next_stripped = lines[i].rstrip('\r').strip()
                    i += 1
                    done_pos = next_stripped.find('[TXT:DONE]')
                    if done_pos >= 0:
                        if done_pos > 0:
                            txt_lines.append(next_stripped[:done_pos])
                        remainder = next_stripped[done_pos + 10:]
                        if remainder:
                            lines.insert(i, remainder)
                        break
                    txt_lines.append(lines[i - 1])
            result['txt'] = '\n'.join(txt_lines).strip()
            continue

        # [TXT:DONE] 独立终止标记（处理 [TXT]: 收集后的粘连残留）
        if stripped.startswith('[TXT:DONE]'):
            remainder = stripped[10:]
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [PROMPT]...[PROMPT:DONE] 多行块（仅当 [PROMPT] 独占一行或同行含 [PROMPT:DONE]）──
        if stripped == '[PROMPT]':
            prompt_lines = []
            while i < len(lines):
                next_stripped = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_stripped.find('[PROMPT:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        prompt_lines.append(next_stripped[:done_pos])
                    remainder = next_stripped[done_pos + 13:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                prompt_lines.append(lines[i - 1])
            result['prompt'] = '\n'.join(prompt_lines).strip()
            continue
        if stripped.startswith('[PROMPT]') and not stripped.startswith('[PROMPT:DONE]') and not stripped.startswith('[PROMPT]:') and '[PROMPT:DONE]' in stripped:
            inline = stripped[8:]
            done_pos = inline.find('[PROMPT:DONE]')
            if done_pos > 0:
                result['prompt'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 13:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [TAG]...[TAG:DONE] 多行块 ──
        if stripped == '[TAG]':
            tag_lines = []
            while i < len(lines):
                next_stripped = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_stripped.find('[TAG:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        tag_lines.append(next_stripped[:done_pos])
                    remainder = next_stripped[done_pos + 10:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                tag_lines.append(lines[i - 1])
            result['tag'] = '\n'.join(tag_lines).strip()
            continue
        if stripped.startswith('[TAG]') and not stripped.startswith('[TAG:DONE]') and not stripped.startswith('[TAG]:') and '[TAG:DONE]' in stripped:
            inline = stripped[4:]
            done_pos = inline.find('[TAG:DONE]')
            if done_pos > 0:
                result['tag'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 10:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [ANALYSIS]...[ANALYSIS:DONE] 多行块 ──
        if stripped == '[ANALYSIS]':
            analysis_lines = []
            while i < len(lines):
                next_stripped = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_stripped.find('[ANALYSIS:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        analysis_lines.append(next_stripped[:done_pos])
                    remainder = next_stripped[done_pos + 15:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                analysis_lines.append(lines[i - 1])
            result['analysis'] = '\n'.join(analysis_lines).strip()
            continue
        if stripped.startswith('[ANALYSIS]') and not stripped.startswith('[ANALYSIS:DONE]') and not stripped.startswith('[ANALYSIS]:') and '[ANALYSIS:DONE]' in stripped:
            inline = stripped[10:]
            done_pos = inline.find('[ANALYSIS:DONE]')
            if done_pos > 0:
                result['analysis'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 15:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [PLAN]...[PLAN:DONE] 多行块（大写新格式）──
        if stripped == '[PLAN]':
            plan_lines_new = []
            while i < len(lines):
                next_stripped = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_stripped.find('[PLAN:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        plan_lines_new.append(next_stripped[:done_pos])
                    remainder = next_stripped[done_pos + 11:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                plan_lines_new.append(lines[i - 1])
            result['plan'] = '\n'.join(plan_lines_new).strip()
            continue
        if stripped.startswith('[PLAN]') and not stripped.startswith('[PLAN:DONE]') and '[PLAN:DONE]' in stripped:
            inline = stripped[6:]
            done_pos = inline.find('[PLAN:DONE]')
            if done_pos > 0:
                result['plan'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 11:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # [PROMPT:DONE] / [ANALYSIS:DONE] / [PLAN:DONE] / [TAG:DONE] 独立终止标记
        if stripped.startswith('[PROMPT:DONE]'):
            remainder = stripped[13:]
            if remainder:
                lines.insert(i, remainder)
            continue
        if stripped.startswith('[ANALYSIS:DONE]'):
            remainder = stripped[15:]
            if remainder:
                lines.insert(i, remainder)
            continue
        if stripped.startswith('[PLAN:DONE]'):
            remainder = stripped[11:]
            if remainder:
                lines.insert(i, remainder)
            continue
        if stripped.startswith('[TAG:DONE]'):
            remainder = stripped[10:]
            if remainder:
                lines.insert(i, remainder)
            continue
        if stripped.startswith('[PLAN:DONE]'):
            remainder = stripped[11:]
            if remainder:
                lines.insert(i, remainder)
            continue

        # [plan]...[plan:done] 块（旧小写格式，兼容保留）
        if stripped == '[plan]':
            plan_lines = []
            while i < len(lines) and lines[i].rstrip('\r').strip() != '[plan:done]':
                plan_lines.append(lines[i])
                i += 1
            result['plan'] = '\n'.join(plan_lines).strip()
            i += 1
            continue

        # [tool:NAME PARAMS]...[tool:NAME:done] 块
        tool_match = re.match(r'^\[tool:(\S+)\s*(.*)\]$', stripped)
        if tool_match:
            tool_name = tool_match.group(1)
            tool_params = tool_match.group(2).strip()
            tool_body_lines = []
            done_marker = f'[tool:{tool_name}:done]'
            while i < len(lines) and lines[i].rstrip('\r').strip() != done_marker:
                tool_body_lines.append(lines[i])
                i += 1
            result['tool_calls'].append({
                'name': tool_name,
                'params_str': tool_params,
                'body': '\n'.join(tool_body_lines).strip(),
            })
            i += 1
            continue

        # 单行字段（带冒号）— TXT 也支持冒号格式兼容 AI 简写
        fm = re.match(r'^\[(ANALYSIS|ANSWER|ASK|MEMORY|TAG|CLASS|SLEEP|PROMPT|TXT)\]:', stripped)
        if fm:
            name = fm.group(1)
            value = stripped[fm.end():].strip()
            key = name.lower()
            result[key] = value
            continue

        # 无冒号字段标记：[ANSWER]yes / [PROMPT]text ──
        fm_no_colon = re.match(r'^\[(ANSWER|ANALYSIS|PROMPT|TAG|MEMORY|CLASS|SLEEP)\]\s*(.*)', stripped)
        if fm_no_colon:
            name = fm_no_colon.group(1)
            value = fm_no_colon.group(2).strip()
            if value:
                key = name.lower()
                result[key] = value
                continue
            # 无值 → 交给多行字段处理

        # 多行字段（无冒号）：[FIELD]\nvalue 格式
        fm_multi = re.match(r'^\[(ANALYSIS|ANSWER|TAG|MEMORY|CLASS|SLEEP|PROMPT)\]$', stripped)
        if fm_multi:
            name = fm_multi.group(1)
            value_lines = []
            while i < len(lines):
                next_stripped = lines[i].rstrip('\r').strip()
                if not next_stripped or next_stripped.startswith('['):
                    break
                value_lines.append(lines[i])
                i += 1
            key = name.lower()
            result[key] = '\n'.join(value_lines).strip()
            continue

    return result


def _parse_legacy_shell(lines: List[str], start_i: int) -> Dict[str, Any]:
    """
    解析 @@SHELL 格式。

    规则：
      - 一个 @@SHELL 块 = 一个命令（可多行，如 if/fi、for/done）
      - 多个 @@SHELL 块 = 多个命令
      - >>>>>>>>>> 之间的内容为命令体，不支持用多个 >>>>>>>>>> 分割命令
      - [ 开头的行（DEBUG、标记等）跳过
    """
    result = {'commands': [], 'fields': {}, 'tool_calls': [], 'plan': ''}
    i = start_i
    cmd_lines = None  # 当前正在收集的命令行

    def _flush_cmd():
        nonlocal cmd_lines
        if cmd_lines is not None:
            cmd = '\n'.join(cmd_lines).strip()
            if cmd:
                result['commands'].append(cmd)
            cmd_lines = None

    while i < len(lines):
        stripped = lines[i].rstrip('\r').strip()
        i += 1

        # @@SHELL 开始新块 → 先提交上一个命令
        if stripped.startswith('@@SHELL'):
            _flush_cmd()
            # 同行粘连：@@SHELL>>>>>>>>>> → 跳过 @@SHELL 前缀
            remainder = stripped[7:]
            if remainder:
                lines.insert(i, remainder)
            continue

        # >>>>>>>>>> 分隔符：翻转收集状态
        if stripped.startswith('>>>>>>>>>>'):
            if cmd_lines is None:
                cmd_lines = []
            else:
                _flush_cmd()
            continue

        # 正在收集中：跳过 [ 开头的行，收集其他行
        if cmd_lines is not None:
            if stripped and not stripped.startswith('['):
                cmd_lines.append(stripped)
            continue

        # 多行块（非收集状态）
        if stripped == '[TXT]':
            txt_lines = []
            while i < len(lines) and lines[i].rstrip('\r').strip() != '[TXT:DONE]':
                txt_lines.append(lines[i])
                i += 1
            result['fields']['TXT'] = '\n'.join(txt_lines).strip()
            i += 1
            continue

        if stripped == '[plan]':
            plan_lines = []
            while i < len(lines) and lines[i].rstrip('\r').strip() != '[plan:done]':
                plan_lines.append(lines[i])
                i += 1
            result['plan'] = '\n'.join(plan_lines).strip()
            i += 1
            continue

        tool_match = re.match(r'^\[tool:(\S+)\s*(.*)\]$', stripped)
        if tool_match:
            tool_name = tool_match.group(1)
            tool_params = tool_match.group(2).strip()
            tool_body_lines = []
            done_marker = f'[tool:{tool_name}:done]'
            while i < len(lines) and lines[i].rstrip('\r').strip() != done_marker:
                tool_body_lines.append(lines[i])
                i += 1
            result['tool_calls'].append({
                'name': tool_name,
                'params_str': tool_params,
                'body': '\n'.join(tool_body_lines).strip(),
            })
            i += 1
            continue

        fm = re.match(r'^\[(ANALYSIS|ANSWER|ASK|MEMORY|TAG|CLASS|SLEEP)\]:', stripped)
        if fm:
            name = fm.group(1)
            value = stripped[fm.end():].strip()
            result['fields'][name] = value
            continue

    _flush_cmd()
    return result

# -------------------------- 7. AI API 调用（SSE模式）-------------------------
def call_ai_api_sse(question: str = "", type: Optional[str] = None, new_key: Optional[str] = None, 
                    debug_mode: bool = False, onyx_module=None, mode: str = "normal", times: int = 1,
                    ai_tools_prompt: str = "", on_content: Optional[Callable[[str], None]] = None,
                    on_tool_call: Optional[Callable[[str], None]] = None,
                    on_reasoning: Optional[Callable[[str], None]] = None,
                    user_home_dir: str = None,
                    tools: Optional[List[Dict]] = None,
                    messages: Optional[List[Dict]] = None) -> Dict[str, Any]:
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
                tool_list = sorted(set(tool_list))  # sort: 稳定顺序=可缓存
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
    
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    system_label = "System" if lang == "english" else "系统"
    env_label = "Environment" if lang == "english" else "环境"
    user_label = "User" if lang == "english" else "用户"
    permission_label = "Permission" if lang == "english" else "权限"
    workdir_label = "Working directory" if lang == "english" else "工作目录"
    language_label = "Language" if lang == "english" else "语言"
    time_label = "Current time" if lang == "english" else "当前时间"
    tools_label = "Available tools" if lang == "english" else "可用工具列表"
    task_label = "Task" if lang == "english" else "任务"
    
    permission_value = "root administrator" if USER == "root" else "regular user"
    permission_value_cn = "root管理员" if USER == "root" else "普通用户"
    current_shell = os.environ.get("SHELL", "unknown")
    onyx_mode = "unknown"
    if onyx_module and hasattr(onyx_module, "user_mode"):
        onyx_mode = onyx_module.user_mode.current_mode

    # 加载 .ai_s/onyx_ai.md（最高指示/持久记忆）
    # 优先使用 user_home_dir（Onyx 虚拟家目录），回退到 OS 真实家目录
    onyx_ai_prompt = ""
    try:
        _prompt_home = user_home_dir if user_home_dir else os.path.expanduser("~")
        ai_prompt_file = os.path.join(_prompt_home, ".ai_s", "onyx_ai.md")
        if os.path.exists(ai_prompt_file):
            with open(ai_prompt_file, "r", encoding="utf-8") as _apf:
                onyx_ai_prompt = _apf.read().strip()
    except Exception:
        pass

    # 构建用户消息：稳定前缀（可缓存） + 动态后缀
    # ⚠️ 稳定前缀里不要放任何会变的内容——时间、记忆、情绪都放后面
    _stable_env = f"""{system_label}: {sys_main_type} - {sys_sub_type}
{env_label}: {'Termux' if is_termux else 'PC'}
{user_label}: {USER}
Shell: {current_shell}
Onyx Mode: {onyx_mode}
{language_label}: {get_current_lang()}
#可用工具（{tool_count}）
{chr(10).join(tool_list)}
{ai_tools_prompt}"""

    _dynamic_suffix = f"""#当前时间: {current_time}
#工作目录: {os.getcwd()}
#持久记忆
{onyx_ai_prompt if onyx_ai_prompt else '(暂无)'}
{mood_context()}
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

    # ── 条件加载情感模块提示词 etc/ai/mood.md ──
    if is_mood_enabled():
        try:
            _mood_prompt_path = os.path.join(ROOT_DIR, "etc", "ai", "mood.md")
            if os.path.exists(_mood_prompt_path):
                with open(_mood_prompt_path, "r", encoding="utf-8") as _mf:
                    system_prompt = (system_prompt or "") + "\n\n" + _mf.read()
        except Exception:
            pass

    # ── 构建 messages ──
    # 如果外部传入了完整 messages（标准对话历史），优先使用
    if messages is None:
        # 旧路径：从 question + env_info 构建单轮 user message
        _messages = []
        if system_prompt:
            _messages.append({"role": "system", "content": system_prompt})
        _messages.append({"role": "user", "content": env_info})
    else:
        # 新路径：直接使用外部传入的标准 messages（handle_ai 负责维护）
        _messages = messages

    headers = {
        "Content-Type": "application/json",
    }
    # Common SSE header — signals to the server that we expect event-stream
    headers["Accept"] = "text/event-stream"

    if plat_key == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    # ── 合并参数（默认 → 模型覆盖 → 用户覆盖）──
    default_params = dict(plat_info.get("params", {"temperature": 0.1, "top_p": 0.2, "max_tokens": 4096}))
    model_overrides = plat_info.get("model_params", {}).get(model, {})
    p = {**default_params, **model_overrides, **user_params}

    payload: dict
    if plat_key == "anthropic":
        system_content = ""
        user_content = ""
        for m in _messages:
            if m["role"] == "system":
                system_content = m["content"]
            else:
                user_content = m["content"]
        payload = {
            "model": model,
            "max_tokens": p.get("max_tokens", 4096),
            "system": system_content,
            "messages": [{"role": "user", "content": user_content}],
            "stream": True,
        }
    else:
        payload = {
            "model": model,
            "messages": _messages,
            "stream": True,
            "max_tokens": p.get("max_tokens", 4096),
        }
        # Some models (e.g. deepseek-reasoner) reject temperature/top_p
        if p.get("temperature") is not None:
            payload["temperature"] = p["temperature"]
        if p.get("top_p") is not None:
            payload["top_p"] = p["top_p"]

    # DeepSeek thinking mode (2026 API): controlled by "thinking" + "reasoning_effort"
    if plat_info.get("thinking"):
        payload["thinking"] = plat_info["thinking"]
    # reasoning_effort: prefer user override from key.conf params, fall back to platform default
    _effort = user_params.get("reasoning_effort") or plat_info.get("reasoning_effort")
    if _effort:
        payload["reasoning_effort"] = _effort

    # Native function calling (OpenAI-compatible tools array)
    if tools:
        payload["tools"] = tools

    # Request token usage stats in the final SSE chunk
    payload["stream_options"] = {"include_usage": True}

    api_url = plat_info["api_url"]
    stream_fmt = plat_info["stream_format"]

    max_retries = 3
    base_delay = 2
    last_error = None

    global _AI_INTERRUPTED
    _AI_INTERRUPTED = False

    for retry in range(max_retries):
        try:
            _mcp_debug(f"HTTP POST {api_url} (attempt {retry+1}/{max_retries})")
            response = requests.post(
                api_url, headers=headers, json=payload,
                timeout=120, stream=True
            )
            _mcp_debug(f"HTTP response: {response.status_code}")

            if response.status_code == 400:
                _detail = response.text[:500]
                _mcp_debug(f"HTTP 400 body: {_detail}")
                return {"error": f"请求参数错误 (400): {_detail}", "answer": "no", "ask": "", "txt": "", "analysis": ""}
            if response.status_code == 401:
                return {"error": "API key 无效 (401)", "answer": "no", "ask": "", "txt": "", "analysis": ""}
            if response.status_code == 402:
                return {"error": "⚠️ API 余额不足 (402)，请充值后重试 | Insufficient balance, please top up", "answer": "no", "ask": "", "txt": "", "analysis": ""}
            if response.status_code == 422:
                detail = ""
                try:
                    detail = f": {response.json().get('message', response.text[:200])}"
                except Exception:
                    detail = f": {response.text[:200]}"
                return {"error": f"请求参数错误 (422){detail}", "answer": "no", "ask": "", "txt": "", "analysis": ""}
            if response.status_code == 429:
                return {"error": "请求过于频繁 (429)，请稍后再试 | Rate limit reached, please retry later", "answer": "no", "ask": "", "txt": "", "analysis": ""}
            if response.status_code in (500, 502, 503):
                return {"error": f"AI 服务暂时不可用 ({response.status_code})，正在重试…", "answer": "no", "ask": "", "txt": "", "analysis": ""}
            response.raise_for_status()

            response.encoding = 'utf-8'
            full_content = ""
            debug_lines = []
            _usage = {}
            _tool_calls_acc: Dict[int, Dict] = {}
            _reasoning_display: List[str] = []

            if stream_fmt == "openai":
                # DeepSeek / OpenAI SSE: data: {"choices":[{"delta":{"content":"..."}}]}
                for line in response.iter_lines(decode_unicode=True):
                    if _AI_INTERRUPTED:
                        response.close()
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
                        # Final chunk with usage stats (empty choices + usage field)
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
                        # DeepSeek reasoner: separate thinking tokens from content
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
                        # Native tool_calls from function calling
                        tc_delta = delta.get("tool_calls")
                        if tc_delta and isinstance(tc_delta, list):
                            for tc_chunk in tc_delta:
                                if not isinstance(tc_chunk, dict):
                                    continue
                                tc_idx = tc_chunk.get("index", 0)
                                _is_new = tc_idx not in _tool_calls_acc
                                if _is_new:
                                    _tool_calls_acc[tc_idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                                    # 首次检测到工具调用 → 通知回调
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
                # Anthropic SSE: data: {"type":"content_block_delta","delta":{"text":"..."}}
                for line in response.iter_lines(decode_unicode=True):
                    if _AI_INTERRUPTED:
                        response.close()
                        return {"txt": "", "analysis": "", "answer": "yes", "ask": "", "_interrupted": True}
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    try:
                        chunk = json.loads(data_str)
                        if not isinstance(chunk, dict):
                            continue
                        if chunk.get("type") == "content_block_delta":
                            delta = chunk.get("delta", {})
                            if not isinstance(delta, dict):
                                continue
                            text = delta.get("text", "")
                            if text:
                                full_content += text
                                if on_content:
                                    on_content(text)
                        elif chunk.get("type") == "message_stop":
                            break
                    except json.JSONDecodeError:
                        continue

            # 规范化换行（在解析之前，保留 full_content 原始副本用于 debug 日志）
            raw_full = full_content
            if full_content:
                import re as _re
                full_content = _re.sub(
                    r'(?<!\n)(\[TXT\](?![:D])|\[TXT:DONE\]|\[ANALYSIS\](?![:D])|\[ANALYSIS:DONE\]|@@SHELL|>>>>>>>>>>|\[ANSWER\]|\[ASK\]|\[PLAN\]|\[PLAN:DONE\]|\[PROMPT\]|\[PROMPT:DONE\]|\[TAG\]|\[TAG:DONE\]|\[MEMORY\]|\[CLASS\]|\[SLEEP\])',
                    r'\n\1', full_content
                )

            result = parse_sse_structured_response(full_content)

            # ── 解析自研标记语言（纯文本 VIEW/EDIT/WRITE/APPEND/INSERT/DELETE）──
            try:
                result["markup_blocks"] = _parse_markup(raw_full if raw_full else full_content)
            except Exception:
                result["markup_blocks"] = []

            # Merge native tool_calls from function calling with text-parsed ones
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

            # ── --debug：原始响应写入 .ai_s/deb/ ──
            if debug_mode:
                import re as _re
                deb_dir = os.path.join(user_home_dir or os.path.expanduser("~"), ".ai_s", "deb")
                os.makedirs(deb_dir, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                # 写入原始响应（完整，不截断）
                raw_path = os.path.join(deb_dir, f"{ts}_raw.txt")
                with open(raw_path, "w", encoding="utf-8") as _df:
                    _df.write(f"── Raw API Response ({plat_key}, model={model}) ──\n")
                    _df.write(raw_full)
                    _df.write("\n── End Raw ──\n")
                # 写入解析后的字段
                parsed_path = os.path.join(deb_dir, f"{ts}_parsed.json")
                with open(parsed_path, "w", encoding="utf-8") as _df:
                    json.dump(result, _df, ensure_ascii=False, indent=2)
                # 终端上也打印摘要
                debug_lines.append(f"── Raw ({plat_key}) ──")
                debug_lines.append(raw_full[:2000])
                debug_lines.append("── End Raw ──")
                debug_lines.append(f"── 完整日志: {raw_path} ──")
                debug_lines.append(f"── 解析结果: {parsed_path} ──")
                debug_lines.append("── Parsed ──")
                debug_lines.append(json.dumps(result, ensure_ascii=False, indent=2)[:2000])

            # Attach token usage stats (from stream_options include_usage)
            if _usage:
                result["_usage"] = _usage
            # Attach reasoning/thinking content (separate from structured response)
            if _reasoning_display:
                result["_reasoning"] = "".join(_reasoning_display)
            result["_debug"] = "\n".join(debug_lines) if debug_lines else ""
            return result

        except KeyboardInterrupt:
            _AI_INTERRUPTED = True
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

# -------------------------- 8. AI返回字段处理函数包装 --------------------------
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
    
    return result

def extract_ai_commands(ai_result: Dict[str, Any]) -> List[str]:
    """提取AI返回的命令"""
    commands = []
    for key, cmd in ai_result.items():
        if key.startswith("cmd") and key[3:].isdigit() and cmd and str(cmd).strip():
            commands.append(str(cmd).strip())
    return commands

def build_memory_context(home_dir: str, chat_name: str, current_session_id: str,
                         referenced_memory_uuid: Optional[str], is_first_interaction: bool,
                         mode: str = "normal") -> str:
    """构建记忆上下文 — 分三段：历史摘要(UUID链) | 当前会话(ongoing) | 引用记忆"""
    lang = get_current_lang()
    is_en = lang == "english"
    parts = []
    
    if mode == "normal":
        # ── 第一段：UUID 链 — 历史摘要，每条带独立 id，可被 MEMORY 精确引用 ──
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
        
        # ── 第二段：当前正在进行的会话（ongoing，实时更新）──
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
        
        # ── 第三段：引用记忆（通过 [MEMORY]uuid 主动查询）──
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

# handle_path_field 已移除 — 文件编辑现在通过 AI 工具 (edit_file, write_file) 以 [tool:...] 格式调用
# -------------------------- 9. 双语映射表 --------------------------
def get_lang_text(lang: str) -> Dict[str, str]:
    if lang == "english":
        return {
            "param_error": "Missing parameter! Usage:\n  ai [options] <question>\nOptions:\n  -cmd true/false  Auto-execute commands (default: true)\n  -t <text>       Input long text\n  -f <file>       Load file content\n  -key <32-bit key>  Set AI license key (adv mode only)\n  -c switch/list/new  Chat memory management\n  --debug         Enable debug mode (default: false)",
            "text_usage": "Usage: ai -t <text_content> (e.g. ai -t Explain nmap -sV)",
            "file_usage": "Usage: ai -f <file_path> (e.g. ai -f ./scan.log)",
            "cmd_option_usage": "Invalid -cmd option! Must be 'true' or 'false'",
            "key_format_error": "❌ Invalid key format! Must be 32-character string",
            "sandbox_block": "Sandbox blocked: Path not allowed → {}",
            "file_not_exist": "File not exists: {}",
            "file_no_perm": "No read permission: {}",
            "file_too_large": "File too large (>200KB): {}",
            "file_read_fail": "Read failed: {}",
            "loading": "Analyzing...",
            "api_call": "Calling AI API (onyx SSE): question={}...",
            "api_error": "AI API error: {}",
            "api_conn_fail": "❌ AI API connection failed! Check network/API address",
            "json_format_error": "AI return format error",
            "raw_return": "Raw return: {}...",
            "ai_answer": "🎯 AI Answer:",
            "ai_analysis": "🤖 AI Strategy Analysis",
            "analysis_sep": "─" * 50,
            "analysis_title": "Planning Analysis:",
            "no_cmd": "",
            "no_analysis": "No command generated, no strategy analysis required",
            "cmd_exec_disabled": "ℹ️ Auto-execute disabled (-cmd false)",
            "cmd_exec_enabled": "📋 Execution Tasks:",
            "cmd_exec_item": "  {} . Execute command: {}",
            "cmd_exec_wait": "  ⏳ Waiting 1.5s before next command...",
            "cmd_exec_success": "✅ Command executed successfully: {}",
            "cmd_exec_fail": "❌ Command execution failed: {} → Reason: {}",
            "task_complete": "✅ Task completed!",
            "plan_mode_warning": "⚠️ You are currently in PLAN mode. You can ONLY generate plans — you MUST NOT execute any commands or modify any files. Use [plan]...[plan:done] format to output your plan. Wait for user confirmation before execution.",
            "plan_received": "📋 AI has generated the following plan:",
            "plan_choose": "Use ↑↓ to select, Enter to confirm:",
            "plan_confirm": "✅ Confirm this plan",
            "plan_guide": "💬 Guide AI to revise",
            "plan_discard": "🗑️ Discard this plan",
            "plan_discarded": "🗑️ Plan discarded, AI will be asked to re-plan",
            "plan_guide_prompt": "💡 Enter your revision guidance:",
            "plan_confirmed": "✅ Plan confirmed, entering execution phase",
            "plan_blocked": "⛔ Plan mode: AI commands/tools blocked. Please confirm the plan first.",
            "tool_call_summary": "🔧 AI called tool: {} {}",
            "tool_call_result": "   → {}",
            "tool_exec_failed": "❌ Tool execution failed: {}",
            "thread_priority_win": "Windows thread priority: {} (ID: {})",
            "thread_priority_linux": "Linux/Termux priority: nice {}→{} ({task} task)",
            "thread_priority_fail": "Thread priority adjustment failed: {} (function not affected)",
            "stat_info": "📊 Stats: Question {} chars → Answer {} chars",
            "cache_save_success": "✅ AI commands saved to cache: {}",
            "license_valid": "✅ AI license verified successfully",
            "license_invalid": "❌ Invalid AI license",
            "key_set_success": "✅ AI license key set successfully",
            "server_url_info": "Server URL: {}",
            "ai_ask": "🤔 AI Question:",
            "chat_list_header": "📋 Available chat memories:",
            "chat_switched": "✅ Switched to chat memory: {}",
            "chat_not_found": "❌ Chat memory not found: {}",
            "chat_created": "✅ Created new chat memory: {}",
            "chat_already_exists": "⚠️ Chat memory already exists: {}",
            "chat_switch_usage": "Usage: ai -c switch <name>",
            "chat_new_usage": "Usage: ai -c new [name]",
            "current_chat": "💬 Current chat memory: {}",
            "memory_referenced": "📖 AI referenced memory: {}",
            "danger_cmd_title": "⚠️ AI Dangerous Command Warning",
            "danger_cmd_prompt": "AI attempts to execute a potentially dangerous command:",
            "danger_cmd_display": "Command",
            "danger_cmd_reason": "Reason",
            "danger_cmd_msg": "Command「{}」is marked as potentially dangerous",
            "danger_cmd_confirm": "Confirm execution of this command? (y/N)：",
            "danger_cmd_cancelled": "❌ Dangerous command execution cancelled",
            "danger_cmd_executing": "✅ User confirmed, executing dangerous command...",
            "danger_cmd_ask_reason": "❓ Please tell us why you refused (max 500 chars)：",
            "danger_cmd_reason_recorded": "✅ Your refusal reason has been recorded",
            "debug_param_parsed": "[DEBUG] Parameter parsing completed:",
            "debug_content_type": "[DEBUG] - Content type: {}",
            "debug_content_preview": "[DEBUG] - Core content: {}...",
            "debug_extra_info": "[DEBUG] - Extra info: {}",
            "debug_auto_exec": "[DEBUG] - Auto execute: {}",
            "debug_new_key": "[DEBUG] - Quick set key: {}",
            "debug_current_chat": "[DEBUG] - Current chat memory: {}",
            "debug_api_call": "[DEBUG] API call #{}, question length: {} chars",
            "debug_session_file": "[DEBUG] Read session memory file: {}",
            "debug_chat_memory": "[DEBUG] Read chat memory: {}",
            "debug_referenced_memory": "[DEBUG] Read referenced memory: {}",
            "debug_api_return_type": "[DEBUG] API raw return type: SSE",
            "debug_json_parse_fail": "[DEBUG] SSE parsing failed: {}",
            "debug_memory_file_info": "[DEBUG] Current memory file: {}, size: {} chars",
            "debug_chat_memory_appended": "[DEBUG] Appended chat memory to: {}",
            "sleep_countdown": "AI chose to wait {} seconds, press Ctrl+C to interrupt ({}/{}s)...",
            "sleep_completed": "✅ AI {} seconds wait completed",
            "sleep_interrupted": "⏸️ User interrupted sleep, continuing...",
            "no_memory": "No historical memory",
            "interaction_prefix": "--- Interaction {} ({}) ---",
            "ai_ask_label": "🤔 AI Question",
            "user_answer_label": "💬 User answer",
            "ai_response_label": "AI response",
            "executed_cmds_label": "Executed commands",
            "realtime_output_label": "real-time output",
            "user_refused_cmds": "\n\n🚫 Commands rejected by user:\n",
            "tool_output_cache": "[Tool output cache] {}",
            "no_output": "[No output] Command executed successfully, no output returned",
            "command_interrupted": "[Interrupted] Command interrupted by user",
            "command_error": "[ERROR] Command execution exception: {}",
            "execution_time": "Execution time",
            "output_content": "Output content",
            "sleep_operation": "--- Sleep Operation ---",
            "sleep_wait_msg": "AI chose to wait {} seconds, user interrupted after {} seconds",
            "sleep_complete_msg": "AI chose to wait {} seconds, completed normally",
            "short_interaction_prefix": "This is the {}th interaction. Initial task: {}\nPlease continue or supplement operations",
            "continue_task": "Please continue the above task or supplement based on context",
            "analysis_cmd_prefix": "This execution generated {} commands, executed according to the following logic:\n",
            "adv_code_rejected_syntax": "❌ Adv_code mode: command contains forbidden syntax (pipe/redirect/here document) -> {}",
            "adv_code_all_rejected": "⚠️ All commands rejected due to forbidden syntax in adv_code mode",
            "sse_parsing_error": "❌ Failed to parse SSE response: {}",
        }
    return {
            "param_error": "缺少参数！用法：\n  ai [选项] <问题>\n选项：\n  -cmd true/false  命令自动执行（默认：true）\n  -t <文本>       输入长文本\n  -f <文件>       加载文件内容\n  -key <32位密钥>  快速设置AI许可证密钥（仅adv模式）\n  -c switch/list/new  聊天记忆管理\n  --debug         启用调试模式（默认：false）",
            "text_usage": "用法：ai -t <文本内容>（例：ai -t 解释nmap -sV参数）",
            "file_usage": "用法：ai -f <文件路径>（例：ai -f ./scan.log）",
            "cmd_option_usage": "无效的 -cmd 选项！必须是 'true' 或 'false'",
            "key_format_error": "❌ 密钥格式错误！必须是32位字符串",
            "sandbox_block": "沙箱拦截：路径不允许 → {}",
            "file_not_exist": "文件不存在：{}",
            "file_no_perm": "无读取权限：{}",
            "file_too_large": "文件过大（>200KB）：{}",
            "file_read_fail": "读取失败：{}",
            "loading": "分析中...",
            "api_call": "调用AI接口(onyx SSE)：问题={}...",
            "api_error": "AI接口错误：{}",
            "api_conn_fail": "❌ AI API连接失败！请检查网络/API地址",
            "json_format_error": "AI返回格式错误",
            "raw_return": "原始返回：{}...",
            "ai_answer": "🎯 AI 回答：",
            "ai_analysis": "🤖 AI策略分析",
            "analysis_sep": "─" * 50,
            "analysis_title": "策划分析：",
            "no_cmd": "",
            "no_analysis": "未生成命令，无需策略分析",
            "cmd_exec_disabled": "ℹ️ 已禁用命令自动执行（-cmd false）",
            "cmd_exec_enabled": "📋 执行工作：",
            "cmd_exec_item": "  {} . 执行命令：{}",
            "cmd_exec_wait": "  ⏳ 等待1.5秒后执行下一条命令...",
            "cmd_exec_success": "✅ 命令执行成功：{}",
            "cmd_exec_fail": "❌ 命令执行失败：{} → 原因：{}",
            "task_complete": "✅ 工作完成！",
            "plan_mode_warning": "⚠️ 当前处于 PLAN 模式。你只能生成计划 — 不能执行任何命令或修改文件。请使用 [plan]...[plan:done] 格式输出你的计划。等用户确认后，才能进入执行阶段。",
            "plan_received": "📋 AI 生成了以下计划：",
            "plan_choose": "请用上下键选择，回车确认：",
            "plan_confirm": "✅ 确认这份计划",
            "plan_guide": "💬 继续指导 AI 修改计划",
            "plan_discard": "🗑️ 摒弃这份计划",
            "plan_discarded": "🗑️ 计划已摒弃，将通知 AI 重新规划",
            "plan_guide_prompt": "💡 请输入你对计划的修改意见：",
            "plan_confirmed": "✅ 计划已确认，即将进入执行阶段",
            "plan_blocked": "⛔ Plan 模式：AI 命令/工具调用已被拦截。请先确认计划。",
            "tool_call_summary": "🔧 AI 调用了工具: {} {}",
            "tool_call_result": "   → {}",
            "tool_exec_failed": "❌ 工具执行失败: {}",
            "thread_priority_win": "Windows线程优先级：{}（ID：{}）",
            "thread_priority_linux": "Linux/Termux优先级：nice {}→{}（{task}任务）",
            "thread_priority_fail": "线程优先级调整失败：{}（不影响功能）",
            "stat_info": "📊 统计：问题{}字符 → 回答{}字符",
            "cache_save_success": "✅ AI命令已保存到缓存：{}",
            "license_valid": "✅ AI许可证验证成功",
            "license_invalid": "❌ 无效的AI许可证",
            "key_set_success": "✅ AI许可证密钥设置成功",
            "server_url_info": "服务器地址：{}",
            "ai_ask": "🤔 AI 询问：",
            "chat_list_header": "📋 可用的聊天记忆：",
            "chat_switched": "✅ 已切换到聊天记忆：{}",
            "chat_not_found": "❌ 聊天记忆不存在：{}",
            "chat_created": "✅ 已创建新聊天记忆：{}",
            "chat_already_exists": "⚠️ 聊天记忆已存在：{}",
            "chat_switch_usage": "用法：ai -c switch <名称>",
            "chat_new_usage": "用法：ai -c new [名称]",
            "current_chat": "💬 当前聊天记忆：{}",
            "memory_referenced": "📖 AI引用了记忆：{}",
            "danger_cmd_title": "⚠️ AI危险命令警告",
            "danger_cmd_prompt": "AI尝试执行可能危险的命令：",
            "danger_cmd_display": "命令",
            "danger_cmd_reason": "原因",
            "danger_cmd_msg": "命令「{}」被标记为可能危险的操作",
            "danger_cmd_confirm": "确认执行此命令？(y/N)：",
            "danger_cmd_cancelled": "❌ 已取消执行危险命令",
            "danger_cmd_executing": "✅ 用户确认，正在执行危险命令...",
            "danger_cmd_ask_reason": "❓ 请问您拒绝执行的原因是什么？（输入后按回车，最多500字）：",
            "danger_cmd_reason_recorded": "✅ 已记录您拒绝的原因",
            "debug_param_parsed": "[DEBUG] 参数解析完成：",
            "debug_content_type": "[DEBUG] - 内容类型：{}",
            "debug_content_preview": "[DEBUG] - 核心内容：{}...",
            "debug_extra_info": "[DEBUG] - 额外信息：{}",
            "debug_auto_exec": "[DEBUG] - 自动执行：{}",
            "debug_new_key": "[DEBUG] - 快速设置密钥：{}",
            "debug_current_chat": "[DEBUG] - 当前聊天记忆：{}",
            "debug_api_call": "[DEBUG] 第{}次调用AI，问题长度：{} 字符",
            "debug_session_file": "[DEBUG] 读取到会话记忆文件：{}",
            "debug_chat_memory": "[DEBUG] 读取到聊天记忆：{}",
            "debug_referenced_memory": "[DEBUG] 读取到引用的记忆：{}",
            "debug_api_return_type": "[DEBUG] API原始返回类型：SSE",
            "debug_json_parse_fail": "[DEBUG] SSE解析失败：{}",
            "debug_memory_file_info": "[DEBUG] 当前记忆文件：{}，大小：{} 字符",
            "debug_chat_memory_appended": "[DEBUG] 已追加聊天记忆到：{}",
            "sleep_countdown": "AI选择等待 {} 秒，可按Ctrl+C中断（{}/{}秒）...",
            "sleep_completed": "✅ AI等待 {} 秒完成",
            "sleep_interrupted": "⏸️ 用户已中断等待，继续执行...",
            "no_memory": "无历史记忆",
            "interaction_prefix": "--- 第{}次交互（{}） ---",
            "ai_ask_label": "🤔 AI询问",
            "user_answer_label": "💬 用户回答",
            "ai_response_label": "AI回答",
            "executed_cmds_label": "执行命令",
            "realtime_output_label": "实时输出",
            "user_refused_cmds": "\n\n🚫 用户拒绝执行的命令:\n",
            "tool_output_cache": "[工具输出缓存] {}",
            "no_output": "[无输出] 命令执行成功，未返回任何输出",
            "command_interrupted": "[中断] 命令被用户中断",
            "command_error": "[ERROR] 命令执行异常: {}",
            "execution_time": "执行时间",
            "output_content": "输出内容",
            "sleep_operation": "--- 等待操作 ---",
            "sleep_wait_msg": "AI选择等待 {} 秒，用户在等待 {} 秒后中断",
            "sleep_complete_msg": "AI选择等待 {} 秒，正常完成",
            "short_interaction_prefix": "这是第{}次交互。初始任务：{}\n请继续完成或补充操作",
            "continue_task": "请继续完成上述任务或根据上下文补充",
            "analysis_cmd_prefix": "本次生成 {} 条命令，按以下逻辑执行：\n",
            "adv_code_rejected_syntax": "❌ Adv_code 模式：命令包含禁止语法（管道符/重定向/here document）-> {}",
            "adv_code_all_rejected": "⚠️ adv_code 模式下所有命令均因包含禁止语法而被拒绝",
            "sse_parsing_error": "❌ 解析SSE响应失败：{}",
        }

# -------------------------- 10. 辅助函数 --------------------------
def handle_sleep_wait(sleep_seconds: int, session_id: str, lang_text: Dict[str, str], log_info: Callable = None) -> Tuple[bool, int]:
    """处理AI的sleep等待，返回(是否被中断, 实际等待秒数)"""
    current_lang = get_current_lang()
    interrupted = False
    waited_seconds = 0
    
    try:
        for i in range(1, sleep_seconds + 1):
            time.sleep(1)
            waited_seconds = i
            console.print(f"\r{lang_text['sleep_countdown'].format(sleep_seconds, i, sleep_seconds)}", end="", style="bold blue")
        
        console.print(f"\n{lang_text['sleep_completed'].format(sleep_seconds)}", style="bold green")
        if log_info:
            log_info(f"AI sleep {sleep_seconds} seconds completed", session_id)
    except KeyboardInterrupt:
        interrupted = True
        console.print(f"\n{lang_text['sleep_interrupted']}", style="bold yellow")
        if log_info:
            log_info(f"AI sleep interrupted after {waited_seconds} seconds", session_id)
    
    return interrupted, waited_seconds

def set_ai_thread_priority(lang_text: Dict[str, str], thread: threading.Thread, is_core_task: bool = True, onyx_module=None) -> None:
    try:
        if onyx_module and hasattr(onyx_module, "sys_type") and onyx_module.sys_type == "Windows":
            import ctypes
            THREAD_PRIORITY_HIGHEST = 2
            THREAD_PRIORITY_LOWEST = -2
            priority = "High" if is_core_task else "Low"
            thread_id = ctypes.c_longlong(thread.ident)
            handle = ctypes.windll.kernel32.OpenThread(0x001F03FF, False, thread_id)
            if handle:
                ctypes.windll.kernel32.SetThreadPriority(handle, THREAD_PRIORITY_HIGHEST if is_core_task else THREAD_PRIORITY_LOWEST)
                ctypes.windll.kernel32.CloseHandle(handle)
                win_msg = lang_text.get("thread_priority_win", "Windows thread priority: {} (ID: {})")
                if onyx_module and hasattr(onyx_module, "log_info"):
                    onyx_module.log_info(win_msg.format(priority, thread.ident), str(uuid.uuid4()))
        else:
            current_nice = os.nice(0)
            target_nice = max(-10, current_nice - 5) if is_core_task else min(10, current_nice + 5)
            os.nice(target_nice - current_nice)
            task_type = "core" if is_core_task else "non-core"
            linux_msg = lang_text.get("thread_priority_linux", "Linux/Termux priority: nice {}→{} ({task} task)")
            if onyx_module and hasattr(onyx_module, "log_info"):
                onyx_module.log_info(linux_msg.format(current_nice, target_nice, task=task_type), str(uuid.uuid4()))
    except Exception as e:
        fail_msg = lang_text.get("thread_priority_fail", "Thread priority adjustment failed: {} (function not affected)")
        if onyx_module and hasattr(onyx_module, "log_warning"):
            onyx_module.log_warning(fail_msg.format(str(e)[:30]), str(uuid.uuid4()))

def confirm_plan(plan_text: str, lang_text: Dict[str, str]) -> str:
    """
    上下键选择 Plan 确认流程：Rich Panel 展示计划 + 箭头键选择。
    返回: "confirm" / "guide" / "discard"
    """
    # Rich Panel 渲染计划内容
    console.print(render_plan_panel(plan_text))
    console.print()

    try:
        choice = select_option(
            message=lang_text.get("plan_prompt", "请选择操作:"),
            options=[
                lang_text.get("plan_opt_confirm", "✅ 确认计划，开始执行"),
                lang_text.get("plan_opt_guide", "💡 提出修改意见"),
                lang_text.get("plan_opt_discard", "🗑️ 摒弃计划，重新制定"),
            ],
            default=lang_text.get("plan_opt_confirm", "✅ 确认计划，开始执行"),
            lang=get_current_lang(),
        )
    except (KeyboardInterrupt, EOFError):
        console.print()
        return "confirm"

    if choice in (lang_text.get("plan_opt_discard", "🗑️ 摒弃计划，重新制定"),):
        return "discard"
    elif choice in (lang_text.get("plan_opt_guide", "💡 提出修改意见"),):
        return "guide"
    return "confirm"


def parse_arguments(cmd_parts: List[str], lang_text: Dict[str, str], onyx_module=None) -> Tuple:
    if onyx_module and not hasattr(onyx_module, "SANDBOX_CONFIG"):
        onyx_module.SANDBOX_CONFIG = {"enable": False}
    
    ai_args = cmd_parts[1:] if len(cmd_parts) > 1 else []
    auto_exec = True
    content_type = "direct"
    content = ""
    extra_info = None
    new_key = None
    chat_action = None
    chat_param = None
    mode = "normal"
    times = 1
    
    i = 0
    while i < len(ai_args):
        arg = ai_args[i]
        # ── -model 子命令 ──
        if arg == "-model":
            model_name = ai_args[i + 1] if i + 1 < len(ai_args) and not ai_args[i + 1].startswith("-") else None
            if model_name:
                i += 2
            else:
                i += 1
            return ("model_command", model_name or "", [], auto_exec, new_key, None, None, mode, times)
        # ── -effort 推理强度 ──
        elif arg == "-effort":
            effort_val = ai_args[i + 1] if i + 1 < len(ai_args) and not ai_args[i + 1].startswith("-") else None
            if effort_val:
                i += 2
            else:
                i += 1
            return ("effort_command", effort_val or "", [], auto_exec, new_key, None, None, mode, times)
        # ── -mid / -machine-id ──
        elif arg in ("-mid", "-machine-id"):
            return ("machine_id_command", "", [], auto_exec, new_key, None, None, mode, times)
        # ── -plugin 子命令 ──
        elif arg in ("-plugin", "plugin"):
            sub = ai_args[i + 1] if i + 1 < len(ai_args) and not ai_args[i + 1].startswith("-") else "list"
            extra = []
            if sub in ("load", "sign", "verify", "compile"):
                extra = ai_args[i + 2:] if i + 2 < len(ai_args) else []
                i += len(extra) + 2
            else:
                i += 2 if sub != "list" else 1
            return ("plugin_command", sub, extra, auto_exec, new_key, None, None, mode, times)
        # ── -mcp 子命令 ──
        elif arg in ("-mcp", "mcp"):
            if i + 1 >= len(ai_args):
                return ("mcp_command", "list", [], auto_exec, new_key, None, None, mode, times)
            mcp_sub = ai_args[i + 1].lower()
            mcp_args = ai_args[i + 2:] if i + 2 < len(ai_args) else []
            if mcp_sub in ("install", "remove", "list", "start"):
                return ("mcp_command", mcp_sub, mcp_args, auto_exec, new_key, None, None, mode, times)
            return ("error", f"Invalid -mcp subcommand: {mcp_sub}. Use install/list/remove", None, auto_exec, new_key, None, None, mode, times)
        elif arg == "-cmd":
            if i + 1 >= len(ai_args):
                return ("error", lang_text["cmd_option_usage"], None, auto_exec, new_key, None, None, mode, times)
            cmd_val = ai_args[i+1].lower()
            if cmd_val not in ["true", "false"]:
                return ("error", lang_text["cmd_option_usage"], None, auto_exec, new_key, None, None, mode, times)
            auto_exec = (cmd_val == "true")
            i += 2
        elif arg == "-m":
            if i + 1 >= len(ai_args):
                return ("error", "Missing mode for -m parameter", None, auto_exec, new_key, None, None, mode, times)
            mode_val = ai_args[i+1].lower()
            if mode_val not in ["plan", "normal"]:
                return ("error", "Invalid -m mode! Must be 'plan' or 'normal'", None, auto_exec, new_key, None, None, mode, times)
            mode = mode_val
            i += 2
        elif arg == "-mode":
            if i + 1 >= len(ai_args):
                return ("error", "Missing mode type for -mode", None, auto_exec, new_key, None, None, mode, times)
            mode_type = ai_args[i+1].lower()
            mode_val = ai_args[i+2] if i + 2 < len(ai_args) and not ai_args[i+2].startswith("-") else "true"
            if mode_type == "deep-aff":
                return ("deep_aff_mode", mode_val, [], auto_exec, new_key, None, None, mode, times)
            return ("error", f"Unknown mode: {mode_type}", None, auto_exec, new_key, None, None, mode, times)
        elif arg == "-c":
            if i + 1 >= len(ai_args):
                return ("chat_only", "", None, auto_exec, new_key, None, None, mode, times)
            chat_action = ai_args[i+1].lower()
            if chat_action in ["switch", "new"]:
                if i + 2 >= len(ai_args):
                    return ("chat_only", f"Missing name for -c {chat_action}", None, auto_exec, new_key, chat_action, None, mode, times)
                chat_param = ai_args[i+2]
                i += 3
            elif chat_action == "list":
                i += 2
            else:
                return ("error", f"Invalid -c action: {chat_action}. Use switch/list/new", None, auto_exec, new_key, None, None, mode, times)
        elif arg == "-t":
            if i + 1 >= len(ai_args):
                return ("error", lang_text["text_usage"], None, auto_exec, new_key, None, None, mode, times)
            text_parts = []
            j = i + 1
            while j < len(ai_args) and not ai_args[j].startswith("-"):
                text_parts.append(ai_args[j])
                j += 1
            if not text_parts:
                return ("error", lang_text["text_usage"], None, auto_exec, new_key, None, None, mode, times)
            content = " ".join(text_parts)
            content_type = "text"
            i = j
        elif arg == "-f":
            if i + 1 >= len(ai_args):
                return ("error", lang_text["file_usage"], None, auto_exec, new_key, None, None, mode, times)
            file_path = ai_args[i+1]
            if onyx_module and hasattr(onyx_module, "SANDBOX_CONFIG") and onyx_module.SANDBOX_CONFIG.get("enable", False):
                if hasattr(onyx_module, "check_sandbox_path") and not onyx_module.check_sandbox_path(file_path, str(uuid.uuid4())):
                    return ("error", lang_text["sandbox_block"].format(file_path), None, auto_exec, new_key, None, None, mode, times)
            if not os.path.exists(file_path):
                return ("error", lang_text["file_not_exist"].format(file_path), None, auto_exec, new_key, None, None, mode, times)
            if not os.access(file_path, os.R_OK):
                return ("error", lang_text["file_no_perm"].format(file_path), None, auto_exec, new_key, None, None, mode, times)
            if os.path.getsize(file_path) > 1024 * 200:
                return ("error", lang_text["file_too_large"].format(file_path), None, auto_exec, new_key, None, None, mode, times)
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    file_content = f.read().strip()
                file_prefix = "[File]" if get_current_lang() == "english" else "[文件]"
                content = f"{file_prefix}{os.path.basename(file_path)}\n{file_content[:4000]}..."
                content_type = "file"
                extra_info = file_path
                i += 2
            except Exception as e:
                return ("error", lang_text["file_read_fail"].format(str(e)[:20]), None, auto_exec, new_key, None, None, mode, times)
        elif arg == "-key":
            if i + 1 >= len(ai_args):
                return ("error", lang_text["key_format_error"], None, auto_exec, new_key, None, None, mode, times)
            new_key = ai_args[i+1].strip()
            if len(new_key) != 32:
                return ("error", lang_text["key_format_error"], None, auto_exec, new_key, None, None, mode, times)
            i += 2
        # ── 裸子命令（无需 -c/-mcp 前缀的快捷方式）──
        elif arg in ("new", "switch", "list"):
            chat_action = arg
            if arg in ("new", "switch"):
                if i + 1 >= len(ai_args):
                    return ("chat_only", f"Missing name for {arg}", None, auto_exec, new_key, arg, None, mode, times)
                chat_param = ai_args[i + 1]
                i += 2
            else:
                i += 1
        elif arg == "mcp":
            if i + 1 >= len(ai_args):
                return ("mcp_command", "list", [], auto_exec, new_key, None, None, mode, times)
            mcp_sub = ai_args[i + 1].lower()
            mcp_args = ai_args[i + 2:] if i + 2 < len(ai_args) else []
            if mcp_sub in ("install", "remove", "list", "start"):
                return ("mcp_command", mcp_sub, mcp_args, auto_exec, new_key, None, None, mode, times)
            return ("error", f"Invalid mcp subcommand: {mcp_sub}. Use install/list/remove", None, auto_exec, new_key, None, None, mode, times)
        elif arg.startswith("-"):
            i += 1
        else:
            question_parts = []
            while i < len(ai_args) and not ai_args[i].startswith("-"):
                question_parts.append(ai_args[i])
                i += 1
            if question_parts:
                content = " ".join(question_parts)
                content_type = "direct"
            else:
                i += 1
    
    if chat_action is not None:
        return ("chat_only", "", None, auto_exec, new_key, chat_action, chat_param, mode, times)
    
    if new_key is not None and not content:
        return ("key_only", "", None, auto_exec, new_key, None, None, mode, times)
    
    if not content and new_key is None:
        return ("error", lang_text["param_error"], None, auto_exec, new_key, None, None, mode, times)
    
    return (content_type, content, extra_info, auto_exec, new_key, None, None, mode, times)

def show_loading(loading_flag: List[bool], lang_text: Dict[str, str]) -> None:
    symbols = ["◐", "◓", "◑", "◒"]
    idx = 0
    while loading_flag[0]:
        sys.stdout.write(f"\r{symbols[idx%4]} {lang_text['loading']}")
        sys.stdout.flush()
        idx += 1
        time.sleep(0.25)
    sys.stdout.write("\r" + " " * 30 + "\r")
    sys.stdout.flush()

def init_ai_dangerous_commands(home_dir: str, log_info_func=None) -> None:
    danger_dir = os.path.join(home_dir, ".config", "onyx", "ai_danger")
    if not os.path.exists(danger_dir):
        os.makedirs(danger_dir, mode=0o755)
        default_cmds = [
            "rm", "rmdir", "del", "rd",
            "format", "mkfs", "fdisk",
            "dd", "shred", "wipe",
            "shutdown", "reboot", "halt",
            "kill", "pkill", "killall",
            "chmod", "chown", "chattr",
            "mv", "cp", "dd",
            "sudo", "su", "passwd"
        ]
        default_file = os.path.join(danger_dir, "dangerous_commands.txt")
        with open(default_file, "w", encoding="utf-8") as f:
            f.write("\n".join(default_cmds))
        if log_info_func:
            log_info_func(f"AI dangerous commands config initialized: {default_file}", str(uuid.uuid4()))

def load_ai_dangerous_commands(home_dir: str, log_info_func=None) -> set:
    danger_dir = os.path.join(home_dir, ".config", "onyx", "ai_danger")
    dangerous_commands = set()
    
    if not os.path.exists(danger_dir):
        init_ai_dangerous_commands(home_dir, log_info_func)
    
    for filename in os.listdir(danger_dir):
        if filename.endswith(".txt"):
            filepath = os.path.join(danger_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        cmd = line.strip().lower()
                        if cmd and not cmd.startswith("#"):
                            dangerous_commands.add(cmd)
            except Exception as e:
                if log_info_func:
                    log_info_func(f"Failed to load dangerous commands file {filename}: {str(e)}", str(uuid.uuid4()))
    
    return dangerous_commands

def is_dangerous_command(cmd_str: str, dangerous_commands: set) -> Tuple[bool, str]:
    if not cmd_str or not cmd_str.strip():
        return False, ""
    try:
        clean_cmd = re.sub(r'[^a-zA-Z0-9]', ' ', cmd_str)
        cmd_parts_check = [part for part in clean_cmd.split() if part.strip()]
        if not cmd_parts_check:
            return False, ""
        for part in cmd_parts_check:
            part_lower = part.lower()
            if part_lower in dangerous_commands:
                return True, part_lower
        return False, ""
    except Exception:
        return False, ""

def confirm_dangerous_command(cmd_str: str, cmd_name: str, lang_text: dict,
                              session_id: str, initial_question: str,
                              interaction_count: int, log_info: Callable = None) -> Tuple[bool, str, str]:
    """危险命令确认：Rich 红框 Panel + InquirerPy confirm"""
    MAX_REFUSE_REASON_LEN = 500

    confirmed, user_resp, refuse_reason = ui_confirm_dangerous(
        title=lang_text["danger_cmd_title"],
        command=f"{lang_text['danger_cmd_display']}: {cmd_str}",
        reason=lang_text['danger_cmd_msg'].format(cmd_name),
    )

    if confirmed:
        console.print(lang_text["danger_cmd_executing"], style="bold green")
        if log_info:
            log_info(f"AI dangerous command confirmed: {cmd_str}", session_id)
        return True, "y", ""
    else:
        console.print(lang_text["danger_cmd_cancelled"], style="bold red")
        if log_info:
            log_info(f"AI dangerous command cancelled: {cmd_str}", session_id)

        if len(refuse_reason) > MAX_REFUSE_REASON_LEN:
            refuse_reason = refuse_reason[:MAX_REFUSE_REASON_LEN] + (
                "...(truncated)" if get_current_lang() == "english" else "...(截断)"
            )

        if refuse_reason:
            console.print(lang_text["danger_cmd_reason_recorded"], style="bold green")
        return False, "n", refuse_reason


def has_forbidden_syntax(cmd: str) -> bool:
    """检测命令是否包含 adv_code 模式禁止的语法：管道、重定向、here document"""
    forbidden_patterns = ['|', '>', '<', '<<', '>>', '&>', '|&', '<<-']
    for pattern in forbidden_patterns:
        if pattern in cmd:
            return True
    return False

# ========================================================================
# 10.5 MCP (Model Context Protocol) 客户端模块
#    替代原 plugin_loader 插件系统，通过本地 MCP server 提供 AI 工具
#    - 出厂自动安装 @modelcontextprotocol/server-filesystem
#    - 用户可通过 ai -mcp install/remove/list 管理
#    - 工具列表中过滤 shell/bash 类工具（Onyx 已有 shell 接口）
#    - edit_file/write_file 在 mid 及以上模式允许（low 禁止）
#
#    v2.7 — Reasonix 风格重构：
#      - Transport 抽象层: bin/ai_lib/mcp_transport.py
#      - Registry 模式:    bin/ai_lib/mcp_registry.py
#      - Schema 缓存指纹:  加速冷启动
# ========================================================================

import subprocess
import signal

# ── 新版抽象层 ──
from .ai_lib.mcp_transport import (
    Transport, StdioTransport, create_transport,
)
from .ai_lib.mcp_registry import (
    MCPRegistry, MCPSchemaCache, get_registry, reset_registry,
)

# ── 旧版兼容变量（逐步迁移中）──
MCP_SERVER_PROCESSES: Dict[str, subprocess.Popen] = {}
MCP_TOOLS_CACHE: Dict[str, List[Dict]] = {}          # 旧缓存，逐步替换为 registry
MCP_TRANSPORTS: Dict[str, StdioTransport] = {}        # 新版 transport 实例
MCP_CONFIG_PATH = os.path.join(ROOT_DIR, "onyx", "etc", "mcp", "mcp.json")
MCP_PRELOADED = False
MCP_PRELOAD_LOCK = threading.Lock()
MCP_INSTALL_LOCK = threading.Lock()
MCP_HEALTH_CHECK_INTERVAL = 120
_MCP_LAST_HEALTH_CHECK = 0.0

# stderr 收集器（防止管道死锁：daemon 线程持续读取，避免子进程阻塞在 stderr write）
_MCP_STDERR_BUFFERS: Dict[int, List[str]] = {}       # proc.pid → [lines...]
_MCP_STDERR_LOCKS: Dict[int, threading.Lock] = {}    # proc.pid → Lock

def _start_stderr_reader(proc: subprocess.Popen, name: str = "mcp") -> None:
    """启动 daemon 线程持续读取 stderr，防止管道缓冲区满导致子进程死锁。"""
    pid = proc.pid
    lock = threading.Lock()
    _MCP_STDERR_LOCKS[pid] = lock
    _MCP_STDERR_BUFFERS[pid] = []

    stderr_fd = proc.stderr.fileno() if hasattr(proc.stderr, 'fileno') else None

    def _reader():
        try:
            if stderr_fd is not None:
                import os as _os
                # 直接读原始 fd（避免 TextIOWrapper 缓冲问题）
                buf = b""
                while True:
                    try:
                        chunk = _os.read(stderr_fd, 4096)
                        if not chunk:
                            break
                        buf += chunk
                        # 按行拆分
                        while b"\n" in buf:
                            line_b, buf = buf.split(b"\n", 1)
                            line = line_b.decode("utf-8", errors="replace").strip()
                            if line:
                                with lock:
                                    _MCP_STDERR_BUFFERS[pid].append(line)
                    except (OSError, BlockingIOError, ValueError):
                        break
            else:
                # 回退：TextIOWrapper 逐行读取
                for line in proc.stderr:
                    line = line.strip()
                    if line:
                        with lock:
                            _MCP_STDERR_BUFFERS[pid].append(line)
        except Exception:
            pass

    t = threading.Thread(target=_reader, daemon=True, name=f"mcp-stderr-{name}-{pid}")
    t.start()


def _get_stderr_lines(proc: subprocess.Popen) -> str:
    """获取已收集的 stderr 内容（用于诊断输出）。"""
    pid = proc.pid
    lock = _MCP_STDERR_LOCKS.get(pid)
    buf = _MCP_STDERR_BUFFERS.get(pid, [])
    if lock:
        with lock:
            return "\n".join(buf[-50:])  # 最近 50 行
    return "\n".join(buf[-50:])


# Schema 缓存单例
_schema_cache: Optional[MCPSchemaCache] = None

def _get_schema_cache() -> MCPSchemaCache:
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = MCPSchemaCache()
    return _schema_cache

# Shell/bash 类工具名过滤列表
MCP_TOOL_FILTER = {
    "shell", "bash", "sh", "zsh", "fish", "terminal", "execute_command",
    "run_command", "exec", "spawn", "pty", "tty",
}


def _ensure_dir(path: str) -> None:
    """安全创建目录（兼容安卓等 exist_ok 不生效的平台，处理旧文件冲突）"""
    if os.path.isfile(path):
        # 旧版本 manage set mcp 把 mcp 写成文件，现在它是目录，删掉重建
        os.remove(path)
    if not os.path.isdir(path):
        try:
            os.makedirs(path, exist_ok=True)
        except FileExistsError:
            pass  # 目录已被其他线程创建


def _get_mcp_config_dir(user_home_dir: str = None) -> str:
    """获取用户 MCP 配置目录（按用户隔离）"""
    home = user_home_dir or USER_HOME_DIR
    return os.path.join(home, ".config", "onyx", "mcp")


def _get_mcp_config_path(user_home_dir: str = None) -> str:
    """获取用户 MCP 配置文件路径"""
    return os.path.join(_get_mcp_config_dir(user_home_dir), "mcp.json")


def _migrate_mcp_config_if_needed(user_home_dir: str = None) -> str:
    """
    如果用户目录下没有 MCP 配置，从全局模板复制一份。
    返回用户配置文件路径。
    """
    user_path = _get_mcp_config_path(user_home_dir)
    if os.path.exists(user_path):
        return user_path

    # 从全局模板复制（保留 {CWD} 模板标记，运行时动态替换为当前工作目录）
    global_path = MCP_CONFIG_PATH
    if os.path.exists(global_path):
        _ensure_dir(os.path.dirname(user_path))
        try:
            with open(global_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            # 保持 {CWD} 模板不变，运行时由 connect_mcp_server 动态替换
            with open(user_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return user_path
        except Exception:
            pass

    # 没有模板，创建默认配置（使用 {CWD} 模板标记）
    default_config = {
        "_comment": "Onyx MCP server registry — per-user config",
        "servers": {
            "filesystem": {
                "name": "filesystem",
                "description": "文件系统操作 (read/write/edit/list/search)",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "{CWD}"],
                "auto_start": False,
                "installed": False
            }
        }
    }
    _ensure_dir(os.path.dirname(user_path))
    with open(user_path, "w", encoding="utf-8") as f:
        json.dump(default_config, f, ensure_ascii=False, indent=2)
    return user_path


def _validate_mcp_mount_path(server_info: dict, user_home_dir: str) -> bool:
    """
    校验 MCP server 的挂载路径是否安全。
    允许：用户主目录内 或 当前工作目录内。
    返回 True 表示安全，False 表示越界。
    """
    args = server_info.get("args", [])
    user_home = os.path.realpath(user_home_dir)
    cwd = os.path.realpath(os.getcwd())

    def _is_under(path: str, parent: str) -> bool:
        return path == parent or path.startswith(parent + os.sep)

    for i, arg in enumerate(args):
        if arg.startswith("/") and not arg.startswith("-"):
            real_path = os.path.realpath(arg) if os.path.exists(arg) else os.path.abspath(arg)
            # 检查是否在用户主目录内或当前工作目录内
            if _is_under(real_path, user_home) or _is_under(real_path, cwd):
                continue
            else:
                return False
    return True


def _load_mcp_config(user_home_dir: str = None) -> Dict:
    """加载 MCP 服务器注册表（按用户）"""
    config_path = _migrate_mcp_config_if_needed(user_home_dir)
    if not os.path.exists(config_path):
        return {"servers": {}}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"servers": {}}


def _save_mcp_config(config: Dict, user_home_dir: str = None) -> None:
    """保存 MCP 服务器注册表（按用户）"""
    config_path = _get_mcp_config_path(user_home_dir)
    _ensure_dir(os.path.dirname(config_path))
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# MCP 调试开关（由 handle_ai 根据 --debug 设置）
_MCP_DEBUG = False

# AI 中断标志（Ctrl+C 打断思考时置位）
_AI_INTERRUPTED = False
_MCP_DEBUG_START: float = 0.0  # --debug 启动时的基准时间


def _mcp_debug(msg: str) -> None:
    """--debug 模式实时追踪：打印带时间戳的消息（输出到 stderr 确保立即可见）"""
    if _MCP_DEBUG:
        import sys as _sys
        elapsed = (time.time() - _MCP_DEBUG_START) if _MCP_DEBUG_START else 0
        _sys.stderr.write(f"[{elapsed:06.2f}s] MCP {msg}\n")
        _sys.stderr.flush()


def _mcp_debug_enter(func_name: str) -> None:
    """函数进入时的 debug 追踪"""
    if _MCP_DEBUG:
        import sys as _sys
        elapsed = (time.time() - _MCP_DEBUG_START) if _MCP_DEBUG_START else 0
        _sys.stderr.write(f"[{elapsed:06.2f}s] → {func_name}\n")
        _sys.stderr.flush()


def _mcp_debug_exit(func_name: str, ok: bool = True, detail: str = "") -> None:
    """函数退出时的 debug 追踪"""
    if _MCP_DEBUG:
        import sys as _sys
        elapsed = (time.time() - _MCP_DEBUG_START) if _MCP_DEBUG_START else 0
        status = "OK" if ok else "FAIL"
        extra = f" ({detail})" if detail else ""
        _sys.stderr.write(f"[{elapsed:06.2f}s] ← {func_name} {status}{extra}\n")
        _sys.stderr.flush()


def _mcp_t(cn: str, en: str) -> str:
    """MCP 消息双语：根据当前语言返回中文或英文"""
    return cn if get_current_lang() == "chinese" else en


def _mcp_send(proc: subprocess.Popen, msg: Dict) -> None:
    """通过 stdin 发送 JSON-RPC 消息（换行分隔 JSON，MCP stdio 传输标准）"""
    body = json.dumps(msg, ensure_ascii=False) + "\n"
    method = msg.get("method", "?")
    _mcp_debug_enter(f"_mcp_send({method})")
    _mcp_debug(f"SEND → {body[:200]}{'...' if len(body) > 200 else ''}")
    _mcp_debug(f"  stdin type={type(proc.stdin).__name__}, closed={getattr(proc.stdin, 'closed', '?')}")
    try:
        proc.stdin.write(body)
        proc.stdin.flush()
        _mcp_debug("  write+flush OK")
        _mcp_debug_exit("_mcp_send", ok=True)
    except (BrokenPipeError, OSError) as e:
        _mcp_debug(f"  FAILED: {e}")
        _mcp_debug_exit("_mcp_send", ok=False, detail=str(e))
        raise ConnectionError(f"MCP server disconnected: {e}")


def _mcp_recv(proc: subprocess.Popen, timeout: float = 30.0) -> Optional[Dict]:
    """通过 stdout 接收 JSON-RPC 消息（换行分隔 JSON）
    
    关键修复：用 os.read(fd, 1) 直接读原始文件描述符，而不是 proc.stdout.read(1)。
    后者在 text=True 时经过 TextIOWrapper → BufferedReader 多层缓冲，
    导致 select.select (监视内核 fd) 与 read (读 Python 缓冲区) 脱节，
    表现为 select 频繁超时（每次最多等 1s），整行 JSON 看起来像"立即卡死"。
    """
    import select as _select
    import os as _os
    _mcp_debug_enter(f"_mcp_recv(timeout={timeout}s)")
    deadline = time.time() + timeout
    fd = proc.stdout.fileno() if hasattr(proc.stdout, 'fileno') else proc.stdout
    _mcp_debug(f"RECV waiting (timeout={timeout}s, fd={fd}, stdout_type={type(proc.stdout).__name__})")
    line_bytes = b""
    while True:
        # 检查中断标志（Ctrl+C），允许用户打断卡住的 MCP 请求
        if _AI_INTERRUPTED:
            _mcp_debug(f"RECV interrupted by user after {len(line_bytes)} bytes")
            _mcp_debug_exit("_mcp_recv", ok=False, detail="interrupted")
            return None
        remaining = deadline - time.time()
        if remaining <= 0:
            _mcp_debug(f"RECV TIMEOUT after {len(line_bytes)} bytes: {line_bytes[:200]}")
            _mcp_debug_exit("_mcp_recv", ok=False, detail="timeout")
            return None
        if _select.select([fd], [], [], min(remaining, 1.0))[0]:
            try:
                ch = _os.read(fd, 1)  # 直接读原始 fd，与 select 监视的是同一层
            except (OSError, BlockingIOError):
                _mcp_debug(f"RECV os.read error, fd may be closed")
                _mcp_debug_exit("_mcp_recv", ok=False, detail="os.read error")
                return None
            if not ch:
                _mcp_debug(f"RECV EOF after {len(line_bytes)} bytes")
                _mcp_debug_exit("_mcp_recv", ok=False, detail="EOF")
                return None
            # os.read 始终返回 bytes，无需 isinstance 判断
            if ch == b'\n':
                _mcp_debug(f"RECV \\n (total {len(line_bytes)} bytes)")
                break
            line_bytes += ch
        else:
            continue
    line = line_bytes.decode('utf-8').strip()
    _mcp_debug(f"RECV ← {line[:200]}{'...' if len(line) > 200 else ''}")
    if not line:
        _mcp_debug_exit("_mcp_recv", ok=False, detail="empty line")
        return None
    try:
        result = json.loads(line)
        _mcp_debug_exit("_mcp_recv", ok=True, detail=f"{len(line_bytes)} bytes")
        return result
    except json.JSONDecodeError as e:
        _mcp_debug(f"RECV JSON parse error: {e}")
        _mcp_debug_exit("_mcp_recv", ok=False, detail="JSON parse error")
        return None


def _mcp_request(proc: subprocess.Popen, method: str, params: Dict = None,
                 msg_id: int = None) -> Optional[Dict]:
    """发送 JSON-RPC 请求并等待响应"""
    _mcp_debug_enter(f"_mcp_request({method})")
    if msg_id is None:
        msg_id = int(time.time() * 1000) % 1000000
    _mcp_send(proc, {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "params": params or {},
    })
    result = _mcp_recv(proc)
    _mcp_debug_exit(f"_mcp_request({method})", ok=result is not None and "error" not in result)
    return result


def _mcp_notification(proc: subprocess.Popen, method: str, params: Dict = None) -> None:
    """发送 JSON-RPC 通知（无响应）"""
    _mcp_send(proc, {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
    })


def is_mcp_server_running(name: str) -> bool:
    """检查 MCP server 是否在运行"""
    if name in MCP_SERVER_PROCESSES:
        proc = MCP_SERVER_PROCESSES[name]
        return proc.poll() is None
    return False


def _ensure_npx_available() -> bool:
    """检查 npx 是否可用"""
    try:
        result = subprocess.run(
            ["npx", "--version"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


# ── 推荐自动安装的 MCP 服务器列表 ──
_AUTO_INSTALL_MCP = [
    {"name": "fetch", "desc": "网页抓取/HTTP API"},
]

def install_default_mcp_server(user_home_dir: str = None, auto_extras: bool = False) -> bool:
    """标记 filesystem 为已安装。auto_extras=True 时同时安装推荐 MCP 模块。"""
    home = user_home_dir or USER_HOME_DIR
    config = _load_mcp_config(home)
    fs_config = config.get("servers", {}).get("filesystem", {})

    if not _ensure_npx_available():
        return False

    # 1. 确保 filesystem 已标记
    with MCP_INSTALL_LOCK:
        config2 = _load_mcp_config(home)
        fs2 = config2.get("servers", {}).get("filesystem", {})
        if not fs2.get("installed", False):
            fs_config["installed"] = True
            config.setdefault("servers", {})["filesystem"] = fs_config
            _save_mcp_config(config, home)

    # 2. 自动安装推荐 MCP（仅在 preload 时触发，避免阻塞 AI 调用）
    if auto_extras:
        for mcp_info in _AUTO_INSTALL_MCP:
            mcp_name = mcp_info["name"]
            mcp_desc = mcp_info["desc"]
            cfg = _load_mcp_config(home)
            if mcp_name in cfg.get("servers", {}):
                continue
            pkg = f"@modelcontextprotocol/server-{mcp_name}"
            console.print(_mcp_t(f"📦 自动安装 {mcp_name} ({mcp_desc})...", f"📦 Auto-installing {mcp_name} ({mcp_desc})..."), style="dim")
            result = install_mcp_server_cmd(mcp_name, pkg)
            if "✅" in result:
                try:
                    connect_mcp_server(mcp_name, home)
                except Exception:
                    pass

    return True


def connect_mcp_server(name: str = "filesystem", user_home_dir: str = None) -> Optional[subprocess.Popen]:
    """启动并初始化 MCP 服务器（同步阻塞直到 initialize 完成）"""
    _mcp_debug_enter(f"connect_mcp_server({name})")
    if is_mcp_server_running(name):
        _mcp_debug(f"Server '{name}' already running, returning cached proc")
        _mcp_debug_exit("connect_mcp_server", ok=True, detail="already running")
        return MCP_SERVER_PROCESSES[name]

    home = user_home_dir or USER_HOME_DIR
    config = _load_mcp_config(home)
    server_info = config.get("servers", {}).get(name)
    if not server_info:
        console.print(_mcp_t(
            f"❌ MCP server '{name}' 未注册",
            f"❌ MCP server '{name}' not registered"
        ), style="bold red")
        return None

    # 检查是否已安装（避免对未安装的 server 反复尝试启动）
    if not server_info.get("installed", False):
        console.print(
            f"⚠️ MCP server '{name}' 尚未安装。请执行: ai -mcp install {name}",
            style="bold yellow"
        )
        return None

    # 安全校验：挂载路径是否安全
    if not _validate_mcp_mount_path(server_info, home):
        lang = get_current_lang()
        args = server_info.get("args", [])
        bad_paths = [a for a in args if a.startswith("/") and not a.startswith("-")]
        fallback_dir = os.getcwd()
        msg = (
            f"⚠️ MCP server '{name}' 挂载路径 {bad_paths} 超出安全范围！\n"
            f"   用户目录: {home}\n"
            f"   已自动修正为当前工作目录。如需自定义请手动编辑配置文件。"
        ) if lang == "chinese" else (
            f"⚠️ MCP server '{name}' mount path {bad_paths} outside safe range!\n"
            f"   User home: {home}\n"
            f"   Auto-corrected to CWD. Edit config manually to customize."
        )
        console.print(msg, style="bold yellow")
        # 自动修正：替换越界路径为 CWD
        fixed_args = []
        for a in server_info.get("args", []):
            if a.startswith("/") and not a.startswith("-"):
                fixed_args.append(fallback_dir)
            else:
                fixed_args.append(a)
        server_info["args"] = fixed_args

    cmd = server_info.get("command", "npx")
    args = list(server_info.get("args", []))  # 拷贝避免修改原配置

    # ── 动态路径替换：{CWD} → 当前工作目录（每次 ai 命令时实时获取）──
    cwd_now = os.getcwd()
    for i, arg in enumerate(args):
        if arg == "{CWD}":
            args[i] = cwd_now
        elif arg == "{USER_HOME}":
            args[i] = home

    # Termux: npx 在 FUSE/exFAT 上极不可靠，已历经 EACCES → TAR_ENTRY_ERROR → ECOMPROMISED
    # 改为全局安装后直接运行二进制，彻底绕过 npx 的临时安装和缓存机制
    env = os.environ.copy()
    _is_on_termux = False
    try:
        from lib.get_lib_path import _is_termux_environment
        if _is_termux_environment():
            _is_on_termux = True
            from lib.get_lib_path import TERMUX_PREFIX, TERMUX_HOME

            # Termux 上恢复真实 HOME（npm 用 $HOME 解析 prefix 等路径）
            env["HOME"] = TERMUX_HOME

            # 查找全局安装的 MCP filesystem 二进制
            mcp_bin = os.path.join(TERMUX_PREFIX, "bin", "mcp-server-filesystem")
            if not os.path.exists(mcp_bin):
                # 首次使用：npm install -g（仅一次，后续直接运行二进制）
                console.print(_mcp_t(
                    "📱 Termux: 首次安装 MCP filesystem server（约 30-60s）...",
                    "📱 Termux: Installing MCP filesystem server (~30-60s)..."
                ), style="cyan")
                termux_cache = os.path.join(TERMUX_PREFIX, "tmp", "npm_cache")
                _ensure_dir(termux_cache)
                install_env = env.copy()
                install_env["NPM_CONFIG_CACHE"] = termux_cache
                install_env["npm_config_cache"] = termux_cache
                install_env["npm_config_prefix"] = TERMUX_PREFIX
                result = subprocess.run(
                    ["npm", "install", "-g", "@modelcontextprotocol/server-filesystem"],
                    capture_output=True, text=True, timeout=120,
                    env=install_env
                )
                if result.returncode != 0:
                    console.print(
                        _mcp_t("❌ Termux: npm install -g 失败", "❌ Termux: npm install -g failed") +
                        f"\n{result.stderr[:500]}", style="bold red")
                    return None
                if not os.path.exists(mcp_bin):
                    console.print(
                        f"❌ Termux: 安装完成但 binary 不存在\n"
                        f"   预期路径: {mcp_bin}\n"
                        f"   npm stdout: {result.stdout[:300]}",
                        style="bold red"
                    )
                    return None
                console.print(_mcp_t("✅ Termux: MCP server 就绪", "✅ Termux: MCP server ready"), style="green")

            # 直接用二进制 + PTY 替代 stdbuf -o0
            # Node.js stdout 在 pipe 模式下全缓冲，stdbuf 在 Termux 不稳定
            # PTY 天然行缓冲，彻底解决 JSON-RPC 握手超时
            cmd = mcp_bin
            # binary 直接运行只需挂载路径，不需要 npx 的 -y 等参数
            args = [home]
            import pty as _pty
            import termios as _termios
            _master_fd, _slave_fd = _pty.openpty()
            _mcp_debug(f"PTY created: master={_master_fd}, slave={_slave_fd}")
            # PTY 设为原始模式：关闭行缓冲(ICANON)、输出处理(OPOST)、回显(ECHO)、信号(ISIG)
            _attrs = _termios.tcgetattr(_slave_fd)
            _mcp_debug(f"PTY attrs: iflag=0x{_attrs[0]:x} oflag=0x{_attrs[1]:x} cflag=0x{_attrs[2]:x} lflag=0x{_attrs[3]:x}")
            _attrs[0] = _attrs[0] & ~(_termios.ICRNL | _termios.INLCR)  # 输入不转换
            _attrs[1] = _attrs[1] & ~_termios.OPOST                      # 输出不转换
            _attrs[3] = _attrs[3] & ~(_termios.ICANON | _termios.ECHO | _termios.ISIG)
            _termios.tcsetattr(_slave_fd, _termios.TCSANOW, _attrs)
            _mcp_debug(f"PTY raw mode: lflag=0x{_attrs[3]:x} ICANON={'ON' if _attrs[3] & _termios.ICANON else 'OFF'} OPOST={'ON' if _attrs[1] & _termios.OPOST else 'OFF'}")
            _mcp_debug(f"Starting: {cmd} {' '.join(args)}")
            proc = subprocess.Popen(
                [cmd] + args,
                stdin=subprocess.PIPE,
                stdout=_slave_fd,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            _mcp_debug(f"Process started pid={proc.pid}, stdin_type={type(proc.stdin).__name__}")
            os.close(_slave_fd)
            # 用 PTY master 替换 proc.stdout（无缓冲二进制，直接走 OS read）
            proc.stdout = os.fdopen(_master_fd, 'rb', buffering=0)
            _mcp_debug(f"stdout replaced with PTY master (rb, buffering=0)")
            # 启动 stderr 读取线程（防止管道死锁）
            _start_stderr_reader(proc, name)
            # 跳过下面的通用 Popen 路径
            raise StopIteration
    except StopIteration:
        pass
    except Exception:
        pass

    if not (_is_on_termux and 'proc' in dir()):
        _mcp_debug(f"Non-Termux: starting {cmd} {' '.join(args)}")

        # Node.js 在 pipe 模式下 stdout 全缓冲（默认 16KB），
        # JSON-RPC 响应通常远小于此阈值，会长期滞留在缓冲区不发出。
        # 导致 Python _mcp_recv 在 select+read 上无限等待。
        # Termux 用 PTY 避开了这个问题；非 Termux 用 stdbuf 强制行缓冲。
        _full_cmd = [cmd] + args
        if shutil.which("stdbuf"):
            _full_cmd = ["stdbuf", "-o0"] + _full_cmd
            _mcp_debug(f"stdbuf available, using: stdbuf -o0 {' '.join([cmd] + args)}")
        else:
            # 备选：设置 NODE_OPTIONS 禁止警告输出（防 stderr 洪水），
            # 但无法解决 Node stdout 缓冲问题。没有 stdbuf 时只能接受风险。
            env.setdefault("NODE_NO_WARNINGS", "1")
            _mcp_debug("stdbuf not available, Node.js pipe buffering risk remains")

        try:
            proc = subprocess.Popen(
                _full_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            _mcp_debug(f"Process started pid={proc.pid}")
        except FileNotFoundError:
            console.print(_mcp_t(
                f"❌ 命令 '{cmd}' 未找到，请确认已安装",
                f"❌ Command '{cmd}' not found, please verify installation"
            ), style="bold red")
            return None
        except Exception as e:
            console.print(_mcp_t(
                f"❌ 启动 MCP server 失败: {str(e)}",
                f"❌ Failed to start MCP server: {str(e)}"
            ), style="bold red")
            return None

    # 立即启动 stderr 读取线程，防止管道缓冲区满导致子进程死锁
    # （npx 在首次下载时 stderr 输出大量进度条，很容易超过 64KB 管道缓冲）
    _start_stderr_reader(proc, name)

    # 快速诊断：等 2s 看进程是否立即崩溃
    _mcp_debug(f"Waiting 2s, checking liveness... pid={proc.pid}")
    time.sleep(2)
    exit_code = proc.poll()
    _mcp_debug(f"Process status: exit_code={exit_code}, pid={proc.pid}")
    # 读取启动 stderr（从收集器获取，不再直接读管道）
    early_stderr = _get_stderr_lines(proc)
    if early_stderr:
        _mcp_debug(f"Startup stderr: {early_stderr[:500]}")
    if exit_code is not None:
        stderr_output = _get_stderr_lines(proc)
        _mcp_debug(f"stderr: {stderr_output[:500]}")
        console.print(
            _mcp_t(
                f"❌ MCP server 启动后立即退出 (exit={exit_code})\n   命令: {cmd} {' '.join(args)}\n   stderr: {stderr_output[:500] or '(无)'}",
                f"❌ MCP server exited immediately (exit={exit_code})\n   Command: {cmd} {' '.join(args)}\n   stderr: {stderr_output[:500] or '(none)'}"
            ),
            style="bold red"
        )
        return None

    # 发送 initialize 请求
    _mcp_debug("Sending initialize request...")
    _mcp_send(proc, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "Onyx", "version": "2.7.0"},
        },
    })
    # Termux 上二进制已在本地，30s 足够；非 Termux 首次 npx 下载可能较慢给 90s
    init_timeout = 30.0 if _is_on_termux else 90.0
    _mcp_debug(f"Waiting initialize response (timeout={init_timeout}s)...")
    init_result = _mcp_recv(proc, timeout=init_timeout)
    _mcp_debug(f"Initialize result: {'OK' if init_result and 'error' not in init_result else f'FAIL {init_result}'}")

    if init_result is None:
        exit_code = proc.poll()
        stderr_output = ""
        try:
            stderr_output = proc.stderr.read()
        except Exception:
            pass
        proc.kill()
        if exit_code is not None:
            err_hint_cn = stderr_output[:500] if stderr_output else "(无 stderr)"
            err_hint_en = stderr_output[:500] if stderr_output else "(no stderr)"
            console.print(
                _mcp_t(
                    f"❌ MCP server 进程异常退出 (exit={exit_code})\n   命令: {cmd} {' '.join(args)}\n   stderr: {err_hint_cn}",
                    f"❌ MCP server crashed (exit={exit_code})\n   Command: {cmd} {' '.join(args)}\n   stderr: {err_hint_en}"
                ),
                style="bold red"
            )
        else:
            # 超时 — 收集更多诊断信息
            diag_lines_cn = [f"   命令: {cmd} {' '.join(args)}"]
            diag_lines_en = [f"   Command: {cmd} {' '.join(args)}"]
            if stderr_output:
                diag_lines_cn.append(f"   stderr: {stderr_output[:500]}")
                diag_lines_en.append(f"   stderr: {stderr_output[:500]}")
            diag_lines_cn.append(f"   提示: MCP server 握手超时（已等待{int(init_timeout)}s），请检查进程是否正常运行")
            diag_lines_en.append(f"   Hint: MCP server handshake timed out after {int(init_timeout)}s, check if the process is running normally")
            console.print(_mcp_t(
                f"❌ MCP server 初始化超时 ({int(init_timeout)}s)\n" + "\n".join(diag_lines_cn),
                f"❌ MCP server init timeout ({int(init_timeout)}s)\n" + "\n".join(diag_lines_en)
            ), style="bold red")
        return None

    if "error" in init_result:
        proc.kill()
        console.print(_mcp_t(
            f"❌ MCP server 初始化失败: {init_result['error']}",
            f"❌ MCP server init failed: {init_result['error']}"
        ), style="bold red")
        return None

    # 发送 initialized 通知
    _mcp_notification(proc, "notifications/initialized")

    MCP_SERVER_PROCESSES[name] = proc
    MCP_TOOLS_CACHE.pop(name, None)  # 清空旧缓存

    # 立即拉取工具列表并缓存（避免后续 get_mcp_tools 再次阻塞请求 tools/list）
    # 之前这里只做握手就返回，紧接着 build_mcp_tools_prompt → _discover_mcp_tools
    # 又会发起一次 tools/list 阻塞请求，如果 Node.js stdout 全缓冲或 server 慢响应
    # 就会表现为"AI 立即卡死"
    _mcp_debug("准备发送 tools/list 请求...")
    tools_result = _mcp_request(proc, "tools/list", msg_id=2)
    _mcp_debug(f"tools/list 返回: {'OK' if tools_result and 'result' in tools_result else 'FAIL'}")
    if tools_result and "result" in tools_result:
        tools = tools_result["result"].get("tools", [])
        _mcp_debug(f"解析到 {len(tools)} 个工具")
        MCP_TOOLS_CACHE[name] = tools
        _mcp_debug("已写入 MCP_TOOLS_CACHE")
        # 同步到新版 Registry
        try:
            _mcp_debug("同步到 Registry...")
            registry = get_registry()
            registry.replace_server(name, tools)
            _mcp_debug("Registry 同步完成")
            # 写入 Schema 缓存（加速下次冷启动）
            home = user_home_dir or USER_HOME_DIR
            _mcp_debug(f"写入 Schema 缓存 (home={home[:30]}...)...")
            config = _load_mcp_config(home)
            server_info2 = config.get("servers", {}).get(name, {})
            if server_info2:
                fp = MCPSchemaCache.fingerprint(server_info2)
                _get_schema_cache().put(name, fp, tools)
                _mcp_debug(f"Schema 缓存写入完成 (fp={fp})")
        except Exception as _e:
            _mcp_debug(f"Registry/缓存同步异常: {_e}")

    # 标记首次连接成功，后续启动仅健康检查
    try:
        _mcp_debug("写入 mcp_connected.flag...")
        flag_path = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "mcp_connected.flag")
        _ensure_dir(os.path.dirname(flag_path))
        with open(flag_path, "w") as _f:
            _f.write(str(time.time()))
        _mcp_debug("mcp_connected.flag 写入完成")
    except Exception as _e2:
        _mcp_debug(f"mcp_connected.flag 写入异常: {_e2}")

    _mcp_debug("即将输出 ✅ 已连接...")
    console.print(_mcp_t(f"✅ MCP server '{name}' 已连接", f"✅ MCP server '{name}' connected"), style="dim")
    _mcp_debug("✅ 已连接输出完成")
    _mcp_debug_exit("connect_mcp_server", ok=True, detail=f"pid={proc.pid}")
    return proc


def preload_mcp_servers(user_home_dir: str = None) -> None:
    """
    预加载 MCP server（后台线程，不阻塞主流程）。
    在 Main.py 初始化阶段调用。
    """
    global MCP_PRELOADED
    with MCP_PRELOAD_LOCK:
        if MCP_PRELOADED:
            return
        MCP_PRELOADED = True  # 防止重复预加载

    home = user_home_dir or USER_HOME_DIR

    def _do_preload():
        try:
            _migrate_mcp_config_if_needed(home)
            if install_default_mcp_server(home, auto_extras=True):
                connect_mcp_server("filesystem", home)
                tools = _discover_mcp_tools("filesystem", home)
                if tools:
                    console.print(_mcp_t(
                        f"✅ MCP 预加载: {len(tools)} 个工具就绪",
                        f"✅ MCP preload: {len(tools)} tools ready"
                    ), style="dim")
                    # 标记预加载已完成，后续启动跳过
                    try:
                        flag_path = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "mcp_preloaded.flag")
                        _ensure_dir(os.path.dirname(flag_path))
                        with open(flag_path, "w") as _f:
                            _f.write(str(time.time()))
                    except Exception:
                        pass
        except Exception as e:
            pass  # 预加载失败不打扰用户

    t = threading.Thread(target=_do_preload, daemon=True)
    t.start()


def health_check_mcp(user_home_dir: str = None) -> None:
    """
    后台检查 MCP server 健康状态 + 工具增量更新。
    每次 AI 命令后调用（非阻塞）。
    """
    home = user_home_dir or USER_HOME_DIR

    def _do_health_check():
        global _MCP_LAST_HEALTH_CHECK
        now = time.time()
        if now - _MCP_LAST_HEALTH_CHECK < MCP_HEALTH_CHECK_INTERVAL:
            return
        _MCP_LAST_HEALTH_CHECK = now

        for name in list(MCP_SERVER_PROCESSES.keys()):
            if not is_mcp_server_running(name):
                console.print(_mcp_t(
                    f"⚠️ MCP server '{name}' 已断开，尝试重连...",
                    f"⚠️ MCP server '{name}' disconnected, reconnecting..."
                ), style="dim yellow")
                MCP_SERVER_PROCESSES.pop(name, None)
                connect_mcp_server(name, home)

        # 增量更新工具缓存
        for name in list(MCP_SERVER_PROCESSES.keys()):
            try:
                old_tools = MCP_TOOLS_CACHE.get(name, [])
                old_names = {t.get("name") for t in old_tools}
                new_tools = _discover_mcp_tools(name, home)
                new_names = {t.get("name") for t in new_tools}
                added = new_names - old_names
                removed = old_names - new_names
                if added or removed:
                    MCP_TOOLS_CACHE[name] = new_tools
                    if added:
                        console.print(_mcp_t(
                            f"🔧 MCP 工具新增: {added}",
                            f"🔧 MCP tools added: {added}"
                        ), style="dim")
                    if removed:
                        console.print(_mcp_t(
                            f"🔧 MCP 工具移除: {removed}",
                            f"🔧 MCP tools removed: {removed}"
                        ), style="dim")
            except Exception:
                pass

    t = threading.Thread(target=_do_health_check, daemon=True)
    t.start()


def _schedule_mcp_health_check(user_home_dir: str = None) -> None:
    """每次 AI 命令后调度后台健康检查（非阻塞）"""
    health_check_mcp(user_home_dir)


def _discover_mcp_tools(name: str = "filesystem", user_home_dir: str = None) -> List[Dict]:
    """从 MCP server 获取工具列表（内部，带缓存 + Registry 同步）"""
    if name in MCP_TOOLS_CACHE:
        return MCP_TOOLS_CACHE[name]

    proc = connect_mcp_server(name, user_home_dir)
    if proc is None:
        return []

    result = _mcp_request(proc, "tools/list", msg_id=2)
    if result is None or "error" in result:
        console.print(_mcp_t(
            f"⚠️ 获取 MCP 工具列表失败: {result.get('error', 'timeout') if result else 'timeout'}",
            f"⚠️ Failed to get MCP tool list: {result.get('error', 'timeout') if result else 'timeout'}"
        ), style="yellow")
        return []

    tools = result.get("result", {}).get("tools", [])
    MCP_TOOLS_CACHE[name] = tools

    # ── 同步到新版 Registry ──
    try:
        registry = get_registry()
        registry.replace_server(name, tools)
        # 写入 Schema 缓存（加速下次冷启动）
        home = user_home_dir or USER_HOME_DIR
        config = _load_mcp_config(home)
        server_info = config.get("servers", {}).get(name, {})
        if server_info:
            fp = MCPSchemaCache.fingerprint(server_info)
            _get_schema_cache().put(name, fp, tools)
    except Exception:
        pass

    return tools


def get_mcp_tools(name: str = "filesystem", user_home_dir: str = None) -> List[Dict]:
    """
    获取 MCP 工具列表，过滤掉 shell/bash 类工具。
    优先从 Registry 读取（支持 lazy 加载的缓存 schema），回退到旧 MCP_TOOLS_CACHE。
    返回: [{"name": "...", "description": "...", "inputSchema": {...}}, ...]
    """
    # 尝试从 Registry 获取（可能已通过缓存预加载）
    registry = get_registry()
    registry_tools = registry.get_by_server(name)
    if registry_tools:
        all_tools = registry_tools
    else:
        # 回退：旧版缓存（会触发 connect + tools/list）
        all_tools = _discover_mcp_tools(name, user_home_dir)
    filtered = []
    for tool in all_tools:
        tool_name = (tool.get("name") or "").lower()
        # 过滤 shell/bash 类工具
        if tool_name in MCP_TOOL_FILTER:
            continue
        # 子串匹配过滤
        blocked = any(
            kw in tool_name
            for kw in ["shell", "bash", "exec", "spawn", "terminal"]
        )
        if blocked:
            continue
        filtered.append(tool)
    return filtered


def build_mcp_tools_prompt(lang: str = "chinese", user_home_dir: str = None) -> str:
    """
    构建注入给 AI 的工具说明提示词。
    文件操作已由原生标记语言覆盖，这里只展示非文件类 MCP 工具。
    """
    _mcp_debug_enter("build_mcp_tools_prompt")
    tools = get_mcp_tools(user_home_dir=user_home_dir)

    # ── 过滤掉 filesystem 工具（文件操作用原生标记语言）──
    non_file_tools = []
    for t in tools:
        name = t.get("name", "")
        # filesystem 工具的常见名
        if name in ("read_file", "write_file", "edit_file",
                     "create_directory", "list_directory",
                     "directory_tree", "move_file", "copy_file",
                     "delete_file", "delete_directory",
                     "get_file_info", "search_files", "search_content",
                     "glob", "find_on_path", "get_workspace_folders"):
            continue
        non_file_tools.append(t)

    _mcp_debug(f"get_mcp_tools 返回 {len(tools)} 个工具，过滤后 {len(non_file_tools)} 个")

    if not non_file_tools:
        # 没有非文件 MCP 工具，返回空字符串（不占用 prompt 空间）
        _mcp_debug_exit("build_mcp_tools_prompt", ok=True, detail="only file tools, skipped")
        return ""

    lines = []
    if lang == "chinese":
        lines.append("## 非文件工具（MCP 兜底）")
        lines.append("调用格式: [tool:<工具名>] 换行 JSON参数 换行 [tool:<工具名>:done]")
        lines.append("参数必须是合法 JSON。")
    else:
        lines.append("## Non-file Tools (MCP Fallback)")
        lines.append("Call format: [tool:<name>] newline JSON-args newline [tool:<name>:done]")
        lines.append("Arguments MUST be valid JSON.")

    lines.append("")

    for tool in non_file_tools:
        raw_name = tool.get("name", "?")
        full_name = raw_name  # 不再加 mcp__filesystem__ 前缀
        desc = tool.get("description", "")
        schema = tool.get("inputSchema", {})
        props = schema.get("properties", {})
        required = schema.get("required", [])

        # 构建 JSON 参数说明
        param_entries = []
        for pname, pinfo in props.items():
            req_mark = " (必填)" if pname in required else ""
            ptype = pinfo.get("type", "string")
            pdesc = pinfo.get("description", "")
            param_entries.append(f'    "{pname}": {{{{ {ptype} }}}}{req_mark} — {pdesc}')

        lines.append(f"- **{full_name}**: {desc}")
        if param_entries:
            if lang == "chinese":
                lines.append("  JSON 参数:")
            else:
                lines.append("  JSON params:")
            lines.extend(param_entries)

        # edit_file 特殊：SEARCH/REPLACE
        if raw_name == "edit_file":
            if lang == "chinese":
                lines.append('  使用 SEARCH/REPLACE: "old_string" 精确匹配且唯一, "new_string" 替换文本')
            else:
                lines.append('  SEARCH/REPLACE: "old_string" exact+unique match, "new_string" replacement')

        # 示例
        if raw_name == "read_file":
            lines.append(f'  示例: [tool:{full_name}]\n  {{"path": "/home/user/test.py"}}\n  [tool:{full_name}:done]')
        elif raw_name == "write_file":
            lines.append(f'  示例: [tool:{full_name}]\n  {{"path": "/home/u/out.txt", "content": "hello"}}\n  [tool:{full_name}:done]')
        elif raw_name == "edit_file":
            lines.append(f'  示例: [tool:{full_name}]\n  {{"path": "/a.py", "old_string": "return a+b", "new_string": "return a+b+1"}}\n  [tool:{full_name}:done]')
        else:
            lines.append(f'  示例: [tool:{full_name}]\n  {{"param": "value"}}\n  [tool:{full_name}:done]')

        lines.append("")

    # ── 分块提示（原生标记语言场景）──
    if lang == "chinese":
        lines.append("📐 **大文件操作建议**")
        lines.append("- 超过 200 行的文件，修改超过 70% 时直接用 `[WRITE:]` 全量重写")
        lines.append("- 局部修改用 `[EDIT:path:N-M]` 行号替换，省 Token 且不出错")
        lines.append("- 每个 `[EDIT:]` 的 SEARCH 块不超过 50 行")
    else:
        lines.append("📐 **Large File Tips**")
        lines.append("- Over 200 lines & >70% change → use `[WRITE:]` to rewrite entirely")
        lines.append("- Local changes → `[EDIT:path:N-M]` line-range replacement (cheaper, safer)")
        lines.append("- Each `[EDIT:]` SEARCH block ≤ 50 lines")
    lines.append("")

    result = "\n".join(lines)
    _mcp_debug_exit("build_mcp_tools_prompt", ok=len(tools) > 0, detail=f"{len(tools)} tools, {len(result)} chars")
    return result


def build_native_tools_prompt(lang: str = "chinese") -> str:
    """
    构建注入给 AI 的工具说明。
    文件操作使用 function calling（read_file/edit_file/write_file/validate_edit/preview_edit）
    TXT/ANALYSIS/PLAN/ASK/@@SHELL 仍使用原生标记语言。
    """
    lines = []
    if lang == "chinese":
        lines.append("## 文件操作（Function Calling）")
        lines.append("文件读写编辑使用标准 function calling 工具，通过 tool_calls 调用。")
        lines.append("注意：工具名是 `read_file`/`edit_file`/`write_file`，不要加 `mcp_` 或 `mcp__filesystem__` 前缀。")
        lines.append("")
        lines.append("### 可用工具")
        lines.append("- `get_file_info(path)` — 获取文件信息（大小/行数/修改时间）")
        lines.append("- `read_file(path, range?)` — 读取文件，range='10-30' 指定行范围")
        lines.append("- `edit_file(path, old_string, new_string)` — SEARCH/REPLACE 精确替换")
        lines.append("- `write_file(path, content)` — 创建/覆盖文件")
        lines.append("- `validate_edit(file_path, search, replace)` — 校验 SEARCH 存在且唯一")
        lines.append("- `preview_edit(file_path, search, replace)` — 预览 diff")
        lines.append("")
        lines.append("### 操作原则")
        lines.append("1. **先查后改**：先 `get_file_info` 了解文件概况，再 `read_file` 看具体内容")
        lines.append("2. **优先用 edit_file**：局部修改用 `edit_file`，只有新建文件或超 70% 改动才用 `write_file`")
        lines.append("3. **超过 20KB 的新文件：先骨架后血肉**：先用 `write_file` 创建骨架，再用多次 `edit_file` 逐步填入具体实现")
        lines.append("4. **先校验再编辑**：每次 `edit_file` 前先调 `validate_edit` 校验")
        lines.append("5. **唯一锚点**：`edit_file` 的 old_string 必须逐字节匹配且唯一")
        lines.append("6. **Shell 优先**：ls/cat/grep/find 能用 `@@SHELL` 解决的就不用读文件")
        lines.append("")
        lines.append("### 计划工具")
        lines.append("- `submit_plan(plan, steps?)` — 提交计划给用户确认，steps 是结构化步骤数组")
        lines.append("- `mark_step_complete(step_id)` — 完成一步后标记进度")
        lines.append("")
        lines.append("> TXT/ANALYSIS/ASK/@@SHELL 仍使用原生标记语言")
    else:
        lines.append("## File Operations (Function Calling)")
        lines.append("Use standard function calling tools for file read/write/edit.")
        lines.append("")
        lines.append("### Available Tools")
        lines.append("- `get_file_info(path)` — Get file info (size/lines/mtime)")
        lines.append("- `read_file(path, range?)` — Read file, range='10-30' for line range")
        lines.append("- `edit_file(path, old_string, new_string)` — SEARCH/REPLACE edit")
        lines.append("- `write_file(path, content)` — Create/overwrite file")
        lines.append("- `validate_edit(file_path, search, replace)` — Validate SEARCH exists & unique")
        lines.append("- `preview_edit(file_path, search, replace)` — Preview diff")
        lines.append("")
        lines.append("### Guidelines")
        lines.append("1. **Check first**: `get_file_info` then `read_file` before editing")
        lines.append("2. **Prefer edit_file**: local changes → `edit_file`; new file or >70% change → `write_file`")
        lines.append("3. **Large file chunking**: for new files >20KB, create skeleton with `write_file` then fill with multiple `edit_file` calls")
        lines.append("4. **Validate before edit**: Always call `validate_edit` before `edit_file`")
        lines.append("5. **Unique anchor**: `edit_file` old_string must be byte-exact and unique")
        lines.append("6. **Shell first**: use `@@SHELL` for ls/cat/grep/find when possible")
        lines.append("")
        lines.append("> TXT/ANALYSIS/PLAN/ASK/@@SHELL still use native markup language")

        lines.append("### Guidelines")
        lines.append("- Shell first: use ls/cat/grep/find when possible")
        lines.append("- View first: `[VIEW:]` before editing")
        lines.append("- Unique anchor: SEARCH text must be byte-exact and unique")
        lines.append("- On SEARCH failure, system returns closest line numbers — fix whitespace and retry")
        lines.append("- Color panels auto-show: green=new, red=deleted, blue=reading")
        lines.append("")
        lines.append("> File operations → Native markup (plain text, no JSON)")
        lines.append("> Non-file operations → MCP protocol (fallback)")

    return "\n".join(lines)


# ── 权限级别常量 ──
PERM_READONLY = "ReadOnly"           # 安全只读，自动放行
PERM_WORKSPACE_WRITE = "WorkspaceWrite"  # 修改工作区，需轻确认
PERM_DANGER_FULL = "DangerFullAccess"    # 危险操作，需显式批准


def _make_tool(name: str, description: str, properties: dict, required: list,
               permission: str = PERM_READONLY) -> Dict:
    """构建标准 OpenAI function calling 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
        "x_permission": permission,  # 自定义字段，用于执行时权限检查
    }


def build_native_tools(user_home_dir: str = None) -> List[Dict]:
    """Build OpenAI-compatible tools array — Claw Code inspired full tool set.

    Permission levels: ReadOnly (auto), WorkspaceWrite (light confirm), DangerFullAccess (approval).
    Each tool has exact JSON Schema parameters (type, enum, required, additionalProperties=False).
    """
    _mcp_debug_enter("build_native_tools")

    native = [
        # ═══════════════════════════════════════════
        # ReadOnly — 安全只读，自动放行
        # ═══════════════════════════════════════════

        _make_tool(
            "get_file_info",
            "获取文件基本信息：大小、修改时间、行数、类型。修改文件前先调用此工具了解概况。",
            {"path": {"type": "string", "description": "文件路径"}},
            ["path"],
            PERM_READONLY,
        ),
        _make_tool(
            "read_file",
            "读取文件内容。支持行号范围。改文件前务必先读文件确认当前内容。",
            {
                "path": {"type": "string", "description": "文件路径"},
                "range": {"type": "string", "description": "可选行号范围，如 '10-30' 或 '42'（单行）"},
            },
            ["path"],
            PERM_READONLY,
        ),
        _make_tool(
            "glob_search",
            "使用 glob 模式查找文件。如 'src/**/*.ts' 查找所有 TypeScript 文件。",
            {
                "pattern": {"type": "string", "description": "Glob 模式，如 'src/**/*.py'"},
                "path": {"type": "string", "description": "可选搜索根目录，默认当前工作目录"},
            },
            ["pattern"],
            PERM_READONLY,
        ),
        _make_tool(
            "grep_search",
            "使用正则表达式搜索文件内容。支持上下文行、大小写控制。",
            {
                "pattern": {"type": "string", "description": "搜索的正则表达式"},
                "path": {"type": "string", "description": "可选搜索根目录"},
                "glob": {"type": "string", "description": "可选文件过滤，如 '*.py'"},
                "context": {"type": "integer", "description": "可选上下各行数，默认 0"},
                "-i": {"type": "boolean", "description": "可选忽略大小写，默认 false"},
                "head_limit": {"type": "integer", "description": "可选结果数量上限"},
            },
            ["pattern"],
            PERM_READONLY,
        ),
        _make_tool(
            "ToolSearch",
            "搜索可用工具的名称或关键字。不知道用什么工具时调用此工具查找。",
            {"query": {"type": "string", "description": "搜索关键词，如 'file'、'search'、'web'"}},
            ["query"],
            PERM_READONLY,
        ),
        _make_tool(
            "Skill",
            "加载并执行一个技能剧本。技能是预定义的可复用操作流程。",
            {
                "skill": {"type": "string", "description": "技能名称"},
                "args": {"type": "string", "description": "可选参数"},
            },
            ["skill"],
            PERM_READONLY,
        ),
        _make_tool(
            "Sleep",
            "等待指定秒数。用于监控、等待异步操作等场景。",
            {"seconds": {"type": "integer", "minimum": 1, "description": "等待秒数"}},
            ["seconds"],
            PERM_READONLY,
        ),
        _make_tool(
            "StructuredOutput",
            "以请求的格式返回结构化数据。format='json'时返回 JSON 字符串。",
            {
                "format": {"type": "string", "enum": ["json"], "description": "输出格式"},
                "data": {"type": "string", "description": "要结构化的数据"},
            },
            ["format", "data"],
            PERM_READONLY,
        ),
        _make_tool(
            "TodoWrite",
            "更新当前会话的任务列表。用于多步骤任务中跟踪进度。设置状态为 completed 表示该步骤完成。",
            {
                "todos": {
                    "type": "array",
                    "description": "任务列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "任务描述"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"],
                                       "description": "任务状态"},
                            "activeForm": {"type": "string", "description": "进行中状态的动名词描述，如'正在分析架构'"},
                        },
                        "required": ["content", "status", "activeForm"],
                        "additionalProperties": False,
                    },
                }
            },
            ["todos"],
            PERM_WORKSPACE_WRITE,
        ),

        # ═══════════════════════════════════════════
        # WorkspaceWrite — 修改工作区，需轻确认
        # ═══════════════════════════════════════════

        _make_tool(
            "write_file",
            "创建新文件或全量覆盖现有文件。仅用于新建文件或超过 70% 内容变动。局部修改优先用 edit_file。超过 20KB 的新文件应先用 write_file 创建骨架，再用多次 edit_file 填入实现。",
            {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整的文件内容"},
            },
            ["path", "content"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "edit_file",
            "SEARCH/REPLACE 精确替换。old_string 必须逐字节匹配文件内容且唯一。改前先用 validate_edit 校验。保留代码缩进。",
            {
                "path": {"type": "string", "description": "目标文件路径"},
                "old_string": {"type": "string", "description": "要替换的旧文本（逐字节精确匹配，必须唯一）"},
                "new_string": {"type": "string", "description": "替换后的新文本"},
                "replace_all": {"type": "boolean", "description": "可选：是否替换所有匹配项（默认只替换第一个）"},
            },
            ["path", "old_string", "new_string"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "validate_edit",
            "校验 SEARCH 文本在目标文件中存在且唯一。每次 edit_file 前务必先调用此工具校验。",
            {
                "file_path": {"type": "string", "description": "目标文件路径"},
                "search": {"type": "string", "description": "要搜索的旧文本（逐字节精确匹配）"},
                "replace": {"type": "string", "description": "替换后的新文本"},
            },
            ["file_path", "search", "replace"],
            PERM_READONLY,  # 校验是安全的
        ),
        _make_tool(
            "preview_edit",
            "预览 edit_file 的 unified diff。确认修改正确后再执行编辑。",
            {
                "file_path": {"type": "string", "description": "目标文件路径"},
                "search": {"type": "string", "description": "要搜索的旧文本"},
                "replace": {"type": "string", "description": "替换后的新文本"},
            },
            ["file_path", "search", "replace"],
            PERM_READONLY,  # 预览是安全的
        ),
        _make_tool(
            "submit_plan",
            "提交多步骤执行计划给用户确认。steps 是结构化步骤数组，plan 是纯文本描述（二选一）。确认后才能开始执行。复杂任务必须先用此工具提交计划。",
            {
                "plan": {"type": "string", "description": "Markdown 格式的计划描述"},
                "steps": {
                    "type": "array",
                    "description": "结构化步骤列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "步骤 ID，如 step-1"},
                            "title": {"type": "string", "description": "简短标题"},
                            "action": {"type": "string", "description": "具体操作描述"},
                            "risk": {"type": "string", "enum": ["low", "med", "high"], "description": "风险等级"},
                        },
                        "required": ["id", "title"],
                        "additionalProperties": False,
                    },
                },
            },
            ["plan"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "mark_step_complete",
            "标记一个步骤已完成。提交计划后每完成一步调用此工具更新进度。",
            {"step_id": {"type": "string", "description": "步骤 ID，如 step-1"}},
            ["step_id"],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "EnterPlanMode",
            "进入计划模式。在此模式下禁止执行任何命令和文件修改，只能输出计划。调用 EnterPlanMode 后应紧接着用 submit_plan 提交计划。",
            {},
            [],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "ExitPlanMode",
            "退出计划模式，恢复正常执行模式。计划被确认后调用此工具退出 plan 模式并开始执行。",
            {},
            [],
            PERM_WORKSPACE_WRITE,
        ),
        _make_tool(
            "Config",
            "获取或设置 Onyx 配置。action='get' 返回当前配置，action='set' 设置指定键的值。",
            {
                "action": {"type": "string", "enum": ["get", "set"], "description": "操作类型"},
                "key": {"type": "string", "description": "配置键名"},
                "value": {"type": "string", "description": "配置值（set 时需要）"},
            },
            ["action", "key"],
            PERM_WORKSPACE_WRITE,
        ),

        # ═══════════════════════════════════════════
        # 情感工具（内部使用，不在 TXT 中提及）
        # ═══════════════════════════════════════════

        _make_tool(
            "set_mood",
            "调整情感状态。dimension: happy/angry, delta: -3~+3。此工具静默执行，不要在 TXT 回复中提及。",
            {
                "dimension": {"type": "string", "enum": ["happy", "angry"], "description": "情感维度"},
                "delta": {"type": "number", "description": "调整值（-3.0 ~ +3.0）"},
            },
            ["dimension", "delta"],
            PERM_READONLY,
        ),
        _make_tool(
            "update_people",
            "更新人物档案。此工具静默执行，不要在 TXT 回复中提及。",
            {
                "action": {"type": "string", "enum": ["add", "likeability", "perception"], "description": "操作类型"},
                "name": {"type": "string", "description": "人物名称"},
                "value": {"type": "string", "description": "likeability 时传数字，perception 时传描述"},
            },
            ["action", "name"],
            PERM_READONLY,
        ),

        # ═══════════════════════════════════════════
        # DangerFullAccess — 危险操作，需显式批准
        # ═══════════════════════════════════════════

        _make_tool(
            "Agent",
            "启动一个子代理处理独立任务。子代理在隔离环境中运行，返回结果后继续。用于并行探索或独立子任务。",
            {
                "description": {"type": "string", "description": "子代理任务描述"},
                "prompt": {"type": "string", "description": "子代理的完整指令"},
                "name": {"type": "string", "description": "可选子代理名称"},
            },
            ["description", "prompt"],
            PERM_DANGER_FULL,
        ),
        _make_tool(
            "WebFetch",
            "获取 URL 内容并转换为可读文本。需用户批准。",
            {
                "url": {"type": "string", "description": "要获取的 URL"},
                "prompt": {"type": "string", "description": "关于获取内容的具体问题"},
            },
            ["url", "prompt"],
            PERM_DANGER_FULL,
        ),
        _make_tool(
            "WebSearch",
            "搜索网络获取最新信息并返回引用结果。需用户批准。",
            {
                "query": {"type": "string", "minLength": 2, "description": "搜索关键词"},
                "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "可选限制搜索域名"},
            },
            ["query"],
            PERM_DANGER_FULL,
        ),
    ]

    # ── Include non-filesystem MCP tools (puppeteer/github/postgres etc.) ──
    mcp_tools = get_mcp_tools(user_home_dir=user_home_dir)
    if mcp_tools:
        seen_names = {t["function"]["name"] for t in native if "function" in t}
        for mt in mcp_tools:
            name = mt.get("name", "")
            if not name or name in seen_names:
                continue
            if name in ("read_file", "write_file", "edit_file",
                         "create_directory", "list_directory",
                         "directory_tree", "move_file", "copy_file",
                         "delete_file", "delete_directory",
                         "get_file_info", "search_files", "search_content",
                         "glob", "find_on_path", "get_workspace_folders"):
                continue
            mcp_prefixed = f"mcp_{name}"
            native.append({
                "type": "function",
                "function": {
                    "name": mcp_prefixed,
                    "description": f"[MCP {name}] {mt.get('description', '')}",
                    "parameters": mt.get("inputSchema", {}),
                },
                "x_permission": PERM_DANGER_FULL,  # MCP 工具默认危险
            })
            seen_names.add(mcp_prefixed)

    native.sort(key=lambda t: t.get("function", {}).get("name", ""))
    _mcp_debug_exit("build_native_tools", ok=len(native) > 0,
                    detail=f"{len(native)} native tools")
    return native


# ──────────────────── 内置分析工具执行器 ────────────────────

def _exec_validate_edit(file_path: str, search: str, replace: str) -> str:
    """校验 SEARCH/REPLACE 编辑。"""
    try:
        from lib.edit_engine import validate_edit, dry_run_edit
        ok, msg = validate_edit(file_path, search, replace)
        if ok:
            diff = dry_run_edit(file_path, search, replace)
            return f"✅ Edit valid\n\n{diff[:2000]}"
        return f"❌ {msg}"
    except Exception as e:
        return f"❌ validate_edit failed: {e}"


def _exec_preview_edit(file_path: str, search: str, replace: str) -> str:
    """预览 diff。"""
    try:
        from lib.edit_engine import dry_run_edit
        diff = dry_run_edit(file_path, search, replace)
        if diff.startswith("❌"):
            return diff
        return f"```diff\n{diff}\n```"
    except Exception as e:
        return f"❌ preview_edit failed: {e}"


def _exec_get_file_info(file_path: str) -> str:
    """获取文件基本信息。"""
    try:
        import os, datetime
        if not os.path.exists(file_path):
            return f"❌ File not found: {file_path}"
        stat = os.stat(file_path)
        size = stat.st_size
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        # 行数
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                line_count = sum(1 for _ in f)
        except Exception:
            line_count = -1
        size_str = f"{size:,} bytes"
        if size > 1024:
            size_str += f" ({size/1024:.1f} KB)"
        if size > 1024*1024:
            size_str += f" ({size/1024/1024:.1f} MB)"
        # 文件类型
        _, ext = os.path.splitext(file_path)
        ext = ext.lower() if ext else "(no extension)"
        return (
            f"📄 {file_path}\n"
            f"  大小: {size_str}\n"
            f"  修改时间: {mtime}\n"
            f"  行数: {line_count if line_count >= 0 else 'binary/unknown'}\n"
            f"  类型: {ext}"
        )
    except Exception as e:
        return f"❌ get_file_info failed: {e}"


def _exec_read_file(file_path: str, range_str: str = None) -> str:
    """读取文件内容，支持行号范围。"""
    try:
        if not os.path.exists(file_path):
            return f"❌ File not found: {file_path}"
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if range_str:
            try:
                if "-" in range_str:
                    start, end = map(int, range_str.split("-", 1))
                    lines = content.split("\n")
                    lines = lines[max(0, start-1):end]
                    content = "\n".join(lines)
                else:
                    line_no = int(range_str)
                    lines = content.split("\n")
                    content = lines[min(line_no-1, len(lines)-1)]
            except (ValueError, IndexError):
                pass
        return content
    except Exception as e:
        return f"❌ read_file failed: {e}"


def _exec_write_file(file_path: str, content: str) -> str:
    """写入文件（全量覆盖）。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        # 检查内容是否相同，避免无效写入
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                if f.read() == content:
                    return f"⏭️ 内容未变化，跳过写入 {file_path}"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        total_lines = content.count("\n") + (1 if content else 0)
        return f"✅ 已写入 {file_path}（{total_lines} 行）"
    except Exception as e:
        return f"❌ write_file failed: {e}"


def _exec_edit_file(file_path: str, old_string: str, new_string: str) -> str:
    """SEARCH/REPLACE 精确替换。先读旧内容做 diff 展示，再执行编辑。"""
    try:
        from lib.edit_engine import apply_edit
        # 读旧内容做 diff 预览
        old_content = ""
        try:
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8", errors="replace") as _f:
                    old_content = _f.read()
        except Exception:
            old_content = ""
        if old_content and old_string in old_content:
            new_content = old_content.replace(old_string, new_string, 1)
            console.print(f"  ✅ ✏️ 编辑 {file_path}")
            try:
                _render_edit_diff(old_content, new_content)
            except Exception:
                pass
        ok, msg = apply_edit(file_path, old_string, new_string)
        if ok:
            return f"✅ 编辑成功: {file_path}"
        _err_lower = msg.lower()
        if "not found" in _err_lower or "not unique" in _err_lower:
            return f"❌ {msg}\n提示：使用 validate_edit 先校验 SEARCH 文本，或用 [EDIT:path:N-M] 行号模式"
        return f"❌ {msg}"
    except Exception as e:
        return f"❌ edit_file failed: {e}"


def _exec_set_mood(dimension: str, delta: float) -> str:
    """调整情感状态。"""
    try:
        apply_mood_delta(dimension, delta)
        return f"✅ mood {dimension} {delta:+.1f}"
    except Exception as e:
        return f"❌ set_mood failed: {e}"


def _exec_update_people(action: str, name: str, value: str = "") -> str:
    """更新人物档案。"""
    try:
        apply_people_action(action, name, value)
        return f"✅ people {action} {name}" + (f" {value}" if value else "")
    except Exception as e:
        return f"❌ update_people failed: {e}"


# ──────────────────── 新增工具执行器 ────────────────────

def _exec_glob_search(pattern: str, path: str = None) -> str:
    """使用 glob 模式查找文件。"""
    try:
        import glob as _glob
        search_root = path or "."
        matches = _glob.glob(pattern, root_dir=search_root, recursive=True) if hasattr(_glob, 'root_dir') else []
        if not matches:
            try:
                import subprocess as _sp
                if "**" in pattern:
                    result = _sp.run(["find", search_root, "-type", "f", "-name", pattern.split("/")[-1]],
                                     capture_output=True, text=True, timeout=5)
                    matches = [l.strip() for l in result.stdout.split("\n") if l.strip()]
                else:
                    result = _sp.run(["ls", "-1", os.path.join(search_root, pattern)],
                                     capture_output=True, text=True, timeout=5, shell=True)
                    matches = [l.strip() for l in result.stdout.split("\n") if l.strip()]
            except Exception:
                pass
        if not matches:
            return f"(no matches for '{pattern}' in {search_root})"
        # 限制返回数量
        total = len(matches)
        if total > 200:
            matches = matches[:200]
            return f"\n".join(matches) + f"\n... 以及 {total - 200} 个其他文件（共 {total} 个）"
        return "\n".join(matches)
    except Exception as e:
        return f"❌ glob_search failed: {e}"


def _exec_grep_search(pattern: str, path: str = None, glob: str = None,
                      context: int = 0, i: bool = False, head_limit: int = None) -> str:
    """使用正则表达式搜索文件内容。"""
    try:
        import subprocess as _sp
        cmd = ["grep", "-rn"]
        if i:
            cmd.append("-i")
        if context and context > 0:
            cmd.append(f"-C{context}")
        if glob:
            cmd.extend(["--include", glob])
        search_root = path or "."
        cmd.extend([pattern, search_root])
        result = _sp.run(cmd, capture_output=True, text=True, timeout=15)
        output = result.stdout.strip() or result.stderr.strip() or "(no matches)"
        lines = output.split("\n")
        if head_limit and len(lines) > head_limit:
            output = "\n".join(lines[:head_limit]) + f"\n…[共 {len(lines)} 行，仅显示前 {head_limit} 行]"
        if len(output) > 10000:
            output = output[:5000] + f"\n…[输出过长，截断至 5000 字符，共 {len(output)} 字符]"
        return output
    except subprocess.TimeoutExpired:
        return "❌ grep_search: 搜索超时（15s），请缩小搜索范围"
    except Exception as e:
        return f"❌ grep_search failed: {e}"


def _exec_tool_search(query: str) -> str:
    """搜索可用工具。"""
    try:
        # 获取当前注册的工具列表
        from bin.ai_cmd import build_native_tools
        import inspect
        tools = build_native_tools()
        query_lower = query.lower()
        matches = []
        for t in tools:
            func = t.get("function", {})
            name = func.get("name", "")
            desc = func.get("description", "")
            if query_lower in name.lower() or query_lower in desc.lower():
                perm = t.get("x_permission", "Unknown")
                matches.append(f"- `{name}` [{perm}]\n  {desc[:120]}")
        if not matches:
            # 返回所有工具列表供参考
            all_tools = []
            for t in tools:
                func = t.get("function", {})
                name = func.get("name", "")
                perm = t.get("x_permission", "?")
                all_tools.append(f"- `{name}` [{perm}]")
            return f"未找到与 '{query}' 相关的工具。可用工具列表:\n" + "\n".join(all_tools)
        return f"找到 {len(matches)} 个相关工具:\n\n" + "\n\n".join(matches)
    except Exception as e:
        return f"❌ ToolSearch failed: {e}"


def _find_skill_file(skill_name: str) -> Tuple[Optional[str], str]:
    """在所有标准位置查找 SKILL.md 文件。
    
    查找路径（按优先级）:
      1. .onyx/skills/<name>/SKILL.md        ← Onyx 原生
      2. .onyx/commands/<name>.md
      3. .claw/skills/<name>/SKILL.md         ← Claude Code 兼容
      4. .claw/commands/<name>.md
      5. .claude/skills/<name>/SKILL.md
      6. .claude/commands/<name>.md
      7. ~/.onyx/skills/<name>/SKILL.md
      8. ~/.claw/skills/<name>/SKILL.md
      9. ~/.claude/skills/<name>/SKILL.md
     10. .reasonix/skills/<name>/SKILL.md
     11. <name>.md (当前目录)
     12. skills/<name>.md (当前目录)
    """
    import glob as _glob
    _cwd = os.getcwd()
    _home = os.path.expanduser("~")

    _search_roots = [
        # ═══ Onyx 原生（最高优先级）═══
        os.path.join(_cwd, ".onyx", "skills"),
        os.path.join(_cwd, ".onyx", "commands"),
        # ═══ Claude Code 兼容 ═══
        os.path.join(_cwd, ".claw", "skills"),
        os.path.join(_cwd, ".claw", "commands"),
        os.path.join(_cwd, ".claude", "skills"),
        os.path.join(_cwd, ".claude", "commands"),
        # ═══ 其他 ═══
        os.path.join(_cwd, ".reasonix", "skills"),
        os.path.join(_cwd, "skills"),
        # ═══ 用户 Home ═══
        os.path.join(_home, ".onyx", "skills"),
        os.path.join(_home, ".onyx", "commands"),
        os.path.join(_home, ".claw", "skills"),
        os.path.join(_home, ".claw", "commands"),
        os.path.join(_home, ".claude", "skills"),
        os.path.join(_home, ".claude", "commands"),
        os.path.join(_home, ".reasonix", "skills"),
        os.path.join(_home, ".ai_s", "skills"),
    ]

    _found = []

    for root in _search_roots:
        if not os.path.isdir(root):
            continue
        # 精确匹配: <root>/<name>/SKILL.md
        exact = os.path.join(root, skill_name, "SKILL.md")
        if os.path.isfile(exact):
            _found.append((exact, os.path.getmtime(exact)))

        # 精确匹配: <root>/<name>/<name>.md
        exact2 = os.path.join(root, skill_name, f"{skill_name}.md")
        if os.path.isfile(exact2):
            _found.append((exact2, os.path.getmtime(exact2)))

        # 精确匹配: <root>/<name>.md（commands 风格）
        exact3 = os.path.join(root, f"{skill_name}.md")
        if os.path.isfile(exact3):
            _found.append((exact3, os.path.getmtime(exact3)))

        # 精确匹配: <root>/SKILL.md（直接将 root 当作技能目录）
        direct = os.path.join(root, "SKILL.md")
        if os.path.isfile(direct) and os.path.basename(os.path.dirname(direct)).lower() == skill_name.lower():
            _found.append((direct, os.path.getmtime(direct)))

    # 按修改时间排序（最新的优先）
    _found.sort(key=lambda x: x[1], reverse=True)

    if _found:
        return _found[0][0], ""

    # 尝试在当前目录直接查找 <name>.md
    for ext in [".md", ".txt"]:
        _local = os.path.join(os.getcwd(), f"{skill_name}{ext}")
        if os.path.isfile(_local):
            return _local, ""

    # 如果找不到精确匹配，扫描所有 skill 目录做大小写不敏感匹配
    for root in _search_roots:
        if not os.path.isdir(root):
            continue
        try:
            for entry in os.listdir(root):
                entry_path = os.path.join(root, entry)
                if os.path.isdir(entry_path):
                    # 大小写不敏感比较目录名
                    if entry.lower() == skill_name.lower():
                        for _sf in ["SKILL.md", f"{entry}.md"]:
                            _skill_file = os.path.join(entry_path, _sf)
                            if os.path.isfile(_skill_file):
                                return _skill_file, ""
                    # 检查子目录中的 SKILL.md 的 frontmatter name
                    _sf = os.path.join(entry_path, "SKILL.md")
                    if os.path.isfile(_sf):
                        _fm_name = _parse_skill_name_from_file(_sf)
                        if _fm_name and _fm_name.lower() == skill_name.lower():
                            return _sf, ""
                # 也检查根目录下的 .md 文件
                elif entry.lower() == f"{skill_name.lower()}.md":
                    _full = os.path.join(root, entry)
                    if os.path.isfile(_full):
                        return _full, ""
        except PermissionError:
            continue

    return None, f"未找到技能 '{skill_name}'"


def _parse_skill_name_from_file(filepath: str) -> Optional[str]:
    """从 SKILL.md 的 YAML frontmatter 中提取 name 字段。"""
    try:
        with open(filepath, "r", encoding="utf-8") as _f:
            _content = _f.read()
        if _content.startswith("---"):
            _end = _content.find("---", 3)
            if _end > 0:
                _fm = _content[3:_end].strip()
                for _line in _fm.split("\n"):
                    if _line.strip().startswith("name:"):
                        _val = _line.split(":", 1)[1].strip().strip('"').strip("'")
                        if _val:
                            return _val
    except Exception:
        pass
    return None


def _exec_skill(skill: str, args: str = "") -> str:
    """加载并执行技能（Claw Code 兼容的 Skill.md 发现系统）。"""
    try:
        skill_path, error = _find_skill_file(skill)
        if not skill_path:
            return f"⚠️ {error}\n\n支持的位置: .onyx/skills/<name>/SKILL.md, .claw/skills/<name>/SKILL.md, .claude/skills/<name>/SKILL.md, ~/.onyx/skills/<name>/SKILL.md"

        with open(skill_path, "r", encoding="utf-8") as _f:
            content = _f.read()

        # 解析 frontmatter
        description = ""
        skill_name = skill
        if content.startswith("---"):
            _end = content.find("---", 3)
            if _end > 0:
                _fm = content[3:_end].strip()
                for _line in _fm.split("\n"):
                    _line = _line.strip()
                    if _line.startswith("name:"):
                        skill_name = _line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif _line.startswith("description:"):
                        description = _line.split(":", 1)[1].strip().strip('"').strip("'")
                # 去掉 frontmatter 后的正文
                body = content[_end + 3:].strip()
        else:
            body = content.strip()
            # 尝试从首行提取 description
            _first_line = body.split("\n")[0] if body else ""
            if _first_line.startswith("description:"):
                description = _first_line.split(":", 1)[1].strip()
                body = "\n".join(body.split("\n")[1:]).strip()

        # 如果传了 args，追加到 body
        if args:
            body += f"\n\n## Arguments\n{args}"

        result_parts = [f"✅ 已加载技能: **{skill_name}**"]
        if description:
            result_parts.append(f"📝 {description}")
        result_parts.append(f"📂 {skill_path}")
        result_parts.append("")
        result_parts.append(body)

        return "\n".join(result_parts)

    except Exception as e:
        return f"❌ Skill '{skill}' 加载失败: {e}"


def _exec_sleep(seconds: int) -> str:
    """等待指定秒数。"""
    try:
        import time as _time
        seconds = max(1, min(seconds, 300))  # 限制 1-300 秒
        _time.sleep(seconds)
        return f"✅ 等待 {seconds} 秒完成"
    except Exception as e:
        return f"❌ Sleep failed: {e}"


def _exec_structured_output(format: str, data: str) -> str:
    """返回结构化数据。"""
    try:
        if format == "json":
            import json as _json
            # 尝试解析 data 是否为合法 JSON
            try:
                parsed = _json.loads(data)
                return _json.dumps(parsed, ensure_ascii=False, indent=2)
            except (_json.JSONDecodeError, ValueError):
                # data 不是 JSON，包装成 JSON
                return _json.dumps({"data": data}, ensure_ascii=False, indent=2)
        return data
    except Exception as e:
        return f"❌ StructuredOutput failed: {e}"


def _exec_todo_write(todos: list) -> str:
    """更新任务列表。"""
    try:
        if not todos:
            return "✅ 任务列表已清空"
        lines = []
        pending = sum(1 for t in todos if t.get("status") == "pending")
        in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
        completed = sum(1 for t in todos if t.get("status") == "completed")
        lines.append(f"📋 任务列表（共 {len(todos)} 项：⏳ {pending} 待办 · 🔄 {in_progress} 进行中 · ✅ {completed} 完成）")
        for t in todos:
            status = t.get("status", "pending")
            content = t.get("content", "")
            active = t.get("activeForm", "")
            icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}.get(status, "⏳")
            status_text = {"pending": "待办", "in_progress": active or "进行中", "completed": "完成"}.get(status, "")
            lines.append(f"{icon} {content} _{status_text}_")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ TodoWrite failed: {e}"


def _exec_enter_plan_mode() -> str:
    """进入计划模式。通过修改全局标记实现。"""
    try:
        global _PLAN_MODE_ACTIVE
        _PLAN_MODE_ACTIVE = True
        return "✅ 已进入 Plan 模式。在此模式下禁止执行命令和修改文件。请输出计划并提交用户确认。"
    except Exception as e:
        return f"❌ EnterPlanMode failed: {e}"


def _exec_exit_plan_mode() -> str:
    """退出计划模式。"""
    try:
        global _PLAN_MODE_ACTIVE
        _PLAN_MODE_ACTIVE = False
        return "✅ 已退出 Plan 模式，恢复正常执行模式。"
    except Exception as e:
        return f"❌ ExitPlanMode failed: {e}"


def _exec_config(action: str, key: str, value: str = None) -> str:
    """获取或设置配置。"""
    try:
        config_path = os.path.join(os.path.expanduser("~"), ".config", "onyx", "config.json")
        if action == "get":
            if os.path.exists(config_path):
                import json as _json
                with open(config_path, "r", encoding="utf-8") as f:
                    config = _json.load(f)
                if key in config:
                    val = config[key]
                    return f"`{key}` = {_json.dumps(val, ensure_ascii=False)}"
                return f"`{key}` 未设置"
            return "配置文件不存在"
        elif action == "set":
            if value is None:
                return "❌ set 操作需要提供 value"
            import json as _json
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            config = {}
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    try:
                        config = _json.load(f)
                    except Exception:
                        config = {}
            # 尝试解析 value 为数字或布尔
            try:
                parsed = _json.loads(value)
                config[key] = parsed
            except Exception:
                config[key] = value
            with open(config_path, "w", encoding="utf-8") as f:
                _json.dump(config, f, ensure_ascii=False, indent=2)
            return f"✅ `{key}` 已设置为 {config[key]}"
        return f"❌ 未知操作: {action}"
    except Exception as e:
        return f"❌ Config failed: {e}"


def _exec_agent(description: str, prompt: str, name: str = "") -> str:
    """启动子代理。"""
    try:
        # 尝试通过 Skill/run_skill 工具执行子代理
        agent_name = name or description[:30]
        try:
            import json as _json
            # 尝试调用 run_skill（工具系统注册的顶层工具）
            from .ai_lib import mcp_registry as _mcp_reg
            result = f"[子代理 {agent_name}]\n任务: {prompt[:200]}...\n\n子代理系统已激活，请使用 Skill 工具或 explore/research 子代理工具执行具体任务。"
        except ImportError:
            result = f"[子代理 {agent_name}]\n请使用 explore 或 research 工具来执行此子任务。"
        return result
    except Exception as e:
        return f"❌ Agent 执行失败: {e}"


def _exec_web_fetch(url: str, prompt: str) -> str:
    """获取 URL 内容。"""
    try:
        # 尝试使用 requests 获取
        import requests as _req
        resp = _req.get(url, timeout=15, headers={"User-Agent": "Onyx-AI/1.0"})
        text = resp.text[:5000]
        return f"✅ 已获取 {url} ({len(resp.text)} bytes)\n\n{text[:3000]}"
    except ImportError:
        pass
    except Exception as e:
        return f"❌ WebFetch '{url}' 失败: {e}"

    # 回退：通过 shell curl
    try:
        import subprocess as _sp
        result = _sp.run(["curl", "-sL", "--max-time", "10", url], capture_output=True, text=True, timeout=15)
        if result.stdout:
            text = result.stdout[:5000]
            return f"✅ 已获取 {url}\n\n{text[:3000]}"
        return f"⚠️ curl 返回空: {result.stderr[:200]}"
    except Exception as e:
        return f"❌ WebFetch '{url}' 全部方法失败: {e}"


def _exec_web_search(query: str, allowed_domains: list = None) -> str:
    """搜索网络。"""
    try:
        # 尝试通过 requests + DuckDuckGo 轻搜索
        import requests as _req
        import re as _re
        search_url = f"https://html.duckduckgo.com/html/?q={_req.utils.quote(query)}"
        resp = _req.get(search_url, timeout=15, headers={"User-Agent": "Onyx-AI/1.0"})
        # 简单提取结果
        snippets = _re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', resp.text, _re.DOTALL)
        if snippets:
            results = []
            for s in snippets[:5]:
                clean = _re.sub(r'<[^>]+>', '', s).strip()
                results.append(f"- {clean}")
            return "搜索结果:\n" + "\n".join(results)
        return f"WebSearch 返回 {len(resp.text)} bytes，请使用更精确的查询"
    except ImportError:
        pass
    except Exception as e:
        return f"❌ WebSearch '{query}' 失败: {e}"

    return "⚠️ WebSearch 不可用（需要安装 requests 库）"


# 计划系统已简化为纯引导模式（不再跟踪步骤状态）
_PLAN_MODE_ACTIVE = False  # 全局 plan 模式标记


# 线程局部存储
import threading as _threading_mod
_thread_locals = _threading_mod.local()


def execute_mcp_tool(tool_name: str, params: Dict, name: str = "filesystem",
                     user_mode: str = "low", user_home_dir: str = None,
                     path_validator: Callable = None) -> Tuple[bool, str]:
    """
    执行工具调用。
    优先级：内置 handler → MCP 协议（外部 server）

    工具名规则：
      - 裸名（read_file）→ 先查内置 handler，再查 MCP server
      - mcp_xxx（mcp_puppeteer_navigate）→ 路由到 MCP server，调用 xxx
      - mcp__server__xxx（旧格式）→ 兼容旧版 MCP 调用
    """
    # ── 工具名解析 ──
    # mcp_xxx → MCP 工具，去掉 mcp_ 后路由到 server
    # 裸名 → 先查内置 handler，再查 MCP server（默认走 filesystem）
    raw_tool = tool_name
    mcp_server = name  # 保留调用者指定的 server 名
    # mcp_xxx（单下划线）→ 新版 MCP 前缀，去掉 mcp_ 后路由到 server
    if raw_tool.startswith("mcp_") and not raw_tool.startswith("mcp__"):
        raw_tool = raw_tool[4:]
        mcp_server = None
        try:
            _registry = get_registry()
            for _srv in _registry.server_names():
                if _registry.get(f"mcp__{_srv}__{raw_tool}"):
                    mcp_server = _srv
                    break
        except Exception:
            pass
    # mcp__server__xxx（旧格式）→ 取最后一段工具名
    if raw_tool.startswith("mcp__"):
        raw_tool = raw_tool.rsplit("__", 1)[-1]

    # ── 内置分析工具（不经过 MCP，直接 Python 执行）──
    # 用剥离后的 raw_tool 匹配
    _BUILTIN_HANDLERS = {
        # ── 文件操作 ──
        "validate_edit": lambda p: _exec_validate_edit(p.get("file_path", ""), p.get("search", ""), p.get("replace", "")),
        "preview_edit": lambda p: _exec_preview_edit(p.get("file_path", ""), p.get("search", ""), p.get("replace", "")),
        "get_file_info": lambda p: _exec_get_file_info(p.get("path", "")),
        "read_file":    lambda p: _exec_read_file(p.get("path", ""), p.get("range", None)),
        "write_file":   lambda p: _exec_write_file(p.get("path", ""), p.get("content", "")),
        "edit_file":    lambda p: _exec_edit_file(p.get("path", ""), p.get("old_string", ""), p.get("new_string", "")),
        "glob_search":  lambda p: _exec_glob_search(p.get("pattern", ""), p.get("path", None)),
        "grep_search":  lambda p: _exec_grep_search(p.get("pattern", ""), p.get("path", None), p.get("glob", None),
                                                     p.get("context", 0), p.get("-i", False), p.get("head_limit", None)),
        # ── 搜索与发现 ──
        "ToolSearch":   lambda p: _exec_tool_search(p.get("query", "")),
        "Skill":        lambda p: _exec_skill(p.get("skill", ""), p.get("args", "")),
        # ── 计划与任务 ──
        "submit_plan":   lambda p: json.dumps({"plan": p.get("plan", ""), "steps": p.get("steps", [])}, ensure_ascii=False),
        "mark_step_complete": lambda p: p.get("step_id", ""),
        "TodoWrite":    lambda p: _exec_todo_write(p.get("todos", [])),
        "EnterPlanMode": lambda p: _exec_enter_plan_mode(),
        "ExitPlanMode":  lambda p: _exec_exit_plan_mode(),
        # ── 配置 ──
        "Config":       lambda p: _exec_config(p.get("action", "get"), p.get("key", ""), p.get("value", None)),
        # ── 子代理与输出 ──
        "Agent":        lambda p: _exec_agent(p.get("description", ""), p.get("prompt", ""), p.get("name", "")),
        "StructuredOutput": lambda p: _exec_structured_output(p.get("format", "json"), p.get("data", "")),
        "Sleep":        lambda p: _exec_sleep(int(p.get("seconds", 1))),
        # ── Web ──
        "WebFetch":     lambda p: _exec_web_fetch(p.get("url", ""), p.get("prompt", "")),
        "WebSearch":    lambda p: _exec_web_search(p.get("query", ""), p.get("allowed_domains", None)),
        # ── 情感（内部） ──
        "set_mood":     lambda p: _exec_set_mood(p.get("dimension", ""), float(p.get("delta", 0))),
        "update_people": lambda p: _exec_update_people(p.get("action", ""), p.get("name", ""), p.get("value", "")),
    }
    if raw_tool in _BUILTIN_HANDLERS:
        try:
            result = _BUILTIN_HANDLERS[raw_tool](params or {})
            return True, result
        except Exception as e:
            return False, f"Builtin tool error: {e}"

    # ── write_file 容错：如果参数被 _parse_tool_params 回退成 range_str，尝试从原始 JSON 中抠出 path 和 content ──
    if raw_tool == "write_file" and "content" not in params and "range_str" in params:
        _raw = str(params.get("range_str", ""))
        if _raw.startswith("{"):
            import re as _re
            # 尝试从破损 JSON 中提取 path
            _pm = _re.search(r'"path"\s*:\s*"([^"]*)"', _raw)
            if _pm:
                params["path"] = _pm.group(1)
            # 提取 content：从 "content": " 到文件末尾（JSON 可能被截断，取到最后一个 "） )
            _cm = _re.search(r'"content"\s*:\s*"(.+)', _raw, _re.DOTALL)
            if _cm:
                _raw_content = _cm.group(1)
                # 去掉末尾可能多出的 `"}` 残留
                _raw_content = _raw_content.rstrip('"').rstrip('}').rstrip('"').rstrip('}')
                # 反转义
                _raw_content = _raw_content.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                params["content"] = _raw_content
                params.pop("range_str", None)
                if _pm and not _raw_content.endswith("\n"):
                    params["content"] += "\n"
                _mcp_debug(f"write_file 容错: path={params.get('path', '?')}, content_len={len(params.get('content', ''))}")

    # ── 权限门控：根据 x_permission 级别决定是否需要用户确认 ──
    # 从 build_native_tools() 查找当前工具的权限级别
    _tool_permission = PERM_READONLY  # 默认只读安全
    try:
        _all_tools = build_native_tools()
        for _t in _all_tools:
            if _t.get("function", {}).get("name", "") == raw_tool:
                _tool_permission = _t.get("x_permission", PERM_READONLY)
                break
    except Exception:
        pass

    if _tool_permission == PERM_DANGER_FULL:
        # DangerFullAccess：显式用户批准
        _lang = get_current_lang()
        _prompt = (_lang == "chinese" and "🔴 工具 '{tool}' 需要危险权限，确认执行？(y/N): " or
                   "🔴 Tool '{tool}' requires dangerous access, confirm? (y/N): ").format(tool=raw_tool)
        try:
            _confirm = input(f"  {_prompt}").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return False, "⛔ 用户取消了危险操作"
        if _confirm not in ("y", "yes"):
            return False, "⛔ 用户拒绝了危险操作"
        console.print(f"  [dim]✓ 已授权[/]")

    elif _tool_permission == PERM_WORKSPACE_WRITE and user_mode == "low":
        # WorkspaceWrite + low 模式：轻确认
        _lang = get_current_lang()
        _prompt = (_lang == "chinese" and "✏️ 工具 '{tool}' 将修改工作区，确认？(Y/n): " or
                   "✏️ Tool '{tool}' will modify workspace, confirm? (Y/n): ").format(tool=raw_tool)
        try:
            _confirm = input(f"  {_prompt}").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return False, "⛔ 用户取消了操作"
        if _confirm == "n":
            return False, "⛔ 用户拒绝了修改操作"
        console.print(f"  [dim]✓ 已授权[/]")
    # ReadOnly & WorkspaceWrite+mid/adv → 自动放行

    # ---- 安全限制：写入类工具仅 mid 及以上模式可用（low 禁止） ----
    write_tools = {"edit_file", "write_file", "create_file", "delete_file",
                   "delete_files", "move_file", "rename", "replace_in_file"}
    if raw_tool.lower() in write_tools and user_mode == "low":
        lang = get_current_lang()
        if lang == "chinese":
            return False, (
                f"⛔ 权限不足：'{raw_tool}' 需要 mid 模式才能执行。\n"
                f"请先执行 activite -m mid 提升权限后再重试。"
            )
        return False, (
            f"⛔ Permission denied: '{raw_tool}' requires mid mode.\n"
            f"Run: activite -m mid"
        )

    # ---- 路径安全校验（MCP 工具执行前必须经过 Onyx 沙箱检查） ----
    if path_validator is not None:
        arguments = dict(params) if params else {}
        file_tool_paths = _extract_paths_from_tool(raw_tool, arguments)
        for p in file_tool_paths:
            ok, err_msg = path_validator(raw_tool, p)
            if not ok:
                return False, err_msg

    proc = connect_mcp_server(name, user_home_dir)
    if proc is None:
        return False, f"MCP server '{name}' not connected"

    # 构建 MCP call_tool arguments
    arguments = dict(params) if params else {}

    # edit_file: old_string/new_string → MCP edits[].oldText/.newText
    if raw_tool == "edit_file":
        old_str = arguments.pop("old_string", None) or arguments.pop("old_str", None)
        new_str = arguments.pop("new_string", None) or arguments.pop("new_str", None)
        if old_str is not None:
            arguments["edits"] = [{"oldText": old_str, "newText": new_str or ""}]
        # 移除旧的 range_str/operation（兼容旧格式）
        arguments.pop("range_str", None)
        arguments.pop("operation", None)

    call_params = {
        "name": raw_tool,
        "arguments": arguments,
    }

    result = _mcp_request(proc, "tools/call", call_params, msg_id=int(time.time() * 1000) % 1000000)

    if result is None:
        return False, "MCP tool call timeout"

    if "error" in result:
        return False, f"MCP error: {result['error']}"

    # 提取 content
    content = result.get("result", {}).get("content", [])
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                text_parts.append(item)
        output = "\n".join(text_parts)
    elif isinstance(content, str):
        output = content
    else:
        output = str(content)

    return True, output


def _extract_paths_from_tool(tool_name: str, arguments: Dict) -> List[str]:
    """从 MCP 工具参数中提取所有文件路径，用于安全校验"""
    paths = []
    # 常见的路径参数名
    path_keys = {"path", "paths", "source", "destination", "file_path",
                 "directory", "dir_path", "target", "file", "dir"}

    for key in path_keys:
        val = arguments.get(key)
        if isinstance(val, str) and val:
            paths.append(val)

    # edit_file 特殊处理：edits 中可能含路径引用
    if tool_name == "edit_file":
        edits = arguments.get("edits", [])
        if isinstance(edits, list):
            for edit in edits:
                if isinstance(edit, dict):
                    for k in path_keys:
                        v = edit.get(k)
                        if isinstance(v, str) and v:
                            paths.append(v)

    return paths


def parse_mcp_tool_calls(text: str) -> List[Dict[str, str]]:
    """
    从 AI 响应中解析 [tool:名称]JSON参数[tool:名称:done] 块（Reasonix 风格）。
    - 工具名: mcp__<server>__<tool> 格式
    - 块体为 JSON 参数字符串
    - 兼容旧格式：[tool:名 空格参数]...[tool:名:done]
    """
    calls = []
    # 新格式: [tool:mcp__server__tool]\n{json}\n[tool:mcp__server__tool:done]
    pattern_new = r'\[tool:(mcp__\S+)\]\n(\{.*?\})\n\[tool:\1:done\]'
    for m in re.findall(pattern_new, text, re.DOTALL):
        full_name = m[0]
        json_body = m[1].strip()
        # 解析 mcp__server__tool → server, tool
        server, tool = _parse_mcp_tool_name(full_name)
        calls.append({
            "name": tool,
            "server": server,
            "full_name": full_name,
            "params_str": json_body,
            "body": json_body,
        })
        continue

    # 兼容旧格式: [tool:名 空格参数]...[tool:名:done]
    pattern_old = r'\[tool:(\S+)\s+([^\]]*)\]\n?(.*?)\n?\[tool:\1:done\]'
    for m in re.findall(pattern_old, text, re.DOTALL):
        old_name = m[0]
        # 如果已经被新模式匹配过就跳过
        if any(c.get("full_name") == old_name for c in calls):
            continue
        # 尝试解析为 mcp__server__tool
        server, tool = _parse_mcp_tool_name(old_name)
        # 尝试将 body 解析为 JSON
        body_text = m[2].strip() if len(m) > 2 else ""
        params = m[1].strip()
        if body_text and body_text.startswith("{"):
            params = body_text  # JSON 在块体中
        calls.append({
            "name": tool,
            "server": server,
            "full_name": old_name,
            "params_str": params,
            "body": body_text if body_text else params,
        })

    return calls


def _parse_mcp_tool_name(full_name: str) -> tuple:
    """解析 mcp__server__tool → (server, tool_name)"""
    if full_name.startswith("mcp__"):
        parts = full_name.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
    return "filesystem", full_name


def _parse_tool_params(params_str: str, body: str) -> Dict:
    """
    解析工具参数：JSON 优先，回退到旧空格分隔格式。
    - 新格式: params_str 是 JSON，直接解析
    - 兼容: body 是 JSON（放在块体中）
    - 旧格式: "path 10-30" 空格分隔
    """
    # 尝试 JSON
    for candidate in (params_str, body):
        if candidate and candidate.strip().startswith("{"):
            try:
                return json.loads(candidate.strip())
            except (json.JSONDecodeError, ValueError) as _je:
                _mcp_debug(f"_parse_tool_params JSON decode failed: {_je}")
                pass

    # 回退：旧空格分隔格式 "path [operation] [range]"
    params = {"range_str": params_str, "content": body}
    if params_str and not params_str.startswith("{"):
        parts = params_str.split(None, 1)
        params["path"] = parts[0]
        if len(parts) > 1:
            rest = parts[1]
            if rest in ("replace", "insert", "delete", "append"):
                params["operation"] = rest
            else:
                params["range_str"] = rest
    return params


def list_mcp_servers() -> str:
    """列出已注册的 MCP 服务器及状态"""
    config = _load_mcp_config()
    servers = config.get("servers", {})
    if not servers:
        return "没有已注册的 MCP 服务器"

    lines = ["📋 MCP 服务器列表:", ""]
    for sname, sinfo in servers.items():
        installed = "✅" if sinfo.get("installed") else "❌"
        running = "🟢" if is_mcp_server_running(sname) else "⚫"
        desc = sinfo.get("description", "")
        lines.append(f"  {running} {installed} {sname}: {desc}")
    return "\n".join(lines)


def install_mcp_server_cmd(name: str, package: str = None) -> str:
    """
    安装并注册一个 MCP 服务器
    ai -mcp install <name> [package]
    默认 package = @modelcontextprotocol/server-<name>
    """
    if package is None:
        package = f"@modelcontextprotocol/server-{name}"

    console.print(_mcp_t(f"📦 正在安装 {package}...", f"📦 Installing {package}..."), style="cyan")

    # 构建 env（Termux 上需重定向到内部存储，避免 FUSE symlink 错误）
    env = os.environ.copy()
    try:
        from lib.get_lib_path import _is_termux_environment
        if _is_termux_environment():
            from lib.get_lib_path import TERMUX_PREFIX, TERMUX_HOME
            termux_cache = os.path.join(TERMUX_PREFIX, "tmp", "npm_cache")

            # 彻底删除整个 npm cache（包括 _cacache 和 _npx）
            if os.path.exists(termux_cache):
                try:
                    shutil.rmtree(termux_cache)
                except Exception:
                    pass
            _ensure_dir(termux_cache)

            env["NPM_CONFIG_CACHE"] = termux_cache
            env["npm_config_cache"] = termux_cache
            env["HOME"] = TERMUX_HOME
            console.print(f"📱 Termux: npm cache → {termux_cache}", style="dim")
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["npm", "install", "-g", package],
            capture_output=True, text=True, timeout=120,
            env=env
        )
        if result.returncode != 0:
            return _mcp_t(f"❌ 安装失败: {result.stderr[:300]}", f"❌ Install failed: {result.stderr[:300]}")
    except FileNotFoundError:
        return _mcp_t("❌ npm 未找到，请先安装 Node.js", "❌ npm not found, please install Node.js")
    except subprocess.TimeoutExpired:
        return "❌ 安装超时（120s）"

    # 注册到配置文件
    config = _load_mcp_config()
    config.setdefault("servers", {})[name] = {
        "name": name,
        "description": f"MCP server: {package}",
        "command": "npx",
        "args": ["-y", package, "/"],
        "auto_start": False,
        "installed": True,
    }
    _save_mcp_config(config)

    return f"✅ MCP server '{name}' 安装并注册成功\n   包: {package}\n   使用 ai -mcp list 查看状态"


def remove_mcp_server_cmd(name: str) -> str:
    """从注册表移除 MCP 服务器"""
    if name == "filesystem":
        return "❌ 默认 filesystem MCP server 不可移除"

    # 先关闭进程
    if name in MCP_SERVER_PROCESSES:
        proc = MCP_SERVER_PROCESSES.pop(name)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    MCP_TOOLS_CACHE.pop(name, None)

    config = _load_mcp_config()
    if name in config.get("servers", {}):
        del config["servers"][name]
        _save_mcp_config(config)
        return f"✅ MCP server '{name}' 已移除"
    return f"⚠️ MCP server '{name}' 未在注册表中"


def handle_mcp_command(subcommand: str, args: List[str]) -> None:
    """
    处理 ai -mcp <subcommand> 子命令
    在 handle_ai 入口处调用
    """
    if subcommand == "list":
        result = list_mcp_servers()
        console.print(result, style="white")
    elif subcommand == "install":
        mcp_name = args[0] if args else None
        mcp_pkg = args[1] if len(args) > 1 else None
        if not mcp_name:
            console.print(_mcp_t("用法: ai -mcp install <name> [package]", "Usage: ai -mcp install <name> [package]"), style="bold yellow")
            return
        result = install_mcp_server_cmd(mcp_name, mcp_pkg)
        console.print(result, style="white")
    elif subcommand == "remove":
        mcp_name = args[0] if args else None
        if not mcp_name:
            console.print(_mcp_t("用法: ai -mcp remove <name>", "Usage: ai -mcp remove <name>"), style="bold yellow")
            return
        result = remove_mcp_server_cmd(mcp_name)
        console.print(result, style="white")
    else:
        console.print(
            "用法: ai -mcp <install|list|remove> [args]",
            style="bold yellow"
        )


# ── Shell 命令快速执行器（用于项目上下文采集）──
def _run_shell_cmd(cmd: str, timeout: int = 10) -> str:
    """执行 shell 命令并返回 stdout 文本。静默失败返回空字符串。"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True,
                                text=True, timeout=timeout)
        return result.stdout.strip()
    except Exception:
        return ""


# ========================================================================

# -------------------------- 11. handle_ai 核心函数（SSE模式）-------------------------
def handle_ai(
    cmd_parts: List[str],
    request_id: str,
    onyx_module=None,
    user_home_dir: str = None,
    global_config: Dict[str, Any] = None,
    user_info: Dict[str, Any] = None,
    user_mode=None,
    AI_TOOL_OUTPUT_CACHE: Dict[str, str] = None,
    BUILTIN_COMMANDS: Dict[str, Callable] = None,
    CMD_MAPPING_CACHE: Dict[str, Any] = None,
    current_sys_cmds: Dict[str, List[str]] = None,
    sys_type: str = None,
    get_cached_cmd: Callable = None,
    parse_and_execute: Callable = None,
    get_current_lang_func: Callable = None,
    log_info: Callable = None,
    log_error: Callable = None,
    log_warning: Callable = None,
    security_log: Callable = None,
    _in_repl: bool = False,
) -> None:
    from io import StringIO
    import sys as sys_module
    from contextlib import contextmanager

    # ── --debug 必须在最开头解析，否则 MCP 初始化卡住时没有追踪输出 ──
    # 每次 handle_ai 调用先复位，避免上次 --debug 残留
    global _MCP_DEBUG, _MCP_DEBUG_START
    _MCP_DEBUG = False
    _MCP_DEBUG_START = 0.0
    debug_mode = False
    if "--debug" in cmd_parts:
        debug_mode = True
        _MCP_DEBUG = True
        _MCP_DEBUG_START = time.time()
        cmd_parts.remove("--debug")
        # 用 stderr 输出确保立即可见（stdout 可能被 Live Panel 等捕获）
        sys_module.stderr.write(f"[{time.time()-_MCP_DEBUG_START:06.2f}s] 🔍 DEBUG 模式已启用 — 实时追踪每个函数调用和耗时\n")
        sys_module.stderr.flush()

    if user_home_dir is None:
        user_home_dir = USER_HOME_DIR
    if AI_TOOL_OUTPUT_CACHE is None:
        AI_TOOL_OUTPUT_CACHE = {}
    if global_config is None:
        global_config = {"display_info": {"language": {"current": "chinese"}}}
    if user_info is None:
        user_info = {"name": "default", "session_id": request_id}
    if get_current_lang_func is None:
        get_current_lang_func = get_current_lang
    
    current_lang = get_current_lang_func()
    lang_text = get_lang_text(current_lang)
    
    MAX_CACHE_SIZE = 10000
    MAX_SESSION_FILE_SIZE = 10 * 1024 * 1024
    
    # CMD之间等待时间（秒）
    CMD_WAIT_INTERVAL = 1.5
    
    init_ai_dangerous_commands(user_home_dir, log_info)
    dangerous_commands = load_ai_dangerous_commands(user_home_dir, log_info)
    
    # 提取当前用户模式字符串（用于安全限制）
    _current_user_mode = "low"
    if user_mode is not None:
        if hasattr(user_mode, 'current_mode'):
            _current_user_mode = str(user_mode.current_mode).lower()
        else:
            _current_user_mode = str(user_mode).lower()

    # 检查 MCP 是否启用（manage set mcp false/true）
    _mcp_enabled = True
    _mcp_enabled_path = os.path.join(user_home_dir, ".config", "onyx", "mcp_enabled")
    try:
        if os.path.exists(_mcp_enabled_path) and os.path.isfile(_mcp_enabled_path):
            with open(_mcp_enabled_path, "r") as f:
                _mcp_enabled = f.read().strip().lower() != "false"
    except Exception:
        pass

    # ── 初始化内置工具系统 ──
    # 默认零 MCP，只加载本地内置工具
    # 只有用户主动安装了外部 MCP server（如 puppeteer/github）才会带 mcp_ 前缀
    _mcp_debug("── 初始化内置工具 ──")
    ai_tools_prompt = build_native_tools_prompt(current_lang)
    native_tools = build_native_tools(user_home_dir)

    # 如果用户显式启用了 MCP（安装了非 filesystem 的外部 server），再加载
    if _mcp_enabled:
        _migrate_mcp_config_if_needed(user_home_dir)
        registry = get_registry()
        # 只加载非 filesystem 的 MCP server（puppeteer/github/postgres 等）
        for _srv_name in registry.server_names():
            if _srv_name == "filesystem":
                continue
            _mcp_debug(f"检测到外部 MCP server: {_srv_name}")
        # 后台异步连接已有 MCP server
        _schedule_mcp_health_check(user_home_dir)
        # 注意：不要清空 ai_tools_prompt 和 native_tools
        # build_native_tools() 内部已通过 get_mcp_tools() 自动追加 MCP 工具
    _mcp_debug(f"── MCP 初始化完成, tools_prompt 长度={len(ai_tools_prompt)} ──")
    
    # ANSI 转义序列正则（颜色码、光标控制等）
    _RE_ANSI = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][0-9;]*[^\x07]*\x07|\x1b\(B')

    class RealTimeOutputCatcher:
        def __init__(self, stream_type):
            self.stream_type = stream_type
            self.buffer = []
            self._closed = False
            self._line_count = 0        # 累计行数
            self._ai_triggered = False  # AI 触发时限制显示行数
        
        def write(self, message):
            if self._closed:
                return
            # 剥离 ANSI 颜色码后再存入 buffer（AI 上下文需要干净文本）
            cleaned = _RE_ANSI.sub('', message) if message else ''
            if cleaned:
                self.buffer.append(cleaned)
            # 显示策略：AI 触发 → 前10行实时显示后截断；用户触发 → 全量
            if self.stream_type == "stdout":
                self._line_count += message.count('\n')
                if self._ai_triggered and self._line_count > 10:
                    return  # AI 模式超过10行，停止实时显示
                sys_module.__stdout__.write(message)
                sys_module.__stdout__.flush()
            else:
                sys_module.__stderr__.write(message)
                sys_module.__stderr__.flush()
        
        def flush(self):
            if self._closed:
                return
            if self.stream_type == "stdout":
                sys_module.__stdout__.flush()
            else:
                sys_module.__stderr__.flush()
        
        def isatty(self):
            return False
        
        def close(self):
            self._closed = True
        
        def get_output(self):
            return "".join(self.buffer)
    
    @contextmanager
    def capture_command_output():
        original_stdout = sys_module.stdout
        original_stderr = sys_module.stderr
        stdout_catcher = RealTimeOutputCatcher("stdout")
        stderr_catcher = RealTimeOutputCatcher("stderr")
        
        try:
            sys_module.stdout = stdout_catcher
            sys_module.stderr = stderr_catcher
            yield stdout_catcher, stderr_catcher
        except Exception as e:
            if log_error:
                log_error(f"Command execution capture exception: {str(e)}", request_id)
            raise
        finally:
            sys_module.stdout = original_stdout
            sys_module.stderr = original_stderr
            stdout_catcher.close()
            stderr_catcher.close()
    
    def cleanup_output_cache():
        if len(AI_TOOL_OUTPUT_CACHE) > MAX_CACHE_SIZE:
            items = list(AI_TOOL_OUTPUT_CACHE.items())
            for k, _ in items[:len(items)//5]:
                AI_TOOL_OUTPUT_CACHE.pop(k, None)
    
    def check_session_file_size(file_path: str) -> bool:
        if not os.path.exists(file_path):
            return True
        try:
            if os.path.getsize(file_path) > MAX_SESSION_FILE_SIZE:
                backup_path = f"{file_path}.{int(time.time())}.bak"
                os.rename(file_path, backup_path)
                if log_info:
                    log_info(f"Session file exceeded size limit, rotated: {os.path.basename(backup_path)}", request_id)
                return False
        except Exception as e:
            if log_error:
                log_error(f"Failed to check session file size: {str(e)}", request_id)
        return True

    parse_result = parse_arguments(cmd_parts, lang_text, onyx_module)
    if len(parse_result) == 9:
        content_type, content, extra_info, auto_exec, new_key, chat_action, chat_param, mode, times = parse_result
    elif len(parse_result) == 7:
        content_type, content, extra_info, auto_exec, new_key, chat_action, chat_param = parse_result
        mode = "normal"
        times = 1
    else:
        content_type, content, extra_info, auto_exec, new_key = parse_result
        chat_action, chat_param = None, None
        mode = "normal"
        times = 1
        
    if content_type == "mcp_command":
        # ai -mcp <install|list|remove> [args]
        handle_mcp_command(content, extra_info if isinstance(extra_info, list) else [])
        return

    if content_type == "model_command":
        # ai -model [name] — view or switch model
        import json as _json
        conf = load_key_conf()
        if not conf:
            console.print("[yellow]No API key configured. Run 'ai -key <key>' first.[/]")
            return
        platform = conf.get("platform", "deepseek")
        current_model = conf.get("model", "")
        is_custom = (platform == "custom")
        plat_name = "Custom" if is_custom else _SUPPORTED_PLATFORMS.get(platform, {}).get("name", platform)
        if not content:
            # List current model + effort
            effort = conf.get("params", {}).get("reasoning_effort", "") or _SUPPORTED_PLATFORMS.get(platform, {}).get("reasoning_effort", "")
            console.print(f"[dim]Platform: {plat_name}  Model: {current_model or '?'}  Effort: {effort or '—'}[/]")
            if not is_custom:
                models = _SUPPORTED_PLATFORMS.get(platform, {}).get("models", [])
                console.print("Available models:")
                for m in models:
                    marker = "  ←" if m == current_model else ""
                    console.print(f"  {m}{marker}")
                console.print("\nUsage: ai -model <name>\n       ai -effort high|max")
            return
        # Switch model
        new_model = content.strip()
        conf["model"] = new_model
        # 混淆 api_key 后写入
        key_conf_path = os.path.join(user_home_dir, ".config", "onyx", "ai", "key.conf")
        os.makedirs(os.path.dirname(key_conf_path), exist_ok=True)
        _write_conf = dict(conf)
        if "api_key" in _write_conf and isinstance(_write_conf["api_key"], str):
            _write_conf["api_key"] = _obfuscate(_write_conf["api_key"])
        with open(key_conf_path, "w", encoding="utf-8") as f:
            _json.dump(_write_conf, f, ensure_ascii=False, indent=2)
        os.chmod(key_conf_path, 0o600)
        console.print(f"[green]✅ Switched to model: {new_model}[/]")
        return

    if content_type == "effort_command":
        # ai -effort [high|max] — view or set reasoning effort
        import json as _json
        conf = load_key_conf()
        if not conf:
            console.print("[yellow]No API key configured.[/]")
            return
        if not content:
            current_effort = conf.get("params", {}).get("reasoning_effort", "") or _SUPPORTED_PLATFORMS.get(conf.get("platform", ""), {}).get("reasoning_effort", "high")
            console.print(f"[dim]Current reasoning effort: {current_effort}[/]")
            console.print("Available: high, max")
            console.print("Usage: ai -effort high  |  ai -effort max")
            return
        effort_val = content.strip().lower()
        if effort_val not in ("high", "max"):
            console.print("[yellow]Invalid effort. Use: high or max[/]")
            return
        params = conf.get("params", {})
        if not isinstance(params, dict):
            params = {}
        params["reasoning_effort"] = effort_val
        conf["params"] = params
        # 混淆 api_key 后写入
        key_conf_path = os.path.join(user_home_dir, ".config", "onyx", "ai", "key.conf")
        os.makedirs(os.path.dirname(key_conf_path), exist_ok=True)
        _write_conf = dict(conf)
        if "api_key" in _write_conf and isinstance(_write_conf["api_key"], str):
            _write_conf["api_key"] = _obfuscate(_write_conf["api_key"])
        with open(key_conf_path, "w", encoding="utf-8") as f:
            _json.dump(_write_conf, f, ensure_ascii=False, indent=2)
        os.chmod(key_conf_path, 0o600)
        console.print(f"[green]✅ Reasoning effort set to: {effort_val}[/]")
        return

    if content_type == "deep_aff_mode":
        # ai -mode deep-aff <true|false> — 深情模式
        enable = content.lower() in ("true", "1", "yes")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if enable:
            try:
                # 加载插件
                from bin.plugin_loader import load_plugin, verify
                ok, reason, payload = verify("deep_aff")
                if not ok:
                    console.print(f"❌ 深情模式插件验证失败: {reason}", style="bold red")
                    return
                lib = load_plugin("deep_aff")
                if not lib:
                    console.print("❌ 无法加载深情模式插件", style="bold red")
                    return
                # 调用 C 模块初始化
                validation_key = payload.get("binary_hash", "deep_aff_key")[:32]
                lib.deep_aff_init.argtypes = [ctypes.c_char_p]
                lib.deep_aff_init.restype = ctypes.c_int
                ret = lib.deep_aff_init(validation_key.encode())
                if ret != 0:
                    console.print("❌ 深情模式授权失败", style="bold red")
                    return
                # 获取提示词
                lib.deep_aff_get_prompt.argtypes = []
                lib.deep_aff_get_prompt.restype = ctypes.c_char_p
                lib.deep_aff_free.argtypes = [ctypes.c_char_p]
                prompt_ptr = lib.deep_aff_get_prompt()
                if not prompt_ptr:
                    console.print("❌ 无法获取深情模式提示词", style="bold red")
                    return
                prompt_text = ctypes.c_char_p(prompt_ptr).value.decode("utf-8")
                lib.deep_aff_free(prompt_ptr)
                # 保存提示词到文件（后续 AI 调用时会读取）
                deep_aff_path = os.path.join(user_home_dir, ".ai_s", "deep_aff_prompt.txt")
                os.makedirs(os.path.dirname(deep_aff_path), exist_ok=True)
                with open(deep_aff_path, "w", encoding="utf-8") as f:
                    f.write(prompt_text)
                console.print("💕 深情模式已激活", style="bold magenta")
                console.print(f"   提示词已保存: {len(prompt_text)} 字", style="dim")
            except Exception as e:
                console.print(f"❌ 深情模式启动失败: {e}", style="bold red")
                import traceback
                traceback.print_exc()
        else:
            # 关闭深情模式
            deep_aff_path = os.path.join(user_home_dir, ".ai_s", "deep_aff_prompt.txt")
            if os.path.exists(deep_aff_path):
                os.remove(deep_aff_path)
            console.print("💕 深情模式已关闭", style="dim")
        return

    if content_type == "machine_id_command":
        # ai -mid / ai -machine-id — show current device fingerprint
        try:
            from bin.plugin_loader import get_machine_id
            mid = get_machine_id()
            console.print(f"Machine ID: [bold]{mid}[/]")
        except Exception as e:
            console.print(f"[red]Failed to get machine ID: {e}[/]")
        return

    if content_type == "plugin_command":
        # ai -plugin <list|load|sign|verify|compile> [args]
        sub = content  # "list", "load", "sign", "verify", "compile"
        args = extra_info if isinstance(extra_info, list) else []
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if sub == "list":
            import subprocess as _sp
            _sp.run([sys.executable, os.path.join(root, "plugin_loader.py"), "list"])
        elif sub == "load" and args:
            import subprocess as _sp
            _sp.run([sys.executable, os.path.join(root, "plugin_loader.py"), "load", args[0]])
        elif sub == "verify" and args:
            import subprocess as _sp
            _sp.run([sys.executable, os.path.join(root, "plugin_loader.py"), "verify", args[0]])
        elif sub == "sign" and args:
            import subprocess as _sp
            cmd = [sys.executable, os.path.join(root, "plugin_loader.py"), "sign"] + args
            _sp.run(cmd)
        elif sub == "compile" and args:
            import subprocess as _sp
            _sp.run([sys.executable, os.path.join(root, "plugin_compile.py"), args[0]])
        else:
            console.print("Usage: ai -plugin list | load <name> | verify <name> | sign <name> [ver] | compile <file.c>")
        return

    if content_type == "chat_only":
        if chat_action == "list":
            memories = list_chat_memories(user_home_dir)
            console.print(lang_text["chat_list_header"], style="bold cyan")
            current = get_current_chat_name(user_home_dir)
            for mem in memories:
                if mem == current:
                    current_label = " (current)" if current_lang == "english" else " (当前)"
                    console.print(f"  * {mem}{current_label}", style="bold green")
                else:
                    console.print(f"    {mem}", style="white")
            return
        elif chat_action == "switch":
            if not chat_param:
                console.print(lang_text["chat_switch_usage"], style="bold red")
                return
            if switch_chat_memory(user_home_dir, chat_param):
                console.print(lang_text["chat_switched"].format(chat_param), style="bold green")
            else:
                console.print(lang_text["chat_not_found"].format(chat_param), style="bold red")
            return
        elif chat_action == "new":
            name = chat_param if chat_param else datetime.now().strftime('%Y%m%d_%H%M%S')
            if create_chat_memory(user_home_dir, name):
                switch_chat_memory(user_home_dir, name)
                console.print(lang_text["chat_created"].format(name), style="bold green")
            else:
                console.print(lang_text["chat_already_exists"].format(name), style="bold yellow")
            return
        else:
            console.print(f"Unknown -c action: {chat_action}", style="bold red")
            return
    
    if content_type == "key_only":
        result = call_ai_api_sse(question="", new_key=new_key, debug_mode=debug_mode, onyx_module=onyx_module, user_home_dir=user_home_dir)
        if "error" in result:
            console.print(f"❌ {result['error']}", style="bold red")
        elif "key_set" in result and result["key_set"]:
            console.print(lang_text["key_set_success"], style="bold green")
            return
        return
    
    if content_type == "error":
        console.print(f"❌ {content}", style="bold red")
        if log_error:
            log_error(f"AI parameter error: {content}", request_id)
        return

    # ── TUI 模式已移除（-tui 参数不再支持）──

    # Ctrl+C 打断思考：直接抛出 KeyboardInterrupt 向上传播
    import signal as _signal

    def _on_interrupt(signum, frame):
        raise KeyboardInterrupt("User interrupted")

    _original_sigint = _signal.signal(_signal.SIGINT, _on_interrupt)

    # 重置中断标志（避免上次 Ctrl+C 残留导致本次立即中断）
    global _AI_INTERRUPTED
    _AI_INTERRUPTED = False

    current_session_id = request_id
    initial_question = content
    last_user_question = content  # 追踪最近一次用户输入，ESC 追问时更新
    continue_asking = True
    interaction_count = 0
    _pending_plan = ""  # 来自 submit_plan 工具调用的计划文本（跨循环持久化）
    plan_confirmed = False  # Plan 模式：计划是否已获用户确认
    referenced_memory_uuid = None
    current_chat_name = get_current_chat_name(user_home_dir)
    message_appended = False
    
    cleanup_output_cache()

    def _ensure_library_record():
        """确保 library 文件存在（plan 流程等提前 continue 可能跳过常规记录）"""
        nonlocal current_session_id
        record_path = os.path.join(
            get_ai_session_library_dir(user_home_dir), f"{current_session_id}.txt"
        )
        if not os.path.exists(record_path):
            with open(record_path, "w", encoding="utf-8") as f:
                f.write(f"Session ID: {current_session_id}\n"
                        f"Record time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"{'=' * 60}\n")
    
    # ── last_prompt_tokens 清零（每场对话独立） ──
    _thread_locals.last_prompt_tokens = 0

    current_times = 1
    executed_tools = []          # 已执行工具键（跨轮持久）
    _tool_results_cache = {}     # exec_key → output（供去重时回传缓存结果）
    # ── 风暴检测 / 重复成功防护 ──
    _MAX_TOOL_OUTPUT = 32 * 1024   # 单次工具结果最大字节数（~8K tokens），超长截断防上下文撑爆
    _storm_counter = {}          # error_signature → count: 连续相同错误次数，>=3 时触发换策略
    _repeat_success = {}         # exec_key → count: 同一操作连续成功次数，>=2 时拦截

    # ── 标准对话历史（messages 结构）──
    conversation_history: List[Dict] = []
    import platform as _pf
    _env_info = (
        f"系统: {_pf.system()} - {_pf.release()}\n"
        f"用户: {os.environ.get('USER', '?')}\n"
        f"工作目录: {os.getcwd()}\n"
        f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"#AI工具说明\n{ai_tools_prompt}\n"
        f"{mood_context()}\n"
    )
    # 读取 onyx_ai.md 最高指示
    _onyx_prompt_path = os.path.join(user_home_dir, ".ai_s", "onyx_ai.md")
    _onyx_ai_prompt = ""
    if os.path.exists(_onyx_prompt_path):
        try:
            with open(_onyx_prompt_path, "r", encoding="utf-8") as _f:
                _onyx_ai_prompt = _f.read().strip()
        except Exception:
            pass
    if _onyx_ai_prompt:
        _env_info += f"\n#最高指示（持久记忆）\n{_onyx_ai_prompt}\n"

    # ── 项目上下文自动注入（git 状态 + 指令文件）──
    _project_context = ""
    try:
        _git_root = os.getcwd()
        # git status（简短）
        _git_status = _run_shell_cmd("git status --short 2>/dev/null | head -30")
        if _git_status:
            _project_context += f"#Git 状态\n{_git_status}\n"
            # git 当前分支
            _git_branch = _run_shell_cmd("git rev-parse --abbrev-ref HEAD 2>/dev/null")
            if _git_branch:
                _project_context = f"分支: {_git_branch}\n" + _project_context
            # git diff（前 30KB）
            _git_diff = _run_shell_cmd("git diff --no-color 2>/dev/null | head -500")
            if _git_diff and len(_git_diff) > 100:
                _diff_str = _git_diff[:30000]
                if len(_git_diff) > 30000:
                    _diff_str += f"\n…[diff 过长，截断至 30000 字符，共 {len(_git_diff)} 字符]"
                _project_context += f"#Git 变更\n{_diff_str}\n"
            # 最近 5 条 commit
            _git_log = _run_shell_cmd("git log --oneline -5 2>/dev/null")
            if _git_log:
                _project_context += f"#最近提交\n{_git_log}\n"
        # 指令文件自动发现（CLAUDE.md / CLAW.md / AGENTS.md / .claw/rules/*）
        _instruction_files = []
        for _root in [_git_root] if _git_status else [os.getcwd()]:
            for _fname in ["CLAUDE.md", "CLAW.md", "AGENTS.md", "CLAUDE.local.md"]:
                _fpath = os.path.join(_root, _fname)
                if os.path.exists(_fpath):
                    _instruction_files.append(_fpath)
            # .claw/ 目录
            _claw_dir = os.path.join(_root, ".claw")
            if os.path.isdir(_claw_dir):
                for _fname in ["CLAUDE.md", "instructions.md"]:
                    _fpath = os.path.join(_claw_dir, _fname)
                    if os.path.exists(_fpath):
                        _instruction_files.append(_fpath)
                _rules_dir = os.path.join(_claw_dir, "rules")
                if os.path.isdir(_rules_dir):
                    for _rf in sorted(os.listdir(_rules_dir)):
                        if _rf.endswith((".md", ".txt", ".mdc")):
                            _instruction_files.append(os.path.join(_rules_dir, _rf))
        # 读取指令文件内容（限制每个 4KB，总 12KB）
        _total_inst_chars = 0
        _inst_lines = []
        for _fpath in _instruction_files:
            if _total_inst_chars > 12000:
                break
            try:
                with open(_fpath, "r", encoding="utf-8") as _f:
                    _content = _f.read()[:4000]
                _rel = os.path.relpath(_fpath, _git_root) if _git_root else _fpath
                _inst_lines.append(f"### {_rel}\n{_content}")
                _total_inst_chars += len(_content)
            except Exception:
                pass
        if _inst_lines:
            _project_context += "#项目指令\n" + "\n\n".join(_inst_lines) + "\n"
    except Exception:
        pass

    if _project_context:
        _env_info = _project_context + "\n" + _env_info

    # ── 加载核心系统提示词 agreement.md ──
    try:
        _agreement_paths = [
            os.path.join(ROOT_DIR, "onyx", "etc", "ai", "agreement.md"),
            os.path.join("etc", "ai", "agreement.md"),
        ]
        for _ap in _agreement_paths:
            if os.path.exists(_ap):
                with open(_ap, "r", encoding="utf-8") as _af:
                    _env_info = _af.read() + "\n\n" + _env_info
                break
    except Exception:
        pass

    _system_msg = {"role": "system", "content": _env_info}
    conversation_history.append(_system_msg)
    conversation_history.append({"role": "user", "content": initial_question})

    current_question = initial_question  # 用于日志/估算，API 实际走 conversation_history

    while continue_asking:
        _tool_calls_processed_this_round = False
        _commands_processed_this_round = False
        if _AI_INTERRUPTED:
            console.print(_mcp_t("\n⏹ 已中断", "\n⏹ Interrupted"), style="yellow")
            break
        interaction_count += 1
        user_answer = ""
        user_refuse_reasons = []
        
        # ── 确保 library 磁盘记录存在（工具结果依赖它持久化）──
        _ensure_library_record()
        
        existing_memory, memory_file = get_latest_ai_session(user_home_dir, current_session_id)
        if memory_file:
            check_session_file_size(memory_file)
        
        memory_section = build_memory_context(
            user_home_dir, current_chat_name, current_session_id,
            referenced_memory_uuid, (interaction_count == 1 and not message_appended), mode
        )

        # AI 引用记忆时显示提示（API 调用前，让用户提前看到）
        if referenced_memory_uuid:
            console.print(
                lang_text["memory_referenced"].format(referenced_memory_uuid[:24] + "..."),
                style="dim cyan"
            )
        
        no_memory_text = lang_text.get("no_memory", "No historical memory" if current_lang == "english" else "无历史记忆")
        # 记忆上下文注入：每次循环都从磁盘加载 library 记忆
        # 系统提示词（agreement/tools/mood）保持在最前面，记忆紧随其后
        if memory_section != no_memory_text:
            _memory_content = f"#聊天记忆\n{memory_section}"
            # 查找是否已有记忆 system 消息，有则更新
            _mem_idx = next((i for i, m in enumerate(conversation_history)
                            if m.get("role") == "system" and m.get("content", "").startswith("#聊天记忆")), None)
            if _mem_idx is not None:
                conversation_history[_mem_idx]["content"] = _memory_content
            else:
                # 插入到第一个 system 消息（核心提示词）之后，保持其最前位置
                _first_sys = next((i for i, m in enumerate(conversation_history)
                                  if m.get("role") == "system" and not m.get("content", "").startswith("#聊天记忆")), None)
                if _first_sys is not None:
                    conversation_history.insert(_first_sys + 1, {"role": "system", "content": _memory_content})
                else:
                    conversation_history.insert(0, {"role": "system", "content": _memory_content})
            
            # 后续循环：只移除已在 library 里的用户提问，不删 system 消息
            if interaction_count > 1:
                _first_asst = next((i for i, m in enumerate(conversation_history)
                                   if m.get("role") == "assistant"), None)
                if _first_asst is not None:
                    # 保留：所有 system 消息 + 第一个 assistant 及其之后的所有消息
                    conversation_history = [m for i, m in enumerate(conversation_history)
                                           if m.get("role") == "system" or i >= _first_asst]
                    # 清理后如果无 user 消息，插入系统信号（告诉 AI 命令已执行、结果在 library 里）
                    _has_user = any(m.get("role") == "user" for m in conversation_history)
                    if not _has_user:
                        if current_lang == "chinese":
                            _continue_prompt = (
                                "上一轮的命令已完成执行，执行结果已记录在上方 #聊天记忆 的 "
                                "当前会话记忆(library) 部分。请根据执行结果判断任务是否完成："
                                "若已完成则用 [ANSWER]yes 正常结束，若需继续则继续生成下一步命令。"
                            )
                        else:
                            _continue_prompt = (
                                "The previous round of commands has finished executing. "
                                "Results are recorded in the #聊天记忆 Current Session Memory (library) section above. "
                                "Please check the results and determine if the task is complete: "
                                "if done, set [ANSWER]yes; if more work is needed, continue with next commands."
                            )
                        conversation_history.append({"role": "user", "content": _continue_prompt})

        # Plan 模式前缀：告知 AI 当前处于 plan 模式，禁止执行命令和文件修改
        # mode=="plan"（用户 ai plan 命令）或 _PLAN_MODE_ACTIVE（AI 调用 EnterPlanMode）
        if mode == "plan" or _PLAN_MODE_ACTIVE:
            plan_warning = lang_text.get("plan_mode_warning",
                "⚠️ 当前处于 PLAN 模式。你只能生成计划，不能执行任何命令或修改文件。"
                "请使用 [plan]...[plan:done] 格式输出你的计划。"
                "等用户确认后，才能进入执行阶段。"
                "如果要退出 plan 模式，请调用 ExitPlanMode 工具。")
            conversation_history.append({"role": "system", "content": plan_warning})
        
        # 流式展示：Rich Live Panel — 实时更新 AI 回答
        from rich.live import Live
        from rich.panel import Panel
        from rich.box import ROUNDED
        
        # ── 多块流式状态机：每个字段类型独立缓冲区 + 独立 Panel ──
        stream_buffer = ""        # 累积原始流式文本
        txt_content = ""          # [TXT]...[TXT:DONE] 或 [TXT]:... 主回复内容
        analysis_content = ""     # [ANALYSIS]:... 或 [ANALYSIS]...[ANSWER] 分析内容
        plan_content = ""         # [plan]...[plan:done] 计划内容
        ask_content = ""          # [ASK]:... 追问内容
        answer_state = ""         # [ANSWER]:yes/no
        memory_uuid = ""          # [MEMORY]:uuid
        tag_val = ""              # [TAG]:value
        prompt_val = ""           # [PROMPT]:value — 写入 .ai_s/onyx_ai.md
        live_ref = [None]         # Live 对象引用
        loading_flag = [True]
        tool_results_display = []  # 工具执行结果（用于面板展示：名前10行灰色虚影）
        _txt_phase = "pre"        # "pre" | "in_txt" | "post_txt"

        _SAFE_MARGIN = 20  # 安全缓冲（覆盖最长标记 [TXT:DONE]=10, [plan:done]=11）

        def _strip_markers(text: str) -> str:
            """去除所有格式标记，只保留纯文本（行首标记 + @@SHELL 块）"""
            import re as _re
            # 多行块闭合标记（可能单独成行残留）
            text = _re.sub(r'\[TXT:DONE\]', '', text)
            text = _re.sub(r'\[ANALYSIS:DONE\]', '', text)
            text = _re.sub(r'\[PLAN:DONE\]', '', text)
            text = _re.sub(r'\[PROMPT:DONE\]', '', text)
            text = _re.sub(r'\[TAG:DONE\]', '', text)
            # 行首单行标记（只移除标记本身，保留标记后的内容）
            text = _re.sub(r'^\[TXT\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[ANALYSIS\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[ANSWER\]:?\w*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[ASK\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[TAG\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[CLASS[^\]]*\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[SLEEP\]:?\d*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[MEMORY\]:?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[plan(?:\:done)?\]\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[tool:\S+\]?\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^\[tool:\S+:done\]\s*', '', text, flags=_re.MULTILINE)
            # @@SHELL / @@CMD 命令块 — 独立成行 + 同行粘连都过滤
            text = _re.sub(r'^@@SHELL\s*$.*?(?=^@@|\Z)', '', text,
                           flags=_re.MULTILINE | _re.DOTALL)
            text = _re.sub(r'^.*@@SHELL.*$\n?', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^@@CMD\s*$.*?(?=^@@|\Z)', '', text,
                           flags=_re.MULTILINE | _re.DOTALL)
            text = _re.sub(r'^.*@@CMD.*$\n?', '', text, flags=_re.MULTILINE)
            # >>>>>>>>>> 分隔符 — 独立成行 + 同行粘连都过滤
            text = _re.sub(r'^>{8,}\s*$', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'^.*>{8,}.*$\n?', '', text, flags=_re.MULTILINE)
            return text.strip()

        def _write_onyx_ai_prompt(content: str, home_dir: str = None) -> None:
            """将 AI 的 [PROMPT]: 内容追加写入 ~/.ai_s/onyx_ai.md（纯粹追加）"""
            if not content.strip():
                return
            prompt_dir = home_dir if home_dir else os.path.expanduser("~")
            prompt_file = os.path.join(prompt_dir, ".ai_s", "onyx_ai.md")
            try:
                os.makedirs(os.path.dirname(prompt_file), exist_ok=True)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                entry = f"\n\n> [{timestamp}]\n\n{content.strip()}\n"
                with open(prompt_file, "a", encoding="utf-8") as f:
                    f.write(entry)
                _mcp_debug(f"[PROMPT] 已追加到 {prompt_file}: {content[:80]}...")
                # 控制台可见确认（不用 debug 模式也能看到）
                try:
                    console.print(f"📝 最高指示已更新: {content[:60]}{'...' if len(content) > 60 else ''}", style="dim cyan")
                except Exception:
                    pass
            except Exception as e:
                _mcp_debug(f"[PROMPT] 写入失败: {e}")
                try:
                    console.print(f"⚠️ 最高指示写入失败: {e}", style="bold red")
                except Exception:
                    pass

        def _render_all_panels():
            """将所有已接收的内容块组合为复合 Panel"""
            from rich.console import Group
            from rich.markdown import Markdown
            from rich.text import Text

            parts = []

            # 流式文本：只在 TXT 块闭合后显示绿色面板，流式中显示灰色预览
            if txt_content.strip():
                cleaned = _strip_markers(txt_content)
                if cleaned.strip():
                    if _txt_phase == "post_txt":
                        # TXT 块已闭合 → 绿色正式面板
                        parts.append(Panel(Markdown(cleaned.strip()),
                                           title="💬 回复", border_style="green", box=ROUNDED))
                    else:
                        # 仍在流式接收 → 灰色预览（最后100字符）——避免刷屏
                        tail = cleaned.strip()[-100:] if len(cleaned.strip()) > 100 else cleaned.strip()
                        if tail:
                            parts.append(Text(tail, style="dim"))

            # 分析 Panel
            if analysis_content.strip():
                parts.append(Panel(Markdown(analysis_content.strip()),
                                   title="📊 分析", border_style="blue", box=ROUNDED))

            # 计划 Panel
            if plan_content.strip():
                parts.append(Panel(Markdown(plan_content.strip()),
                                   title="📋 计划", border_style="cyan", box=ROUNDED))

            # 追问 Panel
            if ask_content.strip():
                parts.append(Panel(ask_content.strip(),
                                   title="🤔 追问", border_style="yellow", box=ROUNDED))

            # MCP 工具执行结果（前4行）
            if tool_results_display:
                for tr in tool_results_display:
                    icon = "✅" if tr["ok"] else "❌"
                    style = "dim green" if tr["ok"] else "dim red"
                    header = f"{icon} {tr['name']}"
                    body = tr.get("preview", tr.get("output", "")[:100])
                    _total = len(tr.get("output", ""))
                    if _total > 100:
                        body += f"\n…(共 {_total} 字符，完整输出已保留)"
                    parts.append(Panel(body, title=header, border_style=style, box=ROUNDED,
                                       padding=(0, 1)))

            if not parts:
                return Panel(Spinner("dots", text=_mcp_t(" 思考中...", " Thinking..."),
                                     style="bold cyan"),
                            title="🤖 AI", border_style="green", box=ROUNDED)

            if len(parts) == 1:
                return parts[0]
            return Group(*parts)

        # ── MCP 路径安全校验器（桥接 Onyx 沙箱与 MCP 工具执行）──
        def _mcp_path_validator(tool: str, path: str) -> Tuple[bool, str]:
            """校验 MCP 工具操作的路径是否在 Onyx 沙箱允许范围内"""
            # 尝试通过 onyx_module 调用 check_sandbox_path
            if onyx_module and hasattr(onyx_module, "check_sandbox_path"):
                try:
                    if not onyx_module.check_sandbox_path(path, request_id):
                        lang = get_current_lang()
                        if lang == "chinese":
                            return False, f"⛔ 沙箱拦截：MCP 工具 '{tool}' 无权访问路径 '{path}'"
                        return False, f"⛔ Sandbox blocked: MCP tool '{tool}' cannot access path '{path}'"
                except Exception as e:
                    if log_warning:
                        log_warning(f"MCP path check exception for '{path}': {e}", request_id)

            # 回退：检查是否在用户主目录内
            home = user_home_dir or USER_HOME_DIR
            try:
                real_path = os.path.realpath(path) if os.path.exists(path) else os.path.abspath(path)
                real_home = os.path.realpath(home)
                if real_path == real_home or real_path.startswith(real_home + os.sep):
                    return True, ""
            except Exception:
                pass

            # 最终回退：放行（非 Termux 环境）
            if not os.path.exists('/data/data/com.termux'):
                return True, ""

            lang = get_current_lang()
            if lang == "chinese":
                return False, f"⛔ 路径越界：MCP 工具 '{tool}' 尝试访问 '{path}'，超出用户主目录范围"
            return False, f"⛔ Path out of bounds: MCP tool '{tool}' attempted to access '{path}'"

        def _execute_single_tool(tool_name: str, params_str: str = "") -> None:
            """执行单个 MCP 工具并将结果追加到面板展示列表"""
            import json as _json
            exec_key = f"{tool_name}:{params_str}"
            if exec_key in executed_tools:
                return
            executed_tools.append(exec_key)

            # Plan 模式未确认 → 跳过
            if mode == "plan" and not plan_confirmed:
                tool_results_display.append({
                    "name": tool_name, "params": params_str[:80],
                    "ok": False, "output": _mcp_t("Plan 模式: 已跳过", "Plan mode: skipped"),
                    "lines": []
                })
                return

            try:
                if params_str.strip():
                    params = _json.loads(params_str)
                else:
                    params = {}
            except _json.JSONDecodeError:
                params = _parse_tool_params(params_str, "")

            ok, output = execute_mcp_tool(tool_name, params, "filesystem", _current_user_mode,
                                          path_validator=_mcp_path_validator)
            # 取前100字符用于面板展示
            _preview = output[:100] + ("..." if len(output) > 100 else "")
            tool_results_display.append({
                "name": tool_name, "params": params_str[:80],
                "ok": ok, "output": output,
                "preview": _preview
            })

        def _try_extract_blocks() -> None:
            """从 stream_buffer 中扫描所有已知块类型，分发到对应缓冲区并实时执行工具"""
            import re as _re
            nonlocal stream_buffer, txt_content, analysis_content, plan_content
            nonlocal ask_content, answer_state, memory_uuid, tag_val, prompt_val
            nonlocal _txt_phase

            # 连续扫描直到无法再提取完整块
            max_iter = 50  # 安全上限，防止死循环
            for _ in range(max_iter):
                buf = stream_buffer
                if not buf:
                    break
                # 前导换行会让 _re.match 失效（[TXT] 块被 _re.search 消费后剩余 \n[ANSWER]...）
                # buf_match 用于 _re.match 模式，buf 用于 _re.search 模式
                buf_match = buf.lstrip('\n\r ')
                match_offset = len(buf) - len(buf_match)

                # ── [TXT]...[TXT:DONE] 多行块 ──
                # (?![:D]) 防止误匹配 [TXT]: 和 [TXT:DONE] 前缀
                # 不要求 \n 在 [TXT] 前，支持 [TXT]content 同行格式
                m = _re.search(r'\[TXT\](?![:D])(.*?)\[TXT:DONE\]', buf, _re.DOTALL)
                if m:
                    block_text = m.group(1)
                    txt_content += block_text  # 追加而非覆盖
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    _txt_phase = "post_txt"
                    # ── 扫描 TXT 块内是否嵌套 [ANSWER]yes/no（AI 可能违反格式规范）──
                    ans_inner = _re.search(r'\[ANSWER\](yes|no)', block_text)
                    if ans_inner and not answer_state:
                        answer_state = ans_inner.group(1)
                    continue

                # ── [TXT]: 单行（新格式，逐行提取）──
                m = _re.match(r'\[TXT\]:(.*?)(\n|$)', buf_match)
                if m:
                    txt_content += m.group(1) + "\n"
                    stream_buffer = buf[match_offset + m.end():]
                    _txt_phase = "in_txt"
                    continue

                # ── [PLAN]...[PLAN:DONE] 多行块（大写新格式，优先）──
                m = _re.search(r'\[PLAN\](.*?)\[PLAN:DONE\]', buf, _re.DOTALL)
                if m:
                    plan_content += m.group(1).strip()
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    continue

                # ── [plan]...[plan:done] 多行块（小写旧格式，兼容）──
                m = _re.search(r'\[plan\]\n(.*?)\[plan:done\]', buf, _re.DOTALL)
                if m:
                    plan_content += m.group(1)
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    continue

                # ── [ANALYSIS]...[ANALYSIS:DONE] 多行块（优先于单行格式）──
                m = _re.search(r'\[ANALYSIS\](?![:D])(.*?)\[ANALYSIS:DONE\]', buf, _re.DOTALL)
                if m:
                    analysis_content += m.group(1).strip()
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    continue

                # ── [ANALYSIS]: 单行（兼容）──
                m = _re.match(r'\[ANALYSIS\]:(.*?)(\n|$)', buf_match)
                if m:
                    analysis_content += m.group(1) + "\n"
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [ANALYSIS]\n...[下一个 [XXX] 标记] 多行块（兼容旧格式）──
                # 原版只认 [ANSWER] 终止，若 AI 输出 [ANALYSIS]\n内容\n[TXT] 会死锁
                # 改为任意下一行 [ 开头的标记均可终止
                m = _re.search(r'\[ANALYSIS\]\n(.*?)(?=\n\[)', buf, _re.DOTALL)
                if m:
                    analysis_content += m.group(1).strip()
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    continue

                # ── [ANSWER]:yes/no ──
                m = _re.match(r'\[ANSWER\]:(yes|no)', buf_match)
                if m:
                    answer_state = m.group(1)
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [ANSWER]yes/no（无冒号）──
                m = _re.match(r'\[ANSWER\](yes|no)', buf_match)
                if m:
                    answer_state = m.group(1)
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [ASK]:text ──
                m = _re.match(r'\[ASK\]:(.*?)(\n|$)', buf_match)
                if m:
                    ask_content = m.group(1).strip()
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [MEMORY]:uuid ──
                m = _re.match(r'\[MEMORY\]:(.*?)(\n|$)', buf_match)
                if m:
                    memory_uuid = m.group(1).strip()
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [TAG]:value ──
                m = _re.match(r'\[TAG\]:(.*?)(\n|$)', buf_match)
                if m:
                    tag_val = m.group(1).strip()
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [PROMPT]...[PROMPT:DONE] 多行块（优先于单行格式）──
                m = _re.search(r'\[PROMPT\](?![:D])(.*?)\[PROMPT:DONE\]', buf, _re.DOTALL)
                if m:
                    prompt_val = m.group(1).strip()
                    if prompt_val:
                        _write_onyx_ai_prompt(prompt_val, user_home_dir)
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    continue

                # ── [PROMPT]:value（单行兼容）──
                m = _re.match(r'\[PROMPT\]:(.*?)(\n|$)', buf_match)
                if m:
                    prompt_val = m.group(1).strip()
                    if prompt_val:
                        _write_onyx_ai_prompt(prompt_val, user_home_dir)
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [PROMPT]text（无冒号单行兼容）──
                m = _re.match(r'\[PROMPT\](.*?)(\n|$)', buf_match)
                if m:
                    prompt_val = m.group(1).strip()
                    if prompt_val:
                        _write_onyx_ai_prompt(prompt_val, user_home_dir)
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [mood]: happy +0.1 / [mood]: angry -0.2 ──
                m = _re.match(r'\[mood\]:\s*(\S+)\s+([+-]\d+(?:\.\d+)?)(?:\n|$)', buf_match)
                if m:
                    try:
                        apply_mood_delta(m.group(1), float(m.group(2)))
                    except ValueError:
                        pass
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [PEOPLE]:add/Likeability/Perception ──
                m = _re.match(r'\[PEOPLE\]:\s*(\S+)\s+(.+?)(?:\n|$)', buf_match)
                if m:
                    action = m.group(1)
                    rest = m.group(2).strip()
                    if action.lower() == "add":
                        apply_people_action("add", rest)
                    elif action.lower() == "likeability":
                        parts = rest.rsplit(None, 1)
                        if len(parts) == 2:
                            apply_people_action("likeability", parts[0], parts[1])
                    elif action.lower() == "perception":
                        parts = rest.split(None, 1)
                        if len(parts) == 2:
                            apply_people_action("perception", parts[0], parts[1])
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [CLASS]:N / [SLEEP]:N（元数据，静默消费）──
                m = _re.match(r'\[(?:CLASS|SLEEP)\]:(.*?)(\n|$)', buf_match)
                if m:
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── [tool:name]\n{json}\n[tool:name:done] 新格式 ──
                m = _re.search(r'\[tool:(\S+)\]\n(\{.*?\})\n\[tool:\1:done\]', buf, _re.DOTALL)
                if m:
                    tool_name = m.group(1)
                    params_str = m.group(2).strip()
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    _execute_single_tool(tool_name, params_str)
                    continue

                # ── [tool:name 空格参数]...[tool:name:done] 旧格式 ──
                m = _re.search(r'\[tool:(\S+)\s+([^\]]*)\](.*?)\[tool:\1:done\]', buf, _re.DOTALL)
                if m:
                    tool_name = m.group(1)
                    params_str = m.group(2).strip()
                    body = m.group(3).strip() if m.lastindex and m.lastindex >= 3 else ""
                    stream_buffer = buf[:m.start()] + buf[m.end():]
                    params = _parse_tool_params(params_str, body)
                    import json as _json
                    ps = _json.dumps(params, ensure_ascii=False) if isinstance(params, dict) else str(params)
                    _execute_single_tool(tool_name, ps)
                    continue

                # ── [ANSWER]（无冒号，多行格式的结束标记，静默消费）──
                m = _re.match(r'\[ANSWER\]\s*(\n|$)', buf_match)
                if m:
                    stream_buffer = buf[match_offset + m.end():]
                    continue

                # ── 裸文本行（无 [ 前缀）→ pre/in_txt 阶段收集到 txt ──
                if _txt_phase in ("pre", "in_txt"):
                    m = _re.match(r'^([^\[]+)', buf_match)
                    if m:
                        raw = m.group(1)
                        # 只移除控制字符（回车/换行/空字节），保留空格和制表符以保持缩进
                        clean = raw.lstrip('\r\n\0')
                        if clean:
                            # 只保留安全部分：如果文本末尾可能是不完整的标记开始符，保守截断
                            to_take = clean
                            stream_buffer = buf[match_offset + len(raw):]
                            txt_content += to_take
                            continue
                        elif raw and raw != clean:
                            # 只有控制字符 → 丢弃它们
                            stream_buffer = buf[match_offset + len(raw):]
                            continue

                break  # 无法再提取任何完整块

        def on_stream_content(chunk: str) -> None:
            """实时流式回调：统一提取所有块类型并更新复合 Panel"""
            nonlocal stream_buffer, txt_content, _content_started
            _content_started = True  # 首次收到内容，切换到内容面板

            # 规范化换行符 + 去除原始回车符（防止 ^M 污染显示）
            chunk = chunk.replace('\r\n', '\n').replace('\r', '\n')

            stream_buffer += chunk

            # _try_extract_blocks 负责从 stream_buffer 提取文本并追加到 txt_content

            # 防止缓冲区无限增长（异常情况下丢旧数据）
            if len(stream_buffer) > 50000:
                stream_buffer = stream_buffer[-5000:]

            # 提取所有完整块（处理 [TXT]/[ANSWER]/[TAG] 等结构化标记）
            _try_extract_blocks()

            # 更新 Live Panel
            if live_ref[0]:
                live_ref[0].update(_render_all_panels())
        
        # 启动 Live Panel：动画 spinner + 流式展示
        from rich.spinner import Spinner
        spinner = Spinner("dots", text=_mcp_t(" 思考中...", " Thinking..."), style="bold cyan")
        initial_panel = Panel(spinner, title="🤖 AI", border_style="green", box=ROUNDED)
        
        ai_result = {}
        _live_shown = False  # 标记 Live Panel 是否已展示（避免重复 console.print）
        try:
            if log_info:
                log_info(lang_text["api_call"].format(current_question[:50]), current_session_id)

            with Live(initial_panel, console=console, refresh_per_second=15, transient=False) as live:
                live_ref[0] = live
                loading_flag[0] = False  # Live Panel 已接管展示
                
                # 使用SSE模式调用（带实时流式回调）
                _mcp_debug(f"调用 call_ai_api_sse(messages={len(conversation_history)}条)")
                # ── debug 模式：把 AI 真实看到的 conversation 写入 deb/{session_id}/round_N.txt ──
                if debug_mode:
                    _deb_session_dir = os.path.join(user_home_dir, ".ai_s", "deb", current_session_id)
                    os.makedirs(_deb_session_dir, exist_ok=True)
                    _conv_path = os.path.join(_deb_session_dir, f"round_{interaction_count}.txt")
                    try:
                        _lines = []
                        for _idx, _msg in enumerate(conversation_history):
                            _role = _msg.get("role", "?")
                            _content = _msg.get("content", "") or ""
                            _lines.append(f"╔══ [{_idx}] {_role.upper()} ══╗")
                            _lines.append(_content.rstrip())
                            _lines.append("")
                        with open(_conv_path, "w", encoding="utf-8") as _cf:
                            _cf.write("\n".join(_lines))
                        _mcp_debug(f"conversation saved: {_conv_path} ({len(conversation_history)} msgs)")
                    except Exception as _ce:
                        _mcp_debug(f"conversation save failed: {_ce}")
                try:
                    _reasoning_buffer = []
                    _content_started = False
                    def _on_reasoning(chunk: str) -> None:
                        """流式显示 AI 思考过程"""
                        nonlocal _reasoning_buffer, _content_started
                        if _content_started:
                            return  # 已切换到内容显示，不再更新思考面板
                        _reasoning_buffer.append(chunk)
                        _text = "".join(_reasoning_buffer[-100:])
                        live.update(Panel(
                            RichText(_text, style="dim italic"),
                            title="🤖 AI 思考中...",
                            border_style="bright_black",
                            box=ROUNDED,
                        ))
                    def _on_tool_call(tool_name: str) -> None:
                        """流式检测到工具调用时立即更新面板"""
                        # 不展示工具调用信息给用户，保持界面清爽
                    api_raw_result = call_ai_api_sse(
                        question="", 
                        messages=conversation_history,
                        new_key=new_key, 
                        debug_mode=debug_mode, 
                    onyx_module=onyx_module,
                    mode=mode,
                    times=current_times,
                    ai_tools_prompt=ai_tools_prompt,
                    on_content=on_stream_content,
                    on_tool_call=_on_tool_call,
                    on_reasoning=_on_reasoning,
                    user_home_dir=user_home_dir,
                    tools=native_tools,
                    )
                    _mcp_debug(f"call_ai_api_sse 返回: {'interrupted' if (api_raw_result or {}).get('_interrupted') else 'OK' if api_raw_result else 'None'}")
                except Exception as _api_exc:
                    import traceback as _tb
                    import sys as _sys
                    _tb.print_exc(file=_sys.stderr)
                    _mcp_debug(f"call_ai_api_sse 异常: {type(_api_exc).__name__}: {_api_exc}")
                    console.print(f"[red]API 调用异常: {_api_exc}[/]")
                    continue_asking = False
                    break
                current_times += 1
                
                # Live Panel 最终更新
                if (api_raw_result or {}).get("_interrupted"):
                    live.update(Panel(_mcp_t("⏹ 已中断", "⏹ Interrupted"), title="🤖 AI", border_style="yellow", box=ROUNDED))
                else:
                    parsed_txt = (api_raw_result or {}).get("txt", "").strip()
                    api_error = (api_raw_result or {}).get("error", "")
                    if parsed_txt:
                        parsed_txt = _strip_markers(parsed_txt)
                        live.update(render_ai_panel(parsed_txt))
                        _live_shown = True
                    elif api_error:
                        err_short = api_error[:200] + ("..." if len(api_error) > 200 else "")
                        live.update(Panel(f"❌ {err_short}", title="🤖 AI", border_style="red", box=ROUNDED))
                        _live_shown = True
            
            # SSE返回的已经是解析好的dict
            if isinstance(api_raw_result, dict):
                ai_result = api_raw_result
            else:
                ai_result = {"error": f"Format error: {str(api_raw_result)[:50]}", "answer": "no", "ask": ""}
                live_ref[0] = None
                
        except Exception as e:
            ai_result = {"error": f"SSE processing error: {str(e)}", "answer": "no", "ask": ""}
        finally:
            loading_flag[0] = False
            live_ref[0] = None
        
        ai_result = process_ai_result_fields(ai_result)

        # ── 流式解析的 answer_state 合并到 ai_result（流式解析能捕获 TXT 块内嵌套的 [ANSWER]）──
        if answer_state:
            ai_result["answer"] = answer_state

        # 处理 [PROMPT]: 字段 — 写入 .ai_s/onyx_ai.md 最高指示
        _prompt_from_result = ai_result.get("prompt", "") or prompt_val
        if _prompt_from_result.strip():
            _write_onyx_ai_prompt(_prompt_from_result, user_home_dir)

        was_interrupted = ai_result.get("_interrupted", False)
        if was_interrupted:
            continue_asking = False  # don't auto-loop, but still process any commands below
        
        has_error = "error" in ai_result and ai_result["error"]
        has_txt = ai_result.get("txt", "").strip() if ai_result.get("txt") else False
        answer = ai_result.get("answer", "no")
        ai_ask = ai_result.get("ask", "") or ""
        tag = ai_result.get("tag", "") or ""
        memory_uuid = ai_result.get("memory", "") or ""
        # 优先用 [PLAN] 文本标记，其次用 submit_plan 工具结果
        plan_text = ai_result.get("plan", "") or _pending_plan or ""
        tool_calls = ai_result.get("tool_calls", [])
        markup_blocks = ai_result.get("markup_blocks", [])
        sleep_value = ai_result.get("sleep")
        class_level = ai_result.get("class", "1")
        
        sleep_seconds = 0
        if sleep_value is not None:
            try:
                sleep_seconds = int(sleep_value)
            except (ValueError, TypeError):
                sleep_seconds = 0
        
        if sleep_seconds > 0 and answer == "no":
            interrupted, waited_seconds = handle_sleep_wait(sleep_seconds, current_session_id, lang_text, log_info)
            
            _md = current_lang == "english"
            sleep_record = f"\n\n### {'Sleep' if _md else '休眠'} ({time.strftime('%H:%M:%S')})\n\n"
            if interrupted:
                sleep_record += f"- {'Interrupted after' if _md else '中断于'} {waited_seconds}/{sleep_seconds}s\n"
            else:
                sleep_record += f"- {'Completed' if _md else '完成'} {sleep_seconds}s\n"
            
            existing_content, record_path = get_latest_ai_session(user_home_dir, current_session_id)
            if record_path:
                try:
                    with open(record_path, "a", encoding="utf-8") as f:
                        f.write(sleep_record)
                except Exception:
                    pass
            
            continue
        
        if memory_uuid and not referenced_memory_uuid:
            referenced_memory_uuid = memory_uuid
            console.print(lang_text["memory_referenced"].format(memory_uuid[:8] + "..."), style="bold cyan")
        
        if has_error:
            error_str = str(ai_result["error"])
            if "Request failed" in error_str or "Connection" in error_str or "timeout" in error_str.lower():
                console.print(lang_text["api_conn_fail"], style="bold red")
            else:
                console.print(f"❌ {lang_text['api_error'].format(error_str)}", style="bold red")
            if log_error:
                log_error(f"AI error: {error_str}", current_session_id)
            continue_asking = False
            continue
        
        if not message_appended and (has_txt or ai_ask):
            message_id = append_message_to_chat(
                user_home_dir, current_chat_name, current_session_id,
                last_user_question, ai_result.get("txt", ""), tag, class_level
            )
            message_appended = True
            if debug_mode:
                debug_prefix = "[DEBUG] " if current_lang == "english" else "[DEBUG] "
                console.print(debug_prefix + f"Message appended: {message_id}", style="bold magenta")
        elif message_appended and tag:
            update_message_tag(user_home_dir, current_chat_name, current_session_id, tag, class_level)
            if debug_mode:
                debug_prefix = "[DEBUG] " if current_lang == "english" else "[DEBUG] "
                console.print(debug_prefix + f"Tag updated: {tag[:50]}...", style="bold magenta")
        elif message_appended and answer == "yes":
            update_message_tag(user_home_dir, current_chat_name, current_session_id, tag, class_level)
        
        if ai_ask.strip():
            # 如果已通过流式展示了 txt 内容，不再重复打印
            if has_txt and not txt_content.strip():
                console.print(lang_text["ai_answer"], style="bold green")
                console.print("-" * 50, style="white")
                for line in ai_result["txt"].strip().split('\n'):
                    console.print(line, style="white")
                console.print("-" * 50, style="white")
            
            # Rich Panel 展示 AI 提问
            console.print(Panel(
                ai_ask.strip(),
                title="🤔 " + lang_text.get("ai_ask", "AI 提问"),
                border_style="yellow",
                box=ROUNDED,
                padding=(1, 2),
            ))
            
            try:
                user_answer = ui_text_input("💬 You").strip()
                last_user_question = user_answer  # 记录追问，供聊天记忆使用
                message_appended = False           # 新输入 → 允许追加新消息
                # 标准 messages：AI 提问 + 用户回答
                _ask_msg = {"role": "assistant", "content": ai_ask.strip()}
                _ask_reasoning = ai_result.get("_reasoning", "")
                if _ask_reasoning:
                    _ask_msg["reasoning_content"] = _ask_reasoning
                conversation_history.append(_ask_msg)
                conversation_history.append({"role": "user", "content": user_answer})
                current_question = f"{current_question}\n\nUser answer: {user_answer}" if current_lang == "english" else f"{current_question}\n\n用户回答：{user_answer}"
                continue_asking = True
                
                if interaction_count == 1:
                    record_ai_session(user_home_dir, current_session_id, initial_question, ai_result, user_answer, {}, referenced_memory_uuid or "", native_results=_native_call_log_text)
                else:
                    existing_content, record_path = get_latest_ai_session(user_home_dir, current_session_id)
                    if existing_content and record_path:
                        _ts = time.strftime('%Y-%m-%d %H:%M:%S')
                        _md = current_lang == "english"
                        new_content = f"\n\n### {'Interaction' if _md else '交互'} #{interaction_count} ({_ts})\n\n"
                        new_content += f"- **{'AI Ask' if _md else 'AI询问'}**:\n  {ai_ask.strip()}\n"
                        new_content += f"- **{'User Answer' if _md else '用户回答'}**:\n  {user_answer}\n"
                        try:
                            with open(record_path, "a", encoding="utf-8") as f:
                                f.write(new_content)
                        except Exception:
                            pass
                
                continue
            except KeyboardInterrupt:
                console.print("\n^C", style="bold yellow")
                user_answer = "User cancelled the answer" if current_lang == "english" else "用户取消了回答"
                continue_asking = False
            except EOFError:
                console.print("\n^D", style="bold yellow")
                user_answer = "User terminated the session" if current_lang == "english" else "用户终止了会话"
                continue_asking = False
        
        # 如果已通过流式或 Live Panel 展示了 txt 内容，不再重复打印
        # _live_shown 在 Live 块内设为 True，避免 Live 结束后 console.print 再打一遍
        if has_txt and not ai_ask.strip() and not _live_shown:
            cleaned_txt = _strip_markers(ai_result["txt"])
            console.print(render_ai_panel(cleaned_txt.strip()))
        
        ai_commands = extract_ai_commands(ai_result)
        # 硬限制：最多执行 10 条命令，超出的丢弃并通知 AI
        if len(ai_commands) > 10:
            _discarded = ai_commands[10:]
            ai_commands = ai_commands[:10]
            _warn = lang_text.get("cmd_limit", "⚠️ 命令超过 10 条限制，已截断前 10 条执行") if False else "⚠️ 命令超过 10 条限制，已截断前 10 条执行"
            console.print(f"  [bold yellow]{_warn}[/]")
            conversation_history.append({"role": "system", "content": f"[SYSTEM] {_warn}。多余的 {len(_discarded)} 条命令被丢弃，请下一轮继续。"})
        analysis_content = (ai_result.get("analysis", "") or "").strip()
        
        if ai_commands and not analysis_content:
            analysis_content = lang_text["analysis_cmd_prefix"].format(len(ai_commands))
            for idx, cmd in enumerate(ai_commands, 1):
                analysis_content += f"{idx}. {cmd}\n"
        
        if analysis_content:
            console.print(render_analysis_panel(analysis_content))
        
        # ── Token usage stats (from stream_options.include_usage) ──
        _usage_info = ai_result.get("_usage")
        if _usage_info:
            _total = _usage_info.get("total_tokens", 0)
            _prompt = _usage_info.get("prompt_tokens", 0)
            _completion = _usage_info.get("completion_tokens", 0)
            _cache_hit = _usage_info.get("prompt_cache_hit_tokens", 0)
            _cache_miss = _usage_info.get("prompt_cache_miss_tokens", 0)
            # 存下精确 prompt_tokens（末尾显示用，纯磁盘架构不依赖内存 tracker）
            if _prompt:
                _thread_locals.last_prompt_tokens = _prompt
            parts = [f"⚡ {_total} tokens"]
            if _cache_hit:
                saved_pct = _cache_hit / (_cache_hit + _cache_miss) * 100 if (_cache_hit + _cache_miss) else 0
                parts.append(f"💰 cache {saved_pct:.0f}% hit")
            console.print(f"  [dim]{' · '.join(parts)}[/]")
        
        # ---- Plan 确认流程（纯引导模式）----
        if plan_text and plan_text.strip():
            _plan_display = plan_text
            try:
                _plan_json = json.loads(plan_text)
                if isinstance(_plan_json, dict):
                    _plan_display = _plan_json.get("plan", plan_text)
            except (json.JSONDecodeError, TypeError):
                pass

            _ensure_library_record()
            plan_choice = confirm_plan(_plan_display, lang_text)

            if plan_choice == "discard":
                console.print(lang_text.get("plan_discarded", "🗑️ 计划已摒弃，将通知 AI 重新规划"), style="bold yellow")
                conversation_history.append({"role": "user", "content": "[用户摒弃了你的计划，请重新制定]"})
                _pending_plan = ""
                continue_asking = True
                continue

            elif plan_choice == "guide":
                console.print(lang_text.get("plan_guide_prompt", "💡 请输入你对计划的修改意见："), style="bold cyan")
                try:
                    guide_text = ui_text_input("💡 修改意见").strip()
                except (KeyboardInterrupt, EOFError):
                    guide_text = ""
                    console.print()
                if guide_text:
                    conversation_history.append({"role": "user", "content": f"[用户对计划的指导意见]:\n{guide_text}\n\n请根据指导意见修改计划。"})
                else:
                    conversation_history.append({"role": "user", "content": "[用户未提供具体意见，请简化或重新生成计划]"})
                _pending_plan = ""
                continue_asking = True
                continue

            elif plan_choice == "confirm":
                console.print(lang_text.get("plan_confirmed", "✅ 计划已确认，即将进入执行阶段"), style="bold green")
                # 将确认后的计划内容追加到 AI 的上下文
                conversation_history.append({"role": "user", "content": f"[用户已确认以下计划，请开始执行]:\n{_plan_display}"})
                _pending_plan = ""
                plan_confirmed = True
                continue_asking = True
                continue

        # Plan 模式安全限制：未确认计划前，拦截所有命令执行和工具调用
        # 既支持 mode=="plan"（用户输入 ai plan），也支持 _PLAN_MODE_ACTIVE（AI 调用 EnterPlanMode）
        if (mode == "plan" or _PLAN_MODE_ACTIVE) and not plan_confirmed:
            if ai_commands or tool_calls:
                console.print(lang_text.get("plan_blocked",
                    "⛔ Plan 模式：AI 命令/工具调用已被拦截。请先确认计划。"), style="bold red")
            ai_commands = []
            tool_calls = []
        
        # ── 工具结果收集器（同时服务 markup 和 MCP 工具）──
        tool_results = []
        _native_call_log_text = ""

        # ── 处理自研标记语言块 [VIEW:]/[EDIT:]/[WRITE:]/[APPEND:]/[INSERT:]/[DELETE:] ──
        if markup_blocks:
            try:
                _native_results = _process_native_blocks(markup_blocks, cwd=user_home_dir, user_mode=_current_user_mode)
                _native_call_log = []
                for _nr in _native_results:
                    _op_type = _nr.type.upper()
                    _op_path = _nr.path
                    icon = "✅" if _nr.success else "❌"
                    _nr_type = _nr.type

                    # ── 风暴检测：重复 VIEW/EDIT 同一文件 ──
                    _nb_key = f"{_nr_type}:{_op_path}"
                    if _nr.success:
                        _repeat_success[_nb_key] = _repeat_success.get(_nb_key, 0) + 1
                        _storm_counter.pop(_nb_key, None)
                        if _repeat_success[_nb_key] >= 3 and _nr_type == "view":
                            _storm_warn = _mcp_t(
                                f"⚠️ 重复警告：已查看 {_op_path} 第 {_repeat_success[_nb_key]} 次，AI 应停止重复查看",
                                f"⚠️ Repeat alert: viewed {_op_path} {_repeat_success[_nb_key]}x, AI should stop"
                            )
                            console.print(f"  [bold yellow]{_storm_warn}[/]")
                            # 不阻断 VIEW（只读无害），只提示
                    elif not _nr.success:
                        _storm_counter[_nb_key] = _storm_counter.get(_nb_key, 0) + 1
                        _repeat_success.pop(_nb_key, None)
                        if _storm_counter[_nb_key] >= 3:
                            _storm_warn = _mcp_t(
                                f"⚠️ 风暴检测：对 {_op_path} 连续失败 {_storm_counter[_nb_key]} 次，AI 应更换策略",
                                f"⚠️ Storm detected: {_op_path} failed {_storm_counter[_nb_key]}x, switch strategy"
                            )
                            console.print(f"  [bold red]{_storm_warn}[/]")
                            conversation_history.append({"role": "system", "content": f"[STORM_WARNING] {_storm_warn}"})

                    if _nr_type == "view" and _nr.success:
                        view_content = _nr.content or ""
                        lines = view_content.split("\n")
                        console.print(f"  {icon} 📖 {_op_path}  ({len(lines)} 行)")
                        # 显示前 10 行内容（精简）
                        for line in lines[:10]:
                            console.print(f"    {line}", style="dim")
                        if len(lines) > 10:
                            console.print(f"    ... 共 {len(lines)} 行", style="dim")
                        # 截断超大 VIEW 结果，防止上下文撑爆
                        if len(view_content) > _MAX_TOOL_OUTPUT:
                            _trunc_info = f"\n\n…[截断 {len(view_content) - _MAX_TOOL_OUTPUT} 字节，共 {len(view_content)} 字节 — 缩小查看范围避免撑爆上下文]…\n\n"
                            view_content = view_content[:_MAX_TOOL_OUTPUT // 2] + _trunc_info + view_content[-_MAX_TOOL_OUTPUT // 2:]
                        tool_results.append(view_content)
                        _record = f"[{_op_type}] {_op_path}\n{view_content}"

                    elif _nr_type == "edit" and _nr.success:
                        old_c = _nr.old_content or ""
                        new_c = _nr.content or ""
                        old_lines = old_c.split("\n")
                        new_lines = new_c.split("\n")
                        # 重复编辑防护：连续 3 次以上相同编辑 → 提示 AI 停止
                        _edit_key = f"edit_success:{_op_path}:{hash(new_c) % 10000}"
                        _edit_rep = _repeat_success.get(_edit_key, 0)
                        if _edit_rep >= 3:
                            _storm_warn = _mcp_t(
                                f"⚠️ 重复编辑 {_op_path} 已达 {_edit_rep + 1} 次，内容未变化，AI 应停止",
                                f"⚠️ Repeated edit on {_op_path} {_edit_rep + 1}x, content unchanged, AI should stop"
                            )
                            console.print(f"  [bold yellow]{_storm_warn}[/]")
                        _repeat_success[_edit_key] = _edit_rep + 1
                        # ── 彩色 diff 展示 ──
                        console.print(f"  {icon} ✏️ 编辑 {_op_path}  [{len(old_lines)}→{len(new_lines)} 行]")
                        try:
                            _render_edit_diff(old_c, new_c)
                        except Exception:
                            # fallback: 简单显示
                            console.print(f"    旧: {len(old_lines)} 行 → 新: {len(new_lines)} 行")
                        tool_results.append(f"[EDIT] {_op_path}: {new_c}")
                        _record = f"[{_op_type}] {_op_path}\n旧({len(old_lines)}行):\n{old_c}\n新({len(new_lines)}行):\n{new_c}"

                    elif _nr_type in ("write", "append", "insert") and _nr.success:
                        content = _nr.content or ""
                        total_lines = len(content.split("\n"))
                        action_label = {"write": "📝 写入", "append": "➕ 追加", "insert": "📌 插入"}.get(_nr_type, "✏️")
                        preview = content[:100].replace("\n", "↵ ").strip()
                        preview += "..." if len(content) > 100 else ""
                        # 截断超大写入内容，防止上下文撑爆
                        if len(content) > _MAX_TOOL_OUTPUT:
                            content = content[:_MAX_TOOL_OUTPUT // 2] + f"\n\n…[截断 {len(content) - _MAX_TOOL_OUTPUT} 字节，共 {len(content)} 字节]…\n\n" + content[-_MAX_TOOL_OUTPUT // 2:]
                        console.print(f"  {icon} {action_label} {_op_path}  ({total_lines} 行)")
                        console.print(f"    {preview}", style="dim")
                        tool_results.append(f"[{_op_type}] {_op_path}: {content}")
                        _record = f"[{_op_type}] {_op_path} ({total_lines}行)\n{content}"

                    elif _nr_type == "delete" and _nr.success:
                        del_content = _nr.content or ""
                        del_lines = del_content.split("\n")
                        console.print(f"  {icon} 🗑️ 删除 {_op_path}  ({len(del_lines)} 行)")
                        # 显示被删行（整行红色底色）
                        for _dl_idx, _dl_line in enumerate(del_lines, 1):
                            console.print((f"  {_dl_idx:>4} │ {_dl_line}").ljust(80), style="white on red")
                        if len(del_lines) > 20:
                            console.print(f"  [dim white]... 共 {len(del_lines)} 行[/]")
                        tool_results.append(f"[DELETE] {_op_path}: 删除 {len(del_lines)} 行")
                        _record = f"[{_op_type}] {_op_path}\n{del_content}"

                    elif _nr.success:
                        console.print(f"  {icon} [{_op_type}] {_op_path}: {_nr.message}")
                        tool_results.append(f"[{_nr.type}] {_nr.path}: {_nr.content or _nr.message}")
                        _record = f"[{_op_type}] {_op_path}: {_nr.message}"
                    else:
                        # 失败操作
                        console.print(f"   {icon} [{_op_type}] {_op_path}: {_nr.message}", style="bold red")
                        tool_results.append(f"❌ [{_nr.type}] {_nr.path}: {_nr.message}")
                        _record = f"❌ [{_op_type}] {_op_path}: {_nr.message}"

                    _native_call_log.append(_record)
                _native_call_log_text = "\n\n".join(_native_call_log)
            except Exception as _native_err:
                console.print(f"   ❌ 原生文件操作异常: {_native_err}", style="bold red")
                _native_call_log_text = f"❌ 原生文件操作异常: {_native_err}"

        # ── 原生标记语言结果追加到 conversation_history（让 AI 立刻看到）──
        if _native_call_log_text or (tool_results and not tool_calls):
            _native_feedback = _native_call_log_text or "\n".join(tool_results)
            if _native_feedback.strip():
                conversation_history.append({"role": "system", "content": f"[NATIVE_RESULT]\n{_native_feedback.strip()}"})

        # 处理 AI 工具调用 ([tool:...] 格式)
        if tool_calls:
            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_params_str = tc.get("params_str", "")
                tool_body = tc.get("body", "")

                # 去重：相同工具+参数已在前几轮执行过，复用缓存结果
                exec_key = f"{tool_name}:{tool_params_str}"
                if exec_key in executed_tools:
                    cached_output = _tool_results_cache.get(exec_key, "")
                    tool_results.append(cached_output)
                    console.print(f"   → (缓存) {cached_output[:100]}...", style="dim")
                    continue

                # 显示绿色工具调用提示（去前缀）
                _tool_display_name = tool_name
                if _tool_display_name.startswith("mcp__"):
                    _tool_display_name = _tool_display_name.rsplit("__", 1)[-1]
                elif _tool_display_name.startswith("mcp_"):
                    _tool_display_name = _tool_display_name[4:]
                console.print(f"  [bold green]🔧 {_tool_display_name}[/]")

                # 解析参数（JSON优先 → _parse_tool_params 回退）
                if tool_params_str.strip().startswith("{"):
                    try:
                        params = json.loads(tool_params_str.strip())
                    except (json.JSONDecodeError, ValueError):
                        # JSON 非法 → 反馈 schema 引导 AI 重发
                        _err_schema = f"❌ JSON parse failed for {tool_name}. Arguments must be valid JSON."
                        tool_results.append(_err_schema)
                        console.print(f"   {_err_schema}", style="bold red")
                        # 跳过本轮执行，下一轮 AI 看到错误信息后会修正参数
                        executed_tools.append(f"{tool_name}:{tool_params_str}")
                        continue
                else:
                    params = _parse_tool_params(tool_params_str, tool_body)

                # 先尝试内置 handler，走不通再走 MCP
                ok, output = execute_mcp_tool(tool_name, params, "filesystem", _current_user_mode,
                                              path_validator=_mcp_path_validator)

                # ── 风暴检测（MCP 工具调用）──
                _tc_key = f"mcp:{tool_name}:{tool_params_str[:80]}"
                if not ok:
                    _storm_counter[_tc_key] = _storm_counter.get(_tc_key, 0) + 1
                    _repeat_success.pop(_tc_key, None)
                    if _storm_counter[_tc_key] >= 3:
                        _storm_warn = _mcp_t(
                            f"⚠️ 风暴检测：{tool_name} 连续失败 {_storm_counter[_tc_key]} 次，AI 应更换策略",
                            f"⚠️ Storm detected: {tool_name} failed {_storm_counter[_tc_key]}x, AI should switch strategy"
                        )
                        console.print(f"  [bold red]{_storm_warn}[/]")
                        conversation_history.append({"role": "system", "content": f"[STORM_WARNING] {_storm_warn}"})
                else:
                    _storm_counter.pop(_tc_key, None)

                executed_tools.append(exec_key)
                _tool_results_cache[exec_key] = output  # 缓存结果供后续去重复用

                if ok:
                    # 截断超大工具结果，防止上下文撑爆
                    if len(output) > _MAX_TOOL_OUTPUT:
                        output = output[:_MAX_TOOL_OUTPUT // 2] + f"\n\n…[truncated {len(output) - _MAX_TOOL_OUTPUT} bytes of {len(output)} total]…\n\n" + output[-_MAX_TOOL_OUTPUT // 2:]
                    tool_results.append(output)
                    # 灰字显示简短结果
                    short = output[:100] + ("..." if len(output) > 100 else "")
                    console.print(f"   → {short}", style="dim")
                else:
                    err_msg = f"❌ 工具执行失败: {output}"
                    tool_results.append(err_msg)
                    console.print(f"   {err_msg}", style="bold red")

            # ── 提取 submit_plan / mark_step_complete 结果 ──
            for _tc_idx, _tc in enumerate(tool_calls):
                _tc_name = _tc.get("name", "")
                if _tc_name.endswith("submit_plan"):
                    if _tc_idx < len(tool_results) and tool_results[_tc_idx]:
                        _pending_plan = tool_results[_tc_idx]
                    break

            # ── 追加工具调用结果到 conversation_history ──
            # 无论来源是 MCP tool_calls 还是原生 markup_blocks，
            # 结果必须回传给 AI，否则 AI 不知道自己操作已生效，会反复重试
            if tool_calls:
                # 标准 OpenAI/DeepSeek 工具调用格式：
                # assistant: tool_calls → tool: 结果
                tc_ids = [f"call_{interaction_count}_{i}" for i in range(len(tool_calls))]
                import json as _json
                _tool_call_items = []
                for i, tc in enumerate(tool_calls):
                    _raw_args = tc.get("params_str", "{}")
                    try:
                        _parsed = _json.loads(_raw_args)
                        _args_str = _json.dumps(_parsed, ensure_ascii=False)
                    except (_json.JSONDecodeError, ValueError):
                        _args_str = _raw_args
                    _tool_call_items.append({
                        "id": tc_ids[i],
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": _args_str,
                        }
                    })
                _reasoning = ai_result.get("_reasoning", "")
                _assistant_msg = {
                    "role": "assistant",
                    "content": None,  # DeepSeek thinking mode 要求 tool_call 时 content 为 null
                    "tool_calls": _tool_call_items,
                }
                if _reasoning:
                    _assistant_msg["reasoning_content"] = _reasoning
                conversation_history.append(_assistant_msg)
                # tool role 结果消息
                for i, res in enumerate(tool_results):
                    conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tc_ids[i],
                        "content": res,
                    })
            # 原生标记语言结果由 library 记忆系统持久化（record_ai_session），
            # 下一轮 build_memory_context 从磁盘加载注入提示词。
            # 不嵌入 AI 回复，保持流式输出纯净。

            # 写入 library 磁盘（Markdown格式，仅记录用途）
            if tool_results:
                _now_str = datetime.now().strftime('%H:%M:%S')
                _log_lines = [f"### 第 {interaction_count} 轮工具调用 ({_now_str})", ""]
                _res_idx = 0
                for tc in tool_calls:
                    _tn = tc.get("name", "?")
                    _res = tool_results[_res_idx] if _res_idx < len(tool_results) else "(无结果)"
                    _res_idx += 1
                    _log_lines.append(f"- **工具**: `{_tn}`")
                    _log_lines.append(f"  ```")
                    _log_lines.append(f"  {_res}")
                    _log_lines.append(f"  ```")
                _log_text = "\n".join(_log_lines)
                _, record_path = get_latest_ai_session(user_home_dir, current_session_id)
                if record_path:
                    try:
                        with open(record_path, "a", encoding="utf-8") as f:
                            f.write(f"\n\n{_log_text}\n")
                    except Exception:
                        pass

        # ── AI 纯文本回复 → 追加 assistant 消息 ──
        _ai_txt = (ai_result.get("txt", "") or "").strip()
        if _ai_txt and not tool_calls:
            _assistant_msg = {"role": "assistant", "content": _ai_txt}
            _reasoning = ai_result.get("_reasoning", "")
            if _reasoning:
                _assistant_msg["reasoning_content"] = _reasoning
            conversation_history.append(_assistant_msg)

        # ── 标记本轮已处理工具调用（命令的标记在 cmd_results 之后设置）──
        _tool_calls_processed_this_round = bool(tool_calls)
        
        cmd_results = {}
        
        if ai_commands and auto_exec:
            dangerous_cmds_found = []
            for cmd in ai_commands:
                is_danger, cmd_name = is_dangerous_command(cmd, dangerous_commands)
                if is_danger:
                    dangerous_cmds_found.append((cmd, cmd_name))
            
            original_cmd_count = len(ai_commands)
            
            if dangerous_cmds_found:
                confirmed_commands = []
                for cmd, cmd_name in dangerous_cmds_found:
                    confirmed, user_response, refuse_reason = confirm_dangerous_command(
                        cmd, cmd_name, lang_text, current_session_id, initial_question, interaction_count, log_info
                    )
                    if confirmed:
                        confirmed_commands.append(cmd)
                    else:
                        if log_info:
                            log_info(f"Dangerous command rejected by user: {cmd}, reason: {refuse_reason}", current_session_id)
                        refuse_prefix = "❌ User rejected dangerous command" if current_lang == "english" else "❌ 用户拒绝执行危险命令"
                        user_refuse_reasons.append(f"{refuse_prefix} [{cmd_name}]: {cmd}\n   Rejection reason: {refuse_reason}" if current_lang == "english" else f"❌ 用户拒绝执行危险命令 [{cmd_name}]: {cmd}\n   拒绝原因: {refuse_reason}")
                
                safe_commands = [cmd for cmd in ai_commands if not is_dangerous_command(cmd, dangerous_commands)[0]]
                ai_commands = confirmed_commands + safe_commands
            
            if mode == "adv_code" and ai_commands:
                allowed_cmds = []
                for cmd in ai_commands:
                    if has_forbidden_syntax(cmd):
                        refuse_reason = lang_text["adv_code_rejected_syntax"].format(cmd)
                        user_refuse_reasons.append(refuse_reason)
                        if log_info:
                            log_info(f"Adv_code mode rejected command with forbidden syntax: {cmd}", current_session_id)
                        console.print(f"⚠️ {refuse_reason}", style="bold yellow")
                    else:
                        allowed_cmds.append(cmd)
                ai_commands = allowed_cmds
                if not ai_commands and original_cmd_count > 0:
                    console.print(lang_text["adv_code_all_rejected"], style="bold yellow")
                             
            save_ai_commands(user_home_dir, ai_commands)

            # 过滤自然语言：字母数字占比 < 10% 的"命令"弹确认框
            filtered_commands = []
            for cmd in ai_commands:
                alpha_num = sum(1 for c in cmd if c.isascii() and (c.isalpha() or c.isdigit()))
                ratio = alpha_num / max(len(cmd), 1)
                if ratio < 0.1:
                    warn = _mcp_t(
                        f"⚠️ 疑似非命令文本（字母/数字占比 {ratio:.0%}）：\n  {cmd[:120]}",
                        f"⚠️ Possibly not a command (alphanum ratio {ratio:.0%}):\n  {cmd[:120]}"
                    )
                    console.print(warn, style="bold yellow")
                    try:
                        confirm = input(_mcp_t("  确认执行？(y/N): ", "  Execute anyway? (y/N): ")).strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        confirm = 'n'
                    if confirm != 'y':
                        console.print(_mcp_t("  已跳过", "  Skipped"), style="dim")
                        continue
                filtered_commands.append(cmd)
            ai_commands = filtered_commands

            console.print(lang_text["cmd_exec_enabled"], style="bold cyan")

            total_commands = len(ai_commands)
            for idx, cmd in enumerate(ai_commands, 1):
                console.print("\n" + lang_text["cmd_exec_item"].format(idx, cmd), style="bold cyan")
                
                cmd_start_time = time.time()
                cmd_output = []
                cmd_request_id = str(uuid.uuid4())
                
                try:
                    cmd_parts_cmd = shlex.split(cmd)
                    is_tool_cmd = False
                    
                    if cmd_parts_cmd and get_cached_cmd:
                        cache_result = get_cached_cmd(cmd_parts_cmd[0].lower())
                        cmd_type, _ = cache_result
                        is_tool_cmd = (cmd_type == "tools")
                    
                    # ── 风暴检测：重复成功防护 ──
                    _cmd_key = f"cmd:{cmd}"
                    _repeat_count = _repeat_success.get(_cmd_key, 0)
                    if _repeat_count >= 2:
                        _storm_note = _mcp_t(
                            f"⚠️ 拦截重复命令「{cmd}」— 已成功执行 {_repeat_count} 次，跳过",
                            f"⚠️ Duplicate command blocked「{cmd}」— succeeded {_repeat_count}x, skipped"
                        )
                        console.print(f"  [bold yellow]{_storm_note}[/]")
                        cmd_output.append(f"[BLOCKED] 重复命令，已跳过")
                        cmd_results[cmd] = f"Blocked: repeated {_repeat_count}x"
                        continue  # 跳到下一条命令

                    captured_output = ""
                    _output_line_count = 0
                    with capture_command_output() as (stdout_catcher, stderr_catcher):
                        stdout_catcher._ai_triggered = True  # AI 执行 → 限制显示
                        # 标记为 AI 执行模式（exe.py 据此启用超时弹窗）
                        _exe_module = sys.modules.get('lib.terminal.exe')
                        if _exe_module:
                            _exe_module.AI_EXECUTION_MODE = True
                        try:
                            if parse_and_execute:
                                parse_and_execute(cmd)
                        finally:
                            if _exe_module:
                                _exe_module.AI_EXECUTION_MODE = False
                        
                        full_output = stdout_catcher.get_output() + "\n" + stderr_catcher.get_output()
                        captured_output = full_output.strip()
                        _output_line_count = stdout_catcher._line_count
                    
                    # ── 在 capture 外面显示输出（capture 内 sys.stdout 被替换了）──
                    if captured_output:
                        if _output_line_count <= 10:
                            console.print(captured_output, style="dim white")
                        else:
                            trunc_note = _mcp_t(
                                f'… 以下省略 {_output_line_count - 10} 行（完整输出已保留）',
                                f'… {_output_line_count - 10} more lines omitted (full output retained)'
                            )
                            console.print(f"[dim]{trunc_note}[/]")
                        
                        if is_tool_cmd:
                            cached_tool_output = AI_TOOL_OUTPUT_CACHE.get(cmd_request_id)
                            if captured_output:
                                cmd_output.append(captured_output)
                            elif cached_tool_output:
                                cmd_output.append(lang_text["tool_output_cache"].format(cached_tool_output))
                                AI_TOOL_OUTPUT_CACHE.pop(cmd_request_id, None)
                            else:
                                cmd_output.append(lang_text["no_output"])
                        else:
                            if captured_output:
                                cmd_output.append(captured_output)
                            else:
                                cmd_output.append(lang_text["no_output"])
                
                except KeyboardInterrupt:
                    cmd_output.append(lang_text["command_interrupted"])
                    console.print("\n^C", style="bold yellow")
                    if log_error:
                        log_error(f"Command interrupted: {cmd}", current_session_id)
                except Exception as e:
                    error_msg = lang_text["command_error"].format(str(e))
                    cmd_output.append(error_msg)
                    console.print(error_msg, style="bold red")
                    if log_error:
                        log_error(f"Command execution failed: {cmd} -> {str(e)}", current_session_id)
                
                cmd_exec_time = time.time() - cmd_start_time
                
                if cmd_output:
                    time_label = lang_text.get("execution_time", "Execution time" if current_lang == "english" else "执行时间")
                    output_label = lang_text.get("output_content", "Output content" if current_lang == "english" else "输出内容")
                    cmd_result = f"{time_label}: {cmd_exec_time:.2f} seconds\n{output_label}:\n{''.join(cmd_output)}"
                else:
                    time_label = lang_text.get("execution_time", "Execution time" if current_lang == "english" else "执行时间")
                    output_label = lang_text.get("output_content", "Output content" if current_lang == "english" else "输出内容")
                    no_output_detail = "Command executed successfully, but no output returned" if current_lang == "english" else "命令执行成功，但未返回任何输出"
                    cmd_result = f"{time_label}: {cmd_exec_time:.2f} seconds\n{output_label}: {no_output_detail}"
                
                cmd_results[cmd] = cmd_result
            
                # ── 风暴检测：记录命令执行结果 ──
                _cmd_key = f"cmd:{cmd}"
                if "失败" in cmd_result or "error" in cmd_result.lower() or "exception" in cmd_result.lower():
                    _storm_counter[_cmd_key] = _storm_counter.get(_cmd_key, 0) + 1
                    _repeat_success.pop(_cmd_key, None)
                    if _storm_counter[_cmd_key] >= 3:
                        _storm_warn = _mcp_t(
                            f"⚠️ 风暴检测：命令「{cmd}」连续失败 {_storm_counter[_cmd_key]} 次，AI 应更换策略",
                            f"⚠️ Storm detected: cmd「{cmd}」failed {_storm_counter[_cmd_key]}x, AI should switch strategy"
                        )
                        console.print(f"  [bold red]{_storm_warn}[/]")
                        conversation_history.append({"role": "system", "content": f"[STORM_WARNING] {_storm_warn}"})
                else:
                    _storm_counter.pop(_cmd_key, None)
                    _repeat_success[_cmd_key] = _repeat_success.get(_cmd_key, 0) + 1
            
            # 标记本轮已处理的命令（基于实际执行结果）
            _commands_processed_this_round = bool(cmd_results)
            
            # ── 命令执行结果立即喂回给 AI ──
            if cmd_results:
                _cmd_feedback_lines = []
                for _cmd, _result in cmd_results.items():
                    _cmd_feedback_lines.append(f"$ {_cmd}\n{_result}")
                _cmd_feedback = "\n\n".join(_cmd_feedback_lines)
                conversation_history.append({"role": "system", "content": f"[CMD_RESULT]\n{_cmd_feedback}"})
            
            if not ai_ask.strip():
                final_ai_result = ai_result.copy()
                if user_refuse_reasons:
                    refuse_summary = lang_text["user_refused_cmds"] + "\n".join(user_refuse_reasons)
                    if "txt" in final_ai_result:
                        final_ai_result["txt"] = (final_ai_result.get("txt") or "") + refuse_summary
                    else:
                        final_ai_result["txt"] = refuse_summary
                
                if interaction_count == 1:
                    record_ai_session(user_home_dir, current_session_id, initial_question, final_ai_result, "", cmd_results, referenced_memory_uuid or "", native_results=_native_call_log_text)
                else:
                    existing_content, record_path = get_latest_ai_session(user_home_dir, current_session_id)
                    if existing_content and record_path:
                        _ts = time.strftime('%Y-%m-%d %H:%M:%S')
                        _md = current_lang == "english"
                        new_content = f"\n\n### {'Interaction' if _md else '交互'} #{interaction_count} ({_ts})\n\n"
                        # 记录本轮的用户提问（对话历史中最后一个 user 消息）
                        _last_user_q = ""
                        for _m in reversed(conversation_history):
                            if _m.get("role") == "user":
                                _last_user_q = _m.get("content", "")
                                break
                        if _last_user_q:
                            new_content += f"- **{'User' if _md else '用户'}**: {_last_user_q[:200]}{'...' if len(_last_user_q) > 200 else ''}\n"
                        _resp = (final_ai_result.get('txt', '') or '').strip()
                        if _resp:
                            new_content += f"- **{'AI Response' if _md else 'AI回答'}**:\n  {_resp}\n"
                        if ai_commands:
                            new_content += f"- **{'Commands' if _md else '命令'}**:\n"
                            for idx_cmd, cmd in enumerate(ai_commands, 1):
                                cmd_result_val = cmd_results.get(cmd, "Not executed or execution failed" if _md else "未执行或执行失败")
                                new_content += f"  {idx_cmd}. `{cmd}`\n"
                                new_content += f"  - {'Output' if _md else '输出'}: {cmd_result_val[:200]}{'...' if len(cmd_result_val) > 200 else ''}\n"
                        try:
                            with open(record_path, "a", encoding="utf-8") as f:
                                f.write(new_content)
                        except Exception:
                            pass
        else:
            if not ai_ask.strip():
                final_ai_result = ai_result.copy()
                if user_refuse_reasons:
                    refuse_summary = lang_text["user_refused_cmds"] + "\n".join(user_refuse_reasons)
                    if "txt" in final_ai_result:
                        final_ai_result["txt"] = (final_ai_result.get("txt") or "") + refuse_summary
                    else:
                        final_ai_result["txt"] = refuse_summary
                
                if interaction_count == 1:
                    record_ai_session(user_home_dir, current_session_id, initial_question, final_ai_result, "", {}, referenced_memory_uuid or "", native_results=_native_call_log_text)
                else:
                    existing_content, record_path = get_latest_ai_session(user_home_dir, current_session_id)
                    if existing_content and record_path:
                        _ts = time.strftime('%Y-%m-%d %H:%M:%S')
                        _md = current_lang == "english"
                        new_content = f"\n\n### {'Interaction' if _md else '交互'} #{interaction_count} ({_ts})\n\n"
                        # 记录本轮的用户提问（对话历史中最后一个 user 消息）
                        _last_user_q = ""
                        for _m in reversed(conversation_history):
                            if _m.get("role") == "user":
                                _last_user_q = _m.get("content", "")
                                break
                        if _last_user_q:
                            new_content += f"- **{'User' if _md else '用户'}**: {_last_user_q[:200]}{'...' if len(_last_user_q) > 200 else ''}\n"
                        _resp = (final_ai_result.get('txt', '') or '').strip()
                        if _resp:
                            new_content += f"- **{'AI Response' if _md else 'AI回答'}**:\n  {_resp}\n"
                        try:
                            with open(record_path, "a", encoding="utf-8") as f:
                                f.write(new_content)
                        except Exception:
                            pass
        
        if not ai_ask.strip():
            if tag:
                update_message_tag(user_home_dir, current_chat_name, current_session_id, tag, class_level)
            # answer=yes → AI 主动表示完成；answer=no → AI 认为还需继续
            # 但 answer 是可选信号，有挂起项时优先处理挂起项

        # Debug 面板：debug 模式下用 dim Panel 展示 SSE 原始响应
        debug_info = ai_result.get("_debug", "")
        if debug_info and debug_info.strip():
            from rich.panel import Panel as DebugPanel
            from rich.box import ROUNDED as DebugBox
            console.print(DebugPanel(
                debug_info.strip(),
                title="🔧 Debug",
                border_style="dim",
                box=DebugBox,
            ))
        
        # ── 自动判断是否继续循环（不再依赖 AI 的 [ANSWER] 标记）──
        # 规则：仅当响应中只有 txt/analysis 纯文本字段时才停止循环；
        #       但凡存在其他字段（memory/plan/ask/commands/本轮新工具调用），
        #       都需要回问 AI 以传递上下文反馈。
        # 注意：markup_blocks 本身不能作为 pending 判断——AI 文本回复中可能提到
        # "[VIEW:]" 等词导致解析器误提取。必须用实际执行结果 _native_call_log_text 来判断。
        has_pending = bool(
            _native_call_log_text.strip() or
            memory_uuid or
            _commands_processed_this_round or
            _tool_calls_processed_this_round or
            ai_ask.strip() or
            plan_text.strip()
        )

        if has_pending and not was_interrupted:
            # 有待执行项 → 自动继续下一轮
            # 但如果被 ESC 中断过，不自动循环，把控制权交还给用户
            continue_asking = True
        elif _in_repl:
            # REPL 模式 → 直接退出，由外层 REPL 接管
            continue_asking = False
        elif answer == "yes":
            # AI 主动表示完成 → 显示 ESC 门控（Enter 退出 / ESC 追问）
            # ── 显示 token 量 ──
            _pt = getattr(_thread_locals, "last_prompt_tokens", 0)
            if _pt:
                console.print(f"  [dim]📊 上下文 ~{_pt} tokens（API 精确值）[/]")
            elif conversation_history:
                _total_chars = sum(len(m.get("content", "") or "") for m in conversation_history)
                _est_tokens = _total_chars // 3 + 1500
                console.print(f"  [dim]📊 上下文 ~{_est_tokens} tokens（估算）[/]")
            continue_asking = False
            esc_pressed = [False]
            kb_esc = KeyBindings()

            @kb_esc.add('escape')
            def on_esc(event):
                esc_pressed[0] = True
                event.app.exit(result='')

            hint = lang_text.get("esc_hint",
                "Press ESC to ask, Enter to exit") if current_lang == "chinese" else \
                lang_text.get("esc_hint", "Press ESC to ask, Enter to exit")
            try:
                follow_up = prompt(
                    [('class:dim', hint + ' ')],
                    key_bindings=kb_esc,
                    style=PromptStyle.from_dict({'dim': 'dim'}),
                ).strip()
            except (KeyboardInterrupt, EOFError):
                console.print()
                console.print(lang_text.get("user_exit",
                    "Goodbye!" if current_lang == "english" else "再见！"), style="dim")
                continue

            if esc_pressed[0]:
                console.print()
                console.print(lang_text.get("esc_ask",
                    "Any questions?" if current_lang == "english" else "有什么问题吗？"), style="dim")
                try:
                    follow_up = prompt("> ").strip()
                except (KeyboardInterrupt, EOFError):
                    console.print()
                    console.print(lang_text.get("user_exit",
                        "Goodbye!" if current_lang == "english" else "再见！"), style="dim")
                    continue

                if follow_up:
                    last_user_question = follow_up
                    message_appended = False
                    current_question = follow_up
                    conversation_history.append({"role": "user", "content": follow_up})
                    continue_asking = True
        else:
            # answer=no 且无待执行 → AI 想继续，自动循环，不显示 ESC 门控
            _pt = getattr(_thread_locals, "last_prompt_tokens", 0)
            if _pt:
                console.print(f"  [dim]📊 上下文 ~{_pt} tokens（API 精确值）[/]")
            elif conversation_history:
                _total_chars = sum(len(m.get("content", "") or "") for m in conversation_history)
                _est_tokens = _total_chars // 3 + 1500
                console.print(f"  [dim]📊 上下文 ~{_est_tokens} tokens（估算）[/]")
            continue_asking = True

    # 恢复原始 SIGINT 处理器
    import signal as _signal
    _signal.signal(_signal.SIGINT, _original_sigint)
    cleanup_output_cache()
