#lib.makecache.py
import os
import time
import msgpack
import uuid
from typing import Dict, List, Tuple, Any, Optional, Callable
from pathlib import Path
# 引入get_lib_path模块（按实际目录结构调整，核心依赖）
from lib.get_lib_path import get_lib_path, get_available_libs, clear_lib_cache, get_lib_info

# 缓存相关全局常量（与Onyx.py保持一致，新增库缓存TTL）
PATH_CACHE_TTL = 1800  # 30分钟
CMD_CACHE_TTL = 3600  # 1小时
DIR_CACHE_TTL = 1800  # 30分钟
LIB_CACHE_TTL = 3600   # 1小时（动态库路径缓存有效期）
DIR_CACHE_MAX_FILES = 100
PATH_SCAN_DEPTH = 10
# 动态库缓存默认路径（与其他缓存同目录）
DEFAULT_LIB_CACHE_PATH = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "lib_path_cache.msgpack")

def save_msgpack(path: str, data: Any) -> bool:
    """保存数据到Msgpack文件，自动创建父目录"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            msgpack.dump(data, f, use_bin_type=True)
        return True
    except Exception as e:
        return False

def load_msgpack(path: str) -> Optional[Any]:
    """从Msgpack文件加载数据，失败返回None并清理损坏文件"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return msgpack.load(f, raw=False)
    except Exception as e:
        if os.path.exists(path):
            os.remove(path)
        return None

def build_cmd_mapping_cache(
    request_id: str,
    sys_type: str,
    BUILTIN_COMMANDS: Dict[str, Any],
    current_sys_cmds: Dict[str, List[str]],
    TOOL_INDEX_CACHE: Dict[str, Any],
    CMD_MAPPING_MSG_PATH: str,
    log_info: Callable[[str, str], None]
) -> None:
    """构建命令映射缓存（迁移自Onyx.py核心逻辑，无改动）"""
    current_time = time.time()
    cmd_mapping = {"builtins": {}, "tools": {}, "system": []}
    # 缓存内置命令
    for cmd_name, cmd_func in BUILTIN_COMMANDS.items():
        cmd_mapping["builtins"][cmd_name.lower()] = cmd_func.__name__
    log_info(f"缓存内置命令：{len(cmd_mapping['builtins'])}个", request_id)
    # 缓存工具箱工具
    for cache_key, tool_info in TOOL_INDEX_CACHE.items():
        if sys_type == "Windows":
            tool_name = cache_key.split(f"_{sys_type}")[0].lower()
        else:
            tool_name = cache_key.split(f"_{sys_type}")[0] if f"_{sys_type}" in cache_key else cache_key
        cmd_mapping["tools"][tool_name] = {
            "path": tool_info.path,
            "perm": tool_info.tool_perm,
            "type": tool_info.tool_type
        }
    log_info(f"缓存工具箱工具：{len(cmd_mapping['tools'])}个", request_id)
    # 缓存系统命令 — 优先保留已有的扫描结果，防止 build_cmd_mapping_cache
    # 用 current_sys_cmds（硬编码列表）覆盖掉后台扫描产出的完整命令列表
    system_cmds = current_sys_cmds.get(sys_type, [])
    existing = load_msgpack(CMD_MAPPING_MSG_PATH)
    if existing and sys_type in existing:
        existing_system = existing[sys_type].get("mapping", {}).get("system", [])
        if len(existing_system) > len(system_cmds):
            system_cmds = existing_system
    cmd_mapping["system"] = system_cmds
    log_info(f"缓存系统命令：{len(cmd_mapping['system'])}个（无过滤）", request_id)
    # 持久化缓存 — 只更新当前 sys_type，保留其他 sys_type 的数据
    serializable_mapping = existing if existing else {}
    serializable_mapping[sys_type] = {"mapping": cmd_mapping, "cache_time": current_time}
    if save_msgpack(CMD_MAPPING_MSG_PATH, serializable_mapping):
        log_info(f"缓存保存完成：{CMD_MAPPING_MSG_PATH}", request_id)

def load_cmd_mapping_cache(
    request_id: str,
    sys_type: str,
    CMD_MAPPING_MSG_PATH: str,
    CMD_CACHE_TTL: int,
    log_info: Callable[[str, str], None]
) -> Optional[Dict[str, Any]]:
    """加载命令映射缓存（迁移自Onyx.py核心逻辑，无改动）"""
    current_time = time.time()
    cached_data = load_msgpack(CMD_MAPPING_MSG_PATH)
    if not cached_data or sys_type not in cached_data:
        log_info("命令映射缓存不存在，将新建缓存", request_id)
        return None
    sys_cache = cached_data[sys_type]
    if (current_time - sys_cache["cache_time"] > CMD_CACHE_TTL) or "mapping" not in sys_cache:
        log_info("命令映射缓存过期或格式非法，重新构建", request_id)
        return None
    log_info(
        f"加载命令映射缓存：{len(sys_cache['mapping']['builtins'])}个内置命令，"
        f"{len(sys_cache['mapping']['system'])}个系统命令，{len(sys_cache['mapping']['tools'])}个工具",
        request_id
    )
    return sys_cache

def load_directory_cache(
    DIR_CACHE_MSG_PATH: str,
    DIR_CACHE_TTL: int,
    DIR_CACHE_MAX_FILES: int,
    log_info: Callable[[str, str], None],
    request_id: str = str(uuid.uuid4())
) -> Dict[str, Tuple[List[Dict[str, Any]], float, int]]:
    """加载目录缓存（迁移自Onyx.py核心逻辑，无改动）"""
    current_time = time.time()
    cached_dirs = load_msgpack(DIR_CACHE_MSG_PATH)
    valid_cache = {}
    if not cached_dirs or not isinstance(cached_dirs, dict):
        log_info(f"目录缓存文件不存在或格式非法：{DIR_CACHE_MSG_PATH}", request_id)
        return {}
    for dir_path, (file_entries, cache_time, file_count) in cached_dirs.items():
        if (current_time - cache_time < DIR_CACHE_TTL and
            os.path.isdir(dir_path) and
            file_count < DIR_CACHE_MAX_FILES):
            valid_cache[dir_path] = (file_entries, cache_time, file_count)
    log_info(f"目录缓存加载完成：共{len(valid_cache)}个有效目录（来源：{DIR_CACHE_MSG_PATH}）", request_id)
    return valid_cache

def cache_directory_files(
    dir_path: str,
    request_id: str,
    ROOT_DIR: str,
    PATH_SCAN_DEPTH: int,
    DIR_CACHE_MAX_FILES: int,
    DIR_CACHE_MSG_PATH: str,
    log_info: Callable[[str, str], None],
    log_warning: Callable[[str, str], None],
    log_error: Callable[[str, str], None]
) -> None:
    """缓存目录文件信息（迁移自Onyx.py核心逻辑，无改动）"""
    current_time = time.time()
    if not os.path.isdir(dir_path):
        log_warning(f"缓存目录失败：{dir_path} 不是有效目录", request_id)
        return
    # 校验目录深度
    try:
        root_abs = os.path.abspath(ROOT_DIR)
        dir_abs = os.path.abspath(dir_path)
        rel_path = os.path.relpath(dir_abs, root_abs)
        dir_depth = len([p for p in rel_path.split(os.sep) if p.strip()])
        if dir_depth > PATH_SCAN_DEPTH:
            log_info(f"目录{dir_path}深度{dir_depth}超过限制（{PATH_SCAN_DEPTH}级），不缓存", request_id)
            return
    except ValueError:
        log_info(f"目录{dir_path}不在程序根目录内，不缓存", request_id)
        return
    # 读取目录文件
    file_entries = []
    file_count = 0
    max_scan_files = 200
    try:
        for entry_name in os.listdir(dir_path):
            if file_count >= max_scan_files:
                log_info(f"目录{dir_path}文件数量超过{max_scan_files}，停止扫描", request_id)
                break
            entry_phys_path = os.path.join(dir_path, entry_name)
            entry_info = {
                "name": entry_name,
                "is_file": os.path.isfile(entry_phys_path),
                "is_link": os.path.islink(entry_phys_path)
            }
            try:
                stat_info = os.stat(entry_phys_path)
                entry_info["size"] = stat_info.st_size if entry_info["is_file"] else 4096
                entry_info["mtime"] = stat_info.st_mtime
                entry_info["inode"] = stat_info.st_ino
                entry_info["mode"] = stat_info.st_mode
                entry_info["nlink"] = stat_info.st_nlink
            except Exception as e:
                entry_info["size"] = 0
                entry_info["mtime"] = 0
                entry_info["inode"] = 0
                entry_info["mode"] = 0
                entry_info["nlink"] = 0
                log_warning(f"获取{entry_name}属性失败：{str(e)}", request_id)
            if entry_info["is_file"] and not entry_info["is_link"]:
                file_count += 1
            file_entries.append(entry_info)
        # 缓存文件数量<100的目录
        if file_count < DIR_CACHE_MAX_FILES:
            DIR_FILE_CACHE = load_directory_cache(DIR_CACHE_MSG_PATH, DIR_CACHE_TTL, DIR_CACHE_MAX_FILES, log_info, request_id)
            if dir_path in DIR_FILE_CACHE:
                del DIR_FILE_CACHE[dir_path]
            DIR_FILE_CACHE[dir_path] = (file_entries, current_time, file_count)
            # 异步保存
            import threading
            threading.Thread(
                target=save_msgpack,
                args=(DIR_CACHE_MSG_PATH, DIR_FILE_CACHE),
                daemon=True
            ).start()
            log_info(f"目录缓存成功：{dir_path}（深度{dir_depth}级，{file_count}个文件）", request_id)
        else:
            log_info(f"目录{dir_path}文件数量{file_count}≥{DIR_CACHE_MAX_FILES}，不缓存", request_id)
    except PermissionError:
        log_error(f"缓存目录{dir_path}失败：权限不足", request_id)
    except Exception as e:
        log_error(f"缓存目录{dir_path}异常：{str(e)}", request_id)

# ====================== 新增：动态库路径缓存逻辑（基于get_lib_path）======================
def build_lib_path_cache(
    request_id: str,
    lib_names: List[str],
    log_info: Callable[[str, str], None],
    log_warning: Callable[[str, str], None],
    lib_cache_path: str = DEFAULT_LIB_CACHE_PATH
) -> None:
    """
    构建动态库路径缓存（基于get_lib_path）
    :param lib_names: 需要缓存的库名称列表
    :param lib_cache_path: 库缓存文件保存路径
    :param log_info/log_warning: 日志函数
    """
    current_time = time.time()
    # 清空原有内存缓存，重新获取最新库路径
    clear_lib_cache()
    # 获取可用库路径
    available_libs = get_available_libs(lib_names)
    # 构建缓存数据：包含库信息+缓存时间
    lib_cache = {
        "cache_time": current_time,
        "libs": {},
        "system_info": get_lib_info("")  # 获取系统架构/环境信息，无实际库名
    }
    # 填充库详细信息
    for lib_name, lib_path in available_libs:
        lib_cache["libs"][lib_name] = get_lib_info(lib_name)
    # 缓存未找到的库，便于排查
    for lib_name in lib_names:
        if lib_name not in lib_cache["libs"]:
            lib_cache["libs"][lib_name] = {"lib_name": lib_name, "exists": False, "error": "库文件未找到"}
            log_warning(f"库{lib_name}未找到，已记录到缓存", request_id)
    # 持久化缓存
    if save_msgpack(lib_cache_path, lib_cache):
        log_info(f"动态库路径缓存构建完成：{len(available_libs)}个可用库，保存至{lib_cache_path}", request_id)
    else:
        log_warning(f"动态库路径缓存保存失败：{lib_cache_path}", request_id)

def load_lib_path_cache(
    request_id: str,
    log_info: Callable[[str, str], None],
    log_warning: Callable[[str, str], None],
    lib_cache_path: str = DEFAULT_LIB_CACHE_PATH,
    lib_ttl: int = LIB_CACHE_TTL
) -> Optional[Dict[str, Any]]:
    """
    加载动态库路径缓存，校验有效期和合法性
    :return: 有效缓存字典/None（过期/非法）
    """
    current_time = time.time()
    cached_data = load_msgpack(lib_cache_path)
    # 校验缓存是否存在
    if not cached_data or not isinstance(cached_data, dict):
        log_info(f"动态库路径缓存不存在或格式非法：{lib_cache_path}", request_id)
        return None
    # 校验缓存是否过期
    if "cache_time" not in cached_data or (current_time - cached_data["cache_time"] > lib_ttl):
        log_info(f"动态库路径缓存过期（TTL：{lib_ttl}s），将重新构建", request_id)
        return None
    # 校验核心字段
    if "libs" not in cached_data or not isinstance(cached_data["libs"], dict):
        log_warning(f"动态库路径缓存无有效库信息，将重新构建", request_id)
        return None
    # 统计可用库数量
    available_count = sum(1 for lib_info in cached_data["libs"].values() if lib_info.get("exists", False))
    log_info(f"加载动态库路径缓存成功：{available_count}个可用库，共{len(cached_data['libs'])}个库（来源：{lib_cache_path}）", request_id)
    return cached_data

def update_lib_path_cache(
    request_id: str,
    lib_names: List[str],
    log_info: Callable[[str, str], None],
    log_warning: Callable[[str, str], None],
    lib_cache_path: str = DEFAULT_LIB_CACHE_PATH
) -> None:
    """
    更新动态库路径缓存（增量更新，仅刷新指定库）
    :param lib_names: 需要增量更新的库名称列表
    """
    current_time = time.time()
    # 加载原有缓存
    old_cache = load_msgpack(lib_cache_path) or {"cache_time": current_time, "libs": {}, "system_info": get_lib_info("")}
    # 清空内存缓存，重新获取指定库最新信息
    clear_lib_cache()
    for lib_name in lib_names:
        lib_info = get_lib_info(lib_name)
        old_cache["libs"][lib_name] = lib_info
        if lib_info.get("exists", False):
            log_info(f"增量更新库缓存：{lib_name} -> {lib_info['path']}", request_id)
        else:
            log_warning(f"增量更新库缓存失败：{lib_name} 未找到", request_id)
    # 更新缓存时间，重新保存
    old_cache["cache_time"] = current_time
    if save_msgpack(lib_cache_path, old_cache):
        log_info(f"动态库路径缓存增量更新完成，保存至{lib_cache_path}", request_id)
    else:
        log_warning(f"动态库路径缓存增量更新保存失败：{lib_cache_path}", request_id)
