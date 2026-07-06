#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台手册页扫描器 —— 纯异步后台扫描模式
支持增量更新，每处理一个命令立即保存，不阻塞主程序
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
from typing import Dict, List, Set, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

# ---------- 路径定义 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUILTIN_CMD_JSON = os.path.join(BASE_DIR, "etc", "cmd.json")
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "onyx")
COMMAND_JSON_PATH = os.path.join(CACHE_DIR, "command.json")
SCAN_PROGRESS_PATH = os.path.join(CACHE_DIR, "scan_progress.json")

os.makedirs(CACHE_DIR, exist_ok=True)

# 配置 logging，只输出错误
logging.basicConfig(
    level=logging.ERROR,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class SystemConfig:
    """系统配置信息"""
    platform: str
    man_dirs: List[str] = field(default_factory=list)
    use_man_command: bool = True
    use_apropos: bool = True
    man_sections: List[str] = field(default_factory=lambda: ['1', '8'])


class AsyncManScanner:
    """异步后台手册页扫描器 - 增量更新模式"""
    
    def __init__(self):
        self.config = self._detect_system()
        self._stop_flag = False
        self._scan_thread = None
        self._current_progress = self._load_progress()
        
    def _detect_system(self) -> SystemConfig:
        """检测当前系统环境"""
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
            logger.error(f"系统检测失败: {e}")
        
        return config
    
    def _load_progress(self) -> Dict:
        """加载扫描进度"""
        if os.path.exists(SCAN_PROGRESS_PATH):
            try:
                with open(SCAN_PROGRESS_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载扫描进度失败: {e}")
        return {"scanned": [], "last_index": 0, "total_commands": 0}
    
    def _save_progress(self):
        """保存扫描进度"""
        try:
            with open(SCAN_PROGRESS_PATH, 'w', encoding='utf-8') as f:
                json.dump(self._current_progress, f, indent=2)
        except Exception as e:
            logger.error(f"保存扫描进度失败: {e}")
    
    def _load_builtin_commands(self) -> Dict:
        """加载内置 cmd.json 中的命令数据"""
        if os.path.exists(BUILTIN_CMD_JSON):
            try:
                with open(BUILTIN_CMD_JSON, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载内置命令文件失败: {e}")
        return {}
    
    def _load_existing_commands(self) -> Dict:
        """加载已存在的命令数据（合并内置命令）"""
        commands = {}

        # 先加载内置命令作为基础
        try:
            commands = self._load_builtin_commands()
        except Exception as e:
            logger.error(f"加载内置命令失败: {e}")

        # 加载已保存的命令文件，合并到内置命令上（已保存的优先级更高）
        if os.path.exists(COMMAND_JSON_PATH):
            try:
                with open(COMMAND_JSON_PATH, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
            except Exception as e:
                logger.error(f"加载命令文件失败: {e}")
                saved = {}

            for cmd, info in saved.items():
                if cmd in commands:
                    existing_opts = set(commands[cmd].get("options", []))
                    new_opts = set(info.get("options", []))
                    existing_opts.update(new_opts)
                    commands[cmd]["options"] = sorted(existing_opts)
                    existing_sub = set(commands[cmd].get("subcommands", []))
                    new_sub = set(info.get("subcommands", []))
                    existing_sub.update(new_sub)
                    commands[cmd]["subcommands"] = sorted(existing_sub)
                else:
                    commands[cmd] = info
        else:
            # 首次运行：将内置命令写入 command.json
            if commands:
                try:
                    self._save_commands(commands)
                except Exception as e:
                    logger.error(f"初始化 command.json 失败: {e}")

        return commands

    def _save_commands(self, commands: Dict):
        """保存命令数据（原子写入：先写 .tmp 再替换，崩溃不丢数据）"""
        tmp_path = COMMAND_JSON_PATH + ".tmp"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(commands, f, indent=2, ensure_ascii=False, sort_keys=True)
            os.replace(tmp_path, COMMAND_JSON_PATH)
        except Exception as e:
            logger.error(f"保存命令数据失败: {e}")
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    
    def find_manpage_files(self, cmd_name: str) -> List[Path]:
        """查找命令的手册页文件"""
        manpage_paths = []
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
                            if file.startswith(f"{cmd_name}."):
                                manpage_paths.append(Path(man_section_dir) / file)
                                break
                    except (PermissionError, OSError):
                        continue
        except Exception as e:
            logger.error(f"查找手册页失败 {cmd_name}: {e}")
        return manpage_paths
    
    def read_manpage_content(self, manpage_path: Path) -> str:
        """读取手册页内容"""
        try:
            if str(manpage_path).endswith('.gz'):
                with gzip.open(manpage_path, 'rt', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            else:
                with open(manpage_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
        except Exception as e:
            logger.error(f"读取手册页失败 {manpage_path}: {e}")
            return ""
    
    def parse_options_from_roff(self, content: str) -> Set[str]:
        """从 roff 格式中解析选项"""
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
            logger.error(f"解析选项失败: {e}")
        
        return options
    
    def extract_options_quick(self, cmd: str) -> List[str]:
        """快速提取选项"""
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
            logger.error(f"提取选项失败 {cmd}: {e}")
        return sorted(options)
    
    def get_all_commands(self) -> List[str]:
        """获取系统中的所有命令"""
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
            logger.error(f"获取命令列表失败: {e}")
        
        return sorted(commands)
    
    def start_background_scan(self):
        """启动后台扫描线程（非阻塞）"""
        if self._scan_thread and self._scan_thread.is_alive():
            return
        
        self._stop_flag = False
        self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._scan_thread.start()
    
    def stop_scan(self):
        """停止扫描"""
        self._stop_flag = True
        if self._scan_thread:
            self._scan_thread.join(timeout=2)
    
    def _scan_loop(self):
        """后台扫描循环 —— 在现有缓存上增量，不重复扫描已有选项的命令"""
        try:
            # 加载已有命令（内置 + 已扫描缓存）
            existing = self._load_existing_commands()
            all_commands = self.get_all_commands()

            # 只扫描新命令，以及缓存中尚无选项的命令（首次补充扫描）
            to_scan = [
                cmd for cmd in all_commands
                if cmd not in existing or not existing[cmd].get("options")
            ]

            self._current_progress["total_commands"] = len(all_commands)
            self._save_progress()

            for i, cmd in enumerate(to_scan):
                if self._stop_flag:
                    break

                try:
                    options = self.extract_options_quick(cmd)
                    if options:
                        if cmd in existing:
                            existing_opts = set(existing[cmd].get("options", []))
                            existing_opts.update(options)
                            existing[cmd]["options"] = sorted(existing_opts)
                            if "subcommands" not in existing[cmd]:
                                existing[cmd]["subcommands"] = []
                        else:
                            existing[cmd] = {"subcommands": [], "options": options}
                    else:
                        if cmd not in existing:
                            existing[cmd] = {"subcommands": [], "options": []}
                except Exception as e:
                    logger.error(f"扫描命令失败 {cmd}: {e}")

                self._current_progress["scanned"] = list(existing.keys())
                self._current_progress["last_index"] = i + 1

                # 每处理一个命令就立即保存
                self._save_commands(existing)
                self._save_progress()

            # 扫描完成，清理进度文件
            if os.path.exists(SCAN_PROGRESS_PATH):
                try:
                    os.remove(SCAN_PROGRESS_PATH)
                except Exception as e:
                    logger.error(f"删除进度文件失败: {e}")
        except Exception as e:
            logger.error(f"扫描循环失败: {e}")


_scanner: Optional[AsyncManScanner] = None


def get_scanner() -> AsyncManScanner:
    """获取全局扫描器实例"""
    global _scanner
    if _scanner is None:
        _scanner = AsyncManScanner()
    return _scanner


def start_background_scan():
    """启动后台扫描（供 Onyx.py 调用）"""
    scanner = get_scanner()
    scanner.start_background_scan()


def incremental_update():
    """增量更新（仅扫描新命令）"""
    scanner = get_scanner()
    scanner.start_background_scan()


def main():
    parser = argparse.ArgumentParser(description="跨平台命令扫描器")
    parser.add_argument("--force", action="store_true", help="强制重新扫描")
    parser.add_argument("--background", action="store_true", help="后台模式（静默运行）")
    args = parser.parse_args()
    
    # 后台模式：降低优先级并静默输出
    if args.background:
        if hasattr(os, 'nice'):
            try:
                os.nice(19)
            except Exception:
                pass
        # 重定向标准输出和错误到 null
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')
        # 禁用 logging 输出
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
    
    # 等待扫描完成（后台模式不等待）
    if not args.background:
        try:
            if scanner._scan_thread:
                scanner._scan_thread.join()
        except KeyboardInterrupt:
            scanner.stop_scan()


if __name__ == "__main__":
    main()