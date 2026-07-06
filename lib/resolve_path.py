import os
import json
from typing import Dict, Tuple, Optional
import time

from lib.get_lib_path import get_lib_path

ROOT_DIR = ""
USER_HOME_DIR = ""
PATH_CACHE_TTL = 1800
PATH_INDEX_MSG_PATH = ""

C_LIB = None
C_LIB_AVAILABLE = False
C_LIB_PATH = ""

FORBIDDEN_MSG = "You can't cross /"

PATH_RESOLVE_CACHE: Dict[str, Tuple[str, float, str]] = {}
PATH_RESOLVE_CACHE_MAX = 1000  # 硬上限，防止内存泄漏

def _add_to_path_cache(path: str, resolved: str, ts: float, cwd: str) -> None:
    """添加路径缓存条目，超限时淘汰最旧的"""
    if len(PATH_RESOLVE_CACHE) >= PATH_RESOLVE_CACHE_MAX:
        # 淘汰最旧的 200 条（批量淘汰减少开销）
        oldest = sorted(PATH_RESOLVE_CACHE.items(), key=lambda x: x[1][1])[:200]
        for old_key, _ in oldest:
            del PATH_RESOLVE_CACHE[old_key]
    PATH_RESOLVE_CACHE[path] = (resolved, ts, cwd)

PERM_RULES_JSON = ""  # 存储规则字符串，用于传递给C库

PERM_PATH_FILE = "/onyx/etc/perm_path.json"


def init_resolve_path(root_dir: str, user_home_dir: str):
    global ROOT_DIR, USER_HOME_DIR, PERM_RULES_JSON
    ROOT_DIR = os.path.realpath(root_dir)
    USER_HOME_DIR = os.path.realpath(user_home_dir)
    PERM_RULES_JSON = _build_perm_rules_json()
    _load_c_library()
    if C_LIB_AVAILABLE:
        _set_c_perm_rules()
    _load_path_cache()


def _build_perm_rules_json() -> str:
    """构建规则JSON字符串，格式：每行 pattern:depth"""
    if not os.path.exists(PERM_PATH_FILE):
        return ""
    
    rules_lines = []
    try:
        with open(PERM_PATH_FILE) as f:
            data = json.load(f)
        
        for pattern, value in data.items():
            depth = 0
            if "<*:" in pattern:
                try:
                    depth = int(pattern.split("<*:")[1].split(">")[0])
                except (IndexError, ValueError):
                    pass
            
            # 处理pattern中的特殊字符
            pattern = pattern.replace("<*:0>", "").replace("<*:1>", "").replace("<*:2>", "")
            
            full_pattern = os.path.join(ROOT_DIR, pattern.lstrip('/'))
            rules_lines.append(f"{full_pattern}:{depth}")
    except Exception:
        pass
    
    return "\n".join(rules_lines)


def _set_c_perm_rules():
    """将规则传递给C库"""
    if not C_LIB_AVAILABLE:
        return
    try:
        from ctypes import c_char_p
        C_LIB.set_perm_rules.argtypes = [c_char_p]
        C_LIB.set_perm_rules(PERM_RULES_JSON.encode())
    except Exception:
        pass


def _load_c_library():
    global C_LIB, C_LIB_AVAILABLE
    C_LIB_PATH = get_lib_path("resolve_path")
    if not C_LIB_PATH:
        return
    try:
        from ctypes import CDLL, c_char_p
        C_LIB = CDLL(C_LIB_PATH)
        C_LIB.resolve_path.argtypes = [c_char_p, c_char_p, c_char_p, c_char_p]
        C_LIB.resolve_path.restype = c_char_p
        # 添加set_perm_rules函数
        try:
            C_LIB.set_perm_rules.argtypes = [c_char_p]
            C_LIB.set_perm_rules.restype = None
        except AttributeError:
            pass
        C_LIB_AVAILABLE = True
    except Exception as e:
        print(f"Failed to load C library: {e}")
        C_LIB_AVAILABLE = False


def _load_path_cache():
    global PATH_RESOLVE_CACHE
    if not PATH_INDEX_MSG_PATH or not os.path.exists(PATH_INDEX_MSG_PATH):
        return
    try:
        import msgpack
        with open(PATH_INDEX_MSG_PATH, "rb") as f:
            PATH_RESOLVE_CACHE.update(msgpack.load(f, raw=False))
    except Exception:
        pass


def _is_root_overlap() -> bool:
    return os.path.realpath("/") == os.path.realpath(ROOT_DIR)


def _is_in_virtual_root(path: str) -> bool:
    root = os.path.realpath(ROOT_DIR)
    target = os.path.realpath(path)
    return target == root or target.startswith(root + os.sep)


def _match_perm_rule(path: str) -> Optional[str]:
    """在Python层匹配规则（作为fallback）"""
    if not PERM_RULES_JSON:
        return None
    
    for line in PERM_RULES_JSON.split('\n'):
        if not line:
            continue
        parts = line.split(':')
        if len(parts) != 2:
            continue
        pattern, depth_str = parts
        depth = int(depth_str)
        
        if depth > 0:
            cnt = path.count("/")
            if cnt != depth + 1:
                continue
        if path.startswith(pattern):
            return path
    return None


def _should_resolve(path: str) -> bool:
    if not path or path == ".":
        return False
    if path.startswith("/") and _is_in_virtual_root(path):
        return False
    return path.startswith(("/", "~", "-", "./", "../"))


def resolve_path(path: str) -> str:
    if not path:
        return ""

    path = path.strip()

    if _is_root_overlap():
        return path

    # 检查缓存
    if path in PATH_RESOLVE_CACHE:
        cached, ts, cwd = PATH_RESOLVE_CACHE[path]
        if time.time() - ts < PATH_CACHE_TTL and cwd == os.getcwd():
            return cached

    # Python层规则匹配（快速检查）
    matched = _match_perm_rule(path)
    if matched:
        resolved = os.path.realpath(matched)
        if not _is_in_virtual_root(resolved):
            return FORBIDDEN_MSG
        _add_to_path_cache(path, resolved, time.time(), os.getcwd())
        return resolved

    if not _should_resolve(path):
        return path

    # 优先使用C库
    if C_LIB_AVAILABLE:
        try:
            res = C_LIB.resolve_path(
                path.encode(),
                ROOT_DIR.encode(),
                USER_HOME_DIR.encode(),
                os.getcwd().encode()
            )
            if res:
                decoded = res.decode()
                # C库已经做过权限检查，但为了安全再验证一次
                if not _is_in_virtual_root(decoded) and decoded != FORBIDDEN_MSG:
                    return FORBIDDEN_MSG
                _add_to_path_cache(path, decoded, time.time(), os.getcwd())
                return decoded
        except Exception as e:
            print(f"C library resolve failed: {e}")

    # Python fallback解析
    resolved = _resolve_path_python_fallback(path)

    if not _is_in_virtual_root(resolved) and resolved != FORBIDDEN_MSG:
        return FORBIDDEN_MSG

    _add_to_path_cache(path, resolved, time.time(), os.getcwd())
    return resolved


def _resolve_path_python_fallback(path: str) -> str:
    """Python实现的路径解析fallback"""
    if path == "/":
        base = ROOT_DIR
    elif path == "~":
        base = USER_HOME_DIR
    elif path == "-":
        oldpwd = os.environ.get("OLDPWD", USER_HOME_DIR)
        base = oldpwd
    elif path.startswith("~/"):
        base = USER_HOME_DIR
        path = path[2:]
    elif path.startswith("/"):
        base = ROOT_DIR
        path = path[1:]
    else:
        base = os.getcwd()
    
    resolved = os.path.realpath(os.path.join(base, path))
    
    if not _is_in_virtual_root(resolved):
        return FORBIDDEN_MSG
    
    return resolved


def reload_perm_rules():
    """重新加载权限规则"""
    global PERM_RULES_JSON
    PERM_RULES_JSON = _build_perm_rules_json()
    if C_LIB_AVAILABLE:
        _set_c_perm_rules()


def clear_cache():
    """清除路径解析缓存"""
    global PATH_RESOLVE_CACHE
    PATH_RESOLVE_CACHE.clear()