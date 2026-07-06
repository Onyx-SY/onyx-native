# lib/terminal/input_lib.py
"""
输入处理核心模块 - 现代化增强版
提供命令输入、历史记录去重、上下键导航、路径补全等功能
支持彩色元数据、命令/子命令补全、绝对路径补全
新增：语法高亮、命令分隔符处理、补全问题修复
新增：实时错误标红、路径存在校验、引号匹配、变量高亮、频率排序、前缀历史搜索
新增：历史持久化（异步、无上限文件、内存限制1000条）
新增：命令缓存（异步）、Ctrl+上下键补全选择
修复：上下键历史导航前缀匹配及连续切换问题
修复：用户手动编辑后导航状态重置问题
修复：使用索引追踪历史位置，解决重复命令问题
修复：前缀导航时前缀固定不变
修复：命令频率更新问题，改为异步加载和保存
新增：多行命令输入支持（here document、if/fi、for/do 等），Pygments 语法高亮
新增：here-document 支持 #语法 切换（如 #python、#bash）
修复：多行命令历史导航显示问题，正确格式化换行
修复：历史导航中多行命令一次性完整显示，而非逐行显示
修复：多行输入模式下语法高亮和补全失效问题
修复：历史记录存储格式问题，使用实际换行符而非 ^J 转义
修复：历史导航显示 ^J 混乱问题，统一转义字符处理
新增：终端类型适配，加载 other_terminal_cmd.json
新增：CMD 多行输入支持（IF/FOR/ELSE 块结构）
新增：com_cmd.json 路径参数传递，支持选项和参数补全
新增：空格保留补全修复、鼠标支持（仅在补全菜单激活时接管）
新增：配置文件系统
修复：多行输入中语法切换被覆盖，始终使用同一补全器/高亮器
修复：嵌套多行结构误判，增加深度栈管理
新增：补全菜单翻页键绑定支持
修复：鼠标滚动过度接管问题，移除全局 mouse_support，仅保留键盘导航
修复：多行输入自动缩进逻辑，基于 Pygments 词法分析实现智能缩进
修复：多行模式下语法检测与切换，充分利用 SmartSyntaxDetector
"""

import os
import sys
import time
import uuid
import json
import threading
import re
import shlex
import queue
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple, Union, Iterable

from prompt_toolkit import prompt
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PromptStyle
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion, AutoSuggestFromHistory
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.validation import Validator, ValidationError

# 导入拆分的模块
from .kb import create_key_bindings
from .com import (
    set_history_highlight_token, clear_history_highlight_token,
    set_nav_reset_callback,
    get_path_cache,
    PathCache,
    SmartCompleter,
    CommandConfigLoader,
    CommandLexer,
    FirstSuggestionAutoSuggest,
    get_command_cache,
    get_command_freq,
    get_detected_terminal_type,
    set_terminal_type,
    set_other_terminal_cmds_path,
    get_other_terminal_cmds,
    set_com_cmd_config_path,
    get_com_cmd_config,
    get_posix_mode,
    COLORS,
    META_COLORS,
    META_TEXTS_EN,
    LANG_TEXTS,
    load_ptk_config,
    DEFAULT_PTK_CONFIG,
    PTK_CONFIG_PATH,
)

# 导入多行输入模块
from .mul_line import (
    handle_multiline_input,
    detect_syntax_from_command,
    MultiLineDetector,
    MultiLineState,
    MultiLineInput,
    MultiLineFormatter,
    SyntaxDetector,
    SyntaxType,
    SmartSyntaxDetector,
    HAS_PYGMENTS,
)

# ===================== 全局变量 =====================
_HISTORY_BUFFER: List[str] = []          # 历史记录列表（按时间倒序，最新的在索引0）
_CURRENT_HISTORY_INDEX: int = -1          # 普通导航：当前在 _HISTORY_BUFFER 中的索引
_NAVIGATION_START_INPUT: str = ""         # 普通导航：开始导航时的原始输入
_HISTORY_INITIALIZED: bool = False
_CURRENT_LANG: str = "chinese"

# 前缀导航专用状态
_PREFIX_NAVIGATION_ACTIVE: bool = False   # 前缀导航是否激活
_PREFIX_VALUE: str = ""                   # 固定的前缀值（导航过程中不变）
_PREFIX_FILTERED_INDICES: List[int] = []  # 匹配前缀的历史记录在 _HISTORY_BUFFER 中的索引
_PREFIX_CURRENT_POS: int = -1             # 当前在 _PREFIX_FILTERED_INDICES 中的位置

# 多行输入状态
_MULTILINE_STATE: Optional[MultiLineState] = None  # 当前多行输入状态
_MULTILINE_BUFFER: List[str] = []                  # 多行输入缓冲
_MULTILINE_ACTIVE: bool = False                    # 多行输入是否激活

# 虚拟根目录（从主程序注入）
_VIRTUAL_ROOT: str = ""

# 命令补全配置缓存
_CMD_CONFIG_CACHE: Dict[str, Dict] = {}

# 有效命令集合（用于实时校验）
_VALID_COMMANDS: set = set()

# 用户主目录（用于持久化）
_USER_HOME_DIR: str = ""

# 历史持久化配置
_HISTORY_FILE_NAME = ".onyx_history.txt"
_HISTORY_MAX_MEMORY = 1000          # 内存中保留条数
_HISTORY_MAX_FILE = 50000           # 文件保留最大行数（后台整理）

# 历史文件中多行命令的分隔符
_HISTORY_MULTILINE_SEPARATOR = "\x00"  # 使用 null 字符作为多行命令分隔符（在历史文件中不可见）

# 异步写入队列和线程
_history_write_queue = queue.Queue()
_history_writer_thread: Optional[threading.Thread] = None
_history_writer_stop = threading.Event()

# 新增：终端类型检测
_TERMINAL_TYPE: str = ""

# com_cmd.json 配置路径
_COM_CMD_CONFIG_PATH: str = ""

# ptk 配置（从 ~/.config/onyx/ptk.json 加载）
_ptk_config: Dict[str, Any] = {}

# 元信息文本
META_TEXTS = META_TEXTS_EN

def _ensure_ptk_config() -> None:
    global _ptk_config
    _ptk_config = load_ptk_config()
    # 应用配置覆盖
    global _HISTORY_MAX_MEMORY, _HISTORY_MAX_FILE, _HISTORY_FILE_NAME
    history_cfg = _ptk_config.get("history", {})
    if "memory_limit" in history_cfg:
        _HISTORY_MAX_MEMORY = history_cfg["memory_limit"]
    if "file_limit" in history_cfg:
        _HISTORY_MAX_FILE = history_cfg["file_limit"]
    if "file_name" in history_cfg:
        _HISTORY_FILE_NAME = history_cfg["file_name"]

def set_language(lang: str) -> None:
    """设置语言"""
    global _CURRENT_LANG, META_TEXTS
    if lang.lower() in ["chinese", "english"]:
        _CURRENT_LANG = lang.lower()
        META_TEXTS = META_TEXTS_EN

def get_text(key: str) -> str:
    """获取本地化文本"""
    return LANG_TEXTS.get(_CURRENT_LANG, LANG_TEXTS["chinese"]).get(key, key)

def set_virtual_root(root: str) -> None:
    global _VIRTUAL_ROOT
    _VIRTUAL_ROOT = root

def get_virtual_root() -> str:
    return _VIRTUAL_ROOT

def set_valid_commands(cmds: Iterable[str]) -> None:
    global _VALID_COMMANDS
    _VALID_COMMANDS = set(cmds)

def set_user_home_dir(home_dir: str) -> None:
    global _USER_HOME_DIR
    _USER_HOME_DIR = home_dir
    history_file = os.path.join(home_dir, _HISTORY_FILE_NAME) if home_dir else None
    freq_mgr = get_command_freq(home_dir, history_file)
    freq_mgr.set_user_home_dir(home_dir, history_file)

def set_com_cmd_config_path_from_root(root_dir: str) -> None:
    """
    根据虚拟根目录设置 com_cmd.json 路径
    """
    global _COM_CMD_CONFIG_PATH
    _COM_CMD_CONFIG_PATH = os.path.join(root_dir, "onyx", "etc", "com_cmd.json")
    # 同时设置到 com 模块
    set_com_cmd_config_path(_COM_CMD_CONFIG_PATH)

def get_com_cmd_config_path() -> str:
    """获取 com_cmd.json 路径"""
    global _COM_CMD_CONFIG_PATH
    return _COM_CMD_CONFIG_PATH

def detect_and_set_terminal_type() -> str:
    """检测并设置终端类型，同时加载对应的命令"""
    global _TERMINAL_TYPE
    _TERMINAL_TYPE = get_detected_terminal_type()
    return _TERMINAL_TYPE

def get_terminal_type() -> str:
    """获取当前终端类型"""
    global _TERMINAL_TYPE
    if not _TERMINAL_TYPE:
        _TERMINAL_TYPE = get_detected_terminal_type()
    return _TERMINAL_TYPE

def _get_terminal_specific_commands() -> List[str]:
    """从 other_terminal_cmd.json 获取当前终端的内置命令"""
    terminal_type = get_terminal_type()
    all_cmds = get_other_terminal_cmds()
    
    commands = []
    # 获取终端专属命令
    if terminal_type in all_cmds:
        commands.extend(all_cmds[terminal_type])
    
    # 获取通用命令
    if 'common' in all_cmds:
        commands.extend(all_cmds['common'])
    
    return commands

# ===================== 异步历史持久化系统 =====================
def _get_history_file_path() -> str:
    if _USER_HOME_DIR:
        return os.path.join(_USER_HOME_DIR, _HISTORY_FILE_NAME)
    return os.path.join(str(Path.home()), _HISTORY_FILE_NAME)

def _start_history_writer():
    global _history_writer_thread, _history_writer_stop
    if _history_writer_thread and _history_writer_thread.is_alive():
        return
    _history_writer_stop.clear()
    _history_writer_thread = threading.Thread(target=_history_writer_loop, daemon=True)
    _history_writer_thread.start()

def _encode_multiline_for_storage(cmd: str) -> str:
    """
    将多行命令编码为单行存储格式。
    使用 \x00 (null字符) 作为换行符的替代，因为：
    1. null 字符在终端输入中几乎不可能出现
    2. 不会被误认为是文件换行
    """
    if '\n' in cmd:
        return cmd.replace('\n', _HISTORY_MULTILINE_SEPARATOR)
    return cmd

def _decode_multiline_from_storage(line: str) -> str:
    """
    将从文件读取的单行解码，恢复多行命令。
    支持多种格式：
    1. 旧的 ^J 转义格式（字面字符串 ^J）
    2. 旧的 \n 转义格式（反斜杠+n）
    3. 新的 null 字符格式
    """
    if not line:
        return line
    
    if '^J' in line:
        line = line.replace('^J', '\n')
    
    if '\\n' in line:
        line = line.replace('\\n', '\n')
    
    if _HISTORY_MULTILINE_SEPARATOR in line:
        line = line.replace(_HISTORY_MULTILINE_SEPARATOR, '\n')
    
    return line

def _clean_display_text(cmd: str) -> str:
    """
    清理显示文本，确保所有转义字符都被正确处理为实际的控制字符。
    同时调用 MultiLineFormatter 进行格式化解码。
    """
    if not cmd:
        return cmd
    
    # 先通过 MultiLineFormatter 解码（处理历史文件中的 null 分隔符等）
    result = MultiLineFormatter.decode_history_command(cmd)
    
    replacements = [
        ('^J', '\n'),
        ('^M', '\r'),
        ('^I', '\t'),
        ('\\n', '\n'),
        ('\\r', '\r'),
        ('\\t', '\t'),
    ]
    
    for old, new in replacements:
        if old in result:
            result = result.replace(old, new)
    
    return result

def _history_writer_loop():
    batch = []
    file_path = _get_history_file_path()
    last_flush = time.time()
    while not _history_writer_stop.is_set():
        try:
            cmd = _history_write_queue.get(timeout=0.5)
            batch.append(cmd)
            while True:
                try:
                    cmd = _history_write_queue.get_nowait()
                    batch.append(cmd)
                except queue.Empty:
                    break
        except queue.Empty:
            pass

        now = time.time()
        if batch and (len(batch) >= 10 or now - last_flush > 2.0):
            try:
                with open(file_path, 'a', encoding='utf-8') as f:
                    for cmd in batch:
                        encoded = _encode_multiline_for_storage(cmd)
                        f.write(encoded + '\n')
                batch.clear()
                last_flush = now
                threading.Thread(target=_trim_history_file, daemon=True).start()
            except Exception:
                pass

def _trim_history_file():
    file_path = _get_history_file_path()
    max_lines = _HISTORY_MAX_FILE
    try:
        if not os.path.exists(file_path):
            return
        with open(file_path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            block_size = 8192
            data = []
            remaining = file_size
            while remaining > 0:
                seek_size = min(block_size, remaining)
                f.seek(remaining - seek_size, os.SEEK_SET)
                chunk = f.read(seek_size)
                data.insert(0, chunk)
                remaining -= seek_size
            content = b''.join(data).decode('utf-8', errors='ignore')
            all_lines = content.splitlines()
            lines_to_keep = all_lines[-max_lines:] if len(all_lines) > max_lines else all_lines
        if len(all_lines) > max_lines:
            temp_file = file_path + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines_to_keep) + '\n')
            os.replace(temp_file, file_path)
    except Exception:
        pass

def _load_history_buffer() -> List[str]:
    """加载历史记录，正确解码多行命令"""
    file_path = _get_history_file_path()
    old_json_path = os.path.join(os.path.dirname(file_path), ".prompt_onyx_cmd_history.json")
    
    if not os.path.exists(file_path) and os.path.exists(old_json_path):
        try:
            with open(old_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    migrated = []
                    for item in data:
                        if isinstance(item, str) and item.strip():
                            migrated.append(item)
                    if migrated:
                        with open(file_path, 'w', encoding='utf-8') as nf:
                            for cmd in migrated:
                                encoded = _encode_multiline_for_storage(cmd)
                                nf.write(encoded + '\n')
        except Exception:
            pass

    try:
        if not os.path.exists(file_path):
            return []
        
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()
        
        if len(all_lines) > _HISTORY_MAX_MEMORY:
            all_lines = all_lines[-_HISTORY_MAX_MEMORY:]
        
        result = []
        for line in reversed(all_lines):
            line = line.strip()
            if line:
                decoded = _decode_multiline_from_storage(line)
                cleaned = _clean_display_text(decoded)
                result.append(cleaned)
        
        return result
    except Exception:
        return []

def _save_history_buffer_async(cmd: str):
    global _history_writer_thread
    if not cmd:
        return
    _start_history_writer()
    _history_write_queue.put(cmd)

# ===================== 历史导航（修复版 - 使用索引追踪） =====================
def init_history_navigation() -> None:
    """初始化历史导航，并触发命令频率管理器从历史文件中预加载"""
    global _HISTORY_BUFFER, _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT, _HISTORY_INITIALIZED
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS
    
    if not _HISTORY_INITIALIZED:
        _HISTORY_BUFFER = _load_history_buffer()
        _HISTORY_BUFFER = [_clean_display_text(cmd) if any(x in cmd for x in ['^J', '\\n', '^M', '\\r']) else cmd 
                          for cmd in _HISTORY_BUFFER]
    
    _CURRENT_HISTORY_INDEX = -1
    _NAVIGATION_START_INPUT = ""
    _PREFIX_NAVIGATION_ACTIVE = False
    _PREFIX_VALUE = ""
    _PREFIX_FILTERED_INDICES = []
    _PREFIX_CURRENT_POS = -1
    _HISTORY_INITIALIZED = True
    
    # 注册导航重置回调：lexer 自动清除高亮时同步重置导航状态
    set_nav_reset_callback(reset_history_index)

    if _USER_HOME_DIR:
        history_file = os.path.join(_USER_HOME_DIR, _HISTORY_FILE_NAME)
        freq_mgr = get_command_freq(_USER_HOME_DIR, history_file)

def add_to_history(cmd: str) -> bool:
    """添加命令到历史记录"""
    global _HISTORY_BUFFER
    cmd_stripped = cmd.strip()
    if not cmd_stripped:
        return False
    
    if '^J' in cmd_stripped:
        cmd_stripped = cmd_stripped.replace('^J', '\n')
    if '\\n' in cmd_stripped:
        cmd_stripped = cmd_stripped.replace('\\n', '\n')
    
    if _HISTORY_BUFFER and _HISTORY_BUFFER[0] == cmd_stripped:
        return False
    
    if cmd_stripped in _HISTORY_BUFFER:
        _HISTORY_BUFFER.remove(cmd_stripped)
    
    _HISTORY_BUFFER.insert(0, cmd_stripped)
    
    if len(_HISTORY_BUFFER) > _HISTORY_MAX_MEMORY:
        _HISTORY_BUFFER = _HISTORY_BUFFER[:_HISTORY_MAX_MEMORY]
    
    _save_history_buffer_async(cmd_stripped)
    
    freq_manager = get_command_freq(
        _USER_HOME_DIR,
        os.path.join(_USER_HOME_DIR, _HISTORY_FILE_NAME) if _USER_HOME_DIR else None
    )
    freq_manager.record(cmd_stripped)
    return True

def _find_history_index_by_content(content: str, start_from: int = 0) -> int:
    for i in range(start_from, len(_HISTORY_BUFFER)):
        if _HISTORY_BUFFER[i] == content:
            return i
    return -1

def _build_prefix_filtered_indices(prefix: str) -> List[int]:
    """基于 token 匹配（任意位置子串）+ 按命令文本去重 — 用于 Up/Down 裸键"""
    indices = []
    seen = set()
    for i, cmd in enumerate(_HISTORY_BUFFER):
        if prefix in cmd and cmd not in seen:
            seen.add(cmd)
            indices.append(i)
    return indices

def _build_strict_prefix_filtered_indices(prefix: str) -> List[int]:
    """严格前缀匹配 + 按命令文本去重 — 用于 Alt+Up/Down"""
    indices = []
    seen = set()
    for i, cmd in enumerate(_HISTORY_BUFFER):
        if cmd.startswith(prefix) and cmd not in seen:
            seen.add(cmd)
            indices.append(i)
    return indices

# ── ANSI 反色高亮（swap fg/bg = 白框效果）──
_HL_START = "\033[7m"   # reverse video
# ── 高亮改为 ptk CommandLexer 方案（set_history_highlight_token），不再嵌入 ANSI ──

# ── 历史导航匹配信息（供 kb.py 底部工具栏使用）──
_NAV_MATCH_INFO: str = ""  # 如 "匹配: nmap (token: a) — 第 2/5 项"

def _get_nav_match_info() -> str:
    """返回当前历史导航的匹配信息，供底部工具栏展示"""
    return _NAV_MATCH_INFO

def _set_nav_match_info(token: str, current: int, total: int) -> None:
    """设置历史导航匹配信息"""
    global _NAV_MATCH_INFO
    if token and total > 0:
        _NAV_MATCH_INFO = f"🔍 \"{token}\" — {current}/{total}"
    else:
        _NAV_MATCH_INFO = ""

def _format_history_for_display(cmd: str) -> str:
    """格式化历史命令用于显示（纯文本，高亮由 ptk CommandLexer 的 set_history_highlight_token 负责）
    
    单行 prompt 模式下 \\n 会被 ptk 渲染为 ^J，因此统一替换为空格。
    """
    if not cmd:
        return cmd
    
    cleaned = _clean_display_text(cmd)
    
    if '\n' in cleaned:
        return cleaned.replace('\n', ' ')
    
    if '^J' in cmd:
        cleaned = cmd.replace('^J', '\n')
        return cleaned.replace('\n', ' ')
    
    if MultiLineFormatter.is_multiline_command(cmd):
        formatted = MultiLineFormatter.format_multiline_command(cmd)
        return formatted.replace('\n', ' ') if '\n' in formatted else formatted
    
    return cmd

def handle_up_arrow_normal(current_input: str) -> Tuple[str, int]:
    """处理普通 Up 键：空输入→线性遍历全部历史；有文字→子串匹配筛选 + ANSI 反色高亮"""
    global _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT, _HISTORY_BUFFER
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS
    
    if not _HISTORY_BUFFER:
        return current_input, len(current_input)
    
    _token = current_input.strip()
    
    # ── 已在导航中 + 用户未手动编辑 → 继续当前模式 ──
    if _CURRENT_HISTORY_INDEX != -1:
        if _CURRENT_HISTORY_INDEX < len(_HISTORY_BUFFER) and current_input == _HISTORY_BUFFER[_CURRENT_HISTORY_INDEX]:
            if _PREFIX_NAVIGATION_ACTIVE and _PREFIX_FILTERED_INDICES:
                # 继续子串筛选（高亮用原始 token）
                set_history_highlight_token(_PREFIX_VALUE)
                if _PREFIX_CURRENT_POS < len(_PREFIX_FILTERED_INDICES) - 1:
                    _PREFIX_CURRENT_POS += 1
                else:
                    return current_input, len(current_input)
                formatted = _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]])
                return formatted, len(formatted)
            else:
                # 继续线性遍历
                if _CURRENT_HISTORY_INDEX < len(_HISTORY_BUFFER) - 1:
                    _CURRENT_HISTORY_INDEX += 1
                else:
                    return current_input, len(current_input)
                formatted = _format_history_for_display(_HISTORY_BUFFER[_CURRENT_HISTORY_INDEX])
                return formatted, len(formatted)
        else:
            # 用户手动编辑了 → 重置所有状态
            _CURRENT_HISTORY_INDEX = -1
            _NAVIGATION_START_INPUT = ""
            _PREFIX_NAVIGATION_ACTIVE = False
            _PREFIX_VALUE = ""
            _PREFIX_FILTERED_INDICES = []
            _PREFIX_CURRENT_POS = -1
    
    # ── 全新开始 ──
    if not _token:
        # 空输入 → 原版线性遍历
        clear_history_highlight_token()
        
        if _CURRENT_HISTORY_INDEX == -1:
            _NAVIGATION_START_INPUT = current_input
            idx = _find_history_index_by_content(current_input)
            if idx != -1 and idx < len(_HISTORY_BUFFER) - 1:
                _CURRENT_HISTORY_INDEX = idx + 1
            else:
                _CURRENT_HISTORY_INDEX = 0
        else:
            if _CURRENT_HISTORY_INDEX < len(_HISTORY_BUFFER) - 1:
                _CURRENT_HISTORY_INDEX += 1
            else:
                return current_input, len(current_input)
        formatted = _format_history_for_display(_HISTORY_BUFFER[_CURRENT_HISTORY_INDEX])
        return formatted, len(formatted)
    
    # 有文字 → filtered-list 子串匹配
    if not _PREFIX_NAVIGATION_ACTIVE:
        _PREFIX_NAVIGATION_ACTIVE = True
        _PREFIX_VALUE = _token
        _PREFIX_FILTERED_INDICES = _build_prefix_filtered_indices(_token)
        set_history_highlight_token(_PREFIX_VALUE)
        
        if not _PREFIX_FILTERED_INDICES:
            return current_input, len(current_input)
        
        _PREFIX_CURRENT_POS = 0
        # 如果当前输入恰好是第一个匹配，且还有更多匹配 → 跳到第二个
        if _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[0]] == current_input and len(_PREFIX_FILTERED_INDICES) > 1:
            _PREFIX_CURRENT_POS = 1
        
        formatted = _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]])
        return formatted, len(formatted)
    
    # 继续筛选列表（高亮始终用原始搜索 token _PREFIX_VALUE，而非当前缓冲区文字）
    set_history_highlight_token(_PREFIX_VALUE)
    if _PREFIX_CURRENT_POS < len(_PREFIX_FILTERED_INDICES) - 1:
        _PREFIX_CURRENT_POS += 1
        formatted = _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]])
        return formatted, len(formatted)
    return current_input, len(current_input)


def handle_down_arrow_normal(current_input: str) -> Tuple[str, int]:
    """处理普通 Down 键：空输入→线性遍历全部历史（反向）；有文字→子串匹配筛选 + ANSI 反色高亮（反向）"""
    global _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT, _HISTORY_BUFFER
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS
    
    if not _HISTORY_BUFFER:
        return current_input, len(current_input)
    
    _token = current_input.strip()
    
    # ── 已在导航中 + 用户未手动编辑 → 继续当前模式 ──
    if _CURRENT_HISTORY_INDEX != -1:
        if _CURRENT_HISTORY_INDEX < len(_HISTORY_BUFFER) and current_input == _HISTORY_BUFFER[_CURRENT_HISTORY_INDEX]:
            if _PREFIX_NAVIGATION_ACTIVE and _PREFIX_FILTERED_INDICES:
                set_history_highlight_token(_PREFIX_VALUE)
                if _PREFIX_CURRENT_POS > 0:
                    _PREFIX_CURRENT_POS -= 1
                elif _PREFIX_CURRENT_POS == 0:
                    _PREFIX_NAVIGATION_ACTIVE = False
                    _PREFIX_VALUE = ""
                    _PREFIX_FILTERED_INDICES = []
                    _PREFIX_CURRENT_POS = -1
                    clear_history_highlight_token()
                    return current_input, len(current_input)
                else:
                    return current_input, len(current_input)
                formatted = _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]])
                return formatted, len(formatted)
            else:
                if _CURRENT_HISTORY_INDEX > 0:
                    _CURRENT_HISTORY_INDEX -= 1
                elif _CURRENT_HISTORY_INDEX == 0:
                    _CURRENT_HISTORY_INDEX = -1
                    original = _NAVIGATION_START_INPUT
                    _NAVIGATION_START_INPUT = ""
                    return original, len(original)
                else:
                    return current_input, len(current_input)
                formatted = _format_history_for_display(_HISTORY_BUFFER[_CURRENT_HISTORY_INDEX])
                return formatted, len(formatted)
        else:
            _CURRENT_HISTORY_INDEX = -1
            _NAVIGATION_START_INPUT = ""
            _PREFIX_NAVIGATION_ACTIVE = False
            _PREFIX_VALUE = ""
            _PREFIX_FILTERED_INDICES = []
            _PREFIX_CURRENT_POS = -1
    
    # ── 全新开始 ──
    if not _token:
        clear_history_highlight_token()
        if _CURRENT_HISTORY_INDEX == -1:
            return current_input, len(current_input)
        if _CURRENT_HISTORY_INDEX < len(_HISTORY_BUFFER):
            if current_input != _HISTORY_BUFFER[_CURRENT_HISTORY_INDEX]:
                _CURRENT_HISTORY_INDEX = -1
                _NAVIGATION_START_INPUT = ""
                return current_input, len(current_input)
        if _CURRENT_HISTORY_INDEX > 0:
            _CURRENT_HISTORY_INDEX -= 1
            formatted = _format_history_for_display(_HISTORY_BUFFER[_CURRENT_HISTORY_INDEX])
            return formatted, len(formatted)
        elif _CURRENT_HISTORY_INDEX == 0:
            _CURRENT_HISTORY_INDEX = -1
            original = _NAVIGATION_START_INPUT
            _NAVIGATION_START_INPUT = ""
            return original, len(original)
        return current_input, len(current_input)
    
    # 有文字 → filtered-list 子串匹配（反向）
    if not _PREFIX_NAVIGATION_ACTIVE:
        _PREFIX_NAVIGATION_ACTIVE = True
        _PREFIX_VALUE = _token
        _PREFIX_FILTERED_INDICES = _build_prefix_filtered_indices(_token)
        set_history_highlight_token(_PREFIX_VALUE)
        
        if not _PREFIX_FILTERED_INDICES:
            return current_input, len(current_input)
        
        _PREFIX_CURRENT_POS = len(_PREFIX_FILTERED_INDICES) - 1
        formatted = _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]])
        return formatted, len(formatted)
    
    # 继续筛选列表（高亮始终用原始 token）
    set_history_highlight_token(_PREFIX_VALUE)
    if _PREFIX_CURRENT_POS > 0:
        _PREFIX_CURRENT_POS -= 1
        formatted = _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]])
        return formatted, len(formatted)
    elif _PREFIX_CURRENT_POS == 0:
        _PREFIX_NAVIGATION_ACTIVE = False
        _PREFIX_VALUE = ""
        _PREFIX_FILTERED_INDICES = []
        _PREFIX_CURRENT_POS = -1
        clear_history_highlight_token()
        return current_input, len(current_input)
    return current_input, len(current_input)

def handle_up_arrow_with_prefix(current_input: str) -> Tuple[str, int]:
    """处理 Alt+Up：基于前缀的历史导航"""
    global _HISTORY_BUFFER
    global _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS
    
    _CURRENT_HISTORY_INDEX = -1
    _NAVIGATION_START_INPUT = ""
    
    # 如果当前 PREFIX 状态是普通 Up/Down 留下的（token 是子串而非前缀），重置
    if _PREFIX_NAVIGATION_ACTIVE and _PREFIX_VALUE and not current_input.startswith(_PREFIX_VALUE):
        _PREFIX_NAVIGATION_ACTIVE = False
        _PREFIX_VALUE = ""
        _PREFIX_FILTERED_INDICES = []
        _PREFIX_CURRENT_POS = -1
    
    if not _PREFIX_NAVIGATION_ACTIVE:
        prefix = current_input.strip()
        if not prefix:
            return handle_up_arrow_normal(current_input)
        
        _PREFIX_NAVIGATION_ACTIVE = True
        _PREFIX_VALUE = prefix
        _PREFIX_FILTERED_INDICES = _build_strict_prefix_filtered_indices(prefix)
        
        if not _PREFIX_FILTERED_INDICES:
            return current_input, len(current_input)
        
        for pos, idx in enumerate(_PREFIX_FILTERED_INDICES):
            if _HISTORY_BUFFER[idx] == current_input:
                if pos < len(_PREFIX_FILTERED_INDICES) - 1:
                    _PREFIX_CURRENT_POS = pos + 1
                    formatted = _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]])
                    return formatted, len(formatted)
                else:
                    _PREFIX_CURRENT_POS = pos
                    return current_input, len(current_input)
        
        _PREFIX_CURRENT_POS = 0
        formatted = _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[0]])
        return formatted, len(formatted)
    
    if _PREFIX_CURRENT_POS >= 0 and _PREFIX_CURRENT_POS < len(_PREFIX_FILTERED_INDICES):
        expected_idx = _PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]
        expected_cmd = _HISTORY_BUFFER[expected_idx]
        if current_input != expected_cmd:
            if current_input.startswith(_PREFIX_VALUE):
                for pos, idx in enumerate(_PREFIX_FILTERED_INDICES):
                    if _HISTORY_BUFFER[idx] == current_input:
                        _PREFIX_CURRENT_POS = pos
                        break
            else:
                _PREFIX_NAVIGATION_ACTIVE = False
                _PREFIX_VALUE = ""
                _PREFIX_FILTERED_INDICES = []
                _PREFIX_CURRENT_POS = -1
                clear_history_highlight_token()
                return current_input, len(current_input)
    
    if _PREFIX_CURRENT_POS < len(_PREFIX_FILTERED_INDICES) - 1:
        _PREFIX_CURRENT_POS += 1
        formatted = _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]])
        return formatted, len(formatted)
    else:
        return current_input, len(current_input)

def handle_down_arrow_with_prefix(current_input: str) -> Tuple[str, int]:
    """处理 Alt+Down：基于前缀的历史导航（反向）"""
    global _HISTORY_BUFFER
    global _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS
    
    _CURRENT_HISTORY_INDEX = -1
    _NAVIGATION_START_INPUT = ""
    
    if _PREFIX_NAVIGATION_ACTIVE and _PREFIX_VALUE and not current_input.startswith(_PREFIX_VALUE):
        _PREFIX_NAVIGATION_ACTIVE = False
        _PREFIX_VALUE = ""
        _PREFIX_FILTERED_INDICES = []
        _PREFIX_CURRENT_POS = -1
    
    if not _PREFIX_NAVIGATION_ACTIVE:
        prefix = current_input.strip()
        if not prefix:
            return current_input, len(current_input)
        
        _PREFIX_NAVIGATION_ACTIVE = True
        _PREFIX_VALUE = prefix
        _PREFIX_FILTERED_INDICES = _build_strict_prefix_filtered_indices(prefix)
        
        if not _PREFIX_FILTERED_INDICES:
            return current_input, len(current_input)
        
        _PREFIX_CURRENT_POS = len(_PREFIX_FILTERED_INDICES) - 1
        formatted = _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]])
        return formatted, len(formatted)
    
    if _PREFIX_CURRENT_POS >= 0 and _PREFIX_CURRENT_POS < len(_PREFIX_FILTERED_INDICES):
        expected_idx = _PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]
        expected_cmd = _HISTORY_BUFFER[expected_idx]
        if current_input != expected_cmd:
            if current_input.startswith(_PREFIX_VALUE):
                for pos, idx in enumerate(_PREFIX_FILTERED_INDICES):
                    if _HISTORY_BUFFER[idx] == current_input:
                        _PREFIX_CURRENT_POS = pos
                        break
            else:
                _PREFIX_NAVIGATION_ACTIVE = False
                _PREFIX_VALUE = ""
                _PREFIX_FILTERED_INDICES = []
                _PREFIX_CURRENT_POS = -1
                clear_history_highlight_token()
                return current_input, len(current_input)
    
    if _PREFIX_CURRENT_POS > 0:
        _PREFIX_CURRENT_POS -= 1
        formatted = _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]])
        return formatted, len(formatted)
    elif _PREFIX_CURRENT_POS == 0:
        _PREFIX_NAVIGATION_ACTIVE = False
        _PREFIX_VALUE = ""
        _PREFIX_FILTERED_INDICES = []
        _PREFIX_CURRENT_POS = -1
        return current_input, len(current_input)
    else:
        return current_input, len(current_input)

def reset_history_index() -> None:
    """重置所有历史导航状态"""
    global _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS
    global _MULTILINE_STATE, _MULTILINE_BUFFER, _MULTILINE_ACTIVE
    
    _CURRENT_HISTORY_INDEX = -1
    _NAVIGATION_START_INPUT = ""
    _PREFIX_NAVIGATION_ACTIVE = False
    _PREFIX_VALUE = ""
    _PREFIX_FILTERED_INDICES = []
    _PREFIX_CURRENT_POS = -1
    _MULTILINE_STATE = None
    _MULTILINE_BUFFER = []
    _MULTILINE_ACTIVE = False
    clear_history_highlight_token()

# ===================== 新增：CMD 多行命令检测 =====================
def _is_cmd() -> bool:
    """检查当前是否是 CMD 终端"""
    return get_terminal_type() == 'cmd'

def _detect_cmd_multiline(line: str) -> Optional[str]:
    """
    检测 CMD 的多行命令类型。
    返回类型字符串或 None。
    """
    if not _is_cmd():
        return None
    
    stripped = line.strip().lower()
    
    # 检测 IF 块（可能带 ELSE）
    if re.search(r'\bif\b.*\(', stripped) and not re.search(r'\)', stripped):
        return 'cmd_if'
    
    # 检测 FOR 循环
    if re.search(r'\bfor\b.*\(', stripped) and not re.search(r'\)', stripped):
        return 'cmd_for'
    
    # 检查未闭合的括号块
    open_count = stripped.count('(')
    close_count = stripped.count(')')
    if open_count > close_count:
        return 'cmd_block'
    
    # 检查行续符 ^
    if stripped.endswith('^'):
        return 'cmd_continuation'
    
    return None

def _is_cmd_block_terminated(lines: List[str], line: str) -> bool:
    """
    检查 CMD 块是否已终止（通过匹配括号闭合）。
    使用栈式括号匹配，支持嵌套。
    """
    all_lines = lines + [line]
    full_text = '\n'.join(all_lines)
    balance = 0
    for ch in full_text:
        if ch == '(':
            balance += 1
        elif ch == ')':
            balance -= 1
            if balance < 0:
                return True  # 多余闭合，视为终止（错误状态）
    return balance == 0

def _process_multiline_input(
    user_input: str,
    virtual_root: str = "",
    completer: Optional[SmartCompleter] = None,
    lexer: Optional[CommandLexer] = None,
    comp_style: Optional[PromptStyle] = None,
    kb: Optional[KeyBindings] = None,
    auto_suggest: Optional[AutoSuggest] = None,
) -> Optional[str]:
    """
    处理多行输入（增强版，支持 heredoc #语法 切换，支持 CMD 多行块）
    
    修复：确保多行输入模式下语法高亮正确应用，补全功能正常工作。
    新增：忽略以 # 开头的注释行的结构化语法检测。
    新增：CMD 的 IF/FOR 块结构支持。
    修复：语法切换时正确使用 ml_input 的补全器和词法分析器。
    修复：heredoc 中 #语法 切换在终止检查之前执行。
    修复：终止符优先级高于语法切换，防止 EOF 被 continue 跳过。
    修复：使用 SmartSyntaxDetector 重新评估语法，提升准确性。
    """
    global _MULTILINE_STATE, _MULTILINE_BUFFER, _MULTILINE_ACTIVE
    
    # 新增：CMD 多行处理
    cmd_type = _detect_cmd_multiline(user_input)
    if cmd_type and _is_cmd():
        return _process_cmd_multiline_input(
            user_input, cmd_type,
            virtual_root=virtual_root,
            completer=completer,
            lexer=lexer,
            comp_style=comp_style,
            kb=kb,
            auto_suggest=auto_suggest,
        )
    
    # 使用 SmartSyntaxDetector 检测首行可能的语法
    detected_syntax = detect_syntax_from_command(user_input)
    if detected_syntax == 'bash' or detected_syntax == 'unknown':
        # 进一步使用内容特征检测
        content_syntax, confidence = SmartSyntaxDetector.detect_with_confidence(user_input)
        if confidence > 0.6 and content_syntax != SyntaxType.BASH:
            detected_syntax = content_syntax.value
    
    state = MultiLineDetector.detect(user_input, detected_syntax or "bash")
    
    if state is None:
        return None
    
    # 如果检测到非 bash 语法，更新 state.syntax
    if detected_syntax and detected_syntax != 'bash' and state.syntax == 'bash':
        state.syntax = detected_syntax
    
    _MULTILINE_ACTIVE = True
    _MULTILINE_STATE = state
    _MULTILINE_BUFFER = [user_input]
    
    ml_input = MultiLineInput(
        syntax=state.syntax,
        virtual_root=virtual_root,
    )
    
    # 设置初始的 lexer 和 completer（基于 ml_input）
    current_lexer = ml_input.lexer if ml_input.lexer is not None else lexer
    # heredoc 类型不启用补全
    use_completer = None if state.type in ('heredoc',) else (ml_input.completer if ml_input.completer is not None else completer)
    
    prompt_text = ml_input._get_prompt_text(state)
    
    while _MULTILINE_ACTIVE:
        try:
            line = prompt(
                prompt_text,
                lexer=current_lexer,
                style=comp_style,
                key_bindings=ml_input.kb if HAS_PYGMENTS else kb,
                auto_suggest=auto_suggest,
                completer=use_completer,
                complete_while_typing=(use_completer is not None),
                # mouse_support 已移除，避免鼠标接管终端滚动
            )
            
            if line is None or line.strip() == '__CANCEL__':
                _MULTILINE_ACTIVE = False
                _MULTILINE_STATE = None
                _MULTILINE_BUFFER = []
                return None
            
            # ===== 修复后的 heredoc 处理逻辑（终止符优先级最高）=====
            if state.type == 'heredoc':
                # 1. 先检查是否是终止行（EOF 等），优先级最高
                if MultiLineDetector.is_terminated(state, line):
                    _MULTILINE_BUFFER.append(line)
                    _MULTILINE_ACTIVE = False
                    _MULTILINE_STATE = None
                    result = '\n'.join(_MULTILINE_BUFFER)
                    _MULTILINE_BUFFER = []
                    return result
                
                # 2. 再检查语法切换（#python、#bash 等），仅当不是终止行时
                if not state.heredoc_syntax_locked:
                    new_syntax = MultiLineDetector.detect_heredoc_syntax_switch(line)
                    if new_syntax and new_syntax != state.syntax:
                        # 切换语法
                        state.syntax = new_syntax
                        state.heredoc_syntax_locked = True
                        ml_input.current_syntax = new_syntax
                        ml_input.lexer = ml_input._get_pygments_lexer(new_syntax)
                        ml_input.completer.syntax = new_syntax
                        current_lexer = ml_input.lexer if ml_input.lexer is not None else lexer
                        # heredoc 中不启用补全
                        use_completer = None
                        # 更新提示符以反映新语法
                        prompt_text = ml_input._get_prompt_text(state)
                        # 不将 #语法 行加入缓冲区（它是控制指令，不是内容）
                        continue
                
                # 3. 普通 heredoc 行
                _MULTILINE_BUFFER.append(line)
                prompt_text = ml_input._get_prompt_text(state)
                continue
            # ===== heredoc 处理结束 =====
            
            # 非 heredoc 的普通多行处理
            is_comment_line = line.strip().startswith('#')
            
            if is_comment_line:
                _MULTILINE_BUFFER.append(line)
                prompt_text = ml_input._get_prompt_text(state)
                continue
            
            # 重新评估累积内容的语法（仅在非 heredoc 模式下）
            if not state.heredoc_syntax_locked:
                full_code = '\n'.join(_MULTILINE_BUFFER + [line])
                new_detected, confidence = SmartSyntaxDetector.detect_with_confidence(full_code)
                if confidence > 0.6 and new_detected.value != state.syntax:
                    # 自动切换语法
                    state.syntax = new_detected.value
                    ml_input.current_syntax = new_detected.value
                    ml_input.lexer = ml_input._get_pygments_lexer(new_detected.value)
                    ml_input.completer.syntax = new_detected.value
                    current_lexer = ml_input.lexer if ml_input.lexer is not None else lexer
                    if state.type not in ('heredoc',):
                        use_completer = ml_input.completer if ml_input.completer is not None else completer
                    prompt_text = ml_input._get_prompt_text(state)
            
            # 使用 ml_input 的深度栈更新逻辑
            terminated, new_state = ml_input.process_line(line, state)
            _MULTILINE_BUFFER.append(line)
            
            if terminated:
                # 多行输入结束
                _MULTILINE_ACTIVE = False
                _MULTILINE_STATE = None
                result = '\n'.join(_MULTILINE_BUFFER)
                _MULTILINE_BUFFER = []
                return result
            elif new_state is not None:
                # 嵌套新的多行结构
                state = new_state
                ml_input.current_syntax = state.syntax
                ml_input.lexer = ml_input._get_pygments_lexer(state.syntax)
                ml_input.completer.syntax = state.syntax
                # 更新循环变量
                current_lexer = ml_input.lexer if ml_input.lexer is not None else lexer
                if state.type not in ('heredoc',):
                    use_completer = ml_input.completer if ml_input.completer is not None else completer
                else:
                    use_completer = None
            
            prompt_text = ml_input._get_prompt_text(state)
            
        except KeyboardInterrupt:
            _MULTILINE_ACTIVE = False
            _MULTILINE_STATE = None
            _MULTILINE_BUFFER = []
            return None
        except EOFError:
            _MULTILINE_ACTIVE = False
            _MULTILINE_STATE = None
            result = '\n'.join(_MULTILINE_BUFFER)
            _MULTILINE_BUFFER = []
            return result if result else None
    
    return None


def _process_cmd_multiline_input(
    user_input: str,
    cmd_type: str,
    virtual_root: str = "",
    completer: Optional[SmartCompleter] = None,
    lexer: Optional[CommandLexer] = None,
    comp_style: Optional[PromptStyle] = None,
    kb: Optional[KeyBindings] = None,
    auto_suggest: Optional[AutoSuggest] = None,
) -> Optional[str]:
    """
    处理 CMD 多行输入（IF/FOR 块结构）
    
    CMD 的多行语法：
    IF condition (
        command1
        command2
    ) ELSE (
        command3
    )
    
    FOR %%i IN (set) DO (
        command1
    )
    
    终止条件：括号完全闭合。
    修复：使用 ml_input 的补全器和 lexer（如果有）。
    """
    global _MULTILINE_STATE, _MULTILINE_BUFFER, _MULTILINE_ACTIVE
    
    _MULTILINE_ACTIVE = True
    _MULTILINE_BUFFER = [user_input]
    _MULTILINE_STATE = MultiLineState(
        type='cmd_block',
        syntax='bash',  # CMD 没有专门的 Pygments lexer，用 bash 近似
        start_line=user_input,
    )
    
    ml_input = MultiLineInput(
        syntax='bash',
        virtual_root=virtual_root,
    )
    
    current_lexer = lexer  # CMD 多行不强制 pygments
    use_completer = None  # 默认禁用补全，避免干扰
    
    prompt_text = 'more> '
    
    while _MULTILINE_ACTIVE:
        try:
            line = prompt(
                prompt_text,
                lexer=current_lexer,
                style=comp_style,
                key_bindings=kb,
                auto_suggest=auto_suggest,
                completer=use_completer,
                complete_while_typing=False,
                # mouse_support 已移除，避免鼠标接管终端滚动
            )
            
            if line is None or line.strip() == '__CANCEL__':
                _MULTILINE_ACTIVE = False
                _MULTILINE_STATE = None
                _MULTILINE_BUFFER = []
                return None
            
            _MULTILINE_BUFFER.append(line)
            
            # 检查括号是否闭合
            if _is_cmd_block_terminated([user_input], '\n'.join(_MULTILINE_BUFFER[1:])):
                _MULTILINE_ACTIVE = False
                _MULTILINE_STATE = None
                result = '\n'.join(_MULTILINE_BUFFER)
                _MULTILINE_BUFFER = []
                return result
            
            # 更新提示符
            if _MULTILINE_STATE.type in ('cmd_if', 'cmd_for'):
                prompt_text = 'more> '
            
        except KeyboardInterrupt:
            _MULTILINE_ACTIVE = False
            _MULTILINE_STATE = None
            _MULTILINE_BUFFER = []
            return None
        except EOFError:
            _MULTILINE_ACTIVE = False
            _MULTILINE_STATE = None
            result = '\n'.join(_MULTILINE_BUFFER)
            _MULTILINE_BUFFER = []
            return result if result else None
    
    return None

# ===================== 主输入函数 =====================
def universal_input(
    prompt_func: Callable[[], Union[FormattedText, str]],
    builtin_commands: Dict = None,
    cmd_mapping_cache: Dict = None,
    sys_type: str = "",
    alias_cache: Dict = None,
    user_home_dir: str = "",
    command_history: List = None,
    max_history_len: int = 1000,
    session_id: str = "",
    save_history_func: Optional[Callable] = None,
    read_config_file_func: Optional[Callable] = None,
    log_info_func: Optional[Callable] = None,
    log_error_func: Optional[Callable] = None,
    graceful_shutdown_func: Optional[Callable] = None,
    Fore: Any = None,
    Style: Any = None,
    language: str = "chinese",
    virtual_root: str = "",
    cmd_config_path: str = "",          # 外部传入的 JSON 补全详情路径
    com_cmd_config_path: str = "",      # 另一个 JSON 补全详情路径
    other_terminal_cmd_path: str = "",
) -> str:
    """主输入函数"""
    global _HISTORY_INITIALIZED, _CURRENT_LANG, _VIRTUAL_ROOT, META_TEXTS, _VALID_COMMANDS, _USER_HOME_DIR, _TERMINAL_TYPE
    global _HISTORY_BUFFER

    _CURRENT_LANG = language
    set_language(language)
    _VIRTUAL_ROOT = virtual_root
    set_virtual_root(virtual_root)
    _USER_HOME_DIR = user_home_dir
    set_user_home_dir(user_home_dir)

    # 确保 ptk 配置已加载
    _ensure_ptk_config()

    # 新增：检测终端类型
    _TERMINAL_TYPE = detect_and_set_terminal_type()
    
    # 设置 com_cmd.json 路径（优先使用 virtual_root 推导）
    if com_cmd_config_path:
        set_com_cmd_config_path(com_cmd_config_path)
    elif virtual_root:
        default_com_cmd_path = os.path.join(virtual_root, "onyx", "etc", "com_cmd.json")
        set_com_cmd_config_path(default_com_cmd_path)
    
    # 设置 other_terminal_cmd.json 路径
    if other_terminal_cmd_path:
        set_other_terminal_cmds_path(other_terminal_cmd_path)
    elif virtual_root:
        vpath = os.path.join(virtual_root, "onyx", "etc", "other_terminal_cmd.json")
        if os.path.exists(vpath):
            set_other_terminal_cmds_path(vpath)

    if not _HISTORY_INITIALIZED:
        init_history_navigation()

    if builtin_commands is None:
        builtin_commands = {}
    if cmd_mapping_cache is None:
        cmd_mapping_cache = {}
    if alias_cache is None:
        alias_cache = {}

    try:
        completion_items = set()
        completion_items.update(builtin_commands.keys())

        if sys_type and sys_type in cmd_mapping_cache:
            mapping = cmd_mapping_cache[sys_type].get("mapping", {})
            completion_items.update(mapping.get("tools", {}).keys())
            completion_items.update(mapping.get("system", []))

        completion_items.update(alias_cache.keys())

        # ----- 自动加载 cmd_mapping.msgpack 获取基础命令列表 -----
        msgpack_path = os.path.join(
            user_home_dir, ".cache", "onyx", "onyx", "cmd_mapping.msgpack"
        ) if user_home_dir else ""
        if msgpack_path and os.path.exists(msgpack_path):
            try:
                msgpack_cmds = CommandConfigLoader.get_commands(msgpack_path)
                completion_items.update(msgpack_cmds)
            except Exception:
                pass

        # ----- 如果外部传入了 JSON 补全详情，也将其中的命令加入候选 -----
        if cmd_config_path and os.path.exists(cmd_config_path):
            try:
                json_cmds = CommandConfigLoader.get_commands(cmd_config_path)
                completion_items.update(json_cmds)
            except Exception:
                pass

        # ----- 同样处理 com_cmd_config_path（内部推导或显式传入的 JSON） -----
        actual_com_cmd_path = com_cmd_config_path
        if not actual_com_cmd_path and virtual_root:
            actual_com_cmd_path = os.path.join(virtual_root, "onyx", "etc", "com_cmd.json")
        if actual_com_cmd_path and os.path.exists(actual_com_cmd_path):
            try:
                com_json_cmds = CommandConfigLoader.get_commands(actual_com_cmd_path)
                completion_items.update(com_json_cmds)
            except Exception:
                pass

        # 新增：加载终端专属命令
        terminal_commands = _get_terminal_specific_commands()
        if terminal_commands:
            completion_items.update(terminal_commands)
        
        # 确保 sudo 和 sado 在补全列表中
        completion_items.add('sudo')
        completion_items.add('sado')

        set_valid_commands(completion_items)

        # 创建智能补全器，传入基础命令列表和 JSON 补全详情
        completer = SmartCompleter(
            list(completion_items),
            show_hidden=True,
            cmd_config_path=cmd_config_path,          # 外部 JSON 详情
            com_cmd_config_path=actual_com_cmd_path or "",  # 内部 JSON 详情
            virtual_root=virtual_root,
            user_home_dir=user_home_dir,
            history_buffer=_HISTORY_BUFFER
        )

        if virtual_root and os.path.isdir(virtual_root):
            cache = get_path_cache()
            warm_paths = [virtual_root]
            if user_home_dir and os.path.isdir(user_home_dir):
                warm_paths.append(user_home_dir)
            cache.warm_up(warm_paths, virtual_root=virtual_root, show_hidden=True)

        lexer = CommandLexer(valid_commands=_VALID_COMMANDS, virtual_root=virtual_root)
        
        # 使用智能虚影补全（基于频率的完整命令建议）
        from .com import SmartAutoSuggest
        auto_suggest = SmartAutoSuggest(completer)

        # 应用 ptk 颜色配置
        ptk_colors = _ptk_config.get("colors", {})
        default_comp_style = {
            "completion-menu": "bg:#2d2d30 #cccccc",
            "completion-menu.completion": "bg:#2d2d30 #aaaaaa",
            "completion-menu.completion.current": "bg:#007acc #ffffff",
            "completion-menu.meta": "bg:#3d3d40 #888888",
            "completion-menu.meta.current": "bg:#007acc #cccccc",
            "scrollbar.background": "bg:#1e1e1e",
            "scrollbar.button": "bg:#555555",
            "bottom-toolbar": "bg:#007acc #ffffff",
        }
        for key, value in ptk_colors.items():
            if key in default_comp_style:
                default_comp_style[key] = value
        comp_style = PromptStyle.from_dict(default_comp_style)

        # 应用 ptk 键位配置，获取自定义键绑定
        kb = create_key_bindings(sys_type=sys_type, ptk_config=_ptk_config)

        # 补全自动触发过滤器：受 ESC+Space 全局开关控制
        from .kb import is_completion_locked

        @Condition
        def completion_typing_filter():
            return not is_completion_locked()

        prompt_text = prompt_func()
        user_input = prompt(
            prompt_text,
            completer=completer,
            lexer=lexer,
            complete_while_typing=completion_typing_filter,
            style=comp_style,
            key_bindings=kb,
            # mouse_support 已移除，避免鼠标接管终端滚动
            complete_in_thread=True,
            reserve_space_for_menu=6,
            auto_suggest=auto_suggest,
        )

        user_input_stripped = user_input.strip()

        if user_input_stripped:
            user_input_stripped = _clean_display_text(user_input_stripped)

        if user_input_stripped:
            multiline_result = _process_multiline_input(
                user_input_stripped,
                virtual_root=virtual_root,
                completer=completer,
                lexer=lexer,
                comp_style=comp_style,
                kb=kb,
                auto_suggest=auto_suggest,
            )
            
            if multiline_result is not None:
                user_input_stripped = multiline_result.strip()
                
                user_input_stripped = _clean_display_text(user_input_stripped)
                
                if user_input_stripped:
                    add_to_history(user_input_stripped)
                    reset_history_index()
                    return user_input_stripped
            elif _MULTILINE_ACTIVE:
                reset_history_index()
                return ""

        if user_input_stripped:
            add_to_history(user_input_stripped)

        reset_history_index()
        return user_input_stripped

    except KeyboardInterrupt:
        if Fore:
            print(Fore.YELLOW + "\n^C" + (Style.RESET_ALL if Style else ""))
        else:
            print("\n^C")
        reset_history_index()
        return ""
    except EOFError:
        if Fore:
            print(Fore.YELLOW + "\n^D" + (Style.RESET_ALL if Style else ""))
        else:
            print("\n^D")
        if graceful_shutdown_func:
            graceful_shutdown_func(session_id)
        sys.exit(0)
        return ""
    except Exception as e:
        if Fore:
            print(Fore.RED + f"Input error: {e}" + (Style.RESET_ALL if Style else ""))
        reset_history_index()
        return ""

__all__ = [
    'universal_input',
    'set_language',
    'get_path_cache',
    'PathCache',
    'SmartCompleter',
    'set_virtual_root',
    'get_virtual_root',
    'CommandConfigLoader',
    'CommandLexer',
    'set_valid_commands',
    'handle_multiline_input',
    'detect_syntax_from_command',
    'MultiLineInput',
    'MultiLineDetector',
    'MultiLineState',
    'MultiLineFormatter',
    'HAS_PYGMENTS',
    'get_terminal_type',
    'detect_and_set_terminal_type',
    'set_other_terminal_cmds_path',
    'get_other_terminal_cmds',
    'set_com_cmd_config_path',
    'get_com_cmd_config',
    'set_com_cmd_config_path_from_root',
    'get_com_cmd_config_path',
    '_get_nav_match_info',
]