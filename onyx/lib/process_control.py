import os
import sys
import time
import msgpack
import subprocess
import ctypes
import shutil
import threading
from typing import Dict, List, Tuple, Optional, Callable
from pathlib import Path
# 引入get_lib_path模块（按实际目录结构调整，核心依赖）
from lib.get_lib_path import get_lib_path, _is_termux_environment

# 全局状态变量（通过初始化函数从Onyx.py注入）
PROCESS_CACHE: Dict[int, Tuple[float, str, str]] = {}  # PID: (启动时间, 请求ID, 命令)
PROCESS_CACHE_TTL = 3600  # 进程缓存过期时间（1小时）
ROOT_DIR = ""
USER_HOME_DIR = ""
PROCESS_CACHE_PATH = ""
MAX_PROCESS_COUNT = 10  # 最大进程数限制
PROCESS_LOCK = None  # 进程列表锁（从Onyx.py注入）
# C动态库相关全局变量（全部初始化默认值）
C_LIB = None
C_LIB_AVAILABLE = False
C_LIB_PATH = ""
C_LIB_ERROR = ""  # 关键修复：初始化空字符串，避免未关联值错误
# 新增：控制日志输出
IS_LIBRARY_MODE = True  # 作为库使用时设为True，调试时设为False
try:
    from ctypes import CDLL, c_int, c_char_p, c_float, c_bool, POINTER, Structure
except ImportError:
    C_LIB_AVAILABLE = False
    C_LIB_ERROR = "未导入ctypes模块（Python标准库缺失，建议重装Python）"

# 定义C语言结构体（与C动态库对应，原逻辑不变）
class ProcessInfo(Structure):
    _fields_ = [
        ("pid", c_int),
        ("start_time", c_float),
        ("request_id", c_char_p),
        ("command", c_char_p)
    ]
class ProcessControlConfig(Structure):
    _fields_ = [
        ("root_dir", c_char_p),
        ("user_home_dir", c_char_p),
        ("max_process_count", c_int),
        ("cache_ttl", c_int)
    ]

def init_process_control(
    root_dir: str,
    user_home_dir: str,
    process_cache_path: str,
    max_process_count: int = 10,
    process_lock: Optional[Callable] = None,
    cache_ttl: int = 3600
) -> None:
    """初始化进程管理模块（从Onyx.py调用）"""
    global ROOT_DIR, USER_HOME_DIR, PROCESS_CACHE_PATH, MAX_PROCESS_COUNT, PROCESS_LOCK, PROCESS_CACHE_TTL
    ROOT_DIR = os.path.normpath(os.path.realpath(root_dir))
    USER_HOME_DIR = os.path.normpath(os.path.realpath(user_home_dir))
    PROCESS_CACHE_PATH = process_cache_path
    MAX_PROCESS_COUNT = max_process_count
    PROCESS_LOCK = process_lock or threading.Lock()
    PROCESS_CACHE_TTL = cache_ttl
    # 初始化C库（确保异常不中断流程）
    try:
        _diagnose_c_lib_availability()
        _load_c_library()
    except Exception as e:
        global C_LIB_ERROR
        C_LIB_ERROR = f"C库初始化流程异常：{str(e)}"
    
    # 加载缓存（无论C库是否可用，都要初始化缓存）
    try:
        _load_process_cache()
    except Exception as e:
        global PROCESS_CACHE
        PROCESS_CACHE = {}
        if not C_LIB_ERROR:
            C_LIB_ERROR = f"进程缓存加载失败：{str(e)}"

def _get_termux_home() -> str:
    """获取Termux主目录（原逻辑不变）"""
    termux_home = "/data/data/com.termux/files/home"
    if _is_termux_environment():
        return termux_home
    else:
        return os.path.expanduser("~")

def _run_command(cmd: str) -> Tuple[str, str, int]:
    """执行系统命令，返回stdout、stderr、返回码（原逻辑不变）"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            text=True
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), -1

def _diagnose_c_lib_availability() -> None:
    """惰性诊断：先尝试 CDLL 加载，成功则跳过子进程诊断"""
    global C_LIB_ERROR, C_LIB_PATH
    C_LIB_ERROR = ""  # 重置错误信息
    
    # 核心：调用get_lib_path获取process_control库路径（库名称与模块一致）
    C_LIB_PATH = get_lib_path("process_control")
    if not C_LIB_PATH:
        # 未找到库，拼接友好错误信息
        C_LIB_ERROR = f"未找到process_control C动态库\n"
        C_LIB_ERROR += f"查找方式：通过get_lib_path跨平台查找（支持Termux/Windows/Linux/macOS）\n"
        C_LIB_ERROR += f"解决方案：将对应系统+架构的库文件放入get_lib_path的搜索路径（如脚本目录/c/process_control/）"
        return
    
    # --- 先尝试直接加载，成功则跳过所有诊断子进程 ---
    _cdll_load_error = None
    try:
        temp_lib = CDLL(C_LIB_PATH)
        required_funcs = ['init_process_control', 'add_process', 'remove_process', 
                         'check_process_alive', 'kill_process', 'clear_stale_processes',
                         'get_running_processes', 'free_process_list']
        found_funcs = [f for f in required_funcs if hasattr(temp_lib, f)]
        if found_funcs and not IS_LIBRARY_MODE:
            print(f"✅ C库可加载，找到函数: {', '.join(found_funcs)}")
        del temp_lib
        return  # 成功 — 跳过所有诊断
    except OSError as e:
        _cdll_load_error = e
    except Exception as e:
        _cdll_load_error = e
    
    # --- CDLL 失败，逐步诊断原因（仅在失败时跑子进程） ---
    error_msg = str(_cdll_load_error)
    C_LIB_ERROR = f"C库加载失败：{error_msg}"
    if 'wrong ELF class' in error_msg:
        arch = os.uname().machine.lower() if hasattr(os, 'uname') else 'unknown'
        C_LIB_ERROR += f"\n架构不匹配（当前系统架构: {arch}）"
    elif 'cannot open shared object file' in error_msg:
        if _is_termux_environment():
            C_LIB_ERROR += f"\n在Termux中可能需要: pkg install binutils"
        C_LIB_ERROR += f"\n库文件路径: {C_LIB_PATH}"
    elif 'invalid ELF header' in error_msg:
        C_LIB_ERROR += f"\n文件可能损坏，请重新编译C库"
    
    # 1. 检查文件权限
    try:
        import stat
        file_stat = os.stat(C_LIB_PATH)
        if sys.platform != "win32" and not (file_stat.st_mode & stat.S_IRUSR):
            C_LIB_ERROR += f"\nC库文件无读权限：{C_LIB_PATH}\n解决方案：chmod 644 {C_LIB_PATH}"
            return
    except Exception:
        pass
    # 2. 检查动态库有效性（Linux/Termux）
    if sys.platform.startswith("linux") or _is_termux_environment():
        stdout, stderr, ret = _run_command(f"file {C_LIB_PATH}")
        if ret == 0:
            if "shared object" not in stdout and "ELF" not in stdout:
                C_LIB_ERROR += f"\n不是有效动态库：{C_LIB_PATH}\n文件类型：{stdout}"
                return
    # 3. 检查依赖（Linux/Termux）
    if sys.platform.startswith("linux") or _is_termux_environment():
        dependency_check_commands = [
            f"ldd {C_LIB_PATH}",
            f"objdump -p {C_LIB_PATH} | grep NEEDED",
            f"readelf -d {C_LIB_PATH} 2>/dev/null | grep NEEDED"
        ]
        missing_deps = []
        for cmd in dependency_check_commands:
            stdout, stderr, ret = _run_command(cmd)
            if ret == 0 and stdout:
                for line in stdout.split('\n'):
                    if 'not found' in line:
                        dep = line.split('=>')[0].strip()
                        missing_deps.append(dep)
                    elif 'NEEDED' in line:
                        dep = line.split()[-1]
                        if dep.startswith('lib') and not dep.startswith('libc') and not dep.startswith('libm'):
                            find_cmd = f"find $PREFIX -name '{dep}' 2>/dev/null | head -1" if _is_termux_environment() else f"find /usr/lib -name '{dep}' 2>/dev/null | head -1"
                            find_result = subprocess.run(find_cmd, shell=True, capture_output=True, text=True)
                            if not find_result.stdout.strip():
                                missing_deps.append(dep)
                if missing_deps:
                    break
        if missing_deps:
            unique_deps = list(set(missing_deps))
            if not IS_LIBRARY_MODE:
                print(f"⚠️  检测到可能缺失的依赖: {', '.join(unique_deps)}")
                if _is_termux_environment():
                    print(f"   在Termux中尝试: pkg install {' '.join([d.replace('.so', '').replace('lib', '') for d in unique_deps])}")
                else:
                    print(f"   在Linux中尝试: sudo apt install {' '.join([d.replace('.so', '').replace('lib', '') for d in unique_deps])}")

def _load_c_library() -> None:
    """加载C动态库并绑定函数（原逻辑完全不变，仅使用get_lib_path获取的C_LIB_PATH）"""
    global C_LIB, C_LIB_AVAILABLE, C_LIB_ERROR
    if not C_LIB_PATH:
        C_LIB_ERROR = "C库路径未定义"
        return
    try:
        C_LIB = CDLL(C_LIB_PATH)
        # 动态检测并绑定函数
        func_bindings = {
            'init_process_control': [POINTER(ProcessControlConfig), c_bool],
            'add_process': [c_int, c_float, c_char_p, c_char_p, c_bool],
            'remove_process': [c_int, c_bool],
            'check_process_alive': [c_int, c_bool],
            'kill_process': [c_int, c_bool],
            'clear_stale_processes': [c_int],
            'get_running_processes': [POINTER(c_int), POINTER(ProcessInfo)],
            'free_process_list': [POINTER(ProcessInfo), None]
        }
        # 尝试绑定标准函数名
        func_bound = False
        for func_name, signature in func_bindings.items():
            if hasattr(C_LIB, func_name):
                try:
                    getattr(C_LIB, func_name).argtypes = signature[:-2]
                    getattr(C_LIB, func_name).restype = signature[-2]
                    func_bound = True
                    if not IS_LIBRARY_MODE:
                        print(f"✅ 绑定函数: {func_name}")
                except Exception as e:
                    if not IS_LIBRARY_MODE:
                        print(f"⚠️  函数绑定失败 {func_name}: {e}")
        # 如果标准函数名未找到，尝试其他常见名称
        if not func_bound:
            alt_func_names = {
                'init_process_control': ['init', 'initialize', 'setup_process_control'],
                'add_process': ['add', 'register_process', 'track_process'],
                'remove_process': ['remove', 'unregister', 'delete_process'],
                'check_process_alive': ['check_alive', 'is_alive', 'process_alive'],
                'kill_process': ['kill', 'terminate', 'stop_process'],
                'clear_stale_processes': ['clear', 'cleanup', 'purge_stale'],
                'get_running_processes': ['get_processes', 'list_processes', 'running_processes'],
                'free_process_list': ['free', 'release', 'cleanup_list']
            }
            for std_name, alt_names in alt_func_names.items():
                for alt_name in alt_names:
                    if hasattr(C_LIB, alt_name):
                        try:
                            setattr(C_LIB, std_name, getattr(C_LIB, alt_name))
                            signature = func_bindings[std_name]
                            getattr(C_LIB, std_name).argtypes = signature[:-2]
                            getattr(C_LIB, std_name).restype = signature[-2]
                            if not IS_LIBRARY_MODE:
                                print(f"✅ 映射函数: {alt_name} → {std_name}")
                            func_bound = True
                            break
                        except Exception as e:
                            continue
        if func_bound:
            # 初始化C库配置
            config = ProcessControlConfig(
                root_dir=ROOT_DIR.encode("utf-8"),
                user_home_dir=USER_HOME_DIR.encode("utf-8"),
                max_process_count=MAX_PROCESS_COUNT,
                cache_ttl=PROCESS_CACHE_TTL
            )
            # 检查是否有init函数
            if hasattr(C_LIB, 'init_process_control'):
                try:
                    if C_LIB.init_process_control(config):
                        C_LIB_AVAILABLE = True
                        if not IS_LIBRARY_MODE:
                            print(f"✅ C库初始化成功：{os.path.basename(C_LIB_PATH)}")
                    else:
                        C_LIB_ERROR = "C库初始化返回失败"
                        C_LIB_AVAILABLE = False
                except Exception as e:
                    C_LIB_ERROR = f"C库初始化异常：{str(e)}"
                    C_LIB_AVAILABLE = False
            else:
                C_LIB_AVAILABLE = True
                if not IS_LIBRARY_MODE:
                    print(f"✅ C库加载成功（无需初始化）")
        else:
            if not IS_LIBRARY_MODE:
                print(f"⚠️  C库已加载但未找到标准函数接口")
                funcs = [x for x in dir(C_LIB) if not x.startswith('_')]
                if funcs:
                    print(f"   可用函数：{', '.join(funcs[:10])}{'...' if len(funcs) > 10 else ''}")
    except OSError as e:
        C_LIB_ERROR = f"C库加载失败（系统错误）：{str(e)}"
        C_LIB_AVAILABLE = False
    except AttributeError as e:
        C_LIB_ERROR = f"C库函数绑定失败（函数不存在）：{str(e)}"
        C_LIB_AVAILABLE = False
    except Exception as e:
        C_LIB_ERROR = f"C库初始化异常：{str(e)}"
        C_LIB_AVAILABLE = False

# ====================== 以下所有逻辑完全保留（无任何修改）======================
def _load_process_cache() -> None:
    """加载进程缓存（异常时初始化空缓存）"""
    global PROCESS_CACHE
    if not PROCESS_CACHE_PATH:
        PROCESS_CACHE = {}
        return
    try:
        if os.path.exists(PROCESS_CACHE_PATH):
            with open(PROCESS_CACHE_PATH, "rb") as f:
                cached_data = msgpack.load(f, raw=False)
            if isinstance(cached_data, dict):
                PROCESS_CACHE = {int(pid): val for pid, val in cached_data.items()}
            else:
                PROCESS_CACHE = {}
        else:
            PROCESS_CACHE = {}
    except Exception as e:
        PROCESS_CACHE = {}
        global C_LIB_ERROR
        if not C_LIB_ERROR:
            C_LIB_ERROR = f"进程缓存加载失败：{str(e)}"

def _save_process_cache() -> None:
    """保存进程缓存（异常时忽略，不中断流程）"""
    if not PROCESS_CACHE_PATH:
        return
    try:
        os.makedirs(os.path.dirname(PROCESS_CACHE_PATH), exist_ok=True)
        with open(PROCESS_CACHE_PATH, "wb") as f:
            msgpack.dump(PROCESS_CACHE, f, use_bin_type=True)
    except Exception:
        pass

def _update_cache(pid: int, start_time: float, request_id: str, command: str) -> None:
    """更新进程缓存（带锁保护，避免并发问题）"""
    try:
        with PROCESS_LOCK:
            PROCESS_CACHE[pid] = (start_time, request_id, command)
            # 清理过期缓存
            current_time = time.time()
            PROCESS_CACHE = {
                pid: val for pid, val in PROCESS_CACHE.items()
                if current_time - val[0] < PROCESS_CACHE_TTL
            }
            _save_process_cache()
    except Exception:
        pass

# -------------------------- C库降级逻辑（Python实现） --------------------------
def _python_check_process_alive(pid: int) -> bool:
    """Python实现：检查进程是否存活"""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                f"tasklist /FI \"PID eq {pid}\"",
                shell=True,
                stdout=subprocess.PIPE,
                text=True
            )
            return str(pid) in result.stdout
        else:
            os.kill(pid, 0)
            return True
    except (OSError, subprocess.SubprocessError):
        return False

def _python_add_process(pid: int, start_time: float, request_id: str, command: str) -> bool:
    """Python实现：添加进程到管理列表"""
    try:
        with PROCESS_LOCK:
            if len(PROCESS_CACHE) >= MAX_PROCESS_COUNT:
                return False
            _update_cache(pid, start_time, request_id, command)
            return True
    except Exception:
        return False

def _python_remove_process(pid: int) -> bool:
    """Python实现：从管理列表移除进程"""
    try:
        with PROCESS_LOCK:
            if pid in PROCESS_CACHE:
                del PROCESS_CACHE[pid]
                _save_process_cache()
                return True
            return False
    except Exception:
        return False

def _python_kill_process(pid: int) -> bool:
    """Python实现：终止进程"""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                f"taskkill /F /PID {pid}",
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            success = result.returncode == 0
        else:
            os.kill(pid, 9)
            success = True
        if success:
            _python_remove_process(pid)
        return success
    except Exception:
        return False

def _python_clear_stale_processes() -> int:
    """Python实现：清理僵尸进程"""
    try:
        with PROCESS_LOCK:
            stale_count = 0
            current_time = time.time()
            for pid in list(PROCESS_CACHE.keys()):
                if not _python_check_process_alive(pid) or current_time - PROCESS_CACHE[pid][0] > PROCESS_CACHE_TTL:
                    del PROCESS_CACHE[pid]
                    stale_count += 1
            _save_process_cache()
            return stale_count
    except Exception:
        return 0

def _python_get_running_processes() -> List[Tuple[int, float, str, str]]:
    """Python实现：获取所有运行中进程"""
    try:
        with PROCESS_LOCK:
            _python_clear_stale_processes()
            return [(pid, start_time, req_id, cmd) for pid, (start_time, req_id, cmd) in PROCESS_CACHE.items()]
    except Exception:
        return []

# -------------------------- 对外暴露接口（与Onyx.py对接） --------------------------
def check_process_alive(pid: int) -> bool:
    """检查进程是否存活"""
    if C_LIB_AVAILABLE and C_LIB is not None:
        try:
            return C_LIB.check_process_alive(pid)
        except Exception:
            pass
    return _python_check_process_alive(pid)

def add_process(pid: int, start_time: float, request_id: str, command: str) -> bool:
    """添加进程到管理列表"""
    if C_LIB_AVAILABLE and C_LIB is not None:
        try:
            return C_LIB.add_process(
                pid,
                c_float(start_time),
                request_id.encode("utf-8") if request_id else b"",
                command.encode("utf-8") if command else b""
            )
        except Exception:
            pass
    return _python_add_process(pid, start_time, request_id, command)

def remove_process(pid: int) -> bool:
    """从管理列表移除进程"""
    if C_LIB_AVAILABLE and C_LIB is not None:
        try:
            return C_LIB.remove_process(pid)
        except Exception:
            pass
    return _python_remove_process(pid)

def kill_process(pid: int) -> bool:
    """终止进程"""
    if C_LIB_AVAILABLE and C_LIB is not None:
        try:
            return C_LIB.kill_process(pid)
        except Exception:
            pass
    return _python_kill_process(pid)

def clear_stale_processes() -> int:
    """清理僵尸进程（返回清理数量）"""
    if C_LIB_AVAILABLE and C_LIB is not None:
        try:
            return C_LIB.clear_stale_processes()
        except Exception:
            pass
    return _python_clear_stale_processes()

def get_running_processes() -> List[Tuple[int, float, str, str]]:
    """获取所有运行中进程（PID, 启动时间, 请求ID, 命令）"""
    if C_LIB_AVAILABLE and C_LIB is not None:
        try:
            count = c_int(0)
            process_list = C_LIB.get_running_processes(count)
            result = []
            for i in range(count.value):
                p = process_list[i]
                result.append((
                    p.pid,
                    float(p.start_time),
                    p.request_id.decode("utf-8") if p.request_id else "",
                    p.command.decode("utf-8") if p.command else ""
                ))
            # 释放C库内存
            if process_list:
                C_LIB.free_process_list(process_list)
            return result
        except Exception:
            pass
    return _python_get_running_processes()

def get_c_library_filename() -> str:
    """对外暴露：基于get_lib_path逻辑返回C库文件名（适配改造）"""
    try:
        # 复用get_lib_path的系统/架构判断逻辑
        from lib.get_lib_path import _get_system_arch, _get_lib_suffix
        return f"process_control_{_get_system_arch()}{_get_lib_suffix()}"
    except Exception as e:
        global C_LIB_ERROR
        if not C_LIB_ERROR:
            C_LIB_ERROR = f"获取C库文件名失败：{str(e)}"
        return "process_control_unknown_unknown.so"

# 暴露C库错误信息（供Onyx.py调试）
def get_c_lib_error() -> str:
    """获取C库错误信息"""
    return C_LIB_ERROR
