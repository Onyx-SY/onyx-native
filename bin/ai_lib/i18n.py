#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
I18n 国际化模块 — 免费/开源

为 Onyx AI 系统提供双语（中/英）文本加载与查询。

架构:
  翻译数据存储在 lang.json 中，按 语言→键名 组织。
  支持 {placeholder} 格式化。
  单例模式，全局只加载一次，延迟加载。

用法:
  from bin.ai_lib.i18n import I18n
  i18n = I18n.get_instance()
  print(i18n.t("welcome", "chinese"))
  print(i18n.t("model_switched", "english", model="gpt-4"))

  快速函数（推荐）:
  from bin.ai_lib.i18n import _
  _("bye", "chinese")                    # "👋 退出 AI 模式"
  _("model_switched", "en", model="gpt") # "✅ Switched to gpt"
"""

import os
import json
import threading
from typing import Optional, Dict


# ──────────────────────────── 路径 ─────────────────────────────

def _lang_json_path() -> str:
    """返回 lang.json 的绝对路径（相对于本文件）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang.json")


# ──────────────────────────── 语言代码标准化 ───────────────────

_LANG_ALIASES = {
    "zh": "chinese", "cn": "chinese", "zh-cn": "chinese", "zh-hans": "chinese",
    "en": "english", "en-us": "english", "en-gb": "english",
}
_DEFAULT_LANG = "chinese"


def _normalize(lang: str) -> str:
    """标准化语言代码，'en'/'zh' → 'chinese'/'english'"""
    lowered = lang.strip().lower()
    return _LANG_ALIASES.get(lowered, lowered)


# ──────────────────────────── I18n 类 ─────────────────────────

class I18n:
    """国际化文本查询类 — 单例、延迟加载。"""

    _instance: Optional["I18n"] = None
    _instance_lock = threading.Lock()

    def __init__(self, json_path: Optional[str] = None):
        """
        参数:
          json_path: lang.json 路径。为 None 时使用默认路径。
        """
        self._json_path = json_path or _lang_json_path()
        self._data: Dict[str, Dict[str, str]] = {}
        self._loaded = False
        self._lock = threading.Lock()

    # ── 单例 ──

    @classmethod
    def get_instance(cls, json_path: Optional[str] = None) -> "I18n":
        """获取全局单例。首次调用时创建。"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(json_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（主要用于测试）。"""
        with cls._instance_lock:
            cls._instance = None

    # ── 加载 ──

    def load(self, json_path: Optional[str] = None) -> None:
        """
        从 JSON 文件加载翻译数据。可指定新路径，否则使用初始化时路径。
        线程安全，多次调用只加载一次。
        """
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            path = json_path or self._json_path
            if not os.path.exists(path):
                self._data = {_DEFAULT_LANG: {}}
                self._loaded = True
                return
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                # 只取 language → key → value 部分
                self._data = {
                    k: v for k, v in raw.items()
                    if isinstance(v, dict) and k != "_comment" and k != "_schema"
                }
            except (json.JSONDecodeError, IOError, Exception):
                self._data = {_DEFAULT_LANG: {}}
            self._loaded = True

    def reload(self, json_path: Optional[str] = None) -> None:
        """强制重新加载（用于热更新翻译）。"""
        with self._lock:
            self._loaded = False
        self.load(json_path)

    # ── 查询 ──

    def t(self, key: str, lang: str = _DEFAULT_LANG, **fmt) -> str:
        """
        获取翻译文本。

        参数:
          key:  文本键名
          lang: 语言代码（'chinese' / 'english' 或 'zh' / 'en'）
          **fmt: 用于 {placeholder} 格式化的值

        返回:
          翻译文本，键不存在时返回 key 本身。
        """
        self.load()
        lang = _normalize(lang)
        lang_data = self._data.get(lang) or self._data.get(_DEFAULT_LANG, {})
        text = lang_data.get(key, key)
        if fmt:
            try:
                text = text.format(**fmt)
            except (KeyError, IndexError):
                pass
        return text

    def has_key(self, key: str, lang: Optional[str] = None) -> bool:
        """检查某个语言的某个键是否存在。lang=None 时检查所有语言。"""
        self.load()
        if lang:
            lang_data = self._data.get(_normalize(lang), {})
            return key in lang_data
        return any(key in ld for ld in self._data.values())

    @property
    def available_languages(self) -> list:
        """返回可用语言列表。"""
        self.load()
        return list(self._data.keys())

    @property
    def key_count(self) -> Dict[str, int]:
        """返回每种语言的键数量。"""
        self.load()
        return {lang: len(keys) for lang, keys in self._data.items()}


# ──────────────────────────── 快速函数 ────────────────────────

_default_i18n = None
_default_i18n_lock = threading.Lock()


def _(key: str, lang: str = _DEFAULT_LANG, **fmt) -> str:
    """
    快速翻译函数 — 等价于 I18n.get_instance().t(key, lang, **fmt)。

    用法:
      from bin.ai_lib.i18n import _
      _("bye")                             # 中文
      _("bye", "en")                        # 英文
      _("model_switched", "en", model="gpt") # 带格式化
    """
    global _default_i18n
    if _default_i18n is None:
        with _default_i18n_lock:
            if _default_i18n is None:
                _default_i18n = I18n.get_instance()
    return _default_i18n.t(key, lang, **fmt)
