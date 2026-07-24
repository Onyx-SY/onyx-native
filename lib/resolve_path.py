import os
import json
import re
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

FORBIDDEN_MSG = "You cannot cross root dir"

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
    # ..  /  ~  -  ./  ../  都需要解析
    if path == "..":
        return True
    return path.startswith(("/", "~", "-", "./", "../"))


# === 特殊路径：真实存在，不转虚拟路径 ===
_SPECIAL_REAL_PATHS = frozenset({
    '/dev/null', '/dev/zero', '/dev/random', '/dev/urandom',
    '/dev/stdin', '/dev/stdout', '/dev/stderr', '/dev/fd',
    '/dev/tty', '/dev/pts',
})


def _is_all_special(s: str) -> bool:
    """检查字符串是否全由非路径字符组成（用于判断 / 前的前缀是否是 shell 元字符而非路径名）"""
    if not s:
        return True  # 空前缀 = 以 / 开头
    for ch in s:
        if ch.isalnum() or ch in '._-~+@':
            return False
    return True


def _is_special_real_path(path: str) -> bool:
    """检查是否为真实存在的特殊路径（如 /dev/null），这些路径不应被虚拟路径转换"""
    if path in _SPECIAL_REAL_PATHS:
        return True
    # /dev/ 下的子路径也保护
    if path.startswith('/dev/') and os.path.exists(path):
        return True
    return False


# 非路径字符正则：保留 0-9 a-z A-Z _ / .  其余替换为空格
_NON_PATH_RE = re.compile(r'[^a-zA-Z0-9_/.]')


def _extract_path_core(token: str) -> str:
    """
    提取 token 中的路径核心并检查是否越界。

    算法（大道至简）：
    1. 非路径字符全部替换为空格
    2. split 后取第一个 token 作为路径核心
    3. 解析核心路径，若越界返回 FORBIDDEN_MSG，否则返回原 token

    例：
        '..;ls'            → 核心 '..' → 未越界 → 返回 '..;ls'
        'cd'               → 核心 'cd' → 不需要解析 → 返回 'cd'
        '/dev/null'        → 特殊路径 → 返回 '/dev/null'
        'simple_forum/'    → 核心 'simple_forum/' → 普通相对路径不解析 → 返回原值
    """
    if not token:
        return token

    if _is_special_real_path(token):
        return token

    # 替换非路径字符为空格，取第一个片段
    cleaned = _NON_PATH_RE.sub(' ', token).strip()
    if not cleaned:
        return token

    # 如果 token 本身已是纯路径（无 shell 元字符），不做掐头去尾
    # 由 resolve_path 正常流程处理，避免递归
    if cleaned == token:
        return token

    first = cleaned.split()[0]

    # 不需要解析的：不含 / 且不是 .. 或 ./
    if '/' not in first and first != '..' and not first.startswith('./') and not first.startswith('../'):
        return token

    # 解析核心路径：若越界则返回阻断消息
    resolved = resolve_path(first)
    if resolved == FORBIDDEN_MSG:
        return FORBIDDEN_MSG

    return token


def resolve_path(path: str) -> str:
    if not path:
        return ""

    path = path.strip()

    # === 掐头去尾：提取路径核心并检查越界 ===
    # FORBIDDEN_MSG 是阻断信号，不再做路径解析
    if path == FORBIDDEN_MSG:
        return FORBIDDEN_MSG

    checked = _extract_path_core(path)
    if checked == FORBIDDEN_MSG:
        return FORBIDDEN_MSG
    if checked != path:
        return checked

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