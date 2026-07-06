"""AppContext — 统一应用状态容器，替代 Onyx.py 中所有模块级全局变量"""

import os
import re
import uuid
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any, Callable
from concurrent.futures import ThreadPoolExecutor
from getpass import getpass

from lib.terminal.colors import Fore, Style


# ============================================================
# 辅助数据类
# ============================================================

@dataclass
class ToolInfo:
    """工具信息"""
    path: str           # 工具绝对路径
    is_cli: bool = False
    tool_perm: int = 3  # 权限等级 1-5
    tool_type: str = "other"


@dataclass
class UserMode:
    """用户模式"""
    current_mode: str = "low"        # low / mid / adv
    current_tool_perm: int = 3
    language: str = "chinese"


# ============================================================
# AppContext — 统一状态
# ============================================================

@dataclass
class AppContext:
    """Onyx 运行时全局状态容器。

    所有原本散落在 Onyx.py 模块级的全局变量统一收纳于此。
    通过 get_ctx() 获取单例。
    """

    # ---- 路径 / 根目录 ----
    ROOT_DIR: str = field(default_factory=lambda: os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")))
    VIRTUAL_ROOT: str = ""          # init 时设为 ROOT_DIR
    CURRENT_VIRTUAL_PATH: str = ""
    USER_HOME_DIR: str = ""
    TOOL_MAIN_DIR: str = ""
    CACHE_DIR: str = ""

    # ---- 系统 ----
    sys_type: str = ""
    OS_OR_TBS: str = ""
    PYTHON_EXE: str = ""

    # ---- 用户 / 权限 ----
    user_info: Dict[str, Any] = field(default_factory=dict)
    user_mode: Optional[UserMode] = None
    global_config: Dict[str, Any] = field(default_factory=dict)
    SANDBOX_CONFIG: Dict[str, Any] = field(default_factory=dict)
    executable_config: Dict[str, Any] = field(default_factory=dict)

    # ---- 沙箱 ----
    _SANDBOX_ENABLED: bool = True
    _SANDBOX_CONFIG_PATH: str = ""

    # ---- 密码 / 安全常量 ----
    MIN_PASSWORD_LEN: int = 6
    SALT_LEN: int = 16
    FILE_PERMISSION: int = 0o600
    DIR_PERMISSION: int = 0o700
    ADMIN_PASSWORD_PATH: str = ""

    # ---- 配置文件路径 ----
    CONFIG_FILE_PATH: str = ""
    SADO_CONFIG_PATH: str = ""
    SADO_CONFIG: List[Dict[str, Any]] = field(default_factory=list)

    # ---- 输入 / 历史 ----
    HISTORY_BUFFER: List[str] = field(default_factory=list)
    CURRENT_HISTORY_INDEX: int = -1
    INPUT_BUFFER: str = ""
    COMMAND_HISTORY: List[Tuple[str, float, str, str, str]] = field(default_factory=list)
    USER_CONFIG_PATH: str = ""
    USER_HISTORY_PATH: str = ""
    last_history_save_time: float = 0.0
    history_save_interval: int = 30
    MAX_HISTORY_LEN: int = 0

    # ---- 别名 ----
    ALIAS_CACHE: Dict[str, Any] = field(default_factory=dict)

    # ---- 内置命令 ----
    BUILTIN_COMMANDS: Dict[str, Callable] = field(default_factory=dict)
    AUTO_CMD_PATH: Optional[str] = None
    AUTO_CMDS: List = field(default_factory=list)

    # ---- 工具 ----
    TOOL_INDEX_CACHE: Dict[str, ToolInfo] = field(default_factory=dict)
    SUPPORTED_MAIN_FILES: List[str] = field(default_factory=lambda: [
        "Main.py", "Main.pyc", "main.py", "main.pyc",
        "tool.py", "tool.pyc", "entry.py", "entry.pyc"])
    MAIN_FILE_KEYWORDS: List[str] = field(default_factory=lambda: [
        "main", "主", "entry", "入口", "start", "启动", "launch"])
    SUPPORTED_EXEC_SUFFIXES: Dict[str, List[str]] = field(default_factory=dict)

    # ---- 进程 ----
    PROCESS_LOCK: Any = None
    process_lock: Any = field(default_factory=threading.Lock)
    executor: Optional[ThreadPoolExecutor] = None
    CURRENT_PROCESSES: List[Tuple[int, float, str, str]] = field(default_factory=list)

    # ---- AI 缓存 ----
    AI_TOOL_OUTPUT_CACHE: Dict[str, str] = field(default_factory=dict)

    # ---- 日志 ----
    LOG_LEVELS: Dict[str, int] = field(default_factory=lambda:
        {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4})
    CURRENT_LOG_LEVEL: int = 1  # INFO
    LOG_DIR: str = ""
    LOG_TIMESTAMP: str = ""
    LOG_FILE_PATH: str = ""
    log_file_handler: Any = None

    # ---- 缓存 ----
    TOOL_INDEX_MSG_PATH: str = ""
    PATH_INDEX_MSG_PATH: str = ""
    CMD_MAPPING_MSG_PATH: str = ""
    DIR_CACHE_MSG_PATH: str = ""
    PATH_RESOLVE_CACHE: Dict[str, Tuple[str, float]] = field(default_factory=dict)
    PATH_CACHE_TTL: int = 1800
    PATH_SCAN_DEPTH: int = 10
    CMD_MAPPING_CACHE: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    CMD_CACHE_TTL: int = 3600
    DIR_FILE_CACHE: Dict[str, Tuple[List[Dict[str, Any]], float, int]] = field(default_factory=dict)
    DIR_CACHE_TTL: int = 1800
    DIR_CACHE_MAX_FILES: int = 100
    current_sys_cmds: Dict[str, List[str]] = field(default_factory=dict)

    # ---- 调试 ----
    DEBUG_TIMES_PATH: str = ""
    DEBUG_PARSECMD_PATH: str = ""

    # ---- 启动 ----
    BOOT_TIME_DIR: str = ""
    BOOT_USAGE_FILE: str = ""
    SYSTEM_BOOT_TIMESTAMP: int = 0
    CURRENT_BOOT_USAGE: str = ""
    _ONESHOT_MODE: bool = False

    # ---- prompt 模板 ----
    _TEMPLATES: Dict[str, str] = field(default_factory=dict)
    _PROMPT_CONF_PATH: str = ""
    _CACHED_PROMPT_CONF: Optional[str] = None

    # ---- prompt 生成缓存 ----
    _CACHED_HOSTNAME: Optional[str] = None
    _COLOR_STYLES: Dict[str, str] = field(default_factory=lambda: {
        "BLUE": "#4a90e2",
        "RED": "#e74c3c",
        "GREEN": "#2ecc71",
        "YELLOW": "#f1c40f",
        "RESET": "",
        "ACCENT_GREEN": "#2ecc71",
        "ACCENT_RED": "#e74c3c",
    })
    _COLOR_MAP: Dict[str, str] = field(default_factory=dict)

    # ---- Git 状态缓存 ----
    _GIT_CACHE: Optional[str] = None
    _GIT_CACHE_TIME: float = 0.0

    # ---- 编译窗口缓存 ----
    _comp_window_cache: Optional[bool] = None
    _comp_window_cache_time: float = 0.0
    _comp_window_cache_ttl: float = 30.0

    # ---- 交互式命令 ----
    _INTERACTIVE_DEFAULT: frozenset = field(default_factory=lambda: frozenset([
        "vim", "vi", "nano", "top", "htop", "less", "more", "watch",
        "ssh", "telnet", "python", "python3", "irb", "node",
    ]))
    _INTERACTIVE_FULL: frozenset = field(default_factory=frozenset)

    # ---- 正则编译缓存 ----
    _RE_MULTI_SPACE: Any = None
    _RE_OPT_SLASH: Any = None
    _RE_OPT_STAR: Any = None

    # ---- 退出码 ----
    _LAST_EXIT_CODE: int = 0

    # ---- readline ----
    HAS_READLINE: bool = False

    # ---- Fore/Style 持有（避免全项目 import Fore） ----
    Fore: Any = Fore
    Style: Any = Style

    def __post_init__(self):
        if not self.VIRTUAL_ROOT:
            self.VIRTUAL_ROOT = self.ROOT_DIR
        if not self.CONFIG_FILE_PATH:
            self.CONFIG_FILE_PATH = os.path.join(self.ROOT_DIR, "onyx", "etc", "config.json")
        if not self.BOOT_TIME_DIR:
            self.BOOT_TIME_DIR = os.path.join(self.ROOT_DIR, "onyxlog", "starttime")
        if not self._RE_MULTI_SPACE:
            self._RE_MULTI_SPACE = re.compile(r'\s+')
        if not self._RE_OPT_SLASH:
            self._RE_OPT_SLASH = re.compile(r'(\s*-\w+)\s*(\/)')
        if not self._RE_OPT_STAR:
            self._RE_OPT_STAR = re.compile(r'(\s*-\w+)\s*(\*)')
        if not self._INTERACTIVE_FULL:
            self._INTERACTIVE_FULL = self._INTERACTIVE_DEFAULT
        if not self._COLOR_MAP:
            self._COLOR_MAP = {
                "{BLUE}": self.Fore.BLUE,
                "{RED}": self.Fore.RED,
                "{GREEN}": self.Fore.GREEN,
                "{YELLOW}": self.Fore.YELLOW,
                "{RESET}": self.Style.RESET_ALL,
            }
        # 初始化 user_info
        if not self.user_info:
            self.user_info = {
                "name": self._detect_username(),
                "is_admin": False,
                "permission_flag": "$",
                "session_id": str(uuid.uuid4()),
            }

    @staticmethod
    def _detect_username() -> str:
        try:
            return getpass.getuser()
        except Exception:
            for var in ["USER", "USERNAME", "LOGNAME"]:
                u = os.getenv(var)
                if u and u.strip():
                    return u.strip()
            return "default"


# ============================================================
# 单例工厂
# ============================================================

_ctx: Optional[AppContext] = None


def init_ctx(**overrides) -> AppContext:
    """初始化全局 AppContext 单例（程序启动时调用一次）"""
    global _ctx
    _ctx = AppContext(**overrides)
    return _ctx


def get_ctx() -> AppContext:
    """获取全局 AppContext 单例"""
    global _ctx
    if _ctx is None:
        _ctx = AppContext()
    return _ctx
