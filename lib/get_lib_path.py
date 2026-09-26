"""
get_lib_path.py - 跨平台动态库路径查找模块
支持 Termux、Linux、Windows、macOS
"""

import os
import shutil
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
    """Termux 环境搜索路径。

    2026-09 修复：旧实现只搜 Termux home / $PREFIX/lib，**完全不搜项目内 lib/c** ——
    而仓库自带 lib/c/<name>/<arch>.so。项目把库拷到 `expanduser("~")/c` 依赖 HOME
    未被重定向；一旦 HOME 指向别处（沙箱/自定义 HOME），拷贝目标与查找目标不一致，
    C 库就永远找不到。这里把项目路径放在最前，与 Linux 分支保持一致。
    """
    return [
        # 项目目录（最可信来源，优先）
        os.path.join(_PROJECT_ROOT, "lib", "c", lib_name, lib_filename),
        os.path.join(_PROJECT_ROOT, "c", lib_name, lib_filename),
        os.path.join(_SCRIPT_DIR, "c", lib_name, lib_filename),
        # Termux 运行时拷贝目录
        os.path.join(TERMUX_HOME, "c", lib_name, lib_filename),
        os.path.join(TERMUX_HOME, lib_name, lib_filename),
        os.path.join(TERMUX_PREFIX, "lib", lib_filename),
        os.path.join(TERMUX_PREFIX, "local", "lib", lib_filename),
    ]


def _get_linux_search_paths(lib_name: str, lib_filename: str) -> List[str]:
    """Linux 环境搜索路径

    2026-09 加固（H6）：不再加入 LD_LIBRARY_PATH——环境变量指向的目录可被
    同用户进程写入，植入同名 <arch>.so 即可替换权限检查库本身（DLL hijack）。
    只搜索项目目录与系统标准目录。
    """
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
    return paths


def _get_windows_search_paths(lib_name: str, lib_filename: str) -> List[str]:
    """Windows 环境搜索路径

    2026-09 加固（H6）：不再加入 PATH 目录（可被植入同名 .dll 的 DLL hijack 面），
    只搜索项目目录与系统目录。
    """
    paths = [
        os.path.join(_PROJECT_ROOT, "c", lib_name, lib_filename),
        os.path.join(_PROJECT_ROOT, "lib", "c", lib_name, lib_filename),
        os.path.join(_SCRIPT_DIR, "c", lib_name, lib_filename),
        os.path.join(_SCRIPT_DIR, lib_name, lib_filename),
    ]
    
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


# ─────────────────── Termux：外部存储上的 .so 无法 dlopen ───────────────────
# Android/Termux 的 linker namespace（"(default)"）只允许从应用私有目录映射动态库：
#   可用：/data/data/com.termux/files/home/**、/data/data/com.termux/files/usr/**
#   不可用：/storage/emulated/0/**、/sdcard/**、/mnt/**（报
#           "is not accessible for the namespace \"(default)\""）
# 而本仓库常被放在 /storage/emulated/0 下的项目目录里 → ctypes.CDLL 必然失败。
# 解决：Termux 下把私有目录之外的库文件硬拷贝到私有目录再加载（与
# bin/plugin_loader.py 的 Termux 硬拷贝策略一致）。

# 私有（可 dlopen）库目录候选，按优先级排序
_TERMUX_PRIVATE_LIB_ROOTS = (
    os.path.join(TERMUX_HOME, ".onyx", "lib"),
    os.path.join(TERMUX_PREFIX, "var", "lib", "onyx"),
    os.path.join(TERMUX_PREFIX, "tmp", "onyx-lib"),
)

_TERMUX_PRIVATE_ROOT_CACHE = None


def _termux_private_lib_root() -> Optional[str]:
    """返回一个可写、且可被 Termux linker 映射的私有目录；都不可用则 None。"""
    global _TERMUX_PRIVATE_ROOT_CACHE
    if _TERMUX_PRIVATE_ROOT_CACHE is not None:
        return _TERMUX_PRIVATE_ROOT_CACHE or None

    for root in _TERMUX_PRIVATE_LIB_ROOTS:
        try:
            os.makedirs(root, exist_ok=True)
            probe = os.path.join(root, ".onyx_write_probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("")
            os.remove(probe)
            _TERMUX_PRIVATE_ROOT_CACHE = root
            return root
        except Exception:
            continue

    _TERMUX_PRIVATE_ROOT_CACHE = ""
    return None


def _is_termux_private_path(path: str) -> bool:
    """路径是否已位于 Termux 私有（可 dlopen）目录内。"""
    try:
        real = os.path.realpath(path)
    except Exception:
        real = path
    for ok in (TERMUX_HOME, TERMUX_PREFIX):
        if real == ok or real.startswith(ok + os.sep):
            return True
    return False


def _ensure_termux_loadable(path: str) -> str:
    """Termux 下保证返回的路径可被 dlopen。

    若库文件位于私有目录之外（如 /storage/emulated/0 的项目目录），按
    「size + mtime」比对后原子拷贝到私有目录，返回副本路径；其余情况原样返回。
    拷贝失败时退回原路径（保持旧行为，绝不抛异常）。
    """
    if not path:
        return path
    try:
        if not _is_termux_environment():
            return path
        if _is_termux_private_path(path):
            return path

        root = _termux_private_lib_root()
        if not root:
            return path

        # 保留 <lib_name>/<arch>.so 的目录层级，避免同名不同库互相覆盖
        dst_dir = os.path.join(root, os.path.basename(os.path.dirname(path)) or "lib")
        dst = os.path.join(dst_dir, os.path.basename(path))

        src_stat = os.stat(path)
        need_copy = True
        try:
            dst_stat = os.stat(dst)
            need_copy = (
                dst_stat.st_size != src_stat.st_size
                or int(dst_stat.st_mtime) < int(src_stat.st_mtime)
            )
        except OSError:
            need_copy = True

        if need_copy:
            os.makedirs(dst_dir, exist_ok=True)
            tmp = f"{dst}.{os.getpid()}.tmp"
            shutil.copy2(path, tmp)
            os.chmod(tmp, 0o700)
            os.replace(tmp, dst)

        return dst if os.path.isfile(dst) else path
    except Exception:
        return path


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

    # Termux：私有目录之外的 .so 无法 dlopen → 拷贝到私有目录并返回副本
    if lib_path:
        lib_path = _ensure_termux_loadable(lib_path)

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