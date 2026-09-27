#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# bin/repl_config.py
"""
config-onyx-repl — 主 REPL 统一配置入口

无参数启动时先询问：以 Web 界面还是 TUI 界面打开。
- Web：从 8000 起自动找空闲端口，用系统默认浏览器打开
- TUI：终端交互式配置

也支持参数式读写（与 manage set 共用同一套 JSON 配置）。
中英双语可切换。
"""

import os
import sys
import json
import time
import socket
import argparse
import threading
import webbrowser
import traceback
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

# ── 路径 ──
CONFIG_DIR        = Path.home() / ".config" / "onyx"
PTK_CONFIG_PATH   = CONFIG_DIR / "ptk.json"          # 主 REPL 配置（颜色/补全/历史/建议）
MAIN_CONFIG_PATH  = CONFIG_DIR / "config.json"       # 主配置（language 等）
REPL_CONFIG_JSON  = Path(__file__).resolve().parent.parent / "etc" / "config-onyx-repl.json"

# 全局：UI 起始端口
DEFAULT_WEB_PORT = 8000
MAX_PORT_TRIES   = 100


# ═══════════════════════════════════════════════════════════
# 语言字符串
# ═══════════════════════════════════════════════════════════

STRINGS: Dict[str, Dict[str, str]] = {
    "zh": {
        "title":            "Onyx 主 REPL 配置",
        "choose_mode":      "请选择打开方式：",
        "mode_web":         "  [1] Web 界面（浏览器）",
        "mode_tui":         "  [2] TUI 界面（终端）",
        "mode_prompt":      "请输入 1 或 2（默认 1，直接回车 = Web）：",
        "invalid_choice":   "无效输入，默认使用 Web 界面。",
        "starting_web":     "正在启动 Web 配置界面…",
        "starting_tui":     "正在启动 TUI 配置界面…",
        "port_trying":      "尝试端口 {port} …",
        "port_occupied":    "端口 {port} 被占用，继续尝试下一个。",
        "port_found":       "已找到空闲端口：{port}",
        "no_free_port":     "无法找到空闲端口（已尝试 {start}-{end}）。",
        "server_started":   "Web 服务器已启动：{url}",
        "open_browser":     "正在用默认浏览器打开：{url}",
        "browser_failed":   "无法自动打开浏览器，请手动访问：{url}",
        "web_hint":         "按 Ctrl+C 停止服务器。",
        "stopping":         "正在停止服务器…",
        "stopped":          "服务器已停止。",
        "saved":            "已保存",
        "reset_done":       "已恢复默认值",
        "unknown_cmd":      "未知子命令：{cmd}",
        "unknown_item":     "未知配置项：{item}",
        "invalid_value":    "值不合法：{value}",
        "current_lang":     "当前语言：{lang}",
        "lang_set":         "语言已切换为：{lang}",
        "need_item":        "请提供配置项名。",
        "need_item_value":  "请提供配置项名和值。",
        "list_header":      "配置项列表（* 表示已改动）：",
        "tui_hint":         "↑↓ 选择 · Enter 修改 · r 恢复该项 · R 恢复全部 · q 退出",
        "tui_item_prompt":  "新值（回车=不变）：",
        "tui_reset_all":    "已恢复全部默认值。",
        "tui_quit":         "已退出。",
        "sect_main":        "通用配置",
        "sect_repl":        "主 REPL 配置",
        "sect_colors":      "配色",
        "sect_keys":        "键位",
        "sect_completion":  "补全行为",
        "sect_history":     "历史记录",
        "sect_suggest":     "虚影建议",
        "btn_save":         "保存",
        "btn_reset":        "恢复默认",
        "btn_back":         "返回",
        "lang_switch":      "English",
        "web_title":        "Onyx 主 REPL 配置",
        "web_saved_msg":    "✓ 已保存",
        "web_reset_msg":    "✓ 已恢复默认",
        "web_item":         "配置项",
        "web_value":        "当前值",
        "web_action":       "操作",
        "changed_mark":     "（已改动）",
    },
    "en": {
        "title":            "Onyx Main REPL Configuration",
        "choose_mode":      "How do you want to open?",
        "mode_web":         "  [1] Web UI (browser)",
        "mode_tui":         "  [2] TUI (terminal)",
        "mode_prompt":      "Enter 1 or 2 (default 1, just press Enter = Web): ",
        "invalid_choice":   "Invalid input, defaulting to Web UI.",
        "starting_web":     "Starting Web config UI...",
        "starting_tui":     "Starting TUI config UI...",
        "port_trying":      "Trying port {port} ...",
        "port_occupied":    "Port {port} is occupied, trying next.",
        "port_found":       "Free port found: {port}",
        "no_free_port":     "No free port available (tried {start}-{end}).",
        "server_started":   "Web server started: {url}",
        "open_browser":     "Opening in default browser: {url}",
        "browser_failed":   "Could not open browser automatically. Please visit: {url}",
        "web_hint":         "Press Ctrl+C to stop the server.",
        "stopping":         "Stopping server...",
        "stopped":          "Server stopped.",
        "saved":            "Saved",
        "reset_done":       "Reset to default",
        "unknown_cmd":      "Unknown subcommand: {cmd}",
        "unknown_item":     "Unknown setting: {item}",
        "invalid_value":    "Invalid value: {value}",
        "current_lang":     "Current language: {lang}",
        "lang_set":         "Language switched to: {lang}",
        "need_item":        "Please provide a setting name.",
        "need_item_value":  "Please provide a setting name and a value.",
        "list_header":      "Settings (* = changed):",
        "tui_hint":         "up/down move · Enter edit · r reset item · R reset all · q quit",
        "tui_item_prompt":  "New value (Enter = keep): ",
        "tui_reset_all":    "All settings reset to default.",
        "tui_quit":         "Bye.",
        "sect_main":        "General",
        "sect_repl":        "Main REPL config",
        "sect_colors":      "Colors",
        "sect_keys":        "Key bindings",
        "sect_completion":  "Completion",
        "sect_history":     "History",
        "sect_suggest":     "Auto-suggest",
        "btn_save":         "Save",
        "btn_reset":        "Reset",
        "btn_back":         "Back",
        "lang_switch":      "中文",
        "web_title":        "Onyx Main REPL Configuration",
        "web_saved_msg":    "✓ Saved",
        "web_reset_msg":    "✓ Reset to default",
        "web_item":         "Setting",
        "web_value":        "Value",
        "web_action":       "Action",
        "changed_mark":     " (changed)",
    },
}


def t(lang: str, key: str, **kw) -> str:
    """取本地化文本。"""
    s = STRINGS.get(lang, STRINGS["zh"]).get(key, key)
    if kw:
        try:
            return s.format(**kw)
        except Exception:
            return s
    return s


# ═══════════════════════════════════════════════════════════
# 配置项定义
# ═══════════════════════════════════════════════════════════

# 主配置（config.json）项定义：(键, 类型, 默认值, 中/英说明)
MAIN_SETTINGS: List[Tuple[str, str, Any, str, str]] = [
    ("language",             "choice",  "Chinese",   "界面语言",             "UI language"),
    ("debug-times",          "bool",    False,       "命令耗时输出",         "Command timing"),
    ("debug-parsecmd",       "bool",    False,       "命令解析调试",         "Parse debug"),
    ("clean-log-time",       "int_or_false", 3,      "日志保留天数",         "Log retention (days)"),
    ("adv_danger_cmd_prompt","bool",    True,        "adv 危险命令二次确认", "Adv dangerous cmd confirm"),
    ("mcp",                  "bool",    True,        "MCP 协议工具",         "MCP tools"),
    ("spring-mode",          "bool",    True,        "启动问候语",           "Startup greeting"),
    ("builtin-adv-syntax",   "bool",    True,        "内置命令高级语法",     "Builtin advanced syntax"),
    ("sandbox",              "bool",    True,        "AI 沙箱",              "AI sandbox"),
    ("prompt-style",         "str",     "onyx",      "提示符样式",           "Prompt style"),
    ("history-len",          "int",     1000,        "历史记录条数上限",     "History length"),
]

# 主 REPL（ptk.json）项定义：(点分路径, 类型, 默认值, 中/英说明)
REPL_SETTINGS: List[Tuple[str, str, Any, str, str]] = [
    # completion
    ("completion.show_hidden",         "bool",  True,  "补全显示隐藏文件",       "Show hidden in completion"),
    ("completion.reserve_space_for_menu", "int", 6,    "补全菜单预留行数",       "Completion menu reserved rows"),
    ("completion.complete_while_typing","bool", True,  "输入时自动补全",         "Complete while typing"),
    ("completion.complete_in_thread",  "bool",  True,  "多线程补全",             "Complete in thread"),
    ("completion.max_completions",     "int",   100,   "补全最大候选数",         "Max completions"),
    ("completion.use_dropdown_menu",   "bool",  True,  "使用下拉补全菜单",       "Use dropdown completion menu"),
    # history
    ("history.memory_limit",           "int",   1000,  "内存中历史条数",         "History in memory"),
    ("history.file_limit",             "int",   50000, "历史文件最大行数",       "History file max lines"),
    ("history.file_name",              "str",   ".onyx_history.txt", "历史文件名", "History file name"),
    # auto_suggest
    ("auto_suggest.enabled",           "bool",  True,  "启用虚影补全",           "Enable auto-suggest"),
    ("auto_suggest.strategy",          "str",   "frequency", "虚影策略",        "Auto-suggest strategy"),
]

# 配色（ptk.json 的 colors.*）
COLOR_SETTINGS: List[Tuple[str, str, str, str, str]] = [
    ("colors.completion-menu",                     "bg:#2d2d30 #cccccc", "补全菜单底色",   "Completion menu"),
    ("colors.completion-menu.completion",          "bg:#2d2d30 #aaaaaa", "补全条目",       "Completion item"),
    ("colors.completion-menu.completion.current",  "bg:#007acc #ffffff", "补全当前项",     "Current completion"),
    ("colors.completion-menu.meta",                "bg:#3d3d40 #888888", "补全元信息",     "Completion meta"),
    ("colors.completion-menu.meta.current",        "bg:#007acc #cccccc", "补全元信息当前", "Current meta"),
    ("colors.scrollbar.background",                "bg:#1e1e1e",         "滚动条背景",     "Scrollbar bg"),
    ("colors.scrollbar.button",                    "bg:#555555",         "滚动条滑块",     "Scrollbar thumb"),
    ("colors.bottom-toolbar",                      "bg:#007acc #ffffff", "底部工具栏",     "Bottom toolbar"),
]

# 主 REPL 键位（与 kb.REPL_KEY_ACTIONS 对齐）
KEY_ACTIONS: List[Tuple[str, str, str, str]] = [
    ("history_up",             "up",              "历史上一条",             "History previous"),
    ("history_down",           "down",            "历史下一条",             "History next"),
    ("prefix_history_up",      "escape, up",      "前缀历史上一条",         "Prefix history prev"),
    ("prefix_history_down",    "escape, down",    "前缀历史下一条",         "Prefix history next"),
    ("completion_next",        "tab",             "补全下一项",             "Completion next"),
    ("completion_prev",        "s-tab",           "补全上一项",             "Completion previous"),
    ("completion_page_up",     "pageup",          "补全上翻页",             "Completion page up"),
    ("completion_page_down",   "pagedown",        "补全下翻页",             "Completion page down"),
    ("completion_menu_up",     "c-up",            "补全菜单上",             "Completion menu up"),
    ("completion_menu_down",   "c-down",          "补全菜单下",             "Completion menu down"),
    ("completion_trigger",     "c-space",         "手动触发补全",           "Trigger completion"),
    ("completion_alt_next",    "c-n",             "补全下一项（备用）",     "Completion next (alt)"),
    ("completion_alt_prev",    "c-p",             "补全上一项（备用）",     "Completion prev (alt)"),
    ("completion_lock",        "escape, space",   "切换补全锁定",           "Toggle completion lock"),
    ("clear_screen",           "c-l",             "清屏",                   "Clear screen"),
    ("multiline_editor",       "escape, enter",   "进入全屏多行编辑区",     "Open multi-line editor"),
]

_ALL_MAIN_KEYS = {k for k, _, _, _ in MAIN_SETTINGS}
_ALL_REPL_KEYS = {k for k, _, _, _, _ in REPL_SETTINGS}
_ALL_COLOR_KEYS = {k for k, _, _, _, _ in COLOR_SETTINGS}
_ALL_KEY_ACTIONS = {k for k, _, _, _ in KEY_ACTIONS}


# ═══════════════════════════════════════════════════════════
# 配置读写
# ═══════════════════════════════════════════════════════════

def _ensure_dirs() -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    _ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def load_main_config() -> Dict[str, Any]:
    return _load_json(MAIN_CONFIG_PATH)


def save_main_config(cfg: Dict[str, Any]) -> None:
    _save_json(MAIN_CONFIG_PATH, cfg)


def load_ptk_config() -> Dict[str, Any]:
    return _load_json(PTK_CONFIG_PATH)


def save_ptk_config(cfg: Dict[str, Any]) -> None:
    _save_json(PTK_CONFIG_PATH, cfg)


def get_ui_language() -> str:
    """取当前界面语言：主配置 > 环境变量 > 默认 zh。"""
    cfg = load_main_config()
    lang = str(cfg.get("language", "") or "").strip().lower()
    if lang in ("chinese", "zh", "cn"):
        return "zh"
    if lang in ("english", "en"):
        return "en"
    env = os.environ.get("ONYX_LANG", "").strip().lower()
    if env in ("zh", "cn", "chinese"):
        return "zh"
    if env in ("en", "english"):
        return "en"
    return "zh"


# ── 点分路径读写 ptk.json ──
def _get_by_path(d: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _set_by_path(d: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _del_by_path(d: Dict[str, Any], path: str) -> None:
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        cur = cur[part]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


# ═══════════════════════════════════════════════════════════
# 值解析 / 校验
# ═══════════════════════════════════════════════════════════

def _parse_value(raw: str, vtype: str) -> Tuple[bool, Any]:
    """把用户输入的字符串按 vtype 解析。返回 (ok, value)。"""
    s = raw.strip()
    if vtype == "bool":
        low = s.lower()
        if low in ("true", "1", "yes", "on", "y", "t"):
            return True, True
        if low in ("false", "0", "no", "off", "n", "f"):
            return True, False
        return False, None
    if vtype == "int":
        try:
            return True, int(s)
        except ValueError:
            return False, None
    if vtype == "int_or_false":
        if s.lower() == "false":
            return True, False
        try:
            return True, int(s)
        except ValueError:
            return False, None
    if vtype == "choice":
        low = s.lower()
        if low in ("chinese", "zh", "cn", "中文", "汉语"):
            return True, "Chinese"
        if low in ("english", "en", "英文"):
            return True, "English"
        return False, None
    # str 及其他
    return True, s


def _format_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# ═══════════════════════════════════════════════════════════
# 子命令：list / get / set / reset / lang
# ═══════════════════════════════════════════════════════════

def _iter_all_settings():
    """产出 (组, 键, 类型, 默认值, 中说明, 英说明)。"""
    for k, typ, dflt, zh, en in MAIN_SETTINGS:
        yield ("main", k, typ, dflt, zh, en)
    for k, typ, dflt, zh, en in REPL_SETTINGS:
        yield ("repl", k, typ, dflt, zh, en)
    for k, dflt, zh, en in COLOR_SETTINGS:
        yield ("color", k, "str", dflt, zh, en)
    for k, dflt, zh, en in KEY_ACTIONS:
        yield ("key", k, "str", dflt, zh, en)


def _read_value(group: str, key: str) -> Any:
    if group == "main":
        return load_main_config().get(key)
    if group == "repl":
        return _get_by_path(load_ptk_config(), key)
    if group == "color":
        # color 键是 "colors.xxx" 形式
        return _get_by_path(load_ptk_config(), key)
    if group == "key":
        return _get_by_path(load_ptk_config(), f"key_bindings.{key}")
    return None


def _write_value(group: str, key: str, value: Any) -> None:
    if group == "main":
        cfg = load_main_config()
        cfg[key] = value
        save_main_config(cfg)
    elif group == "repl":
        cfg = load_ptk_config()
        _set_by_path(cfg, key, value)
        save_ptk_config(cfg)
    elif group == "color":
        cfg = load_ptk_config()
        _set_by_path(cfg, key, value)
        save_ptk_config(cfg)
    elif group == "key":
        cfg = load_ptk_config()
        _set_by_path(cfg, f"key_bindings.{key}", value)
        save_ptk_config(cfg)


def _reset_value(group: str, key: str) -> None:
    if group == "main":
        cfg = load_main_config()
        cfg.pop(key, None)
        save_main_config(cfg)
    elif group == "repl":
        cfg = load_ptk_config()
        _del_by_path(cfg, key)
        save_ptk_config(cfg)
    elif group == "color":
        cfg = load_ptk_config()
        _del_by_path(cfg, key)
        save_ptk_config(cfg)
    elif group == "key":
        cfg = load_ptk_config()
        _del_by_path(cfg, f"key_bindings.{key}")
        save_ptk_config(cfg)


def _find_group(key: str) -> Optional[str]:
    if key in _ALL_MAIN_KEYS:
        return "main"
    if key in _ALL_REPL_KEYS:
        return "repl"
    if key in _ALL_COLOR_KEYS:
        return "color"
    if key in _ALL_KEY_ACTIONS:
        return "key"
    return None


def cmd_list(lang: str) -> int:
    print(t(lang, "list_header"))
    print()
    last_group = None
    for group, key, typ, dflt, zh, en in _iter_all_settings():
        if group != last_group:
            name = {
                "main":  t(lang, "sect_main"),
                "repl":  t(lang, "sect_repl"),
                "color": t(lang, "sect_colors"),
                "key":   t(lang, "sect_keys"),
            }.get(group, group)
            print(f"\n── {name} ──")
            last_group = group
        cur = _read_value(group, key)
        if cur is None:
            cur = dflt
        mark = " " if _format_value(cur) == _format_value(dflt) else "*"
        desc = zh if lang == "zh" else en
        print(f"  {mark} {key:<45} = {_format_value(cur):<28} # {desc}")
    print()
    return 0


def cmd_get(lang: str, key: str) -> int:
    group = _find_group(key)
    if group is None:
        print(t(lang, "unknown_item", item=key))
        return 1
    val = _read_value(group, key)
    if val is None:
        # 从默认值里取
        for g, k, _, dflt, _, _ in _iter_all_settings():
            if g == group and k == key:
                val = dflt
                break
    print(_format_value(val))
    return 0


def cmd_set(lang: str, key: str, raw: str) -> int:
    group = _find_group(key)
    if group is None:
        print(t(lang, "unknown_item", item=key))
        return 1

    # 找类型
    vtype = "str"
    for g, k, typ, _, _, _ in _iter_all_settings():
        if g == group and k == key:
            vtype = typ
            break

    ok, value = _parse_value(raw, vtype)
    if not ok:
        print(t(lang, "invalid_value", value=raw))
        return 1

    _write_value(group, key, value)
    print(t(lang, "saved"))
    return 0


def cmd_reset(lang: str, key: str) -> int:
    if key == "--all":
        # 主配置：清空所有受管键
        cfg = load_main_config()
        for k, *_ in MAIN_SETTINGS:
            cfg.pop(k, None)
        save_main_config(cfg)

        # ptk：删掉受管的键
        ptk = load_ptk_config()
        for k, *_ in REPL_SETTINGS:
            _del_by_path(ptk, k)
        for k, *_ in COLOR_SETTINGS:
            _del_by_path(ptk, k)
        for k, *_ in KEY_ACTIONS:
            _del_by_path(ptk, f"key_bindings.{k}")
        save_ptk_config(ptk)

        print(t(lang, "reset_done"))
        return 0

    group = _find_group(key)
    if group is None:
        print(t(lang, "unknown_item", item=key))
        return 1
    _reset_value(group, key)
    print(t(lang, "reset_done"))
    return 0


def cmd_lang(lang: str, arg: Optional[str]) -> int:
    if not arg:
        cur = load_main_config().get("language", "Chinese")
        print(t(lang, "current_lang", lang=cur))
        return 0
    ok, value = _parse_value(arg, "choice")
    if not ok:
        print(t(lang, "invalid_value", value=arg))
        return 1
    cfg = load_main_config()
    cfg["language"] = value
    save_main_config(cfg)
    print(t(lang, "lang_set", lang=value))
    return 0


# ═══════════════════════════════════════════════════════════
# TUI
# ═══════════════════════════════════════════════════════════

def run_tui(lang: str) -> int:
    """简单终端交互式界面。不依赖 prompt_toolkit。"""
    print()
    print("═" * 60)
    print(f"  {t(lang, 'title')}")
    print("═" * 60)
    print(f"  {t(lang, 'tui_hint')}")
    print()

    items = list(_iter_all_settings())
    idx = 0
    while True:
        # 渲染
        os.system("cls" if os.name == "nt" else "clear")
        print(f"── {t(lang, 'title')} ──")
        print(f"   {t(lang, 'tui_hint')}")
        print()

        last_group = None
        for i, (group, key, typ, dflt, zh, en) in enumerate(items):
            if group != last_group:
                name = {
                    "main":  t(lang, "sect_main"),
                    "repl":  t(lang, "sect_repl"),
                    "color": t(lang, "sect_colors"),
                    "key":   t(lang, "sect_keys"),
                }.get(group, group)
                print(f"\n── {name} ──")
                last_group = group
            cur = _read_value(group, key)
            if cur is None:
                cur = dflt
            changed = _format_value(cur) != _format_value(dflt)
            mark = "*" if changed else " "
            cursor = ">" if i == idx else " "
            desc = zh if lang == "zh" else en
            line = f" {cursor}{mark} {key:<42} = {_format_value(cur):<26} # {desc}"
            print(line)

        print()
        try:
            ch = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print(t(lang, "tui_quit"))
            return 0

        if not ch:
            continue
        if ch == "q":
            print(t(lang, "tui_quit"))
            return 0
        if ch == "R":
            cmd_reset(lang, "--all")
            print(t(lang, "tui_reset_all"))
            time.sleep(0.5)
            continue
        if ch == "r":
            group, key, *_ = items[idx]
            _reset_value(group, key)
            print(t(lang, "reset_done"))
            time.sleep(0.5)
            continue
        if ch in ("j", "J"):
            idx = min(idx + 1, len(items) - 1)
            continue
        if ch in ("k", "K"):
            idx = max(idx - 1, 0)
            continue
        if ch == "\x1b[B":     # 下
            idx = min(idx + 1, len(items) - 1)
            continue
        if ch == "\x1b[A":     # 上
            idx = max(idx - 1, 0)
            continue
        if ch == "":
            continue

        # 直接输入数字行号 → 跳到该行
        if ch.isdigit():
            n = int(ch) - 1
            if 0 <= n < len(items):
                idx = n
            continue

        # Enter（空）以外的任意键：视为修改当前项
        group, key, typ, dflt, zh, en = items[idx]
        cur = _read_value(group, key)
        if cur is None:
            cur = dflt
        print()
        print(f"  {key} = {_format_value(cur)}")
        try:
            newval = input(f"  {t(lang, 'tui_item_prompt')}").strip()
        except (EOFError, KeyboardInterrupt):
            continue
        if not newval:
            continue
        ok, parsed = _parse_value(newval, typ)
        if not ok:
            print(t(lang, "invalid_value", value=newval))
            time.sleep(0.8)
            continue
        _write_value(group, key, parsed)
        print(t(lang, "saved"))
        time.sleep(0.4)

    return 0


# ═══════════════════════════════════════════════════════════
# Web 服务器
# ═══════════════════════════════════════════════════════════

# 页面里的默认语言（在 Handler 类属性上写）
class ConfigWebHandler(BaseHTTPRequestHandler):
    server_version = "OnyxConfig/1.0"

    def log_message(self, fmt, *args):
        # 静默日志，避免刷屏
        pass

    # ── 工具 ──
    def _send_html(self, html: str, status: int = 200) -> None:
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _redirect(self, path: str) -> None:
        self.send_response(302)
        self.send_header("Location", path)
        self.end_headers()

    def _client_lang(self) -> str:
        # 优先 cookie（我们设置的），其次全局配置
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("onyx_lang="):
                v = part[len("onyx_lang="):].strip()
                if v in ("zh", "en"):
                    return v
        return get_ui_language()

    # ── GET ──
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html", "/index"):
            lang = self._client_lang()
            flash = None
            qs = parse_qs(parsed.query)
            if "saved" in qs:
                flash = t(lang, "web_saved_msg")
            elif "reset" in qs:
                flash = t(lang, "web_reset_msg")
            self._send_html(self._render_index(lang, flash))
            return
        if parsed.path == "/lang":
            qs = parse_qs(parsed.query)
            new_lang = (qs.get("set", [""])[0] or "").strip()
            if new_lang in ("zh", "en"):
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"onyx_lang={new_lang}; Path=/; Max-Age=31536000")
                self.end_headers()
                return
            self._redirect("/")
            return
        self.send_error(404)

    # ── POST ──
    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = ""
        if length > 0:
            try:
                body = self.rfile.read(length).decode("utf-8", errors="replace")
            except Exception:
                body = ""

        params = parse_qs(body, keep_blank_values=True)
        lang = self._client_lang()

        if parsed.path == "/save":
            self._handle_save(params)
            self._redirect("/?saved=1")
            return
        if parsed.path == "/reset":
            self._handle_reset(params)
            self._redirect("/?reset=1")
            return
        if parsed.path == "/reset_all":
            cmd_reset(lang, "--all")
            self._redirect("/?reset=1")
            return
        self.send_error(404)

    # ── 保存 ──
    def _handle_save(self, params: Dict[str, List[str]]) -> None:
        # 遍历所有字段：字段名形如 "main:language"、"repl:completion.show_hidden"、"color:colors.x"、"key:history_up"
        for full_key, values in params.items():
            if ":" not in full_key:
                continue
            group, key = full_key.split(":", 1)
            if not values:
                continue
            raw = values[0]
            # 找类型
            vtype = "str"
            for g, k, typ, _, _, _ in _iter_all_settings():
                if g == group and k == key:
                    vtype = typ
                    break
            ok, parsed = _parse_value(raw, vtype)
            if not ok:
                continue
            _write_value(group, key, parsed)

    def _handle_reset(self, params: Dict[str, List[str]]) -> None:
        for full_key, _ in params.items():
            if ":" not in full_key:
                continue
            group, key = full_key.split(":", 1)
            _reset_value(group, key)

    # ── HTML 渲染 ──
    def _render_index(self, lang: str, flash: Optional[str]) -> str:
        html_parts = []
        html_parts.append("<!DOCTYPE html>")
        html_parts.append('<html lang="' + lang + '">')
        html_parts.append("<head>")
        html_parts.append('<meta charset="utf-8">')
        html_parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
        html_parts.append(f"<title>{t(lang, 'web_title')}</title>")
        html_parts.append(self._css())
        html_parts.append("</head>")
        html_parts.append("<body>")

        # 头部
        html_parts.append('<div class="header">')
        html_parts.append(f'<h1>{t(lang, "web_title")}</h1>')
        switch_label = t(lang, "lang_switch")
        switch_to = "en" if lang == "zh" else "zh"
        html_parts.append(f'<a class="lang-btn" href="/lang?set={switch_to}">{switch_label}</a>')
        html_parts.append("</div>")

        if flash:
            html_parts.append(f'<div class="flash">{flash}</div>')

        html_parts.append('<form method="POST" action="/save">')

        # 通用配置
        html_parts.append(self._render_section(lang, "main", t(lang, "sect_main"), MAIN_SETTINGS))
        # 主 REPL
        html_parts.append(self._render_section(lang, "repl", t(lang, "sect_repl"), REPL_SETTINGS))

        # 配色
        html_parts.append(f'<h2>{t(lang, "sect_colors")}</h2>')
        html_parts.append('<table>')
        html_parts.append(
            f'<tr><th>{t(lang, "web_item")}</th><th>{t(lang, "web_value")}</th><th>{t(lang, "web_action")}</th></tr>'
        )
        for key, dflt, zh, en in COLOR_SETTINGS:
            cur = _read_value("color", key)
            if cur is None:
                cur = dflt
            changed = str(cur) != str(dflt)
            cls = ' class="changed"' if changed else ""
            desc = zh if lang == "zh" else en
            html_parts.append(f"<tr{cls}>")
            html_parts.append(f'<td>{desc}<br><code>{key}</code></td>')
            html_parts.append(
                f'<td><input type="text" name="color:{key}" value="{self._esc(cur)}"></td>'
            )
            html_parts.append(f'<td><a class="reset-link" href="#" data-k="color:{key}">{t(lang, "btn_reset")}</a></td>')
            html_parts.append("</tr>")
        html_parts.append("</table>")

        # 键位
        html_parts.append(f'<h2>{t(lang, "sect_keys")}</h2>')
        html_parts.append('<table>')
        html_parts.append(
            f'<tr><th>{t(lang, "web_item")}</th><th>{t(lang, "web_value")}</th><th>{t(lang, "web_action")}</th></tr>'
        )
        for key, dflt, zh, en in KEY_ACTIONS:
            cur = _read_value("key", key)
            if cur is None:
                cur = dflt
            changed = str(cur) != str(dflt)
            cls = ' class="changed"' if changed else ""
            desc = zh if lang == "zh" else en
            html_parts.append(f"<tr{cls}>")
            html_parts.append(f'<td>{desc}<br><code>{key}</code></td>')
            html_parts.append(
                f'<td><input type="text" name="key:{key}" value="{self._esc(cur)}"></td>'
            )
            html_parts.append(f'<td><a class="reset-link" href="#" data-k="key:{key}">{t(lang, "btn_reset")}</a></td>')
            html_parts.append("</tr>")
        html_parts.append("</table>")

        # 底部按钮
        html_parts.append('<div class="footer">')
        html_parts.append(f'<button type="submit" class="primary">{t(lang, "btn_save")}</button>')
        html_parts.append("</form>")
        html_parts.append(
            f'<form method="POST" action="/reset_all" style="display:inline">'
            f'<button type="submit" class="danger">{t(lang, "btn_reset")} (--all)</button>'
            f"</form>"
        )
        html_parts.append("</div>")

        # 隐藏的重置表单（单个项重置）
        html_parts.append('<form id="reset-form" method="POST" action="/reset" style="display:none">')
        html_parts.append('<input type="hidden" name="_k" id="reset-k" value="">')
        html_parts.append("</form>")

        # JS：处理单项重置
        html_parts.append("<script>")
        html_parts.append("""
document.querySelectorAll('.reset-link').forEach(function(a){
  a.addEventListener('click', function(e){
    e.preventDefault();
    var k = a.getAttribute('data-k');
    var f = document.getElementById('reset-form');
    f.innerHTML = '<input type="hidden" name="' + k + '" value="1">';
    f.submit();
  });
});
""")
        html_parts.append("</script>")

        html_parts.append("</body></html>")
        return "".join(html_parts)

    @staticmethod
    def _esc(s: Any) -> str:
        s = "" if s is None else str(s)
        return (s.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;"))

    def _render_section(self, lang: str, group: str, title: str, defs) -> str:
        out = [f"<h2>{title}</h2>", "<table>"]
        out.append(
            f'<tr><th>{t(lang, "web_item")}</th><th>{t(lang, "web_value")}</th><th>{t(lang, "web_action")}</th></tr>'
        )
        for k, typ, dflt, zh, en in defs:
            cur = _read_value(group, k)
            if cur is None:
                cur = dflt
            changed = _format_value(cur) != _format_value(dflt)
            cls = ' class="changed"' if changed else ""
            desc = zh if lang == "zh" else en
            out.append(f"<tr{cls}>")
            out.append(f'<td>{desc}<br><code>{k}</code></td>')
            out.append(
                f'<td><input type="text" name="{group}:{k}" value="{self._esc(_format_value(cur))}"></td>'
            )
            out.append(f'<td><a class="reset-link" href="#" data-k="{group}:{k}">{t(lang, "btn_reset")}</a></td>')
            out.append("</tr>")
        out.append("</table>")
        return "".join(out)

    @staticmethod
    def _css() -> str:
        return """<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
       max-width: 980px; margin: 0 auto; padding: 24px 20px 80px;
       color: #222; background: #fafafa; }
.header { display: flex; align-items: center; justify-content: space-between;
          border-bottom: 2px solid #007acc; padding-bottom: 12px; margin-bottom: 24px; }
h1 { color: #007acc; margin: 0; font-size: 22px; }
h2 { color: #333; margin: 32px 0 12px; padding-bottom: 6px; border-bottom: 1px solid #eee; font-size: 17px; }
.lang-btn { text-decoration: none; background: #007acc; color: white;
            padding: 6px 14px; border-radius: 4px; font-size: 13px; }
.lang-btn:hover { background: #0066aa; }
.flash { background: #e8f5e9; color: #2e7d32; border-left: 4px solid #66bb6a;
         padding: 10px 14px; border-radius: 4px; margin-bottom: 18px; }
table { width: 100%; border-collapse: collapse; background: white;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05); border-radius: 6px; overflow: hidden; }
th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #eee; vertical-align: middle; font-size: 14px; }
th { background: #f5f5f5; font-weight: 600; color: #555; font-size: 13px; }
tr.changed { background: #fff8e1; }
tr.changed td:first-child::after { content: " *"; color: #f57c00; font-weight: bold; }
input[type=text] { width: 100%; max-width: 380px; padding: 7px 10px; font-family: inherit; font-size: 13px;
                   border: 1px solid #ddd; border-radius: 4px; background: #fff; }
input[type=text]:focus { border-color: #007acc; outline: none; }
code { color: #666; font-size: 12px; }
.reset-link { color: #d32f2f; text-decoration: none; font-size: 13px; }
.reset-link:hover { text-decoration: underline; }
.footer { margin-top: 32px; display: flex; gap: 12px; }
button { padding: 10px 24px; border: none; border-radius: 4px; font-size: 14px; cursor: pointer; }
button.primary { background: #007acc; color: white; }
button.primary:hover { background: #0066aa; }
button.danger { background: #d32f2f; color: white; }
button.danger:hover { background: #b71c1c; }
</style>"""


def _find_free_port(start: int = DEFAULT_WEB_PORT, max_tries: int = MAX_PORT_TRIES) -> Tuple[Optional[int], Optional[HTTPServer]]:
    """从 start 起找空闲端口；成功返回 (port, server)，失败返回 (None, None)。"""
    for port in range(start, start + max_tries):
        try:
            server = HTTPServer(("127.0.0.1", port), ConfigWebHandler)
            return port, server
        except OSError:
            continue
    return None, None


def run_web(lang: str, start_port: int = DEFAULT_WEB_PORT) -> int:
    print(t(lang, "starting_web"))
    port, server = _find_free_port(start_port)
    if port is None:
        print(t(lang, "no_free_port", start=start_port, end=start_port + MAX_PORT_TRIES - 1))
        return 1

    url = f"http://127.0.0.1:{port}/"
    print(t(lang, "port_found", port=port))
    print(t(lang, "server_started", url=url))

    # 后台线程开浏览器（给服务器一点启动时间）
    def _open():
        time.sleep(0.4)
        try:
            ok = webbrowser.open(url, new=2)
            if not ok:
                print(t(lang, "browser_failed", url=url))
            else:
                print(t(lang, "open_browser", url=url))
        except Exception:
            print(t(lang, "browser_failed", url=url))

    threading.Thread(target=_open, daemon=True).start()

    print(t(lang, "web_hint"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print(t(lang, "stopping"))
    finally:
        try:
            server.server_close()
        except Exception:
            pass
    print(t(lang, "stopped"))
    return 0


# ═══════════════════════════════════════════════════════════
# 首启询问：Web / TUI
# ═══════════════════════════════════════════════════════════

def ask_open_mode(lang: str) -> str:
    """返回 'web' 或 'tui'。默认 web。"""
    print()
    print(t(lang, "choose_mode"))
    print(t(lang, "mode_web"))
    print(t(lang, "mode_tui"))
    try:
        raw = input(t(lang, "mode_prompt")).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return "web"

    if raw in ("", "1", "w", "web", "Web"):
        return "web"
    if raw in ("2", "t", "tui", "TUI"):
        return "tui"
    print(t(lang, "invalid_choice"))
    return "web"


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="config-onyx-repl",
        description="Onyx 主 REPL 配置（Web / TUI）",
        add_help=True,
    )
    parser.add_argument("command", nargs="?", default=None,
                        help="list / get / set / reset / ui / web / lang / keys / repl / colors")
    parser.add_argument("args", nargs="*", help="子命令参数")
    parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT,
                        help=f"Web 起始端口（默认 {DEFAULT_WEB_PORT}）")
    parser.add_argument("--lang", choices=["zh", "en"], default=None,
                        help="本次运行的界面语言")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)

    # 语言：--lang 优先，否则按配置
    lang = ns.lang or get_ui_language()

    cmd = ns.command
    rest = ns.args or []

    # ── 无参数：询问 Web / TUI ──
    if cmd is None:
        mode = ask_open_mode(lang)
        if mode == "web":
            return run_web(lang, start_port=ns.port)
        return run_tui(lang)

    # ── 直接 web ──
    if cmd == "web":
        # 允许 web --port 9000
        p = ns.port
        if rest and rest[0].lstrip("-").isdigit():
            try:
                p = int(rest[0])
            except ValueError:
                pass
        elif len(rest) >= 2 and rest[0] == "--port" and rest[1].isdigit():
            p = int(rest[1])
        return run_web(lang, start_port=p)

    # ── 直接 tui ──
    if cmd in ("ui", "tui"):
        return run_tui(lang)

    # ── list ──
    if cmd == "list":
        return cmd_list(lang)

    # ── get <item> ──
    if cmd == "get":
        if not rest:
            print(t(lang, "need_item"))
            return 1
        return cmd_get(lang, rest[0])

    # ── set <item> <value> ──
    if cmd == "set":
        if len(rest) < 2:
            print(t(lang, "need_item_value"))
            return 1
        return cmd_set(lang, rest[0], rest[1])

    # ── reset <item> | --all ──
    if cmd == "reset":
        if not rest:
            print(t(lang, "need_item"))
            return 1
        return cmd_reset(lang, rest[0])

    # ── lang [zh|en] ──
    if cmd == "lang":
        return cmd_lang(lang, rest[0] if rest else None)

    # ── keys / repl / colors：把子指令转给 set/get/reset 处理 ──
    if cmd in ("keys", "repl", "colors"):
        if not rest:
            # 列出对应分组
            print(t(lang, "list_header"))
            last_group = None
            for group, key, typ, dflt, zh, en in _iter_all_settings():
                # 只显示相关组
                if cmd == "keys" and group != "key":
                    continue
                if cmd == "repl" and group not in ("repl",):
                    continue
                if cmd == "colors" and group != "color":
                    continue
                if group != last_group:
                    name = {
                        "repl":  t(lang, "sect_repl"),
                        "color": t(lang, "sect_colors"),
                        "key":   t(lang, "sect_keys"),
                    }.get(group, group)
                    print(f"\n── {name} ──")
                    last_group = group
                cur = _read_value(group, key)
                if cur is None:
                    cur = dflt
                mark = " " if _format_value(cur) == _format_value(dflt) else "*"
                desc = zh if lang == "zh" else en
                print(f"  {mark} {key:<42} = {_format_value(cur):<28} # {desc}")
            return 0

        sub = rest[0]
        tail = rest[1:]
        if sub == "set":
            if len(tail) < 2:
                print(t(lang, "need_item_value"))
                return 1
            # colors set <name> <val> → colors.colors.<name> 前缀处理
            if cmd == "colors":
                key = tail[0] if tail[0].startswith("colors.") else f"colors.{tail[0]}"
                return cmd_set(lang, key, tail[1])
            if cmd == "keys":
                # keys set <action> <key>
                return cmd_set(lang, tail[0], " ".join(tail[1:]))
            # repl set
            return cmd_set(lang, tail[0], " ".join(tail[1:]))
        if sub == "get":
            if not tail:
                print(t(lang, "need_item"))
                return 1
            if cmd == "colors":
                key = tail[0] if tail[0].startswith("colors.") else f"colors.{tail[0]}"
                return cmd_get(lang, key)
            return cmd_get(lang, tail[0])
        if sub == "reset":
            if not tail:
                print(t(lang, "need_item"))
                return 1
            if tail[0] == "--all":
                return cmd_reset(lang, "--all")
            if cmd == "colors":
                key = tail[0] if tail[0].startswith("colors.") else f"colors.{tail[0]}"
                return cmd_reset(lang, key)
            return cmd_reset(lang, tail[0])
        if sub == "list":
            return main(["list"] + (["--lang", lang] if lang else []))

    # 未知
    print(t(lang, "unknown_cmd", cmd=cmd))
    parser.print_help()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(1)