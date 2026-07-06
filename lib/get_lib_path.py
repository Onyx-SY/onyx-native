"""
get_lib_path.py - 跨平台动态库路径查找模块
支持 Termux、Linux、Windows、macOS
"""

import os
import sys
import platform
from typing import Optional, List, Tuple
from functools import lru_cache

# 全局缓存
_LIB_PATH_CACHE: dict = {}

# Termux 硬编码路径（仅在 Termux 环境使用）
TERMUX_HOME = "/data/data/com.termux/files/home"
TERMUX_PREFIX = "/data/data/com.termux/files/usr"

# 当前脚本所在目录
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 项目根目录（假设脚本在 src/lib/ 下，可根据实际情况调整）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR)) if _SCRIPT_DIR.endswith("lib") else _SCRIPT_DIR

# 系统信息缓存
_SYSTEM_ARCH = None
_LIB_SUFFIX = None
_IS_TERMUX = None


def _is_termux_environment() -> bool:
    """检测是否为 Termux 环境"""
    global _IS_TERMUX
    if _IS_TERMUX is not None:
        return _IS_TERMUX
    
    # 检查 Termux 特有路径
    if os.path.exists(TERMUX_HOME) and os.path.exists(TERMUX_PREFIX):
        _IS_TERMUX = True
        return True
    
    # 检查 sys.prefix
    if "termux" in sys.prefix.lower():
        _IS_TERMUX = True
        return True
    
    _IS_TERMUX = False
    return False


def _get_system_arch() -> str:
    """获取系统架构（统一格式）"""
    global _SYSTEM_ARCH
    if _SYSTEM_ARCH:
        return _SYSTEM_ARCH
    
    machine = platform.machine().lower()
    
    arch_map = {
        "x86_64": "x64", "amd64": "x64",
        "aarch64": "arm64", "arm64": "arm64",
        "armv7l": "arm32", "armv8l": "arm32",
        "i386": "x86", "i686": "x86",
    }
    
    # Windows 特殊处理
    if sys.platform.startswith("win32"):
        if machine.endswith("64"):
            _SYSTEM_ARCH = "x64"
        else:
            _SYSTEM_ARCH = "x86"
    else:
        _SYSTEM_ARCH = arch_map.get(machine, machine)
    
    return _SYSTEM_ARCH


def _get_lib_suffix() -> str:
    """获取动态库后缀"""
    global _LIB_SUFFIX
    if _LIB_SUFFIX:
        return _LIB_SUFFIX
    
    if sys.platform.startswith("win32"):
        _LIB_SUFFIX = ".dll"
    elif sys.platform.startswith("darwin"):
        _LIB_SUFFIX = ".dylib"
    else:
        _LIB_SUFFIX = ".so"
    return _LIB_SUFFIX


def _get_termux_search_paths(lib_name: str, lib_filename: str) -> List[str]:
    """Termux 环境搜索路径"""
    return [
        os.path.join(TERMUX_HOME, "c", lib_name, lib_filename),
        os.path.join(TERMUX_HOME, lib_name, lib_filename),
        os.path.join(TERMUX_PREFIX, "lib", lib_filename),
        os.path.join(TERMUX_PREFIX, "local", "lib", lib_filename),
    ]


def _get_linux_search_paths(lib_name: str, lib_filename: str) -> List[str]:
    """Linux 环境搜索路径"""
    paths = [
        # 项目目录
        os.path.join(_PROJECT_ROOT, "c", lib_name, lib_filename),
        os.path.join(_PROJECT_ROOT, "lib", "c", lib_name, lib_filename),
        # 脚本目录
        os.path.join(_SCRIPT_DIR, "c", lib_name, lib_filename),
        os.path.join(_SCRIPT_DIR, lib_name, lib_filename),
        # 系统目录
        os.path.join("/usr/lib", lib_filename),
        os.path.join("/usr/local/lib", lib_filename),
        os.path.join("/lib", lib_filename),
    ]
    
    # 环境变量
    ld_path = os.environ.get("LD_LIBRARY_PATH", "")
    if ld_path:
        paths.extend(ld_path.split(":"))
    
    return paths


def _get_windows_search_paths(lib_name: str, lib_filename: str) -> List[str]:
    """Windows 环境搜索路径"""
    paths = [
        os.path.join(_PROJECT_ROOT, "c", lib_name, lib_filename),
        os.path.join(_PROJECT_ROOT, "lib", "c", lib_name, lib_filename),
        os.path.join(_SCRIPT_DIR, "c", lib_name, lib_filename),
        os.path.join(_SCRIPT_DIR, lib_name, lib_filename),
    ]
    
    # PATH 环境变量
    for p in os.environ.get("PATH", "").split(";"):
        if p:
            paths.append(os.path.join(p, lib_filename))
    
    # Windows 系统目录
    system_root = os.environ.get("SystemRoot", "C:\\Windows")
    paths.append(os.path.join(system_root, "System32", lib_filename))
    
    return paths


def _get_macos_search_paths(lib_name: str, lib_filename: str) -> List[str]:
    """macOS 环境搜索路径"""
    paths = [
        os.path.join(_PROJECT_ROOT, "c", lib_name, lib_filename),
        os.path.join(_PROJECT_ROOT, "lib", "c", lib_name, lib_filename),
        os.path.join(_SCRIPT_DIR, "c", lib_name, lib_filename),
        os.path.join(_SCRIPT_DIR, lib_name, lib_filename),
        os.path.join("/usr/local/lib", lib_filename),
        os.path.join("/usr/lib", lib_filename),
    ]
    
    # 环境变量
    dyld_path = os.environ.get("DYLD_LIBRARY_PATH", "")
    if dyld_path:
        paths.extend(dyld_path.split(":"))
    
    return paths


def _get_normal_search_paths(lib_name: str, lib_filename: str) -> List[str]:
    """非 Termux 环境搜索路径（根据系统选择）"""
    if sys.platform.startswith("win32"):
        return _get_windows_search_paths(lib_name, lib_filename)
    elif sys.platform.startswith("darwin"):
        return _get_macos_search_paths(lib_name, lib_filename)
    else:  # Linux 和其他 Unix-like
        return _get_linux_search_paths(lib_name, lib_filename)


@lru_cache(maxsize=256)
def _file_exists(path: str) -> bool:
    """带缓存的路径存在检查"""
    return os.path.exists(path) and os.path.isfile(path)


def _find_lib_file(lib_name: str, lib_filename: str) -> Optional[str]:
    """查找库文件"""
    # 根据环境选择搜索路径
    if _is_termux_environment():
        search_paths = _get_termux_search_paths(lib_name, lib_filename)
    else:
        search_paths = _get_normal_search_paths(lib_name, lib_filename)
    
    # 去重并查找
    seen = set()
    for path in search_paths:
        if path in seen:
            continue
        seen.add(path)
        
        if _file_exists(path):
            return path
    
    return None


def get_lib_path(lib_name: str) -> Optional[str]:
    """
    获取动态库文件的绝对路径
    
    支持平台：
        - Termux (Android)
        - Linux
        - Windows
        - macOS
    
    搜索顺序（以 Linux 为例）：
        1. 项目根目录/c/{lib_name}/{arch}.so
        2. 项目根目录/lib/c/{lib_name}/{arch}.so
        3. 脚本目录/c/{lib_name}/{arch}.so
        4. 脚本目录/{lib_name}/{arch}.so
        5. /usr/lib/{arch}.so
        6. /usr/local/lib/{arch}.so
        7. /lib/{arch}.so
        8. LD_LIBRARY_PATH 中的目录
    
    :param lib_name: 库名称（如 "resolve_path"）
    :return: 库文件绝对路径，未找到返回 None
    """
    if not lib_name or not isinstance(lib_name, str):
        return None
    
    lib_name = lib_name.strip()
    if not lib_name:
        return None
    
    # 检查缓存
    if lib_name in _LIB_PATH_CACHE:
        cached = _LIB_PATH_CACHE[lib_name]
        if cached and _file_exists(cached):
            return cached
        del _LIB_PATH_CACHE[lib_name]
    
    # 构建文件名
    arch = _get_system_arch()
    suffix = _get_lib_suffix()
    lib_filename = f"{arch}{suffix}"
    
    # 查找
    lib_path = _find_lib_file(lib_name, lib_filename)
    
    # 缓存结果
    if lib_path:
        _LIB_PATH_CACHE[lib_name] = lib_path
    
    return lib_path


def get_lib_path_with_fallback(lib_name: str, fallback_name: Optional[str] = None) -> Optional[str]:
    """获取库路径，支持备用名称"""
    path = get_lib_path(lib_name)
    if path:
        return path
    if fallback_name:
        return get_lib_path(fallback_name)
    return None


def get_available_libs(lib_names: List[str]) -> List[Tuple[str, str]]:
    """批量获取多个库的路径"""
    return [(name, get_lib_path(name)) for name in lib_names if get_lib_path(name)]


def clear_lib_cache() -> None:
    """清除缓存"""
    global _LIB_PATH_CACHE
    _LIB_PATH_CACHE.clear()
    _file_exists.cache_clear()


def get_lib_info(lib_name: str) -> dict:
    """获取库详细信息（调试用）"""
    info = {
        "lib_name": lib_name,
        "path": None,
        "exists": False,
        "size": None,
        "arch": _get_system_arch(),
        "suffix": _get_lib_suffix(),
        "is_termux": _is_termux_environment(),
        "platform": sys.platform,
        "error": None
    }
    
    try:
        lib_path = get_lib_path(lib_name)
        if lib_path and os.path.exists(lib_path):
            info["path"] = lib_path
            info["exists"] = True
            info["size"] = os.path.getsize(lib_path)
        else:
            info["error"] = "库文件不存在"
    except Exception as e:
        info["error"] = str(e)
    
    return info