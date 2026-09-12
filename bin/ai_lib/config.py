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
from typing import Dict, List, Optional, Any, Callable, Tuple

from rich.console import Console
console = Console()

from .ui import select_option, text_input as ui_text_input

# ── 核心路径配置 ──
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
USER = os.getlogin() if hasattr(os, "getlogin") else os.getenv("USER", "default")
# 运行时 $HOME 优先（Onyx 沙箱开=虚拟 home / 关=OS 真实 home，AI 配置读/写必须跟随同一 home）；
# 缺失时回退到按代码位置推导的静态 home（模块独立运行场景）
_STATIC_USER_HOME_DIR = os.path.join(ROOT_DIR, "root") if USER == "root" else os.path.join(ROOT_DIR, "home", USER)
USER_HOME_DIR = os.environ.get("HOME") or _STATIC_USER_HOME_DIR
LANGUAGE_CONFIG_PATH = os.path.join(USER_HOME_DIR, ".config", "onyx", "language")
help_info_path = os.path.join(ROOT_DIR, "onyx", "bin", "help", "help_info.json")
onyx_config_path = os.path.join(ROOT_DIR, "onyx", "etc", "config.json")
AI_KEY_DIR = os.path.join(USER_HOME_DIR, ".config", "onyx", "ai")
AI_KEY_PATH = os.path.join(AI_KEY_DIR, "key.key")
KEY_CONF_PATH = os.path.join(USER_HOME_DIR, ".config", "onyx", "ai", "key.json")
KEY_CONF_LEGACY_PATH = os.path.join(USER_HOME_DIR, ".config", "onyx", "ai", "key.conf")
MOOD_PATH = os.path.join(USER_HOME_DIR, ".ai_s", "mood.json")
SERVER_URL_FILE = os.path.join(ROOT_DIR, "onyx", "etc", ".url")


def sync_home(home: Optional[str] = None) -> None:
    """把 AI 配置相关路径常量重绑到运行时用户主目录。

    Onyx 沙箱开：HOME = 虚拟 home；沙箱关（真实根模式）：HOME = OS 真实 home。
    模块常量在 import 时用代码位置/静态用户名推导，可能与运行时 HOME 不一致，
    导致 key.json 等配置“写一个路径、读另一个路径”。AI 会话入口调用本函数后，
    所有 load/save 使用同一份运行时 home 下的文件。
    """
    global USER_HOME_DIR, LANGUAGE_CONFIG_PATH, AI_KEY_DIR
    global AI_KEY_PATH, KEY_CONF_PATH, KEY_CONF_LEGACY_PATH, MOOD_PATH
    home = home or os.environ.get("HOME") or USER_HOME_DIR
    if not home:
        return
    USER_HOME_DIR = home
    LANGUAGE_CONFIG_PATH = os.path.join(home, ".config", "onyx", "language")
    AI_KEY_DIR = os.path.join(home, ".config", "onyx", "ai")
    AI_KEY_PATH = os.path.join(AI_KEY_DIR, "key.key")
    KEY_CONF_PATH = os.path.join(AI_KEY_DIR, "key.json")
    KEY_CONF_LEGACY_PATH = os.path.join(AI_KEY_DIR, "key.conf")
    MOOD_PATH = os.path.join(home, ".ai_s", "mood.json")


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
            "supports_prompt_cache": True,
            "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
            "protocols": {"deepseek-v4-pro": "openai", "deepseek-v4-flash": "openai"},
            "default_model": "deepseek-v4-pro",
            "params": {"temperature": 0.1, "top_p": 0.2, "max_tokens": 8192},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "price_per_million_tokens": {
                "deepseek-v4-pro": {"input": 0.435, "output": 0.87},
                "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
            },
        },
        "openai": {
            "name": "OpenAI",
            "api_url": "https://api.openai.com/v1/chat/completions",
            "stream_format": "openai",
            "supports_prompt_cache": True,
            "models": ["gpt-5.5", "gpt-5.5-instant", "gpt-5.5-pro",
                       "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
            "protocols": {"gpt-5.5": "openai", "gpt-5.5-instant": "openai", "gpt-5.5-pro": "openai",
                          "gpt-5.6-sol": "openai", "gpt-5.6-terra": "openai", "gpt-5.6-luna": "openai"},
            "default_model": "gpt-5.6-terra",
            "params": {"temperature": 0.1, "top_p": 0.2, "max_tokens": 8192},
            "price_per_million_tokens": {
                "gpt-5.5-instant": {"input": 3.0, "output": 12.0},
                "gpt-5.5": {"input": 5.0, "output": 18.0},
                "gpt-5.5-pro": {"input": 12.5, "output": 75.0},
                "gpt-5.6-sol": {"input": 5.0, "output": 30.0},
                "gpt-5.6-terra": {"input": 2.0, "output": 12.0},
                "gpt-5.6-luna": {"input": 0.2, "output": 1.2},
            },
        },
        "anthropic": {
            "name": "Anthropic",
            "api_url": "https://api.anthropic.com/v1/messages",
            "stream_format": "anthropic",
            "supports_prompt_cache": True,
            "models": [
                "claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-sonnet-4",
                "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5",
                "claude-haiku-4-5", "claude-fable-5", "claude-mythos-5",
            ],
            "protocols": {m: "anthropic" for m in [
                "claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-sonnet-4",
                "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5",
                "claude-haiku-4-5", "claude-fable-5", "claude-mythos-5",
            ]},
            "default_model": "claude-sonnet-5",
            "params": {"temperature": 0.1, "top_p": 0.2, "max_tokens": 8192},
            "price_per_million_tokens": {
                "claude-sonnet-5": {"input": 2.0, "output": 10.0},
                "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
                "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
                "claude-sonnet-4": {"input": 3.0, "output": 15.0},
                "claude-opus-5": {"input": 5.0, "output": 25.0},
                "claude-opus-4-8": {"input": 5.0, "output": 25.0},
                "claude-opus-4-7": {"input": 5.0, "output": 25.0},
                "claude-opus-4-6": {"input": 5.0, "output": 25.0},
                "claude-opus-4-5": {"input": 5.0, "output": 25.0},
                "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
                "claude-fable-5": {"input": 10.0, "output": 50.0},
                "claude-mythos-5": {"input": 12.0, "output": 60.0},
            },
        },

        "zen": {
            "name": "OpenCode Zen",
            "api_url": "https://opencode.ai/zen/v1/chat/completions",
            "stream_format": "openai",
            "protocol_api_urls": {
                "openai": "https://opencode.ai/zen/v1/chat/completions",
                "anthropic": "https://opencode.ai/zen/v1/messages",
                "openai_responses": "https://opencode.ai/zen/v1/responses",
                "google": "https://opencode.ai/zen/v1/models/{model}:streamGenerateContent?alt=sse",
            },
            "supports_prompt_cache": False,
            "models": [
                "deepseek-v4-pro",
                "deepseek-v4-flash",
                "glm-5.2",
                "glm-5.1",
                "glm-5",
                "minimax-m3",
                "minimax-m2.7",
                "minimax-m2.5",
                "kimi-k3",
                "kimi-k2.7-code",
                "kimi-k2.6",
                "kimi-k2.5",
                "big-pickle",
                "mimo-v2.5-free",
                "ling-3.0-flash-fin-free",
                "nemotron-3-ultra-free",
                "nemotron-3.5-lightning-free",
                "laguna-s-2.1-free",
                "claude-sonnet-5",
                "claude-sonnet-4-6",
                "claude-sonnet-4-5",
                "claude-sonnet-4",
                "claude-opus-5",
                "claude-opus-4-8",
                "claude-opus-4-7",
                "claude-opus-4-6",
                "claude-opus-4-5",
                "claude-haiku-4-5",
                "claude-fable-5",
                "qwen3.6-plus",
                "qwen3.5-plus",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.5",
                "gpt-5.5-pro",
                "gpt-5.4",
                "gpt-5.4-pro",
                "gpt-5.4-mini",
                "gpt-5.4-nano",
                "gpt-5.3-codex-spark",
                "gpt-5.3-codex",
                "gpt-5.2",
                "gpt-5.2-codex",
                "gpt-5.1",
                "gpt-5.1-codex-max",
                "gpt-5.1-codex",
                "gpt-5.1-codex-mini",
                "gpt-5",
                "gpt-5-codex",
                "gpt-5-nano",
                "grok-build-0.1",
                "grok-4.6",
                "grok-4.5",
                "muse-spark-1.2",
                "muse-spark-1.2-contributor-free",
                "gemini-3.7-flash",
                "gemini-3.6-flash",
                "gemini-3.5-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-pro",
                "gemini-3-flash",
            ],
            "protocols": {
                "deepseek-v4-pro": "openai",
                "deepseek-v4-flash": "openai",
                "glm-5.2": "openai",
                "glm-5.1": "openai",
                "glm-5": "openai",
                "minimax-m3": "openai",
                "minimax-m2.7": "openai",
                "minimax-m2.5": "openai",
                "kimi-k3": "openai",
                "kimi-k2.7-code": "openai",
                "kimi-k2.6": "openai",
                "kimi-k2.5": "openai",
                "big-pickle": "openai",
                "mimo-v2.5-free": "openai",
                "ling-3.0-flash-fin-free": "openai",
                "nemotron-3-ultra-free": "openai",
                "nemotron-3.5-lightning-free": "openai",
                "laguna-s-2.1-free": "openai",
                "claude-sonnet-5": "anthropic",
                "claude-sonnet-4-6": "anthropic",
                "claude-sonnet-4-5": "anthropic",
                "claude-sonnet-4": "anthropic",
                "claude-opus-5": "anthropic",
                "claude-opus-4-8": "anthropic",
                "claude-opus-4-7": "anthropic",
                "claude-opus-4-6": "anthropic",
                "claude-opus-4-5": "anthropic",
                "claude-haiku-4-5": "anthropic",
                "claude-fable-5": "anthropic",
                "qwen3.6-plus": "anthropic",
                "qwen3.5-plus": "anthropic",
                "gpt-5.6-sol": "openai_responses",
                "gpt-5.6-terra": "openai_responses",
                "gpt-5.6-luna": "openai_responses",
                "gpt-5.5": "openai_responses",
                "gpt-5.5-pro": "openai_responses",
                "gpt-5.4": "openai_responses",
                "gpt-5.4-pro": "openai_responses",
                "gpt-5.4-mini": "openai_responses",
                "gpt-5.4-nano": "openai_responses",
                "gpt-5.3-codex-spark": "openai_responses",
                "gpt-5.3-codex": "openai_responses",
                "gpt-5.2": "openai_responses",
                "gpt-5.2-codex": "openai_responses",
                "gpt-5.1": "openai_responses",
                "gpt-5.1-codex-max": "openai_responses",
                "gpt-5.1-codex": "openai_responses",
                "gpt-5.1-codex-mini": "openai_responses",
                "gpt-5": "openai_responses",
                "gpt-5-codex": "openai_responses",
                "gpt-5-nano": "openai_responses",
                "grok-build-0.1": "openai_responses",
                "grok-4.6": "openai_responses",
                "grok-4.5": "openai_responses",
                "muse-spark-1.2": "openai_responses",
                "muse-spark-1.2-contributor-free": "openai_responses",
                "gemini-3.7-flash": "google",
                "gemini-3.6-flash": "google",
                "gemini-3.5-flash": "google",
                "gemini-3.5-flash-lite": "google",
                "gemini-3.1-pro": "google",
                "gemini-3-flash": "google",
            },
            "default_model": "deepseek-v4-pro",
            "params": {"temperature": 0.1, "top_p": 0.2, "max_tokens": 32768},
            "price_per_million_tokens": {
                "deepseek-v4-pro": {"input": 1.32, "output": 3.96},
                "deepseek-v4-flash": {"input": 0.44, "output": 1.32},
                "glm-5.2": {"input": 1.4, "output": 4.4},
                "glm-5.1": {"input": 1.4, "output": 4.4},
                "glm-5": {"input": 1.0, "output": 3.2},
                "minimax-m3": {"input": 0.3, "output": 1.2},
                "minimax-m2.7": {"input": 0.3, "output": 1.2},
                "minimax-m2.5": {"input": 0.3, "output": 1.2},
                "kimi-k3": {"input": 3.0, "output": 15.0},
                "kimi-k2.7-code": {"input": 0.95, "output": 4.0},
                "kimi-k2.6": {"input": 0.95, "output": 4.0},
                "kimi-k2.5": {"input": 0.6, "output": 3.0},
                "big-pickle": {"input": 0.0, "output": 0.0},
                "mimo-v2.5-free": {"input": 0.0, "output": 0.0},
                "ling-3.0-flash-fin-free": {"input": 0.0, "output": 0.0},
                "nemotron-3-ultra-free": {"input": 0.0, "output": 0.0},
                "nemotron-3.5-lightning-free": {"input": 0.0, "output": 0.0},
                "laguna-s-2.1-free": {"input": 0.0, "output": 0.0},
                "claude-sonnet-5": {"input": 2.0, "output": 10.0},
                "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
                "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
                "claude-sonnet-4": {"input": 3.0, "output": 15.0},
                "claude-opus-5": {"input": 5.0, "output": 25.0},
                "claude-opus-4-8": {"input": 5.0, "output": 25.0},
                "claude-opus-4-7": {"input": 5.0, "output": 25.0},
                "claude-opus-4-6": {"input": 5.0, "output": 25.0},
                "claude-opus-4-5": {"input": 5.0, "output": 25.0},
                "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
                "claude-fable-5": {"input": 10.0, "output": 50.0},
                "qwen3.6-plus": {"input": 0.5, "output": 3.0},
                "qwen3.5-plus": {"input": 0.2, "output": 1.2},
                "gpt-5.6-sol": {"input": 2.0, "output": 10.0},
                "gpt-5.6-terra": {"input": 2.0, "output": 12.0},
                "gpt-5.6-luna": {"input": 0.2, "output": 1.2},
                "gpt-5.5": {"input": 5.0, "output": 30.0},
                "gpt-5.5-pro": {"input": 30.0, "output": 180.0},
                "gpt-5.4": {"input": 2.5, "output": 15.0},
                "gpt-5.4-pro": {"input": 30.0, "output": 180.0},
                "gpt-5.4-mini": {"input": 0.75, "output": 4.5},
                "gpt-5.4-nano": {"input": 0.2, "output": 1.25},
                "gpt-5.3-codex-spark": {"input": 1.75, "output": 14.0},
                "gpt-5.3-codex": {"input": 1.75, "output": 14.0},
                "gpt-5.2": {"input": 1.75, "output": 14.0},
                "gpt-5.2-codex": {"input": 1.75, "output": 14.0},
                "gpt-5.1": {"input": 1.07, "output": 8.5},
                "gpt-5.1-codex-max": {"input": 1.25, "output": 10.0},
                "gpt-5.1-codex": {"input": 1.07, "output": 8.5},
                "gpt-5.1-codex-mini": {"input": 0.25, "output": 2.0},
                "gpt-5": {"input": 1.07, "output": 8.5},
                "gpt-5-codex": {"input": 1.07, "output": 8.5},
                "gpt-5-nano": {"input": 0.05, "output": 0.4},
                "grok-build-0.1": {"input": 1.0, "output": 2.0},
                "grok-4.6": {"input": 2.0, "output": 6.0},
                "grok-4.5": {"input": 2.0, "output": 6.0},
                "muse-spark-1.2": {"input": 1.25, "output": 4.25},
                "muse-spark-1.2-contributor-free": {"input": 0.0, "output": 0.0},
                "gemini-3.7-flash": {"input": 1.5, "output": 7.5},
                "gemini-3.6-flash": {"input": 1.5, "output": 7.5},
                "gemini-3.5-flash": {"input": 1.5, "output": 9.0},
                "gemini-3.5-flash-lite": {"input": 0.3, "output": 2.5},
                "gemini-3.1-pro": {"input": 2.0, "output": 12.0},
                "gemini-3-flash": {"input": 0.5, "output": 3.0},
            },
        },
    }

_SUPPORTED_PLATFORMS = _load_ai_models()

# ── 模型别名（opus / sonnet / haiku → 平台具体模型）──
_MODEL_ALIASES: Dict[str, Dict[str, str]] = {
    "anthropic": {
        "opus": "claude-opus-5",
        "sonnet": "claude-sonnet-5",
        "haiku": "claude-haiku-4-5",
    },
}


def resolve_model_alias(platform: str, model: str) -> str:
    """解析模型别名；非别名或未知平台返回原值"""
    if not model:
        return model
    alias_map = _MODEL_ALIASES.get(platform or "", {})
    key = model.strip().lower()
    if key in alias_map:
        return alias_map[key]
    return model


def resolve_model_protocol(platform: str, model: str) -> str:
    """解析模型使用的流式协议：openai / anthropic / openai_responses / google。

    三级回退：
      1. models.json 中该平台 protocols 映射的逐模型标注（zen 等网关平台）
      2. 平台级 stream_format（所有平台都有，是权威字段）
      3. 模型名前缀推断（claude-* → anthropic），未知 → openai
    """
    _KNOWN_PROTOCOLS = ("openai", "anthropic", "openai_responses", "google")
    plat_info = _SUPPORTED_PLATFORMS.get(platform or "", {})
    if model:
        protocols = plat_info.get("protocols") or {}
        if isinstance(protocols, dict) and model in protocols:
            p = protocols.get(model)
            if p in _KNOWN_PROTOCOLS:
                return p
    sf = plat_info.get("stream_format")
    if sf in _KNOWN_PROTOCOLS:
        return sf
    if model and model.strip().lower().startswith("claude-"):
        return "anthropic"
    return "openai"


def is_model_free(platform: str, model: str) -> bool:
    """判断模型是否免费：price_per_million_tokens 中 input/output 均为 0 视为免费。

    未收录价格或非数字时返回 False（保守：不标免费）。
    """
    plat_info = _SUPPORTED_PLATFORMS.get(platform or "", {})
    price_map = plat_info.get("price_per_million_tokens") or {}
    if not isinstance(price_map, dict):
        return False
    price = price_map.get(model)
    if not isinstance(price, dict):
        return False
    try:
        inp = float(price.get("input", 0) or 0)
        outp = float(price.get("output", 0) or 0)
    except (TypeError, ValueError):
        return False
    return inp == 0.0 and outp == 0.0


# ── OpenCode Zen 生态兼容：复用 opencode 已登录的 zen 凭据 ──
def load_zen_key_from_opencode() -> Optional[str]:
    """从 OpenCode 的 auth.json 读取 Zen API Key（`opencode auth login zen` 写入）。

    路径：$XDG_DATA_HOME/opencode/auth.json（默认 ~/.local/share/opencode/auth.json）
    结构：{"zen": {"type": "api", "key": "sk-..."}}
    读取失败/缺失返回 None。
    """
    try:
        data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        auth_path = os.path.join(data_home, "opencode", "auth.json")
        if not os.path.exists(auth_path):
            return None
        with open(auth_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        zen = data.get("zen") or {}
        if isinstance(zen, dict):
            key = zen.get("key")
            if isinstance(key, str) and key.strip():
                return key.strip()
    except Exception:
        pass
    return None


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
    """读取 key.json（旧 key.conf 自动迁移），返回 {platform, api_key, model, params} 或空 dict"""
    path = KEY_CONF_PATH
    if not os.path.exists(path):
        if os.path.exists(KEY_CONF_LEGACY_PATH):
            # 旧版 key.conf → 自动迁移为 key.json（迁移成功才删旧文件）
            try:
                with open(KEY_CONF_LEGACY_PATH, "r", encoding="utf-8") as _f:
                    _data = json.load(_f)
                if isinstance(_data, dict):
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as _f:
                        json.dump(_data, _f, ensure_ascii=False, indent=2)
                    os.chmod(path, 0o600)
                    os.remove(KEY_CONF_LEGACY_PATH)
            except Exception:
                pass
            if not os.path.exists(path):
                path = KEY_CONF_LEGACY_PATH  # 迁移失败，回退旧文件
        else:
            return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
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
    # 迁移完成：删除旧版 key.conf（若仍存在）
    try:
        if os.path.exists(KEY_CONF_LEGACY_PATH):
            os.remove(KEY_CONF_LEGACY_PATH)
    except Exception:
        pass

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

    # 选择模型（标签带协议与免费标记，如 big-pickle (openai) 免费 / claude-sonnet-5 (anthropic)）
    model_labels = []
    model_value = {}
    for _m in info["models"]:
        _label = f"{_m} ({resolve_model_protocol(platform, _m)})"
        if is_model_free(platform, _m):
            _label += " 免费"
        model_labels.append(_label)
        model_value[_label] = _m
    _default_label = f"{info['default_model']} ({resolve_model_protocol(platform, info['default_model'])})"
    if is_model_free(platform, info["default_model"]):
        _default_label += " 免费"
    model_prompt = f"选择模型（默认 {info['default_model']}）" if lang == "chinese" else f"Select model (default: {info['default_model']})"
    model_choice = select_option(model_prompt, model_labels, default=_default_label, lang=lang)
    model = model_value.get(model_choice) or info["default_model"]

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
            _tk_default = params.get("thinking", bool(info.get("thinking", False)))
            tk = input(f"  思考模式 [{'on' if _tk_default else 'off'}]: ").strip().lower()
            if tk in ("on", "true", "1", "yes", "enabled"):
                params["thinking"] = True
            elif tk in ("off", "false", "0", "no", "disabled", "none"):
                params["thinking"] = False
        except (ValueError, KeyboardInterrupt, EOFError):
            pass

    save_key_conf(platform, key, model, params)
    suffix = " (自定义参数)" if tune == "y" else ""
    if lang != "chinese":
        suffix = " (custom params)" if tune == "y" else ""
    console.print(f"✅ {info['name']} — {model}{suffix}", style="bold green")
    return {"platform": platform, "api_key": key, "model": model, "params": params}

def _render_edit_diff(old_text: str, new_text: str, context_lines: int = 2):
    """渲染柔和 diff：淡色 + 行号前 +/- 标记"""
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
                console.print(f"       [dim]... {total - context_lines * 2} 行未变化 ... | {total - context_lines * 2} lines unchanged ...[/]")
            for idx in show_range:
                console.print((f"   {idx + 1:>4} │ {old_lines[idx]}").ljust(_w), style="bright_black")
            if total > context_lines * 2 + 1:
                continue
        elif op == "delete":
            for idx in range(i1, i2):
                console.print((f"  -{idx + 1:>4} │ {old_lines[idx]}").ljust(_w), style="#ff6b6b on #2d0000")
        elif op == "replace":
            for idx in range(i1, i2):
                console.print((f"  -{idx + 1:>4} │ {old_lines[idx]}").ljust(_w), style="#6b9fff on #1a1a3a")
            for idx in range(j1, j2):
                console.print((f"  +{idx + 1:>4} │ {new_lines[idx]}").ljust(_w), style="#ffd700 on #3a3a1a")
        elif op == "insert":
            for idx in range(j1, j2):
                console.print((f"  +{idx + 1:>4} │ {new_lines[idx]}").ljust(_w), style="#6bff6b on #1a3a1a")
    console.print(f"  [dim]────────────────[/]")

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
    import requests  # 延迟导入（仅此函数使用）——启动提速，行为不变
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
