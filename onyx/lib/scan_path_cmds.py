# lib/scan_path_cmds.py - 系统命令扫描（支持刷新接口）

import os
import sys
import time
import msgpack
import concurrent.futures
import asyncio
import threading
from typing import Dict, List, Set, Optional, Any, Tuple, Callable
import subprocess
from pathlib import Path
from functools import lru_cache

try:
    from ctypes import CDLL, c_char_p, c_int, POINTER, byref
except ImportError:
    _ctypes_available = False
else:
    _ctypes_available = True


class ScanPathCmds:
    """系统命令扫描类（极致速度优化版）"""
    
    # 类级缓存，避免重复计算
    _EXEC_SUFFIXES_CACHE = {
        "Windows": {".exe", ".com", ".bat", ".cmd", ".ps1", ".vbs"},
        "Linux/macOS": {""},
        "macOS": {""},
        "Termux": {""},
        "SpecialLinux": {""},
        "macos": {""}
    }
    
    def __init__(
        self,
        root_dir: str,
        user_home_dir: str,
        sys_type: str,
        builtin_commands: Dict[str, Any],
        tool_index_cache: Dict[str, Any],
        cmd_mapping_msg_path: str = "",
        cmd_cache_ttl: int = 86400,  # 24小时，实际不过期
        is_library_mode: bool = True,
        max_workers: int = None,
        debug_mode: bool = True,  # 调试模式
        external_current_sys_cmds: Dict[str, List[str]] = None  # 外部共享 dict 引用
    ):
        """初始化时进行预计算和缓存"""
        # 核心配置参数
        self.root_dir = os.path.normpath(os.path.realpath(root_dir))
        self.user_home_dir = os.path.normpath(os.path.realpath(user_home_dir))
        self.sys_type = sys_type
        self.builtin_commands = builtin_commands
        self.tool_index_cache = tool_index_cache
        self.cmd_mapping_msg_path = cmd_mapping_msg_path
        self.cmd_cache_ttl = cmd_cache_ttl
        self.is_library_mode = is_library_mode
        self.max_workers = max_workers or os.cpu_count() or 4
        self.debug_mode = debug_mode  # 调试模式开关
        
        # 预计算常用值
        self._exec_suffixes = self._EXEC_SUFFIXES_CACHE.get(sys_type, {""})
        self._is_windows = sys_type == "Windows"
        
        # 状态缓存 — 使用外部共享 dict 引用，确保与 Onyx.py 全局同步
        if external_current_sys_cmds is not None:
            self.current_sys_cmds: Dict[str, List[str]] = external_current_sys_cmds
        else:
            self.current_sys_cmds: Dict[str, List[str]] = {}
        self.cmd_mapping_cache: Dict[str, Dict[str, Any]] = {}
        
        # 刷新相关
        self._refresh_lock = threading.Lock()
        self._last_refresh_time = 0
        self._is_refreshing = False
        
        # C库相关状态（暂时禁用，使用Python并行扫描更可靠）
        self.c_lib = None
        self.c_lib_available = False
        self.c_lib_path = ""
        self.c_lib_error = "C库暂时禁用，使用Python扫描"
        
        # 初始化流程
        self._load_cmd_mapping_cache()
        
        # 缓存已扫描的目录内容（持久化缓存）— (cache_time, dir_mtime, files_set)
        self._dir_persistent_cache: Dict[str, Tuple[float, float, Set[str]]] = {}
        self._dir_cache_path = ""
        if cmd_mapping_msg_path:
            cache_dir = os.path.dirname(cmd_mapping_msg_path)
            self._dir_cache_path = os.path.join(cache_dir, "dir_scan_cache.msgpack")
            self._load_dir_persistent_cache()
        
        # 内存缓存TTL（5分钟，避免频繁读文件）— (cache_time, dir_mtime, files_set)
        self._dir_mem_cache: Dict[str, Tuple[float, float, Set[str]]] = {}
        self._dir_mem_cache_ttl = 300  # 5分钟
        
        # 新增：调试缓存路径
        if self.debug_mode and cache_dir:
            self._debug_cache_path = os.path.join(cache_dir, "debug_scan_path.cache")
        else:
            self._debug_cache_path = None
        
    def _load_dir_persistent_cache(self) -> None:
        """加载目录持久化缓存（兼容旧格式：2-tuple 无 mtime 则设为 0 触发重扫）"""
        if not self._dir_cache_path or not os.path.exists(self._dir_cache_path):
            return
        
        try:
            with open(self._dir_cache_path, 'rb') as f:
                cached_data = msgpack.load(f, raw=False)
            
            # 不检查TTL，持久化缓存永不过期
            if isinstance(cached_data, dict):
                for dir_path, entry in cached_data.items():
                    if isinstance(entry, (list, tuple)):
                        if len(entry) == 3:
                            # 新格式: (cache_time, dir_mtime, files)
                            cache_time, dir_mtime, files = entry
                        elif len(entry) == 2:
                            # 旧格式: (cache_time, files) — 无 mtime，设为 0 强制重扫
                            cache_time, files = entry
                            dir_mtime = 0.0
                        else:
                            continue
                        if isinstance(files, list):
                            self._dir_persistent_cache[dir_path] = (cache_time, dir_mtime, set(files))
        except Exception:
            pass
    
    def _save_dir_persistent_cache(self) -> None:
        """保存目录持久化缓存"""
        if not self._dir_cache_path:
            return
        
        try:
            cache_dir = os.path.dirname(self._dir_cache_path)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir, mode=0o755, exist_ok=True)
            
            # 转换为可序列化格式 — (cache_time, dir_mtime, files_list)
            serializable = {}
            for dir_path, (cache_time, dir_mtime, files_set) in self._dir_persistent_cache.items():
                serializable[dir_path] = (cache_time, dir_mtime, list(files_set))
            
            temp_path = f"{self._dir_cache_path}.tmp"
            with open(temp_path, 'wb') as f:
                msgpack.dump(serializable, f, use_bin_type=True)
            os.replace(temp_path, self._dir_cache_path)
        except Exception:
            pass
    
    def _save_debug_cache(self, scan_data: Dict[str, Any]) -> None:
        """
        第8个保存点：保存调试缓存到 debug_scan_path.cache
        
        保存内容包括：
        - 扫描时间戳
        - 系统类型
        - 扫描的所有目录列表
        - 每个目录的命令数量
        - 总命令列表
        - 命令数量统计
        - 是否强制扫描
        - 扫描耗时
        """
        if not self.debug_mode or not self._debug_cache_path:
            return
        
        try:
            cache_dir = os.path.dirname(self._debug_cache_path)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir, mode=0o755, exist_ok=True)
            
            # 添加额外元数据
            scan_data['_metadata'] = {
                'version': '1.0',
                'created_by': 'scan_path_cmds.py',
                'save_point': 8,
                'save_point_name': 'debug_scan_path.cache',
                'sys_type': self.sys_type,
                'is_windows': self._is_windows,
                'max_workers': self.max_workers,
                'debug_mode': self.debug_mode
            }
            
            # 使用 JSON 格式保存（更易读）
            import json
            temp_path = f"{self._debug_cache_path}.tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(scan_data, f, ensure_ascii=False, indent=2, default=str)
            os.replace(temp_path, self._debug_cache_path)
            
            if not self.is_library_mode:
                print(f"🐛 调试缓存已保存: {self._debug_cache_path}")
                print(f"   - 文件大小: {os.path.getsize(self._debug_cache_path)} bytes")
                print(f"   - 命令总数: {len(scan_data.get('all_commands', []))}")
                print(f"   - 扫描目录数: {len(scan_data.get('scanned_dirs', []))}")
        except Exception as e:
            if not self.is_library_mode:
                print(f"⚠️ 调试缓存保存失败: {str(e)}")
    
    def _run_command(self, cmd: str) -> Tuple[str, str, int]:
        """执行系统命令（优化：使用预创建的启动信息）"""
        startupinfo = None
        if sys.platform.startswith("win32"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3,
                text=True,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win32") else 0
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "", "Command timeout", -2
        except Exception as e:
            return "", str(e), -1
    
    @lru_cache(maxsize=256)
    def _is_valid_executable(self, file_path: str, filename: str) -> bool:
        """判断是否为可执行文件（缓存优化）"""
        if filename.startswith('.') or filename.lower() == 'import':
            return False
            
        if self._is_windows:
            # Windows: 检查后缀
            fname_lower = filename.lower()
            for suffix in self._exec_suffixes:
                if fname_lower.endswith(suffix):
                    return True
            return False
        else:
            # Unix-like: 检查文件权限
            try:
                return os.access(file_path, os.X_OK) and os.path.isfile(file_path)
            except (OSError, PermissionError):
                return False
    
    def _scan_single_directory(self, dir_path: str, force_scan: bool = False) -> Tuple[Set[str], Dict[str, Any]]:
        """
        扫描单个目录（支持符号链接跟随）
        返回: (命令集合, 调试信息)
        """
        resolved_dir = os.path.expanduser(dir_path)
        debug_info = {
            'dir_path': dir_path,
            'resolved_path': resolved_dir,
            'exists': False,
            'accessible': False,
            'cached': False,
            'command_count': 0,
            'commands': []
        }
        
        # 1. 检查内存缓存
        current_time = time.time()
        if not force_scan:
            if resolved_dir in self._dir_mem_cache:
                mem_entry = self._dir_mem_cache[resolved_dir]
                cache_time = mem_entry[0]
                cached_files = mem_entry[-1]
                if current_time - cache_time < self._dir_mem_cache_ttl:
                    debug_info['cached'] = True
                    debug_info['cache_type'] = 'memory'
                    debug_info['command_count'] = len(cached_files)
                    debug_info['commands'] = list(cached_files)[:20]
                    return cached_files, debug_info
        
        # 2. 检查持久化缓存（mtime 增量：目录未变化则跳过重扫）
        if not force_scan and resolved_dir in self._dir_persistent_cache:
            cache_entry = self._dir_persistent_cache[resolved_dir]
            cache_time, dir_mtime, cached_files = cache_entry
            
            # 获取当前目录 mtime
            try:
                current_dir_mtime = os.stat(resolved_dir).st_mtime
            except OSError:
                current_dir_mtime = 0.0
            
            # 如果 mtime 匹配，目录未变化，直接使用缓存
            if dir_mtime > 0 and current_dir_mtime == dir_mtime:
                self._dir_mem_cache[resolved_dir] = (current_time, current_dir_mtime, cached_files)
                debug_info['cached'] = True
                debug_info['cache_type'] = 'persistent'
                debug_info['cache_age'] = current_time - cache_time
                debug_info['command_count'] = len(cached_files)
                debug_info['commands'] = list(cached_files)[:20]
                debug_info['mtime_match'] = True
                return cached_files, debug_info
            
            # mtime 变化或未知（旧缓存），需要重扫；但缓存的命令仍可作参考
            debug_info['cached'] = False
            debug_info['cache_type'] = 'persistent_stale'
            debug_info['cache_age'] = current_time - cache_time
            debug_info['mtime_match'] = False
            debug_info['old_mtime'] = dir_mtime
            debug_info['new_mtime'] = current_dir_mtime
        
        # 3. 实际扫描
        try:
            if not os.path.isdir(resolved_dir):
                debug_info['exists'] = False
                return set(), debug_info
            
            if not os.access(resolved_dir, os.R_OK | os.X_OK):
                debug_info['exists'] = True
                debug_info['accessible'] = False
                return set(), debug_info
                
            debug_info['exists'] = True
            debug_info['accessible'] = True
        except (OSError, PermissionError) as e:
            debug_info['error'] = str(e)
            return set(), debug_info
        
        found_cmds = set()
        try:
            with os.scandir(resolved_dir) as entries:
                for entry in entries:
                    try:
                        if entry.name.startswith('.'):
                            continue
                        
                        # ========== 关键修改：处理符号链接 ==========
                        if entry.is_symlink():
                            # 跟随符号链接获取真实路径
                            try:
                                real_path = os.path.realpath(entry.path)
                                # 检查真实路径是否是文件且可执行
                                if os.path.isfile(real_path) and self._is_valid_executable(real_path, entry.name):
                                    cmd_name = entry.name
                                    if self._is_windows:
                                        fname_lower = entry.name.lower()
                                        for suffix in self._exec_suffixes:
                                            if fname_lower.endswith(suffix):
                                                cmd_name = entry.name[:-len(suffix)]
                                                break
                                    found_cmds.add(cmd_name.lower())
                            except (OSError, PermissionError):
                                pass
                            continue
                        
                        # 跳过目录
                        if entry.is_dir():
                            continue
                        
                        # 普通文件：检查是否可执行
                        if self._is_valid_executable(entry.path, entry.name):
                            cmd_name = entry.name
                            if self._is_windows:
                                fname_lower = entry.name.lower()
                                for suffix in self._exec_suffixes:
                                    if fname_lower.endswith(suffix):
                                        cmd_name = entry.name[:-len(suffix)]
                                        break
                            found_cmds.add(cmd_name.lower())
                            
                    except (OSError, PermissionError):
                        continue
                        
        except (OSError, PermissionError, FileNotFoundError) as e:
            debug_info['scan_error'] = str(e)
            return set(), debug_info
        
        # 保存到持久化缓存（含目录 mtime，用于后续增量判断）
        try:
            dir_mtime = os.stat(resolved_dir).st_mtime
        except OSError:
            dir_mtime = 0.0
        self._dir_persistent_cache[resolved_dir] = (current_time, dir_mtime, found_cmds)
        self._dir_mem_cache[resolved_dir] = (current_time, dir_mtime, found_cmds)
        
        debug_info['command_count'] = len(found_cmds)
        debug_info['commands'] = list(found_cmds)[:20]  # 只保存前20个用于调试
        debug_info['cached'] = False
        
        return found_cmds, debug_info
    
    def _scan_with_python_parallel(self, path_dirs: List[str], force_scan: bool = False) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Python并行扫描（多线程优化）"""
        all_cmds = set()
        all_debug_info = []
        
        # 使用ThreadPoolExecutor并行扫描
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_dir = {executor.submit(self._scan_single_directory, dir_path, force_scan): dir_path 
                           for dir_path in path_dirs}
            
            for future in concurrent.futures.as_completed(future_to_dir):
                try:
                    dir_cmds, debug_info = future.result(timeout=5)
                    all_cmds.update(dir_cmds)
                    all_debug_info.append(debug_info)
                except (concurrent.futures.TimeoutError, Exception) as e:
                    dir_path = future_to_dir[future]
                    all_debug_info.append({
                        'dir_path': dir_path,
                        'error': f"Timeout/Exception: {str(e)}",
                        'command_count': 0
                    })
                    continue
        
        return list(all_cmds), all_debug_info
    
    def _get_path_dirs(self) -> List[str]:
        """获取所有需要扫描的目录（去重优化）"""
        # 获取PATH变量
        path_str = os.environ.get("PATH", "")
        path_dirs = {d for d in path_str.split(os.pathsep) if d.strip()}
        
        # 添加通用目录
        universal_dirs = {
            "/bin", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin",
            "/data/data/com.termux/files/usr/bin",
            os.path.join(os.environ.get("SystemRoot", ""), "System32"),
            os.path.join(os.environ.get("ProgramFiles", ""), "Git\\bin"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Git\\bin")
        }
        path_dirs.update(universal_dirs)
        
        # 扩展用户目录
        expanded_dirs = []
        for dir_path in path_dirs:
            try:
                expanded = os.path.expanduser(dir_path)
                if os.path.isdir(expanded):
                    expanded_dirs.append(expanded)
            except (OSError, TypeError):
                continue
        
        # 去重并返回
        seen = set()
        unique_dirs = []
        for d in expanded_dirs:
            real_path = os.path.realpath(d) if os.path.islink(d) else d
            if real_path not in seen:
                seen.add(real_path)
                unique_dirs.append(d)
        
        return unique_dirs
    
    def scan_path_for_system_cmds(self, request_id: str = str(os.urandom(16).hex()), 
                                   force_scan: bool = False) -> Dict[str, List[str]]:
        """极致优化的扫描入口"""
        start_time = time.time()
        
        # 1. 获取所有需要扫描的目录
        valid_dirs = self._get_path_dirs()
        
        # 2. 备份原有缓存
        original_cmds = self.current_sys_cmds.get(self.sys_type, []).copy()
        
        # 3. Python并行扫描（禁用C库）
        new_cmds, scan_debug_info = self._scan_with_python_parallel(valid_dirs, force_scan)
        
        # 4. 合并结果
        original_set = set(original_cmds)
        new_set = set(new_cmds)
        merged_set = original_set.union(new_set)
        merged_cmds = list(merged_set)
        
        # 5. 更新缓存
        self.current_sys_cmds[self.sys_type] = merged_cmds
        
        # 6. 保存持久化缓存
        self._save_dir_persistent_cache()
        
        # 7. 保存命令映射缓存
        self._save_cmd_mapping_cache(merged_cmds)
        
        # ========== 8. 保存调试缓存到 debug_scan_path.cache ==========
        elapsed_ms = (time.time() - start_time) * 1000
        
        # 收集所有目录的命令详情（用于调试）
        dir_details = []
        for debug_info in scan_debug_info:
            dir_details.append({
                'path': debug_info.get('dir_path', 'unknown'),
                'resolved': debug_info.get('resolved_path', ''),
                'exists': debug_info.get('exists', False),
                'accessible': debug_info.get('accessible', True),
                'cached': debug_info.get('cached', False),
                'cache_type': debug_info.get('cache_type', 'none'),
                'cache_age': debug_info.get('cache_age', 0),
                'command_count': debug_info.get('command_count', 0),
                'sample_commands': debug_info.get('commands', [])[:10],  # 前10个命令样例
                'error': debug_info.get('error', None),
                'scan_error': debug_info.get('scan_error', None)
            })
        
        # 构建完整的调试数据
        debug_data = {
            'timestamp': time.time(),
            'timestamp_str': time.strftime("%Y-%m-%d %H:%M:%S"),
            'request_id': request_id,
            'sys_type': self.sys_type,
            'force_scan': force_scan,
            'scan_duration_ms': elapsed_ms,
            'scanned_dirs_count': len(valid_dirs),
            'scanned_dirs': valid_dirs,  # 所有扫描的目录列表
            'dir_details': dir_details,  # 每个目录的详细信息
            'original_command_count': len(original_cmds),
            'new_command_count': len(new_cmds),
            'merged_command_count': len(merged_cmds),
            'added_commands': list(new_set - original_set)[:50],  # 新增的命令（最多50个）
            'removed_commands': list(original_set - new_set)[:50],  # 删除的命令（最多50个）
            'all_commands': sorted(merged_cmds),  # 所有命令（排序）
            'cache_info': {
                'dir_persistent_cache_size': len(self._dir_persistent_cache),
                'dir_mem_cache_size': len(self._dir_mem_cache),
                'cmd_mapping_cache_path': self.cmd_mapping_msg_path,
                'dir_cache_path': self._dir_cache_path
            },
            'config': {
                'max_workers': self.max_workers,
                'is_windows': self._is_windows,
                'debug_mode': self.debug_mode,
                'exec_suffixes': list(self._exec_suffixes)
            }
        }
        
        # 保存调试缓存
        self._save_debug_cache(debug_data)
        # ============================================================
        
        if not self.is_library_mode:
            print(f"✅ 扫描完成: {len(original_cmds)}→{len(merged_cmds)}命令 (+{len(new_set - original_set)})")
            print(f"⏱️  耗时: {elapsed_ms:.1f}ms | 目录: {len(valid_dirs)} | 线程: {self.max_workers}")
        
        return {self.sys_type: merged_cmds}
    
    def _save_cmd_mapping_cache(self, merged_cmds: List[str]) -> None:
        """保存命令映射缓存"""
        if not self.cmd_mapping_msg_path:
            return
        
        cache_entry = {
            "mapping": {
                "builtins": {k.lower(): v.__name__ if hasattr(v, '__name__') else str(v) 
                            for k, v in self.builtin_commands.items()},
                "system": merged_cmds,
                "tools": {
                    k.split(f"_{self.sys_type}")[0].lower() if f"_{self.sys_type}" in k else k: {
                        "path": getattr(v, 'path', ''),
                        "perm": getattr(v, 'tool_perm', 3),
                        "type": getattr(v, 'tool_type', 'other')
                    } for k, v in self.tool_index_cache.items()
                }
            },
            "cache_time": time.time()
        }
        self.cmd_mapping_cache[self.sys_type] = cache_entry
        
        try:
            cache_dir = os.path.dirname(self.cmd_mapping_msg_path)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir, mode=0o755, exist_ok=True)
            
            temp_path = f"{self.cmd_mapping_msg_path}.tmp"
            with open(temp_path, "wb") as f:
                msgpack.dump(self.cmd_mapping_cache, f, use_bin_type=True)
            os.replace(temp_path, self.cmd_mapping_msg_path)
        except Exception:
            pass
    
    def _load_cmd_mapping_cache(self) -> None:
        """快速加载缓存"""
        if not self.cmd_mapping_msg_path or not os.path.exists(self.cmd_mapping_msg_path):
            return
            
        try:
            with open(self.cmd_mapping_msg_path, "rb") as f:
                cached_data = msgpack.load(f, raw=False)
                
            if isinstance(cached_data, dict) and self.sys_type in cached_data:
                sys_cache = cached_data[self.sys_type]
                # 不检查TTL，缓存永不过期
                self.cmd_mapping_cache = cached_data
                self.current_sys_cmds[self.sys_type] = sys_cache["mapping"]["system"]
        except Exception:
            self.cmd_mapping_cache = {}
    
    def get_current_state(self) -> Dict[str, Any]:
        """获取当前状态"""
        return {
            "sys_type": self.sys_type,
            "cmd_count": len(self.current_sys_cmds.get(self.sys_type, [])),
            "c_lib_available": self.c_lib_available,
            "dir_persistent_cache_size": len(self._dir_persistent_cache),
            "dir_mem_cache_size": len(self._dir_mem_cache),
            "max_workers": self.max_workers,
            "last_refresh_time": self._last_refresh_time,
            "is_refreshing": self._is_refreshing,
            "debug_mode": self.debug_mode,
            "debug_cache_path": self._debug_cache_path
        }


# 全局实例
_global_scan_instance: Optional[ScanPathCmds] = None
_refresh_lock = threading.Lock()
_last_refresh_time = 0
_REFRESH_INTERVAL = 30  # 30秒内不重复刷新


def init_scan_path_cmds(
    root_dir: str,
    user_home_dir: str,
    sys_type: str,
    builtin_commands: Dict[str, Any],
    tool_index_cache: Dict[str, Any],
    cmd_mapping_msg_path: str = "",
    cmd_cache_ttl: int = 86400,
    max_workers: int = None,
    debug_mode: bool = True,  # 调试模式参数
    external_current_sys_cmds: Dict[str, List[str]] = None  # 外部共享 dict
) -> None:
    """初始化扫描模块"""
    global _global_scan_instance
    _global_scan_instance = ScanPathCmds(
        root_dir=root_dir,
        user_home_dir=user_home_dir,
        sys_type=sys_type,
        builtin_commands=builtin_commands,
        tool_index_cache=tool_index_cache,
        cmd_mapping_msg_path=cmd_mapping_msg_path,
        cmd_cache_ttl=cmd_cache_ttl,
        is_library_mode=True,
        max_workers=max_workers,
        debug_mode=debug_mode,  # 传递调试模式
        external_current_sys_cmds=external_current_sys_cmds
    )


def scan_path_for_system_cmds(request_id: str = str(os.urandom(16).hex()), 
                               force_scan: bool = False) -> Dict[str, List[str]]:
    """扫描系统命令"""
    global _global_scan_instance
    if _global_scan_instance is None:
        raise RuntimeError("请先调用 init_scan_path_cmds 初始化")
    return _global_scan_instance.scan_path_for_system_cmds(request_id, force_scan)


# ==================== 新增：刷新接口 ====================

def refresh_system_cmds(
    request_id: str = "",
    force: bool = False,
    log_info_func=None,
    log_error_func=None
) -> bool:
    """
    异步刷新系统命令（命令执行前调用）
    
    特性：
    1. 非阻塞，立即返回
    2. 30秒内不重复刷新（避免频繁扫描）
    3. 刷新过程中不删除现有缓存
    4. 新缓存构建完成后原子替换
    
    参数:
        request_id: 请求ID
        force: 是否强制刷新（忽略间隔限制）
        log_info_func: 日志函数
        log_error_func: 错误日志函数
    
    返回:
        bool: 是否启动了刷新任务
    """
    global _last_refresh_time, _global_scan_instance
    
    if _global_scan_instance is None:
        if log_error_func:
            log_error_func("扫描模块未初始化", request_id)
        return False
    
    # 检查刷新间隔
    current_time = time.time()
    if not force and (current_time - _last_refresh_time) < _REFRESH_INTERVAL:
        if log_info_func:
            log_info_func(f"系统命令刷新跳过（距上次刷新{int(current_time - _last_refresh_time)}秒）", request_id)
        return False
    
    # 检查是否已在刷新中
    if _global_scan_instance._is_refreshing:
        if log_info_func:
            log_info_func("系统命令已在刷新中，跳过", request_id)
        return False
    
    def refresh_task():
        """后台刷新任务"""
        try:
            _global_scan_instance._is_refreshing = True
            if log_info_func:
                log_info_func("系统命令后台刷新已启动", request_id)
            
            # 强制重新扫描
            _global_scan_instance.scan_path_for_system_cmds(request_id, force_scan=True)
            
            if log_info_func:
                cmd_count = len(_global_scan_instance.current_sys_cmds.get(_global_scan_instance.sys_type, []))
                log_info_func(f"系统命令刷新完成：共{cmd_count}个命令", request_id)
        except Exception as e:
            if log_error_func:
                log_error_func(f"系统命令刷新失败：{str(e)}", request_id)
        finally:
            _global_scan_instance._is_refreshing = False
    
    with _refresh_lock:
        _last_refresh_time = current_time
        # 启动后台线程
        thread = threading.Thread(target=refresh_task, daemon=True)
        thread.start()
    
    return True


def get_system_cmds_status() -> Dict[str, Any]:
    """获取系统命令状态"""
    global _last_refresh_time
    if _global_scan_instance is None:
        return {"initialized": False}
    
    return {
        "initialized": True,
        "cmd_count": len(_global_scan_instance.current_sys_cmds.get(_global_scan_instance.sys_type, [])),
        "last_refresh_time": _last_refresh_time,
        "last_refresh_ago": time.time() - _last_refresh_time if _last_refresh_time > 0 else -1,
        "is_refreshing": _global_scan_instance._is_refreshing,
        "dir_cache_size": len(_global_scan_instance._dir_persistent_cache),
        "debug_mode": _global_scan_instance.debug_mode,
        "debug_cache_path": getattr(_global_scan_instance, '_debug_cache_path', None)
    }


# ==================== 兼容原有接口 ====================

if __name__ == "__main__":
    # 性能测试
    import time
    
    test_root = os.path.abspath("../../")
    test_home = os.path.join(test_root, "home", "default")
    test_cmd_mapping_path = os.path.join(test_home, ".cache", "onyx", "cmd_mapping.msgpack")
    
    if sys.platform.startswith("win32"):
        sys_type = "Windows"
    elif sys.platform == "darwin":
        sys_type = "macOS"
    else:
        sys_type = "Linux/macOS"
    
    print(f"\n{'='*60}")
    print("系统命令扫描测试")
    print('='*60)
    
    start = time.time()
    init_scan_path_cmds(
        root_dir=test_root,
        user_home_dir=test_home,
        sys_type=sys_type,
        builtin_commands={"cd": lambda x, y: None, "ls": lambda x, y: None},
        tool_index_cache={},
        cmd_mapping_msg_path=test_cmd_mapping_path,
        max_workers=8,
        debug_mode=True  # 启用调试模式
    )
    
    result = scan_path_for_system_cmds(force_scan=True)  # 强制扫描
    elapsed = (time.time() - start) * 1000
    
    cmd_count = len(result[sys_type]) if sys_type in result else 0
    print(f"结果: {cmd_count} 个命令 | 耗时: {elapsed:.1f}ms")
    
    # 显示调试缓存位置
    debug_cache = os.path.join(os.path.dirname(test_cmd_mapping_path), "debug_scan_path.cache")
    if os.path.exists(debug_cache):
        size = os.path.getsize(debug_cache)
        print(f"\n🐛 调试缓存已保存: {debug_cache}")
        print(f"   文件大小: {size} bytes")
    
    print(f"\n✅ 扫描完成！")