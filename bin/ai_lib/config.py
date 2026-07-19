# -*- coding: utf-8 -*-
"""
Onyx AI 配置模块 — 路径常量、模型列表、API 密钥、情感模拟、语言、prompt 文本

从 bin/ai_cmd.py 提取（原 1-542 行），零功能变更。
"""

import os
import sys
import json
import time
import base64
import requests
from typing import Dict, List, Optional, Any, Callable, Tuple

from rich.console import Console
console = Console()

from .ui import select_option, text_input as ui_text_input

# ── 核心路径配置 ──
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
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

# ──────────────────── AI 模型列表 ────────────────────
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
_KEY_OBFUSCATE_PREFIX = "~"

def _obfuscate(plain: str) -> str:
    """简单 XOR + base64 混淆，返回带前缀的编码字符串"""
    key = 0xA7
    data = plain.encode("utf-8")
    xored = bytes(b ^ key for b in data)
    return _KEY_OBFUSCATE_PREFIX + base64.b64encode(xored).decode()

def _deobfuscate(encoded: str) -> str:
    """解码混淆字符串，若无前缀则视为明文（向后兼容）"""
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

# -------------------------- 许可证验证 --------------------------
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
