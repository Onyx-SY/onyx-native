# lib/build_tool_index.py - 真正的异步非阻塞版本（支持刷新接口）

import os
import sys
import json
import time
import hashlib
import threading
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import dataclass, asdict, field
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, Future
import msgpack

# ==================== 配置 ====================
MAX_WORKERS = os.cpu_count() or 4  # 线程池大小
BATCH_COMMIT_SIZE = 500
CACHE_TTL = 86400  # 24小时，实际不过期，只是用于判断
CACHE_VERSION = 5

# 文件过滤
SUPPORTED_MAIN_FILES = {"Main.py", "Main.pyc", "main.py", "main.pyc", 
                        "tool.py", "tool.pyc", "entry.py", "entry.pyc"}
MAIN_KEYWORDS = {"main", "entry", "start", "launch", "run", "init"}
EXEC_EXTENSIONS = {'.py', '.pyc', '.sh', '.bash', '.exe', '.bin'}

TOOL_TYPE_PRIORITY = [
    ("exploit", "exploit"),
    ("wireless", "wireless"),
    ("crack", "crack"),
    ("scan", "scan"),
    ("web", "web"),
    ("app", "app"),
]


@dataclass
class ToolInfo:
    """工具信息数据类"""
    path: str
    is_cli: bool
    tool_perm: int
    tool_type: str
    name: str = ""
    size: int = 0
    mtime: float = 0
    hash: str = ""
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "ToolInfo":
        return cls(**data)


class ToolIndexCache:
    """线程安全的缓存管理器"""
    
    def __init__(self, cache_path: str, ttl: int = CACHE_TTL):
        self.cache_path = cache_path
        self.ttl = ttl
        self._cache: Dict[str, ToolInfo] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._version = CACHE_VERSION
        
    def get(self, key: str) -> Optional[ToolInfo]:
        with self._lock:
            return self._cache.get(key)
    
    def set(self, key: str, value: ToolInfo):
        with self._lock:
            self._cache[key] = value
            self._dirty = True
    
    def set_batch(self, items: Dict[str, ToolInfo]):
        with self._lock:
            self._cache.update(items)
            self._dirty = True
    
    def delete(self, key: str):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._dirty = True
    
    def clear(self):
        with self._lock:
            self._cache.clear()
            self._dirty = True
    
    def get_all(self) -> Dict[str, ToolInfo]:
        with self._lock:
            return self._cache.copy()
    
    def load(self) -> bool:
        """加载缓存文件"""
        if not os.path.exists(self.cache_path):
            return False
        
        try:
            with open(self.cache_path, 'rb') as f:
                data = f.read()
                cache_data = msgpack.unpackb(data, raw=False)
            
            if cache_data.get('_version', 0) != self._version:
                return False
            
            # 不检查TTL，缓存永不过期（但版本不匹配时重新构建）
            # timestamp = cache_data.get('_timestamp', 0)
            # if time.time() - timestamp > self.ttl:
            #     return False
            
            tools_data = cache_data.get('_tools', {})
            with self._lock:
                for key, value in tools_data.items():
                    self._cache[key] = ToolInfo.from_dict(value)
            
            return True
        except Exception:
            return False
    
    def save(self, tool_count: int = 0):
        """保存缓存文件"""
        with self._lock:
            if not self._dirty and not tool_count:
                return
            
            cache_dir = os.path.dirname(self.cache_path)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir, mode=0o755, exist_ok=True)
            
            cache_data = {
                '_timestamp': time.time(),
                '_version': self._version,
                '_tool_count': tool_count or len(self._cache),
                '_tools': {k: v.to_dict() for k, v in self._cache.items()}
            }
            
            temp_path = f"{self.cache_path}.tmp"
            with open(temp_path, 'wb') as f:
                f.write(msgpack.packb(cache_data))
            
            os.replace(temp_path, self.cache_path)
            self._dirty = False


class AsyncToolIndexBuilder:
    """真正的异步非阻塞工具索引构建器（后台线程池）"""
    
    def __init__(self, max_workers: int = MAX_WORKERS):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tool_scanner")
        self._cache: Optional[ToolIndexCache] = None
        self._building = False
        self._build_future: Optional[Future] = None
        self._on_complete_callbacks: List[callable] = []
        self._lock = threading.RLock()
        self._stats = {"scanned": 0, "found": 0, "errors": 0}
        # 保存配置供刷新使用
        self._tool_main_dir = ""
        self._sys_type = ""
        
    def set_cache(self, cache: ToolIndexCache):
        self._cache = cache
    
    def _scan_tool_directory_sync(self, tool_path: str, sys_type: str) -> Optional[Tuple[str, ToolInfo]]:
        """同步扫描单个工具目录（在线程池中执行）"""
        try:
            tool_name = os.path.basename(tool_path)
            
            # 查找入口文件
            entry_file = self._find_tool_entry(tool_path)
            if not entry_file:
                return None
            
            tool_file = os.path.join(tool_path, entry_file)
            
            if not os.path.exists(tool_file):
                return None
            
            # 获取文件状态
            try:
                stat = os.stat(tool_file)
            except OSError:
                return None
            
            # 读取配置
            perm, is_cli = self._read_config(tool_path)
            tool_type = self._get_tool_type(tool_name)
            dir_hash = self._compute_dir_hash(tool_path)
            
            with self._lock:
                self._stats["scanned"] += 1
                self._stats["found"] += 1
            
            cache_key = f"{tool_name}_{sys_type}"
            tool_info = ToolInfo(
                path=tool_file,
                is_cli=is_cli,
                tool_perm=perm,
                tool_type=tool_type,
                name=tool_name,
                size=stat.st_size,
                mtime=stat.st_mtime,
                hash=dir_hash
            )
            
            return (cache_key, tool_info)
        except Exception:
            with self._lock:
                self._stats["errors"] += 1
            return None
    
    def _find_tool_entry(self, tool_path: str) -> Optional[str]:
        """查找工具入口文件"""
        try:
            files = os.listdir(tool_path)
        except (PermissionError, OSError):
            return None
        
        tool_name = os.path.basename(tool_path).lower()
        
        # 优先匹配工具名
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in EXEC_EXTENSIONS:
                continue
            base = os.path.splitext(f)[0].lower()
            if base == tool_name:
                return f
        
        # 标准入口文件
        for f in SUPPORTED_MAIN_FILES:
            if f in files:
                return f
        
        # 关键词匹配
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in EXEC_EXTENSIONS:
                continue
            base = os.path.splitext(f)[0].lower()
            for kw in MAIN_KEYWORDS:
                if kw in base:
                    return f
        
        return None
    
    def _read_config(self, tool_dir: str) -> Tuple[int, bool]:
        """读取工具配置"""
        perm = 3
        is_cli = True
        
        config_path = os.path.join(tool_dir, "config.conf")
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('cli='):
                            cli_val = line[4:].strip()
                            is_cli = cli_val in ('1', '2')
                        elif line.startswith('perm='):
                            try:
                                perm = int(line[5:].strip())
                                perm = max(1, min(perm, 5))
                            except:
                                pass
            except:
                pass
        
        return perm, is_cli
    
    def _get_tool_type(self, tool_name: str) -> str:
        tool_name_lower = tool_name.lower()
        for keyword, tool_type in TOOL_TYPE_PRIORITY:
            if keyword in tool_name_lower:
                return tool_type
        return "other"
    
    def _compute_dir_hash(self, tool_dir: str) -> str:
        """计算目录哈希"""
        try:
            hasher = hashlib.md5()
            entries = []
            for f in os.listdir(tool_dir):
                if f.endswith(('.py', '.pyc', '.sh', '.conf')):
                    fpath = os.path.join(tool_dir, f)
                    if os.path.isfile(fpath):
                        stat = os.stat(fpath)
                        entries.append(f"{f}:{stat.st_mtime}:{stat.st_size}")
            entries.sort()
            hasher.update(''.join(entries).encode())
            return hasher.hexdigest()[:8]
        except Exception:
            return ""
    
    def _get_all_tool_dirs(self, tool_main_dir: str) -> List[str]:
        """获取所有工具目录"""
        tool_dirs = []
        
        if not os.path.exists(tool_main_dir):
            return tool_dirs
        
        try:
            for cat in os.listdir(tool_main_dir):
                if cat.startswith('.'):
                    continue
                cat_path = os.path.join(tool_main_dir, cat)
                if not os.path.isdir(cat_path):
                    continue
                
                for tool in os.listdir(cat_path):
                    if tool.startswith('.'):
                        continue
                    tool_path = os.path.join(cat_path, tool)
                    if os.path.isdir(tool_path):
                        tool_dirs.append(tool_path)
        except OSError:
            pass
        
        return tool_dirs
    
    def _build_index_sync(self, tool_main_dir: str, sys_type: str, 
                          progress_callback=None) -> Dict[str, ToolInfo]:
        """同步构建（在线程池中执行）"""
        with self._lock:
            self._stats = {"scanned": 0, "found": 0, "errors": 0}
        
        # 保存配置供刷新使用
        self._tool_main_dir = tool_main_dir
        self._sys_type = sys_type
        
        # 获取所有工具目录
        tool_dirs = self._get_all_tool_dirs(tool_main_dir)
        
        if not tool_dirs:
            return {}
        
        # 使用线程池并发扫描
        results = {}
        total = len(tool_dirs)
        processed = 0
        
        # 提交所有任务
        futures = []
        for tool_path in tool_dirs:
            future = self._executor.submit(self._scan_tool_directory_sync, tool_path, sys_type)
            futures.append(future)
        
        # 收集结果
        for future in futures:
            try:
                result = future.result(timeout=30)
                processed += 1
                if result:
                    key, info = result
                    results[key] = info
                
                if progress_callback and processed % 50 == 0:
                    with self._lock:
                        progress_callback(processed, total, self._stats.copy())
            except Exception:
                with self._lock:
                    self._stats["errors"] += 1
        
        return results
    
    def start_build_async(self, tool_main_dir: str, sys_type: str, 
                          cache_path: str, on_complete: callable = None,
                          progress_callback: callable = None,
                          force_rebuild: bool = False) -> bool:
        """
        启动异步非阻塞构建
        立即返回，构建在后台线程中进行
        
        参数:
            tool_main_dir: 工具主目录
            sys_type: 系统类型
            cache_path: 缓存路径
            on_complete: 完成回调
            progress_callback: 进度回调
            force_rebuild: 是否强制重建（忽略缓存）
        """
        with self._lock:
            # 如果正在构建且不是强制重建，将回调加入队列
            if self._building and not force_rebuild:
                if on_complete:
                    self._on_complete_callbacks.append(on_complete)
                return False
            
            # 保存配置
            self._tool_main_dir = tool_main_dir
            self._sys_type = sys_type
            
            self._building = True
            if on_complete:
                self._on_complete_callbacks.append(on_complete)
        
        def build_task():
            try:
                # 强制重建时，清除缓存
                if force_rebuild and self._cache:
                    self._cache.clear()
                    # 重新加载缓存（可能为空）
                    self._cache.load()
                
                # 构建索引
                results = self._build_index_sync(tool_main_dir, sys_type, progress_callback)
                
                # 保存缓存
                if self._cache:
                    self._cache.set_batch(results)
                    self._cache.save(len(results))
                
                # 触发完成回调
                with self._lock:
                    callbacks = self._on_complete_callbacks.copy()
                    self._on_complete_callbacks.clear()
                
                for callback in callbacks:
                    try:
                        callback(results)
                    except Exception:
                        pass
                
                return results
            except Exception as e:
                with self._lock:
                    callbacks = self._on_complete_callbacks.copy()
                    self._on_complete_callbacks.clear()
                
                for callback in callbacks:
                    try:
                        callback({})
                    except Exception:
                        pass
                return {}
            finally:
                with self._lock:
                    self._building = False
                    self._build_future = None
        
        # 提交到线程池执行（非阻塞）
        self._build_future = self._executor.submit(build_task)
        return True
    
    def is_building(self) -> bool:
        with self._lock:
            return self._building
    
    def get_cached_tools(self) -> Dict[str, ToolInfo]:
        """获取缓存的工具（立即返回，不等待）"""
        if self._cache:
            return self._cache.get_all()
        return {}
    
    def wait_for_build(self, timeout: float = None) -> Optional[Dict[str, ToolInfo]]:
        """等待构建完成（会阻塞，不推荐在交互中使用）"""
        if self._build_future:
            try:
                return self._build_future.result(timeout=timeout)
            except:
                pass
        return self.get_cached_tools()
    
    def get_stats(self) -> Dict:
        with self._lock:
            return self._stats.copy()
    
    def shutdown(self):
        """关闭线程池"""
        self._executor.shutdown(wait=False)


# ==================== 全局状态 ====================
_builder: Optional[AsyncToolIndexBuilder] = None
_cache: Optional[ToolIndexCache] = None
_refresh_lock = threading.Lock()
_last_refresh_time = 0
_REFRESH_INTERVAL = 30  # 30秒内不重复刷新


def get_builder() -> AsyncToolIndexBuilder:
    global _builder
    if _builder is None:
        _builder = AsyncToolIndexBuilder()
    return _builder


def get_cache(cache_path: str) -> ToolIndexCache:
    global _cache
    if _cache is None or _cache.cache_path != cache_path:
        _cache = ToolIndexCache(cache_path)
    return _cache


# ==================== 刷新接口 ====================

def refresh_tool_index(
    request_id: str = "",
    force: bool = False,
    log_info_func=None,
    log_error_func=None
) -> bool:
    """
    异步刷新工具索引（命令执行前调用）
    
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
    global _last_refresh_time
    
    # 检查刷新间隔
    current_time = time.time()
    if not force and (current_time - _last_refresh_time) < _REFRESH_INTERVAL:
        if log_info_func:
            log_info_func(f"工具索引刷新跳过（距上次刷新{int(current_time - _last_refresh_time)}秒）", request_id)
        return False
    
    # 检查是否已在刷新中
    builder = get_builder()
    if builder.is_building():
        if log_info_func:
            log_info_func("工具索引已在刷新中，跳过", request_id)
        return False
    
    # 检查是否有缓存路径
    if _cache is None:
        if log_error_func:
            log_error_func("工具索引模块未初始化", request_id)
        return False
    
    # 获取工具主目录（从缓存路径推断或使用默认）
    tool_main_dir = ""
    sys_type = ""
    
    # 尝试从 builder 获取保存的配置
    if hasattr(builder, '_tool_main_dir') and builder._tool_main_dir:
        tool_main_dir = builder._tool_main_dir
        sys_type = builder._sys_type
    
    if not tool_main_dir:
        if log_error_func:
            log_error_func("无法获取工具主目录配置，请先调用 init_tool_index", request_id)
        return False
    
    def on_refresh_complete(results):
        """刷新完成回调"""
        if log_info_func:
            log_info_func(f"工具索引刷新完成：共{len(results)}个工具", request_id)
    
    # 启动异步刷新（force=True 强制重新扫描）
    with _refresh_lock:
        _last_refresh_time = current_time
        success = builder.start_build_async(
            tool_main_dir=tool_main_dir,
            sys_type=sys_type,
            cache_path=_cache.cache_path,
            on_complete=on_refresh_complete,
            force_rebuild=force  # 强制重新扫描
        )
    
    if success and log_info_func:
        log_info_func("工具索引后台刷新已启动", request_id)
    
    return success


def get_tool_index_status() -> Dict[str, Any]:
    """获取工具索引状态"""
    global _last_refresh_time
    builder = get_builder()
    return {
        "is_building": builder.is_building(),
        "cached_count": len(builder.get_cached_tools()),
        "last_refresh_time": _last_refresh_time,
        "last_refresh_ago": time.time() - _last_refresh_time if _last_refresh_time > 0 else -1
    }


# ==================== 公开接口（保持原有函数签名） ====================

def init_tool_index(root_dir: str, user_home_dir: str, sys_type: str,
                    tool_main_dir: str, cache_path: str,
                    log_info_func=None, request_id="") -> None:
    """
    初始化工具索引模块（同步，但只是设置缓存）
    这个函数立即返回，不阻塞
    """
    cache = get_cache(cache_path)
    builder = get_builder()
    builder.set_cache(cache)
    
    # 保存配置供刷新使用
    builder._tool_main_dir = tool_main_dir
    builder._sys_type = sys_type
    
    # 尝试加载缓存
    loaded = cache.load()
    if loaded and log_info_func:
        tools = cache.get_all()
        log_info_func(f"加载已有缓存：{len(tools)}个工具", request_id)
    elif log_info_func:
        log_info_func("无缓存，将在后台构建工具索引", request_id)


def build_tool_index(root_dir: str, user_home_dir: str, sys_type: str,
                      tool_main_dir: str, cache_path: str,
                      log_info_func=None, log_error_func=None,
                      request_id="", force_rebuild: bool = False) -> Dict[str, Any]:
    """
    构建工具索引 - 使用异步非阻塞方式
    
    参数:
        force_rebuild: 是否强制重建（忽略缓存）
    
    返回:
        工具索引字典（立即返回缓存，后台更新）
    """
    cache = get_cache(cache_path)
    builder = get_builder()
    builder.set_cache(cache)
    
    # 保存配置供刷新使用
    builder._tool_main_dir = tool_main_dir
    builder._sys_type = sys_type
    
    # 检查是否需要强制重建
    if force_rebuild:
        cache.clear()
    
    # 获取当前缓存（立即返回）
    cached_tools = builder.get_cached_tools()
    
    # 如果已经在构建中，不重复启动
    if builder.is_building():
        if log_info_func:
            log_info_func(f"工具索引已在后台构建中，当前缓存：{len(cached_tools)}个工具", request_id)
        
        # 转换格式后返回
        result = {}
        for key, info in cached_tools.items():
            if hasattr(info, 'to_dict'):
                result[key] = info.to_dict()
            elif hasattr(info, '__dict__'):
                result[key] = info.__dict__
            else:
                result[key] = info
        return result
    
    # 如果缓存为空或者需要更新，启动后台构建
    if not cached_tools or force_rebuild:
        if log_info_func:
            log_info_func(f"启动后台工具索引构建...", request_id)
        
        def on_build_complete(results):
            if log_info_func:
                log_info_func(f"后台工具索引构建完成：共{len(results)}个工具", request_id)
        
        builder.start_build_async(
            tool_main_dir=tool_main_dir,
            sys_type=sys_type,
            cache_path=cache_path,
            on_complete=on_build_complete,
            force_rebuild=force_rebuild
        )
    else:
        if log_info_func:
            log_info_func(f"使用缓存的工具索引：{len(cached_tools)}个工具", request_id)
    
    # 转换格式（保持与原有代码兼容）
    result = {}
    for key, info in cached_tools.items():
        if hasattr(info, 'to_dict'):
            result[key] = info.to_dict()
        elif hasattr(info, '__dict__'):
            result[key] = info.__dict__
        else:
            result[key] = info
    
    return result


def build_tool_index_sync(root_dir: str, user_home_dir: str, sys_type: str,
                           tool_main_dir: str, cache_path: str,
                           log_info_func=None, log_error_func=None,
                           request_id="", force_rebuild: bool = False) -> Dict[str, Any]:
    """
    同步构建工具索引（会阻塞等待完成）
    仅在确实需要等待构建完成的场景使用
    """
    cache = get_cache(cache_path)
    builder = get_builder()
    builder.set_cache(cache)
    
    # 保存配置供刷新使用
    builder._tool_main_dir = tool_main_dir
    builder._sys_type = sys_type
    
    if force_rebuild:
        cache.clear()
    
    # 如果已经在构建中，等待完成
    if builder.is_building():
        if log_info_func:
            log_info_func(f"等待后台构建完成...", request_id)
        builder.wait_for_build()
    
    # 获取当前缓存
    cached_tools = builder.get_cached_tools()
    
    # 如果有缓存且不需要强制重建，直接返回
    if cached_tools and not force_rebuild:
        result = {}
        for key, info in cached_tools.items():
            if hasattr(info, 'to_dict'):
                result[key] = info.to_dict()
            elif hasattr(info, '__dict__'):
                result[key] = info.__dict__
            else:
                result[key] = info
        return result
    
    # 同步构建
    if log_info_func:
        log_info_func(f"开始同步构建工具索引...", request_id)
    
    results = builder._build_index_sync(tool_main_dir, sys_type, None)
    
    if cache:
        cache.set_batch(results)
        cache.save(len(results))
    
    if log_info_func:
        log_info_func(f"同步构建完成：共{len(results)}个工具", request_id)
    
    # 转换格式
    result = {}
    for key, info in results.items():
        if hasattr(info, 'to_dict'):
            result[key] = info.to_dict()
        elif hasattr(info, '__dict__'):
            result[key] = info.__dict__
        else:
            result[key] = info
    
    return result


def get_cached_tool(tool_name: str, sys_type: str) -> Optional[Dict]:
    """获取缓存的工具（同步，立即返回）"""
    builder = get_builder()
    cache_key = f"{tool_name}_{sys_type}"
    tool = builder.get_cached_tools().get(cache_key)
    if tool:
        if hasattr(tool, 'to_dict'):
            return tool.to_dict()
        elif hasattr(tool, '__dict__'):
            return tool.__dict__
        return tool
    return None


def is_building() -> bool:
    """检查是否正在构建"""
    return get_builder().is_building()


def wait_for_build(timeout: float = None) -> bool:
    """等待构建完成（会阻塞，谨慎使用）"""
    builder = get_builder()
    result = builder.wait_for_build(timeout)
    return result is not None