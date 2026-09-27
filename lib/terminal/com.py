# lib/terminal/com.py
"""
补全与高亮模块
包含路径补全引擎、命令补全器、语法高亮器、虚影补全、缓存等所有核心逻辑
新增：
- 终端类型适配（get_terminal_type）
- other_terminal_cmd.json 加载
- CMD 环境变量展开支持（%VAR%）
- Windows 可执行文件判断
- posix 模式自适应
- com_cmd.json 选项和参数补全支持
修复：
- 子命令和路径补全同时出现时，子命令优先级高于路径补全
- manage s[tab] 同时显示子命令 set 和路径 static/
- com_cmd.json 路径使用虚拟根目录
- npm run 等命令的参数补全
- 子命令后第三个词的参数/路径补全优先级
- 路径补全重复首字母问题（修复 start_position 计算）
新增：
- 智能虚影补全：基于频率的完整命令建议
- 虚影优先显示频率最高的完整命令
- 参数优先显示（如 manage set a）
- 前缀实时响应虚影变化
修复：
- 虚影补全对齐当前输入，不添加额外空格
- 路径补全 start_position 正确计算
- 空格保留补全：正确识别命令后的空格，匹配历史完整命令
- 为多行代码输入提供上下文感知补全（委托给 MultiLineCompleter）
修复（AST/词法分析专项）：
- _tokenize_quoted_string 显式返回 (tokens, consumed)，不再依赖隐式长度求和
- _classify_word 新增 is_first_word 参数，仅首词识别为 command 样式
- _overlay_highlight 前置长度校验，token 拼接与 full_text 不一致时跳过叠加
- tokenize 判断「本行第一个有意义 token」的辅助逻辑提取为 _has_meaningful_token
新增（命令树专项）：
- CommandNode / CommandTree / _normalize_command_spec：
  把任意 JSON 规格归一化为统一的命令树节点，兼容旧格式
- 支持 arguments: ["值1", "值2"]、arguments: {"type": "file"}、
  arguments: {"type": "dir", "values": [...]} 等多种写法
- 顶层 type: "file" / "dir" 简写
- 补全按树逐级推进：manage → set → language → zh/en/Chinese/English
- python → 只补文件；cd → 只补目录
新增（配置专项）：
- DEFAULT_PTK_CONFIG.completion.use_dropdown_menu：是否使用下拉菜单
"""

import os
import time
import json
import threading
import re
import shlex
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Callable, Iterable, Any, Set
from collections import OrderedDict

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion, AutoSuggestFromHistory
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.document import Document

# 尝试导入 msgpack
try:
    import msgpack
    HAS_MSGPACK = True
except ImportError:
    HAS_MSGPACK = False

# ===================== ptk 配置加载 =====================
DEFAULT_PTK_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".config", "onyx", "ptk.json")
PTK_CONFIG_PATH = DEFAULT_PTK_CONFIG_PATH

DEFAULT_PTK_CONFIG = {
    "key_bindings": {
        "history_up": "up",
        "history_down": "down",
        "prefix_history_up": "escape, up",
        "prefix_history_down": "escape, down",
        "completion_next": "tab",
        "completion_prev": "s-tab",
        "clear_screen": "c-l",
        "completion_page_up": "pageup",
        "completion_page_down": "pagedown",
        "completion_menu_up": "c-up",
        "completion_menu_down": "c-down",
        "completion_trigger": "c-space",
        "completion_alt_next": "c-n",
        "completion_alt_prev": "c-p",
        "completion_lock": "escape, space",
        "multiline_editor": "escape, enter"
    },
    "colors": {
        "completion-menu": "bg:#2d2d30 #cccccc",
        "completion-menu.completion": "bg:#2d2d30 #aaaaaa",
        "completion-menu.completion.current": "bg:#007acc #ffffff",
        "completion-menu.meta": "bg:#3d3d40 #888888",
        "completion-menu.meta.current": "bg:#007acc #cccccc",
        "scrollbar.background": "bg:#1e1e1e",
        "scrollbar.button": "bg:#555555",
        "bottom-toolbar": "bg:#007acc #ffffff"
    },
    "completion": {
        "show_hidden": True,
        "reserve_space_for_menu": 6,
        "complete_while_typing": True,
        "complete_in_thread": True,
        "max_completions": 100,
        "use_dropdown_menu": True
    },
    "history": {
        "memory_limit": 1000,
        "file_limit": 50000,
        "file_name": ".onyx_history.txt"
    },
    "auto_suggest": {
        "enabled": True,
        "strategy": "frequency"
    },
    "meta_texts": {}
}

# 配置缓存（按 mtime 失效）
_PTK_CONFIG_CACHE: Optional[Dict[str, Any]] = None
_PTK_CONFIG_MTIME: float = 0.0
_PTK_CONFIG_LOCK = threading.RLock()


def load_ptk_config(force_reload: bool = False) -> Dict[str, Any]:
    """加载 ptk.json 配置，若不存在则生成默认配置。

    修复：新增缓存（按 mtime 失效）。此前每次 universal_input 都重新读文件 +
    深拷贝 + 深合并，成本 ~0.1-0.5ms，纯浪费。
    """
    global _PTK_CONFIG_CACHE, _PTK_CONFIG_MTIME

    config_path = os.path.expanduser(PTK_CONFIG_PATH)
    config_dir = os.path.dirname(config_path)

    with _PTK_CONFIG_LOCK:
        # 计算 mtime
        try:
            mtime = os.path.getmtime(config_path) if os.path.exists(config_path) else -1.0
        except Exception:
            mtime = -1.0

        if (not force_reload
                and _PTK_CONFIG_CACHE is not None
                and mtime == _PTK_CONFIG_MTIME):
            return _PTK_CONFIG_CACHE

        if not os.path.exists(config_dir):
            try:
                os.makedirs(config_dir, mode=0o755, exist_ok=True)
            except Exception:
                pass

        if not os.path.exists(config_path):
            try:
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(DEFAULT_PTK_CONFIG, f, indent=2, ensure_ascii=False)
                result = DEFAULT_PTK_CONFIG.copy()
            except Exception:
                result = DEFAULT_PTK_CONFIG.copy()
            _PTK_CONFIG_CACHE = result
            _PTK_CONFIG_MTIME = mtime
            return result

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                user_config = json.load(f)

            import copy as _copy
            merged = _copy.deepcopy(DEFAULT_PTK_CONFIG)
            for key, value in user_config.items():
                if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                    merged[key].update(value)
                else:
                    merged[key] = value
            _PTK_CONFIG_CACHE = merged
            _PTK_CONFIG_MTIME = mtime
            return merged
        except Exception:
            _PTK_CONFIG_CACHE = DEFAULT_PTK_CONFIG.copy()
            _PTK_CONFIG_MTIME = mtime
            return _PTK_CONFIG_CACHE


# ===================== 终端类型适配 =====================
_TERMINAL_TYPE: Optional[str] = None

def get_detected_terminal_type() -> str:
    """获取检测到的终端类型（从 get_terminal_type 导入）"""
    global _TERMINAL_TYPE
    if _TERMINAL_TYPE is None:
        try:
            from lib.get_terminal_type import get_terminal_type
            _TERMINAL_TYPE = get_terminal_type()
        except ImportError:
            try:
                from ..get_terminal_type import get_terminal_type  # type: ignore
                _TERMINAL_TYPE = get_terminal_type()
            except (ImportError, ValueError):
                _TERMINAL_TYPE = 'sh'
    return _TERMINAL_TYPE


def set_terminal_type(term_type: str) -> None:
    """手动设置终端类型"""
    global _TERMINAL_TYPE
    _TERMINAL_TYPE = term_type


def get_posix_mode() -> bool:
    """根据终端类型返回是否使用 POSIX 模式（shlex.split）
    cmd / powershell 使用非 POSIX：反斜杠按字面量处理，符合各自引号/转义规则"""
    term_type = get_detected_terminal_type()
    return term_type not in ('cmd', 'powershell')


_OTHER_TERMINAL_CMDS_CACHE: Optional[Dict[str, List[str]]] = None
_OTHER_TERMINAL_CMDS_CONFIG_PATH: Optional[str] = None

def set_other_terminal_cmds_path(config_path: str) -> None:
    """设置 other_terminal_cmd.json 路径"""
    global _OTHER_TERMINAL_CMDS_CONFIG_PATH
    _OTHER_TERMINAL_CMDS_CONFIG_PATH = config_path


def get_other_terminal_cmds() -> Dict[str, List[str]]:
    """加载 other_terminal_cmd.json 并缓存"""
    global _OTHER_TERMINAL_CMDS_CACHE, _OTHER_TERMINAL_CMDS_CONFIG_PATH
    if _OTHER_TERMINAL_CMDS_CACHE is not None:
        return _OTHER_TERMINAL_CMDS_CACHE

    config_path = _OTHER_TERMINAL_CMDS_CONFIG_PATH
    if not config_path:
        possible_paths = [
            "/onyx/etc/other_terminal_cmd.json",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "etc", "other_terminal_cmd.json"),
        ]
        for p in possible_paths:
            if os.path.exists(p):
                config_path = p
                break

    if not config_path or not os.path.exists(config_path):
        _OTHER_TERMINAL_CMDS_CACHE = {}
        return _OTHER_TERMINAL_CMDS_CACHE

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        _OTHER_TERMINAL_CMDS_CACHE = data if isinstance(data, dict) else {}
    except Exception:
        _OTHER_TERMINAL_CMDS_CACHE = {}

    return _OTHER_TERMINAL_CMDS_CACHE


# ===================== com_cmd.json 选项和参数补全配置 =====================
_COM_CMD_CONFIG_CACHE: Optional[Dict[str, Any]] = None
_COM_CMD_CONFIG_PATH: Optional[str] = None

def set_com_cmd_config_path(config_path: str) -> None:
    """设置 com_cmd.json 路径"""
    global _COM_CMD_CONFIG_PATH
    _COM_CMD_CONFIG_PATH = config_path


def get_com_cmd_config_path() -> str:
    """获取 com_cmd.json 路径"""
    global _COM_CMD_CONFIG_PATH
    return _COM_CMD_CONFIG_PATH or ""


def get_com_cmd_config() -> Dict[str, Any]:
    """加载 com_cmd.json 并缓存"""
    global _COM_CMD_CONFIG_CACHE, _COM_CMD_CONFIG_PATH
    if _COM_CMD_CONFIG_CACHE is not None:
        return _COM_CMD_CONFIG_CACHE

    config_path = _COM_CMD_CONFIG_PATH
    if not config_path:
        possible_paths = [
            "/onyx/etc/com_cmd.json",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "etc", "com_cmd.json"),
        ]
        for p in possible_paths:
            if os.path.exists(p):
                config_path = p
                break

    if not config_path or not os.path.exists(config_path):
        _COM_CMD_CONFIG_CACHE = {}
        return _COM_CMD_CONFIG_CACHE

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        _COM_CMD_CONFIG_CACHE = data if isinstance(data, dict) else {}
    except Exception:
        _COM_CMD_CONFIG_CACHE = {}

    return _COM_CMD_CONFIG_CACHE


# ===================== 颜色定义 =====================
COLORS = {
    "dir": "ansicyan bold",
    "dir_hidden": "ansiblack bold",
    "file": "ansiwhite",
    "file_hidden": "ansiblack",
    "file_exec": "ansigreen bold",
    "symlink": "ansiyellow bold",
    "parent": "ansipurple bold",
    "current": "ansiblue bold",
    "command": "ansigreen bold",
    "command_invalid": "ansired bold underline",
    "path_invalid": "ansiyellow underline",
    "subcommand": "ansiyellow",
    "option": "ansired",
    "argument": "ansimagenta",
    "path": "ansicyan",
    "error": "ansired bold",
    "info": "ansiblue",
    "string": "ansigreen",
    "variable": "ansicyan bold",
    "separator": "ansimagenta",
    "history_match": "bg:#ffffff #000000 bold",
}

# ── 历史导航高亮 token（由 input_lib 设置，CommandLexer 消费）──
_HIGHLIGHT_TOKEN: str = ""

def set_history_highlight_token(token: str) -> None:
    """设置历史导航匹配 token，CommandLexer 会在渲染时高亮"""
    global _HIGHLIGHT_TOKEN
    _HIGHLIGHT_TOKEN = token

# ── 导航重置回调（由 input_lib 注册，lexer 自动清除时触发）──
_nav_reset_callback = None

def set_nav_reset_callback(cb) -> None:
    """注册导航重置回调（input_lib.reset_history_index）"""
    global _nav_reset_callback
    _nav_reset_callback = cb

def clear_history_highlight_token() -> None:
    """清除历史导航高亮 token"""
    global _HIGHLIGHT_TOKEN
    _HIGHLIGHT_TOKEN = ""


def _overlay_highlight(
    tokens: List[Tuple[str, str]],
    full_text: str,
    hl_token: str,
    hl_style: str,
) -> List[Tuple[str, str]]:
    """
    在已有 token 列表上叠加高亮：找到 full_text 中所有 hl_token 出现位置，
    将覆盖到的 token 拆分并赋予 hl_style。

    修复：先校验 tokens 拼接长度是否等于 full_text，不一致时降级为「不叠加高亮」，
    避免高亮标错位置导致渲染错乱。
    """
    if not hl_token or hl_token not in full_text:
        return tokens

    total_len = sum(len(t[1]) for t in tokens)
    if total_len != len(full_text):
        return tokens

    spans = []
    start = 0
    while True:
        idx = full_text.find(hl_token, start)
        if idx == -1:
            break
        spans.append((idx, idx + len(hl_token)))
        start = idx + 1

    if not spans:
        return tokens

    hl_set = set()
    for s, e in spans:
        for pos in range(s, e):
            hl_set.add(pos)

    result = []
    char_pos = 0
    for style, text in tokens:
        seg_start = char_pos
        seg_end = char_pos + len(text)
        overlap = [p for p in range(seg_start, seg_end) if p in hl_set]
        if not overlap:
            result.append((style, text))
        else:
            i = 0
            while i < len(text):
                abs_pos = seg_start + i
                if abs_pos in hl_set:
                    j = i
                    while j < len(text) and (seg_start + j) in hl_set:
                        j += 1
                    result.append((hl_style, text[i:j]))
                    i = j
                else:
                    j = i
                    while j < len(text) and (seg_start + j) not in hl_set:
                        j += 1
                    result.append((style, text[i:j]))
                    i = j
        char_pos = seg_end

    return result

META_COLORS = {
    "dir": "ansicyan",
    "hidden": "ansiblack",
    "symlink": "ansiyellow",
    "exec": "ansigreen",
    "parent": "ansipurple",
    "current": "ansiblue",
    "command": "ansigreen",
    "option": "ansired",
    "argument": "ansimagenta",
    "file": "ansiwhite",
    "subcommand": "ansiyellow bold",
}

META_TEXTS_EN = {
    "command": "cmd",
    "dir": "dir",
    "file": "file",
    "hidden": "hidden",
    "symlink": "link",
    "exec": "exec",
    "parent": "parent",
    "current": "current",
    "option": "option",
    "argument": "arg",
    "subcommand": "subcmd",
}

LANG_TEXTS = {
    "chinese": {
        "loading": "加载中...",
        "no_match": "无匹配项",
        "permission_denied": "权限不足",
        "not_found": "未找到",
    },
    "english": {
        "loading": "Loading...",
        "no_match": "No matches",
        "permission_denied": "Permission denied",
        "not_found": "Not found",
    }
}

# ===================== 路径存在性缓存 =====================
class PathExistenceCache:
    def __init__(self, ttl: float = 5.0):
        self._cache: Dict[str, Tuple[bool, float]] = {}
        self._lock = threading.RLock()
        self.ttl = ttl

    def exists(self, path: str) -> bool:
        now = time.time()
        with self._lock:
            if path in self._cache:
                exists, timestamp = self._cache[path]
                if now - timestamp < self.ttl:
                    return exists
            try:
                exists = os.path.exists(path)
            except Exception:
                exists = False
            self._cache[path] = (exists, now)
            return exists

    def clear(self):
        with self._lock:
            self._cache.clear()

_PATH_EXISTENCE_CACHE = PathExistenceCache()

# ===================== 路径缓存 =====================
class PathCache:
    def __init__(self, cache_dir: Optional[str] = None, max_size: int = 10000, ttl: int = 3600):
        self.max_size = max_size
        self.ttl = ttl
        self._cache: OrderedDict[str, Tuple[List[Tuple[str, str, str, int]], float]] = OrderedDict()
        self._lock = threading.RLock()
        self._dirty = False
        self._save_timer: Optional[threading.Timer] = None

        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = self._get_default_cache_dir()

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "ter_path.msgpack" if HAS_MSGPACK else self.cache_dir / "ter_path.json"
        self._load()

    def _get_default_cache_dir(self) -> Path:
        home = Path.home()
        possible_paths = [
            home / ".cache" / "onyx" / "onyx",
            home / ".onyx" / "cache",
            Path(os.getcwd()) / ".cache" / "onyx",
        ]
        for path in possible_paths:
            if path.exists():
                return path
        return home / ".cache" / "onyx" / "onyx"

    def _load(self) -> None:
        if not self.cache_file.exists():
            return
        try:
            with open(self.cache_file, 'rb') as f:
                if HAS_MSGPACK:
                    data = msgpack.unpackb(f.read(), raw=False)
                else:
                    data = json.loads(f.read().decode('utf-8'))

            current_time = time.time()
            with self._lock:
                for key, (items, timestamp) in data.items():
                    if current_time - timestamp < self.ttl:
                        self._cache[key] = (items, timestamp)
                while len(self._cache) > self.max_size:
                    self._cache.popitem(last=False)
        except Exception:
            pass

    def _schedule_save(self) -> None:
        with self._lock:
            self._dirty = True
            if self._save_timer:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(5.0, self._do_save)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _do_save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            try:
                data = {}
                current_time = time.time()
                for key, (items, timestamp) in self._cache.items():
                    if current_time - timestamp < self.ttl:
                        data[key] = (items, timestamp)
                with open(self.cache_file, 'wb') as f:
                    if HAS_MSGPACK:
                        f.write(msgpack.packb(data, use_bin_type=True))
                    else:
                        f.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
            except Exception:
                pass

    def get(self, key: str) -> Optional[List[Tuple[str, str, str, int]]]:
        with self._lock:
            if key in self._cache:
                items, timestamp = self._cache[key]
                if time.time() - timestamp < self.ttl:
                    self._cache.move_to_end(key)
                    return items.copy()
                else:
                    del self._cache[key]
        return None

    def set(self, key: str, items: List[Tuple[str, str, str, int]]) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (items, time.time())
            while len(self._cache) > self.max_size:
                self._cache.popitem(last=False)
        self._schedule_save()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._dirty = True
        self._schedule_save()

    def flush(self) -> None:
        with self._lock:
            if self._save_timer:
                self._save_timer.cancel()
                self._save_timer = None
        self._do_save()

    def warm_up(self, paths: List[str], virtual_root: str = "", show_hidden: bool = True) -> None:
        def _scan():
            for p in paths:
                key = f"{p}:{show_hidden}:{virtual_root}"
                if self.get(key) is not None:
                    continue
                try:
                    if not os.path.isdir(p):
                        continue
                    items = []
                    for item in os.listdir(p):
                        if not show_hidden and item.startswith('.') and item not in ('.', '..'):
                            continue
                        full = os.path.join(p, item)
                        try:
                            is_dir = os.path.isdir(full)
                            is_symlink = os.path.islink(full)
                            is_exec = os.access(full, os.X_OK) and not is_dir
                            is_hidden = item.startswith('.') and item not in ('.', '..')

                            if is_dir:
                                text = item + os.sep
                                meta = META_TEXTS_EN['dir']
                                color = COLORS['dir_hidden'] if is_hidden else COLORS['dir']
                            else:
                                text = item
                                if is_exec:
                                    meta = META_TEXTS_EN['exec']
                                    color = COLORS['file_exec']
                                elif is_hidden:
                                    meta = META_TEXTS_EN['hidden']
                                    color = COLORS['file_hidden']
                                else:
                                    meta = META_TEXTS_EN['file']
                                    color = COLORS['file']

                            if is_symlink:
                                meta = META_TEXTS_EN['symlink']
                                color = COLORS['symlink']
                                try:
                                    target = os.readlink(full)
                                    if len(target) > 20:
                                        target = target[:17] + "..."
                                    meta = f"{META_TEXTS_EN['symlink']} -> {target}"
                                except OSError:
                                    pass

                            items.append((text, meta, color, -len(text), is_hidden))
                        except OSError:
                            continue
                    items.sort(key=lambda x: (1 if x[4] else 0, 0 if x[0].endswith(os.sep) else 1, x[0].lower()))
                    cache_items = [(t, m, c, p) for t, m, c, p, _ in items]
                    self.set(key, cache_items)
                except Exception:
                    pass

        thread = threading.Thread(target=_scan, daemon=True)
        thread.start()

_PATH_CACHE: Optional[PathCache] = None

def get_path_cache() -> PathCache:
    global _PATH_CACHE
    if _PATH_CACHE is None:
        _PATH_CACHE = PathCache()
    return _PATH_CACHE


# ===================== 路径解析器 =====================
class PathResolver:
    @staticmethod
    def expand_path(path: str, virtual_root: str = "") -> str:
        if not path:
            return ""

        if virtual_root and path.startswith('/'):
            path = re.sub(r'/+', '/', path)
            if path == '/':
                return virtual_root
            rel_path = path[1:]
            if rel_path:
                resolved = os.path.join(virtual_root, rel_path)
                if os.path.exists(os.path.dirname(resolved)) or resolved.endswith(os.sep):
                    return resolved
            return os.path.join(virtual_root, rel_path) if rel_path else virtual_root

        if path.startswith('~'):
            if len(path) == 1 or path[1] in ('/', '\\'):
                path = str(Path.home()) + path[1:]
            else:
                parts = path[1:].split(os.sep, 1)
                user_home = Path.home().parent / parts[0]
                if user_home.exists():
                    path = str(user_home) + (os.sep + parts[1] if len(parts) > 1 else "")
                else:
                    path = str(Path.home()) + path[1:]

        term_type = get_detected_terminal_type()
        if term_type == 'cmd':
            if '%' in path:
                def cmd_var_replacer(match):
                    var_name = match.group(1)
                    return os.environ.get(var_name, match.group(0))
                path = re.sub(r'%([^%]+)%', cmd_var_replacer, path)
        else:
            if '$' in path:
                def replacer(match):
                    var_name = match.group(1) or match.group(2)
                    return os.environ.get(var_name, match.group(0))
                path = re.sub(r'\$(\w+)|\$\{(\w+)\}', replacer, path)

        return path

    @staticmethod
    def normalize(path: str, virtual_root: str = "") -> str:
        if not path:
            return ""

        expanded = PathResolver.expand_path(path, virtual_root)

        if virtual_root and path.startswith('/'):
            if not os.path.isabs(expanded):
                expanded = os.path.join(virtual_root, expanded.lstrip('/'))
            return os.path.normpath(expanded)

        if not os.path.isabs(expanded) and not expanded.startswith(('./', '../')):
            expanded = os.path.join(os.getcwd(), expanded)

        return os.path.normpath(expanded)

    @staticmethod
    def split_for_completion(path: str, virtual_root: str = "") -> Tuple[str, str, bool]:
        """分割路径用于补全。返回: (目录路径, 文件前缀, 是否为绝对模式)"""
        if not path:
            return os.getcwd(), "", False

        normalized_path = path
        if os.sep == '\\':
            normalized_path = path.replace('/', '\\')
        else:
            normalized_path = path.replace('\\', '/')

        if virtual_root and path.startswith('/'):
            is_absolute_mode = True
            if path.endswith('/'):
                if path == '/':
                    return virtual_root, "", True
                clean_path = path.rstrip('/')
                dir_path = PathResolver.expand_path(clean_path, virtual_root)
                return dir_path, "", True

            last_slash = path.rfind('/')
            if last_slash <= 0:
                dir_part = virtual_root
                file_prefix = path[1:] if len(path) > 1 else ""
            else:
                dir_path = path[:last_slash]
                file_prefix = path[last_slash + 1:]
                if dir_path == '' or dir_path == '/':
                    dir_part = virtual_root
                else:
                    dir_part = PathResolver.expand_path(dir_path, virtual_root)

            return dir_part, file_prefix, is_absolute_mode

        if os.name == 'nt' and re.match(r'^[A-Za-z]:', path):
            is_absolute_mode = True

            if path.endswith('\\') or path.endswith('/'):
                if re.match(r'^[A-Za-z]:\\$', path) or re.match(r'^[A-Za-z]:/$', path):
                    return path, "", True
                clean_path = path.rstrip('\\/')
                dir_path = PathResolver.expand_path(clean_path, virtual_root)
                return dir_path, "", True

            last_sep = max(path.rfind('\\'), path.rfind('/'))
            if last_sep <= 2:
                if ':' in path:
                    colon_idx = path.find(':')
                    if colon_idx >= 0:
                        dir_part = path[:colon_idx+1] + '\\'
                        file_prefix = path[colon_idx+1:] if len(path) > colon_idx+1 else ""
                    else:
                        dir_part = "."
                        file_prefix = path
                else:
                    dir_part = "."
                    file_prefix = path
            else:
                dir_part = path[:last_sep]
                file_prefix = path[last_sep + 1:]
                dir_part = PathResolver.expand_path(dir_part, virtual_root)

            return dir_part, file_prefix, is_absolute_mode

        if os.path.isabs(path):
            is_absolute_mode = True

            if path.endswith(os.sep):
                if path == os.sep:
                    return path, "", True
                clean_path = path.rstrip(os.sep)
                dir_path = PathResolver.expand_path(clean_path, virtual_root)
                return dir_path, "", True

            last_sep = path.rfind(os.sep)
            if last_sep <= 0:
                dir_part = os.sep
                file_prefix = path[1:] if len(path) > 1 else ""
            else:
                dir_part = path[:last_sep]
                file_prefix = path[last_sep + 1:]
                dir_part = PathResolver.expand_path(dir_part, virtual_root)

            return dir_part, file_prefix, is_absolute_mode

        expanded = PathResolver.expand_path(path, virtual_root)

        if path.endswith(os.sep):
            normalized = expanded.rstrip(os.sep)
            if not normalized:
                return ".", "", False
            return normalized, "", False

        dir_part = os.path.dirname(expanded)
        file_prefix = os.path.basename(expanded)

        if not dir_part:
            dir_part = "."

        return dir_part, file_prefix, False

    @staticmethod
    def is_executable(path: str) -> bool:
        if not os.path.exists(path):
            return False
        if os.name == 'nt':
            executable_exts = os.environ.get('PATHEXT', '.EXE;.BAT;.CMD;.COM;.PS1').split(';')
            ext = os.path.splitext(path)[1].upper()
            return ext in executable_exts
        return os.access(path, os.X_OK)


# ===================== 路径补全引擎 =====================
# ===================== 路径补全引擎 =====================
class PathCompleterEngine:
    def __init__(self, show_hidden: bool = True, follow_symlinks: bool = True, use_cache: bool = True, virtual_root: str = ""):
        self.show_hidden = show_hidden
        self.follow_symlinks = follow_symlinks
        self.use_cache = use_cache
        self.virtual_root = virtual_root
        self.cache = get_path_cache() if use_cache else None

    def get_completions(self, path_prefix: str, start_pos: int = 0) -> List[Tuple[str, str, str, int]]:
        if not path_prefix:
            return self._list_directory_with_prefix(os.getcwd(), "", 0)

        dir_path, file_prefix, _ = PathResolver.split_for_completion(path_prefix, self.virtual_root)

        # ──────────────────────────────────────────────────────────────
        # 修复：补全只替换「文件前缀」部分，而不是整个 current_word。
        #
        # 症状：cd /etc 选择补全后，路径变成 etc/ 而不是 /etc/；
        #       cd /etc/ 选择子目录 passwd 后，变成 passwd 而不是 /etc/passwd。
        #
        # 原因：调用方传入的 start_pos = -len(current_word)，ptk 应用补全时
        #       会删掉「整个当前词」再插入补全文本。而 current_word 里其实
        #       已经包含「用户敲定的目录前缀」（/、/etc/、~/doc 等），这一
        #       段不该被删。真正需要替换的只有 file_prefix（= 用户还没输完
        #       的尾部）：
        #         cd /etc  → file_prefix='etc'  → 只替换 'etc'，保留 '/' → /etc/
        #         cd /etc/ → file_prefix=''     → 纯追加         → /etc/passwd
        # ──────────────────────────────────────────────────────────────
        adjusted_start = -len(file_prefix) if file_prefix else 0

        cache_key = f"{dir_path}:{file_prefix}:{self.show_hidden}:{self.virtual_root}"
        if self.use_cache and self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                # 缓存里存的 start 是构建时快照，这里用 adjusted_start 重写，
                # 保证同一缓存项可服务于不同输入前缀长度（如 'e' 与 'etc'）
                return [(text, meta, color, adjusted_start) for text, meta, color, _ in cached]

        completions = self._list_directory_with_prefix(dir_path, file_prefix, adjusted_start)

        if self.use_cache and self.cache and completions:
            cache_items = [(text, meta, color, 0) for text, meta, color, _ in completions]
            self.cache.set(cache_key, cache_items)

        return completions

    def _list_directory_with_prefix(self, dir_path: str, prefix: str, start_pos: int) -> List[Tuple[str, str, str, int]]:
        completions = []

        if dir_path == "/":
            dir_path = "/"
        elif dir_path.endswith(':'):
            dir_path = dir_path + "\\"
        elif dir_path.endswith(':\\'):
            pass
        else:
            dir_path = os.path.normpath(dir_path)

        try:
            if not os.path.exists(dir_path):
                return []
            if not os.path.isdir(dir_path):
                return []
            items = os.listdir(dir_path)
        except (OSError, PermissionError):
            return []

        should_add_parent = True
        if dir_path == "/":
            should_add_parent = False
        elif os.name == 'nt' and re.match(r'^[A-Za-z]:\\?$', dir_path):
            should_add_parent = False

        if should_add_parent:
            if not prefix or '..'.startswith(prefix.lower()):
                completions.append(('..' + os.sep, META_TEXTS_EN['parent'], COLORS['parent'], start_pos))
            if not prefix or '.'.startswith(prefix.lower()):
                completions.append(('.' + os.sep, META_TEXTS_EN['current'], COLORS['current'], start_pos))

        for item in items:
            if not self.show_hidden and item.startswith('.') and item not in ('.', '..'):
                continue

            if not item.lower().startswith(prefix.lower()):
                continue

            full_path = os.path.join(dir_path, item)

            try:
                is_dir = os.path.isdir(full_path)
                is_symlink = os.path.islink(full_path)
                is_executable = PathResolver.is_executable(full_path) and not is_dir
                is_hidden = item.startswith('.') and item not in ('.', '..')

                completion_text = item + (os.sep if is_dir else "")

                if is_dir:
                    meta = META_TEXTS_EN['dir']
                    color = COLORS['dir_hidden'] if is_hidden else COLORS['dir']
                else:
                    if is_executable:
                        meta = META_TEXTS_EN['exec']
                        color = COLORS['file_exec']
                    elif is_hidden:
                        meta = META_TEXTS_EN['hidden']
                        color = COLORS['file_hidden']
                    else:
                        meta = META_TEXTS_EN['file']
                        color = COLORS['file']

                if is_symlink:
                    meta = META_TEXTS_EN['symlink']
                    color = COLORS['symlink']
                    if self.follow_symlinks:
                        try:
                            target = os.readlink(full_path)
                            if len(target) > 20:
                                target = target[:17] + "..."
                            meta = f"{META_TEXTS_EN['symlink']} -> {target}"
                        except OSError:
                            pass

                completions.append((completion_text, meta, color, start_pos))

            except OSError:
                continue

        if completions:
            completions.sort(key=lambda x: (
                1 if x[0].startswith('.') else 0,
                0 if x[0].endswith(os.sep) else 1,
                x[0].lower()
            ))

        return completions


# ===================== 命令配置加载器 =====================
class CommandConfigLoader:
    _CMD_CONFIG_CACHE: Dict[str, Dict] = {}

    @classmethod
    def load_config(cls, config_path: str) -> Dict:
        if config_path in cls._CMD_CONFIG_CACHE:
            return cls._CMD_CONFIG_CACHE[config_path]

        if not os.path.exists(config_path):
            if config_path.endswith('.msgpack'):
                return cls._load_msgpack_config(config_path)
            return {}

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            cls._CMD_CONFIG_CACHE[config_path] = config
            return config
        except Exception:
            if config_path.endswith('.msgpack'):
                return cls._load_msgpack_config(config_path)
            return {}

    @classmethod
    def _load_msgpack_config(cls, path: str) -> Dict:
        try:
            with open(path, 'rb') as f:
                data = msgpack.load(f, raw=False)
            if isinstance(data, dict):
                commands = {}
                for sys_type, sys_data in data.items():
                    if isinstance(sys_data, dict) and "mapping" in sys_data:
                        mapping = sys_data["mapping"]
                        for cmd in mapping.get("builtins", {}).keys():
                            commands[cmd] = {"subcommands": [], "options": [], "arguments": []}
                        for cmd in mapping.get("system", []):
                            commands[cmd] = {"subcommands": [], "options": [], "arguments": []}
                        for cmd in mapping.get("tools", {}).keys():
                            commands[cmd] = {"subcommands": [], "options": [], "arguments": []}
                cls._CMD_CONFIG_CACHE[path] = commands
                return commands
        except Exception:
            pass
        return {}

    @classmethod
    def get_commands(cls, config_path: str) -> List[str]:
        config = cls.load_config(config_path)
        return list(config.keys())

    @classmethod
    def get_subcommands(cls, config_path: str, cmd: str) -> List[str]:
        config = cls.load_config(config_path)
        cmd_config = config.get(cmd, {})
        subcmds = cmd_config.get("subcommands", [])
        if isinstance(subcmds, dict):
            return list(subcmds.keys())
        if isinstance(subcmds, list):
            return [sc.get("name", sc) if isinstance(sc, dict) else sc for sc in subcmds]
        return []

    @classmethod
    def get_options(cls, config_path: str, cmd: str, subcmd: str = "") -> List[str]:
        config = cls.load_config(config_path)
        cmd_config = config.get(cmd, {})

        if subcmd:
            subcmds = cmd_config.get("subcommands", {})
            if isinstance(subcmds, dict):
                sub_node = subcmds.get(subcmd, {})
                if isinstance(sub_node, dict):
                    return sub_node.get("options", [])
            elif isinstance(subcmds, list):
                for sc in subcmds:
                    if isinstance(sc, dict) and sc.get("name") == subcmd:
                        return sc.get("options", [])

        return cmd_config.get("options", [])

    @classmethod
    def get_arguments(cls, config_path: str, cmd: str, subcmd: str = "") -> List[str]:
        config = cls.load_config(config_path)
        cmd_config = config.get(cmd, {})

        if subcmd:
            subcmds = cmd_config.get("subcommands", {})
            if isinstance(subcmds, dict):
                sub_node = subcmds.get(subcmd, {})
                if isinstance(sub_node, dict):
                    args = sub_node.get("arguments", [])
                    if isinstance(args, list):
                        return args
            elif isinstance(subcmds, list):
                for sc in subcmds:
                    if isinstance(sc, dict) and sc.get("name") == subcmd:
                        args = sc.get("arguments", [])
                        if isinstance(args, list):
                            return args

        args = cmd_config.get("arguments", [])
        return args if isinstance(args, list) else []


# ===================== 命令树（AST 式逐级补全）====================

class CommandNode:
    """规范化的命令规格节点，支持嵌套子命令 + 类型化参数。

    字段：
        options:       该层级可用的选项列表
        subcommands:   下一级子命令（name → CommandNode）
        arguments:     该层级的固定候选值（如 ["zh","en","Chinese","English"]）
        argument_type: 类型化参数（"file" / "dir" / "any" / "value" / ""）
        multiple_args: 该参数是否可重复出现
    """
    __slots__ = ("options", "subcommands", "arguments", "argument_type", "multiple_args")

    def __init__(self):
        self.options: List[str] = []
        self.subcommands: Dict[str, "CommandNode"] = {}
        self.arguments: List[str] = []
        self.argument_type: str = ""
        self.multiple_args: bool = False

    def merge(self, other: "CommandNode") -> None:
        for o in other.options:
            if o not in self.options:
                self.options.append(o)
        for a in other.arguments:
            if a not in self.arguments:
                self.arguments.append(a)
        if not self.argument_type and other.argument_type:
            self.argument_type = other.argument_type
        if other.multiple_args:
            self.multiple_args = True
        for name, child in other.subcommands.items():
            if name in self.subcommands:
                self.subcommands[name].merge(child)
            else:
                self.subcommands[name] = child

    def is_empty(self) -> bool:
        return not (self.options or self.subcommands
                    or self.arguments or self.argument_type)


def _normalize_command_spec(spec: Any) -> CommandNode:
    """把任意原始 JSON 规格归一化为 CommandNode（向后兼容旧格式）。

    支持的写法：
        - 数组：["adv", "mid", "low"]                      → 子命令名列表
        - 对象：
            {
              "options": ["-m", "-t"],
              "subcommands": [...数组 或 对象...],
              "arguments": ["zh","en"] 或 {"type":"file","values":[...]} 或 "file",
              "type": "file",
              "multiple": true
            }
    """
    node = CommandNode()

    # 裸数组：子命令名列表
    if isinstance(spec, list):
        for item in spec:
            if isinstance(item, str):
                node.subcommands.setdefault(item, CommandNode())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("command") or ""
                if isinstance(name, str) and name:
                    node.subcommands[name] = _normalize_command_spec(item)
        return node

    if not isinstance(spec, dict):
        return node

    # options
    opts = spec.get("options")
    if isinstance(opts, list):
        for o in opts:
            if isinstance(o, str):
                node.options.append(o)

    # arguments：数组 / 对象 / 字符串 三种形态
    args_raw = spec.get("arguments")
    if isinstance(args_raw, list):
        for a in args_raw:
            if isinstance(a, str):
                node.arguments.append(a)
    elif isinstance(args_raw, dict):
        typ = args_raw.get("type")
        if isinstance(typ, str):
            node.argument_type = typ.lower()
        for v in (args_raw.get("values") or []):
            if isinstance(v, str):
                node.arguments.append(v)
    elif isinstance(args_raw, str):
        node.argument_type = args_raw.lower()

    # 顶层 type 简写
    if not node.argument_type:
        typ = spec.get("type")
        if isinstance(typ, str):
            node.argument_type = typ.lower()

    if spec.get("multiple"):
        node.multiple_args = True

    # subcommands：数组（旧）或对象（新）
    sub_raw = spec.get("subcommands")
    if isinstance(sub_raw, list):
        for sc in sub_raw:
            if isinstance(sc, str):
                node.subcommands.setdefault(sc, CommandNode())
            elif isinstance(sc, dict):
                name = sc.get("name") or sc.get("command") or ""
                if isinstance(name, str) and name:
                    node.subcommands[name] = _normalize_command_spec(sc)
    elif isinstance(sub_raw, dict):
        for name, sub_spec in sub_raw.items():
            if isinstance(name, str):
                node.subcommands[name] = _normalize_command_spec(sub_spec)

    return node


class CommandTree:
    """命令树，一次构建、多次查询。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.root: Dict[str, CommandNode] = {}
        if config:
            self.load(config)

    def load(self, config: Dict[str, Any]) -> None:
        for name, spec in config.items():
            if not isinstance(name, str):
                continue
            node = _normalize_command_spec(spec)
            if name in self.root:
                self.root[name].merge(node)
            else:
                self.root[name] = node

    def get(self, cmd: str) -> Optional[CommandNode]:
        return self.root.get(cmd)

    def lookup(self, path: List[str]) -> Optional[CommandNode]:
        if not path:
            return None
        node = self.root.get(path[0])
        if node is None:
            return None
        for name in path[1:]:
            child = node.subcommands.get(name)
            if child is None:
                return None
            node = child
        return node

    def names(self) -> List[str]:
        return list(self.root.keys())


# ===================== 命令频率记录 =====================
class CommandFrequency:
    def __init__(self, user_home_dir: Optional[str] = None, history_file_path: Optional[str] = None):
        self.user_home_dir = user_home_dir
        if user_home_dir:
            self.file_path = os.path.join(user_home_dir, ".com_used.json")
        else:
            self.file_path = os.path.join(str(Path.home()), ".com_used.json")

        self.history_file_path = history_file_path
        self.freq: Dict[str, int] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._save_timer: Optional[threading.Timer] = None

        self._load()
        if self.history_file_path and os.path.exists(self.history_file_path):
            self._async_load_from_history()

    def _load(self):
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.freq = data
        except Exception:
            pass

    def _async_load_from_history(self):
        def _scan():
            try:
                if not self.history_file_path or not os.path.exists(self.history_file_path):
                    return
                with open(self.history_file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                temp_freq: Dict[str, int] = {}
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    first_word = line.split()[0] if ' ' in line else line
                    if first_word:
                        temp_freq[first_word] = temp_freq.get(first_word, 0) + 1
                with self._lock:
                    for cmd, count in temp_freq.items():
                        self.freq[cmd] = self.freq.get(cmd, 0) + count
                    self._dirty = True
                self._schedule_save()
            except Exception:
                pass
        thread = threading.Thread(target=_scan, daemon=True)
        thread.start()

    def _schedule_save(self):
        with self._lock:
            self._dirty = True
            if self._save_timer:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(3.0, self._do_save)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _do_save(self):
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            try:
                os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
                with open(self.file_path, 'w', encoding='utf-8') as f:
                    json.dump(self.freq, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    def record(self, cmd: str):
        cmd = cmd.strip()
        if not cmd:
            return
        cmd_name = cmd.split()[0] if ' ' in cmd else cmd
        if not cmd_name:
            return
        with self._lock:
            self.freq[cmd_name] = self.freq.get(cmd_name, 0) + 1
            self._dirty = True
        self._schedule_save()

    def get_freq(self, cmd: str) -> int:
        with self._lock:
            return self.freq.get(cmd, 0)

    def get_sorted_commands(self, commands: Iterable[str]) -> List[str]:
        with self._lock:
            cmd_list = list(commands)
            cmd_list.sort(key=lambda c: (-self.freq.get(c, 0), c.lower()))
            return cmd_list

    def set_user_home_dir(self, user_home_dir: str, history_file_path: Optional[str] = None):
        with self._lock:
            self.user_home_dir = user_home_dir
            new_path = os.path.join(user_home_dir, ".com_used.json") if user_home_dir else self.file_path
            if new_path != self.file_path:
                self.file_path = new_path
                self.freq.clear()
                self._load()
            if history_file_path:
                self.history_file_path = history_file_path
                self._async_load_from_history()
            self._dirty = False

    def flush(self):
        if self._save_timer:
            self._save_timer.cancel()
            self._save_timer = None
        self._do_save()

_COMMAND_FREQ: Optional[CommandFrequency] = None

def get_command_freq(user_home_dir: str = "", history_file_path: str = "") -> CommandFrequency:
    global _COMMAND_FREQ
    if _COMMAND_FREQ is None:
        _COMMAND_FREQ = CommandFrequency(user_home_dir, history_file_path)
    else:
        if user_home_dir and user_home_dir != _COMMAND_FREQ.user_home_dir:
            _COMMAND_FREQ.set_user_home_dir(user_home_dir, history_file_path)
    return _COMMAND_FREQ


# ===================== 命令缓存 =====================
class CommandCache:
    def __init__(self, user_home_dir: str, cmd_config_path: str, com_cmd_config_path: str = ""):
        self.user_home_dir = user_home_dir
        self.cmd_config_path = cmd_config_path
        self.com_cmd_config_path = com_cmd_config_path
        self.cache_dir = Path(user_home_dir) / ".cache" / "onyx" / "onyx"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "ter_cmd.msgpack"

        self._data: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._save_timer: Optional[threading.Timer] = None
        self._load()
        self._start_async_updater()

    def _load(self) -> None:
        try:
            if self.cache_file.exists() and os.path.exists(self.cmd_config_path):
                cache_mtime = self.cache_file.stat().st_mtime
                src_mtime = os.path.getmtime(self.cmd_config_path)
                com_cmd_mtime = 0
                if self.com_cmd_config_path and os.path.exists(self.com_cmd_config_path):
                    com_cmd_mtime = os.path.getmtime(self.com_cmd_config_path)
                max_src_mtime = max(src_mtime, com_cmd_mtime)

                if cache_mtime >= max_src_mtime:
                    with open(self.cache_file, 'rb') as f:
                        if HAS_MSGPACK:
                            data = msgpack.unpackb(f.read(), raw=False)
                        else:
                            data = json.loads(f.read().decode('utf-8'))
                    with self._lock:
                        self._data = data
                    return
        except Exception:
            pass

        self._rebuild()

    def _rebuild(self) -> None:
        config = CommandConfigLoader.load_config(self.cmd_config_path)
        commands = list(config.keys())
        subcommands_map = {}
        options_map = {}
        arguments_map = {}

        for cmd, cfg in config.items():
            self._flatten_cmd(cmd, cfg, subcommands_map, options_map, arguments_map)

        if self.com_cmd_config_path and os.path.exists(self.com_cmd_config_path):
            com_cmd_config = CommandConfigLoader.load_config(self.com_cmd_config_path)
            for cmd, cfg in com_cmd_config.items():
                if cmd not in commands:
                    commands.append(cmd)
                self._flatten_cmd(cmd, cfg, subcommands_map, options_map, arguments_map, merge=True)

        with self._lock:
            self._data = {
                "commands": commands,
                "subcommands_map": subcommands_map,
                "options_map": options_map,
                "arguments_map": arguments_map,
                "timestamp": time.time()
            }
        self._schedule_save()

    @staticmethod
    def _flatten_cmd(cmd: str, cfg: Any,
                     subcommands_map: Dict[str, List[str]],
                     options_map: Dict[str, List[str]],
                     arguments_map: Dict[str, List[str]],
                     merge: bool = False) -> None:
        """把单个命令的规格展平成三张表（保持旧行为，供外部调用方使用）。

        兼容：
            subcommands 是数组（旧）或对象（新）
            arguments 是数组 / 对象 / 字符串
        """
        node = _normalize_command_spec(cfg)

        # 一级子命令
        subcmd_names = list(node.subcommands.keys())
        if merge:
            existing = subcommands_map.get(cmd, [])
            subcommands_map[cmd] = list(dict.fromkeys(existing + subcmd_names))
        else:
            subcommands_map[cmd] = subcmd_names

        # 一级 options / arguments
        if node.options:
            if merge:
                existing = options_map.get(cmd, [])
                options_map[cmd] = list(dict.fromkeys(existing + node.options))
            else:
                options_map[cmd] = list(node.options)
        if node.arguments:
            if merge:
                existing = arguments_map.get(cmd, [])
                arguments_map[cmd] = list(dict.fromkeys(existing + node.arguments))
            else:
                arguments_map[cmd] = list(node.arguments)

        # 递归展开二级以上（"cmd:sub" / "cmd:sub:sub2" ...）
        def _walk(prefix: str, n: CommandNode) -> None:
            for sc_name, sc_node in n.subcommands.items():
                key = f"{prefix}:{sc_name}"
                if sc_node.options:
                    if merge:
                        existing = options_map.get(key, [])
                        options_map[key] = list(dict.fromkeys(existing + sc_node.options))
                    else:
                        options_map[key] = list(sc_node.options)
                if sc_node.arguments:
                    if merge:
                        existing = arguments_map.get(key, [])
                        arguments_map[key] = list(dict.fromkeys(existing + sc_node.arguments))
                    else:
                        arguments_map[key] = list(sc_node.arguments)
                _walk(key, sc_node)

        _walk(cmd, node)

    def _schedule_save(self) -> None:
        with self._lock:
            self._dirty = True
            if self._save_timer:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(5.0, self._do_save)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _do_save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            try:
                with open(self.cache_file, 'wb') as f:
                    if HAS_MSGPACK:
                        f.write(msgpack.packb(self._data, use_bin_type=True))
                    else:
                        f.write(json.dumps(self._data, ensure_ascii=False).encode('utf-8'))
            except Exception:
                pass

    def _start_async_updater(self) -> None:
        def updater():
            last_mtime = 0
            if os.path.exists(self.cmd_config_path):
                last_mtime = os.path.getmtime(self.cmd_config_path)
            if self.com_cmd_config_path and os.path.exists(self.com_cmd_config_path):
                com_mtime = os.path.getmtime(self.com_cmd_config_path)
                last_mtime = max(last_mtime, com_mtime)

            _watchdog_stop = threading.Event()
            while not _watchdog_stop.is_set():
                _watchdog_stop.wait(60)
                try:
                    current_mtime = 0
                    if os.path.exists(self.cmd_config_path):
                        current_mtime = os.path.getmtime(self.cmd_config_path)
                    if self.com_cmd_config_path and os.path.exists(self.com_cmd_config_path):
                        com_mtime = os.path.getmtime(self.com_cmd_config_path)
                        current_mtime = max(current_mtime, com_mtime)

                    if current_mtime > last_mtime:
                        self._rebuild()
                        last_mtime = current_mtime
                except Exception:
                    pass

        thread = threading.Thread(target=updater, daemon=True)
        thread.start()

    def get_commands(self) -> List[str]:
        with self._lock:
            return self._data.get("commands", [])

    def get_subcommands_map(self) -> Dict[str, List[str]]:
        with self._lock:
            return self._data.get("subcommands_map", {}).copy()

    def get_options_map(self) -> Dict[str, List[str]]:
        with self._lock:
            return self._data.get("options_map", {}).copy()

    def get_arguments_map(self) -> Dict[str, List[str]]:
        with self._lock:
            return self._data.get("arguments_map", {}).copy()

_CMD_CACHE: Optional[CommandCache] = None

def get_command_cache(user_home_dir: str = "", cmd_config_path: str = "", com_cmd_config_path: str = "") -> CommandCache:
    global _CMD_CACHE
    if _CMD_CACHE is None and user_home_dir and cmd_config_path:
        _CMD_CACHE = CommandCache(user_home_dir, cmd_config_path, com_cmd_config_path)
    return _CMD_CACHE


# ===================== AST 语法分词器 =====================
class ShellAstTokenizer:
    """
    基于状态机的 Shell 命令 AST 分词器
    单遍扫描，将 shell 命令字符串拆分为 (style, text) 语义令牌序列
    """

    __slots__ = ('valid_commands', 'virtual_root', 'sys_type')

    def __init__(self, valid_commands: set = None, virtual_root: str = "", sys_type: str = 'bash'):
        self.valid_commands = valid_commands or set()
        self.virtual_root = virtual_root
        self.sys_type = sys_type or 'bash'

    def _escape_char(self, in_quotes: bool = False) -> str:
        if self.sys_type == 'cmd':
            return '' if in_quotes else '^'
        if self.sys_type == 'powershell':
            return '`'
        return '\\'

    def tokenize(self, text: str) -> List[Tuple[str, str]]:
        """主入口：返回 [(style, text), ...] 列表"""
        tokens = []
        i = 0
        n = len(text)

        while i < n:
            # 跳过空白
            if text[i].isspace():
                j = i
                while j < n and text[j].isspace():
                    j += 1
                tokens.append(('', text[i:j]))
                i = j
                continue

            # 注释
            if text[i] == '#':
                j = i
                while j < n and text[j] != '\n':
                    j += 1
                tokens.append(('ansiwhite italic', text[i:j]))
                i = j
                continue

            # 重定向操作符
            redir_len = self._match_redirect(text, i, n)
            if redir_len:
                tokens.append((COLORS['separator'], text[i:i+redir_len]))
                i += redir_len
                continue

            # 管道 / 逻辑操作符 / 分隔符
            sep_len = self._match_separator(text, i, n)
            if sep_len:
                tokens.append((COLORS['separator'], text[i:i+sep_len]))
                i += sep_len
                continue

            # 子shell / 大括号组
            if text[i] in '()':
                end = self._find_matching_paren(text, i, n)
                if end > i:
                    tokens.append((COLORS['separator'], '('))
                    inner = self.tokenize(text[i+1:end])
                    tokens.extend(inner)
                    tokens.append((COLORS['separator'], ')'))
                    i = end + 1
                    continue
                else:
                    tokens.append((COLORS['separator'], text[i]))
                    i += 1
                    continue

            # 花括号展开/组 {a,b} 或代码块
            if text[i] == '{':
                j = i + 1
                depth = 1
                while j < n and depth > 0:
                    if text[j] == '{':
                        depth += 1
                    elif text[j] == '}':
                        depth -= 1
                    j += 1
                tokens.append((COLORS['separator'], text[i:j]))
                i = j
                continue

            if text[i] == '}':
                tokens.append((COLORS['separator'], text[i]))
                i += 1
                continue

            # 引号字符串
            if text[i] in ('"', "'"):
                q_tokens, consumed = self._tokenize_quoted_string(text, i, n)
                tokens.extend(q_tokens)
                i += consumed
                continue

            # 反引号命令替换
            if self.sys_type != 'powershell' and text[i] == '`':
                end = text.find('`', i + 1)
                if end == -1:
                    tokens.append((COLORS['variable'], text[i:]))
                    i = n
                    continue
                inner_tokens = self.tokenize(text[i+1:end])
                tokens.append((COLORS['variable'], '`'))
                tokens.extend(inner_tokens)
                tokens.append((COLORS['variable'], '`'))
                i = end + 1
                continue

            # 普通词法单元
            word_start = i
            word_end = self._read_word(text, i, n)

            if word_end <= word_start:
                i += 1
                continue

            word = text[word_start:word_end]
            is_first_word = not self._has_meaningful_token(tokens)

            word_tokens = self._classify_word(
                word, text, word_start, word_end, n, is_first_word=is_first_word
            )
            tokens.extend(word_tokens)
            i = word_end

        return tokens

    def _has_meaningful_token(self, tokens: List[Tuple[str, str]]) -> bool:
        """判断 tokens 中是否已经存在「有意义」的 token（非空白、非分隔符）。"""
        for style, text in tokens:
            if text.strip() and style != COLORS['separator']:
                return True
        return False

    # ── 匹配辅助 ──

    def _match_redirect(self, text: str, i: int, n: int) -> int:
        if i + 1 < n and text[i].isdigit():
            if text[i+1] == '>':
                if i + 2 < n and text[i+2] == '>':
                    return 3
                elif i + 2 < n and text[i+2] == '&':
                    if i + 3 < n and text[i+3] == '1':
                        return 4
                    return 3
                return 2
            return 0

        if i + 1 < n and text[i] == '&' and text[i+1] == '>':
            return 2

        if i + 1 < n and text[i] == '>' and text[i+1] == '|':
            return 2

        if i + 1 < n and text[i:i+2] == '>>':
            return 2

        if text[i] == '>':
            return 1

        if i + 2 < n and text[i:i+3] == '<<-':
            return 3

        if i + 1 < n and text[i:i+2] == '<<':
            return 2

        if text[i] == '<':
            return 1

        return 0

    def _match_separator(self, text: str, i: int, n: int) -> int:
        if i + 1 < n:
            pair = text[i:i+2]
            if pair in ('&&', '||', ';;', ';&'):
                return 2

        if text[i] in ('|', ';', '&'):
            return 1

        return 0

    def _find_matching_paren(self, text: str, i: int, n: int) -> int:
        if i >= n or text[i] not in '([':
            return -1

        open_char = text[i]
        close_char = ')' if open_char == '(' else ']'

        depth = 1
        j = i + 1
        in_single = False
        in_double = False

        while j < n and depth > 0:
            c = text[j]

            if in_single:
                if c == "'":
                    in_single = False
                j += 1
                continue

            if in_double:
                if c == '\\':
                    j += 2
                    continue
                if c == '"':
                    in_double = False
                j += 1
                continue

            if c == "'":
                in_single = True
            elif c == '"':
                in_double = True
            elif c == '\\':
                j += 1
            elif c in '([':
                depth += 1
            elif c in ')]':
                depth -= 1
                if depth == 0 and c != close_char:
                    return -1
            elif c == '$' and j + 1 < n and text[j+1] == '(':
                depth += 1
                j += 1

            j += 1

        if depth == 0:
            return j - 1
        return -1

    def _read_word(self, text: str, i: int, n: int) -> int:
        j = i
        in_single = False
        in_double = False

        esc_inner = self._escape_char(in_quotes=True)
        esc_outer = self._escape_char(in_quotes=False)

        while j < n:
            c = text[j]

            if in_single:
                if c == "'":
                    in_single = False
                j += 1
                continue

            if in_double:
                if esc_inner and c == esc_inner and j + 1 < n:
                    j += 2
                    continue
                if c == '"':
                    in_double = False
                elif c == '$' and j + 1 < n and text[j+1] == '(':
                    paren_depth = 1
                    k = j + 2
                    while k < n and paren_depth > 0:
                        if text[k] == '(':
                            paren_depth += 1
                        elif text[k] == ')':
                            paren_depth -= 1
                        k += 1
                    j = k
                    continue
                elif c == '`':
                    end = text.find('`', j + 1)
                    if end != -1:
                        j = end + 1
                        continue
                j += 1
                continue

            if c.isspace() or c in '|;&<>()[]{}#' or (c == '`' and self.sys_type != 'powershell'):
                break
            if esc_outer and c == esc_outer and j + 1 < n:
                j += 2
                continue
            if c in ('"', "'"):
                if c == "'":
                    in_single = True
                else:
                    in_double = True
                j += 1
                continue

            j += 1

        return j

    def _tokenize_quoted_string(
        self, text: str, i: int, n: int
    ) -> Tuple[List[Tuple[str, str]], int]:
        """分词引号字符串（含内部变量展开）。"""
        tokens: List[Tuple[str, str]] = []
        quote = text[i]
        start = i
        j = i + 1

        if quote == "'":
            end = text.find("'", j)
            if end == -1:
                tokens.append(('ansired bold', text[i:]))
                return tokens, n - start
            tokens.append((COLORS['string'], text[i:end+1]))
            return tokens, end + 1 - start

        # 双引号
        tokens.append((COLORS['string'], '"'))
        k = j
        esc = self._escape_char(in_quotes=True)

        while k < n:
            c = text[k]
            if c == '"':
                tokens.append((COLORS['string'], '"'))
                k += 1
                break
            elif esc and c == esc and k + 1 < n:
                tokens.append((COLORS['string'], text[k:k+2]))
                k += 2
            elif c == '$':
                var_tokens = self._tokenize_variable(text, k, n)
                tokens.extend(var_tokens)
                consumed_var = sum(len(t[1]) for t in var_tokens)
                k += consumed_var
            elif self.sys_type != 'powershell' and c == '`':
                end = text.find('`', k + 1)
                if end == -1:
                    tokens.append((COLORS['variable'], text[k:]))
                    k = n
                else:
                    tokens.append((COLORS['variable'], '`'))
                    inner = self.tokenize(text[k+1:end])
                    tokens.extend(inner)
                    tokens.append((COLORS['variable'], '`'))
                    k = end + 1
            else:
                tokens.append((COLORS['string'], c))
                k += 1

        return tokens, k - start

    def _tokenize_variable(self, text: str, i: int, n: int) -> List[Tuple[str, str]]:
        if i >= n or text[i] != '$':
            return [(COLORS['string'], '$')]

        # $((算术))
        if i + 2 < n and text[i:i+3] == '((':
            end = text.find('))', i + 3)
            if end != -1:
                return [(COLORS['variable'], text[i:end+2])]
            return [(COLORS['variable'], text[i:])]

        # $(命令替换)
        if i + 1 < n and text[i+1] == '(':
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if text[j] == '(':
                    depth += 1
                elif text[j] == ')':
                    depth -= 1
                elif text[j] == "'":
                    sq_end = text.find("'", j + 1)
                    j = sq_end if sq_end != -1 else n
                    continue
                elif text[j] == '"':
                    dq_end = text.find('"', j + 1)
                    j = dq_end if dq_end != -1 else n
                    continue
                j += 1
            if depth == 0:
                inner_start = i + 2
                inner_end = j - 1
                inner = self.tokenize(text[inner_start:inner_end]) if inner_end > inner_start else []
                result = [(COLORS['variable'], '$'), (COLORS['separator'], '(')]
                result.extend(inner)
                result.append((COLORS['separator'], ')'))
                return result
            return [(COLORS['variable'], text[i:])]

        # ${变量}
        if i + 1 < n and text[i+1] == '{':
            end = text.find('}', i + 2)
            if end != -1:
                return [(COLORS['variable'], text[i:end+1])]
            return [(COLORS['variable'], text[i:])]

        # $变量名
        j = i + 1
        if j < n and text[j].isalpha():
            while j < n and (text[j].isalnum() or text[j] == '_'):
                j += 1
            return [(COLORS['variable'], text[i:j])]

        if j < n and text[j] in '?!$#*-@0123456789':
            return [(COLORS['variable'], text[i:j+1])]

        return [(COLORS['variable'], '$')]

    def _classify_word(
        self,
        word: str,
        text: str,
        start: int,
        end: int,
        n: int,
        is_first_word: bool = False,
    ) -> List[Tuple[str, str]]:
        """对完整的 word 进行分类。"""
        # KEY=value 赋值
        eq_pos = word.find('=')
        if eq_pos > 0:
            key = word[:eq_pos]
            if key.isidentifier():
                return [('ansiyellow', word)]

        # 变量引用 $VAR
        if word.startswith('$') and len(word) > 1:
            return [(COLORS['variable'], word)]

        # 选项 -x / --xxx
        if word.startswith('-'):
            return [(COLORS['option'], word)]

        # 路径
        if self._looks_like_path(word):
            return self._tokenize_path(word)

        # 命令：仅首词
        if is_first_word and word in self.valid_commands:
            return [(COLORS['command'], word)]

        return [(COLORS['string'], word)]

    def _tokenize_path(self, word: str) -> List[Tuple[str, str]]:
        try:
            expanded = PathResolver.expand_path(word, self.virtual_root)
            if self.virtual_root and word.startswith('/'):
                expanded = PathResolver.normalize(word, self.virtual_root)
            if _PATH_EXISTENCE_CACHE.exists(expanded):
                return [(COLORS['path'], word)]
            else:
                return [(COLORS['path_invalid'], word)]
        except Exception:
            return [(COLORS['path_invalid'], word)]

    def _looks_like_path(self, text: str) -> bool:
        if text in ('.', '..'):
            return True
        if text.startswith('~') and (len(text) == 1 or text[1] == '/'):
            return True
        if '/' in text:
            return True
        if len(text) > 1 and text[1] == ':' and text[0].isalpha():
            return True
        if text.startswith('\\\\'):
            return True
        if '\\' in text:
            if text.startswith('\\') and len(text) >= 2:
                c = text[1]
                if c in '$`"\'\\' or c in 'nrt0a':
                    return False
            if not os.name == 'nt':
                return False
            return True
        return False


# ===================== AST 增强型语法高亮器 =====================
class CommandLexer(Lexer):
    """AST 驱动的命令行语法高亮器"""

    def __init__(self, valid_commands: Optional[set] = None, virtual_root: str = "", sys_type: str = None):
        self.valid_commands = valid_commands if valid_commands is not None else set()
        self.virtual_root = virtual_root
        self.sys_type = sys_type or get_detected_terminal_type()
        self._tokenizer = ShellAstTokenizer(self.valid_commands, self.virtual_root, self.sys_type)

    def lex_document(self, document: Document) -> Callable[[int], List[Tuple[str, str]]]:
        text = document.text
        text_lines = text.split("\n")

        def get_line_tokens(lineno: int) -> List[Tuple[str, str]]:
            if lineno >= len(text_lines):
                return []

            line_text = text_lines[lineno]

            tokens = self._tokenizer.tokenize(line_text)
            tokens = self._apply_first_word_as_command(tokens)

            global _HIGHLIGHT_TOKEN
            if _HIGHLIGHT_TOKEN:
                if _HIGHLIGHT_TOKEN in text:
                    if _HIGHLIGHT_TOKEN in line_text:
                        hl_style = COLORS.get("history_match", "bg:#ffffff #000000")
                        tokens = _overlay_highlight(tokens, line_text, _HIGHLIGHT_TOKEN, hl_style)
                else:
                    _HIGHLIGHT_TOKEN = ""
                    if _nav_reset_callback:
                        _nav_reset_callback()

            return tokens

        return get_line_tokens

    def _apply_first_word_as_command(self, tokens: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """将第一个有意义 token 强制标注为命令（含路径命令检测）"""
        result = []
        found_first = False

        for style, text in tokens:
            if not found_first:
                if text.strip() and style != COLORS['separator']:
                    found_first = True
                    if text in self.valid_commands:
                        result.append((COLORS['command'], text))
                    elif self._tokenizer._looks_like_path(text):
                        try:
                            expanded = PathResolver.expand_path(text, self.virtual_root)
                            if self.virtual_root and text.startswith('/'):
                                expanded = PathResolver.normalize(text, self.virtual_root)
                            if _PATH_EXISTENCE_CACHE.exists(expanded):
                                result.append((COLORS['path'], text))
                            else:
                                result.append((COLORS['path_invalid'], text))
                        except Exception:
                            result.append((COLORS['command_invalid'], text))
                    else:
                        result.append((COLORS['command_invalid'], text))
                    continue

            result.append((style, text))

        return result


# ===================== 智能虚影补全 =====================
class SmartAutoSuggest(AutoSuggest):
    """智能虚影补全器"""

    def __init__(self, completer: Optional['SmartCompleter'] = None):
        self.completer = completer

    def _ghost_from_completion(self, comp, text: str) -> Optional[Suggestion]:
        comp_text = getattr(comp, "text", "") or ""
        if not comp_text:
            return None
        sp = getattr(comp, "start_position", 0) or 0
        if sp == 0:
            return Suggestion(comp_text)
        if sp < 0:
            n = min(-sp, len(text))
            current_word = text[len(text) - n:] if n else ""
            if current_word and comp_text.startswith(current_word):
                suffix = comp_text[len(current_word):]
                return Suggestion(suffix) if suffix else None
        return None

    def _first_completion(self, buffer, document):
        cs = getattr(buffer, "complete_state", None)
        comp = getattr(cs, "current_completion", None) if cs is not None else None
        if comp is None and cs is not None:
            comps = getattr(cs, "completions", None) or []
            comp = comps[0] if comps else None
        if comp is not None:
            return comp
        try:
            from .kb import is_completion_locked
            if is_completion_locked():
                return None
        except Exception:
            pass
        try:
            for comp in self.completer.get_completions(document, None):
                return comp
        except Exception:
            return None
        return None

    def get_suggestion(self, buffer, document):
        if not self.completer:
            return None

        text = document.text_before_cursor
        if not text or text.isspace():
            return None

        suggestion_text = self.completer.get_smart_suggestion(text)
        if suggestion_text:
            return Suggestion(suggestion_text)

        comp = self._first_completion(buffer, document)
        if comp is None:
            return None
        return self._ghost_from_completion(comp, text)


class FirstSuggestionAutoSuggest(AutoSuggest):
    """原有的首补全建议（保留兼容）"""
    def __init__(self, completer: Optional[Completer] = None):
        self.completer = completer

    def get_suggestion(self, buffer, document):
        if self.completer:
            completions = list(self.completer.get_completions(document, None))
            if completions:
                first = completions[0]
                suggestion_text = first.text
                current_word = document.get_word_before_cursor(WORD=True)
                if suggestion_text.startswith(current_word):
                    return Suggestion(suggestion_text[len(current_word):])
        return AutoSuggestFromHistory().get_suggestion(buffer, document)


# ===================== 匹配工具 =====================
def _prefix_match(query: str, candidate: str) -> bool:
    if not query:
        return True
    return candidate.lower().startswith(query.lower())

def _prefix_match_exact(query: str, candidate: str) -> bool:
    return bool(query) and candidate.startswith(query)

def _subsequence_positions(needle: str, haystack: str) -> Optional[List[int]]:
    if not needle:
        return []
    positions: List[int] = []
    start = 0
    for ch in needle:
        idx = haystack.find(ch, start)
        if idx < 0:
            return None
        positions.append(idx)
        start = idx + 1
    return positions


# ===================== 智能补全器 =====================
class SmartCompleter(Completer):

    # 需要路径补全的命令（无显式树定义时的兜底）
    PATH_COMMANDS = {
        'cd', 'ls', 'cat', 'cp', 'mv', 'rm', 'mkdir', 'rmdir',
        'touch', 'chmod', 'chown', 'find', 'grep', 'file', 'stat',
        'python', 'python3', 'source', 'run', 'bash', 'sh', './',
        'nano', 'vim', 'vi', 'emacs', 'less', 'more', 'head', 'tail',
        'dir', 'copy', 'del', 'erase', 'ren', 'rename', 'md', 'rd',
        'type', 'more', 'xcopy', 'robocopy',
    }

    CODE_SHELLS = {
        'python', 'python3', 'python2', 'py', 'ipython',
        'node', 'ruby', 'perl', 'lua',
        'bash', 'sh', 'zsh', 'fish',
    }

    def __init__(self, cmd_list: List[str], show_hidden: bool = True,
                 cmd_config_path: str = "", com_cmd_config_path: str = "",
                 virtual_root: str = "", user_home_dir: str = "",
                 history_buffer: List[str] = None):
        self.original_cmd_list = cmd_list
        self.cmd_list = cmd_list
        self.engine = PathCompleterEngine(show_hidden=show_hidden, use_cache=True, virtual_root=virtual_root)
        self.cmd_config_path = cmd_config_path
        self.com_cmd_config_path = com_cmd_config_path
        self.virtual_root = virtual_root
        self.history_buffer = history_buffer if history_buffer is not None else []
        history_file = os.path.join(user_home_dir, ".onyx_history.txt") if user_home_dir else None
        self.freq_manager = get_command_freq(user_home_dir, history_file)

        self.cmd_cache = get_command_cache(user_home_dir, cmd_config_path, com_cmd_config_path) if user_home_dir and cmd_config_path else None
        self.command_tree = CommandTree()

        # 旧字段（外部可能访问）保留
        self.subcommand_map: Dict[str, List[str]] = {}
        self.option_map: Dict[str, List[str]] = {}
        self.argument_map: Dict[str, List[str]] = {}

        self._load_cmd_config()
        self._update_cmd_list_order()

        self.permission_commands = {'sudo', 'sado'}
        self._multiline_completer = None

    def set_multiline_completer(self, ml_completer: Any):
        self._multiline_completer = ml_completer

    def _load_cmd_config(self):
        """加载补全配置，构建旧版三张表（兼容外部访问）+ 新版命令树。"""
        if self.cmd_cache:
            self.subcommand_map = self.cmd_cache.get_subcommands_map()
            self.option_map = self.cmd_cache.get_options_map()
            self.argument_map = self.cmd_cache.get_arguments_map()
            cached_commands = self.cmd_cache.get_commands()
            self.original_cmd_list = list(set(self.original_cmd_list) | set(cached_commands))

        # 不管有没有 cmd_cache，都把原始 JSON 灌进命令树
        self._build_command_tree()

        # 如果 cmd_cache 没起作用（比如用户没设 user_home_dir），手工展开三张表
        if not self.cmd_cache:
            self._manual_flatten()

    def _manual_flatten(self):
        """在没有 CommandCache 时，从原始 JSON 手工展开 subcommand_map / option_map / argument_map。"""
        for path in (self.cmd_config_path, self.com_cmd_config_path):
            if not path or not os.path.exists(path):
                continue
            config = CommandConfigLoader.load_config(path)
            for cmd, cfg in config.items():
                if cmd not in self.original_cmd_list:
                    self.original_cmd_list.append(cmd)
                CommandCache._flatten_cmd(
                    cmd, cfg,
                    self.subcommand_map, self.option_map, self.argument_map,
                    merge=True,
                )

    def _build_command_tree(self):
        """把 cmd_config_path / com_cmd_config_path 归一化为命令树。"""
        merged: Dict[str, CommandNode] = {}
        for path in (self.cmd_config_path, self.com_cmd_config_path):
            if not path or not os.path.exists(path):
                continue
            cfg = CommandConfigLoader.load_config(path)
            for cmd, spec in cfg.items():
                node = _normalize_command_spec(spec)
                if cmd in merged:
                    merged[cmd].merge(node)
                else:
                    merged[cmd] = node

        self.command_tree = CommandTree()
        for name, node in merged.items():
            self.command_tree.root[name] = node
            if name not in self.original_cmd_list:
                self.original_cmd_list.append(name)

    def _update_cmd_list_order(self):
        if self.original_cmd_list:
            self.cmd_list = self.freq_manager.get_sorted_commands(self.original_cmd_list)

    # ── 分词 / 段提取 ──
    def _split_command(self, text: str) -> List[str]:
        posix = get_posix_mode()
        try:
            return shlex.split(text, posix=posix)
        except ValueError:
            return text.split()

    def _get_last_command_segment(self, document: Document) -> Tuple[str, int]:
        text = document.text_before_cursor
        cursor_pos = document.cursor_position
        last_sep_pos = -1
        for match in re.finditer(r'[;&|]|&&|\|\|', text):
            sep_end = match.end()
            if sep_end <= cursor_pos:
                last_sep_pos = sep_end
        segment_start = max(0, last_sep_pos)
        segment_text = text[segment_start:cursor_pos]
        return segment_text, segment_start

    @staticmethod
    def _has_path_indicators(word: str) -> bool:
        if not word:
            return False
        return (word.startswith(('./', '/', '~/', '../', '.', '..'))
                or os.sep in word)

    # ── 树驱动上下文 ──
    def _get_context(self, document: Document) -> Tuple[str, str, int, str, Optional[CommandNode]]:
        """返回 (ctx_type, current_word, start_pos, cmd, node)。

        ctx_type ∈ {empty, command, permission_cmd, option, tree, path, variable, code, other}
        node 仅当 ctx_type ∈ {option, tree} 时非 None。
        """
        segment_text, _ = self._get_last_command_segment(document)
        if not segment_text:
            return "empty", "", 0, "", None

        # 当前词
        wsi = len(segment_text)
        for i in range(len(segment_text) - 1, -1, -1):
            if segment_text[i].isspace():
                break
            wsi = i
        current_word = "" if wsi == len(segment_text) else segment_text[wsi:]
        start_pos = -len(current_word)

        # 变量补全
        if current_word.startswith('$'):
            sigil = 2 if current_word.startswith('${') else 1
            vp = current_word[sigil:]
            if not (vp and ('/' in vp or '\\' in vp or '}' in vp)):
                return "variable", current_word, start_pos, "", None
        elif current_word.startswith('%') and '%' not in current_word[1:]:
            if get_detected_terminal_type() == 'cmd':
                return "variable", current_word, start_pos, "", None

        before = segment_text[:wsi].strip()
        if not before:
            # 命令名位置
            if self._has_path_indicators(current_word) and not current_word.startswith('-'):
                return "path", current_word, start_pos, "", None
            return "command", current_word, start_pos, "", None

        parts = self._split_command(segment_text)
        if not parts:
            return "other", current_word, start_pos, "", None

        # 跳过权限包装
        cmd_index = 0
        while cmd_index < len(parts) and parts[cmd_index] in self.permission_commands:
            cmd_index += 1

        completed = parts[:-1] if current_word else parts

        if cmd_index >= len(completed):
            if current_word.startswith('-'):
                return "other", current_word, start_pos, "", None
            return "permission_cmd", current_word, start_pos, "", None

        cmd = completed[cmd_index]
        tree_parts = completed[cmd_index:]

        # 代码解释器 → 多行补全
        if cmd in self.CODE_SHELLS and self._multiline_completer and not current_word:
            return "code", current_word, start_pos, cmd, None

        # 树查找
        node = self.command_tree.lookup(tree_parts)

        if node is None:
            # 不在树里 → 退回路径/其他
            if cmd in self.PATH_COMMANDS or self._has_path_indicators(current_word):
                return "path", current_word, start_pos, cmd, None
            return "other", current_word, start_pos, cmd, None

        # 选项优先
        if current_word.startswith('-'):
            if node.options:
                return "option", current_word, start_pos, cmd, node
            return "other", current_word, start_pos, cmd, None

        # 有任意补全信息 → 用树补全
        if not node.is_empty():
            return "tree", current_word, start_pos, cmd, node

        # 空节点 → 退回路径/其他
        if cmd in self.PATH_COMMANDS or self._has_path_indicators(current_word):
            return "path", current_word, start_pos, cmd, None
        return "other", current_word, start_pos, cmd, None

    # ── 虚影建议 ──
    def get_smart_suggestion(self, current_input: str) -> Optional[str]:
        if not current_input:
            return None

        prefix = current_input

        recent_full_command = self._get_most_recent_full_command(prefix)
        if recent_full_command and recent_full_command != prefix:
            return recent_full_command[len(prefix):]

        if prefix and not prefix.endswith(' '):
            parts = prefix.split()
            if len(parts) == 1:
                cmd = parts[0]
                if cmd in self.command_tree.root:
                    subcmds = list(self.command_tree.root[cmd].subcommands.keys())
                    if subcmds:
                        recent_subcmd = self._get_most_recent_subcommand(cmd, subcmds)
                        if recent_subcmd:
                            return f" {recent_subcmd}"

        if not prefix.endswith(' '):
            recent_cmd = self._get_most_recent_command(prefix)
            if recent_cmd and recent_cmd != prefix:
                return recent_cmd[len(prefix):]

        return None

    def _get_most_recent_full_command(self, prefix: str) -> Optional[str]:
        if not self.history_buffer:
            return None
        for cmd in self.history_buffer:
            if '\n' not in cmd and cmd.startswith(prefix):
                return cmd
        return None

    def _get_most_recent_command(self, prefix: str) -> Optional[str]:
        if not self.history_buffer:
            return None
        for cmd in self.history_buffer:
            if '\n' not in cmd and cmd.startswith(prefix):
                parts = cmd.split()
                return parts[0] if parts else cmd
        return None

    def _get_most_recent_subcommand(self, cmd: str, subcmds: List[str]) -> Optional[str]:
        if not self.history_buffer:
            return subcmds[0] if subcmds else None
        full_cmd_prefix = f"{cmd} "
        for history_cmd in self.history_buffer:
            if '\n' not in history_cmd and history_cmd.startswith(full_cmd_prefix):
                parts = history_cmd.split()
                if len(parts) >= 2 and parts[1] in subcmds:
                    return parts[1]
        return subcmds[0] if subcmds else None

    # ── 变量补全 ──
    def _complete_variable(self, current_word: str, start_pos: int):
        if not current_word:
            return
        try:
            keys = list(dict.fromkeys(
                list(os.environ.keys())
                + ["PWD", "OLDPWD", "HOME", "PATH", "USER", "SHELL", "TERM"]
            ))
        except Exception:
            keys = []
        if not keys:
            return
        keys.sort(key=str.lower)
        term_type = get_detected_terminal_type()
        if term_type == 'cmd':
            prefix = current_word[1:]
            for name in keys:
                if name.lower().startswith(prefix.lower()):
                    yield Completion(
                        f"%{name}%",
                        start_position=start_pos,
                        display_meta="env",
                        style=META_COLORS.get('variable', 'ansicyan'),
                    )
            return
        brace = current_word.startswith('${')
        sigil_len = 2 if brace else 1
        prefix = current_word[sigil_len:]
        for name in keys:
            if name.lower().startswith(prefix.lower()):
                text = f"${{{name}}}" if brace else f"${name}"
                yield Completion(
                    text,
                    start_position=start_pos,
                    display_meta="env",
                    style=META_COLORS.get('variable', 'ansicyan'),
                )

    # ── 主入口 ──
    def get_completions(self, document: Document, complete_event):
        self._update_cmd_list_order()
        ctx_type, current_word, start_pos, cmd, node = self._get_context(document)

        if ctx_type == "command":
            yield from self._complete_command(current_word, start_pos)
        elif ctx_type == "permission_cmd":
            yield from self._complete_command(current_word, start_pos, meta_type="permission")
        elif ctx_type == "option" and node is not None:
            yield from self._complete_option(node, current_word, start_pos)
        elif ctx_type == "tree" and node is not None:
            yield from self._complete_tree(node, current_word, start_pos, cmd)
        elif ctx_type == "path":
            yield from self._complete_path(current_word, start_pos)
        elif ctx_type == "variable":
            yield from self._complete_variable(current_word, start_pos)
        elif ctx_type == "code" and self._multiline_completer:
            yield from self._multiline_completer.get_completions(document, complete_event)
        # empty / other → 不产出

    # ── 命令名补全 ──
    def _complete_command(self, current_word: str, start_pos: int, meta_type: str = "command"):
        if meta_type == "permission":
            display_meta = "perm"
            style = "ansiyellow bold"
        else:
            display_meta = META_TEXTS_EN.get('command', 'cmd')
            style = META_COLORS.get('command', 'ansigreen bold')

        safe_start = start_pos
        if safe_start == 0 and current_word:
            safe_start = -len(current_word)
        elif safe_start < 0 and abs(safe_start) > len(current_word):
            safe_start = -len(current_word)

        if not current_word:
            for cmd in self.cmd_list[:100]:
                yield Completion(
                    cmd,
                    start_position=safe_start,
                    display_meta=display_meta,
                    style=style
                )
            return

        prefix_hits = set()
        exact_hits: List[str] = []
        loose_hits: List[str] = []
        for cmd in self.cmd_list:
            if _prefix_match(current_word, cmd):
                prefix_hits.add(cmd)
                if _prefix_match_exact(current_word, cmd):
                    exact_hits.append(cmd)
                else:
                    loose_hits.append(cmd)
        for cmd in exact_hits + loose_hits:
            yield Completion(
                cmd,
                start_position=safe_start,
                display_meta=display_meta,
                style=style
            )

        if len(current_word) >= 2:
            needle = current_word.lower()
            for cmd in self.cmd_list:
                if cmd in prefix_hits:
                    continue
                if _subsequence_positions(needle, cmd.lower()) is not None:
                    yield Completion(
                        cmd,
                        start_position=safe_start,
                        display_meta=display_meta,
                        style=style
                    )

    # ── 树补全 ──
    def _complete_tree(self, node: CommandNode, current_word: str, start_pos: int, cmd: str):
        # 选项优先（- 开头时已在 _get_context 判过，这里再兜一次）
        if current_word.startswith('-') and node.options:
            for opt in node.options:
                if _prefix_match(current_word, opt):
                    yield Completion(
                        opt,
                        start_position=start_pos,
                        display_meta=META_TEXTS_EN.get('option', 'option'),
                        style=META_COLORS.get('option', 'ansired'),
                    )
            return

        # 1) 子命令（下一级）
        if node.subcommands:
            for name in sorted(node.subcommands.keys()):
                if _prefix_match(current_word, name):
                    yield Completion(
                        name,
                        start_position=start_pos,
                        display_meta=META_TEXTS_EN.get('subcommand', 'subcmd'),
                        style=META_COLORS.get('subcommand', 'ansiyellow'),
                    )
            # 选项也一并给出（不少命令 subcommand/option 混用）
            for opt in node.options:
                if _prefix_match(current_word, opt):
                    yield Completion(
                        opt,
                        start_position=start_pos,
                        display_meta=META_TEXTS_EN.get('option', 'option'),
                        style=META_COLORS.get('option', 'ansired'),
                    )
            return

        # 2) 固定参数值
        if node.arguments:
            for val in node.arguments:
                if _prefix_match(current_word, val):
                    yield Completion(
                        val,
                        start_position=start_pos,
                        display_meta=META_TEXTS_EN.get('argument', 'arg'),
                        style=META_COLORS.get('argument', 'ansimagenta'),
                    )
            for opt in node.options:
                if _prefix_match(current_word, opt):
                    yield Completion(
                        opt,
                        start_position=start_pos,
                        display_meta=META_TEXTS_EN.get('option', 'option'),
                        style=META_COLORS.get('option', 'ansired'),
                    )
            return

        # 3) 类型化参数 → 路径
        if node.argument_type == "dir":
            yield from self._complete_path_filtered(current_word, start_pos, "dir")
            return
        if node.argument_type == "file":
            yield from self._complete_path_filtered(current_word, start_pos, "file")
            return
        if node.argument_type in ("value",):
            return  # 自由文本

        # 4) 兜底路径
        yield from self._complete_path(current_word, start_pos)

    def _complete_option(self, node: CommandNode, current_word: str, start_pos: int):
        for opt in node.options:
            if _prefix_match(current_word, opt):
                yield Completion(
                    opt,
                    start_position=start_pos,
                    display_meta=META_TEXTS_EN.get('option', 'option'),
                    style=META_COLORS.get('option', 'ansired'),
                )

    # ── 路径补全 ──
    def _complete_path(self, current_word: str, start_pos: int):
        for comp_text, display_meta, color, rel_start in self.engine.get_completions(current_word, start_pos):
            yield Completion(
                comp_text,
                start_position=rel_start,
                display_meta=display_meta,
                style=color.split()[0] if color else "",
            )

    def _complete_path_filtered(self, current_word: str, start_pos: int, kind: str):
        """kind: 'file' | 'dir'"""
        for comp_text, display_meta, color, rel_start in self.engine.get_completions(current_word, start_pos):
            is_dir = comp_text.endswith(os.sep) or comp_text.endswith('/')
            if kind == "dir" and not is_dir:
                continue
            if kind == "file" and is_dir:
                continue
            yield Completion(
                comp_text,
                start_position=rel_start,
                display_meta=display_meta,
                style=color.split()[0] if color else "",
            )

    # ── 兼容旧接口 ──
    def _complete_subcommand(self, current_word: str, start_pos: int, cmd: str):
        """（兼容）子命令补全：从旧 subcommand_map 取列表。"""
        subcmds = self.subcommand_map.get(cmd, [])
        for subcmd in subcmds:
            if _prefix_match(current_word, subcmd):
                yield Completion(
                    subcmd,
                    start_position=start_pos,
                    display_meta=META_TEXTS_EN.get('subcommand', 'subcmd'),
                    style=META_COLORS.get('subcommand', 'ansiyellow'),
                )

    def _complete_argument(self, current_word: str, start_pos: int, cmd: str, document: Document):
        """（兼容）参数补全：从旧 argument_map 取列表。"""
        segment_text, _ = self._get_last_command_segment(document)
        parts = self._split_command(segment_text)
        subcmd = parts[1] if len(parts) > 1 else ""

        arguments = []
        if subcmd:
            arguments = self.argument_map.get(f"{cmd}:{subcmd}", [])
        if not arguments:
            arguments = self.argument_map.get(cmd, [])

        for arg in arguments:
            if _prefix_match(current_word, arg):
                yield Completion(
                    arg,
                    start_position=start_pos,
                    display_meta=META_TEXTS_EN.get('argument', 'arg'),
                    style=META_COLORS.get('argument', 'ansimagenta'),
                )