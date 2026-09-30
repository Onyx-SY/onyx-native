#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台手册页扫描器 —— 纯异步后台扫描模式
支持增量更新：一次性建立 man 页索引 + 节流批量落盘，不阻塞主程序

新增（com_cmd.json 协同）：
- 用户在 com_cmd.json 里已经定义过的命令，不再进行 man 扫描
- 内置 ARG_TYPE_HINTS：常见系统命令自动带上 arguments.type

修复（结构化 JSON 保全）：
- 合并两个 JSON 时不再用 set() 展开 subcommands，
  改用递归 _merge_node / _merge_subcommands，保留 dict 嵌套结构
- subcommands 是 dict（新格式）时不再被压成 list
- _scan_loop 不再强塞 "subcommands": []，避免污染节点结构
- 已损坏的旧 command.json（subcommands 被压平）会在下次加载时自动以 cmd.json 为准覆盖

修复（Python 3.14 导入死锁专项）：
- _resolve_com_cmd_path 不再 `from lib.terminal.com import ...`。
  此前后台扫描线程会顺着 man.py → com.py 把 prompt_toolkit 整包拖进来，
  与主线程首次导入 prompt_toolkit 撞车，在 3.14 上触发
  _DeadlockError: deadlock detected by _ModuleLock('prompt_toolkit.lexers')。
  现在只在 com 模块【已经被主线程加载过】时才去读取它的路径，
  后台线程不再主动导入 com，避免抢模块锁。
"""

import os
import sys
import json
import re
import gzip
import subprocess
import logging
import time
import signal
import threading
import argparse
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple, Any, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

# ---------- 路径定义 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUILTIN_CMD_JSON = os.path.join(BASE_DIR, "etc", "cmd.json")
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "onyx")
COMMAND_JSON_PATH = os.path.join(CACHE_DIR, "command.json")
SCAN_PROGRESS_PATH = os.path.join(CACHE_DIR, "scan_progress.json")

os.makedirs(CACHE_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.ERROR,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 结构化 JSON 递归合并
# ═══════════════════════════════════════════════════════════
#
# 背景：cmd.json 用新格式（subcommands 是 dict，可嵌套），
# command.json 里可能混有旧格式（subcommands 是 list）。
# 合并时必须保留 dict 结构，不能 set() 展平。
#
# 合并规则：
#   options      → 数组并集（去重）
#   arguments    → a 有则用 a，没有则从 b 拿
#   type         → 同上
#   multiple     → 只要任一方为真则为真
#   subcommands  → 见 _merge_subcommands
# ═══════════════════════════════════════════════════════════

def _merge_subcommands(a: Any, b: Any) -> Any:
    """递归合并两个 subcommands 结构，保留 dict 语义。

    - dict + dict   ：递归合并（同名子节点继续 _merge_node）
    - list + list   ：字符串并集（去重、保序）
    - dict + list   ：以 dict 为准，忽略 list（dict 通常代表更新、更结构化）
    - list + dict   ：以 dict 为准，忽略 list
    - 其他/None     ：返回非空的一方
    """
    a_is_dict = isinstance(a, dict)
    b_is_dict = isinstance(b, dict)
    a_is_list = isinstance(a, list)
    b_is_list = isinstance(b, list)

    if a_is_dict and b_is_dict:
        result: Dict[str, Any] = dict(a)
        for k, v in b.items():
            if k in result:
                result[k] = _merge_node(result[k], v)
            else:
                result[k] = v
        return result

    if a_is_list and b_is_list:
        seen: List[str] = []
        for x in a:
            if isinstance(x, str) and x not in seen:
                seen.append(x)
        for x in b:
            if isinstance(x, str) and x not in seen:
                seen.append(x)
        return seen

    # 类型冲突 → 优先 dict
    if b_is_dict and not a_is_dict:
        return dict(b)
    if a_is_dict and not b_is_dict:
        return dict(a)
    if b_is_list and not a_is_list:
        return list(b)
    if a_is_list and not b_is_list:
        return list(a)

    return a if a is not None else b


def _merge_node(a: Any, b: Any) -> Dict[str, Any]:
    """递归合并两个命令节点规格，保留嵌套结构。"""
    if not isinstance(a, dict):
        a = {}
    if not isinstance(b, dict):
        b = {}

    result: Dict[str, Any] = dict(a)

    # options：数组并集，保序去重
    opts: List[str] = []
    for o in result.get("options", []) or []:
        if isinstance(o, str) and o not in opts:
            opts.append(o)
    for o in b.get("options", []) or []:
        if isinstance(o, str) and o not in opts:
            opts.append(o)
    if opts:
        result["options"] = opts

    # subcommands：递归合并
    if "subcommands" in a or "subcommands" in b:
        result["subcommands"] = _merge_subcommands(
            a.get("subcommands", []),
            b.get("subcommands", []),
        )

    # arguments：a 有则保留 a，否则用 b
    if "arguments" not in result and "arguments" in b:
        result["arguments"] = b["arguments"]

    # type：同上
    if "type" not in result and "type" in b:
        result["type"] = b["type"]

    # multiple：任一为真即真
    if b.get("multiple") or a.get("multiple"):
        result["multiple"] = True

    return result


# ═══════════════════════════════════════════════════════════
# com_cmd.json 协同：用户已处理过的命令跳过扫描
# ═══════════════════════════════════════════════════════════
#
# ⚠️ Python 3.14 死锁规避：
# 本模块运行在【后台扫描线程】里。此前 _resolve_com_cmd_path() 里写了
#   from lib.terminal.com import get_com_cmd_config_path
# 而 com.py 顶部硬编码导入整个 prompt_toolkit。当主线程同时在
# input_lib.py 里首次导入 prompt_toolkit 时，两边会在
# _ModuleLock('prompt_toolkit.lexers') 上互等，3.14 直接抛
# _DeadlockError。修复：后台线程不再主动导入 com，
# 只读取 sys.modules 里【已被主线程加载过】的实例。
# ═══════════════════════════════════════════════════════════

_COM_CMD_JSON_PATH: Optional[str] = None
_COM_CMD_COMMANDS: Optional[Set[str]] = None


def set_com_cmd_path(path: str) -> None:
    global _COM_CMD_JSON_PATH, _COM_CMD_COMMANDS
    _COM_CMD_JSON_PATH = path or None
    _COM_CMD_COMMANDS = None


def _resolve_com_cmd_path() -> Optional[str]:
    global _COM_CMD_JSON_PATH

    if _COM_CMD_JSON_PATH and os.path.exists(_COM_CMD_JSON_PATH):
        return _COM_CMD_JSON_PATH

    candidates: List[str] = []

    env_path = os.environ.get("ONYX_COM_CMD_JSON", "").strip()
    if env_path:
        candidates.append(env_path)

    # ── 关键修复：绝不主动 import lib.terminal.com ──
    # 只在它【已经被主线程加载过】时才去读取它登记的路径。
    # 若尚未加载，本线程直接跳过 —— 宁可少一个候选路径，
    # 也不能触发 com.py 首次导入，把 prompt_toolkit 拖进后台线程。
    com_mod = sys.modules.get("lib.terminal.com")
    if com_mod is None:
        # 兜底：尝试已在 sys.modules 里的其它可能包名
        com_mod = sys.modules.get("onyx.lib.terminal.com")
    if com_mod is not None:
        try:
            p = getattr(com_mod, "get_com_cmd_config_path", None)
            if callable(p):
                path_val = p()
                if path_val:
                    candidates.append(path_val)
        except Exception:
            pass

    candidates.append("/onyx/etc/com_cmd.json")
    candidates.append(os.path.join(BASE_DIR, "etc", "com_cmd.json"))

    for p in candidates:
        if p and os.path.exists(p):
            _COM_CMD_JSON_PATH = p
            return p
    return None


def get_com_cmd_commands() -> Set[str]:
    global _COM_CMD_COMMANDS
    if _COM_CMD_COMMANDS is not None:
        return _COM_CMD_COMMANDS

    result: Set[str] = set()
    path = _resolve_com_cmd_path()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in data.keys():
                    if isinstance(k, str) and k:
                        result.add(k)
        except Exception as e:
            logger.debug(f"读取 com_cmd.json 失败: {e}")

    _COM_CMD_COMMANDS = result
    return result


@dataclass
class SystemConfig:
    platform: str
    man_dirs: List[str] = field(default_factory=list)
    use_man_command: bool = True
    use_apropos: bool = True
    man_sections: List[str] = field(default_factory=lambda: ['1', '8'])


class AsyncManScanner:
    """异步后台手册页扫描器 - 增量更新模式"""

    # ── 常见命令的参数类型提示 ──
    ARG_TYPE_HINTS: Dict[str, str] = {
        "cd":     "dir", "mkdir": "dir", "rmdir": "dir",
        "pushd":  "dir", "popd":  "dir", "chdir": "dir",
        "tree":   "dir", "du":    "dir", "df":    "dir",

        "cat":    "file", "less":  "file", "more":  "file",
        "head":   "file", "tail":  "file",
        "nano":   "file", "vim":   "file", "vi":    "file",
        "emacs":  "file", "micro": "file",
        "md5sum": "file", "sha256sum": "file",
        "base64": "file",
        "gzip":   "file", "bzip2": "file", "xz":    "file",
        "unzip":  "file", "zip":   "file",

        "python":  "file", "python3": "file", "python2": "file",
        "node":    "file", "ruby":    "file", "perl":    "file",
        "lua":     "file",
        "bash":    "file", "sh":      "file", "zsh":     "file",
        "fish":    "file",

        "cp":      "file", "mv":      "file", "rm":      "file",
        "chmod":   "file", "chown":   "file", "chgrp":   "file",
        "touch":   "file", "stat":    "file", "file":    "file",
        "sed":     "file", "awk":     "file", "sort":    "file",
        "uniq":    "file", "cut":     "file", "wc":      "file",
        "diff":    "file", "patch":   "file", "tar":     "file",
        "scp":     "file",

        "find":    "dir",
        "grep":    "file",
    }

    def __init__(self):
        self.config = self._detect_system()
        self._stop_flag = False
        self._scan_thread = None
        self._current_progress = self._load_progress()
        self._man_index: Optional[Dict[str, List[Path]]] = None

    def _detect_system(self) -> SystemConfig:
        config = SystemConfig(platform='unknown')
        try:
            if os.path.exists("/data/data/com.termux") or "termux" in sys.prefix.lower():
                config.platform = 'termux'
                config.man_dirs = ["/data/data/com.termux/files/usr/share/man"]
                config.use_man_command = True
                config.use_apropos = False
                return config

            if sys.platform == "darwin":
                config.platform = 'macos'
                config.man_dirs = ["/usr/share/man", "/opt/local/share/man", "/usr/local/share/man"]
                return config

            if sys.platform.startswith("win32") or sys.platform == "cygwin":
                config.platform = 'windows'
                config.use_man_command = False
                config.use_apropos = False
                return config

            config.platform = 'linux'
            config.man_dirs = ["/usr/share/man", "/usr/local/share/man"]
        except Exception as e:
            logger.debug(f"系统检测失败: {e}")
        return config

    def _load_progress(self) -> Dict:
        if os.path.exists(SCAN_PROGRESS_PATH) and os.path.getsize(SCAN_PROGRESS_PATH) > 0:
            try:
                with open(SCAN_PROGRESS_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.debug(f"加载扫描进度失败: {e}")
        return {"scanned": [], "last_index": 0, "total_commands": 0}

    def _save_progress(self):
        try:
            with open(SCAN_PROGRESS_PATH, 'w', encoding='utf-8') as f:
                json.dump(self._current_progress, f, indent=2)
        except Exception as e:
            logger.debug(f"保存扫描进度失败: {e}")

    def _load_builtin_commands(self) -> Dict:
        if os.path.exists(BUILTIN_CMD_JSON):
            try:
                with open(BUILTIN_CMD_JSON, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.debug(f"加载内置命令文件失败: {e}")
        return {}

    def _load_existing_commands(self) -> Dict:
        """加载已存在的命令数据。

        优先级：cmd.json（内置，新格式，作为基础）> command.json（缓存，旧格式兼容）

        合并时用 _merge_node 递归合并，保留 subcommands 的 dict 嵌套结构，
        不会再出现「dict 被 set() 压成 list」的问题。
        """
        # 1. 以 cmd.json 为基础
        try:
            commands = self._load_builtin_commands()
        except Exception as e:
            logger.debug(f"加载内置命令失败: {e}")
            commands = {}

        # 2. 把 command.json 的增量合并进来
        if os.path.exists(COMMAND_JSON_PATH):
            try:
                with open(COMMAND_JSON_PATH, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
            except Exception as e:
                logger.debug(f"加载命令文件失败: {e}")
                saved = {}

            if isinstance(saved, dict):
                for cmd, info in saved.items():
                    if not isinstance(cmd, str):
                        continue
                    if cmd in commands:
                        # 递归合并：cmd.json 里已有的结构优先保留，
                        # command.json 只贡献新增的 options / 类型提示
                        commands[cmd] = _merge_node(commands[cmd], info)
                    else:
                        # 只存在于缓存里的命令（man 页扫出来的），原样保留
                        commands[cmd] = info
        else:
            # 首次运行：把内置命令写入缓存
            if commands:
                try:
                    self._save_commands(commands)
                except Exception as e:
                    logger.debug(f"初始化 command.json 失败: {e}")

        return commands

    def _save_commands(self, commands: Dict):
        tmp_path = COMMAND_JSON_PATH + ".tmp"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(commands, f, indent=2, ensure_ascii=False, sort_keys=True)
            os.replace(tmp_path, COMMAND_JSON_PATH)
        except Exception as e:
            logger.debug(f"保存命令数据失败: {e}")
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _build_man_index(self) -> Dict[str, List[Path]]:
        index: Dict[str, List[Path]] = {}
        seen: Set[Tuple[str, str]] = set()
        try:
            for man_dir in self.config.man_dirs:
                if not os.path.exists(man_dir):
                    continue
                for section in self.config.man_sections:
                    man_section_dir = os.path.join(man_dir, f"man{section}")
                    if not os.path.exists(man_section_dir):
                        continue
                    try:
                        for file in os.listdir(man_section_dir):
                            if '.' not in file:
                                continue
                            base = file.split('.', 1)[0]
                            if not base:
                                continue
                            key = (base, man_section_dir)
                            if key in seen:
                                continue
                            seen.add(key)
                            index.setdefault(base, []).append(Path(man_section_dir) / file)
                    except (PermissionError, OSError):
                        continue
        except Exception as e:
            logger.debug(f"建立手册页索引失败: {e}")
        return index

    def find_manpage_files(self, cmd_name: str) -> List[Path]:
        if self._man_index is None:
            self._man_index = self._build_man_index()
        return list(self._man_index.get(cmd_name, []))

    def read_manpage_content(self, manpage_path: Path) -> str:
        try:
            if str(manpage_path).endswith('.gz'):
                with gzip.open(manpage_path, 'rt', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            else:
                with open(manpage_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
        except Exception as e:
            logger.debug(f"读取手册页失败 {manpage_path}: {e}")
            return ""

    def parse_options_from_roff(self, content: str) -> Set[str]:
        options = set()
        try:
            patterns = [
                r'\\fB\\-\\-[a-zA-Z][a-zA-Z0-9\-]*\\fP',
                r'\\fB--[a-zA-Z][a-zA-Z0-9\-]*\\fP',
                r'\\fB\\-([a-zA-Z0-9])\\fP',
                r'\\fB-([a-zA-Z0-9])\\fP',
                r'(?<!\w)--([a-zA-Z][a-zA-Z0-9\-]+)(?=\s|,|$|\))',
                r'(?<!\w)-([a-zA-Z0-9])(?=\s|,|$|\))',
            ]
            for pattern in patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    if match.startswith('--'):
                        options.add(match)
                    elif match.startswith('-') and len(match) == 2:
                        options.add(match)
                    elif len(match) == 1 and match.isalnum():
                        options.add(f"-{match}")
                    elif match.startswith('\\'):
                        clean = match.replace('\\fB', '').replace('\\fP', '').replace('\\-', '-')
                        if clean.startswith('--') or (clean.startswith('-') and len(clean) == 2):
                            options.add(clean)

            synopsis_match = re.search(r'\.SH\s+SYNOPSIS(.*?)(\.SH\s+|$)', content, re.DOTALL | re.IGNORECASE)
            if synopsis_match:
                synopsis = synopsis_match.group(1)
                bracket_opts = re.findall(r'\[([^\]]+)\]', synopsis)
                for bracket_opt in bracket_opts:
                    short_opts = re.findall(r'-([a-zA-Z0-9])', bracket_opt)
                    for opt in short_opts:
                        options.add(f"-{opt}")
                    long_opts = re.findall(r'--([a-zA-Z][a-zA-Z0-9\-]*)', bracket_opt)
                    for opt in long_opts:
                        options.add(f"--{opt}")
        except Exception as e:
            logger.debug(f"解析选项失败: {e}")
        return options

    def extract_options_quick(self, cmd: str) -> List[str]:
        options = set()
        try:
            manpage_files = self.find_manpage_files(cmd)
            for manpage in manpage_files:
                content = self.read_manpage_content(manpage)
                if content:
                    opts = self.parse_options_from_roff(content)
                    options.update(opts)
                    if options:
                        break
        except Exception as e:
            logger.debug(f"提取选项失败 {cmd}: {e}")
        return sorted(options)

    def get_all_commands(self) -> List[str]:
        commands = set()
        try:
            path_dirs = os.environ.get("PATH", "").split(os.pathsep)
            for path_dir in path_dirs:
                if not os.path.exists(path_dir):
                    continue
                try:
                    for item in os.listdir(path_dir):
                        item_path = os.path.join(path_dir, item)
                        if os.path.isfile(item_path) and os.access(item_path, os.X_OK):
                            if item and (item[0].islower() or item[0].isalpha()):
                                commands.add(item)
                except (PermissionError, OSError):
                    continue

            for man_dir in self.config.man_dirs:
                if not os.path.exists(man_dir):
                    continue
                for section in self.config.man_sections:
                    man_section_dir = os.path.join(man_dir, f"man{section}")
                    if not os.path.exists(man_section_dir):
                        continue
                    try:
                        for file in os.listdir(man_section_dir):
                            cmd = file.split('.')[0]
                            if cmd and cmd[0].islower() and cmd.isascii():
                                commands.add(cmd)
                    except (PermissionError, OSError):
                        continue
        except Exception as e:
            logger.debug(f"获取命令列表失败: {e}")
        return sorted(commands)

    def start_background_scan(self):
        if self._scan_thread and self._scan_thread.is_alive():
            return
        self._stop_flag = False
        self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._scan_thread.start()

    def stop_scan(self):
        self._stop_flag = True
        if self._scan_thread:
            self._scan_thread.join(timeout=2)

    def _apply_type_hint(self, node: Dict, cmd: str) -> None:
        """给命令节点补上类型提示（只在原节点没有 arguments / type 时补）。"""
        hint = self.ARG_TYPE_HINTS.get(cmd)
        if not hint:
            return
        if not isinstance(node, dict):
            return
        if "arguments" in node or "type" in node:
            return
        node["arguments"] = {"type": hint}

    def _scan_loop(self):
        """后台扫描循环

        - 跳过 com_cmd.json 中已定义的命令
        - 跳过已有选项的命令（增量）
        - 只更新 options 和类型提示，不触碰 subcommands 结构
        """
        try:
            existing = self._load_existing_commands()
            all_commands = self.get_all_commands()

            handled_by_user = get_com_cmd_commands()
            if handled_by_user:
                logger.debug(f"com_cmd.json 已定义 {len(handled_by_user)} 个命令，跳过扫描")

            to_scan = [
                cmd for cmd in all_commands
                if cmd not in handled_by_user
                and (cmd not in existing or not existing[cmd].get("options"))
            ]

            self._current_progress["total_commands"] = len(all_commands)
            self._save_progress()

            SAVE_EVERY = 100
            SAVE_INTERVAL = 2.0
            last_save = time.time()

            for i, cmd in enumerate(to_scan):
                if self._stop_flag:
                    break

                try:
                    options = self.extract_options_quick(cmd)

                    # ── 关键：不要碰 subcommands 结构 ──
                    if cmd not in existing:
                        # 新命令：只放 options，不加 subcommands 字段
                        existing[cmd] = {"options": []}

                    node = existing[cmd]
                    if not isinstance(node, dict):
                        node = {"options": []}
                        existing[cmd] = node

                    if options:
                        existing_opts = set(node.get("options", []))
                        existing_opts.update(options)
                        node["options"] = sorted(existing_opts)

                    # 补类型提示（只在没有 arguments/type 时）
                    self._apply_type_hint(node, cmd)

                except Exception as e:
                    logger.debug(f"扫描命令失败 {cmd}: {e}")

                if (i + 1) % SAVE_EVERY == 0 or (time.time() - last_save) >= SAVE_INTERVAL:
                    self._current_progress["scanned"] = list(existing.keys())
                    self._current_progress["last_index"] = i + 1
                    self._save_commands(existing)
                    self._save_progress()
                    last_save = time.time()

                if (i + 1) % 50 == 0:
                    time.sleep(0)

            # 最终落盘
            self._current_progress["scanned"] = list(existing.keys())
            self._current_progress["last_index"] = len(to_scan)
            self._save_commands(existing)
            self._save_progress()

            if os.path.exists(SCAN_PROGRESS_PATH):
                try:
                    os.remove(SCAN_PROGRESS_PATH)
                except Exception as e:
                    logger.debug(f"删除进度文件失败: {e}")
        except Exception as e:
            logger.debug(f"扫描循环失败: {e}")


_scanner: Optional[AsyncManScanner] = None


def get_scanner() -> AsyncManScanner:
    global _scanner
    if _scanner is None:
        _scanner = AsyncManScanner()
    return _scanner


def start_background_scan():
    scanner = get_scanner()
    scanner.start_background_scan()


def incremental_update():
    scanner = get_scanner()
    scanner.start_background_scan()


def main():
    parser = argparse.ArgumentParser(description="跨平台命令扫描器")
    parser.add_argument("--force", action="store_true", help="强制重新扫描")
    parser.add_argument("--background", action="store_true", help="后台模式（静默运行）")
    parser.add_argument("--com-cmd", dest="com_cmd", default="",
                        help="用户的补全详情文件路径（com_cmd.json）；其中已定义的命令将跳过扫描")
    args = parser.parse_args()

    if args.com_cmd:
        set_com_cmd_path(args.com_cmd)

    if args.background:
        if hasattr(os, 'nice'):
            try:
                os.nice(19)
            except Exception:
                pass
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')
        logging.disable(logging.CRITICAL)

    scanner = get_scanner()

    if args.force and os.path.exists(COMMAND_JSON_PATH):
        try:
            os.remove(COMMAND_JSON_PATH)
        except Exception:
            pass
        if os.path.exists(SCAN_PROGRESS_PATH):
            try:
                os.remove(SCAN_PROGRESS_PATH)
            except Exception:
                pass

    scanner.start_background_scan()

    if not args.background:
        try:
            if scanner._scan_thread:
                scanner._scan_thread.join()
        except KeyboardInterrupt:
            scanner.stop_scan()


if __name__ == "__main__":
    main()