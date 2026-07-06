#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 路径保护系统：主目录内操作均合法，仅拦截主目录外保护路径访问
# 支持C语言动态库降级逻辑，接口兼容Onyx.py调用
import os
import sys
import re
import shlex
import uuid
import time
import msgpack
import stat
import subprocess
import shutil
from typing import List, Optional, Tuple, Dict
# 引入get_lib_path模块（按实际路径调整，此处为from lib.get_lib_path import ...）
from lib.get_lib_path import get_lib_path, _is_termux_environment

# ====================== 全局变量预声明（解决作用域问题核心）======================
# 基础配置变量
ROOT_DIR = ""
USER_HOME_DIR = ""
PATH_CACHE_TTL = 1800
RESULT_CACHE_PATH = ""
# C动态库相关变量（全局优先声明，避免局部遮蔽）
C_LIB = None
C_LIB_AVAILABLE = False
C_LIB_PATH = ""
C_LIB_ERROR = ""
# 缓存变量
PATH_RESOLVE_CACHE: Dict[str, Tuple[str, float]] = {}
PROTECTED_PATHS_CACHE: Optional[List[str]] = None
# Termux硬编码路径（保留原逻辑）
TERMUX_HARDCODED_HOME = "/data/data/com.termux/files/home"

# ====================== 工具函数（删除原路径相关，复用get_lib_path）======================
def _run_command(cmd: str) -> Tuple[str, str, int]:
    """执行系统命令（内部用）"""
    try:
        result = subprocess.run(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, text=True
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), -1

# 替换原_is_termux_env，直接复用get_lib_path中的_is_termux_environment
_is_termux_env = _is_termux_environment

# ====================== C库相关函数（改造路径获取逻辑，依赖get_lib_path）======================
def _diagnose_c_lib_availability() -> None:
    """诊断C库不可用原因（改造核心：调用get_lib_path获取oppath库路径）"""
    global C_LIB_ERROR, C_LIB_PATH
    C_LIB_ERROR = ""
    C_LIB_PATH = ""
    
    # Termux环境默认禁用C库（保留原逻辑）
    if _is_termux_env():
        C_LIB_ERROR = "Termux环境默认禁用C库，使用Python实现"
        return
    
    # 核心改造：调用get_lib_path获取oppath动态库路径（库名称为oppath）
    C_LIB_PATH = get_lib_path("oppath")
    if not C_LIB_PATH:
        C_LIB_ERROR = "非Termux环境未找到oppath C库（通过get_lib_path查找）"
    else:
        C_LIB_ERROR = f"通过get_lib_path找到oppath C库：{os.path.basename(C_LIB_PATH)}"

def _load_c_library() -> None:
    """加载C库并绑定函数（原逻辑不变，仅使用改造后的C_LIB_PATH）"""
    global C_LIB, C_LIB_AVAILABLE, C_LIB_ERROR
    C_LIB_AVAILABLE = False
    
    # Termux环境不加载C库（保留原逻辑）
    if _is_termux_env():
        C_LIB_ERROR = "Termux环境使用Python实现"
        return
    
    if not C_LIB_PATH or C_LIB_ERROR.startswith("非Termux环境未找到"):
        return
    
    try:
        from ctypes import CDLL, c_char_p, c_bool
        C_LIB = CDLL(C_LIB_PATH)
        # 绑定函数（与C库接口完全对齐，原逻辑不变）
        C_LIB.resolve_onyx_path.argtypes = [c_char_p, c_char_p, c_char_p, c_char_p]
        C_LIB.resolve_onyx_path.restype = c_char_p
        C_LIB.validate_onyx_param.argtypes = [c_char_p, c_char_p, c_char_p, c_char_p]
        C_LIB.validate_onyx_param.restype = c_char_p
        C_LIB.extract_onyx_paths.argtypes = [c_char_p, c_char_p, c_char_p, c_char_p]
        C_LIB.extract_onyx_paths.restype = c_char_p
        C_LIB.load_protected_paths.argtypes = [c_char_p, c_char_p]
        C_LIB.load_protected_paths.restype = c_char_p
        C_LIB.check_command.argtypes = [c_char_p, c_char_p, c_char_p, c_char_p]
        C_LIB.check_command.restype = c_bool
        C_LIB.free_c_string.argtypes = [c_char_p]
        C_LIB.free_c_string.restype = None
        C_LIB_AVAILABLE = True
    except Exception as e:
        C_LIB_ERROR = f"C库初始化失败：{str(e)}"
        C_LIB_AVAILABLE = False

# ====================== 以下Python核心逻辑/缓存工具/初始化/对外接口 均保持原代码不变 =======================
def _get_username_strict() -> str:
    """严格获取用户名（原逻辑不变）"""
    try:
        from getpass import getpass
        username = getpass.getuser()
        if username and username.strip() and username.lower() != "default":
            return username.strip()
    except:
        pass
    for var in ["USER", "USERNAME", "LOGNAME"]:
        username = os.getenv(var)
        if username and username.strip() and username.lower() != "default":
            return username.strip()
    try:
        if os.name == "posix":
            import pwd
            return pwd.getpwuid(os.getuid()).pw_name
        elif os.name == "nt":
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            ctypes.windll.advapi32.GetUserNameW(buf, ctypes.byref(ctypes.c_ulong(1024)))
            return buf.value.strip() if buf.value else ""
    except:
        pass
    return f"user_{uuid.uuid4().hex[:8]}"

def _get_user_home_dir_python() -> Tuple[str, str]:
    """获取用户主目录（原逻辑不变）"""
    username = _get_username_strict().strip()
    username = re.sub(r'[\\/:*?"<>|]', "", username) or f"user_{uuid.uuid4().hex[:8]}"
    is_admin = False
    try:
        if os.name == "posix":
            is_admin = os.geteuid() == 0
        elif os.name == "nt":
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        is_admin = False
    
    if is_admin:
        user_home = os.path.abspath(os.path.join(ROOT_DIR, "root"))
    else:
        user_home = os.path.abspath(os.path.join(ROOT_DIR, "home", username))
    if not os.path.exists(user_home):
        os.makedirs(user_home, mode=0o755)
    return user_home, username

def _resolve_onyx_path_python(path: str) -> str:
    """路径解析（与C库resolve_onyx_path逻辑一致，原逻辑不变）"""
    root_abs = os.path.normpath(os.path.realpath(ROOT_DIR))
    user_abs = os.path.normpath(os.path.realpath(USER_HOME_DIR))
    if path == "/":
        return root_abs
    elif path == "~":
        return user_abs
    elif path == "-":
        return os.environ.get("OLDPWD", user_abs)
    elif path.startswith("~/"):
        return os.path.normpath(os.path.abspath(os.path.join(user_abs, path[2:])))
    elif path.startswith("/"):
        return os.path.normpath(os.path.abspath(os.path.join(root_abs, path[1:])))
    else:
        return os.path.normpath(os.path.abspath(os.path.join(os.getcwd(), path)))

def _validate_onyx_param_python(param: str) -> str:
    """参数验证（与C库validate_onyx_param逻辑一致，原逻辑不变）"""
    if not param or param[0] == '-':
        return param
    virtual_path = _resolve_onyx_path_python(param)
    if os.path.exists(virtual_path):
        return virtual_path
    real_phys_path = os.path.abspath(param)
    if os.path.exists(real_phys_path):
        return real_phys_path
    return param

def _extract_onyx_paths_python(cmd: str) -> List[str]:
    """提取命令中的路径参数（与C库extract_onyx_paths逻辑一致，原逻辑不变）"""
    cmd_parts = shlex.split(cmd)
    if not cmd_parts:
        return []
    paths = []
    for part in cmd_parts[1:]:
        validated = _validate_onyx_param_python(part)
        is_path = (
            (os.path.exists(validated) and (os.path.isfile(validated) or os.path.isdir(validated)))
            or
            (any(c in validated for c in ["/", "\\"]) and not validated.startswith("-"))
        )
        if is_path:
            paths.append(validated.lower())
    return paths

def _init_oppath_file_python() -> bool:
    """初始化保护路径文件（原逻辑不变）"""
    oppath_file = os.path.join(ROOT_DIR, "etc", "pki", "oppath.txt")
    oppath_dir = os.path.dirname(oppath_file)
    if not os.path.exists(oppath_dir):
        try:
            os.makedirs(oppath_dir, mode=0o700)
        except Exception:
            return False
    default_paths = ["onyx/", "etc/pki/", "onyxlog/", "tools/","bin/", "*.key", "*.pem", "*.cert", "*.db", "home/*/onyxlog/","home/*/*cmd*"]
    try:
        with open(oppath_file, "w", encoding="utf-8") as f:
            f.write("# Onyx oppath保护路径列表\n# 格式：每行1个路径（相对根目录），支持通配符（如*.key）\n# 注释行以#开头，空行会被忽略\n")
            f.write("\n".join(default_paths))
        os.chmod(oppath_file, 0o600)
        return True
    except Exception:
        return False

def _load_protected_paths_python() -> List[str]:
    """加载保护路径（与C库load_protected_paths逻辑一致，原逻辑不变）"""
    oppath_file = os.path.join(ROOT_DIR, "etc", "pki", "oppath.txt")
    if not os.path.exists(oppath_file):
        if not _init_oppath_file_python():
            return []
    try:
        with open(oppath_file, "r", encoding="utf-8") as f:
            paths = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
        abs_paths = []
        for path in paths:
            if path.startswith("*"):
                abs_paths.append(path.lower())
            else:
                abs_path = os.path.abspath(os.path.join(ROOT_DIR, path)).lower()
                abs_paths.append(abs_path)
                abs_paths.append(path.lower())
        return abs_paths
    except Exception:
        return []

# ====================== 缓存工具函数（原逻辑不变）======================
def _load_path_cache() -> None:
    global PATH_RESOLVE_CACHE
    if not RESULT_CACHE_PATH:
        return
    try:
        if os.path.exists(RESULT_CACHE_PATH):
            with open(RESULT_CACHE_PATH, "rb") as f:
                cached_data = msgpack.load(f, raw=False)
            if isinstance(cached_data, dict):
                PATH_RESOLVE_CACHE = cached_data
    except Exception:
        PATH_RESOLVE_CACHE = {}

def _save_path_cache() -> None:
    if not RESULT_CACHE_PATH:
        return
    try:
        os.makedirs(os.path.dirname(RESULT_CACHE_PATH), exist_ok=True)
        with open(RESULT_CACHE_PATH, "wb") as f:
            msgpack.dump(PATH_RESOLVE_CACHE, f, use_bin_type=True)
    except Exception:
        pass

def _load_protected_paths_cache() -> None:
    global PROTECTED_PATHS_CACHE
    if C_LIB_AVAILABLE:
        try:
            root_bytes = ROOT_DIR.encode("utf-8")
            msg_bytes = b"chinese"
            c_result = C_LIB.load_protected_paths(root_bytes, msg_bytes)
            if c_result:
                PROTECTED_PATHS_CACHE = c_result.decode("utf-8").split(",") if c_result else []
                C_LIB.free_c_string(c_result)  # 释放C库内存
                return
        except Exception as e:
            C_LIB_ERROR = f"C库加载保护路径失败：{str(e)}"
    PROTECTED_PATHS_CACHE = _load_protected_paths_python()

# ====================== 初始化函数（原逻辑不变）======================
def init_oppath(
    root_dir: str,
    user_home_dir: str,
    path_cache_ttl: int = 1800,
    result_cache_path: str = ""
) -> None:
    global ROOT_DIR, USER_HOME_DIR, PATH_CACHE_TTL, RESULT_CACHE_PATH
    ROOT_DIR = os.path.normpath(os.path.realpath(root_dir)) if root_dir else "/home/user/onyx"
    USER_HOME_DIR = os.path.normpath(os.path.realpath(user_home_dir)) if user_home_dir else "/home/user/onyx/home/default"
    PATH_CACHE_TTL = path_cache_ttl
    RESULT_CACHE_PATH = result_cache_path if result_cache_path else os.path.join(USER_HOME_DIR, ".cache", "onyx", "oppath_cache.msgpack")
    
    global C_LIB_AVAILABLE, C_LIB_PATH, C_LIB_ERROR
    C_LIB_AVAILABLE = False
    C_LIB_PATH = ""
    C_LIB_ERROR = ""
    
    _diagnose_c_lib_availability()
    _load_c_library()
    _load_path_cache()
    _load_protected_paths_cache()
    
    if __name__ == "__main__":
        _print_init_info()

# ====================== 对外暴露核心接口（原逻辑不变）======================
def check_oppath(cmd: str) -> bool:
    global C_LIB_ERROR, C_LIB_AVAILABLE
    cmd = cmd.strip()
    if not cmd:
        return True
    
    # 优先使用C库（Termux环境不会进入此分支）
    if C_LIB_AVAILABLE:
        try:
            cmd_bytes = cmd.encode("utf-8")
            root_bytes = ROOT_DIR.encode("utf-8")
            user_home_bytes = USER_HOME_DIR.encode("utf-8")
            protected_paths_bytes = ",".join(PROTECTED_PATHS_CACHE or []).encode("utf-8")
            result = C_LIB.check_command(cmd_bytes, root_bytes, user_home_bytes, protected_paths_bytes)
            return result
        except Exception as e:
            C_LIB_ERROR = f"C库执行失败：{str(e)}"
            C_LIB_AVAILABLE = False
    
    # Python降级逻辑（与C库完全对齐）
    user_home, _ = _get_user_home_dir_python()
    user_home_abs = os.path.abspath(user_home).lower().replace("\\", "/")
    if not user_home_abs.endswith("/"):
        user_home_abs += "/"
    
    cmd_paths = _extract_onyx_paths_python(cmd)
    if not cmd_paths:
        return True
    
    protected_paths = PROTECTED_PATHS_CACHE or []
    for cmd_path in cmd_paths:
        cmd_path_abs = os.path.abspath(cmd_path).lower().replace("\\", "/")
        
        # 主目录内路径 → 放行
        if cmd_path_abs.startswith(user_home_abs):
            continue
        
        # 保护路径校验
        for protected in protected_paths:
            protected = protected.replace("\\", "/")
            if protected.startswith("*"):
                if cmd_path_abs.endswith(protected[1:]):
                    return False
            else:
                if cmd_path_abs == protected or cmd_path_abs.startswith(f"{protected}/"):
                    return False
    return True

def get_result_file_path() -> str:
    user_home, _ = _get_user_home_dir_python()
    result_dir = os.path.join(user_home, ".cache", "onyx")
    if not os.path.exists(result_dir):
        os.makedirs(result_dir, mode=0o755)
    return os.path.join(result_dir, "re_oppath")

# ====================== 调试工具函数（原逻辑微调，适配新路径逻辑）======================
def _print_init_info() -> None:
    print("\n" + "="*80)
    print("🔍 OPPATH模块初始化信息（依赖get_lib_path）")
    print("="*80)
    print(f"程序根目录：{ROOT_DIR}")
    print(f"用户主目录：{USER_HOME_DIR}")
    print(f"环境类型：{'✅ Termux环境' if _is_termux_env() else '✅ 非Termux环境'}")
    if _is_termux_env():
        print(f"Termux硬编码主目录：{TERMUX_HARDCODED_HOME}")
    
    # 调试信息微调：显示get_lib_path查找的C库路径
    if _is_termux_env():
        print(f"\nC库状态：❌ Termux环境默认禁用，使用Python实现")
    else:
        print(f"\nC库查找方式：✅ 依赖get_lib_path模块")
        print(f"C库实际路径：{C_LIB_PATH if C_LIB_PATH else '未找到'}")
        print(f"C库状态：{'✅ 可用' if C_LIB_AVAILABLE else '❌ 不可用'}")
    
    if C_LIB_ERROR:
        print(f"\nℹ️  C库信息：")
        print(C_LIB_ERROR)
    print(f"\n保护路径数量：{len(PROTECTED_PATHS_CACHE) if PROTECTED_PATHS_CACHE else 0}")
    print(f"路径缓存数量：{len(PATH_RESOLVE_CACHE)}")
    print("="*80 + "\n")

# ====================== 程序入口（测试用，原逻辑不变）======================
if __name__ == "__main__":
    current_script_dir = os.path.dirname(os.path.abspath(__file__))
    test_root = os.path.abspath(os.path.join(current_script_dir, "..", ".."))
    test_home = os.path.abspath(os.path.join(test_root, "home", "default"))
    init_oppath(test_root, test_home)
    
    print("📋 命令检查测试用例")
    print("="*80)
    test_cmds = [
        "ls", "cd ~", "pwd", "echo 'test'",  # 无路径参数（允许）
        "ls ~/test.txt", "touch ~/demo.txt",  # 主目录操作（允许）
        "cat onyx/etc/config.json", "cp *.key /tmp",  # 保护路径（拦截）
        "rm -rf /home/user/test", "mkdir /tmp/test"  # 主目录外非保护路径（允许）
    ]
    for idx, cmd in enumerate(test_cmds, 1):
        try:
            result = check_oppath(cmd)
            status = "✅ 允许" if result else "❌ 拦截"
        except Exception as e:
            status = f"⚠️  执行错误：{str(e)}"
        print(f"{idx}. 命令：{cmd}")
        print(f"   结果：{status}")
        if idx != len(test_cmds):
            print()
    print("\n" + "="*80)
    print("✅ 测试完成！")
