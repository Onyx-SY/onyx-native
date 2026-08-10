"""core/i18n.py — 统一双语管理模块

从 etc/lang/{language}.json 加载消息，提供 t() 查找 API。

用法:
    from core.i18n import t, set_lang, get_lang

    set_lang("chinese")
    msg = t("cd_handler.usage")                     # → "用法：cd [选项]... [目录]"
    msg = t("cd_handler.no_such_file", path="/tmp") # → "cd: /tmp: 没有那个文件或目录"
"""

import os
import json
from typing import Dict, Optional

# 项目根目录
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_LANG_DIR = os.path.join(_ROOT, "etc", "lang")

# 缓存: {语言代码: {section.key: 消息模板}}
_cache: Dict[str, Dict[str, str]] = {}

# 当前语言
_current_lang: str = "chinese"


def _load_lang(lang: str) -> Dict[str, str]:
    """加载语言 JSON 文件并展平为 {section.key: message} 字典"""
    if lang in _cache:
        return _cache[lang]

    path = os.path.join(_LANG_DIR, f"{lang}.json")
    if not os.path.exists(path):
        # fallback 到中文
        path = os.path.join(_LANG_DIR, "chinese.json")

    flat: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for section, messages in data.items():
            if isinstance(messages, dict):
                for key, value in messages.items():
                    flat[f"{section}.{key}"] = str(value)
    except Exception:
        pass

    _cache[lang] = flat
    return flat


def t(key: str, **kwargs) -> str:
    """获取本地化消息。key 格式: "section.message_key"

    示例:
        t("cd_handler.usage")
        t("cd_handler.no_such_file", path="/tmp")
    """
    msgs = _load_lang(_current_lang)
    template = msgs.get(key, key)  # fallback 到 key 本身

    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError):
            return template
    return template


def set_lang(lang: str) -> None:
    """设置当前语言"""
    global _current_lang
    lang = lang.lower()
    if lang in ("chinese", "english", "zh", "cn"):
        _current_lang = "chinese" if lang in ("zh", "cn") else lang
    elif lang in ("en",):
        _current_lang = "english"
    else:
        _current_lang = "chinese"


def get_lang() -> str:
    """获取当前语言代码"""
    return _current_lang


def preload() -> None:
    """预加载所有语言文件（启动时调用）"""
    for lang in ("chinese", "english"):
        _load_lang(lang)
