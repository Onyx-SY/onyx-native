#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台手册页扫描器 —— 纯异步后台扫描模式
支持增量更新：一次性建立 man 页索引 + 节流批量落盘，不阻塞主程序

本次跨平台优化（macOS + Windows）：
- _detect_system：macOS 覆盖 Intel Homebrew (/usr/local/share/man)、
  Apple Silicon Homebrew (/opt/homebrew/share/man)、MacPorts (/opt/local/share/man)，
  并新增 /opt/homebrew/opt 与 /usr/local/opt 展开目录
- _build_man_index：识别 macOS Homebrew 的 opt/<pkg>/share/man 结构
- get_all_commands：Windows 按 PATHEXT 扫描可执行文件（.exe/.cmd/.bat/.ps1），
  并提取基础名作为命令名；POSIX 仍按 X_OK
- com_cmd.json 协同：用户在 com_cmd.json 里已定义的命令跳过 man 扫描
- ARG_TYPE_HINTS：常见系统命令自动带上 arguments.type

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

    opts: List[str] = []
    for o in result.get("options", []) or []:
        if isinstance(o, str) and o not in opts:
            opts.append(o)
    for o in b.get("options", []) or []:
        if isinstance(o, str) and o not in opts:
            opts.append(o)
    if opts:
        result["options"] = opts

    if "subcommands" in a or "subcommands" in b:
        result["subcommands"] = _merge_subcommands(
            a.get("subcommands", []),
            b.get("subcommands", []),
        )

    if "arguments" not in result and "arguments" in b:
        result["arguments"] = b["arguments"]

    if "type" not in result and "type" in b:
        result["type"] = b["type"]

    if b.get("multiple") or a.get("multiple"):
        result["multiple"] = True

    return result


# ═══════════════════════════════════════════════════════════
# com_cmd.json 协同
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

    # ── 关键：绝不主动 import lib.terminal.com ──
    # 只在它【已经被主线程加载过】时才去读取它登记的路径。
    com_mod = sys.modules.get("lib.terminal.com")
    if com_mod is None:
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

        # macOS 常用
        "open":    "file", "pbcopy":  "file", "pbpaste": "file",
        "mdfind":  "file", "mdls":    "file",
        "diskutil": "dir", "hdiutil": "file",
        "say":     "file", "afplay":  "file", "screencapture": "file",
        "defaults": "file", "launchctl": "file",

        # PowerShell / Windows 常见
        "Get-Content":    "file", "Set-Content":    "file",
        "Get-ChildItem":  "dir",  "Set-Location":  "dir",
        "Copy-Item":      "file", "Move-Item":     "file",
        "Remove-Item":    "file", "New-Item":      "file",
        "Test-Path":      "file", "Get-Item":      "file",
        "Select-String":  "file", "Out-File":      "file",
        "Add-Content":    "file", "Clear-Content": "file",
        "Join-Path":      "file", "Split-Path":    "file",
        "Resolve-Path":   "file", "Convert-Path":  "file",
        "type":           "file", "dir":           "dir",
        "findstr":        "file", "where":         "file",
    }

    def __init__(self):
        self.config = self._detect_system()
        self._stop_flag = False
        self._scan_thread = None
        self._current_progress = self._load_progress()
        self._man_index: Optional[Dict[str, List[Path]]] = None

    def _detect_system(self) -> SystemConfig:
        """跨平台检测系统并给出 man 目录列表。

        macOS：
          - /usr/share/man             系统自带
          - /usr/local/share/man       Intel Homebrew
          - /usr/local/opt             Intel Homebrew opt 展开
          - /opt/homebrew/share/man    Apple Silicon Homebrew
          - /opt/homebrew/opt          Apple Silicon Homebrew opt 展开
          - /opt/local/share/man       MacPorts

        Windows：无 man，走 PATH + PATHEXT 扫描
        """
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
                config.man_dirs = [
                    "/usr/share/man",
                    "/usr/local/share/man",
                    "/usr/local/opt",
                    "/opt/homebrew/share/man",
                    "/opt/homebrew/opt",
                    "/opt/local/share/man",
                ]
                config.use_man_command = True
                config.use_apropos = True
                return config

            if sys.platform.startswith("win32") or sys.platform == "cygwin":
                config.platform = 'windows'
                config.man_dirs = []
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
        合并时用 _merge_node 递归合并，保留 subcommands 的 dict 嵌套结构。
        """
        try:
            commands = self._load_builtin_commands()
        except Exception as e:
            logger.debug(f"加载内置命令失败: {e}")
            commands = {}

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
                        commands[cmd] = _merge_node(commands[cmd], info)
                    else:
                        commands[cmd] = info
        else:
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
        """建立 man 页索引，支持 macOS Homebrew 的 opt/<pkg>/share/man 结构。"""
        index: Dict[str, List[Path]] = {}
        seen: Set[Tuple[str, str]] = set()

        def _add_man_dir(man_dir: str):
            if not os.path.exists(man_dir):
                return
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

        try:
            for man_dir in self.config.man_dirs:
                # macOS Homebrew：展开 /opt/homebrew/opt/*/share/man 与 /usr/local/opt/*/share/man
                if man_dir.endswith('/opt') and os.path.isdir(man_dir):
                    try:
                        for pkg in os.listdir(man_dir):
                            pkg_man = os.path.join(man_dir, pkg, "share", "man")
                            if os.path.isdir(pkg_man):
                                _add_man_dir(pkg_man)
                    except (PermissionError, OSError):
                        pass
                else:
                    _add_man_dir(man_dir)
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
        """扫描所有可用命令。

        POSIX：PATH 里具有 X_OK 的文件 + man 目录里的命令
        Windows：PATH 里按 PATHEXT 匹配的可执行文件（去掉扩展名作为命令名）
        macOS：额外扫描 /opt/homebrew/bin 与 /usr/local/bin（PATH 未覆盖时）
        """
        commands = set()
        try:
            path_dirs = os.environ.get("PATH", "").split(os.pathsep)

            if os.name == 'nt':
                pathext = os.environ.get(
                    "PATHEXT", ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.PS1"
                ).split(";")
                pathext_upper = {e.upper() for e in pathext if e}
            else:
                pathext_upper = set()

            for path_dir in path_dirs:
                if not path_dir or not os.path.exists(path_dir):
                    continue
                try:
                    for item in os.listdir(path_dir):
                        item_path = os.path.join(path_dir, item)
                        if not os.path.isfile(item_path):
                            continue

                        if os.name == 'nt':
                            _, ext = os.path.splitext(item)
                            if ext.upper() not in pathext_upper:
                                continue
                            base = os.path.splitext(item)[0]
                            if base and (base[0].islower() or base[0].isalpha()):
                                commands.add(base)
                        else:
                            if os.access(item_path, os.X_OK):
                                if item and (item[0].islower() or item[0].isalpha()):
                                    commands.add(item)
                except (PermissionError, OSError):
                    continue

            # man 目录（仅 POSIX 有；跳过 opt 展开目录）
            for man_dir in self.config.man_dirs:
                if man_dir.endswith('/opt') or not os.path.isdir(man_dir):
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

                    if cmd not in existing:
                        existing[cmd] = {"options": []}

                    node = existing[cmd]
                    if not isinstance(node, dict):
                        node = {"options": []}
                        existing[cmd] = node

                    if options:
                        existing_opts = set(node.get("options", []))
                        existing_opts.update(options)
                        node["options"] = sorted(existing_opts)

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