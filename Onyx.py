#作者:脉动大羊 脉动小羊 皓宇 
#测试:脉动小龙 脉动小狼
#时间:2024.11.30诞生，2025.8.23重构
#名字:2024.11.30时叫hard 后命名为Hacker 重构后更名为Hacker-Onyx
#现在:Onyx-Native


"""
南无喝罗怛那哆罗夜耶
南无阿唎耶
婆卢羯帝烁钵罗耶
菩提萨埵婆耶
摩诃萨埵婆耶
摩诃迦卢尼迦耶唵
萨皤罗罚曳数怛那写
南无悉吉栗埵伊蒙阿唎耶
婆卢吉帝室佛罗楞驮婆
南无那罗谨墀
醯唎摩诃皤哆沙咩
萨婆阿他豆输朋阿逝孕
萨婆萨哆那摩婆萨哆那摩婆伽
摩罚特豆怛侄他
唵阿婆卢醯卢迦帝
迦罗帝夷醯唎
摩诃菩提萨埵萨婆萨婆
摩罗摩罗摩醯摩醯唎驮孕
俱卢俱卢羯蒙度卢度卢罚闍耶
摩诃罚闍耶帝

"""
import gc
import os
import sys
import json
import ctypes
import time
import shutil
import re
import threading
import shlex
import uuid
import socket
import subprocess
import platform  
from getpass import getpass
import secrets
import hashlib

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from lib.terminal.colors import Fore, Style
from typing import Dict, List, Tuple, Optional, Any, Callable, Union



from pathlib import Path

from argon2.exceptions import VerifyMismatchError



from bin.activite_cmd import handle_activite_core
from man import start_background_scan

from prompt_toolkit.formatted_text import FormattedText

# === 热路径模块级导入（从函数内提升，避免每条命令重复 import 查找）===
from lib.build_tool_index import refresh_tool_index
from lib.scan_path_cmds import refresh_system_cmds
from lib.parse_and_execute import parse_and_execute as _parse_and_execute
from lib.terminal.exe import run_cmd_sync as _run_cmd_sync
from lib.parse import resolve_paths_in_multiline_text

# 真正的argon2id加密（底层实现，无版本兼容问题）
def argon2id_hash(password: str, salt: str) -> str:
    # 密码和盐值转换为bytes
    password_bytes = password.encode("utf-8")
    salt_bytes = bytes.fromhex(salt)  # 盐值从hex字符串转换
    from argon2.low_level import hash_secret
    from argon2.low_level import Type
    # 直接调用底层hash_secret函数，指定Type.ID（枚举类型，无字符串冲突）
    hashed = hash_secret(
        secret=password_bytes,
        salt=salt_bytes,
        time_cost=3,  # 时间成本（迭代次数）
        memory_cost=65536,  # 内存成本（64MB）
        parallelism=4,  # 并行度（4线程）
        hash_len=32,  # 哈希长度（32字节）
        type=Type.ID,  # 明确指定argon2id算法（枚举类型，无字符串错误）
        version=19  # 固定版本（兼容所有版本）
    )
    
    # 返回标准argon2id哈希字符串（格式：argon2id$v=19$...）
    return hashed.decode("utf-8")

# 真正的argon2id验证（底层实现，无版本兼容问题）
def argon2id_verify(password: str, stored_hash: str) -> bool:
    password_bytes = password.encode("utf-8")
    stored_hash_bytes = stored_hash.encode("utf-8")
    
    try:
        from argon2.low_level import verify_secret
        from argon2.low_level import Type
        # 直接调用底层verify_secret函数，指定Type.ID
        verify_secret(
            hash=stored_hash_bytes,
            secret=password_bytes,
            type=Type.ID  # 枚举类型，无字符串冲突
        )
        return True
    except VerifyMismatchError:
        # 密码不匹配
        return False
    except Exception:
        # 哈希格式无效、版本不兼容等其他异常
        return False



executable_config = ""


# Linux需保留的依赖（Windows会自动跳过）c这地方改了25遍
try:
    import fcntl
    import struct
except ImportError:
    pass  


def get_local_ip() -> str:
    try:
        # -------------------------- Linux 逻辑(感谢抖音！感谢豆包！) --------------------------
        if sys.platform.startswith("linux"):
            
            interfaces = ["wlan0", "eth0", "rmnet_data0", "lo"]
            for ifname in interfaces:
                try:
                    # 创建UDP socket（仅用于获取接口信息，不实际连接）
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    # 通过ioctl获取接口IP（避免依赖外网）
                    ip_addr = socket.inet_ntoa(
                        fcntl.ioctl(
                            sock.fileno(),
                            0x8915,  # SIOCGIFADDR：获取接口IP地址的系统调用
                            struct.pack('256s', ifname[:15].encode('utf-8'))
                        )[20:24]  # 从ioctl返回结果中提取IP地址字段
                    )
                    # 排除本地回环地址，返回有效IP
                    if ip_addr != "127.0.0.1":
                        return ip_addr
                except OSError:
                    continue  # 接口不存在时跳过，尝试下一个
            
        # -------------------------- Windows 逻辑 (等以后做成os指定给你删了)--------------------------
        elif sys.platform.startswith("win32"):
            # 连接公共DNS（8.8.8.8），通过socket获取本地出口IP（不实际发送数据）
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # 超时时间1秒，避免网络异常时阻塞
            sock.settimeout(1)
            try:
                sock.connect(("8.8.8.8", 80))  # 仅建立连接，不发送数据
                return sock.getsockname()[0]  # 获取本地连接的IP地址
            finally:
                sock.close()  # 确保socket关闭
        
        # 所有逻辑均未获取到有效IP，返回回环地址
        return "127.0.0.1"
    
    # 捕获所有异常（如网络未连接、权限不足），返回回环地址
    except Exception:
        return "127.0.0.1"
    

def format_time_milliseconds(milliseconds: int) -> str:
    """毫秒数转易读格式（精确到毫秒，如：1h20m5s123ms、30m20s456ms、5s789ms）"""
    hours = milliseconds // 3600000
    remaining = milliseconds % 3600000
    minutes = remaining // 60000
    remaining %= 60000
    seconds = remaining // 1000
    ms = remaining % 1000
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or (hours > 0 and (seconds > 0 or ms > 0)):
        parts.append(f"{minutes}m")
    if seconds > 0 or (minutes > 0 and ms > 0):
        parts.append(f"{seconds}s")
    parts.append(f"{ms}ms")  # 强制显示毫秒
    return "".join(parts)

def init_boot_usage_time(request_id: str) -> None:
    """初始化加载耗时（秒表功能）：从Main.py启动到Onyx加载成功的时间"""
    global SYSTEM_BOOT_TIMESTAMP, CURRENT_BOOT_USAGE, BOOT_USAGE_FILE
    # 1. 获取Main.py传递的启动时间（从环境变量）
    main_start_str = os.environ.get("MAIN_START_TIME")
    if not main_start_str:
        main_start_time = time.time() - 1  # 兜底：默认1秒前启动
        log_warning("未获取到Main启动时间，使用兜底值", request_id)
    else:
        main_start_time = float(main_start_str)
    
    # 2. 获取Onyx加载成功时间（当前时间）
    onyx_load_time = time.time()
    
    # 3. 计算加载耗时（毫秒级，避免浮点误差）
    load_duration_ms = int((onyx_load_time - main_start_time) * 1000)
    # 兜底：避免异常0ms
    if load_duration_ms < 10:
        load_duration_ms = 10
        log_warning("加载耗时过短，兜底设为10ms", request_id)
    CURRENT_BOOT_USAGE = format_time_milliseconds(load_duration_ms)  # 调用提前定义的格式化函数
    
    # 4. 创建存储目录与文件（保留原路径逻辑，内容改为加载耗时）
    if not os.path.exists(BOOT_TIME_DIR):
        os.makedirs(BOOT_TIME_DIR, mode=0o755 if sys_type != "Windows" else 0o777)
        log_info(f"创建加载时间存储目录：{BOOT_TIME_DIR}", request_id)
    
    # 生成含毫秒的20位时间戳文件名（格式：YYYYMMDDHHMMSSfff，如20251029214000123）
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]  # 前17位含毫秒（%f是微秒，取前3位为毫秒）
    BOOT_USAGE_FILE = os.path.join(BOOT_TIME_DIR, timestamp)
    
    # 写入文件（内容改为加载耗时相关信息）
    with open(BOOT_USAGE_FILE, "w", encoding="utf-8") as f:
        # Main.py启动时间（毫秒级）
        main_dt = datetime.fromtimestamp(main_start_time)
        f.write(f"Main.py Start Time: {main_dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\n")
        # Onyx加载成功时间（毫秒级）
        onyx_dt = datetime.fromtimestamp(onyx_load_time)
        f.write(f"Onyx Load Success Time: {onyx_dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\n")
        # 加载耗时（秒表结果，精确到毫秒）
        f.write(f"Load Duration (Main→Onyx): {CURRENT_BOOT_USAGE}")
    
    log_info(f"Onyx加载完成！加载耗时：{CURRENT_BOOT_USAGE}（存储文件：{BOOT_USAGE_FILE}）", request_id)





from core.context import ToolInfo, UserMode  # 从 core/ 导入，消除重复定义





# 新增：上下键历史切换相关全局变量
HISTORY_BUFFER: List[str] = []  # 存储命令历史缓冲区
CURRENT_HISTORY_INDEX: int = -1  # 当前选中的历史索引（-1表示未选中）
INPUT_BUFFER: str = ""  # 临时存储当前输入内容

# -------------------------- 沙箱启用状态管理（新增） --------------------------
_SANDBOX_ENABLED = True          # 默认启用，实际值由 init_sandbox_config 确定
_SANDBOX_CONFIG_PATH = ""        # 配置文件路径，在 init_sandbox_config 中赋值
# 新增：AI工具命令输出缓存（key: request_id, value: 工具输出内容）
AI_TOOL_OUTPUT_CACHE: Dict[str, str] = {}

# 全局核心变量
# 1. 配置与系统变量
global_config: Dict[str, Any] = {}       # 外部配置数据
sys_type: str = ""                       # 系统类型（Windows/Termux/Linux/macOS/SpecialLinux）(我真是个天才，跨系统简直nb)
OS_OR_TBS: str = ""
ROOT_DIR: str = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

BOOT_TIME_DIR = os.path.join(ROOT_DIR, "onyxlog", "starttime")
BOOT_USAGE_FILE = ""  # 含时间戳的存储文件
SYSTEM_BOOT_TIMESTAMP = 0  # 系统开机时间戳（秒）
CURRENT_BOOT_USAGE = ""  

def check_OS_TBS() -> None:
    # 关键修复：声明使用全局变量OS_OR_TBS，避免创建局部变量
    global OS_OR_TBS
    if sys_type.startswith("win32"):
        real_root = os.path.splitdrive(os.path.abspath("."))[0] + "\\"
    else:
        real_root = "/"
    
    # 比较虚拟根目录与真实根目录是否重合（标准化路径后对比）
    normalized_virtual_root = os.path.normpath(VIRTUAL_ROOT)
    normalized_real_root = os.path.normpath(real_root)
    
    if normalized_virtual_root == normalized_real_root:
        OS_OR_TBS = "OS"  # 修改全局变量为OS

        log_info(f"路径模式切换为OS：虚拟根目录与真实根目录重合（{normalized_real_root}）", user_info["session_id"])
    else:
        OS_OR_TBS = "TBS"  # 修改全局变量为TBS
        log_info(f"路径模式保持TBS：虚拟根目录（{normalized_virtual_root}）≠ 真实根目录（{normalized_real_root}）", user_info["session_id"])
        
CONFIG_FILE_PATH: str = os.path.join(ROOT_DIR,"onyx","etc", "config.json")  # 配置文件路径
TOOL_MAIN_DIR: str = ""                  # 工具主目录（config定义）
current_sys_cmds: Dict[str, List[str]] = {}  # 系统基础命令列表
MAX_HISTORY_LEN: int = 0                 # 命令历史最大长度
_TEMPLATES: Dict[str, str] = {}    # 命令提示符模板(更好的选择~)

SANDBOX_CONFIG: Dict[str, Any] = {}      # 沙箱安全配置(安全)
SUPPORTED_EXEC_SUFFIXES: Dict[str, List[str]] = {}  # 系统可执行后缀

ADMIN_PASSWORD_PATH: str = os.path.join(ROOT_DIR, "etc", "pki", ".onyx_admin_pass")  # 固定路径，
MIN_PASSWORD_LEN: int = 6  # 硬编码最小密码长度
SALT_LEN: int = 16  # 硬编码盐值长度
FILE_PERMISSION: int = 0o600  # 硬编码密码文件权限
DIR_PERMISSION: int = 0o700  # 硬编码root目录权限

VIRTUAL_ROOT: str = ROOT_DIR  # 虚拟根目录(一样后悔谢谢)
CURRENT_VIRTUAL_PATH: str = ""  # 当前虚拟路径（相对于VIRTUAL_ROOT）
USER_HOME_DIR: str = ""  # 用户主目录路径

# 2. 工具缓存
TOOL_INDEX_CACHE: Dict[str, ToolInfo] = {}  # 工具索引缓存(天才设计)
SUPPORTED_MAIN_FILES: List[str] = ["Main.py", "Main.pyc", "main.py", "main.pyc", "tool.py", "tool.pyc", "entry.py", "entry.pyc"]  # 工具入口文件
MAIN_FILE_KEYWORDS: List[str] = ["main", "主", "entry", "入口", "start", "启动", "launch"]  # 入口文件关键词(咖啡不断加加加到厌倦~)


PROCESS_LOCK = None
# 3. 线程与进程管理(吃灰吧，不想维护)
executor: Optional[ThreadPoolExecutor] = None  # 线程池
process_lock = threading.Lock()                # 进程列表锁
CURRENT_PROCESSES: List[Tuple[int, float, str, str]] = []  # 运行中进程（PID+时间+请求ID+命令）

 
 

 
 
log_file_handler: Optional[open] = None      # 日志文件句柄

# 5. 别名与命令历史
ALIAS_CACHE: Dict[str, Any] = {}            # 命令别名缓存
COMMAND_HISTORY: List[Tuple[str, float, str, str, str]] = []  # 命令历史（命令+时间+会话ID+请求ID+目录）
USER_CONFIG_PATH: str = ""                  # 用户配置路径
USER_HISTORY_PATH: str = ""                 # 命令历史路径
last_history_save_time: float = 0.0         # 上次历史保存时间
history_save_interval: int = 30             # 历史保存间隔（秒）

user_info: Dict[str, Any] = {               # 用户基础信息
    "name": "",
    "is_admin": False,
    "permission_flag": "$",
    "session_id": str(uuid.uuid4())
}

# 直接获取系统真实用户名（跨平台兼容）
try:
    # 优先通过 getpass 获取（兼容多系统）
    user_info["name"] = getpass.getuser()
except:
    # 备选1：通过环境变量获取（Windows/Linux通用）
    env_vars = ["USER", "USERNAME", "LOGNAME"]
    for var in env_vars:
        username = os.getenv(var)
        if username and username.strip():
            user_info["name"] = username.strip()
            break
    # 备选2：兜底默认值（极端情况）
    if not user_info["name"]:
        user_info["name"] = "default"

user_mode: Optional[UserMode] = None        # 用户模式实例
HAS_READLINE: bool = False                  # 是否支持命令补全
PYTHON_EXE: str = ""                        # Python可执行路径

AUTO_CMD_PATH = None #os.path.join(os.path.expanduser("~"), ".onyx_autocmd.json")  # 自启命令存储文件路径
AUTO_CMDS = []        # 自启命令列表（初始化空列表，避免未定义错误）


# 4. 日志与用户配置
LOG_LEVELS: Dict[str, int] = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}  # 日志级别
CURRENT_LOG_LEVEL: int = LOG_LEVELS["INFO"]  # 当前日志级别
# 生成日志目录（ROOT_DIR/onyx/log/onyx_log）
#LOG_DIR = os.path.join(ROOT_DIR, "onyxlog", "onyx")
# 日志文件名：.onyx_main_时间戳.log（时间戳格式：YYYYMMDDHHMMSS）
#LOG_TIMESTAMP = datetime.now().strftime("%Y%m%d%H%M%S")
#LOG_FILE_PATH: str = os.path.join(LOG_DIR, f".onyx_main_{LOG_TIMESTAMP}.log")

# ==============================缓存模块============================================
# 缓存路径后续在 init_user_home 后定义，此处留空占位
CACHE_DIR = ""
TOOL_INDEX_MSG_PATH = ""
PATH_INDEX_MSG_PATH = ""
CMD_MAPPING_MSG_PATH = ""
DIR_CACHE_MSG_PATH = ""
# 新增：路径解析缓存（内存+持久化）
PATH_RESOLVE_CACHE: Dict[str, Tuple[str, float]] = {}  # {输入路径: (解析后路径, 缓存时间)}
PATH_CACHE_TTL = 1800  # 路径缓存过期时间（30分钟）
PATH_SCAN_DEPTH = 10  # 路径扫描深度（从程序根目录开始，最大10级）
# 命令映射缓存：{系统类型: {命令名: 命令处理函数/路径}}
CMD_MAPPING_CACHE: Dict[str, Dict[str, Any]] = {}
CMD_CACHE_TTL = 3600  # 命令映射缓存过期时间（1小时）
# 目录文件缓存
DIR_FILE_CACHE: Dict[str, Tuple[List[Dict[str, Any]], float, int]] = {}
DIR_CACHE_TTL = 1800
DIR_CACHE_MAX_FILES = 100

# 在文件开头添加全局变量
SADO_CONFIG_PATH: str = ""  # 在 init_user_home 后设置
SADO_CONFIG: List[Dict[str, Any]] = []  # 存储 sado 配置

def init_sado_config(request_id: str) -> None:
    _sync_globals_to_ctx()
    from core.bootstrap import init_sado_config as _isc
    from core.context import get_ctx
    _isc(get_ctx(), request_id)

def init_onyxlog_permission(request_id: str) -> None:
    """
    OS 模式权限规则：
    - /onyxlog：所有用户可写、不可删（目录 +t 粘滞位 + 777）
    - /onyx：所有用户只读，不可修改/删除
    TBS 模式：不做任何权限限制
    """
    global ROOT_DIR, OS_OR_TBS, user_info

    # TBS 模式直接跳过
    if OS_OR_TBS == "TBS":
        log_info("TBS 模式，跳过权限限制", request_id)
        return

    # 非 root 跳过
    if not user_info.get("is_admin", False):
        return

    # ==================== OS 模式 + root ====================
    import os
    import stat

    # 1) 设置 /onyxlog：所有用户可写、不可删
    onyxlog = os.path.join(ROOT_DIR, "onyxlog")
    if os.path.exists(onyxlog):
        # 目录权限 777 + 粘滞位(+t)
        os.chmod(onyxlog, 0o1777)
        log_info(f"[权限] {onyxlog} → 所有用户可写不可删", request_id)

    # 2) 设置 /onyx：所有用户不可修改、不可删除
    onyx = os.path.join(ROOT_DIR, "onyx")
    if os.path.exists(onyx):
        # 目录 555：所有用户只读，禁止写/删
        os.chmod(onyx, 0o555)
        log_info(f"[权限] {onyx} → 所有用户不可修改", request_id)


# 新增：Msgpack 工具函数（改为调用makecache.py）
def save_msgpack(path: str, data: Any) -> bool:
    from lib.makecache import save_msgpack as lib_save_msgpack
    return lib_save_msgpack(path, data)

def load_msgpack(path: str) -> Optional[Any]:
    from lib.makecache import load_msgpack as lib_load_msgpack
    return lib_load_msgpack(path)

def build_cmd_mapping_cache(request_id: str) -> None:
    from lib.makecache import build_cmd_mapping_cache as lib_build_cmd_mapping_cache
    lib_build_cmd_mapping_cache(
        request_id=request_id,
        sys_type=sys_type,
        BUILTIN_COMMANDS=BUILTIN_COMMANDS,
        current_sys_cmds=current_sys_cmds,
        TOOL_INDEX_CACHE=TOOL_INDEX_CACHE,
        CMD_MAPPING_MSG_PATH=CMD_MAPPING_MSG_PATH,
        log_info=log_info
    )

def load_cmd_mapping_cache(request_id: str) -> None:
    from lib.makecache import load_cmd_mapping_cache as lib_load_cmd_mapping_cache
    sys_cache = lib_load_cmd_mapping_cache(
        request_id=request_id,
        sys_type=sys_type,
        CMD_MAPPING_MSG_PATH=CMD_MAPPING_MSG_PATH,
        CMD_CACHE_TTL=CMD_CACHE_TTL,
        log_info=log_info
    )
    if sys_cache:
        CMD_MAPPING_CACHE[sys_type] = sys_cache
    else:
        build_cmd_mapping_cache(request_id)

def load_directory_cache(request_id: str) -> None:
    from lib.makecache import load_directory_cache as lib_load_directory_cache
    global DIR_FILE_CACHE
    DIR_FILE_CACHE = lib_load_directory_cache(
        DIR_CACHE_MSG_PATH=DIR_CACHE_MSG_PATH,
        DIR_CACHE_TTL=DIR_CACHE_TTL,
        DIR_CACHE_MAX_FILES=DIR_CACHE_MAX_FILES,
        log_info=log_info,
        request_id=request_id
    )
    # 容量硬上限：最多保留 500 个目录缓存（按时间排序，保留最新的）
    if len(DIR_FILE_CACHE) > 500:
        sorted_items = sorted(DIR_FILE_CACHE.items(), key=lambda x: x[1][1], reverse=True)
        DIR_FILE_CACHE = dict(sorted_items[:500])
        log_info(f"目录缓存截断至 500 条", request_id)

def cache_directory_files(dir_path: str, request_id: str) -> None:
    from lib.makecache import cache_directory_files as lib_cache_directory_files
    lib_cache_directory_files(
        dir_path=dir_path,
        request_id=request_id,
        ROOT_DIR=ROOT_DIR,
        PATH_SCAN_DEPTH=PATH_SCAN_DEPTH,
        DIR_CACHE_MAX_FILES=DIR_CACHE_MAX_FILES,
        DIR_CACHE_MSG_PATH=DIR_CACHE_MSG_PATH,
        log_info=log_info,
        log_warning=log_warning,
        log_error=log_error
    )

def get_cached_cmd(cmd_name: str) -> Tuple[str, Any]:
    global CMD_MAPPING_CACHE, sys_type
    cmd_name_lower = cmd_name.lower()
    
    if sys_type not in CMD_MAPPING_CACHE:
        return ("none", None)
    
    sys_cache = CMD_MAPPING_CACHE[sys_type]
    cmd_mapping = sys_cache["mapping"]
    
    # 【最终优先级】1. 内置命令 → 2. 工具命令 → 3. 系统命令
    # 1. 优先匹配内置命令（最高优先级）
    for real_cmd, func in BUILTIN_COMMANDS.items():
        if real_cmd.lower() == cmd_name_lower:
            return ("builtins", func)
    
    # 2. 再匹配工具箱工具（中间优先级）
    if cmd_name_lower in cmd_mapping["tools"]:
        return ("tools", cmd_mapping["tools"][cmd_name_lower])
    
    # 3. 最后匹配系统命令（最低优先级）
    for sys_cmd in cmd_mapping["system"]:
        if sys_cmd.lower() == cmd_name_lower:
            return ("system", sys_cmd)
    
    return ("none", None)





#==================================导入命令映射表=======================(模块化了)
from etc.mapping import *


# Onyx.py - 修改 init_user_home 函数

def init_user_home() -> bool:
    """
    只读取 HOME 环境变量，其他什么都不做
    目录初始化由 Main.py 完全负责
    """
    global USER_HOME_DIR, CURRENT_VIRTUAL_PATH, VIRTUAL_ROOT, AUTO_CMD_PATH, LOG_FILE_PATH, LOG_DIR, LOG_TIMESTAMP
    
    # 只读取 HOME 环境变量
    USER_HOME_DIR = os.environ.get("HOME", "")
    
    if not USER_HOME_DIR:
        # 如果没有 HOME 变量，说明 Main.py 没有正确初始化，直接失败
        log_error("HOME 环境变量未设置，请检查 Main.py 初始化", user_info["session_id"])
        return False
    
    # 验证目录存在
    if not os.path.exists(USER_HOME_DIR):
        log_error(f"HOME 目录不存在: {USER_HOME_DIR}", user_info["session_id"])
        return False
    
    # 设置虚拟路径显示（仅用于显示，不改变实际目录）
    VIRTUAL_ROOT = ROOT_DIR
    if user_info.get("is_admin", False):
        CURRENT_VIRTUAL_PATH = "root/"
    else:
        username = user_info.get("name", "default").strip()
        CURRENT_VIRTUAL_PATH = f"home/{username}/"
    
    # 设置日志和缓存路径（基于 USER_HOME_DIR）
    username = user_info.get("name", "default").strip()
    AUTO_CMD_PATH = os.path.join(USER_HOME_DIR, ".onyx_autocmd.json")
    LOG_DIR = os.path.join(ROOT_DIR, "onyxlog", "onyx", username)
    LOG_TIMESTAMP = datetime.now().strftime("%Y%m%d%H%M%S")
    LOG_FILE_PATH = os.path.join(LOG_DIR, f"onyx_main_{LOG_TIMESTAMP}.log")
    
    # 缓存路径
    global CACHE_DIR, TOOL_INDEX_MSG_PATH, PATH_INDEX_MSG_PATH, CMD_MAPPING_MSG_PATH, DIR_CACHE_MSG_PATH
    CACHE_DIR = os.path.join(USER_HOME_DIR, ".cache", "onyx", "onyx")
    TOOL_INDEX_MSG_PATH = os.path.join(CACHE_DIR, "tool_index.msgpack")
    PATH_INDEX_MSG_PATH = os.path.join(CACHE_DIR, "path_index.msgpack")
    CMD_MAPPING_MSG_PATH = os.path.join(CACHE_DIR, "cmd_mapping.msgpack")
    DIR_CACHE_MSG_PATH = os.path.join(CACHE_DIR, "dir_cache.msgpack")
    
    log_info(f"用户主目录: {USER_HOME_DIR}", user_info["session_id"])
    return True


def init_user_mapping() -> None:
    """初始化时合并用户扩展映射（USER_HOME_DIR/.mapping.json）到系统命令列表"""
    user_mapping_path = os.path.join(USER_HOME_DIR, ".mapping.json")
    if not os.path.exists(user_mapping_path):
        return
    
    try:
        with open(user_mapping_path, "r", encoding="utf-8") as f:
            user_mapping = json.load(f)
        
        if isinstance(user_mapping, dict) and sys_type in user_mapping and isinstance(user_mapping[sys_type], list):
            user_cmds = user_mapping[sys_type]
            # 合并到当前系统命令列表（去重）
            if sys_type in current_sys_cmds:
                current_sys_cmds[sys_type] = list(set(current_sys_cmds[sys_type] + user_cmds))
            else:
                current_sys_cmds[sys_type] = user_cmds
            log_info(f"合并用户扩展映射命令：共{len(user_cmds)}个", str(uuid.uuid4()))
    except Exception as e:
        log_error(f"合并用户扩展映射失败：{str(e)}", str(uuid.uuid4()))





def scan_path_for_system_cmds(request_id: str) -> None:
    """初始化扫描模块：注入参数到 scan_path_cmds.py"""
    from lib.scan_path_cmds import init_scan_path_cmds, scan_path_for_system_cmds

    init_scan_path_cmds(
        root_dir=ROOT_DIR,
        user_home_dir=USER_HOME_DIR,
        sys_type=sys_type,
        builtin_commands=BUILTIN_COMMANDS,
        tool_index_cache=TOOL_INDEX_CACHE,
        cmd_mapping_msg_path=CMD_MAPPING_MSG_PATH,
        cmd_cache_ttl=CMD_CACHE_TTL,
        max_workers=16
    )
    log_info("扫描模块初始化完成", request_id)
    
    request_id_local = str(uuid.uuid4())
    
    # 异步刷新工具索引（非阻塞）
    from lib.build_tool_index import refresh_tool_index
    refresh_tool_index(
        request_id=request_id_local,
        force=True,
        log_info_func=log_info,
        log_error_func=log_error
    )
    
    # 异步刷新系统命令（非阻塞）
    from lib.scan_path_cmds import refresh_system_cmds
    refresh_system_cmds(
        request_id=request_id_local,
        force=True,
        log_info_func=log_info,
        log_error_func=log_error
    )


_PROMPT_CONF_PATH = os.path.join(os.path.expanduser("~"), ".prompt.conf")
_CACHED_PROMPT_CONF: Optional[str] = None  # prompt= 后面的字符串缓存


def _get_default_prompt_conf() -> str:
    """动态生成默认 prompt.conf 内容（避免模块级字符串被意外格式化）"""
    return (
        "# Onyx Prompt Configuration\n"
        "# Edit the prompt= line below to customize your terminal prompt.\n"
        "# Available fields:\n"
        "#   {user}          — current username\n"
        "#   {host}          — hostname\n"
        "#   {mode_per}      — current permission mode (low/mid/adv)\n"
        "#   {mode_TS}       — system type label (TBS/OS)\n"
        "#   {sys_type}      — fixed \"Onyx\"\n"
        "#   {relative_path} — current working directory (virtual path)\n"
        "#   {permission}    — permission symbol ($/#)\n"
        "#   {venv_git}      — Python venv + git branch/dirty (auto-detected)\n"
        "#   {exit_mark}     — green ✓ if last cmd ok, red ✗ (N) if failed\n"
        "# Color tags (only for comp-window mode):\n"
        "#   {BLUE} {RED} {GREEN} {YELLOW} {RESET}\n"
        "#   {accent}        — dynamic green/red based on last exit code\n"
        "#   {accent_reset}  — reset after {accent}\n"
        "# Example:\n"
        "#   prompt={BLUE}╭──<{RESET}{RED}{user}@{host}-{mode_TS}{BLUE}>-({YELLOW}{mode_per}{BLUE})-[{RESET}{relative_path}{venv_git}{BLUE}]\\n{BLUE}╰─{BLUE}{RED}{permission}{RESET} {accent}>{accent_reset}\n"
        "\n"
        "prompt={BLUE}╭──<{RESET}{RED}{user}@{host}-{mode_TS}{BLUE}>-({YELLOW}{mode_per}{BLUE})-[{RESET}{relative_path}{venv_git}{BLUE}]\\n{BLUE}╰─{BLUE}{RED}{permission}{RESET} {accent}>{accent_reset}\n"
    )


def init_prompt_from_storage() -> None:
    """启动时加载提示符配置：优先 ~/.prompt.conf，不存在则自动创建"""
    global _CACHED_PROMPT_CONF
    try:
        if os.path.exists(_PROMPT_CONF_PATH):
            with open(_PROMPT_CONF_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("prompt=") and not line.startswith("#"):
                        _CACHED_PROMPT_CONF = line[7:]  # 去掉 "prompt="
                        # 还原转义的 \n
                        _CACHED_PROMPT_CONF = _CACHED_PROMPT_CONF.replace("\\n", "\n")
                        break
            if _CACHED_PROMPT_CONF:
                log_info("从 ~/.prompt.conf 加载自定义提示符", user_info["session_id"])
                return
    except Exception as e:
        log_error(f"加载 ~/.prompt.conf 失败：{e}", user_info["session_id"])

    # 不存在或读取失败 → 自动创建
    try:
        default_conf = _get_default_prompt_conf()
        with open(_PROMPT_CONF_PATH, "w", encoding="utf-8") as f:
            f.write(default_conf)
        # 从默认内容中提取 prompt= 行
        for line in default_conf.split("\n"):
            if line.startswith("prompt="):
                _CACHED_PROMPT_CONF = line[7:].replace("\\n", "\n")
                break
        log_info("已创建 ~/.prompt.conf（可编辑自定义提示符）", user_info["session_id"])
    except Exception as e:
        log_error(f"创建 ~/.prompt.conf 失败：{e}", user_info["session_id"])
        # 回退到旧系统
        _fallback_init_prompt()


def _fallback_init_prompt() -> None:
    """旧系统回退：从 ~/.config/onyx/.prompt 加载模板名"""
    PROMPT_STORAGE_PATH = os.path.join(USER_HOME_DIR, ".config", "onyx", ".prompt")
    if os.path.exists(PROMPT_STORAGE_PATH):
        try:
            with open(PROMPT_STORAGE_PATH, "r", encoding="utf-8") as f:
                saved_template = f.read().strip()
            prompt_config = global_config.get("display_info", {}).get("command_prompts", {})
            if saved_template in prompt_config:
                global_config["system_info"]["current_prompt_type"] = saved_template
        except Exception:
            pass

        
def get_real_current_dir() -> str:
    """获取当前真实工作目录（物理路径），对齐原生pwd"""
    return os.getcwd()
    
    
# c库，只传话
def resolve_path(path: str) -> str:
    """路径解析接口：根据 _SANDBOX_ENABLED 动态决定根目录（若未启用沙箱则使用 /）"""
    # 获取沙箱启用状态
    sandbox_enabled = _SANDBOX_ENABLED
    effective_root = ROOT_DIR if sandbox_enabled else "/"
    
    # 确保 resolve_path 模块已初始化（首次调用时初始化）
    if not hasattr(resolve_path, "_initialized"):
        from lib import resolve_path as resolve_path_lib
        # 注意：USER_HOME_DIR 保持不变，只有 root_dir 可能变为 /
        resolve_path_lib.init_resolve_path(effective_root, USER_HOME_DIR)
        resolve_path._initialized = True
        resolve_path._lib = resolve_path_lib
    else:
        # 如果沙箱状态发生变化（极少见），重新初始化模块
        if getattr(resolve_path, "_last_root", None) != effective_root:
            from lib import resolve_path as resolve_path_lib
            resolve_path._lib.init_resolve_path(effective_root, USER_HOME_DIR)
            resolve_path._last_root = effective_root
    
    # 调用核心模块解析路径
    return resolve_path._lib.resolve_path(path)




    
def validate_param_path(param: str, request_id: str) -> str:
    """
    直接调用resolve_path模块解析参数路径，无重复逻辑
    流程：选项参数过滤 → 调用resolve_path模块 → 沙箱校验 → 返回结果
    重要：沙箱校验失败时，直接返回空字符串，表示该参数无效（命令会因此失败）
    """
    # 1. 选项参数（以'-'开头）直接返回，不解析
    if param.startswith('-'):
        log_info(f"参数{param}为选项参数，跳过路径解析", request_id)
        return param
    
    # 2. 判断是否看起来像路径参数
    if not param.startswith(('./', '/', '../', '~/')) and '.' not in param:
        log_info(f"参数{param}不是路径参数，跳过路径解析", request_id)
        return param
    
    # 3. 直接调用resolve_path模块
    try:
        resolved_path = resolve_path(param)
        
        # 4. 沙箱校验 - 如果全局沙箱未启用，直接返回解析后的路径
        if not _SANDBOX_ENABLED:
            log_info(f"沙箱已禁用，参数{param}解析后直接返回：{resolved_path}", request_id)
            return resolved_path
        
        # 沙箱启用时进行校验
        if check_sandbox_path(resolved_path, request_id):
            log_info(f"参数路径解析通过：输入={param} → 解析后={resolved_path}", request_id)
            return resolved_path
        else:
            log_warning(f"参数{param}对应路径{resolved_path}触发沙箱拦截，返回空值使命令失败", request_id)
            return ""
    except Exception as e:
        log_warning(f"参数{param}路径解析失败：{str(e)}，返回原始值", request_id)
        return param





    
#和validate_param_path一块整成lib
def parse_options(args: List[str], supported_short: List[str], supported_long: List[str]) -> Tuple[List[str], List[str]]:
    """
    彻底修复选项解析：正确处理长选项和短选项
    """
    options = []
    params = []
    i = 0
    
    while i < len(args):
        arg = args[i]
        
        # 特殊处理：单独的 - 参数（cd -）
        if arg == '-':
            params.append(arg)
            i += 1
            continue
            
        # 长选项（--xxx）
        if arg.startswith('--'):
            # 检查是否是完全匹配的长选项
            if arg in supported_long:
                options.append(arg)
            else:
                # 检查是否是部分匹配的长选项（如 --ver 匹配 --version）
                matched = False
                for long_opt in supported_long:
                    if arg.startswith(long_opt):
                        options.append(arg)
                        matched = True
                        break
                if not matched:
                    raise ValueError(f"invalid option -- '{arg[2:]}'")
            i += 1
            
        # 短选项（必须是完全匹配的单个字符，如 -P、-L）
        elif arg.startswith('-') and len(arg) == 2:
            if arg in supported_short:
                options.append(arg)
            else:
                raise ValueError(f"invalid option -- '{arg[1]}'")
            i += 1
            
        # 其他以-开头但不是选项的情况（如 -version）
        elif arg.startswith('-'):
            # 当作参数处理，不是选项
            params.append(arg)
            i += 1
            
        # 普通参数
        else:
            params.append(arg)
            i += 1
            
    return options, params

#没必要改为C语言
def get_physical_path(virtual_path: str) -> str:
    """将虚拟路径转换为物理路径（修复根目录问题）"""
    return resolve_path(virtual_path)
    
#不需要改为C语言
def get_virtual_path(physical_path: str) -> str:
    from core.path_ops import get_virtual_path as _gvp
    from core.context import get_ctx
    return _gvp(get_ctx(), physical_path)


#=================判断命令是否是交互式===============

def init_user_cmd_cli_file() -> None:
    """初始化用户交互式命令配置文件：user_home_dir/.user_cmd_cli.txt"""
    global USER_HOME_DIR
    cli_file_path = os.path.join(USER_HOME_DIR, ".user_cmd_cli.txt")
    # 若文件不存在则创建并写入默认交互式命令
    if not os.path.exists(cli_file_path):
        default_cmds = "\n".join([
            "vim", "nano", "vi", "emacs", "less", "more", "man",
            "top", "htop", "btop",
            "zsh", "fish", "ftp", "ssh", "telnet", "mysql", "sqlite3"
        ])
        try:
            with open(cli_file_path, "w", encoding="utf-8") as f:
                f.write(default_cmds)
            # 设置文件权限（仅所有者可读写）
            if sys_type in ["Linux/macOS", "macOS", "Termux", "SpecialLinux"]:
                os.chmod(cli_file_path, 0o600)
            log_info(f"初始化交互式命令配置文件：{cli_file_path}", user_info["session_id"])
        except Exception as e:
            log_error(f"初始化.user_cmd_cli.txt失败：{str(e)}", user_info["session_id"])

# 用户交互命令缓存：避免每条命令都读磁盘
_uic_cache: Optional[List[str]] = None
_uic_cache_time: float = 0
_uic_cache_ttl: float = 60  # 60 秒缓存

def load_user_interactive_cmds() -> List[str]:
    """从.user_cmd_cli.txt加载用户自定义交互式命令（60s 内存缓存）"""
    global _uic_cache, _uic_cache_time
    now = time.time()
    if _uic_cache is not None and (now - _uic_cache_time) < _uic_cache_ttl:
        return _uic_cache
    
    cli_file_path = os.path.join(USER_HOME_DIR, ".user_cmd_cli.txt")
    if not os.path.exists(cli_file_path):
        init_user_cmd_cli_file()
        _uic_cache = []
        _uic_cache_time = now
        return []
    
    try:
        with open(cli_file_path, "r", encoding="utf-8") as f:
            # 读取非注释、非空行
            cmds = [line.strip().lower() for line in f 
                    if line.strip() and not line.startswith("#")]
        _uic_cache = list(set(cmds))  # 去重
        _uic_cache_time = now
        return _uic_cache
    except Exception as e:
        log_error(f"加载交互式命令失败：{str(e)}", user_info["session_id"])
        _uic_cache = []
        _uic_cache_time = now
        return []

# 交互式命令集：默认列表 + 用户配置，预编译为 frozenset 实现 O(1) 查找
_INTERACTIVE_DEFAULT = frozenset([
    "vim", "nano", "vi", "emacs", "less", "more", "man",
    "top", "htop", "btop", "python3", "bash", "sh",
    "zsh", "fish", "ftp", "ssh", "telnet", "mysql", "sqlite3"
])
_INTERACTIVE_FULL: frozenset = _INTERACTIVE_DEFAULT
_uic_last_hash: int = 0  # 检测用户命令是否变化

def is_interactive_command(cmd: str) -> bool:
    """增强版：结合默认列表和用户配置判断是否为交互式命令（O(1) frozenset 查找）"""
    global _INTERACTIVE_FULL, _uic_last_hash
    if not cmd.strip():
        return False
    
    # 用户命令变更时重建 frozenset（基于哈希检测，避免每次字符串比较）
    user_cmds = load_user_interactive_cmds()
    uic_hash = len(user_cmds)  # 简单长度哈希，足以检测变更
    if uic_hash != _uic_last_hash:
        _INTERACTIVE_FULL = _INTERACTIVE_DEFAULT | frozenset(user_cmds)
        _uic_last_hash = uic_hash
    
    cmd_name = cmd.split()[0].lower()
    return cmd_name in _INTERACTIVE_FULL


# -------------------------- 日志模块 --------------------------
def init_logger() -> None:
    global CURRENT_LOG_LEVEL, log_file_handler
    log_level = global_config["log_config"]["log_level"]
    CURRENT_LOG_LEVEL = LOG_LEVELS.get(log_level, LOG_LEVELS["INFO"])
     
    # -------------------------- 新增：创建日志目录（若不存在） --------------------------
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, mode=0o755 if sys_type != "Windows" else 0o777)
        log_info(f"创建日志存储目录：{LOG_DIR}", user_info["session_id"])
    
    check_log_rotation()
    try:
        
        log_file_handler = open(LOG_FILE_PATH, "a", encoding="utf-8")
        
        
        log_info("日志系统初始化完成", user_info["session_id"])
    except Exception as e:
        print(Fore.RED + f"日志初始化失败：{str(e)}" + Style.RESET_ALL)
        log_file_handler = None
        
        
# 日志 rotation 检查节流：避免每条日志都 os.path.getsize()
_last_rotation_check_time = 0
_rotation_check_interval = 60  # 每 60 秒最多检查一次
_log_write_count = 0
_rotation_check_log_count = 100  # 或每 100 条日志检查一次

def check_log_rotation() -> None:
    # 仅当日志文件存在且超过限制大小时才轮转
    if os.path.exists(LOG_FILE_PATH) and os.path.getsize(LOG_FILE_PATH) >= global_config["log_config"]["log_rotation_size"]:
        # 轮转后的旧日志名：.onyx-main-时间戳.log.轮转时间戳（如.onyx-main-20251029214000.log.20251029215000）
        rotate_timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        old_log = f"{LOG_FILE_PATH}.{rotate_timestamp}"
        shutil.move(LOG_FILE_PATH, old_log)
        print(Fore.YELLOW + f"日志轮转完成：{os.path.basename(old_log)}" + Style.RESET_ALL)


# 日志上下文缓存：会话期间不变的字段只计算一次
_log_ctx_user_name = None
_log_ctx_client_ip = None
_log_ctx_sys = None
_log_ctx_session = None

def get_log_context(request_id: str) -> str:
    """增强日志上下文，添加用户+环境相关字段（不变字段首次计算后缓存）"""
    global _log_ctx_user_name, _log_ctx_client_ip, _log_ctx_sys, _log_ctx_session
    
    # 会话不变字段：首次调用时计算并缓存
    if _log_ctx_user_name is None:
        _log_ctx_user_name = user_info.get("name", "default")
        _log_ctx_client_ip = get_local_ip()
        _log_ctx_sys = "Windows" if sys_type.startswith("win32") else "Linux"
        _log_ctx_session = user_info['session_id']
    
    # 动态字段：每次重新获取（可能随模式切换变化）
    user_role = "admin" if user_info.get("is_admin", False) else "user"
    current_mode = user_mode.current_mode if user_mode else "low"
    tool_perm = user_mode.current_tool_perm if user_mode else 3
    current_virtual_dir = CURRENT_VIRTUAL_PATH if CURRENT_VIRTUAL_PATH else "~"
    
    # 格式：[用户字段][环境字段][会话/请求ID]
    return f"[user:{_log_ctx_user_name}][role:{user_role}][mode:{current_mode}][perm:{tool_perm}][ip:{_log_ctx_client_ip}][dir:{current_virtual_dir}][system:{_log_ctx_sys}][session:{_log_ctx_session}][req:{request_id}] "



def log_base(level: str, content: str, request_id: str) -> None:
    if LOG_LEVELS[level] < CURRENT_LOG_LEVEL:
        return
    
    time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    context = get_log_context(request_id)
    log_line = f"[{time_str}] [{level}] {context}{content}\n"
    
    color_map = {"INFO": Fore.GREEN, "WARNING": Fore.YELLOW, "ERROR": Fore.RED, "CRITICAL": Fore.MAGENTA}
    
    if log_file_handler:
        try:
            # 节流 rotation 检查：每 60s 或每 100 条日志才检查一次
            global _last_rotation_check_time, _log_write_count
            _log_write_count += 1
            _now = time.time()
            if (_now - _last_rotation_check_time >= _rotation_check_interval
                    or _log_write_count >= _rotation_check_log_count):
                check_log_rotation()
                _last_rotation_check_time = _now
                _log_write_count = 0
            log_file_handler.write(log_line)
            log_file_handler.flush()
        except Exception as e:
            print(Fore.RED + f"日志写入失败：{str(e)}" + Style.RESET_ALL)

def log_info(content: str, request_id: str) -> None:
    log_base("INFO", content, request_id)  # 添加 log_base 调用

def log_error(content: str, request_id: str) -> None:
    log_base("ERROR", content, request_id)

def log_warning(content: str, request_id: str) -> None:
    log_base("WARNING", content, request_id)

def security_log(content: str, request_id: str, event_type: str = "SECURITY") -> None:
    time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    # 拼接用户+环境字段
    user_name = user_info.get("name", "default")
    user_role = "admin" if user_info.get("is_admin", False) else "user"
    current_mode = user_mode.current_mode if user_mode else "low"
    client_ip = get_local_ip()
    current_virtual_dir = CURRENT_VIRTUAL_PATH if CURRENT_VIRTUAL_PATH else "~"
    sys_type_simplified = "Windows" if sys_type.startswith("win32") else "Linux"
    
    context = f"[user:{user_name}][role:{user_role}][mode:{current_mode}][ip:{client_ip}][dir:{current_virtual_dir}][system:{sys_type_simplified}][session:{user_info['session_id']}][req:{request_id}] "
    log_line = f"[{time_str}] [{event_type}] {context}{content}\n"
    
    try:
        with open(SANDBOX_CONFIG["log_path"], "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception as e:
        print(Fore.MAGENTA + f"ERROR：{str(e)} → Event：{content}" + Style.RESET_ALL)



# -------------------------- 配置加载 --------------------------
def load_config() -> bool:
    global global_config, executable_config
    global MAX_HISTORY_LEN, PROMPT_TEMPLATES, CURRENT_PROMPT, SANDBOX_CONFIG
    global TOOL_MAIN_DIR, SUPPORTED_EXEC_SUFFIXES, PYTHON_EXE, executor
    global ADMIN_PASSWORD_PATH, USER_CONFIG_PATH, USER_HISTORY_PATH, current_sys_cmds
    # 新增：延迟初始化 LOG_DIR（确保 ROOT_DIR 和 user_info["name"] 已有效）
    username = user_info.get("name", "default")
    # 定义语言配置路径（固定为 .../onyx/etc/config/language）
    LANGUAGE_CONFIG_PATH = os.path.join(USER_HOME_DIR,  ".config", "onyx", "language")
    
    # 读取语言配置（优先从文件获取，无文件则创建默认中文）
    def get_current_lang() -> str:
        try:
            lang_dir = os.path.dirname(LANGUAGE_CONFIG_PATH)
            if not os.path.exists(lang_dir):
                os.makedirs(lang_dir, mode=0o755 if sys_type != "Windows" else 0o777)
            
            if os.path.exists(LANGUAGE_CONFIG_PATH):
                with open(LANGUAGE_CONFIG_PATH, "r", encoding="utf-8") as f:
                    lang = f.read().strip().lower()
                    return lang if lang in ["chinese", "english"] else "chinese"
            else:
                # 首次运行创建默认中文配置
                with open(LANGUAGE_CONFIG_PATH, "w", encoding="utf-8") as f:
                    f.write("chinese")
                return "chinese"
        except Exception:
            return "chinese"
    
    current_lang = get_current_lang()
    lang_msgs = {
        "chinese": {
            "config_not_found": "配置文件不存在：{}，程序无法启动",
            "config_format_error": "{} 格式错误：{}（行号：{}，列号：{}），程序无法启动",
            "config_read_fail": "{} 读取失败：{}，程序无法启动",
            "missing_required_nodes": "{} 缺失必要节点：{}，程序无法启动",
            "config_loaded_success": "{} 加载成功",
            "log_dir_created": "创建日志存储目录：{}",
            "template_reset": "模板不存在，已重置为'def'并修复config.json",
            "lang_config_loaded": "语言配置加载成功：{}",
            "lang_config_fail": "语言配置加载失败：{}，默认使用中文"
        },
        "english": {
            "config_not_found": "Config file not found: {}, program cannot start",
            "config_format_error": "{} format error: {} (line: {}, column: {}), program cannot start",
            "config_read_fail": "{} read failed: {}, program cannot start",
            "missing_required_nodes": "{} missing required nodes: {}, program cannot start",
            "config_loaded_success": "{} loaded successfully",
            "log_dir_created": "Log storage directory created: {}",
            "template_reset": "Template not found, reset to 'def' and fixed config.json",
            "lang_config_loaded": "Language config loaded successfully: {}",
            "lang_config_fail": "Language config load failed: {}, use chinese by default"
        }
    }
    msg = lang_msgs[current_lang]
    
    # 加载核心配置文件 config.json
    if not os.path.exists(CONFIG_FILE_PATH):
        print(Fore.RED + msg["config_not_found"].format(CONFIG_FILE_PATH) + Style.RESET_ALL)
        return False
    
    try:
        with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
            global_config = json.load(f)
        log_info(msg["config_loaded_success"].format("config.json"), user_info["session_id"])
    except json.JSONDecodeError as e:
        print(Fore.RED + msg["config_format_error"].format("config.json", str(e), e.lineno, e.colno) + Style.RESET_ALL)
        return False
    except Exception as e:
        print(Fore.RED + msg["config_read_fail"].format("config.json", str(e)) + Style.RESET_ALL)
        return False
    
    # 加载 executable_config（etc/executable.json）
    EXECUTABLE_CONFIG_PATH = os.path.join(ROOT_DIR, "onyx", "etc", "executable.json")
    if not os.path.exists(EXECUTABLE_CONFIG_PATH):
        print(Fore.RED + msg["config_not_found"].format(EXECUTABLE_CONFIG_PATH) + Style.RESET_ALL)
        return False
        
    
    init_logger()
         
    try:
        with open(EXECUTABLE_CONFIG_PATH, "r", encoding="utf-8") as f:
            executable_config = json.load(f)
        # 校验必要节点
        required_nodes = ["sys_suffix", "launch_cmd", "search_depth", "tool_search_rule"]
        missing_nodes = [node for node in required_nodes if node not in executable_config]
        if missing_nodes:
            print(Fore.RED + msg["missing_required_nodes"].format("executable.json", ", ".join(missing_nodes)) + Style.RESET_ALL)
            return False
        log_info(msg["config_loaded_success"].format(EXECUTABLE_CONFIG_PATH), user_info["session_id"])
    except json.JSONDecodeError as e:
        print(Fore.RED + msg["config_format_error"].format("executable.json", str(e), e.lineno, e.colno) + Style.RESET_ALL)
        return False
    except Exception as e:
        print(Fore.RED + msg["config_read_fail"].format("executable.json", str(e)) + Style.RESET_ALL)
        return False
    
    # 加载 perm_limit 配置（etc/cmdal.json）
    CMDAL_CONFIG_PATH = os.path.join(ROOT_DIR, "onyx", "etc", "cmdal.json")
    if not os.path.exists(CMDAL_CONFIG_PATH):
        print(Fore.RED + msg["config_not_found"].format(CMDAL_CONFIG_PATH) + Style.RESET_ALL)
        return False
    
    try:
        with open(CMDAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            cmdal_config = json.load(f)
        if "perm_limit" not in cmdal_config:
            print(Fore.RED + msg["missing_required_nodes"].format("cmdal.json", "perm_limit") + Style.RESET_ALL)
            return False
        global_config["mode_config"]["perm_limit"] = cmdal_config["perm_limit"]
        log_info(msg["config_loaded_success"].format(CMDAL_CONFIG_PATH), user_info["session_id"])
    except json.JSONDecodeError as e:
        print(Fore.RED + msg["config_format_error"].format("cmdal.json", str(e), e.lineno, e.colno) + Style.RESET_ALL)
        return False
    except Exception as e:
        print(Fore.RED + msg["config_read_fail"].format("cmdal.json", str(e)) + Style.RESET_ALL)
        return False
        
    if sys_type == "Termux" and OS_OR_TBS == "OS":
        if "Linux/macOS" in current_sys_cmds:
            current_sys_cmds["Termux"] = current_sys_cmds["Linux/macOS"].copy()
            
    # 配置映射（从 global_config 和 executable_config 提取参数）
    SYS_SPECIFIC = global_config["sys_specific"]
    MAX_HISTORY_LEN = global_config["system_info"]["max_history_len"]
    PROMPT_TEMPLATES = global_config["display_info"]["command_prompts"]
    CURRENT_PROMPT = global_config["system_info"].get("current_prompt_type", "def")
    SANDBOX_CONFIG = global_config["security"]
    
    # 从 executable_config 提取可执行相关配置
    SUPPORTED_EXEC_SUFFIXES = executable_config["sys_suffix"]
    SEARCH_DEPTH = executable_config["search_depth"]
    TOOL_SEARCH_RULE = executable_config["tool_search_rule"]
    LAUNCH_CMD = executable_config["launch_cmd"]
    
    # 路径初始化（基于程序根目录）
    tool_main_dir_name = global_config["program_info"]["tool_main_dir_name"]
    TOOL_MAIN_DIR = os.path.join(ROOT_DIR, tool_main_dir_name)
    
    USER_CONFIG_PATH = os.path.join(USER_HOME_DIR, ".onyx_user_config.json")
    USER_HISTORY_PATH = os.path.join(USER_HOME_DIR, ".onyx_cmd_history")
    
    
    # 线程池初始化
    executor = ThreadPoolExecutor(max_workers=SANDBOX_CONFIG["max_process_count"])
    PYTHON_EXE = "python"
    
    # 命令提示符模板异常修复
    if CURRENT_PROMPT not in PROMPT_TEMPLATES:
        CURRENT_PROMPT = "def"
        global_config["system_info"]["current_prompt_type"] = CURRENT_PROMPT
        with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(global_config, f, ensure_ascii=False, indent=2)
        log_warning(msg["template_reset"], user_info["session_id"])
    
    # 新增：初始化安全日志文件（修复日志写入失败）
    if "log_path" in SANDBOX_CONFIG:
        log_dir = os.path.dirname(SANDBOX_CONFIG["log_path"])
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, mode=0o700)
        if not os.path.exists(SANDBOX_CONFIG["log_path"]):
            with open(SANDBOX_CONFIG["log_path"], "w", encoding="utf-8") as f:
                f.write("")
            if sys_type != "Windows":
                os.chmod(SANDBOX_CONFIG["log_path"], 0o600)
    
    
    # 同步语言配置到全局（供其他模块使用）
    try:
        global_config["display_info"]["language"]["current"] = current_lang
        log_info(msg["lang_config_loaded"].format(current_lang), user_info["session_id"])
    except Exception as e:
        log_error(msg["lang_config_fail"].format(str(e)), user_info["session_id"])
        global_config["display_info"]["language"]["current"] = "chinese"
    
    return True








# -------------------------- 初始化 --------------------------
# 新增：读取语言配置文件的通用函数（禁用BOM编码，强制UTF-8无BOM）
def get_current_lang() -> str:
    """从 .../onyx/etc/config/language 读取语言（UTF-8无BOM），默认中文"""
    LANGUAGE_CONFIG_PATH = os.path.join(USER_HOME_DIR,  ".config", "onyx", "language")
    try:
        # 确保目录存在
        lang_dir = os.path.dirname(LANGUAGE_CONFIG_PATH)
        if not os.path.exists(lang_dir):
            os.makedirs(lang_dir, mode=0o755 if sys_type != "Windows" else 0o777)
        
        # 读取文件（禁用BOM，仅支持UTF-8无BOM编码）
        if os.path.exists(LANGUAGE_CONFIG_PATH):
            # 以UTF-8无BOM模式读取，自动忽略BOM（若存在则清理）
            with open(LANGUAGE_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                lang = f.read().strip().lower()
            # 验证语言合法性
            return lang if lang in ["chinese", "english"] else "chinese"
        else:
            # 创建默认配置（强制UTF-8无BOM写入）
            with open(LANGUAGE_CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write("chinese")
            return "chinese"
    except Exception:
        return "chinese"

def init_colorama() -> None:
    # colorama 已替换为 lib.terminal.colors（纯 ANSI），无需初始化
    pass

def detect_system() -> str:
    if "termux" in sys.prefix.lower():
        return "Termux"
    elif sys.platform.startswith("win32"):
        return "Windows"
    elif sys.platform.startswith("darwin"):
        return "macOS"
    else:
        if os.path.exists("/etc/os-release"):
            with open("/etc/os-release", "r") as f:
                content = f.read()
                if "ID=kali" in content:
                    return "SpecialLinux"
        return "Linux/macOS"



def init_sandbox_config() -> None:
    """根据系统类型和权限初始化沙箱配置文件"""
    global _SANDBOX_ENABLED, ROOT_DIR, _SANDBOX_CONFIG_PATH
    
    # ========== 关键修复：基于当前文件位置定位配置目录 ==========
    current_dir = os.path.dirname(os.path.abspath(__file__))
    onyx_root = os.path.dirname(current_dir)
    parent_of_onyx = onyx_root
    _SANDBOX_CONFIG_PATH = os.path.abspath(os.path.join(parent_of_onyx, "etc", "onyx", "sandbox"))
    config_dir = os.path.dirname(_SANDBOX_CONFIG_PATH)
    
    cur_lang = get_current_lang()
    
    # 1. 如果配置文件已存在，读取其内容
    if os.path.exists(_SANDBOX_CONFIG_PATH):
        try:
            with open(_SANDBOX_CONFIG_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip().lower()
                _SANDBOX_ENABLED = (content == "true")
            log_info(f"沙箱配置加载：{_SANDBOX_CONFIG_PATH} -> enabled={_SANDBOX_ENABLED}", str(uuid.uuid4()))
        except:
            _SANDBOX_ENABLED = True
        return

    # 2. 配置文件不存在，根据系统类型决定行为
    sys_type_local = detect_system()
    is_root = False
    if sys.platform.startswith("linux") or sys.platform == "darwin" or "termux" in sys.prefix.lower():
        try:
            is_root = (os.geteuid() == 0)
        except:
            is_root = False

    # ========== 新增：检查是否为 OS 模式（虚拟根目录 == 真实根目录） ==========
    # 获取真实根目录
    if sys_type_local == "Windows":
        real_root = os.path.splitdrive(os.path.abspath("."))[0] + "\\"
    else:
        real_root = "/"
    
    virtual_root = ROOT_DIR  # 当前 ROOT_DIR 值
    normalized_virtual = os.path.normpath(virtual_root)
    normalized_real = os.path.normpath(real_root)
    is_os_mode = (normalized_virtual == normalized_real)
    
    # ========== 关键修改：OS 模式下静默启用沙箱（无影响，不询问） ==========
    if is_os_mode:
        _SANDBOX_ENABLED = True
        log_info(f"OS 模式（根目录重合），沙箱静默启用（实际无影响）", str(uuid.uuid4()))
        # 不创建配置文件，不询问用户
        return

    # ========== 新增：Linux（非Termux）强制启用沙箱，静默跳过，不询问 ==========
    # 判断是否为 Linux（排除 Termux、Windows、macOS）
    is_linux = (sys_type_local == "Linux/macOS" or sys_type_local == "SpecialLinux") and "termux" not in sys.prefix.lower()
    
    if is_linux:
        # Linux 强制启用沙箱，静默跳过，不询问用户，不输出任何提示
        _SANDBOX_ENABLED = True
        log_info("Linux系统，强制启用沙箱（静默模式）", str(uuid.uuid4()))
        # 将配置写入文件，方便用户后续通过 manage 修改
        if not os.path.exists(config_dir):
            os.makedirs(config_dir, mode=0o755)
        try:
            with open(_SANDBOX_CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write("true")
            if sys_type in ["Linux/macOS", "macOS", "Termux", "SpecialLinux"]:
                os.chmod(_SANDBOX_CONFIG_PATH, 0o644)
            log_info(f"沙箱配置已保存：{_SANDBOX_CONFIG_PATH} -> {_SANDBOX_ENABLED}", str(uuid.uuid4()))
        except Exception as e:
            log_error(f"沙箱配置保存失败：{str(e)}", str(uuid.uuid4()))
        return

    # Linux 普通用户 -> 强制启用沙箱，不询问不创建文件（保留原有逻辑）
    if sys_type_local == "Linux/macOS" and not is_root:
        _SANDBOX_ENABLED = True
        log_info("Linux普通用户，强制启用沙箱", str(uuid.uuid4()))
        return

    # ========== Termux 或 Windows 或 Linux root（且非OS模式）-> 询问用户（保持不变） ==========
    if not os.path.exists(config_dir):
        os.makedirs(config_dir, mode=0o755)
    
    prompt_msg = {
        "chinese": "未检测到沙箱配置文件。是否启用沙箱？(y/N，启用后更安全；输入 n 后将使用系统真实根目录，可通过修改 {} 文件更改): ",
        "english": "Sandbox config not found. Enable sandbox? (y/N, safer; if n, use real system root. You can change later by editing {}): "
    }
    
    # 交叉平台输入——确保终端处于 cooked 模式，用 try/finally 保证恢复
    answer = "n"
    fd = None
    old_tty = None
    try:
        import termios
        fd = sys.stdin.fileno()
        old_tty = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        # 开启 ICANON（行缓冲）、ECHO（回显）、ICRNL（\r→\n 转换）
        new[3] |= (termios.ICANON | termios.ECHO)
        new[0] |= termios.ICRNL
        termios.tcsetattr(fd, termios.TCSANOW, new)
        sys.stdout.write(Fore.YELLOW + prompt_msg[cur_lang].format(_SANDBOX_CONFIG_PATH) + Style.RESET_ALL + " ")
        sys.stdout.flush()
        answer = sys.stdin.readline().strip().lower()
    except BaseException:
        # 如果 termios 不可用或 stdin 不是 tty，回退到 input()
        try:
            print(Fore.YELLOW + prompt_msg[cur_lang].format(_SANDBOX_CONFIG_PATH) + Style.RESET_ALL, end="")
            answer = input().strip().lower()
        except BaseException:
            answer = "n"
    finally:
        if old_tty is not None and fd is not None:
            try:
                import termios as _t
                _t.tcsetattr(fd, _t.TCSANOW, old_tty)
            except Exception:
                pass
    if answer == 'y':
        _SANDBOX_ENABLED = True
    else:
        _SANDBOX_ENABLED = False
        # 对于 Linux root，额外将 ROOT_DIR 改为 /
        if sys_type_local == "Linux/macOS" and is_root:
            ROOT_DIR = "/"
            log_info("沙箱禁用，ROOT_DIR 已改为 /", str(uuid.uuid4()))
    
    # 将用户选择写入配置文件
    try:
        with open(_SANDBOX_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("true" if _SANDBOX_ENABLED else "false")
        if sys_type in ["Linux/macOS", "macOS", "Termux", "SpecialLinux"]:
            os.chmod(_SANDBOX_CONFIG_PATH, 0o644)
        log_info(f"沙箱配置已保存：{_SANDBOX_CONFIG_PATH} -> {_SANDBOX_ENABLED}", str(uuid.uuid4()))
    except Exception as e:
        log_error(f"沙箱配置保存失败：{str(e)}", str(uuid.uuid4()))

def check_admin_permission() -> None:
    global user_info
    
    current_lang = get_current_lang()
    try:
        is_admin = False
        # 强制实时检测 root 身份（禁用缓存）
        if sys.platform.startswith("linux") or sys.platform == "darwin" or "termux" in sys.prefix.lower():
            # 直接调用 os.geteuid()，不依赖任何缓存
            is_admin = os.geteuid() == 0
        elif sys.platform.startswith("win32"):
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        
        # 强制覆盖 user_info，不保留旧缓存
        user_info["is_admin"] = is_admin
        user_info["permission_flag"] = "#" if is_admin else "$"
        user_info["name"] = "root" if is_admin else (os.getlogin() if hasattr(os, "getlogin") else os.getenv("USER", "default"))
        
    except Exception as e:
        print(Fore.RED + f"权限检测失败：{str(e)}" + Style.RESET_ALL)
        user_info["is_admin"] = False
        user_info["permission_flag"] = "$"




def init_tool_dirs() -> bool:
    _sync_globals_to_ctx()
    from core.bootstrap import init_tool_dirs as _itd
    from core.context import get_ctx
    return _itd(get_ctx())

def load_user_config() -> None:
    _sync_globals_to_ctx()
    from core.bootstrap import load_user_config as _luc
    from core.context import get_ctx
    _luc(get_ctx())

def generate_salt() -> str:
    """生成16字节安全盐值（内置os库）"""
    return os.urandom(16).hex()

def hash_password(password: str, salt: str) -> str:
    """替换为内置库argon2id加密"""
    return argon2id_hash(password, salt)

def verify_password(input_password: str, stored_hash: str) -> bool:
    """替换为内置库argon2id验证"""
    return argon2id_verify(input_password, stored_hash)





def get_pwd_current_lang() -> str:
    """独立语言检测（不依赖JSON，兼容原有语言文件）"""
    try:
        lang_path = os.path.join(USER_HOME_DIR,  ".config", "onyx", "language")
        if os.path.exists(lang_path):
            with open(lang_path, "r", encoding="utf-8-sig") as f:
                lang = f.read().strip().lower()
            return lang if lang in ["chinese", "english"] else "chinese"
        return "chinese"
    except:
        return "chinese"


def init_admin_password() -> bool:
    _sync_globals_to_ctx()
    from core.bootstrap import init_admin_password as _iap
    from core.context import get_ctx
    return _iap(get_ctx())

def verify_admin_password() -> bool:
    _sync_globals_to_ctx()
    from core.bootstrap import verify_admin_password as _vap
    from core.context import get_ctx
    return _vap(get_ctx())

def run_cmd_sync(cmd: str, request_id: str, is_tool: bool = False, tool_perm: int = 3,
                  passthrough: bool = False) -> int:
    global _LAST_EXIT_CODE
    rc = _run_cmd_sync(
        cmd=cmd,
        request_id=request_id,
        is_tool=is_tool,
        tool_perm=tool_perm,
        sys_type=sys_type,
        check_tool_permission_func=check_tool_permission,
        user_mode=user_mode,
        log_info_func=log_info,
        log_error_func=log_error,
        AI_TOOL_OUTPUT_CACHE=AI_TOOL_OUTPUT_CACHE,
        is_interactive_command_func=is_interactive_command,
        user_interactive_cmds=load_user_interactive_cmds(),
        passthrough=passthrough
    )
    if rc is not None:
        _LAST_EXIT_CODE = rc
    return rc or 0

def submit_cmd_async(cmd: str, request_id: str, is_tool: bool = False, tool_perm: int = 3) -> bool:
    from lib.terminal.exe import submit_cmd_async as _submit_cmd_async
    return _submit_cmd_async(
        cmd=cmd,
        request_id=request_id,
        is_tool=is_tool,
        tool_perm=tool_perm,
        sys_type=sys_type,
        executor=executor,
        PROCESS_LOCK=PROCESS_LOCK,
        CURRENT_PROCESSES=CURRENT_PROCESSES,
        check_tool_permission_func=check_tool_permission,
        user_mode=user_mode,
        log_info_func=log_info,
        log_error_func=log_error,
        is_interactive_command_func=is_interactive_command
    )







# -------------------------- 工具权限与配置管理（第二级优先级） --------------------------
def check_tool_permission(required_perm: int) -> bool:
    from core.security import check_tool_permission as _ctp
    from core.context import get_ctx
    return _ctp(get_ctx(), required_perm)


def get_tool_permission(tool_dir: str) -> int:
    from core.security import get_tool_permission as _gtp
    from core.context import get_ctx
    return _gtp(get_ctx(), tool_dir)


def set_tool_permission(tool_dir: str, perm: int, request_id: str) -> bool:
    from core.security import set_tool_permission as _stp
    from core.context import get_ctx
    return _stp(get_ctx(), tool_dir, perm, request_id)

def load_tool_config(tool_dir: str) -> Dict[str, str]:
    from core.tool_registry import load_tool_config as _ltc
    from core.context import get_ctx
    return _ltc(get_ctx(), tool_dir)

def set_tool_config(tool_dir: str, config_key: str, value: str, request_id: str) -> bool:
    from core.tool_registry import set_tool_config as _stc
    from core.context import get_ctx
    return _stc(get_ctx(), tool_dir, config_key, value, request_id)


# -------------------------- 工具查找与执行（第二级优先级） --------------------------
def is_cli_tool(tool_config: Dict[str, str]) -> bool:
    from core.tool_registry import is_cli_tool
    return is_cli_tool(tool_config)
    


def build_tool_index(request_id: str, batch_size: int = 10, sleep_interval: float = 0.2) -> None:
    from core.tool_registry import build_tool_index as _bti
    from core.context import get_ctx
    _bti(get_ctx(), request_id)


def find_tool(tool_name: str, request_id: str) -> Optional[ToolInfo]:
    from core.tool_registry import find_tool as _ft
    from core.context import get_ctx
    return _ft(get_ctx(), tool_name, request_id)

    
def find_similar_tools(wrong_cmd: str) -> List[Tuple[str, str]]:
    from core.tool_registry import find_similar_tools as _fst
    from core.context import get_ctx
    return _fst(get_ctx(), wrong_cmd)

def find_similar_cmds(wrong_cmd: str) -> List[str]:
    from core.tool_registry import find_similar_cmds as _fsc
    from core.context import get_ctx
    return _fsc(get_ctx(), wrong_cmd)
    
    
def execute_tool(tool_info: ToolInfo, args: List[str], request_id: str) -> None:
    """
    执行工具：严格按“先真实路径→再虚拟路径→保留原始”逻辑处理参数，非路径参数不解析
    强制使用同步执行，确保输出能被捕获
    """
    # 获取当前语言配置（从全局配置读取）
    current_lang = global_config["display_info"]["language"]["current"]
    # 中英文提示映射表
    lang_msgs = {
        "chinese": {
            "sandbox_block_tool": "沙箱拦截：工具路径不在允许范围内 → {}",
            "process_limit": "进程数超过限制（最大{}个）"
        },
        "english": {
            "sandbox_block_tool": "Sandbox blocked: Tool path not in allowed range → {}",
            "process_limit": "Process count exceeds limit (max {})"
        }
    }
    msg = lang_msgs.get(current_lang, lang_msgs["chinese"])

    # 1. 沙箱路径校验：确保工具本身在虚拟根目录内
    tool_phys_path = os.path.abspath(tool_info.path)
    root = os.path.abspath(ROOT_DIR)
    path_allowed = tool_phys_path == root or tool_phys_path.startswith(root + os.sep)
    if not path_allowed:
        err_msg = msg["sandbox_block_tool"].format(tool_info.path)
        print(Fore.RED + err_msg + Style.RESET_ALL)
        log_error(err_msg, request_id)
        security_log(err_msg, "PATH_BLOCK", request_id)
        return

    # 2. 工具权限校验：检查当前模式是否有权限调用
    perm_check = check_tool_permission(tool_info.tool_perm)
    if not perm_check:
        return

    # 3. 核心参数处理：按规则验证每个参数，非路径参数保留原始值
    resolved_args = []
    for arg in args:
        # 调用参数验证函数：仅解析真实存在的路径，否则保留原始参数
        processed_arg = validate_param_path(arg, request_id)
        
        # 对已确认是路径的参数（真实存在），二次做沙箱校验
        if not processed_arg.startswith('-') and os.path.exists(processed_arg):
            if check_sandbox_path(processed_arg, request_id):
                resolved_args.append(processed_arg)
            else:
                # 沙箱拦截：回退为原始参数，避免非法路径执行
                resolved_args.append(arg)
                log_warning(f"参数{arg}对应路径触发沙箱拦截，回退原始值" if current_lang == "chinese" else f"Path corresponding to parameter {arg} triggered sandbox block, fallback to original value", request_id)
        else:
            # 非路径参数（选项/URL/字符串值等）：直接保留处理后的原始值
            resolved_args.append(processed_arg)

    # 4. 构建工具执行命令：从 executable_config 读取系统对应的启动命令
    global executable_config
    cmd_args = shlex.join(resolved_args) if resolved_args else ""  # 处理含空格的参数
    
    # 区分Python脚本与其他可执行文件
    if tool_info.path.endswith((".py", ".pyc")):
        full_cmd = f'{PYTHON_EXE} "{tool_info.path}" {cmd_args}'
    else:
        # 按系统类型获取启动命令（如Windows用start，Linux用./）
        launch_cmd = executable_config["launch_cmd"][sys_type]
        if sys_type == "Windows":
            full_cmd = f'{launch_cmd}"{tool_info.path}" {cmd_args}'
        else:
            full_cmd = f'{launch_cmd} {tool_info.path} {cmd_args}'

    # 5. 进程数限制校验：不超过沙箱配置的最大进程数
    with process_lock:
        current_processes = len(CURRENT_PROCESSES)
        max_processes = SANDBOX_CONFIG["max_process_count"]
        if current_processes >= max_processes:
            err_msg = msg["process_limit"].format(max_processes)
            print(Fore.RED + err_msg + Style.RESET_ALL)
            log_error(err_msg, request_id)
            return

    # 6. 同步执行工具命令（强制同步执行，带工具权限标识）
    # 关键修改：使用同步执行，确保输出被捕获
    run_cmd_sync(full_cmd, request_id, is_tool=True, tool_perm=tool_info.tool_perm)

    
# -------------------------- 沙箱安全 --------------------------

# 预编译正则：高危命令匹配（避免每条命令重新编译）
_RE_MULTI_SPACE = re.compile(r'\s+')
_RE_OPT_SLASH = re.compile(r'(\s*-\w+)\s*(\/)')
_RE_OPT_STAR = re.compile(r'(\s*-\w+)\s*(\*)')
# 新增：cmd-flag 之间缺空格（rm-rf → rm -rf）
_RE_CMD_FLAG_GAP = re.compile(r'(\w)(?=-[a-zA-Z0-9])')
# 新增：token/path 之间缺空格（fdisk/dev/sda → fdisk /dev/sda）
_RE_TOKEN_PATH_START = re.compile(r'(?<=^)([a-zA-Z0-9_.]+)(?=/)')
_RE_TOKEN_PATH_AFTER_SP = re.compile(r'(?<=\s)([a-zA-Z0-9_.]+)(?=/)')

def check_blocked_cmd(cmd: str, request_id: str) -> Tuple[bool, bool]:
    from core.security import check_blocked_cmd as _cbc
    from core.context import get_ctx
    return _cbc(get_ctx(), cmd, request_id)


#改了三遍 手动哪吒  
def check_sandbox_path(path: str, request_id: str) -> bool:
    from core.security import check_sandbox_path as _csp
    from core.context import get_ctx
    return _csp(get_ctx(), path, request_id)


def kill_stale_processes() -> None:
    if not SANDBOX_CONFIG["enable"]:
        return
    
    # 仅导入Python接口，不接触C库
    from lib.process_control import clear_stale_processes, get_running_processes
    
    try:
        # 调用process_control清理僵尸进程（仅Python逻辑）
        stale_count = clear_stale_processes()
        log_info(f"清理僵尸进程完成：共{stale_count}个", str(uuid.uuid4()))
        
        # 同步CURRENT_PROCESSES列表（保持接口兼容）
        global CURRENT_PROCESSES
        CURRENT_PROCESSES = get_running_processes()
    except Exception as e:
        log_error(f"清理僵尸进程失败：{str(e)}", str(uuid.uuid4()))




def init_process_control_module(request_id: str) -> None:
    """初始化进程管理模块（仅调用Python接口，不接触C库）"""
    global PROCESS_LOCK
    try:
        # 仅导入Python接口，不导入C库相关变量
        from lib.process_control import init_process_control
        
        # 初始化进程锁（兜底逻辑）
        if PROCESS_LOCK is None:
            import threading
            PROCESS_LOCK = threading.Lock()
        
        # 校验依赖变量
        if not CACHE_DIR:
            raise ValueError("CACHE_DIR 未初始化")
        if not ROOT_DIR:
            raise ValueError("ROOT_DIR 未初始化")
        if not USER_HOME_DIR:
            raise ValueError("USER_HOME_DIR 未初始化")
        if "max_process_count" not in SANDBOX_CONFIG:
            raise ValueError("SANDBOX_CONFIG 中缺少 max_process_count 配置")
        
        # 构建进程缓存路径
        process_cache_path = os.path.join(CACHE_DIR, "process_cache.msgpack")
        
        # 调用process_control.py初始化（强制禁用C库）
        init_process_control(
            root_dir=ROOT_DIR,
            user_home_dir=USER_HOME_DIR,
            process_cache_path=process_cache_path,
            max_process_count=SANDBOX_CONFIG["max_process_count"],
            process_lock=PROCESS_LOCK,
            cache_ttl=3600,
            enable_c_lib=False  # 彻底禁用C库交互
        )
        
        log_info("进程管理模块初始化完成（使用Python原生逻辑）", request_id)
    except Exception as e:
        # 异常兜底：确保进程锁可用
        if PROCESS_LOCK is None:
            import threading
            PROCESS_LOCK = threading.Lock()
        log_error(f"进程管理模块初始化失败：{str(e)}", request_id)
        log_info("进程管理将使用Python原生逻辑运行", request_id)


        
        
# ================================================================================

def get_process_risk_level(pid: int, cmd: str) -> Tuple[int, str]:
    cmd_lower = cmd.lower()
    risk_map = SANDBOX_CONFIG["guard_mode"]["risk_level_map"]
    if any(keyword in cmd_lower for keyword in ["rm -rf /", "format", "mkfs", "fdisk", "rm -rf /home", "rm -rf /etc"]):
        return 5, risk_map["5"]
    elif any(keyword in cmd_lower for keyword in ["rm -rf", "cp -r /", "hydra", "hashcat", "sqlmap", "msfconsole"]):
        return 4, risk_map["4"]
    elif any(keyword in cmd_lower for keyword in ["nmap", "arp-scan", "wget", "curl", "echo >", "sed -i"]):
        return 3, risk_map["3"]
    elif any(keyword in cmd_lower for keyword in ["grep -r", "find /", "cat /", "ls -lR"]):
        return 2, risk_map["2"]
    else:
        return 1, risk_map["1"]

# -------------------------- 工具查找辅助函数 --------------------------
# _find_tool_in_dir / _find_tool_entry / _is_cli_tool / _get_tool_type / _get_tool_permission
# → 已迁移至 core/tool_registry.py
from core.tool_registry import (
    _find_tool_entry,
    get_tool_permission_from_dir as _get_tool_permission,
)
from core.tool_registry import _get_tool_type

# _is_valid_tool_path (内联于 core/tool_registry)
def _is_valid_tool_path(path: str, no_level_limit: bool) -> bool:
    if no_level_limit:
        return True
    rel_path = os.path.relpath(path, TOOL_MAIN_DIR)
    path_depth = len([p for p in rel_path.split(os.sep) if p.strip()])
    return path_depth == 2

def _is_cli_tool(tool_dir: str) -> bool:
    from core.tool_registry import load_tool_config as _ltc
    from core.context import get_ctx
    return is_cli_tool(_ltc(get_ctx(), tool_dir))


# -------------------------- 命令解析与执行（优先级：1.工具箱命令→2.工具→3.系统命令） --------------------------


def replace_virtual_path_in_cmd(cmd: str, request_id: str) -> str:
    """使用 parse 模块的安全路径解析"""
    return resolve_paths_in_multiline_text(cmd, resolve_path)

# Onyx.py - 修改 parse_and_execute 函数

# 模块级计数器，替代 uuid.uuid4() 减少每条命令的临时对象分配
_refresh_request_counter = 0

# 上一条命令的退出码（0=成功，非0=失败），用于 prompt 颜色切换
_LAST_EXIT_CODE: int = 0


def parse_and_execute(cmd: str, is_recursive: bool = False, is_ai_triggered: bool = False) -> None:
    """命令解析与执行接口 — 从 AppContext 提取所有参数，消除上帝参数"""
    from core.context import get_ctx
    ctx = get_ctx()
    global _LAST_EXIT_CODE, _refresh_request_counter
    
    _refresh_request_counter += 1
    request_id_local = str(_refresh_request_counter)
    
    # 异步刷新缓存
    refresh_tool_index(request_id=request_id_local, force=False, log_info_func=log_info, log_error_func=log_error)
    refresh_system_cmds(request_id=request_id_local, force=False, log_info_func=log_info, log_error_func=log_error)
    
    effective_root = ctx.ROOT_DIR if ctx._SANDBOX_ENABLED else "/"
    
    from bin.ai_cmd import clear_ai_cmd_cache
    _parse_and_execute(
        cmd=cmd,
        is_recursive=is_recursive,
        is_ai_triggered=is_ai_triggered,
        BUILTIN_COMMANDS=ctx.BUILTIN_COMMANDS,
        ALIAS_CACHE=ctx.ALIAS_CACHE,
        CMD_MAPPING_CACHE=ctx.CMD_MAPPING_CACHE,
        TOOL_INDEX_CACHE=ctx.TOOL_INDEX_CACHE,
        current_sys_cmds=ctx.current_sys_cmds,
        sys_type=ctx.sys_type,
        user_mode=ctx.user_mode,
        global_config=ctx.global_config,
        executor=ctx.executor,
        PROCESS_LOCK=ctx.PROCESS_LOCK,
        CURRENT_PROCESSES=ctx.CURRENT_PROCESSES,
        AI_TOOL_OUTPUT_CACHE=ctx.AI_TOOL_OUTPUT_CACHE,
        USER_HOME_DIR=ctx.USER_HOME_DIR,
        ROOT_DIR=effective_root,
        TOOL_MAIN_DIR=ctx.TOOL_MAIN_DIR,
        PYTHON_EXE=ctx.PYTHON_EXE,
        executable_config=ctx.executable_config,
        SANDBOX_CONFIG=ctx.SANDBOX_CONFIG,
        DEBUG_PARSECMD_PATH=ctx.DEBUG_PARSECMD_PATH,
        DEBUG_TIMES_PATH=ctx.DEBUG_TIMES_PATH,
        PATH_INDEX_MSG_PATH=ctx.PATH_INDEX_MSG_PATH,
        DIR_CACHE_MSG_PATH=ctx.DIR_CACHE_MSG_PATH,
        CMD_MAPPING_MSG_PATH=ctx.CMD_MAPPING_MSG_PATH,
        TOOL_INDEX_MSG_PATH=ctx.TOOL_INDEX_MSG_PATH,
        get_current_lang_func=get_current_lang,
        resolve_path_func=resolve_path,
        check_sandbox_path_func=check_sandbox_path,
        validate_param_path_func=validate_param_path,
        check_tool_permission_func=check_tool_permission,
        run_cmd_sync_func=run_cmd_sync,
        execute_tool_func=execute_tool,
        replace_virtual_path_in_cmd_func=replace_virtual_path_in_cmd,
        get_virtual_path_func=get_virtual_path,
        check_blocked_cmd_func=check_blocked_cmd,
        is_interactive_command_func=is_interactive_command,
        read_config_file_func=read_config_file,
        clear_ai_cmd_cache_func=clear_ai_cmd_cache,
        build_tool_index_func=build_tool_index,
        load_cmd_mapping_cache_func=load_cmd_mapping_cache,
        log_info_func=log_info,
        log_error_func=log_error,
        log_warning_func=log_warning,
        security_log_func=security_log,
        Fore=Fore,
        Style=Style,
        username=ctx.user_info["name"]
    )
    
    





def _replace_special_cmd_placeholder(base_cmd: str, args: List[str]) -> str:
    for idx, arg in enumerate(args, 1):
        placeholder = f"{{arg{idx}}}"
        if placeholder in base_cmd:
            base_cmd = base_cmd.replace(placeholder, arg)
        else:
            base_cmd += f" {arg}"
    return base_cmd

def _show_cmd_error_hint(cmd: str, request_id: str, reason: str) -> None:
    err_msg = f"[沙箱拦截] {reason}：{cmd}"
    print(Fore.RED + err_msg + Style.RESET_ALL)
    log_error(err_msg, request_id)
    security_log(err_msg, "CMD_BLOCK", request_id)
    
    # 显示相似工具/命令提示（从config读取提示文案）
    similar_tools = find_similar_tools(cmd.split()[0])
    similar_cmds = find_similar_cmds(cmd.split()[0])
    error_hint = global_config["display_info"]["error_hint"]
    
    print(Fore.YELLOW + error_hint["no_command"].format(cmd=cmd) + Style.RESET_ALL)
    if similar_tools:
        tool_list = ", ".join([f"{name}（{type}）" for name, type in similar_tools])
        print(f"  {error_hint['tool_suggest'].format(cmd=cmd.split()[0])} {tool_list}")
    if similar_cmds:
        print(f"  {error_hint['system_cmd_suggest']} {', '.join(similar_cmds)}")
        
        
        
#-----------------------------Linux系统命令-------------------------------------------

# -------------------------- 自定义Linux命令实现 ------------------------------------------------------


# -------------------------- 工具箱命令（第一级优先级，最高） ---------------------------------------------------------
#已经在头部import  呵呵，循环导入了，我改回去



#Oh My God 一直在维护的核心模块，重点关注谢谢   核心模块啊！！！

def handle_cd(cmd_parts: List[str], request_id: str) -> None:
    from core.handlers.cd_handler import handle_cd as _hcd
    from core.context import get_ctx
    _hcd(get_ctx(), cmd_parts, request_id)

# ====================== mktool 命令处理函数 =======================
# ====================== mktool 命令处理函数 =======================
def handle_mktool(cmd_parts: List[str], request_id: str) -> None:
    """
    工具箱命令：mktool -n <工具名> -l <语言>
    功能：创建支持自动推导根目录+双语切换的工具
    配置依赖：onyx/etc/mktool/language.json（语言扩展配置）
    模板依赖：ROOT_DIR/onyx/etc/mktool/formwork.*（语言模板文件）
    """
    # 从bin.mktool_cmd导入核心逻辑函数
    from bin.mktool_cmd import handle_mktool_core
    
    # 注入所有依赖参数（包含Onyx.py全局变量和工具函数）
    handle_mktool_core(
        cmd_parts=cmd_parts,
        request_id=request_id,
        ROOT_DIR=ROOT_DIR,
        USER_HOME_DIR=USER_HOME_DIR,
        SYS_SPECIFIC=global_config["sys_specific"],
        check_sandbox_path=check_sandbox_path,
        get_virtual_path=get_virtual_path,  # 新增：传递虚拟路径转换函数
        log_info=log_info,
        log_error=log_error,
        Fore=Fore,
        Style=Style
    )


def get_ai_tool_output(request_id: str) -> str:
    """
    获取AI工具命令的输出结果
    :param request_id: 请求ID
    :return: 工具输出内容，如果没有则返回空字符串
    """
    return AI_TOOL_OUTPUT_CACHE.get(request_id, "")
    

def handle_ai(cmd_parts: List[str], request_id: str) -> None:
    """
    AI命令处理包装器（依赖注入版）
    - 带子命令标志 (-mcp, -c, -tui, -key) → 一次性调用 bin.ai_cmd.handle_ai
    - 纯对话 → 进入 bin.ai_interactive.ai_interactive_session 持久 REPL
    """


    # ── 首次使用：检查并引导配置 key.conf ──
    from bin.ai_cmd import load_key_conf, _setup_key_conf_interactive
    _conf = load_key_conf()
    if not _conf or not _conf.get("api_key"):
        _setup_key_conf_interactive(get_current_lang())
        _conf = load_key_conf()
        if not _conf or not _conf.get("api_key"):
            return  # 用户取消配置

    # 仅裸 ai（cmd_parts == ["ai"]）进入持久 REPL，其他走原有一次性逻辑
    is_bare_ai = len(cmd_parts) == 1

    if not is_bare_ai:
        from bin.ai_cmd import handle_ai as _handle_ai
        _handle_ai(
            cmd_parts=cmd_parts,
            request_id=request_id,
            onyx_module=sys.modules[__name__],
            user_home_dir=USER_HOME_DIR,
            global_config=global_config,
            user_info=user_info,
            user_mode=user_mode,
            AI_TOOL_OUTPUT_CACHE=AI_TOOL_OUTPUT_CACHE,
            BUILTIN_COMMANDS=BUILTIN_COMMANDS,
            CMD_MAPPING_CACHE=CMD_MAPPING_CACHE,
            current_sys_cmds=current_sys_cmds,
            sys_type=sys_type,
            get_cached_cmd=get_cached_cmd,
            parse_and_execute=parse_and_execute,
            get_current_lang_func=get_current_lang,
            log_info=log_info,
            log_error=log_error,
            log_warning=log_warning,
            security_log=security_log,
        )
    else:
        from bin.ai_interactive import ai_interactive_session
        ai_interactive_session(
            user_home_dir=USER_HOME_DIR,
            onyx_module=sys.modules[__name__],
            global_config=global_config,
            user_info=user_info,
            user_mode=user_mode,
            parse_and_execute=parse_and_execute,
            AI_TOOL_OUTPUT_CACHE=AI_TOOL_OUTPUT_CACHE,
            BUILTIN_COMMANDS=BUILTIN_COMMANDS,
            CMD_MAPPING_CACHE=CMD_MAPPING_CACHE,
            current_sys_cmds=current_sys_cmds,
            sys_type=sys_type,
            get_cached_cmd=get_cached_cmd,
            get_current_lang_func=get_current_lang,
            log_info=log_info,
            log_error=log_error,
            log_warning=log_warning,
            security_log=security_log,
        )



def handle_import(cmd_parts: List[str], request_id: str) -> None:
    """
    工具箱命令：import <系统命令名>
    功能：
    1. 将命令添加到用户扩展映射文件（USER_HOME_DIR/.mapping.json）
    2. 与系统命令映射文件同级，仅作用户层面扩展，不修改系统映射
    3. 自动化生成扩展文件（不存在时自动创建）
    4. 缓存中已存在该命令（任何形式）则禁止添加
    """
    # 从bin.import_cmd导入核心逻辑函数（模块名已改为import_cmd）
    from bin.import_cmd import handle_import_core
    
    # 注入所有依赖参数（包含颜色输出依赖Fore/Style）
    handle_import_core(
        cmd_parts=cmd_parts,
        request_id=request_id,
        USER_HOME_DIR=USER_HOME_DIR,
        current_sys_cmds=current_sys_cmds,
        sys_type=sys_type,
        BUILTIN_COMMANDS=BUILTIN_COMMANDS,
        CMD_MAPPING_CACHE=CMD_MAPPING_CACHE,
        TOOL_INDEX_CACHE=TOOL_INDEX_CACHE,
        build_cmd_mapping_cache=build_cmd_mapping_cache,
        log_info=log_info,
        log_error=log_error,
        Fore=Fore,  # 传递颜色输出类
        Style=Style  # 传递颜色重置类
    )



def handle_set_adv_pwd(cmd_parts: List[str], request_id: str) -> None:
    from core.handlers.adv_pwd_handler import handle_set_adv_pwd as _hsp
    from core.context import get_ctx
    _hsp(get_ctx(), cmd_parts, request_id)


def handle_unalias(cmd_parts: List[str], request_id: str) -> None:
    from bin.alias_cmd import handle_unalias_core
    handle_unalias_core(
        cmd_parts=cmd_parts,
        request_id=request_id,
        ALIAS_CACHE=ALIAS_CACHE,
        save_user_config=save_user_config,
        log_info=log_info,
        log_error=log_error,
        Fore=Fore,
        Style=Style
    )

    save_user_config()



def handle_autocmd(cmd_parts: List[str], request_id: str) -> None:
    """autocmd命令入口（对外接口）"""
    from bin.autocmd_cmd import handle_autocmd_core, load_autocmd_core, save_autocmd_core
    handle_autocmd_core(
        cmd_parts=cmd_parts,
        request_id=request_id,
        USER_HOME_DIR=USER_HOME_DIR,
        sys_type=sys_type,
        log_info=log_info,
        log_error=log_error,
        Fore=Fore,
        Style=Style
    )







def handle_clear(cmd_parts: List[str], request_id: str) -> None:
    """最简洁的跨平台清屏实现"""
    # ANSI 转义序列：清屏 + 光标归位
    print('\033[2J\033[H', end='')
    
    
    print('\033[3J\033[2J\033[H', end='')
    
    log_info("Clear screen executed", request_id)

def handle_exit(cmd_parts: List[str], request_id: str) -> None:
    log_info("程序退出", request_id)

    if log_file_handler:
        log_file_handler.close()
    if executor:
        executor.shutdown()
        
        
    #os.system("cls" if sys_type == "Windows" else "clear")
    graceful_shutdown(request_id)
    sys.exit(0)






































# Onyx.py 中的 handle_sado 函数（约第 1540 行）
def handle_sado(cmd_parts: List[str], request_id: str) -> None:
    """sado 命令入口：调用独立模块核心逻辑"""
    from bin.sado_cmd import handle_sado_core
    handle_sado_core(
        cmd_parts=cmd_parts,
        request_id=request_id,
        user_mode=user_mode,
        global_config=global_config,
        SADO_CONFIG=SADO_CONFIG,
        SADO_CONFIG_PATH=SADO_CONFIG_PATH,
        user_info=user_info,
        OS_OR_TBS=OS_OR_TBS,
        sys_type=sys_type,
        parse_and_execute=parse_and_execute,
        alias_cache=ALIAS_CACHE,           # 新增：传递别名缓存
        log_info=log_info,
        log_error=log_error,
        get_current_lang=get_current_lang,
        Fore=Fore,
        Style=Style
    )

def handle_nanosado(cmd_parts: List[str], request_id: str) -> None:
    """nanosado 命令入口：调用独立模块核心逻辑"""
    from bin.nanosado_cmd import handle_nanosado_core
    handle_nanosado_core(
        cmd_parts=cmd_parts,
        request_id=request_id,
        user_mode=user_mode,
        OS_OR_TBS=OS_OR_TBS,
        sys_type=sys_type,
        SADO_CONFIG=SADO_CONFIG,
        SADO_CONFIG_PATH=SADO_CONFIG_PATH,
        user_info=user_info,
        get_current_lang=get_current_lang,
        log_info=log_info,
        log_error=log_error,
        Fore=Fore,
        Style=Style
    )

# 在 Onyx.py 中，找到 handle_activite 函数（大约第 1786 行），修改为：

def handle_activite(cmd_parts: List[str], request_id: str) -> None:
    """activite 命令入口：调用独立模块核心逻辑"""
    handle_activite_core(
        cmd_parts=cmd_parts,
        request_id=request_id,
        user_mode=user_mode,
        user_info=user_info,           # 新增：传递 user_info
        global_config=global_config,
        ROOT_DIR=ROOT_DIR,
        SANDBOX_CONFIG=SANDBOX_CONFIG,
        get_virtual_path=get_virtual_path,
        set_tool_permission=set_tool_permission,
        find_tool=find_tool,
        verify_admin_password=verify_admin_password,
        ADMIN_PASSWORD_PATH=ADMIN_PASSWORD_PATH,
        get_current_lang=get_current_lang,
        log_info=log_info,
        log_error=log_error,
        Fore=Fore,
        Style=Style
    )

def handle_export(cmd_parts: List[str], request_id: str) -> None:
    """export命令入口：调用独立模块核心逻辑"""
    from bin.export_cmd import handle_export_core
    handle_export_core(
        cmd_parts=cmd_parts,
        request_id=request_id,
        log_info=log_info,
        log_error=log_error,
        Fore=Fore,
        Style=Style
    )





def _lazy_source(cmd_parts: List[str], request_id: str) -> None:
    """延迟导入 source 命令处理器（跨 shell 支持）"""
    from bin.source_cmd import handle_source
    return handle_source(cmd_parts, request_id)


def _optimize_command_batch(commands: List[str]) -> List[str]:
    """
    批量优化：将连续的简单命令合并为一行执行
    减少 parse_and_execute 调用次数，大幅提升性能
    """
    # 简单命令判断函数（内联加速）
    def is_simple_cmd(cmd: str) -> bool:
        cmd_stripped = cmd.strip()
        # 排除复杂操作符（最快路径检查）
        if any(c in cmd_stripped for c in '|&<>;'):
            return False
        # 排除控制结构
        first_word = cmd_stripped.split()[0] if cmd_stripped else ''
        if first_word in ('if', 'for', 'while', 'case', 'function', '{', '(', 'then', 'elif', 'else', 'fi', 'do', 'done'):
            return False
        # 排除交互式命令
        if first_word in ('read', 'select', 'vim', 'nano', 'less', 'more', 'top', 'htop'):
            return False
        return True
    
    # 如果命令数量少，不优化（避免额外开销）
    if len(commands) <= 3:
        return commands
    
    # 合并简单命令
    optimized = []
    batch = []
    
    for cmd in commands:
        if is_simple_cmd(cmd):
            batch.append(cmd)
            # 批处理大小限制（避免单行过长）
            if len(batch) >= 10:
                optimized.append('; '.join(batch))
                batch = []
        else:
            if batch:
                optimized.append('; '.join(batch))
                batch = []
            optimized.append(cmd)
    
    if batch:
        optimized.append('; '.join(batch))
    
    # 返回优化后的命令列表
    return optimized


def _set_script_args(args: List[str], request_id: str) -> Dict[str, str]:
    """
    设置脚本参数变量 $1, $2, $3...
    返回原始环境变量备份
    """
    original_env = {}
    
    # 备份被覆盖的变量
    for i in range(1, 10):
        var_name = f"_{i}"
        if var_name in os.environ:
            original_env[var_name] = os.environ[var_name]
    
    # 备份特殊变量
    for var in ("_#", "_0", "_*", "_@"):
        if var in os.environ:
            original_env[var] = os.environ[var]
    
    # 设置新参数
    for i, arg in enumerate(args, 1):
        os.environ[f"_{i}"] = arg
    
    os.environ["_#"] = str(len(args))
    os.environ["_0"] = "source"
    os.environ["_*"] = " ".join(args)
    os.environ["_@"] = " ".join(args)
    
    log_info(f"source: set script arguments: {len(args)} args", request_id)
    return original_env


def _clear_script_args(original_env: Dict[str, str], request_id: str) -> None:
    """清理脚本参数变量，恢复原始环境"""
    # 删除我们设置的变量
    keys_to_remove = [k for k in os.environ if k.startswith('_') and k not in original_env]
    for key in keys_to_remove:
        del os.environ[key]
    
    # 恢复原始变量
    for key, value in original_env.items():
        os.environ[key] = value
    
    log_info(f"source: cleared script arguments, restored {len(original_env)} variables", request_id)
        
        
def handle_switch_prompt(cmd_parts: List[str], request_id: str) -> None:
    """switch-prompt命令入口（对外接口，调用独立模块核心逻辑）"""
    from bin.switch_prompt_cmd import handle_switch_prompt_core
    handle_switch_prompt_core(
        cmd_parts=cmd_parts,
        request_id=request_id,
        USER_HOME_DIR=USER_HOME_DIR,
        sys_type=sys_type,
        OS_OR_TBS=OS_OR_TBS,
        user_info=user_info,
        user_mode=user_mode,
        global_config=global_config,
        log_info=log_info,
        log_error=log_error,
        Fore=Fore,
        Style=Style
    )




        
# -------------------------- 缺失辅助函数 --------------------------
def save_history_incremental() -> None:
    """增量保存命令历史（间隔30秒）"""
    global last_history_save_time
    current_time = time.time()
    if current_time - last_history_save_time >= history_save_interval and global_config["user_config"]["save_history"]:
        try:
            with open(USER_HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(COMMAND_HISTORY, f, ensure_ascii=False, indent=2)
            last_history_save_time = current_time
            log_info(f"命令历史保存：{len(COMMAND_HISTORY)}条", user_info["session_id"])
        except Exception as e:
            log_error(f"命令历史保存失败：{str(e)}", user_info["session_id"])

def save_user_config() -> None:
    """保存用户配置（别名）"""
    if not global_config["user_config"]["auto_load_user_config"]:
        return
    
    try:
        user_config = {"alias": ALIAS_CACHE}
        with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(user_config, f, ensure_ascii=False, indent=2)
        log_info("用户配置（别名）保存完成", user_info["session_id"])
    except Exception as e:
        log_error(f"用户配置保存失败：{str(e)}", user_info["session_id"])
        
        




def load_autocmd() -> None:
    """加载开机自启命令（程序启动时调用）"""
    global AUTO_CMDS
    if os.path.exists(AUTO_CMD_PATH):
        try:
            with open(AUTO_CMD_PATH, "r", encoding="utf-8") as f:
                AUTO_CMDS = json.load(f)
                # 校验格式（确保包含id、cmd、create_time）
                AUTO_CMDS = [cmd for cmd in AUTO_CMDS if all(k in cmd for k in ["id", "cmd", "create_time"])]
            log_info(f"加载开机自启命令：共{len(AUTO_CMDS)}条", user_info["session_id"])
        except Exception as e:
            log_error(f"加载自启命令失败：{str(e)}", user_info["session_id"])
            AUTO_CMDS = []
    else:
        log_info("开机自启命令文件不存在，已初始化空列表", user_info["session_id"])

def save_autocmd() -> None:
    """保存开机自启命令（添加/删除时调用）"""
    try:
        with open(AUTO_CMD_PATH, "w", encoding="utf-8") as f:
            json.dump(AUTO_CMDS, f, ensure_ascii=False, indent=2)
        # Linux类系统设置文件权限（仅所有者可读写）
        if sys_type in ["Linux/macOS", "macOS", "Termux", "SpecialLinux"]:
            os.chmod(AUTO_CMD_PATH, 0o600)
        log_info(f"保存开机自启命令：共{len(AUTO_CMDS)}条", user_info["session_id"])
    except Exception as e:
        log_error(f"保存自启命令失败：{str(e)}", user_info["session_id"])
        print(Fore.RED + f"保存开机自启命令失败：{str(e)}" + Style.RESET_ALL)







check_OS_TBS()

# 延迟导入占位：这些模块在首次使用时才加载，避免 import Onyx 时引入 prompt_toolkit 等重依赖
DEBUG_TIMES_PATH = ""
DEBUG_PARSECMD_PATH = ""

def _lazy_manage(cmd_parts: List[str], request_id: str) -> None:
    """延迟导入 manage 命令处理器（避免启动时加载 prompt_toolkit）"""
    from bin.manage import handle_manage
    return handle_manage(cmd_parts, request_id)

def _lazy_help(cmd_parts: List[str], request_id: str) -> None:
    """延迟导入 help 命令处理器"""
    from bin.help.help import main as help_handler
    return help_handler(cmd_parts, request_id)

def _lazy_which(cmd_parts: List[str], request_id: str) -> None:
    """延迟导入 which 命令处理器"""
    from bin.which_cmd import handle_which
    return handle_which(cmd_parts, request_id)

# -------------------------- 工具箱命令映射（第一级优先级） --------------------------



BUILTIN_COMMANDS: Dict[str, Callable[[List[str], str], None]] = {
    # 基础TBS命令
    "clear": handle_clear,
    "exit": handle_exit,
    "refresh": lambda cmd_parts, req_id: executor.submit(build_tool_index, req_id) if executor else None,
    
    "activite": handle_activite,
    "manage": _lazy_manage,
    "import": handle_import,
    "switch-prompt": handle_switch_prompt,
    "ai": handle_ai,
    "set-adv-pwd": handle_set_adv_pwd,
    "autocmd": handle_autocmd,
    "help": _lazy_help,
    "mktool": handle_mktool,

    "unalias": handle_unalias,
    "cd": handle_cd,
    "source": _lazy_source,
    "which": _lazy_which,
    "sado": handle_sado,
    "nanosado": handle_nanosado,
    }

# -------------------------- 命令提示符生成 --------------------------
def format_virtual_path(virtual_path: str, max_len: int = 15) -> str:
    """仅缩短超长虚拟路径（>15字符），保留标准虚拟路径格式"""
    # 1. 保留核心虚拟路径（不缩短）
    if virtual_path in ["/", "~", "/Not in virtual path", "/（路径异常）"]:
        return virtual_path
    # 2. 保留程序内短虚拟路径（如 /onyx-2、~/cd.sh，不缩短）
    if len(virtual_path) <= max_len:
        return virtual_path
    
    # 3. 仅对超长虚拟路径缩短（如 ~/long/path/to/file.sh → .../to/file.sh）
    path_parts = [p for p in virtual_path.split("/") if p.strip()]
    last_part = path_parts[-1]
    # 优先保留倒数两级（确保可读性）
    if len(path_parts) >= 2:
        second_last_part = path_parts[-2]
        shortened = f".../{second_last_part}/{last_part}"
        if len(shortened) <= max_len:
            return shortened
    # 否则保留最后一级
    return f".../{last_part}"
    
    
    
# === generate_prompt 缓存：会话不变值只计算一次 ===
_CACHED_HOSTNAME: Optional[str] = None
_COLOR_STYLES = {
    "BLUE": "#4a90e2",
    "RED": "#e74c3c",
    "GREEN": "#2ecc71",
    "YELLOW": "#f1c40f",
    "RESET": "",
    "ACCENT_GREEN": "#2ecc71",
    "ACCENT_RED": "#e74c3c",
}
_COLOR_MAP = {
    "{BLUE}": Fore.BLUE,
    "{RED}": Fore.RED,
    "{GREEN}": Fore.GREEN,
    "{YELLOW}": Fore.YELLOW,
    "{RESET}": Style.RESET_ALL
}
_comp_window_cache: Optional[bool] = None
_comp_window_cache_time: float = 0
_comp_window_cache_ttl: float = 30  # 30s

# Git 状态缓存（必须在 _get_venv_git_status 之前定义）
_GIT_CACHE: Optional[str] = None
_GIT_CACHE_TIME: float = 0.0


def _get_git_branch_fast() -> str:
    """zsh 风格：直接读 .git/HEAD 文件拿分支名，不 fork 子进程。
    返回 (branch, is_detached) 或 ("", False)。"""
    try:
        head_path = os.path.join(os.getcwd(), ".git", "HEAD")
        if not os.path.isfile(head_path):
            return "", False
        with open(head_path, "r", encoding="utf-8") as f:
            head = f.read().strip()
        if head.startswith("ref: refs/heads/"):
            return head[16:], False
        elif head.startswith("ref: "):
            return head[5:], False
        else:
            # detached HEAD（显示短 hash）
            return head[:7], True
    except Exception:
        return "", False


def _get_git_dirty_fast() -> bool:
    """快速检查 git 是否有未提交变更（只抽样，不全量扫描目录）"""
    try:
        cwd = os.getcwd()
        index_path = os.path.join(cwd, ".git", "index")
        if not os.path.isfile(index_path):
            return False
        index_mtime = os.path.getmtime(index_path)
        # 只抽样 5 个非隐藏条目，避免大目录拖慢 prompt
        count = 0
        for entry in os.listdir(cwd):
            if entry.startswith(".") or count >= 5:
                continue
            try:
                if os.path.getmtime(os.path.join(cwd, entry)) > index_mtime:
                    return True
            except Exception:
                pass
            count += 1
        return False
    except Exception:
        return False


def _get_venv_git_status() -> str:
    """检测 Python venv 和 Git 状态，返回状态字符串如 '(venv) (git:main●)'。
    zsh 风格：直接读 .git 文件系统，不 fork 子进程，极快。"""
    global _GIT_CACHE, _GIT_CACHE_TIME
    try:
        parts = []
        
        # Python venv 检测
        venv = os.environ.get("VIRTUAL_ENV", "")
        if venv:
            venv_name = os.path.basename(venv)
            parts.append(f"venv:({venv_name})")
        
        # Git 检测 — 直接读 .git/HEAD（zsh 风格，不 fork）
        now = time.time()
        if _GIT_CACHE is None or (now - _GIT_CACHE_TIME) > 2:
            _GIT_CACHE = ""
            _GIT_CACHE_TIME = now
            branch, detached = _get_git_branch_fast()
            if branch:
                dirty = " ✗" if _get_git_dirty_fast() else " ✓"
                detached_mark = " ⚡" if detached else ""
                _GIT_CACHE = f"git:({branch}){detached_mark}{dirty}"
        
        if _GIT_CACHE:
            parts.append(_GIT_CACHE)
        
        return " " + " ".join(parts) if parts else ""
    except Exception:
        return ""


def generate_prompt() -> Union['FormattedText', str]:
    from core.display import generate_prompt as _gp
    from core.context import get_ctx
    return _gp(get_ctx())




        
#=====================================关机==================




def graceful_shutdown(request_id: str = "") -> None:
    from core.shutdown import graceful_shutdown as _gs
    from core.context import get_ctx
    _gs(get_ctx(), request_id or str(uuid.uuid4()))









# -------------------------- 主循环（程序入口逻辑） --------------------------

"""读取 config.json 配置，显示欢迎界面"""
def show_welcome():
     # 1. 从配置提取欢迎界面数据
     welcome_title = global_config["display_info"]["welcome_title"]
     ascii_art = '''##      ____    _   _  __     __ __   __     ##
##     / __ \\  | \\ | | \\ \\   / / \\ \\ / /     ##
##    | |  | | |  \\| |  \\ \\_/ /   \\ V /      ##
##    | |  | | | . ` |   \\   /     > <       ##
##    | |__| | | |\\  |    | |     / . \\      ##
##     \\____/  |_| \\_|    |_|    /_/ \\_\\     ##
##                                           ##'''
     default_color = global_config["display_info"]["default_color"]
     default_lang = global_config["display_info"]["language"]["default"]
     
     # 2. 颜色映射（默认颜色值 1 对应 CYAN，可扩展其他颜色）
     color_map = {
         1: Fore.CYAN,
         2: Fore.GREEN,
         3: Fore.YELLOW,
         4: Fore.RED
     }
     title_color = color_map.get(default_color, Fore.CYAN)  # 标题颜色（默认 CYAN）
     
     # 3. 清除终端屏幕（提升视觉效果）
     
     # 4. 显示 ASCII 艺术字（使用默认颜色）
     print(title_color + ascii_art + Style.RESET_ALL)
     
     # 5. 显示欢迎标题（居中对齐，增强美观度）
     terminal_width = shutil.get_terminal_size().columns
     centered_title = welcome_title.center(terminal_width)
     #print(title_color + centered_title + Style.RESET_ALL)
     
     

def resolve_alias(cmd_parts: List[str], request_id: str) -> List[str]:
    """
    解析命令中的别名（接口函数）
    将参数传递给 lib.parse_and_execute.resolve_alias
    """
    from lib.parse_and_execute import resolve_alias as _resolve_alias
    
    global ALIAS_CACHE, validate_param_path, log_info
    
    return _resolve_alias(
        cmd_parts=cmd_parts,
        request_id=request_id,
        ALIAS_CACHE=ALIAS_CACHE,
        validate_param_path_func=validate_param_path,
        log_info_func=log_info
    )




def handle_up_arrow(current_input: str) -> Tuple[str, int]:
    """处理上箭头按键：切换到上一条历史命令"""
    global CURRENT_HISTORY_INDEX, INPUT_BUFFER
    # 首次按上箭头时，保存当前输入内容
    if CURRENT_HISTORY_INDEX == -1:
        INPUT_BUFFER = current_input
    # 索引边界判断
    if CURRENT_HISTORY_INDEX < len(HISTORY_BUFFER) - 1:
        CURRENT_HISTORY_INDEX += 1
        return HISTORY_BUFFER[-(CURRENT_HISTORY_INDEX + 1)], len(HISTORY_BUFFER[-(CURRENT_HISTORY_INDEX + 1)])
    return current_input, len(current_input)

def handle_down_arrow(current_input: str) -> Tuple[str, int]:
    """处理下箭头按键：切换到下一条历史命令"""
    global CURRENT_HISTORY_INDEX, INPUT_BUFFER
    # 索引边界判断
    if CURRENT_HISTORY_INDEX >= 0:
        CURRENT_HISTORY_INDEX -= 1
        # 索引回到-1时，恢复之前的输入内容
        if CURRENT_HISTORY_INDEX == -1:
            return INPUT_BUFFER, len(INPUT_BUFFER)
        return HISTORY_BUFFER[-(CURRENT_HISTORY_INDEX + 1)], len(HISTORY_BUFFER[-(CURRENT_HISTORY_INDEX + 1)])
    return current_input, len(current_input)


def init_history_navigation() -> None:
    """初始化历史导航全局变量（仅保留当前会话命令）"""
    global HISTORY_BUFFER, CURRENT_HISTORY_INDEX, INPUT_BUFFER
    HISTORY_BUFFER = []  # 清空旧历史，不加载之前的命令
    CURRENT_HISTORY_INDEX = -1
    INPUT_BUFFER = ""





def universal_input(prompt_func: Callable[[], Union['FormattedText', str]] = generate_prompt) -> str:
    """包装 universal_input 函数，根据 _SANDBOX_ENABLED 动态传递 virtual_root"""
    from lib.terminal.input_lib import set_virtual_root as _set_virtual_root
    from lib.terminal.input_lib import universal_input as _universal_input
    effective_root = ROOT_DIR if _SANDBOX_ENABLED else "/"
    _set_virtual_root(effective_root)   # 设置虚拟根目录

    CMD_CONFIG_PATH = os.path.expanduser(os.path.join("~", ".cache", "onyx", "onyx", "command.json"))

    
    return _universal_input(
        prompt_func=prompt_func,

        sys_type=sys_type,
        alias_cache=ALIAS_CACHE,
        user_home_dir=USER_HOME_DIR,
        command_history=COMMAND_HISTORY,
        max_history_len=MAX_HISTORY_LEN,
        session_id=user_info["session_id"],
        save_history_func=save_history_incremental,
        read_config_file_func=read_config_file,
        log_info_func=log_info,
        log_error_func=log_error,
        graceful_shutdown_func=graceful_shutdown,
        Fore=Fore,
        Style=Style,
        language=global_config["display_info"]["language"]["current"],
        virtual_root=effective_root,      # 关键修改：动态根目录
        cmd_config_path=CMD_CONFIG_PATH
    )



def read_config_file(path: str, default: Any = None) -> Any:
    """读取配置文件（复用 manage.py 逻辑，避免导入冲突）"""
    config_dir = os.path.dirname(path)
    if not os.path.exists(config_dir):
        os.makedirs(config_dir, mode=0o755)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip().lower()
            if content in ["true", "false"]:
                return content == "true"
            return content
    except Exception:
        return default

def check_and_create_system_rc() -> None:
    """检查并自动创建 ROOT_DIR/etc/onyx.onyxrc"""
    rc_path = os.path.join(ROOT_DIR, "etc", "onyx.onyxrc")
    if not os.path.exists(rc_path):
        try:
            # 确保 /etc 目录存在
            etc_dir = os.path.dirname(rc_path)
            if not os.path.exists(etc_dir):
                os.makedirs(etc_dir, mode=0o755)
            
            # 创建系统级 onyxrc 文件
            with open(rc_path, "w", encoding="utf-8") as f:
                f.write("# Onyx System RC File\n")
                
            # 设置文件权限
            if sys_type in ["Linux/macOS", "macOS", "Termux", "SpecialLinux"]:
                os.chmod(rc_path, 0o644)
            
            log_info(f"创建系统级 onyxrc 文件：{rc_path}", user_info["session_id"])
        except Exception as e:
            log_error(f"创建系统级 onyxrc 文件失败：{str(e)}", user_info["session_id"])


def check_and_create_user_rc() -> None:
    """检查并自动创建 USER_HOME_DIR/.onyxrc"""
    rc_path = os.path.join(USER_HOME_DIR, ".onyxrc")
    if not os.path.exists(rc_path):
        try:
            # 创建用户级 onyxrc 文件
            with open(rc_path, "w", encoding="utf-8") as f:
                f.write("# Onyx User RC File\n")

            
            # 设置文件权限
            if sys_type in ["Linux/macOS", "macOS", "Termux", "SpecialLinux"]:
                os.chmod(rc_path, 0o600)
            
            log_info(f"创建用户级 .onyxrc 文件：{rc_path}", user_info["session_id"])
        except Exception as e:
            log_error(f"创建用户级 .onyxrc 文件失败：{str(e)}", user_info["session_id"])


def source_rc_files(request_id: str) -> None:
    """自动执行系统和用户的 onyxrc 文件"""
    current_lang = global_config["display_info"]["language"]["current"]
    lang_msgs = {
        "chinese": {
            "sys_rc_load": "加载系统级 onyxrc：{}",
            "user_rc_load": "加载用户级 onyxrc：{}",
            "sys_rc_fail": "加载系统级 onyxrc 失败：{}",
            "user_rc_fail": "加载用户级 onyxrc 失败：{}",
            "rc_exec_start": "开始执行 {} 初始化脚本...",
            "rc_exec_complete": "{} 初始化脚本执行完成",
            "rc_disabled": "RC 文件加载已禁用（config.json 中 rc_load_enabled 为 false）"
        },
        "english": {
            "sys_rc_load": "Loading system onyxrc: {}",
            "user_rc_load": "Loading user onyxrc: {}",
            "sys_rc_fail": "Failed to load system onyxrc: {}",
            "user_rc_fail": "Failed to load user onyxrc: {}",
            "rc_exec_start": "Executing {} initialization script...",
            "rc_exec_complete": "{} initialization script executed successfully",
            "rc_disabled": "RC file loading disabled (rc_load_enabled is false in config.json)"
        }
    }
    msg = lang_msgs[current_lang]
    
    # 检查配置是否正确
    if "rc_load_enabled" not in global_config:
        # 默认启用 rc 文件加载
        global_config["rc_load_enabled"] = True
    
    # 检查是否启用 rc 文件加载
    if not global_config.get("rc_load_enabled", True):
        log_info(msg["rc_disabled"], request_id)
        return
    
    # 加载系统级 onyxrc（优先级高）
    sys_rc_path = os.path.join(ROOT_DIR, "etc", "onyx.onyxrc")
    if os.path.exists(sys_rc_path) and os.path.isfile(sys_rc_path):
        try:
            log_info(msg["rc_exec_start"].format("系统"), request_id)
            _lazy_source(["source", sys_rc_path], request_id)
            log_info(msg["rc_exec_complete"].format("系统"), request_id)
            log_info(msg["sys_rc_load"].format(sys_rc_path), request_id)
        except Exception as e:
            log_error(msg["sys_rc_fail"].format(str(e)), request_id)
    
    # 加载用户级 onyxrc（优先级低，但会覆盖系统级设置）
    user_rc_path = os.path.join(USER_HOME_DIR, ".onyxrc")
    if os.path.exists(user_rc_path) and os.path.isfile(user_rc_path):
        try:
            log_info(msg["rc_exec_start"].format("用户"), request_id)
            _lazy_source(["source", user_rc_path], request_id)
            log_info(msg["rc_exec_complete"].format("用户"), request_id)
            log_info(msg["user_rc_load"].format(user_rc_path), request_id)
        except Exception as e:
            log_error(msg["user_rc_fail"].format(str(e)), request_id)

# ==================== 单次命令执行支持 ====================
_ONESHOT_MODE = False   # 全局标志，抑制交互式提示和欢迎信息

def initialize_onyx_environment(request_id: str, oneshot: bool = False) -> bool:
    """
    完整初始化 Onyx 环境
    :param request_id: 请求ID
    :param oneshot: 是否为单次执行模式（抑制交互提示和自启命令）
    :return: True 成功, False 失败
    """
    global _ONESHOT_MODE, sys_type, user_mode, CURRENT_BOOT_USAGE
    global PATH_RESOLVE_CACHE, DIR_FILE_CACHE, CMD_MAPPING_CACHE, current_sys_cmds
    _ONESHOT_MODE = oneshot

    # ========== 初始化计时字典 ==========
    init_timings: Dict[str, float] = {}
    init_start_time = time.perf_counter()
    step_start = init_start_time

    # 获取当前语言配置
    current_lang = get_current_lang()
    lang_msgs = {
        "chinese": {
            "fatal_user_home_fail": "致命错误：用户主目录初始化失败，程序无法启动",
            "fatal_config_load_fail": "致命错误：核心配置加载失败，程序无法启动",
            "fatal_admin_pwd_fail": "致命错误：管理员密码初始化失败，程序无法启动",
            "fatal_tool_dir_fail": "致命错误：工具目录初始化失败，程序无法启动",
            "oneshot_admin_pwd_missing": "非交互模式下需要预先设置管理员密码，请先交互式运行一次",
            "oneshot_sado_config_missing": "非交互模式下需要预先配置 sado 规则，请先交互式运行一次",
            "init_success": "Onyx 环境初始化成功",
            "init_failed": "Onyx 环境初始化失败",
            "cache_mode_detected": "持久化缓存检测到模式: {}，自动切换中...",
            "cache_mode_adv_flow": "缓存指定 adv 模式，正在执行安全切换流程...",
            "cache_mode_adv_low_first": "已切换到 low 模式，准备通过 activite 进入 adv...",
            "cache_mode_adv_success": "✅ 已通过持久化缓存成功进入 adv 模式",
            "cache_mode_adv_failed": "❌ 通过持久化缓存进入 adv 模式失败: {}",
            "cache_mode_invalid": "⚠️ 持久化缓存模式值无效: {}，忽略并使用默认配置"
        },
        "english": {
            "fatal_user_home_fail": "Fatal error: User home directory initialization failed, program cannot start",
            "fatal_config_load_fail": "Fatal error: Core config load failed, program cannot start",
            "fatal_admin_pwd_fail": "Fatal error: Admin password initialization failed, program cannot start",
            "fatal_tool_dir_fail": "Fatal error: Tool directory initialization failed, program cannot start",
            "oneshot_admin_pwd_missing": "Admin password must be set in interactive mode first",
            "oneshot_sado_config_missing": "Sado config must be initialized in interactive mode first",
            "init_success": "Onyx environment initialized successfully",
            "init_failed": "Onyx environment initialization failed",
            "cache_mode_detected": "Persistent mode cache detected: {}, auto switching to this mode",
            "cache_mode_adv_flow": "Cache specifies adv mode, executing safe switching flow...",
            "cache_mode_adv_low_first": "Switched to low mode, preparing to enter adv via activite...",
            "cache_mode_adv_success": "✅ Successfully entered adv mode via persistent cache",
            "cache_mode_adv_failed": "❌ Failed to enter adv mode via persistent cache: {}",
            "cache_mode_invalid": "⚠️ Invalid persistent cache mode value: {}, ignored and using default config"
        }
    }
    msg = lang_msgs.get(current_lang, lang_msgs["chinese"])

    def record_step(step_name: str) -> None:
        """记录当前步骤耗时"""
        nonlocal step_start
        current_time = time.perf_counter()
        elapsed = current_time - step_start
        init_timings[step_name] = elapsed
        step_start = current_time

    def save_init_timings() -> None:
        """保存初始化计时到文件，并标记耗时最高的操作"""
        try:
            cache_dir = os.path.join(USER_HOME_DIR, ".cache", "onyx", "onyx")
            if not os.path.exists(cache_dir):
                os.makedirs(cache_dir, mode=0o755)
            
            timings_file = os.path.join(cache_dir, "starttime_init")
            
            total_elapsed = time.perf_counter() - init_start_time
            init_timings["total"] = total_elapsed
            
            timings_no_total = {k: v for k, v in init_timings.items() if k != "total"}
            if timings_no_total:
                max_step = max(timings_no_total.items(), key=lambda x: x[1])
                max_step_name = max_step[0]
                max_step_time = max_step[1]
            else:
                max_step_name = "none"
                max_step_time = 0.0
            
            timings_data = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "request_id": request_id,
                "oneshot_mode": oneshot,
                "slowest_step": {
                    "name": max_step_name,
                    "time_sec": round(max_step_time, 4),
                    "time_ms": round(max_step_time * 1000, 2)
                },
                "timings_ms": {name: round(value * 1000, 2) for name, value in init_timings.items()},
                "timings_sec": {name: round(value, 4) for name, value in init_timings.items()}
            }
            
            with open(timings_file, "w", encoding="utf-8") as f:
                json.dump(timings_data, f, ensure_ascii=False, indent=2)
            
            if sys_type in ["Linux/macOS", "macOS", "Termux", "SpecialLinux"]:
                os.chmod(timings_file, 0o600)
            
            log_info(f"初始化计时已保存: {timings_file} (总耗时: {total_elapsed*1000:.2f}ms)", request_id)
        except Exception as e:
            log_warning(f"保存初始化计时失败: {str(e)}", request_id)

    try:
        # 1. 清屏（Main.py 入口已清屏，此处不再重复）
        record_step("1.clear_screen")

        # 2. 权限检测与用户主目录初始化
        check_admin_permission()
        record_step("2.check_admin_permission")
        
        # 必须在最前面初始化用户主目录（设置所有缓存路径）
        if not init_user_home():
            print(Fore.RED + msg["fatal_user_home_fail"] + Style.RESET_ALL)
            return False
        record_step("3.init_user_home")

        # 3. 颜色初始化、系统类型检测
        init_colorama()
        record_step("4.init_colorama")
        
        check_OS_TBS()
        record_step("5.check_OS_TBS")
        
        sys_type = detect_system()
        record_step("6.detect_system")

        # 4. 加载核心配置
        if not load_config():
            print(Fore.RED + msg["fatal_config_load_fail"] + Style.RESET_ALL)
            return False
        _sync_globals_to_ctx()  # load_config 设置了 TOOL_MAIN_DIR/global_config 等，同步到 ctx
        record_step("7.load_config")

        # 5. 沙箱配置
        init_sandbox_config()
        record_step("8.init_sandbox_config")
        
        init_onyxlog_permission(request_id)
        record_step("9.init_onyxlog_permission")
        
        init_user_mapping()
        record_step("10.init_user_mapping")

        # 6. 初始化用户交互式命令配置
        init_user_cmd_cli_file()
        record_step("7.init_user_cmd_cli_file")

        # 8. 管理员密码初始化
        if not init_admin_password():
            if oneshot:
                print(Fore.RED + msg["oneshot_admin_pwd_missing"] + Style.RESET_ALL)
                return False
            else:
                print(Fore.RED + msg["fatal_admin_pwd_fail"] + Style.RESET_ALL)
                return False
        record_step("13.init_admin_password")

        # 9. sado 配置初始化
        init_sado_config(request_id)
        if not os.path.exists(SADO_CONFIG_PATH) and oneshot:
            print(Fore.RED + msg["oneshot_sado_config_missing"] + Style.RESET_ALL)
            return False
        record_step("14.init_sado_config")

        # 10. 工具目录初始化
        if not init_tool_dirs():
            print(Fore.RED + msg["fatal_tool_dir_fail"] + Style.RESET_ALL)
            return False
        record_step("15.init_tool_dirs")

        # 11. 加载或扫描系统命令（必须在工具索引之前，防止 build_cmd_mapping_cache 提前写入残缺缓存）
        from lib.makecache import load_cmd_mapping_cache as lib_load_cmd_mapping_cache
        
        # 先尝试加载缓存
        sys_cache = lib_load_cmd_mapping_cache(
            request_id=request_id,
            sys_type=sys_type,
            CMD_MAPPING_MSG_PATH=CMD_MAPPING_MSG_PATH,
            CMD_CACHE_TTL=CMD_CACHE_TTL,
            log_info=log_info
        )
        
        cache_valid = False
        if sys_cache and isinstance(sys_cache, dict):
            # 获取 system 命令列表
            mapping = sys_cache.get("mapping", {})
            system_cmds = mapping.get("system", [])
            
            # 有效缓存应该至少有50个命令
            if isinstance(system_cmds, list) and len(system_cmds) > 50:
                cache_valid = True
                CMD_MAPPING_CACHE[sys_type] = sys_cache
                current_sys_cmds[sys_type] = system_cmds
                log_info(f"使用现有命令缓存: {len(system_cmds)} 个命令", request_id)
            else:
                log_warning(f"缓存无效（仅{len(system_cmds)}个命令），将后台重新扫描", request_id)
        
        # 6KB 缓存文件大小校验：文件过小说明缓存不完整
        if cache_valid and CMD_MAPPING_MSG_PATH and os.path.exists(CMD_MAPPING_MSG_PATH):
            try:
                cache_size = os.path.getsize(CMD_MAPPING_MSG_PATH)
                if cache_size < 6144:  # < 6KB
                    log_warning(f"缓存文件过小（{cache_size} bytes < 6KB），将后台重新扫描", request_id)
                    cache_valid = False
            except OSError:
                pass
        
        if not cache_valid:
            # 缓存无效，启动后台线程扫描（不阻塞启动关键路径）
            log_info(f"启动后台系统命令扫描...", request_id)
            # 同时清除两个缓存文件，确保从头干净扫描
            for cache_file in (CMD_MAPPING_MSG_PATH,
                               os.path.join(os.path.dirname(CMD_MAPPING_MSG_PATH), "dir_scan_cache.msgpack")):
                if os.path.exists(cache_file):
                    try:
                        os.remove(cache_file)
                    except OSError:
                        pass
            threading.Thread(
                target=scan_path_for_system_cmds,
                args=(request_id,),
                daemon=True,
                name="sys-cmd-scanner"
            ).start()
        record_step("16.load_or_scan_system_cmds")

        # 12. 构建工具索引（在系统命令缓存判定之后）
        build_tool_index(request_id)
        record_step("17.build_tool_index")

        # 17b. 后台手册页扫描已移至 main_loop 提示符显示后延迟执行
        record_step("17b.start_man_scan_deferred")
        
        # 13. 加载目录缓存
        load_directory_cache(request_id)
        record_step("18.load_directory_cache")
        
        # 14. 初始化进程管理
        init_process_control_module(request_id)
        record_step("19.init_process_control_module")

        # 15. 用户模式初始化
        default_mode = global_config["user_config"].get("default_mode", "low")
        default_tool_perm = global_config["user_config"].get("default_tool_perm", 3)
        default_lang = global_config["display_info"]["language"].get("default", "Chinese")
        user_mode = UserMode(default_mode, default_tool_perm, default_lang)
        if user_mode.current_mode == "adv":
            mode_perm_limit = global_config["mode_config"]["perm_limit"][user_mode.current_mode]["max_tool_perm"]
            user_mode.current_tool_perm = mode_perm_limit

        log_info(f"用户模式初始化: mode={user_mode.current_mode}, perm={user_mode.current_tool_perm}", request_id)
        record_step("20.init_user_mode")

        # ========== 从持久化缓存加载模式 ==========
        from bin.activite_cmd import load_mode_from_cache, clear_mode_cache
        
        mode_cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "onyx")
        cached_mode = load_mode_from_cache(mode_cache_dir)
        
        if cached_mode and not os.environ.get("_ONYX_ENV_MODE_DONE"):
            valid_modes = global_config["mode_config"]["levels"]
            if cached_mode in valid_modes:
                log_info(msg["cache_mode_detected"].format(cached_mode), request_id)
                
                if cached_mode == "adv":
                    if oneshot:
                        # oneshot 模式不能跳过密码验证，保持默认 low 模式
                        log_info(f"oneshot 模式：缓存的 adv 模式需要密码验证，保持默认模式", request_id)
                    else:
                        user_mode.current_mode = "low"
                        low_perm_limit = global_config["mode_config"]["perm_limit"]["low"]["max_tool_perm"]
                        user_mode.current_tool_perm = low_perm_limit
                        log_info(f"持久化缓存切换：先设置为 low 模式", request_id)
                        
                        os.environ["_ONYX_ENV_MODE_DONE"] = "1"
                        
                        try:
                            parse_and_execute("activite -m adv")
                        except Exception as e:
                            log_error(f"持久化缓存 adv 模式切换失败: {str(e)}", request_id)
                        finally:
                            os.environ.pop("_ONYX_ENV_MODE_DONE", None)
                
                elif cached_mode in ["low", "mid"]:
                    user_mode.current_mode = cached_mode
                    perm_limit = global_config["mode_config"]["perm_limit"][cached_mode]["max_tool_perm"]
                    user_mode.current_tool_perm = perm_limit
                    log_info(f"持久化缓存切换模式: {cached_mode}", request_id)
            else:
                log_warning(msg["cache_mode_invalid"].format(cached_mode), request_id)
                clear_mode_cache(mode_cache_dir)
        record_step("21.persistent_mode_cache")

        # 16. 其他初始化
        init_prompt_from_storage()
        record_step("22.init_prompt_from_storage")
        
        init_history_navigation()
        record_step("23.init_history_navigation")
        
        load_user_config()
        record_step("24.load_user_config")

        # 17. 路径缓存预热
        cached_paths = load_msgpack(PATH_INDEX_MSG_PATH)
        if cached_paths and isinstance(cached_paths, dict):
            current_time = time.time()
            valid_paths = {}
            for path, value in cached_paths.items():
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    resolved, cache_time = value
                    if current_time - cache_time < PATH_CACHE_TTL:
                        valid_paths[path] = (resolved, cache_time)
            # 容量硬上限：最多保留 1000 条（按时间排序，保留最新的）
            if len(valid_paths) > 1000:
                sorted_items = sorted(valid_paths.items(), key=lambda x: x[1][1], reverse=True)
                valid_paths = dict(sorted_items[:1000])
            PATH_RESOLVE_CACHE = valid_paths
            log_info(f"路径缓存加载: {len(valid_paths)} 条记录", request_id)
        record_step("25.path_cache_warmup")

        # 18. 根目录缓存预热
        if os.path.isdir(ROOT_DIR):
            cache_directory_files(ROOT_DIR, request_id)
            log_info(f"根目录缓存预热: {ROOT_DIR}", request_id)
        record_step("26.root_dir_cache_warmup")

        # 19. 初始化启动耗时统计
        init_boot_usage_time(request_id)
        record_step("27.init_boot_usage_time")

        # 20. manage 相关（延迟导入，避免启动时加载 prompt_toolkit）
        from bin.manage import sync_language_to_configjson, auto_clean_expired_logs, handle_manage, DEBUG_TIMES_PATH, DEBUG_PARSECMD_PATH
        global DEBUG_TIMES_PATH, DEBUG_PARSECMD_PATH  # 更新模块级全局变量
        sync_language_to_configjson()
        record_step("28.sync_language_to_configjson")
        
        auto_clean_expired_logs()
        record_step("29.auto_clean_expired_logs")
        
        if not oneshot:
            handle_manage(["manage", "-q"], request_id)
        record_step("30.handle_manage")

        record_step("31.init_oppath")

        # 21. 加载自启命令
        load_autocmd()
        record_step("32.load_autocmd")
        
        if not oneshot and AUTO_CMDS and global_config.get("user_config", {}).get("enable_autocmd", True):
            for cmd_info in AUTO_CMDS:
                auto_req_id = str(uuid.uuid4())
                log_info(f"执行自启命令 (ID: {cmd_info['id']}): {cmd_info['cmd']}", auto_req_id)
                parse_and_execute(cmd_info["cmd"])
        record_step("33.execute_autocmd")

        # 22. RC 文件创建
        check_and_create_system_rc()
        record_step("34.check_and_create_system_rc")
        
        check_and_create_user_rc()
        record_step("35.check_and_create_user_rc")
        
        # ========== 保存计时结果 ==========
        save_init_timings()

        log_info(msg["init_success"], request_id)
        return True

    except Exception as e:
        try:
            record_step("ERROR_" + str(e)[:30])
            save_init_timings()
        except Exception:
            pass
        
        log_error(f"{msg['init_failed']}: {str(e)}", request_id)
        import traceback
        traceback.print_exc()
        return False


def run_command_once(cmd: str) -> int:
    """
    执行单条命令后退出
    :param cmd: 要执行的命令字符串
    :return: 退出码 (0 成功, 1 初始化失败, 130 Ctrl+C)
    """
    # 初始化 AppContext（oneshot 模式也需要）
    from core.context import init_ctx
    init_ctx()
    _sync_globals_to_ctx()

    request_id = str(uuid.uuid4())
    current_lang = get_current_lang()
    lang_msgs = {
        "chinese": {
            "exec_error": "命令执行异常：{}",
            "init_failed": "初始化失败，退出码：1",
        },
        "english": {
            "exec_error": "Command execution error: {}",
            "init_failed": "Initialization failed, exit code: 1",
        }
    }
    msg = lang_msgs.get(current_lang, lang_msgs["chinese"])

    # 1. 初始化环境（单次执行模式）
    if not initialize_onyx_environment(request_id, oneshot=True):
        return 1

    # 再次同步（捕获 init 中设置的变量）
    _sync_globals_to_ctx()

    # 2. 执行命令
    try:
        parse_and_execute(cmd)
        return 0
    except KeyboardInterrupt:
        print("\n^C")
        return 130
    except SystemExit:
        return 0
    except Exception as e:
        print(Fore.RED + msg["exec_error"].format(str(e)) + Style.RESET_ALL)
        return 1
    finally:
        # 3. 清理资源
        try:
            graceful_shutdown(request_id)
        except Exception:
            pass


def is_oneshot_mode() -> bool:
    """判断是否为单次执行模式"""
    return _ONESHOT_MODE

def _sync_globals_to_ctx() -> None:
    """将 Onyx.py 模块级全局变量同步到 AppContext 单例"""
    from core.context import get_ctx
    ctx = get_ctx()
    # 路径
    ctx.ROOT_DIR = ROOT_DIR
    ctx.USER_HOME_DIR = USER_HOME_DIR
    ctx.TOOL_MAIN_DIR = TOOL_MAIN_DIR
    ctx.VIRTUAL_ROOT = VIRTUAL_ROOT
    ctx.CACHE_DIR = CACHE_DIR
    # 系统
    ctx.sys_type = sys_type
    ctx.OS_OR_TBS = OS_OR_TBS
    ctx.PYTHON_EXE = PYTHON_EXE
    # 沙箱
    ctx._SANDBOX_ENABLED = _SANDBOX_ENABLED
    # 配置
    ctx.global_config = global_config
    ctx.SANDBOX_CONFIG = SANDBOX_CONFIG
    ctx.executable_config = executable_config
    ctx.SADO_CONFIG = SADO_CONFIG
    ctx.SADO_CONFIG_PATH = SADO_CONFIG_PATH
    # 用户
    ctx.user_info = user_info
    ctx.user_mode = user_mode
    ctx.ADMIN_PASSWORD_PATH = ADMIN_PASSWORD_PATH
    # 缓存路径
    ctx.TOOL_INDEX_MSG_PATH = TOOL_INDEX_MSG_PATH
    ctx.PATH_INDEX_MSG_PATH = PATH_INDEX_MSG_PATH
    ctx.CMD_MAPPING_MSG_PATH = CMD_MAPPING_MSG_PATH
    ctx.DIR_CACHE_MSG_PATH = DIR_CACHE_MSG_PATH
    ctx.DEBUG_TIMES_PATH = DEBUG_TIMES_PATH
    ctx.DEBUG_PARSECMD_PATH = DEBUG_PARSECMD_PATH
    # 缓存
    ctx.TOOL_INDEX_CACHE = TOOL_INDEX_CACHE
    ctx.CMD_MAPPING_CACHE = CMD_MAPPING_CACHE
    ctx.PATH_RESOLVE_CACHE = PATH_RESOLVE_CACHE
    ctx.DIR_FILE_CACHE = DIR_FILE_CACHE
    ctx.current_sys_cmds = current_sys_cmds
    # 进程
    ctx.PROCESS_LOCK = PROCESS_LOCK
    ctx.executor = executor
    ctx.CURRENT_PROCESSES = CURRENT_PROCESSES
    # 别名/历史
    ctx.ALIAS_CACHE = ALIAS_CACHE
    ctx.BUILTIN_COMMANDS = BUILTIN_COMMANDS
    # 其他
    ctx.AI_TOOL_OUTPUT_CACHE = AI_TOOL_OUTPUT_CACHE
    ctx.SUPPORTED_EXEC_SUFFIXES = SUPPORTED_EXEC_SUFFIXES
    ctx.AUTO_CMD_PATH = AUTO_CMD_PATH
    ctx.AUTO_CMDS = AUTO_CMDS


def main_loop() -> None:
    """
    Onyx Toolbox 主循环
    """
    input_start_time: float = 0.0
    global sys_type, user_mode, LOG_DIR, LOG_TIMESTAMP
    
    # 记录程序启动绝对时间（用于计算总耗时）
    program_start_time = time.perf_counter()
    # 延迟导入 DEBUG_TIMES_PATH（避免 import Onyx 时引入 prompt_toolkit）
    from bin.manage import DEBUG_TIMES_PATH

    def get_lang_msgs(current_lang: str) -> dict:
        lang_map = {
            "chinese": {
                "boot_time_info": "启动耗时：{}",
                "system_ready": "\n[Shell 就绪] 输入命令开始操作（输入 exit 退出）...",
                "main_loop_crash": "主循环异常崩溃：{}",
                "keyboard_interrupt": "\n^C",
                "cmd_exec_interrupt": "命令执行中断：用户触发Ctrl+C",
                "cmd_exec_error": "命令执行异常：{}",
                "debug_time_cost": "（耗时：{}s）",
                "rc_executing": "正在执行 RC 初始化脚本...",
                "rc_executed": "RC 脚本执行完成",
                # 新增启动阶段日志
                "stage_module_load": "阶段1-模块加载完成",
                "stage_init_env_start": "阶段2-开始环境初始化",
                "stage_init_env_complete": "阶段2-环境初始化完成 (耗时: {:.2f}ms)",
                "stage_import_banner": "阶段3-导入Banner模块 (耗时: {:.2f}ms)",
                "stage_show_banner": "阶段4-显示启动横幅 (耗时: {:.2f}ms)",
                "stage_rc_files": "阶段5-执行RC文件 (耗时: {:.2f}ms)",
                "stage_show_ready": "阶段6-显示就绪提示 (耗时: {:.2f}ms)",
                "stage_wait_input": "阶段7-等待用户输入",
                "total_startup": "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Onyx 启动完成！总耗时: {:.2f}ms\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            },
            "english": {
                "boot_time_info": "Boot Time: {}",
                "system_ready": "\n[Shell Ready] Enter command to start operation (enter exit to exit)...",
                "main_loop_crash": "Main loop crashed unexpectedly: {}",
                "keyboard_interrupt": "\n^C",
                "cmd_exec_interrupt": "Command execution interrupted: User triggered Ctrl+C",
                "cmd_exec_error": "Command execution error: {}",
                "debug_time_cost": "（Time cost: {}s）",
                "rc_executing": "Executing RC initialization script...",
                "rc_executed": "RC script executed successfully",
                # New startup stage logs
                "stage_module_load": "Stage1-Module load completed",
                "stage_init_env_start": "Stage2-Start environment initialization",
                "stage_init_env_complete": "Stage2-Environment initialization completed (time: {:.2f}ms)",
                "stage_import_banner": "Stage3-Import Banner module (time: {:.2f}ms)",
                "stage_show_banner": "Stage4-Show startup banner (time: {:.2f}ms)",
                "stage_rc_files": "Stage5-Execute RC files (time: {:.2f}ms)",
                "stage_show_ready": "Stage6-Show ready prompt (time: {:.2f}ms)",
                "stage_wait_input": "Stage7-Waiting for user input",
                "total_startup": "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ Onyx startup complete! Total time: {:.2f}ms\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            }
        }
        return lang_map.get(current_lang.lower(), lang_map["chinese"])

    try:
        request_id = str(uuid.uuid4())

        # ========== 阶段1: 记录模块加载完成时间 ==========
        # 注意：模块加载时间在进入 main_loop 前就已经发生
        # 这个时间点记录的是从程序启动到进入 main_loop 的耗时
        module_load_ms = (time.perf_counter() - program_start_time) * 1000
        log_info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", request_id)
        log_info(f"📊 启动性能分析 - 开始", request_id)
        log_info(f"   阶段1-模块加载: {module_load_ms:.2f}ms", request_id)

        # ========== 阶段2: 初始化环境 ==========
        stage_start = time.perf_counter()
        log_info(f"   阶段2-环境初始化: 开始...", request_id)
        
        # 初始化核心 AppContext（替代散落的全局变量）
        from core.context import init_ctx
        init_ctx()
        # 先同步模块级全局变量（ROOT_DIR/ADMIN_PASSWORD_PATH 等在 import 时已设置）
        _sync_globals_to_ctx()

        if not initialize_onyx_environment(request_id, oneshot=False):
            sys.exit(1)
        
        # 再次同步（捕获 initialize_onyx_environment 中设置的变量）
        _sync_globals_to_ctx()
        
        init_env_ms = (time.perf_counter() - stage_start) * 1000
        log_info(f"   阶段2-环境初始化: 完成 (耗时: {init_env_ms:.2f}ms)", request_id)

        # ========== 获取当前语言配置 ==========
        current_lang = global_config["display_info"]["language"]["current"]
        lang_msgs = get_lang_msgs(current_lang)

        # ========== 阶段3: 导入启动横幅模块 ==========
        stage_start = time.perf_counter()
        log_info(f"   阶段3-导入Banner模块: 开始...", request_id)
        
        from lib.start_banner import show_start_banner, show_ready_prompt
        
        import_banner_ms = (time.perf_counter() - stage_start) * 1000
        log_info(f"   阶段3-导入Banner模块: 完成 (耗时: {import_banner_ms:.2f}ms)", request_id)

        # ========== 阶段4: 显示启动横幅 ==========
        stage_start = time.perf_counter()
        
        # 准备参数
        program_version = global_config["program_info"].get("version", "1.0.0")
        system_display = "Linux(?)" if (OS_OR_TBS == "OS" and sys_type == "Termux") else sys_type
        tool_count = len(TOOL_INDEX_CACHE)
        
        # 显示启动横幅
        show_start_banner(
            version=program_version,
            mode=OS_OR_TBS,
            system_type=system_display,
            tools_count=tool_count,
            boot_time=CURRENT_BOOT_USAGE,
            language=current_lang
        )
        
        show_banner_ms = (time.perf_counter() - stage_start) * 1000
        log_info(f"   阶段4-显示启动横幅: 完成 (耗时: {show_banner_ms:.2f}ms)", request_id)

        # ========== 阶段5: 执行 RC 文件 ==========
        stage_start = time.perf_counter()
        
        if global_config.get("rc_load_enabled", True):
            log_info(lang_msgs["rc_executing"], request_id)
            source_rc_files(request_id)
            log_info(lang_msgs["rc_executed"], request_id)
        
        rc_files_ms = (time.perf_counter() - stage_start) * 1000
        log_info(f"   阶段5-执行RC文件: 完成 (耗时: {rc_files_ms:.2f}ms)", request_id)

        # ========== 阶段6: 显示就绪提示 ==========
        stage_start = time.perf_counter()
        
        show_ready_prompt(language=current_lang)
        
        show_ready_ms = (time.perf_counter() - stage_start) * 1000
        log_info(f"   阶段6-显示就绪提示: 完成 (耗时: {show_ready_ms:.2f}ms)", request_id)

        # ========== 时段问候 ==========
        # 委托 lib/spring.py：按时段随机问候 + 凌晨交互式睡眠确认
        from lib.spring import show_startup_greeting
        if show_startup_greeting(current_lang):
            sys.exit(0)

        # ========== 计算总启动耗时 ==========
        total_startup_ms = (time.perf_counter() - program_start_time) * 1000
        
        # 输出启动耗时汇总
        log_info(f"", request_id)
        log_info(f"📊 启动性能分析 - 汇总", request_id)
        log_info(f"   ┌─────────────────────────────────────────────────────────┐", request_id)
        log_info(f"   │ 阶段1 (模块加载)      : {module_load_ms:>8.2f} ms  │", request_id)
        log_info(f"   │ 阶段2 (环境初始化)    : {init_env_ms:>8.2f} ms  │", request_id)
        log_info(f"   │ 阶段3 (导入Banner)    : {import_banner_ms:>8.2f} ms  │", request_id)
        log_info(f"   │ 阶段4 (显示横幅)      : {show_banner_ms:>8.2f} ms  │", request_id)
        log_info(f"   │ 阶段5 (执行RC文件)    : {rc_files_ms:>8.2f} ms  │", request_id)
        log_info(f"   │ 阶段6 (显示就绪提示)  : {show_ready_ms:>8.2f} ms  │", request_id)
        log_info(f"   ├─────────────────────────────────────────────────────────┤", request_id)
        log_info(f"   │ 总启动耗时           : {total_startup_ms:>8.2f} ms  │", request_id)
        log_info(f"   └─────────────────────────────────────────────────────────┘", request_id)
        
      
        # ========== 后台预热：加载路径权限配置 ==========
        def _preload_perm_path():
            try:
                _u = user_info.get("name", "default").strip()
                def _noop(msg, rid): pass
                from lib.safe import load_perm_path_config
                load_perm_path_config(ROOT_DIR, _u, _noop)
                log_info("路径权限配置已后台加载", request_id)
            except Exception as e:
                log_warning(f"路径权限配置后台加载失败: {str(e)}", request_id)
        threading.Thread(target=_preload_perm_path, daemon=True, name="perm-path-preloader").start()

        # ========== 后台预热：创建持久化 Shell（避免首条命令等待 PTY fork）==========
        def _prewarm_shell():
            try:
                from lib.terminal.exe import _get_persistent_shell
                _get_persistent_shell()
                log_info("持久化 Shell 已后台预热", request_id)
            except Exception as e:
                log_warning(f"持久化 Shell 后台预热失败: {str(e)}", request_id)
        threading.Thread(target=_prewarm_shell, daemon=True, name="shell-prewarmer").start()

        # ========== 启动后台扫描（不阻塞交互） ==========
        # man 手册页扫描：直接启动，不再延迟
        def _deferred_start_man_scan():
            try:
                start_background_scan()
                log_info("后台手册页扫描已启动", request_id)
            except Exception as e:
                log_warning(f"后台手册页扫描启动失败: {str(e)}", request_id)
        threading.Thread(target=_deferred_start_man_scan, daemon=True, name="man-scanner-delayed").start()
        
        # ========== 阶段7: 交互循环 ==========
        log_info(f"   阶段7-进入交互模式: 等待用户输入", request_id)
        
        # 记录命令执行次数
        cmd_count = 0
        last_log_time = time.time()
        # debug_times 配置缓存（避免每条命令读磁盘）
        _debug_times_cache = None
        _debug_times_cache_time = 0.0
        
        while True:
            # 清理僵尸进程
            kill_stale_processes()

            try:
                cmd = universal_input(generate_prompt)
                input_start_time = time.perf_counter()
            except KeyboardInterrupt:
                print(Fore.YELLOW + lang_msgs["keyboard_interrupt"] + Style.RESET_ALL)
                continue
            except EOFError:
                print('^D')
                continue

            cmd_stripped = cmd.strip()
            if not cmd_stripped:
                continue

            # 统计命令执行次数（每100条命令输出一次统计）
            cmd_count += 1
            current_time = time.time()
            if current_time - last_log_time >= 60:  # 每分钟输出一次统计
                log_info(f"📊 命令统计: 已执行 {cmd_count} 条命令", request_id)
                last_log_time = current_time
            
            # 定期触发 GC：每 100 条命令回收一次内存碎片
            if cmd_count % 100 == 0:
                gc.collect()

            parse_execute_success = True
            try:
                parse_and_execute(cmd_stripped)
            except KeyboardInterrupt:
                print(Fore.YELLOW + lang_msgs["cmd_exec_interrupt"] + Style.RESET_ALL)
                log_error(lang_msgs["cmd_exec_interrupt"], user_info["session_id"])
                parse_execute_success = False
            except Exception as e:
                err_msg = lang_msgs["cmd_exec_error"].format(str(e)[:200])
                print(Fore.RED + err_msg + Style.RESET_ALL)
                import traceback
                log_error(f"Command execution failed: {str(e)}\n{traceback.format_exc()}", user_info["session_id"])
                parse_execute_success = False

            # 调试模式耗时显示（30s 缓存，避免每条命令读磁盘）
            _now = time.time()
            if _debug_times_cache is None or (_now - _debug_times_cache_time) >= 30:
                _debug_times_cache = read_config_file(DEBUG_TIMES_PATH, False)
                _debug_times_cache_time = _now
            debug_times_enabled = _debug_times_cache
            if parse_execute_success and debug_times_enabled:
                input_end_time = time.perf_counter()
                input_duration_s = round((input_end_time - input_start_time), 3)
                print(Fore.YELLOW + lang_msgs["debug_time_cost"].format(input_duration_s) + Style.RESET_ALL)

    except Exception as e:
        current_lang = get_current_lang()
        lang_msgs = get_lang_msgs(current_lang)
        import traceback
        
        # 计算从启动到崩溃的总时长
        crash_time_ms = (time.perf_counter() - program_start_time) * 1000
        
        # 使用错误横幅
        from lib.start_banner import show_error_banner
        show_error_banner(str(e), language=current_lang)
        
        full_end_time = time.perf_counter()
        full_duration_ms = int((full_end_time - input_start_time) * 1000) if input_start_time > 0 else 0

        exc_type, exc_obj, exc_tb = sys.exc_info()
        tb_str = "".join(traceback.format_exception(exc_type, exc_obj, exc_tb))
        err_msg = lang_msgs["main_loop_crash"].format(str(e))

        print(Fore.CYAN + "Exception type: " + Style.RESET_ALL + str(exc_type.__name__))
        print(Fore.CYAN + "Exception info: " + Style.RESET_ALL + str(e))
        print(Fore.CYAN + "Crash time: " + Style.RESET_ALL + f"{full_duration_ms}ms")
        print(Fore.CYAN + "Total runtime before crash: " + Style.RESET_ALL + f"{crash_time_ms:.2f}ms")
        print(Fore.CYAN + "Session ID: " + Style.RESET_ALL + user_info["session_id"])
        print(Fore.CYAN + "System type: " + Style.RESET_ALL + sys_type)
        print(Fore.CYAN + "Current path: " + Style.RESET_ALL + os.getcwd())
        print(Fore.RED + "\n[Full Stack Trace]" + Style.RESET_ALL)
        print(tb_str)

        log_error(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", user_info["session_id"])
        log_error(f"❌ 程序崩溃 - 总运行时长: {crash_time_ms:.2f}ms", user_info["session_id"])
        log_error(f"   异常类型: {exc_type.__name__}", user_info["session_id"])
        log_error(f"   异常信息: {str(e)}", user_info["session_id"])
        log_error(f"{err_msg}\n{tb_str}", user_info["session_id"])

        try:
            graceful_shutdown(user_info["session_id"])
        except Exception:
            pass

        sys.exit(1)



# ============================================================
# Core re-exports — 保持外部 `from Onyx import ...` 兼容
# ============================================================
from core.context import AppContext, get_ctx, init_ctx, ToolInfo, UserMode
from core.path_ops import (
    resolve_path as _core_resolve_path,
    get_virtual_path as _core_get_virtual_path,
    get_physical_path as _core_get_physical_path,
    validate_param_path as _core_validate_param_path,
    format_virtual_path as _core_format_virtual_path,
    replace_virtual_path_in_cmd as _core_replace_virtual_path_in_cmd,
)
from core.security import (
    check_sandbox_path as _core_check_sandbox_path,
    check_blocked_cmd as _core_check_blocked_cmd,
    check_tool_permission as _core_check_tool_permission,
    get_tool_permission as _core_get_tool_permission,
    set_tool_permission as _core_set_tool_permission,
    kill_stale_processes as _core_kill_stale_processes,
)
from core.tool_registry import (
    find_tool as _core_find_tool,
    execute_tool as _core_execute_tool,
    build_tool_index as _core_build_tool_index,
    find_similar_tools as _core_find_similar_tools,
    find_similar_cmds as _core_find_similar_cmds,
    load_tool_config as _core_load_tool_config,
)
from core.config_loader import load_config as _core_load_config
from core.log_manager import (
    init_logger as _core_init_logger,
    log_info as _core_log_info,
    log_error as _core_log_error,
    log_warning as _core_log_warning,
    security_log as _core_security_log,
)
from core.cmd_registry import (
    is_interactive_command as _core_is_interactive_command,
    build_builtin_registry,
)

if __name__ == "__main__":
    main_loop()
    