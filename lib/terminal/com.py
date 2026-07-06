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
        "completion_page_down": "pagedown"
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
        "max_completions": 100
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

def load_ptk_config() -> Dict[str, Any]:
    """加载 ptk.json 配置，若不存在则生成默认配置"""
    config_path = os.path.expanduser(PTK_CONFIG_PATH)
    config_dir = os.path.dirname(config_path)
    if not os.path.exists(config_dir):
        try:
            os.makedirs(config_dir, mode=0o755, exist_ok=True)
        except Exception:
            pass

    if not os.path.exists(config_path):
        # 生成默认配置
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_PTK_CONFIG, f, indent=2, ensure_ascii=False)
            return DEFAULT_PTK_CONFIG.copy()
        except Exception:
            return DEFAULT_PTK_CONFIG.copy()

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
        # 深度合并默认配置，确保所有键存在
        merged = DEFAULT_PTK_CONFIG.copy()
        for key, value in user_config.items():
            if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                merged[key].update(value)
            else:
                merged[key] = value
        return merged
    except Exception:
        return DEFAULT_PTK_CONFIG.copy()


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
    """根据终端类型返回是否使用 POSIX 模式（shlex.split）"""
    term_type = get_detected_terminal_type()
    return term_type not in ('cmd',)


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
        # 默认路径
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
        # 默认路径
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
    "history_match": "bg:#ffffff #000000 bold",  # white box on matched token
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
    """
    if not hl_token or hl_token not in full_text:
        return tokens

    # 找到所有 hl_token 的起止位置
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

    # 将 spans 转为快速查找集合（字符位置 → 是否在高亮区内）
    hl_set = set()
    for s, e in spans:
        for pos in range(s, e):
            hl_set.add(pos)

    # 遍历 tokens，拆分被高亮区覆盖的部分
    result = []
    char_pos = 0
    for style, text in tokens:
        seg_start = char_pos
        seg_end = char_pos + len(text)
        # 检查 overlap
        overlap = [p for p in range(seg_start, seg_end) if p in hl_set]
        if not overlap:
            result.append((style, text))
        else:
            # 拆分 token：高亮部分和新样式，非高亮保持原样式
            i = 0
            while i < len(text):
                abs_pos = seg_start + i
                if abs_pos in hl_set:
                    # 连续高亮块
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

        # 支持 CMD 的环境变量展开 %VAR%
        if '%' in path:
            def cmd_var_replacer(match):
                var_name = match.group(1)
                return os.environ.get(var_name, match.group(0))
            path = re.sub(r'%([^%]+)%', cmd_var_replacer, path)

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
        """
        分割路径用于补全。
        返回: (目录路径, 文件前缀, 是否为绝对模式)
        """
        if not path:
            return os.getcwd(), "", False

        # 标准化路径分隔符（只用于逻辑判断，不影响实际路径）
        normalized_path = path
        if os.sep == '\\':
            normalized_path = path.replace('/', '\\')
        else:
            normalized_path = path.replace('\\', '/')
        
        # 处理虚拟根目录下的绝对路径（以 / 开头）
        if virtual_root and path.startswith('/'):
            is_absolute_mode = True
            # 处理末尾带分隔符的情况
            if path.endswith('/'):
                if path == '/':
                    return virtual_root, "", True
                # 去掉末尾的 /，返回完整目录
                clean_path = path.rstrip('/')
                dir_path = PathResolver.expand_path(clean_path, virtual_root)
                return dir_path, "", True
            
            # 分割目录和文件名
            last_slash = path.rfind('/')
            if last_slash <= 0:  # 只有 / 或者根目录
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

        # 处理 Windows 绝对路径（如 C:\ 或 D:）
        if os.name == 'nt' and re.match(r'^[A-Za-z]:', path):
            is_absolute_mode = True
            
            # 处理末尾带分隔符的情况
            if path.endswith('\\') or path.endswith('/'):
                if re.match(r'^[A-Za-z]:\\$', path) or re.match(r'^[A-Za-z]:/$', path):
                    return path, "", True
                clean_path = path.rstrip('\\/')
                dir_path = PathResolver.expand_path(clean_path, virtual_root)
                return dir_path, "", True
            
            # 分割目录和文件名
            last_sep = max(path.rfind('\\'), path.rfind('/'))
            if last_sep <= 2:  # 盘符后没有分隔符，如 "C:abc"
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

        # 处理 Unix 绝对路径（以 / 开头，但没有虚拟根目录）
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

        # 相对路径
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
        """
        判断文件是否可执行。
        """
        if not os.path.exists(path):
            return False
        if os.name == 'nt':
            executable_exts = os.environ.get('PATHEXT', '.EXE;.BAT;.CMD;.COM;.PS1').split(';')
            ext = os.path.splitext(path)[1].upper()
            return ext in executable_exts
        return os.access(path, os.X_OK)


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
            return self._list_directory_with_prefix(os.getcwd(), "", start_pos)

        dir_path, file_prefix, _ = PathResolver.split_for_completion(path_prefix, self.virtual_root)

        cache_key = f"{dir_path}:{file_prefix}:{self.show_hidden}:{self.virtual_root}"
        if self.use_cache and self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return [(text, meta, color, start_pos) for text, meta, color, _ in cached]

        completions = self._list_directory_with_prefix(dir_path, file_prefix, start_pos)

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

        # 添加父目录和当前目录（仅在根目录下不添加）
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
        if isinstance(subcmds, list):
            return [sc.get("name", sc) if isinstance(sc, dict) else sc for sc in subcmds]
        return []

    @classmethod
    def get_options(cls, config_path: str, cmd: str, subcmd: str = "") -> List[str]:
        config = cls.load_config(config_path)
        cmd_config = config.get(cmd, {})

        if subcmd:
            for sc in cmd_config.get("subcommands", []):
                if isinstance(sc, dict) and sc.get("name") == subcmd:
                    return sc.get("options", [])

        return cmd_config.get("options", [])

    @classmethod
    def get_arguments(cls, config_path: str, cmd: str, subcmd: str = "") -> List[str]:
        config = cls.load_config(config_path)
        cmd_config = config.get(cmd, {})

        if subcmd:
            for sc in cmd_config.get("subcommands", []):
                if isinstance(sc, dict) and sc.get("name") == subcmd:
                    return sc.get("arguments", [])

        return cmd_config.get("arguments", [])

# ===================== 命令频率记录（异步加载/保存，支持从历史预加载） =====================
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
            subcmds = cfg.get("subcommands", [])
            if isinstance(subcmds, list):
                subcmd_names = [sc.get("name", sc) if isinstance(sc, dict) else sc for sc in subcmds]
                subcommands_map[cmd] = subcmd_names
                for sc in subcmds:
                    if isinstance(sc, dict):
                        sc_name = sc.get("name", "")
                        sc_options = sc.get("options", [])
                        sc_arguments = sc.get("arguments", [])
                        if sc_name:
                            options_map[f"{cmd}:{sc_name}"] = sc_options
                            arguments_map[f"{cmd}:{sc_name}"] = sc_arguments
            options = cfg.get("options", [])
            arguments = cfg.get("arguments", [])
            if options:
                options_map[cmd] = options
            if arguments:
                arguments_map[cmd] = arguments

        if self.com_cmd_config_path and os.path.exists(self.com_cmd_config_path):
            com_cmd_config = CommandConfigLoader.load_config(self.com_cmd_config_path)
            for cmd, cfg in com_cmd_config.items():
                if cmd not in commands:
                    commands.append(cmd)
                
                subcmds = cfg.get("subcommands", [])
                if isinstance(subcmds, list):
                    subcmd_names = [sc.get("name", sc) if isinstance(sc, dict) else sc for sc in subcmds]
                    existing_subcmds = subcommands_map.get(cmd, [])
                    subcommands_map[cmd] = list(set(existing_subcmds + subcmd_names))
                    for sc in subcmds:
                        if isinstance(sc, dict):
                            sc_name = sc.get("name", "")
                            sc_options = sc.get("options", [])
                            sc_arguments = sc.get("arguments", [])
                            if sc_name:
                                key = f"{cmd}:{sc_name}"
                                existing_opts = options_map.get(key, [])
                                options_map[key] = list(set(existing_opts + sc_options))
                                existing_args = arguments_map.get(key, [])
                                arguments_map[key] = list(set(existing_args + sc_arguments))
                
                options = cfg.get("options", [])
                if options:
                    existing_opts = options_map.get(cmd, [])
                    options_map[cmd] = list(set(existing_opts + options))
                
                arguments = cfg.get("arguments", [])
                if arguments:
                    existing_args = arguments_map.get(cmd, [])
                    arguments_map[cmd] = list(set(existing_args + arguments))

        with self._lock:
            self._data = {
                "commands": commands,
                "subcommands_map": subcommands_map,
                "options_map": options_map,
                "arguments_map": arguments_map,
                "timestamp": time.time()
            }
        self._schedule_save()

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

            # 使用 Event.wait 替代 sleep，可被外部唤醒提前退出
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

# ===================== 命令行语法高亮器 =====================
class CommandLexer(Lexer):
    def __init__(self, valid_commands: Optional[set] = None, virtual_root: str = ""):
        self.valid_commands = valid_commands if valid_commands is not None else set()
        self.virtual_root = virtual_root

    def lex_document(self, document: Document) -> Callable[[int], List[Tuple[str, str]]]:
        text = document.text

        def get_line_tokens(lineno: int) -> List[Tuple[str, str]]:
            if lineno != 0:
                return []

            tokens = []
            segments = self._split_by_separators(text)

            for segment_text, is_separator in segments:
                if is_separator:
                    tokens.append((COLORS['separator'], segment_text))
                else:
                    sub_tokens = self._lex_command_segment(segment_text)
                    tokens.extend(sub_tokens)

            # ── 叠加历史导航高亮 token ──
            global _HIGHLIGHT_TOKEN
            if _HIGHLIGHT_TOKEN:
                if _HIGHLIGHT_TOKEN in text:
                    hl_style = COLORS.get("history_match", "bg:#ffffff #000000")
                    tokens = _overlay_highlight(tokens, text, _HIGHLIGHT_TOKEN, hl_style)
                else:
                    # token 已不在当前文本中（用户清空或编辑了）→ 清除高亮+导航状态
                    _HIGHLIGHT_TOKEN = ""
                    if _nav_reset_callback:
                        _nav_reset_callback()

            return tokens

        return get_line_tokens

    def _split_by_separators(self, text: str) -> List[Tuple[str, bool]]:
        result = []
        last_end = 0
        for match in re.finditer(r'[;&|]|&&|\|\|', text):
            start, end = match.span()
            if start > last_end:
                result.append((text[last_end:start], False))
            result.append((text[start:end], True))
            last_end = end
        if last_end < len(text):
            result.append((text[last_end:], False))
        return result

    def _lex_command_segment(self, segment: str) -> List[Tuple[str, str]]:
        tokens = []
        posix = get_posix_mode()
        try:
            parts = shlex.split(segment, posix=posix)
        except ValueError:
            return self._lex_manual(segment)

        if not parts:
            return self._lex_manual(segment)

        current_pos = 0
        is_first = True
        for part in parts:
            while current_pos < len(segment) and segment[current_pos].isspace():
                tokens.append(('', segment[current_pos]))
                current_pos += 1

            part_len = len(part)
            part_text = segment[current_pos:current_pos+part_len]

            if is_first:
                if part_text in self.valid_commands:
                    style = COLORS['command']
                elif self._looks_like_path(part_text):
                    # 路径形式的命令（如 ./a.sh），使用路径颜色而非错误红色
                    expanded = PathResolver.expand_path(part_text, self.virtual_root)
                    if self.virtual_root and part_text.startswith('/'):
                        expanded = PathResolver.normalize(part_text, self.virtual_root)
                    if _PATH_EXISTENCE_CACHE.exists(expanded):
                        style = COLORS['path']
                    else:
                        style = COLORS['path_invalid']
                else:
                    style = COLORS['command_invalid']
                is_first = False
            elif part.startswith('-'):
                style = COLORS['option']
            elif self._looks_like_path(part):
                expanded = PathResolver.expand_path(part, self.virtual_root)
                if self.virtual_root and part.startswith('/'):
                    expanded = PathResolver.normalize(part, self.virtual_root)
                if _PATH_EXISTENCE_CACHE.exists(expanded):
                    style = COLORS['path']
                else:
                    style = COLORS['path_invalid']
            else:
                if re.search(r'\$[\w{}]+|`[^`]*`|\$\([^)]*\)', part):
                    style = COLORS['variable']
                else:
                    style = COLORS['string']

            sub_tokens = self._highlight_symbols_in_word(part_text, style)
            tokens.extend(sub_tokens)
            current_pos += part_len

        if current_pos < len(segment):
            tokens.append(('', segment[current_pos:]))

        return tokens

    def _lex_manual(self, segment: str) -> List[Tuple[str, str]]:
        tokens = []
        i = 0
        length = len(segment)
        is_first = True
        while i < length:
            if segment[i].isspace():
                j = i
                while j < length and segment[j].isspace():
                    j += 1
                tokens.append(('', segment[i:j]))
                i = j
                continue

            j = i
            in_quote = False
            quote_char = ''
            while j < length:
                if not in_quote and segment[j].isspace():
                    break
                if segment[j] in ('"', "'") and (j == 0 or segment[j-1] != '\\'):
                    if not in_quote:
                        in_quote = True
                        quote_char = segment[j]
                    elif segment[j] == quote_char:
                        in_quote = False
                        quote_char = ''
                j += 1

            word = segment[i:j]

            if is_first:
                if word in self.valid_commands:
                    style = COLORS['command']
                elif self._looks_like_path(word):
                    # 路径形式的命令（如 ./a.sh），使用路径颜色而非错误红色
                    expanded = PathResolver.expand_path(word, self.virtual_root)
                    if self.virtual_root and word.startswith('/'):
                        expanded = PathResolver.normalize(word, self.virtual_root)
                    if _PATH_EXISTENCE_CACHE.exists(expanded):
                        style = COLORS['path']
                    else:
                        style = COLORS['path_invalid']
                else:
                    style = COLORS['command_invalid']
                is_first = False
            elif word.startswith('-'):
                style = COLORS['option']
            elif self._looks_like_path(word):
                expanded = PathResolver.expand_path(word, self.virtual_root)
                if self.virtual_root and word.startswith('/'):
                    expanded = PathResolver.normalize(word, self.virtual_root)
                if _PATH_EXISTENCE_CACHE.exists(expanded):
                    style = COLORS['path']
                else:
                    style = COLORS['path_invalid']
            else:
                if re.search(r'\$[\w{}]+|`[^`]*`|\$\([^)]*\)', word):
                    style = COLORS['variable']
                else:
                    style = COLORS['string']

            sub_tokens = self._highlight_symbols_in_word(word, style)
            tokens.extend(sub_tokens)
            i = j

        return tokens

    def _looks_like_path(self, text: str) -> bool:
        return any(c in text for c in '/\\') or text.startswith('~') or text in ('.', '..')

    def _highlight_symbols_in_word(self, word: str, base_style: str) -> List[Tuple[str, str]]:
        tokens = []
        i = 0
        length = len(word)
        while i < length:
            if word[i] in '<>"\'`()[]{}':
                tokens.append((COLORS['separator'], word[i]))
                i += 1
            else:
                j = i
                while j < length and word[j] not in '<>"\'`()[]{}':
                    j += 1
                if j > i:
                    tokens.append((base_style, word[i:j]))
                i = j
        return tokens

# ===================== 智能虚影补全 =====================
class SmartAutoSuggest(AutoSuggest):
    """
    智能虚影补全器
    根据当前输入和历史时间，实时显示最近使用的完整命令
    修复：保留末尾空格，匹配包含空格的历史命令
    """
    def __init__(self, completer: Optional['SmartCompleter'] = None):
        self.completer = completer

    def get_suggestion(self, buffer, document):
        if not self.completer:
            return None
        
        text = document.text_before_cursor
        if not text or text.isspace():
            return None
        
        # 优先：基于历史频率的虚影
        suggestion_text = self.completer.get_smart_suggestion(text)
        if suggestion_text:
            return Suggestion(suggestion_text)
        
        # 回退：无历史虚影时，用补全菜单当前高亮项作为虚影
        # 用户按 Tab/Shift+Tab 导航菜单时，虚影实时跟随当前选中项
        cs = buffer.complete_state
        if cs and cs.current_completion:
            comp = cs.current_completion
            if comp.start_position == 0:
                # 追加模式（如路径补全 /etc/ → hostname）
                return Suggestion(comp.text)
            else:
                # 替换模式（如命令补全 manag → e）
                current_word = text[comp.start_position:] if comp.start_position < 0 else ""
                if comp.text.startswith(current_word):
                    return Suggestion(comp.text[len(current_word):])
                return Suggestion(comp.text)
        
        return None


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


# ===================== 智能补全器 =====================
class SmartCompleter(Completer):
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
        self.history_buffer = history_buffer or []
        history_file = os.path.join(user_home_dir, ".onyx_history.txt") if user_home_dir else None
        self.freq_manager = get_command_freq(user_home_dir, history_file)

        self.cmd_cache = get_command_cache(user_home_dir, cmd_config_path, com_cmd_config_path) if user_home_dir and cmd_config_path else None
        self._load_cmd_config()
        self._update_cmd_list_order()
        
        self.permission_commands = {'sudo', 'sado'}
        # 为代码语法补全预留引用，运行时注入 MultiLineCompleter 实例
        self._multiline_completer = None

    def set_multiline_completer(self, ml_completer: Any):
        """注入多行补全器，用于代码上下文的补全"""
        self._multiline_completer = ml_completer

    def _load_cmd_config(self):
        if self.cmd_cache:
            self.subcommand_map = self.cmd_cache.get_subcommands_map()
            self.option_map = self.cmd_cache.get_options_map()
            self.argument_map = self.cmd_cache.get_arguments_map()
            cached_commands = self.cmd_cache.get_commands()
            self.original_cmd_list = list(set(self.original_cmd_list) | set(cached_commands))
        else:
            self.subcommand_map = {}
            self.option_map = {}
            self.argument_map = {}
            if self.cmd_config_path:
                config = CommandConfigLoader.load_config(self.cmd_config_path)
                for cmd, cfg in config.items():
                    subcmds = cfg.get("subcommands", [])
                    if isinstance(subcmds, list):
                        subcmd_names = [sc.get("name", sc) if isinstance(sc, dict) else sc for sc in subcmds]
                        self.subcommand_map[cmd] = subcmd_names
                        for sc in subcmds:
                            if isinstance(sc, dict):
                                sc_name = sc.get("name", "")
                                sc_options = sc.get("options", [])
                                sc_arguments = sc.get("arguments", [])
                                if sc_name:
                                    self.option_map[f"{cmd}:{sc_name}"] = sc_options
                                    self.argument_map[f"{cmd}:{sc_name}"] = sc_arguments
                    options = cfg.get("options", [])
                    arguments = cfg.get("arguments", [])
                    if options:
                        self.option_map[cmd] = options
                    if arguments:
                        self.argument_map[cmd] = arguments
            
            if self.com_cmd_config_path:
                com_config = CommandConfigLoader.load_config(self.com_cmd_config_path)
                for cmd, cfg in com_config.items():
                    if cmd not in self.original_cmd_list:
                        self.original_cmd_list.append(cmd)
                    
                    subcmds = cfg.get("subcommands", [])
                    if isinstance(subcmds, list):
                        subcmd_names = [sc.get("name", sc) if isinstance(sc, dict) else sc for sc in subcmds]
                        existing_subcmds = self.subcommand_map.get(cmd, [])
                        self.subcommand_map[cmd] = list(set(existing_subcmds + subcmd_names))
                        for sc in subcmds:
                            if isinstance(sc, dict):
                                sc_name = sc.get("name", "")
                                sc_options = sc.get("options", [])
                                sc_arguments = sc.get("arguments", [])
                                if sc_name:
                                    key = f"{cmd}:{sc_name}"
                                    existing_opts = self.option_map.get(key, [])
                                    self.option_map[key] = list(set(existing_opts + sc_options))
                                    existing_args = self.argument_map.get(key, [])
                                    self.argument_map[key] = list(set(existing_args + sc_arguments))
                    
                    options = cfg.get("options", [])
                    if options:
                        existing_opts = self.option_map.get(cmd, [])
                        self.option_map[cmd] = list(set(existing_opts + options))
                    
                    arguments = cfg.get("arguments", [])
                    if arguments:
                        existing_args = self.argument_map.get(cmd, [])
                        self.argument_map[cmd] = list(set(existing_args + arguments))

    def _update_cmd_list_order(self):
        if self.original_cmd_list:
            self.cmd_list = self.freq_manager.get_sorted_commands(self.original_cmd_list)

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

    def _get_context_for_permission_cmd(self, actual_cmd: str, remaining_parts: List[str], 
                                          current_word: str, start_pos: int) -> Tuple[str, str, int, str]:
        if current_word.startswith('-'):
            return "option", current_word, start_pos, actual_cmd
        
        if actual_cmd in self.subcommand_map:
            subcmds = self.subcommand_map.get(actual_cmd, [])
            if remaining_parts:
                subcmd = remaining_parts[0] if remaining_parts else ""
                if not subcmd and current_word:
                    for sc in subcmds:
                        if sc.lower().startswith(current_word.lower()):
                            return "subcommand", current_word, start_pos, actual_cmd
            
            if any(sc.lower().startswith(current_word.lower()) for sc in subcmds):
                return "subcommand", current_word, start_pos, actual_cmd
        
        path_indicators = ['./', '/', '~/', '../', '.', '..']
        if any(current_word.startswith(ind) for ind in path_indicators) or os.sep in current_word:
            return "path", current_word, start_pos, actual_cmd
        
        return "permission_cmd", current_word, start_pos, actual_cmd

    def _get_context(self, document: Document) -> Tuple[str, str, int, str]:
        """
        获取当前输入的上下文类型。
        如果检测到多行代码上下文，返回 "code" 类型，并将当前词和文档传递给多行补全器。
        """
        text_before = document.text_before_cursor
        cursor_pos = document.cursor_position
        segment_text, segment_start = self._get_last_command_segment(document)

        if not segment_text:
            return "empty", "", 0, ""

        word_start_in_segment = len(segment_text)
        for i in range(len(segment_text) - 1, -1, -1):
            if segment_text[i].isspace():
                break
            word_start_in_segment = i

        if word_start_in_segment == len(segment_text):
            current_word = ""
        else:
            current_word = segment_text[word_start_in_segment:]

        start_pos = -len(current_word)

        text_before_word_in_segment = segment_text[:word_start_in_segment].strip()
        if not text_before_word_in_segment:
            # 检测路径形式的首个词：./a.sh, /usr/bin/foo, ~/script, ../tool 等
            if (current_word.startswith(('./', '/', '~/', '../')) or
                    (os.sep in current_word and not current_word.startswith('-'))):
                # 路径以分隔符结尾时追加模式（start_pos=0），否则替换最后一个路径组件
                if current_word.endswith(os.sep) or current_word.endswith('/'):
                    return "path", current_word, 0, ""
                cleaned = current_word.rstrip(os.sep)
                last_part = os.path.basename(cleaned) if cleaned else ""
                path_start = -len(last_part) if last_part else 0
                return "path", current_word, path_start, ""
            return "command", current_word, start_pos, ""

        parts = self._split_command(segment_text)
        if not parts:
            return "other", current_word, start_pos, ""

        cmd = parts[0]
        
        # 检测是否为已知的多行语法启动命令（如 python, node, bash 等）
        CODE_SHELLS = {'python', 'python3', 'python2', 'py', 'ipython', 'node', 'ruby', 'perl', 'lua', 'bash', 'sh', 'zsh', 'fish'}
        if cmd in CODE_SHELLS:
            # 在此上下文中，后续内容可能是代码。我们可以尝试委托给多行补全器。
            if self._multiline_completer:
                # 传递整个文档和当前词给多行补全器
                return "code", current_word, start_pos, cmd
        
        if cmd in self.permission_commands:
            if len(parts) >= 2:
                actual_cmd = parts[1]
                if len(parts) >= 3:
                    remaining_parts = parts[2:]
                    return self._get_context_for_permission_cmd(
                        actual_cmd, remaining_parts, current_word, start_pos
                    )
                else:
                    return "permission_cmd", current_word, start_pos, actual_cmd
            else:
                return "permission_cmd", current_word, start_pos, ""

        if current_word.startswith('-'):
            return "option", current_word, start_pos, cmd

        path_indicators = ['./', '/', '~/', '../', '.', '..']
        PATH_COMMANDS = {
            'cd', 'ls', 'cat', 'cp', 'mv', 'rm', 'mkdir', 'rmdir',
            'touch', 'chmod', 'chown', 'find', 'grep', 'file', 'stat',
            'python', 'python3', 'source', 'run', 'bash', 'sh', './',
            'nano', 'vim', 'vi', 'emacs', 'less', 'more', 'head', 'tail',
            'dir', 'copy', 'del', 'erase', 'ren', 'rename', 'md', 'rd',
            'type', 'more', 'xcopy', 'robocopy',
        }

        def _has_path_indicators(word: str) -> bool:
            return any(word.startswith(ind) for ind in path_indicators) or os.sep in word

        def _get_path_result(word: str) -> Tuple[str, str, int, str]:
            if not word:
                return "path", "", 0, cmd
            # 用户已输入完整目录路径+分隔符（如 /etc/），补全项应追加而非替换目录名
            if word.endswith(os.sep) or word.endswith('/'):
                return "path", word, 0, cmd
            cleaned = word.rstrip(os.sep)
            last_part = os.path.basename(cleaned) if cleaned else ""
            corrected_start_pos = -len(last_part) if last_part else 0
            return "path", word, corrected_start_pos, cmd

        if not current_word:
            # 子命令优先级高于路径：先检查子命令，再检查路径
            if cmd in self.subcommand_map:
                if cmd in PATH_COMMANDS:
                    return "mixed", "", 0, cmd
                return "subcommand", "", 0, cmd
            if cmd in PATH_COMMANDS:
                return "path", "", 0, cmd
            return "other", current_word, start_pos, cmd

        if len(parts) == 2:
            if cmd in self.subcommand_map:
                subcmds = self.subcommand_map.get(cmd, [])
                matching_subcmds = [sc for sc in subcmds if sc.lower().startswith(current_word.lower())]
                
                if matching_subcmds:
                    has_matching_paths = False
                    if _has_path_indicators(current_word) or cmd in PATH_COMMANDS:
                        try:
                            dir_path, file_prefix, is_abs = PathResolver.split_for_completion(
                                current_word, self.virtual_root
                            )
                            if os.path.isdir(dir_path):
                                for item in os.listdir(dir_path):
                                    if item.lower().startswith(file_prefix.lower()):
                                        has_matching_paths = True
                                        break
                        except Exception:
                            pass
                    
                    if has_matching_paths:
                        return "mixed", current_word, start_pos, cmd
                    else:
                        return "subcommand", current_word, start_pos, cmd
                else:
                    if _has_path_indicators(current_word) or cmd in PATH_COMMANDS:
                        return _get_path_result(current_word)
                    return "other", current_word, start_pos, cmd
            else:
                if _has_path_indicators(current_word) or cmd in PATH_COMMANDS:
                    return _get_path_result(current_word)
                return "other", current_word, start_pos, cmd

        if len(parts) >= 3:
            subcmd = parts[1] if len(parts) > 1 else ""
            
            if cmd in self.subcommand_map and subcmd in self.subcommand_map.get(cmd, []):
                arg_key = f"{cmd}:{subcmd}"
                if arg_key in self.argument_map and self.argument_map[arg_key]:
                    return "argument", current_word, start_pos, cmd
                else:
                    if _has_path_indicators(current_word) or cmd in PATH_COMMANDS:
                        return _get_path_result(current_word)
                    return "other", current_word, start_pos, cmd
            
            if cmd in self.argument_map and self.argument_map[cmd]:
                return "argument", current_word, start_pos, cmd
            
            if _has_path_indicators(current_word) or cmd in PATH_COMMANDS:
                return _get_path_result(current_word)
            return "other", current_word, start_pos, cmd

        # 子命令优先级高于路径：先检查子命令，再 fallback 到路径
        if cmd in self.subcommand_map:
            subcmds = self.subcommand_map[cmd]
            if any(sc.lower().startswith(current_word.lower()) for sc in subcmds):
                if _has_path_indicators(current_word) or cmd in PATH_COMMANDS:
                    return "mixed", current_word, start_pos, cmd
                return "subcommand", current_word, start_pos, cmd
        
        if _has_path_indicators(current_word) or cmd in PATH_COMMANDS:
            return _get_path_result(current_word)

        return "other", current_word, start_pos, cmd

    def get_smart_suggestion(self, current_input: str) -> Optional[str]:
        if not current_input:
            return None
        
        prefix = current_input
        
        recent_full_command = self._get_most_recent_full_command(prefix)
        if recent_full_command and recent_full_command != prefix:
            remaining = recent_full_command[len(prefix):]
            return remaining
        
        if prefix and not prefix.endswith(' '):
            parts = prefix.split()
            if len(parts) == 1:
                cmd = parts[0]
                if cmd in self.subcommand_map:
                    subcmds = self.subcommand_map.get(cmd, [])
                    if subcmds:
                        recent_subcmd = self._get_most_recent_subcommand(cmd, subcmds)
                        if recent_subcmd:
                            return f" {recent_subcmd}"
        
        if not prefix.endswith(' '):
            recent_cmd = self._get_most_recent_command(prefix)
            if recent_cmd and recent_cmd != prefix:
                remaining = recent_cmd[len(prefix):]
                return remaining
        
        return None

    def _get_most_recent_full_command(self, prefix: str) -> Optional[str]:
        if not self.history_buffer:
            return None
        
        matching = [cmd for cmd in self.history_buffer if cmd.startswith(prefix)]
        if matching:
            return matching[0]
        return None

    def _get_most_recent_command(self, prefix: str) -> Optional[str]:
        if not self.history_buffer:
            return None
        
        for cmd in self.history_buffer:
            if cmd.startswith(prefix):
                cmd_name = cmd.split()[0] if cmd.split() else cmd
                return cmd_name
        return None

    def _get_most_recent_subcommand(self, cmd: str, subcmds: List[str]) -> Optional[str]:
        if not self.history_buffer:
            return subcmds[0] if subcmds else None
        
        full_cmd_prefix = f"{cmd} "
        for history_cmd in self.history_buffer:
            if history_cmd.startswith(full_cmd_prefix):
                parts = history_cmd.split()
                if len(parts) >= 2:
                    sub = parts[1]
                    if sub in subcmds:
                        return sub
        return subcmds[0] if subcmds else None

    def get_completions(self, document: Document, complete_event):
        self._update_cmd_list_order()
        ctx_type, current_word, start_pos, cmd = self._get_context(document)

        if ctx_type == "command":
            yield from self._complete_command(current_word, start_pos)
        elif ctx_type == "permission_cmd":
            yield from self._complete_command(current_word, start_pos, meta_type="permission")
        elif ctx_type == "subcommand":
            yield from self._complete_subcommand(current_word, start_pos, cmd)
        elif ctx_type == "mixed":
            yield from self._complete_subcommand(current_word, start_pos, cmd)
            yield from self._complete_path(current_word, start_pos)
        elif ctx_type == "path":
            yield from self._complete_path(current_word, start_pos)
        elif ctx_type == "option":
            yield from self._complete_option(current_word, start_pos, cmd, document)
        elif ctx_type == "argument":
            yield from self._complete_argument(current_word, start_pos, cmd, document)
        elif ctx_type == "code" and self._multiline_completer:
            # 使用多行补全器提供代码补全
            yield from self._multiline_completer.get_completions(document, complete_event)
        # "other" 或 "empty" 不提供补全

    def _complete_command(self, current_word: str, start_pos: int, meta_type: str = "command"):
        if meta_type == "permission":
            display_meta = "perm"
            style = "ansiyellow bold"
        else:
            display_meta = META_TEXTS_EN.get('command', 'cmd')
            style = META_COLORS.get('command', 'ansigreen bold')
        
        # 防守：确保 start_position 不会越界替换或意外追加
        # 当 start_pos == 0 且 current_word 非空时，应替换而非追加
        # 当 |start_pos| > len(current_word) 时，clamp 到当前词长度
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

        for cmd in self.cmd_list:
            if cmd.lower().startswith(current_word.lower()):
                yield Completion(
                    cmd,
                    start_position=safe_start,
                    display_meta=display_meta,
                    style=style
                )

    def _complete_subcommand(self, current_word: str, start_pos: int, cmd: str):
        subcmds = self.subcommand_map.get(cmd, [])
        for subcmd in subcmds:
            if not current_word or subcmd.lower().startswith(current_word.lower()):
                yield Completion(
                    subcmd,
                    start_position=start_pos,
                    display_meta=META_TEXTS_EN.get('subcommand', 'subcmd'),
                    style=META_COLORS.get('subcommand', 'ansiyellow')
                )

    def _complete_path(self, current_word: str, start_pos: int):
        completions = self.engine.get_completions(current_word, start_pos)
        for comp_text, display_meta, color, rel_start in completions:
            yield Completion(
                comp_text,
                start_position=rel_start,
                display_meta=display_meta,
                style=color.split()[0] if color else ""
            )

    def _complete_option(self, current_word: str, start_pos: int, cmd: str, document: Document):
        segment_text, _ = self._get_last_command_segment(document)
        parts = self._split_command(segment_text)
        subcmd = parts[1] if len(parts) > 1 else ""

        if subcmd:
            key = f"{cmd}:{subcmd}"
            options = self.option_map.get(key, [])
            for opt in options:
                if opt.startswith(current_word):
                    yield Completion(
                        opt,
                        start_position=start_pos,
                        display_meta=META_TEXTS_EN.get('option', 'option'),
                        style=META_COLORS.get('option', 'ansired')
                    )

        options = self.option_map.get(cmd, [])
        for opt in options:
            if opt.startswith(current_word):
                yield Completion(
                    opt,
                    start_position=start_pos,
                    display_meta=META_TEXTS_EN.get('option', 'option'),
                    style=META_COLORS.get('option', 'ansired')
                )

    def _complete_argument(self, current_word: str, start_pos: int, cmd: str, document: Document):
        segment_text, _ = self._get_last_command_segment(document)
        parts = self._split_command(segment_text)
        subcmd = parts[1] if len(parts) > 1 else ""

        arguments = []
        if subcmd:
            key = f"{cmd}:{subcmd}"
            arguments = self.argument_map.get(key, [])
        
        if not arguments:
            arguments = self.argument_map.get(cmd, [])
        
        for arg in arguments:
            if not current_word or arg.lower().startswith(current_word.lower()):
                yield Completion(
                    arg,
                    start_position=start_pos,
                    display_meta=META_TEXTS_EN.get('argument', 'arg'),
                    style=META_COLORS.get('argument', 'ansimagenta')
                )