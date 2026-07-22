# lib/parse_and_execute.py
"""
命令解析与执行入口模块
协调 parse、safe 模块完成：命令展开 → 安全检查 → 命令执行

核心流程：
1. 命令展开（变量、通配符、别名等）
2. 按分隔符拆分（; → &&/|| → 管道检测）
3. 判断命令类型（内置/其余终端内置/工具/系统/未知）
4. 安全检查（路径权限、高危命令、模式权限）
5. 执行命令
6. 执行后自动同步 Shell 进程的工作目录到 Python 进程

注意：变量不再由 Python 层展开，而是保留原样交给底层 Shell 处理
"""

import os
import sys
import uuid
import shlex
import tempfile
import threading
import re
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional, Callable
from lib.terminal.colors import Fore, Style

from .parse import (
    smart_shlex_split, check_quotes_balanced,
    expand_variables, expand_tilde, expand_braces, expand_wildcards,
    remove_comments, resolve_path_to_absolute,
    extract_all_argument_paths, extract_redirect_paths,
    has_pipeline, is_shell_logic_structure,
    extract_sub_commands_from_pipeline,
    split_by_semicolon, split_by_logical_operators,
    parse_redirects_from_command,
    extract_all_paths_from_pipeline,
    resolve_alias_in_cmd,
    check_advanced_syntax,
    has_logical_operators,
    resolve_paths_in_multiline_text,
    should_try_path_resolution,
    resolve_token_path,
    is_executable_binary,
    handle_executable_path,
    _is_single_line_block,
    _is_subshell,
    clear_path_mapping_cache,
)
from .safe import (
    load_perm_path_config, set_perm_debug_flag,
    is_path_under_fine_grained_control,
    check_path_permission_for_cmd,
    is_command_allowed_in_mode,
    is_in_protected_dir,
    check_dangerous_commands,
    safe_input,
    check_tool_permission,
    check_fine_grained_advanced_syntax,
    check_protected_dir_for_cmd,
    get_mode_max_perm,
    set_lang,
    _get_msg,
    is_any_path_under_fine_grained_control,
    load_other_terminal_commands,
    is_other_terminal_command,
    load_adv_danger_cmd_config,
    is_adv_danger_cmd_prompt_enabled,
    clear_adv_confirm_memory,
    _adv_confirm_with_disable_option,
    _ADV_FORCE_CONFIRM_COMMANDS,
    check_variable_command_in_fine_grained_path,
)

# === 新增：直接读取 msgpack 缓存文件 ===
try:
    from lib.makecache import load_msgpack
except ImportError:
    # 后备加载函数
    def load_msgpack(path: str) -> Optional[Any]:
        try:
            import msgpack
            with open(path, 'rb') as f:
                return msgpack.unpack(f, raw=False)
        except Exception:
            return None

# === 导入终端类型判断模块 ===
try:
    from onyx.lib.get_terminal_type import get_terminal_type
except ImportError:
    def get_terminal_type():
        """后备终端类型判断"""
        if sys.platform == 'win32':
            return 'cmd'
        shell = os.environ.get('SHELL', '')
        if 'zsh' in shell:
            return 'zsh'
        elif 'fish' in shell:
            return 'fish'
        elif 'bash' in shell:
            return 'bash'
        return 'sh'

# === 导入持久化 Shell 的变量读取函数 ===
try:
    from onyx.lib.exe import get_var_from_shell, get_functions_from_shell, get_shell_cwd, is_shell_alive
except ImportError:
    def get_var_from_shell(var_name: str) -> Optional[str]:
        return None
    
    def get_functions_from_shell() -> List[str]:
        return []
    
    def get_shell_cwd() -> Optional[str]:
        return None
    
    def is_shell_alive() -> bool:
        return False


# ========== 新增：直接读取 msgpack 缓存的函数 ==========

# 模块级缓存：缓存目录路径（会话内不变）
_CACHED_CACHE_DIR: Optional[str] = None

def _get_cache_dir() -> str:
    """获取缓存目录路径（模块级缓存，首次计算后直接返回）"""
    global _CACHED_CACHE_DIR
    if _CACHED_CACHE_DIR is not None:
        return _CACHED_CACHE_DIR
    
    # 优先使用环境变量
    cache_dir = os.environ.get("ONYX_CACHE_DIR", "")
    if cache_dir and os.path.exists(cache_dir):
        _CACHED_CACHE_DIR = cache_dir
        return cache_dir
    
    # 使用用户主目录下的缓存路径
    home_dir = os.path.expanduser("~")
    cache_dir = os.path.join(home_dir, ".cache", "onyx", "onyx")
    
    # 如果不存在，尝试从 ROOT_DIR 推导
    if not os.path.exists(cache_dir):
        # 尝试获取当前文件所在项目的根目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        for _ in range(5):
            if os.path.basename(project_root) == "onyx":
                project_root = os.path.dirname(project_root)
                break
            project_root = os.path.dirname(project_root)
        
        cache_dir = os.path.join(project_root, "home", os.environ.get("USER", "default"), ".cache", "onyx", "onyx")
        if not os.path.exists(cache_dir):
            cache_dir = os.path.join(home_dir, ".cache", "onyx", "onyx")
    
    _CACHED_CACHE_DIR = cache_dir
    return cache_dir


def _load_tool_index_cache() -> Dict[str, Any]:
    """
    直接从 msgpack 文件加载工具索引缓存
    返回: {工具名: ToolInfo 数据字典}
    
    注意：tool_index.msgpack 的顶层结构为 {_timestamp, _version, _tool_count, _tools}
    真正的工具数据在 _tools 子字典中，必须提取出来，否则 merge 到 cmd_mapping["tools"]
    后会变成 {_timestamp, _version, _tool_count, _tools: {工具名...}} 的嵌套结构，
    导致 _is_tool_command_local 永远找不到工具命令。
    """
    cache_dir = _get_cache_dir()
    tool_index_path = os.path.join(cache_dir, "tool_index.msgpack")
    
    if os.path.exists(tool_index_path):
        try:
            data = load_msgpack(tool_index_path)
            if isinstance(data, dict):
                # 提取 _tools 子字典（兼容没有 _tools 键的旧格式回退）
                return data.get("_tools", data) if "_tools" in data else data
        except Exception as e:
            print(Fore.YELLOW + f"[警告] 加载工具索引缓存失败: {e}" + Style.RESET_ALL)
    
    return {}


def _load_cmd_mapping_cache(sys_type: str) -> Dict[str, Any]:
    """
    直接从 msgpack 文件加载命令映射缓存
    返回: 当前系统类型的命令映射 {tools: {...}, system: [...], _system_set: frozenset}
    _system_set 是预计算的 O(1) 查找集合，在返回前统一添加。
    """
    cache_dir = _get_cache_dir()
    cmd_mapping_path = os.path.join(cache_dir, "cmd_mapping.msgpack")
    
    mapping = {"tools": {}, "system": []}
    
    if os.path.exists(cmd_mapping_path):
        try:
            data = load_msgpack(cmd_mapping_path)
            if isinstance(data, dict) and sys_type in data:
                sys_data = data[sys_type]
                if isinstance(sys_data, dict):
                    # 检查是否过期
                    timestamp = sys_data.get("timestamp", 0)
                    ttl = sys_data.get("ttl", 3600)
                    import time
                    if time.time() - timestamp < ttl:
                        mapping = sys_data.get("mapping", mapping)
                    else:
                        mapping = sys_data.get("mapping", mapping)
        except Exception as e:
            print(Fore.YELLOW + f"[警告] 加载命令映射缓存失败: {e}" + Style.RESET_ALL)
    
    # 始终预计算 _system_set，确保 O(1) 查找
    system_cmds = mapping.get("system", [])
    if isinstance(system_cmds, list):
        mapping["_system_set"] = frozenset(c.lower() for c in system_cmds)
    else:
        mapping["_system_set"] = frozenset()
    return mapping


class _LocalToolInfo:
    """本地工具信息类，替代外部 ToolInfo"""
    def __init__(self, path: str, is_cli: bool = False, tool_perm: int = 3, tool_type: str = "other"):
        self.path = path
        self.is_cli = is_cli
        self.tool_perm = tool_perm
        self.tool_type = tool_type


# ========== 本地相似命令查找函数 ==========

def _find_similar_tools_local(wrong_cmd: str, tool_cache: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    本地查找相似工具命令（排除自身和内部 _ 前缀名称）
    """
    similar_tools = []
    wrong_lower = wrong_cmd.lower()
    
    for tool_name, tool_info in tool_cache.items():
        # 跳过内部变量/函数（_ 前缀）
        if tool_name.startswith('_'):
            continue
        # 跳过完全匹配自身
        if tool_name.lower() == wrong_lower:
            continue
        if wrong_lower in tool_name.lower():
            tool_type = tool_info.get("tool_type", "other") if isinstance(tool_info, dict) else "other"
            similar_tools.append((tool_name, tool_type))
    
    return similar_tools[:10]


def _find_similar_cmds_local(wrong_cmd: str, cmd_mapping: Dict[str, Any]) -> List[str]:
    """
    本地查找相似系统命令（排除自身和内部 _ 前缀名称）
    """
    similar_cmds = []
    wrong_lower = wrong_cmd.lower()
    
    system_cmds = cmd_mapping.get("system", [])
    if isinstance(system_cmds, list):
        for cmd in system_cmds:
            # 跳过内部变量/函数（_ 前缀）
            if cmd.startswith('_'):
                continue
            # 跳过完全匹配自身
            if cmd.lower() == wrong_lower:
                continue
            if wrong_lower in cmd.lower():
                similar_cmds.append(cmd)
                if len(similar_cmds) >= 10:
                    break
    
    return similar_cmds


def _get_cached_cmd_local(cmd_name: str, tool_cache: Dict[str, Any], 
                           cmd_mapping: Dict[str, Any],
                           builtin_commands: Dict) -> Tuple[str, Any]:
    """
    本地获取缓存命令，不依赖外部函数
    返回: (类型, 信息) 类型: 'builtins', 'tools', 'system', 'none'
    """
    cmd_lower = cmd_name.lower()
    
    # 1. 优先匹配内置命令
    if builtin_commands and cmd_lower in builtin_commands:
        return ("builtins", builtin_commands[cmd_lower])
    
    # 2. 匹配工具命令
    tools = cmd_mapping.get("tools", {})
    if cmd_lower in tools:
        return ("tools", tools[cmd_lower])
    
    # 3. 匹配系统命令
    system_cmds = cmd_mapping.get("system", [])
    if isinstance(system_cmds, list):
        for sys_cmd in system_cmds:
            if sys_cmd.lower() == cmd_lower:
                return ("system", sys_cmd)
    
    return ("none", None)


def _is_quoted_pipe(cmd_str: str, sys_type: str = 'bash') -> bool:
    """
    检查管道符是否在引号内
    返回 True 表示所有管道符都在引号内，False 表示存在不在引号内的管道符
    """
    if '|' not in cmd_str:
        return True
    
    rules = _get_terminal_escape_rules(sys_type)
    escape_char = rules['escape_char']
    has_single_quotes = rules['has_single_quotes']
    
    in_single = False
    in_double = False
    escaped = False
    
    for i, char in enumerate(cmd_str):
        if escaped:
            escaped = False
            continue
            
        if char == escape_char:
            escaped = True
        elif char == '\\' and sys_type not in ('cmd',):
            escaped = True
        elif char == "'" and not in_double and has_single_quotes:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == '|' and not in_single and not in_double:
            return False
    
    return True


# 模块级缓存：终端转义规则（按 sys_type 缓存，会话内不变）
_CACHED_TERMINAL_ESCAPE_RULES: Dict[str, Dict[str, Any]] = {}

def _get_terminal_escape_rules(sys_type: str) -> Dict[str, Any]:
    """获取终端转义规则（模块级缓存，首次计算后直接返回）"""
    if sys_type in _CACHED_TERMINAL_ESCAPE_RULES:
        return _CACHED_TERMINAL_ESCAPE_RULES[sys_type]
    
    if sys_type == "cmd":
        rules = {
            'escape_char': '^',
            'has_single_quotes': False,
            'single_quote_escape': False
        }
    elif sys_type == "powershell":
        rules = {
            'escape_char': '`',
            'has_single_quotes': True,
            'single_quote_escape': True
        }
    else:
        rules = {
            'escape_char': '\\',
            'has_single_quotes': True,
            'single_quote_escape': False
        }
    _CACHED_TERMINAL_ESCAPE_RULES[sys_type] = rules
    return rules


# ========== 变量赋值模式 ==========
_VARIABLE_ASSIGNMENT_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*=.*')


def _is_variable_assignment(cmd_head: str) -> bool:
    """检查命令头是否是变量赋值（如 a=5, PATH=/bin, c=0）"""
    if not cmd_head:
        return False
    return bool(_VARIABLE_ASSIGNMENT_PATTERN.match(cmd_head))


def _is_sudo_command(cmd_head: str) -> bool:
    """检查命令头是否是 sudo"""
    return cmd_head.lower() == 'sudo'


def _collect_all_paths_from_command(cmd_str: str, redirect_config: Dict,
                                     resolve_path_func=None) -> List[str]:
    """收集命令中的所有物理路径（参数路径 + 重定向路径）"""
    all_paths = []
    
    arg_paths = extract_all_argument_paths(cmd_str, resolve_path_func)
    all_paths.extend(arg_paths)
    
    redirect_paths = extract_redirect_paths(redirect_config)
    for rp in redirect_paths:
        if rp and rp != 'STDOUT':
            resolved = resolve_path_func(rp) if resolve_path_func else resolve_path_to_absolute(rp)
            if resolved:
                all_paths.append(resolved)
    
    return all_paths


def _collect_and_resolve_paths(cmd_str: str, redirect_config: Dict,
                                resolve_path_func, cwd: str) -> 'Tuple[List[str], List[str]]':
    """
    收集并解析命令中的所有路径（包括 CWD）。
    返回 (all_paths, resolved_paths) — 消除 4 处重复代码。
    """
    all_paths = _collect_all_paths_from_command(cmd_str, redirect_config, resolve_path_func)
    all_paths.append(cwd)
    
    resolved_paths = []
    for p in all_paths:
        if p and p != 'STDOUT':
            resolved = resolve_path_func(p) if resolve_path_func else resolve_path_to_absolute(p)
            if resolved:
                resolved_paths.append(resolved)
    
    # CWD 必须纳入检查（即使解析失败 — 虚拟文件系统下路径可能不物理存在）
    if cwd and cwd not in resolved_paths:
        cwd_resolved = resolve_path_func(cwd) if resolve_path_func else resolve_path_to_absolute(cwd)
        resolved_paths.append(cwd_resolved or cwd)
    
    return all_paths, resolved_paths


def _resolve_paths_in_bash_structure(cmd_str: str, resolve_path_func=None) -> str:
    """
    在 shell 逻辑结构（多行命令）中解析路径
    
    对多行内容中的每个 token 进行虚拟路径转换
    """
    if not cmd_str or not resolve_path_func:
        return cmd_str
    
    return resolve_paths_in_multiline_text(cmd_str, resolve_path_func)


def _collect_all_paths_from_bash_structure(cmd_str: str, resolve_path_func=None) -> List[str]:
    """
    从 shell 逻辑结构（多行命令）中收集所有可能的路径
    
    对多行内容中的每个 token 尝试路径解析，收集解析成功的路径
    """
    all_paths = []
    
    if not cmd_str or not resolve_path_func:
        return all_paths
    
    lines = cmd_str.split('\n')
    
    for line in lines:
        if not line.strip():
            continue
        
        tokens = line.split(' ')
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            
            if not should_try_path_resolution(token):
                continue
            
            resolved = resolve_token_path(token, resolve_path_func)
            if resolved and resolved != token:
                all_paths.append(resolved)
    
    return all_paths


def _is_builtin_command(cmd_head: str, builtin_commands: Dict) -> bool:
    """检查是否是内置命令"""
    return bool(builtin_commands and cmd_head.lower() in builtin_commands)


def _is_other_terminal_command(cmd_head: str, sys_type: str,
                                other_terminal_cmds: Dict) -> bool:
    """检查是否是其余终端内置命令"""
    return is_other_terminal_command(cmd_head, sys_type, other_terminal_cmds)


def _is_tool_command_local(cmd_head: str, tool_cache: Dict[str, Any],
                            cmd_mapping: Dict[str, Any]) -> Tuple[bool, Any]:
    """
    本地检查是否是工具命令（不依赖外部函数）
    返回 (是否是工具, 工具信息)
    
    cmd_mapping[\"tools\"] 已由 _get_global_cache 预合并 tool_index，一次查找即可。
    tool_cache 参数保留兼容性但不再使用。
    """
    cmd_lower = cmd_head.lower()
    tools = cmd_mapping.get("tools")
    if tools is not None and cmd_lower in tools:
        return True, tools[cmd_lower]
    return False, None


def _is_system_command_local(cmd_head: str, cmd_mapping: Dict[str, Any], sys_type: str) -> bool:
    """本地检查是否是系统命令（O(1) frozenset 查找，替代 O(n) 列表扫描）"""
    system_set = cmd_mapping.get("_system_set")
    if system_set is not None:
        return cmd_head.lower() in system_set
    # 回退：没有预计算 set 时用列表扫描
    system_cmds = cmd_mapping.get("system", [])
    if isinstance(system_cmds, list):
        return cmd_head.lower() in [c.lower() for c in system_cmds]
    return False


def _determine_cmd_type_local(cmd_head: str, builtin_commands: Dict,
                               tool_cache: Dict[str, Any],
                               cmd_mapping: Dict[str, Any],
                               sys_type: str,
                               other_terminal_cmds: Dict = None,
                               is_sudo: bool = False,
                               is_multiline_line: bool = False) -> str:
    """
    本地判断命令类型（不依赖外部函数）
    返回: 'builtin' | 'other_terminal' | 'tool' | 'system' | 'unknown'
    优先级：内置 > 其余终端内置 > 工具 > 系统 > 未知
    
    特殊规则：
    - sudo 后的命令不会是 builtin（sudo 不能执行内置命令）
    - 多行命令第2行及之后不会是 builtin
    """
    # sudo 后的命令不可能是内置命令
    if is_sudo:
        if _is_other_terminal_command(cmd_head, sys_type, other_terminal_cmds):
            return 'other_terminal'
        
        is_tool, _ = _is_tool_command_local(cmd_head, tool_cache, cmd_mapping)
        if is_tool:
            return 'tool'
        
        if _is_system_command_local(cmd_head, cmd_mapping, sys_type):
            return 'system'
        
        return 'unknown'
    
    # 多行命令第2行及之后不可能是内置命令
    if is_multiline_line:
        if _is_other_terminal_command(cmd_head, sys_type, other_terminal_cmds):
            return 'other_terminal'
        
        is_tool, _ = _is_tool_command_local(cmd_head, tool_cache, cmd_mapping)
        if is_tool:
            return 'tool'
        
        if _is_system_command_local(cmd_head, cmd_mapping, sys_type):
            return 'system'
        
        return 'unknown'
    
    # 正常优先级判断
    if _is_builtin_command(cmd_head, builtin_commands):
        return 'builtin'
    
    if _is_other_terminal_command(cmd_head, sys_type, other_terminal_cmds):
        return 'other_terminal'
    
    is_tool, _ = _is_tool_command_local(cmd_head, tool_cache, cmd_mapping)
    if is_tool:
        return 'tool'
    
    if _is_system_command_local(cmd_head, cmd_mapping, sys_type):
        return 'system'
    
    return 'unknown'


def _build_command_for_execution(cmd_str: str, clean_cmd: str, redirect_config: Dict,
                                  cmd_type: str, tool_info: Any,
                                  replace_virtual_path_in_cmd_func,
                                  request_id: str) -> str:
    """
    构建最终要执行的命令字符串
    
    对于 here-document，会将内容写入临时文件并通过 stdin 重定向传入
    
    修复：工具命令改为提取原始参数字符串，避免 shlex.quote 转义管道等元字符。
    系统/终端内置命令保留原始格式。
    """
    has_here_doc = (redirect_config.get('here_delimiter') and 
                    redirect_config.get('here_doc') is not None)
    
    if cmd_type in ('system', 'other_terminal'):
        # 系统命令：直接使用 clean_cmd，保持原始格式
        # 不要进行额外的路径替换
        replaced_cmd = clean_cmd
        
        non_here_redirect = {}
        if redirect_config:
            for key in ['stdout', 'stderr', 'stdin']:
                if key in redirect_config and redirect_config[key]:
                    non_here_redirect[key] = redirect_config[key]
        
        if non_here_redirect:
            full_cmd = _append_redirect_to_cmd(replaced_cmd, non_here_redirect)
        else:
            full_cmd = replaced_cmd
            
    elif cmd_type == 'tool' and tool_info:
        # 兼容 dict 和对象两种格式
        if isinstance(tool_info, dict):
            tool_path = tool_info.get("path", "")
        else:
            tool_path = tool_info.path if hasattr(tool_info, 'path') else ""
        
        if tool_path:
            # 提取原命令中命令头后面的原始字符串（保留引号、管道等）
            stripped = clean_cmd.lstrip()
            first_space = stripped.find(' ')
            if first_space != -1:
                args_str = stripped[first_space:].lstrip()
            else:
                args_str = ''
            
            if tool_path.endswith(('.py', '.pyc')) and sys.executable:
                tool_cmd = f"{sys.executable} {shlex.quote(tool_path)} {args_str}"
            else:
                tool_cmd = f"{shlex.quote(tool_path)} {args_str}"
            
            non_here_redirect = {}
            if redirect_config:
                for key in ['stdout', 'stderr', 'stdin']:
                    if key in redirect_config and redirect_config[key]:
                        non_here_redirect[key] = redirect_config[key]
            
            if non_here_redirect:
                full_cmd = _append_redirect_to_cmd(tool_cmd, non_here_redirect)
            else:
                full_cmd = tool_cmd
        else:
            full_cmd = cmd_str
    else:
        full_cmd = cmd_str
    
    return full_cmd


def _write_heredoc_to_temp(here_doc_content: str) -> str:
    """将 here-document 内容写入临时文件并返回文件路径"""
    temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.heredoc')
    temp_file.write(here_doc_content)
    if not here_doc_content.endswith('\n'):
        temp_file.write('\n')
    temp_file.close()
    return temp_file.name


def _sync_cwd_from_shell(log_info_func=None, request_id: str = None, cmd_type: str = None):
    """
    从持久化 Shell 进程中读取当前工作目录并同步到 Python 进程（带防抖）
    
    优化策略：
    - builtin 命令不改变 cwd，直接跳过
    - 距离上次同步 < _CWD_SYNC_COOLDOWN 且 cwd 未变，跳过 IPC
    """
    global _LAST_CWD_SYNC_TIME, _LAST_SYNCED_CWD
    
    # builtin 命令不改变工作目录，跳过昂贵的 IPC
    if cmd_type and cmd_type in _CWD_SYNC_SKIP_TYPES:
        return True
    
    import time
    now = time.time()
    
    # 防抖：冷却时间内直接跳过（500ms 内外部 CWD 变化概率极低）
    if _LAST_SYNCED_CWD is not None and (now - _LAST_CWD_SYNC_TIME) < _CWD_SYNC_COOLDOWN:
        return True
    
    try:
        if is_shell_alive():
            shell_cwd = get_shell_cwd()
            if shell_cwd and os.path.isdir(shell_cwd):
                current_cwd = os.getcwd()
                if current_cwd != shell_cwd:
                    os.chdir(shell_cwd)
                    if log_info_func:
                        log_info_func(f"已同步工作目录到: {shell_cwd}", request_id)
                _LAST_SYNCED_CWD = shell_cwd
                _LAST_CWD_SYNC_TIME = now
                return True
    except Exception as e:
        if log_info_func:
            log_info_func(f"同步工作目录失败: {e}", request_id)
    return False


def _execute_resolved_command(full_cmd: str, redirect_config: Dict,
                               run_cmd_sync_func,
                               log_info_func, log_error_func,
                               request_id: str, cmd_type: str = 'tool',
                               cmd_head: str = "") -> Tuple[bool, str]:
    """
    轻量级执行已解析完成的命令（跳过 _execute_command_unified 的开销）。
    
    用于 __TOOL_CMD__: 路径 — 命令已完全解析为 python /path/to/tool args，
    无需再走 smart_shlex_split、虚拟路径转换、工具路径提取等冗余逻辑。
    同时修复原 _execute_command_unified 在 tool_info=None 时丢失重定向的 bug。
    """
    # 追加重定向（修复原 tool_info=None 路径丢失 redirect 的 bug）
    if redirect_config:
        non_here_redirect = {}
        for key in ('stdout', 'stderr', 'stdin'):
            if key in redirect_config and redirect_config[key]:
                non_here_redirect[key] = redirect_config[key]
        if non_here_redirect:
            full_cmd = _append_redirect_to_cmd(full_cmd, non_here_redirect)
    
    if log_info_func:
        cmd_type_name = _CMD_TYPE_NAME_MAP.get(cmd_type, cmd_type)
        log_info_func(f"执行{cmd_type_name}命令：{full_cmd}", request_id)
    
    try:
        if run_cmd_sync_func:
            _use_passthrough = (cmd_type == 'system')
            run_cmd_sync_func(full_cmd, request_id, True, 3,
                              passthrough=_use_passthrough)
        
        _sync_cwd_from_shell(log_info_func, request_id, cmd_type)
        return True, f"命令执行完成：{cmd_head}"
    except Exception as e:
        error_msg = f"命令执行失败：{str(e)}"
        if log_error_func:
            log_error_func(error_msg, request_id)
        return False, error_msg


def _execute_command_unified(cmd_str: str, clean_cmd: str, redirect_config: Dict,
                              cmd_type: str, tool_info: Any,
                              request_id: str, show_output: bool,
                              run_cmd_sync_func,
                              replace_virtual_path_in_cmd_func,
                              sys_type: str,
                              log_info_func, log_error_func,
                              msg: Dict,
                              Fore, Style,
                              debug_parsecmd: bool = False,
                              cmd_head: str = None,
                              **kwargs) -> Tuple[bool, str]:
    """
    统一命令执行函数
    所有命令（系统、工具、内置、其余终端内置）都通过此函数执行
    
    对于 here-document，采用以下处理方式：
    1. 将 here-doc 内容写入临时文件
    2. 在命令末尾添加 < temp_file 重定向
    3. 执行完成后清理临时文件
    """
    if cmd_head is None:
        cmd_parts = smart_shlex_split(clean_cmd, sys_type)
        cmd_head = cmd_parts[0].lower() if cmd_parts else ""
    else:
        cmd_head = cmd_head.lower()
    
    temp_heredoc_file = None
    has_here_doc = (redirect_config.get('here_delimiter') and 
                    redirect_config.get('here_doc') is not None)
    
    if has_here_doc:
        temp_heredoc_file = _write_heredoc_to_temp(redirect_config['here_doc'])
        if log_info_func:
            log_info_func(f"Here-document 内容已写入临时文件: {temp_heredoc_file}", request_id)
    
    # 构建最终命令
    if cmd_type in ('system', 'other_terminal'):
        # 先进行虚拟路径转换（必须做，否则虚拟路径不会转换为物理路径）
        if replace_virtual_path_in_cmd_func:
            # 这个函数应该保持引号结构，只转换路径部分
            replaced_cmd = replace_virtual_path_in_cmd_func(clean_cmd, request_id)
        else:
            replaced_cmd = clean_cmd
        
        full_cmd = replaced_cmd
        
        # 添加非 here-doc 的重定向
        non_here_redirect = {}
        if redirect_config:
            for key in ['stdout', 'stderr', 'stdin']:
                if key in redirect_config and redirect_config[key]:
                    non_here_redirect[key] = redirect_config[key]
        
        if non_here_redirect:
            full_cmd = _append_redirect_to_cmd(full_cmd, non_here_redirect)
            
    elif cmd_type == 'tool' and tool_info:
        # 兼容 dict 和对象两种格式
        if isinstance(tool_info, dict):
            tool_path = tool_info.get("path", "")
            tool_perm = tool_info.get("tool_perm", 3)
        else:
            tool_path = tool_info.path if hasattr(tool_info, 'path') else ""
            tool_perm = tool_info.tool_perm if hasattr(tool_info, 'tool_perm') else 3
        
        if tool_path:
            # 提取原命令中命令头后面的原始字符串（保留引号、管道等）
            stripped = clean_cmd.lstrip()
            first_space = stripped.find(' ')
            if first_space != -1:
                args_str = stripped[first_space:].lstrip()
            else:
                args_str = ''
            
            # 工具路径需要虚拟路径转换
            if replace_virtual_path_in_cmd_func:
                tool_path = replace_virtual_path_in_cmd_func(tool_path, request_id)
            
            # ========== 对工具命令的参数进行虚拟路径解析 ==========
            if replace_virtual_path_in_cmd_func and args_str:
                args_str = resolve_paths_in_multiline_text(args_str, replace_virtual_path_in_cmd_func)
                if debug_parsecmd:
                    print(Fore.CYAN + f"[Debug] 工具参数虚拟路径解析后: {args_str}" + Style.RESET_ALL)
            
            if tool_path.endswith(('.py', '.pyc')) and sys.executable:
                tool_cmd = f"{sys.executable} {shlex.quote(tool_path)} {args_str}"
            else:
                tool_cmd = f"{shlex.quote(tool_path)} {args_str}"
            
            non_here_redirect = {}
            if redirect_config:
                for key in ['stdout', 'stderr', 'stdin']:
                    if key in redirect_config and redirect_config[key]:
                        non_here_redirect[key] = redirect_config[key]
            
            if non_here_redirect:
                full_cmd = _append_redirect_to_cmd(tool_cmd, non_here_redirect)
            else:
                full_cmd = tool_cmd
        else:
            full_cmd = cmd_str
    else:
        full_cmd = cmd_str
    
    if temp_heredoc_file:
        full_cmd = f"{full_cmd} < {shlex.quote(temp_heredoc_file)}"
    
    # === 调试/日志：使用模块级常量，避免每次构造 dict ===
    cmd_type_name = _CMD_TYPE_NAME_MAP.get(cmd_type, cmd_type)
    if debug_parsecmd:
        print(Fore.CYAN + f"[Debug] 最终执行{cmd_type_name}命令: {full_cmd}" + Style.RESET_ALL)
    
    if log_info_func:
        log_info_func(f"执行{cmd_type_name}命令：{full_cmd}", request_id)
    
    try:
        if run_cmd_sync_func:
            # 兼容处理 tool_perm
            if cmd_type == 'tool' and tool_info:
                if isinstance(tool_info, dict):
                    perm = tool_info.get("tool_perm", 3)
                else:
                    perm = tool_info.tool_perm if hasattr(tool_info, 'tool_perm') else 3
            else:
                perm = 3
            # passthrough: system 命令原样透传，不包 {} 不设 TTY
            _use_passthrough = (cmd_type == 'system')
            run_cmd_sync_func(full_cmd, request_id, show_output, perm,
                              passthrough=_use_passthrough)
        
        # 命令执行成功后，同步工作目录（传入 cmd_type 用于防抖）
        _sync_cwd_from_shell(log_info_func, request_id, cmd_type)
        
        return True, f"命令执行完成：{cmd_head}"
    
    except Exception as e:
        error_msg = f"命令执行失败：{str(e)}"
        if log_error_func:
            log_error_func(error_msg, request_id)
        return False, error_msg
    
    finally:
        if temp_heredoc_file:
            try:
                os.unlink(temp_heredoc_file)
                if log_info_func:
                    log_info_func(f"已清理临时文件: {temp_heredoc_file}", request_id)
            except Exception as e:
                if log_error_func:
                    log_error_func(f"清理临时文件失败 {temp_heredoc_file}: {str(e)}", request_id)


def _append_redirect_to_cmd(cmd: str, redirect_config: Dict) -> str:
    """
    将重定向配置追加到命令字符串末尾
    注意：不处理 here-document，here-doc 在处理流程中单独处理
    重要：不要对重定向路径使用 shlex.quote，保持原始引号
    """
    if not redirect_config:
        return cmd
    
    parts = [cmd]
    
    if redirect_config.get('stdout'):
        file_path, mode = redirect_config['stdout']
        # 保持原始格式，不添加额外引号
        parts.append('>>' if mode == 'a' else '>')
        parts.append(file_path)  # 直接使用原始路径，保持引号
    
    if redirect_config.get('stderr'):
        if redirect_config['stderr'] == 'STDOUT':
            parts.append('2>&1')
        else:
            file_path, mode = redirect_config['stderr']
            parts.append('2>>' if mode == 'a' else '2>')
            parts.append(file_path)  # 直接使用原始路径，保持引号
    
    if redirect_config.get('stdin'):
        parts.append('<')
        parts.append(redirect_config['stdin'])  # 直接使用原始路径，保持引号
    
    return ' '.join(parts)


def _resolve_tool_command_in_line(line: str,
                                   tool_cache: Dict[str, Any],
                                   cmd_mapping: Dict[str, Any],
                                   sys_type: str,
                                   other_terminal_cmds: Dict = None,
                                   current_sys_cmds: Dict = None,
                                   builtin_commands: Dict = None,
                                   resolve_path_func=None,
                                   virtual_root_dir: str = None,
                                   is_multiline_line: bool = False) -> str:
    """
    解析一行中的工具命令
    
    对于一行中可能包含多个分号分隔的命令，逐段解析工具路径
    """
    if not line or not line.strip():
        return line
    
    # 按分号拆分为多个子命令
    sub_parts = split_by_semicolon(line, sys_type)
    
    if len(sub_parts) <= 1:
        # 单个命令，直接解析
        return _resolve_single_command_tool(line, tool_cache, cmd_mapping,
                                             sys_type, other_terminal_cmds,
                                             current_sys_cmds, builtin_commands,
                                             resolve_path_func, virtual_root_dir,
                                             is_multiline_line)
    
    # 多个分号分隔的命令，逐个解析
    resolved_parts = []
    for i, part in enumerate(sub_parts):
        if not part.strip():
            resolved_parts.append(part)
            continue
        # 第2个及之后的分号段都是多行上下文
        is_sub_multiline = is_multiline_line or (i > 0)
        resolved = _resolve_single_command_tool(part, tool_cache, cmd_mapping,
                                                  sys_type, other_terminal_cmds,
                                                  current_sys_cmds, builtin_commands,
                                                  resolve_path_func, virtual_root_dir,
                                                  is_sub_multiline)
        resolved_parts.append(resolved)
    
    return '; '.join(resolved_parts)


def _resolve_single_command_tool(cmd_str: str,
                                   tool_cache: Dict[str, Any],
                                   cmd_mapping: Dict[str, Any],
                                   sys_type: str,
                                   other_terminal_cmds: Dict = None,
                                   current_sys_cmds: Dict = None,
                                   builtin_commands: Dict = None,
                                   resolve_path_func=None,
                                   virtual_root_dir: str = None,
                                   is_multiline_line: bool = False) -> str:
    """
    解析单个命令中的工具路径
    
    支持：
    - 普通工具命令：onyx-edit a → python /path/to/onyx-edit/Main.py a
    - sudo 工具命令：sudo onyx-scan → sudo python /path/to/onyx-scan/Main.py
    - 可执行文件路径：/path/to/binary args → 解析后的路径
    - 非二进制文件：/path/to/script.py → python cmd.py source /path/to/script.py
    
    重要：工具命令解析后使用 __TOOL_CMD__: 前缀标记，避免被二次解析
    重要：保留参数的原始引号，不要使用 shlex.split 破坏结构
    
    修复：对工具命令的参数进行虚拟路径解析
    """
    if not cmd_str or not cmd_str.strip():
        return cmd_str
    
    # 检查是否包含管道符（不在引号内），如果包含则不解析工具命令
    if has_pipeline(cmd_str, sys_type):
        # 包含管道符的命令，不要解析工具，保持原样
        return cmd_str
    
    # 使用 smart_shlex_split 但只用于提取命令头
    parts = smart_shlex_split(cmd_str, sys_type)
    if not parts:
        return cmd_str

    # shell 关键字（then/else/elif/do/in）→ 跳过，取后续 token 作为命令头
    _SHELL_KW = frozenset({'then', 'else', 'elif', 'do', 'in'})
    _head_idx = 0
    while _head_idx < len(parts) and parts[_head_idx].lower() in _SHELL_KW:
        _head_idx += 1
    if _head_idx >= len(parts):
        return cmd_str
    
    cmd_head = parts[_head_idx]
    is_sudo = _is_sudo_command(cmd_head)
    
    # 确定实际命令头（sudo 之后可能还有关键字，如 sudo then onyx-nmap）
    if is_sudo and _head_idx + 1 < len(parts):
        _actual_idx = _head_idx + 1
        while _actual_idx < len(parts) and parts[_actual_idx].lower() in _SHELL_KW:
            _actual_idx += 1
        if _actual_idx < len(parts):
            actual_cmd_head = parts[_actual_idx]
            actual_args_start = _actual_idx + 1
        else:
            actual_cmd_head = cmd_head
            actual_args_start = _head_idx + 1
    else:
        actual_cmd_head = cmd_head
        actual_args_start = _head_idx + 1
    
    # 合计跳过的 token 数（关键字 + sudo）
    _total_skip = actual_args_start - 1  # 实际命令头之前的 token 数
    
    # 检查是否是工具命令
    is_tool, tool_info = _is_tool_command_local(actual_cmd_head, tool_cache, cmd_mapping)
    
    if is_tool and tool_info:
        # 兼容 dict 和对象格式
        if isinstance(tool_info, dict):
            tool_path = tool_info.get("path", "")
        else:
            tool_path = tool_info.path if hasattr(tool_info, 'path') else ""
        
        if tool_path:
            # 提取参数：跳过前导 token（关键字 + sudo + 工具名），保留原始引号
            stripped = cmd_str.lstrip()
            _skip_n = _total_skip + 1  # +1 跳过工具名本身
            for _ in range(_skip_n):
                sp = stripped.find(' ')
                if sp == -1:
                    stripped = ''
                    break
                stripped = stripped[sp:].lstrip()
            args_str = stripped
            
            # 对工具命令的参数进行虚拟路径解析
            if resolve_path_func and args_str:
                args_str = resolve_paths_in_multiline_text(args_str, resolve_path_func)
            
            # 构建工具命令
            if tool_path.endswith(('.py', '.pyc')) and sys.executable:
                tool_cmd = f"{sys.executable} {shlex.quote(tool_path)} {args_str}"
            else:
                tool_cmd = f"{shlex.quote(tool_path)} {args_str}"
            
            if is_sudo:
                result = f"sudo {tool_cmd}".strip()
            else:
                result = tool_cmd.strip()
            
            return f"__TOOL_CMD__:{result}"
    
    # 不是工具命令，继续其他类型判断
    # 检查是否是文件路径（未知命令但可能是可执行文件）
    if resolve_path_func and ('/' in actual_cmd_head or actual_cmd_head.startswith('.')):
        stripped = cmd_str.lstrip()
        _skip_n = _total_skip + 1  # +1 跳过命令头本身
        for _ in range(_skip_n):
            sp = stripped.find(' ')
            if sp == -1:
                stripped = ''
                break
            stripped = stripped[sp:].lstrip()
        args_str = stripped
        
        resolved = handle_executable_path(actual_cmd_head, resolve_path_func, virtual_root_dir)
        if resolved != actual_cmd_head:
            if is_sudo:
                result = f"sudo {resolved} {args_str}".strip()
            else:
                result = f"{resolved} {args_str}".strip()
            return f"__TOOL_CMD__:{result}"
    
    return cmd_str


def _resolve_multiline_commands_tools(multiline_cmd: str,
                                        tool_cache: Dict[str, Any],
                                        cmd_mapping: Dict[str, Any],
                                        sys_type: str,
                                        other_terminal_cmds: Dict = None,
                                        current_sys_cmds: Dict = None,
                                        builtin_commands: Dict = None,
                                        resolve_path_func=None,
                                        virtual_root_dir: str = None) -> str:
    """
    解析多行命令中每一行的工具命令
    """
    if not multiline_cmd or not multiline_cmd.strip():
        return multiline_cmd
    
    lines = multiline_cmd.split('\n')
    resolved_lines = []
    
    for i, line in enumerate(lines):
        if not line.strip():
            resolved_lines.append(line)
            continue
        
        if line.strip().startswith('<<'):
            resolved_lines.append(line)
            continue
        
        is_multiline_line = (i > 0)
        
        resolved_line = _resolve_tool_command_in_line(
            line, tool_cache, cmd_mapping, sys_type,
            other_terminal_cmds, current_sys_cmds, builtin_commands,
            resolve_path_func, virtual_root_dir, is_multiline_line
        )
        resolved_lines.append(resolved_line)
    
    return '\n'.join(resolved_lines)


def _check_adv_danger_prompt_config(root_dir: str, read_config_file_func=None) -> bool:
    """
    检查 Adv 模式是否需要二次确认
    """
    if not root_dir or not read_config_file_func:
        return False
    
    try:
        config_path = os.path.expanduser('~/.config/onyx/adv_danger_cmd_prompt')
        content = read_config_file_func(config_path, 'false')
        if content:
            return content.strip().lower() == 'true'
    except Exception:
        pass
    
    return False


def _adv_confirm_prompt(cmd_str: str, current_lang: str, msg: Dict,
                         log_info_func=None, request_id: str = None) -> bool:
    """Adv 模式二次确认 — 4 位 hex 验证码"""
    if current_lang == "chinese":
        warning = f"\n⚠️  [Adv 模式] 即将执行命令：\n  {cmd_str}"
    else:
        warning = f"\n⚠️  [Adv Mode] About to execute:\n  {cmd_str}"
    
    if log_info_func:
        log_info_func(f"Adv 模式二次确认：{cmd_str[:100]}...", request_id)
    
    import secrets as _pae_secrets
    captcha = _pae_secrets.token_hex(2).upper()
    print(f"{Fore.RED}{warning}{Style.RESET_ALL}")
    prompt = f"{Fore.YELLOW}验证码: [ {captcha} ]  — 请输入上方验证码以确认执行\n> {Style.RESET_ALL}"
    try:
        response = safe_input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return response.upper() == captcha


# 全局调试标志
_DEBUG_PARSECMD_FLAG = False

# 全局缓存变量（模块级别，避免重复加载）
_GLOBAL_TOOL_CACHE: Optional[Dict[str, Any]] = None
_GLOBAL_CMD_MAPPING: Optional[Dict[str, Any]] = None
_GLOBAL_CACHE_SYS_TYPE: Optional[str] = None
_GLOBAL_CACHE_TIMESTAMP: float = 0
_GLOBAL_CACHE_TTL: int = 300  # 缓存300秒（5分钟），避免频繁读取文件

# 终端类型缓存（一次会话中不变）
_CACHED_SYS_TYPE: Optional[str] = None

# 轻量 request_id 计数器（替代 uuid.uuid4()）
_REQUEST_COUNTER = 0

# === Lazy CWD Sync 防抖 ===
_LAST_CWD_SYNC_TIME: float = 0.0
_LAST_SYNCED_CWD: Optional[str] = None
_CWD_SYNC_COOLDOWN: float = 0.5  # 500ms 最小同步间隔
# builtin 命令不改变 cwd，跳过同步
_CWD_SYNC_SKIP_TYPES = frozenset({'builtin', 'system'})

# 命令类型中文名映射（模块级常量，避免每次构造 dict）
_CMD_TYPE_NAME_MAP = {
    'system': '系统',
    'other_terminal': '终端内置',
    'tool': '工具',
    'builtin': '内置',
}

# === debug-parsecmd TTL 缓存（避免每条命令读磁盘）===
_DEBUG_PARSECMD_CACHED: Optional[bool] = None
_DEBUG_PARSECMD_CACHE_TIME: float = 0.0
_DEBUG_PARSECMD_CACHE_TTL: float = 5.0  # 5 秒 TTL

# 简单命令快速路径：不含这些特殊字符的命令可以跳过展开和结构检测
_SPECIAL_SHELL_CHARS = frozenset('|;&><$*?[{~#`!')
# 预编译正则：匹配任何特殊 shell 字符（用于快速路径判断）
_SPECIAL_SHELL_CHAR_RE = re.compile(r'[|;&><$*?\[{~#`!]')

# 多语言消息（模块级常量，避免每次调用重建 dict）
_LANG_MESSAGES = {
    "chinese": {
        "quote_error": "❌ 引号不匹配：{}",
        "cmd_not_found": "未找到命令「{cmd}」",
        "tool_mode_deny": "工具权限不足：当前权限{current} < 所需权限{required}",
        "mode_deny": "当前{mode}模式不允许执行命令「{cmd}」",
        "path_permission_denied": "Onyx: {cmd} Permission Denied path:{path}",
        "adv_confirm_exec": "⚠️ 高级模式，确认执行？(y/N): ",
        "fine_grained_no_redirect": "❌ 当前在细颗粒控制路径中，不允许使用重定向或 Here Document",
        "builtin_no_advanced": "内置命令「{cmd}」不支持高级语法（重定向、管道、逻辑操作符等）",
        "path_sandbox_blocked": "❌ 路径沙箱拦截：路径 {path} 不在允许范围内",
        "dangerous_cmd_blocked": "❌ 高危命令被拦截：{cmd} (匹配模式: {pattern})",
        "and_operator_skip": "&& 操作符：前一个命令失败，跳过后续命令",
        "or_operator_skip": "|| 操作符：前一个命令成功，跳过后续命令",
        "cmd_exec_error": "[命令执行错误] {}",
        "here_doc_waiting": "📥 等待Here Document输入（结束符：{delim}）...（输入结束符单独一行结束）",
        "fine_grained_no_pipeline": "❌ 当前在细颗粒控制路径中，不允许使用管道",
        "system_cmd_fine_grained_blocked": "❌ 系统命令「{cmd}」在细颗粒控制路径中不被允许",
        "adv_confirm_title": "\n⚠️  [Adv 模式] 即将执行命令：",
        "adv_confirm_prompt": "  确认执行？(y/N): ",
        "adv_confirm_cancelled": "❌ 用户取消执行",
        "multiline_tool_resolved": "多行工具解析：{line} → {resolved}",
        "variable_cmd_fine_grained_blocked": "❌ 细颗粒度路径安全拦截：命令 '{cmd}' 包含变量（如 $VAR），无法确定变量展开后的路径。当前目录在细颗粒度控制中",
    },
    "english": {
        "quote_error": "❌ Quote mismatch: {}",
        "cmd_not_found": "Command「{cmd}」not found",
        "tool_mode_deny": "Tool permission denied: current {current} < required {required}",
        "mode_deny": "Command「{cmd}」not allowed in {mode} mode",
        "path_permission_denied": "Onyx: {cmd} Permission Denied path:{path}",
        "adv_confirm_exec": "⚠️ Advanced mode, confirm? (y/N): ",
        "fine_grained_no_redirect": "❌ Redirection/Here Doc not allowed in fine-grained path",
        "builtin_no_advanced": "Builtin「{cmd}」doesn't support advanced syntax",
        "path_sandbox_blocked": "❌ Path sandbox blocked: {path}",
        "dangerous_cmd_blocked": "❌ Dangerous cmd blocked: {cmd} (pattern: {pattern})",
        "and_operator_skip": "&&: previous failed, skipping",
        "or_operator_skip": "||: previous succeeded, skipping",
        "cmd_exec_error": "[Exec error] {}",
        "here_doc_waiting": "📥 Waiting for Here Doc (delimiter: {delim})...",
        "fine_grained_no_pipeline": "❌ Pipeline not allowed in fine-grained path",
        "system_cmd_fine_grained_blocked": "❌ System command '{cmd}' not allowed in fine-grained path",
        "adv_confirm_title": "\n⚠️  [Adv Mode] About to execute:",
        "adv_confirm_prompt": "  Confirm? (y/N): ",
        "adv_confirm_cancelled": "❌ Execution cancelled by user",
        "multiline_tool_resolved": "Multiline tool resolved: {line} → {resolved}",
        "variable_cmd_fine_grained_blocked": "❌ Fine-grained path security block: command '{cmd}' contains variable (like $VAR), cannot determine expanded path. Current directory is under fine-grained control",
    }
}

# context dict 的键列表（除去 cmd / is_recursive / is_ai_triggered 后的所有参数名）
# 可变键：会话期间值可能变化的键，每次调用需更新
_CTX_MUTABLE_KEYS = frozenset({"current_sys_cmds", "OTHER_TERMINAL_CMDS", "CURRENT_PROCESSES"})
# 预分配模板 + 缓存：首次调用构建，后续复用（避免每条命令分配 48 键 dict）
_CTX_TEMPLATE: Optional[Dict[str, Any]] = None

_CTX_KEYS = (
    "BUILTIN_COMMANDS", "ALIAS_CACHE", "CMD_MAPPING_CACHE", "TOOL_INDEX_CACHE", "current_sys_cmds",
    "sys_type", "user_mode", "global_config", "executor", "PROCESS_LOCK",
    "CURRENT_PROCESSES", "AI_TOOL_OUTPUT_CACHE", "USER_HOME_DIR", "ROOT_DIR",
    "TOOL_MAIN_DIR", "PYTHON_EXE", "executable_config", "SANDBOX_CONFIG",
    "DEBUG_PARSECMD_PATH", "DEBUG_TIMES_PATH", "PATH_INDEX_MSG_PATH",
    "DIR_CACHE_MSG_PATH", "CMD_MAPPING_MSG_PATH", "TOOL_INDEX_MSG_PATH",
    "OTHER_TERMINAL_CMDS", "get_current_lang_func", "resolve_path_func",
    "check_sandbox_path_func", "validate_param_path_func", "get_cached_cmd_func",
    "check_tool_permission_func", "find_similar_tools_func", "find_similar_cmds_func",
    "run_cmd_sync_func", "run_cmd_with_redirect_func", "execute_tool_func",
    "replace_virtual_path_in_cmd_func", "get_virtual_path_func", "check_blocked_cmd_func",
    "is_interactive_command_func", "read_config_file_func", "clear_ai_cmd_cache_func",
    "build_tool_index_func", "load_cmd_mapping_cache_func",
    "log_info_func", "log_error_func", "log_warning_func", "security_log_func",
    "Fore", "Style", "ToolInfo", "username",
)


def _get_global_cache(sys_type: str, force_refresh: bool = False,
                      CMD_MAPPING_CACHE: Dict = None,
                      TOOL_INDEX_CACHE: Dict = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    获取全局缓存（带 TTL，避免频繁读取 msgpack 文件）
    
    优先使用 Onyx.py 传入的 CMD_MAPPING_CACHE / TOOL_INDEX_CACHE（零磁盘 I/O），
    仅在内存缓存不可用时 fallback 到 msgpack 文件读取。
    """
    global _GLOBAL_TOOL_CACHE, _GLOBAL_CMD_MAPPING, _GLOBAL_CACHE_SYS_TYPE, _GLOBAL_CACHE_TIMESTAMP
    
    import time
    now = time.time()
    
    if not force_refresh and _GLOBAL_TOOL_CACHE is not None and _GLOBAL_CMD_MAPPING is not None:
        if _GLOBAL_CACHE_SYS_TYPE == sys_type and (now - _GLOBAL_CACHE_TIMESTAMP) < _GLOBAL_CACHE_TTL:
            return _GLOBAL_TOOL_CACHE, _GLOBAL_CMD_MAPPING
    
    # === 快速路径：使用内存缓存（零磁盘 I/O）===
    if CMD_MAPPING_CACHE and sys_type in CMD_MAPPING_CACHE:
        sys_cache = CMD_MAPPING_CACHE[sys_type]
        mapping = sys_cache.get("mapping", {})
        
        cmd_mapping: Dict[str, Any] = {
            "tools": dict(mapping.get("tools", {})),
            "system": list(mapping.get("system", [])),
        }
        
        # 合并 TOOL_INDEX_CACHE 中的工具（key 格式: toolname_sysType → 提取 toolname）
        if TOOL_INDEX_CACHE:
            _suffix = f"_{sys_type}"
            for cache_key, tool_info in TOOL_INDEX_CACHE.items():
                # 跳过系统工具（sys_ 前缀），它们已在 system 列表中
                if cache_key.startswith("sys_"):
                    continue
                if _suffix in cache_key:
                    tool_name = cache_key.split(_suffix)[0].lower()
                else:
                    tool_name = cache_key.lower()
                
                if tool_name not in cmd_mapping["tools"]:
                    if hasattr(tool_info, 'path'):
                        cmd_mapping["tools"][tool_name] = {
                            "path": tool_info.path,
                            "tool_perm": getattr(tool_info, 'tool_perm', 3),
                            "tool_type": getattr(tool_info, 'tool_type', "other"),
                        }
        
        # 预计算 O(1) 系统命令查找集合
        cmd_mapping["_system_set"] = frozenset(c.lower() for c in cmd_mapping["system"])
        
        tool_cache = cmd_mapping["tools"]
        
        _GLOBAL_TOOL_CACHE = tool_cache
        _GLOBAL_CMD_MAPPING = cmd_mapping
        _GLOBAL_CACHE_SYS_TYPE = sys_type
        _GLOBAL_CACHE_TIMESTAMP = now
        return tool_cache, cmd_mapping
    
    # === 回退路径：msgpack 文件（兼容没有内存缓存的调用方）===
    _GLOBAL_TOOL_CACHE = _load_tool_index_cache()
    _GLOBAL_CMD_MAPPING = _load_cmd_mapping_cache(sys_type)
    _GLOBAL_CACHE_SYS_TYPE = sys_type
    _GLOBAL_CACHE_TIMESTAMP = now
    
    # 合并工具索引到命令映射，确保工具命令一次命中（消除回退路径）
    if _GLOBAL_TOOL_CACHE and _GLOBAL_CMD_MAPPING is not None:
        tools = _GLOBAL_CMD_MAPPING.setdefault("tools", {})
        if not tools:  # 仅当 tools 为空时才合并（msgpack 已有则不覆盖）
            tools.update(_GLOBAL_TOOL_CACHE)
    
    return _GLOBAL_TOOL_CACHE, _GLOBAL_CMD_MAPPING


def parse_and_execute(cmd: str, is_recursive: bool = False, is_ai_triggered: bool = False,
                     # 核心模块依赖
                     BUILTIN_COMMANDS=None,
                     ALIAS_CACHE=None,
                     CMD_MAPPING_CACHE=None,  # 内存缓存（优先于 msgpack，零 I/O）
                     TOOL_INDEX_CACHE=None,   # 工具索引内存缓存
                     current_sys_cmds=None,   # 保留参数但不再使用
                     sys_type=None,
                     user_mode=None,
                     global_config=None,
                     executor=None,
                     PROCESS_LOCK=None,
                     CURRENT_PROCESSES=None,
                     AI_TOOL_OUTPUT_CACHE=None,
                     USER_HOME_DIR=None,
                     ROOT_DIR=None,
                     TOOL_MAIN_DIR=None,
                     PYTHON_EXE=None,
                     executable_config=None,
                     SANDBOX_CONFIG=None,
                     DEBUG_PARSECMD_PATH=None,
                     DEBUG_TIMES_PATH=None,
                     PATH_INDEX_MSG_PATH=None,
                     DIR_CACHE_MSG_PATH=None,
                     CMD_MAPPING_MSG_PATH=None,
                     TOOL_INDEX_MSG_PATH=None,
                     
                     # === 其余终端内置命令配置 ===
                     OTHER_TERMINAL_CMDS=None,
                     
                     # 函数依赖
                     get_current_lang_func=None,
                     resolve_path_func=None,
                     check_sandbox_path_func=None,
                     validate_param_path_func=None,
                     get_cached_cmd_func=None,  # 保留参数但不再使用
                     check_tool_permission_func=None,
                     find_similar_tools_func=None,  # 保留参数但不再使用
                     find_similar_cmds_func=None,   # 保留参数但不再使用
                     run_cmd_sync_func=None,
                     run_cmd_with_redirect_func=None,
                     execute_tool_func=None,
                     replace_virtual_path_in_cmd_func=None,
                     get_virtual_path_func=None,
                     check_blocked_cmd_func=None,
                     is_interactive_command_func=None,
                     read_config_file_func=None,
                     clear_ai_cmd_cache_func=None,
                     build_tool_index_func=None,
                     load_cmd_mapping_cache_func=None,
                     log_info_func=None,
                     log_error_func=None,
                     log_warning_func=None,
                     security_log_func=None,
                     
                     # 颜色和样式
                     Fore=Fore,
                     Style=Style,
                     
                     # ToolInfo类（保留参数但不再使用）
                     ToolInfo=None,
                     username: str = None):
    """
    命令解析与执行入口
    
    核心流程：
    1. 命令展开（变量、通配符、别名等）
    2. 按分隔符拆分（; → &&/|| → 管道检测）
    3. 判断命令类型（内置/其余终端内置/工具/系统/未知）
    4. 安全检查（路径权限、高危命令、模式权限）
    5. 执行命令（统一使用 run_cmd_sync_func，底层已完全抽象）
    6. 执行后自动同步 Shell 进程的工作目录
    """
    
    global _DEBUG_PARSECMD_FLAG
    
    if not cmd or not cmd.strip():
        return
    
    # 默认日志函数
    if log_info_func is None:
        def log_info_func(msg, req_id): pass
    if log_error_func is None:
        def log_error_func(msg, req_id): pass
    
    global _CACHED_SYS_TYPE
    request_id = str(uuid.uuid4())
    
    # === 自动判断终端类型（模块级缓存，一次会话不变）===
    if sys_type is None:
        if _CACHED_SYS_TYPE is None:
            try:
                _CACHED_SYS_TYPE = get_terminal_type()
            except Exception:
                _CACHED_SYS_TYPE = 'sh'
        sys_type = _CACHED_SYS_TYPE
    
    # ========== 加载工具/命令缓存（优先内存，fallback msgpack）==========
    tool_cache, cmd_mapping = _get_global_cache(
        sys_type,
        CMD_MAPPING_CACHE=CMD_MAPPING_CACHE,
        TOOL_INDEX_CACHE=TOOL_INDEX_CACHE
    )
    
    # 统计缓存中的命令数量（用于调试）
    tool_count = len(tool_cache)
    system_cmd_count = len(cmd_mapping.get("system", []))
    if _DEBUG_PARSECMD_FLAG:
        source = "内存" if (CMD_MAPPING_CACHE and sys_type in CMD_MAPPING_CACHE) else "msgpack"
        print(Fore.CYAN + f"[Debug] 从{source}加载工具缓存: {tool_count} 个工具" + Style.RESET_ALL)
        print(Fore.CYAN + f"[Debug] 从{source}加载系统命令缓存: {system_cmd_count} 个命令" + Style.RESET_ALL)
    
    # 语言
    current_lang = "chinese"
    if get_current_lang_func:
        current_lang = get_current_lang_func()
    
    set_lang(current_lang)
    
    # 多语言消息
    msg = _LANG_MESSAGES.get(current_lang, _LANG_MESSAGES["chinese"])

    # 深夜温度：给关键错误消息注入温暖后缀
    _night_hour = datetime.now().hour
    if 0 <= _night_hour < 6:
        _warm_cn = "\n🌙 凌晨了，这个明天处理也不迟～早点休息吧。"
        _warm_en = "\n🌙 It's late — save this for tomorrow. Get some rest."
        _warm = _warm_cn if current_lang == "chinese" else _warm_en
        _warm_keys = ("mode_deny", "cmd_not_found", "path_permission_denied",
                       "path_sandbox_blocked", "dangerous_cmd_blocked")
        msg = {k: (v + _warm if k in _warm_keys else v) for k, v in msg.items()}
    
    # === 构建 ctx dict（复用模板，仅首次构建，后续只更新可变键）===
    global _CTX_TEMPLATE
    if _CTX_TEMPLATE is None:
        _CTX_TEMPLATE = {k: locals().get(k) for k in _CTX_KEYS}
        _ctx = dict(_CTX_TEMPLATE)
    else:
        _ctx = dict(_CTX_TEMPLATE)
        for k in _CTX_MUTABLE_KEYS:
            val = locals().get(k)
            if val is not None:
                _ctx[k] = val
    
    # === 缓存 CWD（避免多次 syscall）===
    _cwd = os.getcwd()
    
    # 调试（TTL 缓存，避免每条命令读磁盘）
    global _DEBUG_PARSECMD_CACHED, _DEBUG_PARSECMD_CACHE_TIME
    import time as _time_mod
    _now = _time_mod.time()
    if _DEBUG_PARSECMD_CACHED is None or (_now - _DEBUG_PARSECMD_CACHE_TIME) >= _DEBUG_PARSECMD_CACHE_TTL:
        if DEBUG_PARSECMD_PATH and read_config_file_func:
            _DEBUG_PARSECMD_CACHED = read_config_file_func(DEBUG_PARSECMD_PATH, False)
        else:
            _DEBUG_PARSECMD_CACHED = False
        _DEBUG_PARSECMD_CACHE_TIME = _now
    debug_parsecmd = _DEBUG_PARSECMD_CACHED
    _DEBUG_PARSECMD_FLAG = debug_parsecmd
    set_perm_debug_flag(debug_parsecmd)
    
    # 加载路径权限配置
    if ROOT_DIR:
        load_perm_path_config(ROOT_DIR, username, log_error_func)
    
    # 加载其余终端内置命令配置
    if OTHER_TERMINAL_CMDS is None and ROOT_DIR:
        OTHER_TERMINAL_CMDS = load_other_terminal_commands(ROOT_DIR)
        if debug_parsecmd:
            print(Fore.CYAN + f"[Debug] 加载其余终端内置命令：{list(OTHER_TERMINAL_CMDS.keys())}" + Style.RESET_ALL)
    
    # === 检查 Adv 模式是否需要二次确认 ===
    adv_need_confirm = False
    if user_mode and hasattr(user_mode, 'current_mode') and user_mode.current_mode == "adv":
        adv_need_confirm = _check_adv_danger_prompt_config(ROOT_DIR, read_config_file_func)
        if debug_parsecmd:
            print(Fore.CYAN + f"[Debug] Adv 模式二次确认配置: {adv_need_confirm}" + Style.RESET_ALL)
    
    # 清空路径映射缓存（避免跨命令污染）
    clear_path_mapping_cache()
    
    # ========== Step 1: 引号检查 ==========
    quote_balanced, quote_error_msg = check_quotes_balanced(cmd, sys_type)
    if not quote_balanced:
        print(Fore.RED + msg["quote_error"].format(quote_error_msg) + Style.RESET_ALL)
        return
    
    # ========== Step 2: 别名解析 ==========
    if ALIAS_CACHE:
        cmd = resolve_alias_in_cmd(cmd, ALIAS_CACHE, log_info_func, request_id, sys_type)
    
    # ========== Step 3: 命令展开（变量不再展开，保留原样）==========
    expanded_cmd = remove_comments(cmd, sys_type)
    
    if not expanded_cmd or not expanded_cmd.strip():
        return
    
    # === 展开链：仅 ~ 展开保留（安全扫描需要解析路径），其余交给 bash ===
    # expand_variables / expand_braces / expand_wildcards 会破坏 shell 语法
    # （函数定义、$()、TUI 程序参数等），PTY 直通模式下不需要 Python 来做。
    if USER_HOME_DIR and '~' in expanded_cmd:
        expanded_cmd = expand_tilde(expanded_cmd, USER_HOME_DIR, sys_type)
    
    # ========== 快速路径：简单命令跳过结构检测 ==========
    # 如果命令不含任何 shell 特殊字符且无换行，直接跳到工具解析+类型判断
    is_simple_command = (
        '\n' not in expanded_cmd
        and not _SPECIAL_SHELL_CHAR_RE.search(expanded_cmd)
    )
    
    if is_simple_command:
        clean_cmd_simple, redirect_config_simple = parse_redirects_from_command(expanded_cmd, sys_type)
        resolved_simple = _resolve_tool_command_in_line(
            clean_cmd_simple, tool_cache, cmd_mapping, sys_type,
            OTHER_TERMINAL_CMDS, current_sys_cmds, BUILTIN_COMMANDS,
            resolve_path_func, ROOT_DIR
        )
        if resolved_simple != clean_cmd_simple:
            clean_cmd_simple = resolved_simple
        
        if clean_cmd_simple.startswith("__TOOL_CMD__:"):
            actual_cmd_simple = clean_cmd_simple[len("__TOOL_CMD__:"):]
            if not check_dangerous_commands(actual_cmd_simple, user_mode, log_info_func, log_error_func, request_id, USER_HOME_DIR):
                return
            # 快速路径：命令已完全解析，跳过 _execute_command_unified 的开销
            _execute_resolved_command(
                actual_cmd_simple, redirect_config_simple,
                run_cmd_sync_func, log_info_func, log_error_func,
                request_id, 'tool'
            )
            return
        
        # 简单命令的类型判断 + 执行（跳过 Steps 3.5-8 的复杂拆分）
        cmd_parts_simple = smart_shlex_split(clean_cmd_simple, sys_type)
        if not cmd_parts_simple:
            return
        cmd_head_simple = cmd_parts_simple[0].lower()
        is_sudo_simple = _is_sudo_command(cmd_head_simple)
        actual_head_simple = cmd_parts_simple[1].lower() if is_sudo_simple and len(cmd_parts_simple) > 1 else cmd_head_simple
        
        cmd_type_simple = _determine_cmd_type_local(actual_head_simple, BUILTIN_COMMANDS,
                                                     tool_cache, cmd_mapping,
                                                     sys_type, OTHER_TERMINAL_CMDS,
                                                     is_sudo=is_sudo_simple)
        
        if cmd_type_simple == 'builtin':
            if is_sudo_simple:
                print(Fore.RED + f"❌ sudo 不能执行内置命令「{actual_head_simple}」" + Style.RESET_ALL)
                return
            try:
                BUILTIN_COMMANDS[actual_head_simple](cmd_parts_simple, request_id)
            except Exception as e:
                print(Fore.RED + f"内置命令执行失败：{str(e)}" + Style.RESET_ALL)
            _sync_cwd_from_shell(log_info_func, request_id, 'builtin')
            return
        
        elif cmd_type_simple == 'other_terminal':
            if adv_need_confirm:
                if not _adv_confirm_prompt(clean_cmd_simple, current_lang, msg, log_info_func, request_id):
                    print(Fore.YELLOW + msg["adv_confirm_cancelled"] + Style.RESET_ALL)
                    return
            
            all_paths, resolved_paths = _collect_and_resolve_paths(clean_cmd_simple, redirect_config_simple, resolve_path_func, _cwd)
            
            if resolved_paths:
                if not check_path_permission_for_cmd(
                    actual_head_simple, resolved_paths, username, user_mode,
                    log_info_func, log_error_func, request_id, msg,
                    USER_HOME_DIR, is_ai_call=is_ai_triggered
                ):
                    return
            
            advanced_syntax = check_advanced_syntax(clean_cmd_simple, redirect_config_simple, sys_type)
            if resolved_paths:
                if not check_fine_grained_advanced_syntax(
                    resolved_paths, ROOT_DIR, username, user_mode,
                    advanced_syntax, log_info_func, log_error_func, request_id,
                    USER_HOME_DIR, is_ai_call=is_ai_triggered
                ):
                    return
            
            if not check_dangerous_commands(clean_cmd_simple, user_mode, log_info_func, log_error_func, request_id, USER_HOME_DIR):
                return
            
            if user_mode and hasattr(user_mode, 'current_mode') and user_mode.current_mode != "adv":
                if global_config and not is_command_allowed_in_mode(
                    actual_head_simple, user_mode.current_mode, global_config, log_error_func, request_id
                ):
                    print(Fore.RED + msg["mode_deny"].format(mode=user_mode.current_mode, cmd=actual_head_simple) + Style.RESET_ALL)
                    return
            
            if user_mode and hasattr(user_mode, 'current_mode') and user_mode.current_mode != "adv":
                for path in all_paths:
                    if path and path != 'STDOUT':
                        if check_sandbox_path_func and not check_sandbox_path_func(path, request_id):
                            print(Fore.RED + msg["path_sandbox_blocked"].format(path=path) + Style.RESET_ALL)
                            return
            
            if ROOT_DIR:
                if not check_protected_dir_for_cmd(clean_cmd_simple, ROOT_DIR, get_virtual_path_func, log_error_func, request_id):
                    return
            
            _execute_command_unified(
                expanded_cmd, clean_cmd_simple, redirect_config_simple, 'other_terminal', None,
                request_id, True,
                run_cmd_sync_func,
                replace_virtual_path_in_cmd_func,
                sys_type,
                log_info_func, log_error_func,
                msg, Fore, Style,
                debug_parsecmd=debug_parsecmd,
                cmd_head=actual_head_simple
            )
            return
        
        elif cmd_type_simple == 'tool':
            is_t, t_info = _is_tool_command_local(actual_head_simple, tool_cache, cmd_mapping)
            if t_info:
                if adv_need_confirm:
                    if not _adv_confirm_prompt(clean_cmd_simple, current_lang, msg, log_info_func, request_id):
                        print(Fore.YELLOW + msg["adv_confirm_cancelled"] + Style.RESET_ALL)
                        return
                
                if check_tool_permission_func:
                    if isinstance(t_info, dict):
                        perm = t_info.get("tool_perm", 3)
                    else:
                        perm = t_info.tool_perm if hasattr(t_info, 'tool_perm') else 3
                    if not check_tool_permission_func(perm):
                        return
                
                all_paths, resolved_paths = _collect_and_resolve_paths(clean_cmd_simple, redirect_config_simple, resolve_path_func, _cwd)
                
                advanced_syntax = check_advanced_syntax(clean_cmd_simple, redirect_config_simple, sys_type)
                if resolved_paths:
                    if not check_fine_grained_advanced_syntax(
                        resolved_paths, ROOT_DIR, username, user_mode,
                        advanced_syntax, log_info_func, log_error_func, request_id,
                        USER_HOME_DIR, is_ai_call=is_ai_triggered
                    ):
                        return
                
                if resolved_paths:
                    if not check_path_permission_for_cmd(
                        actual_head_simple, resolved_paths, username, user_mode,
                        log_info_func, log_error_func, request_id, msg,
                        USER_HOME_DIR, is_ai_call=is_ai_triggered
                    ):
                        return
                
                if not check_dangerous_commands(clean_cmd_simple, user_mode, log_info_func, log_error_func, request_id, USER_HOME_DIR):
                    return
                
                _execute_command_unified(
                    expanded_cmd, clean_cmd_simple, redirect_config_simple, 'tool', t_info,
                    request_id, True,
                    run_cmd_sync_func,
                    replace_virtual_path_in_cmd_func,
                    sys_type,
                    log_info_func, log_error_func,
                    msg, Fore, Style,
                    debug_parsecmd=debug_parsecmd,
                    cmd_head=actual_head_simple
                )
                return
        
        elif cmd_type_simple == 'system':
            if adv_need_confirm:
                if not _adv_confirm_prompt(clean_cmd_simple, current_lang, msg, log_info_func, request_id):
                    print(Fore.YELLOW + msg["adv_confirm_cancelled"] + Style.RESET_ALL)
                    return
            
            all_paths, resolved_paths = _collect_and_resolve_paths(clean_cmd_simple, redirect_config_simple, resolve_path_func, _cwd)
            
            if resolved_paths:
                if not check_path_permission_for_cmd(
                    actual_head_simple, resolved_paths, username, user_mode,
                    log_info_func, log_error_func, request_id, msg,
                    USER_HOME_DIR, is_ai_call=is_ai_triggered
                ):
                    return
            
            advanced_syntax = check_advanced_syntax(clean_cmd_simple, redirect_config_simple, sys_type)
            if resolved_paths:
                if not check_fine_grained_advanced_syntax(
                    resolved_paths, ROOT_DIR, username, user_mode,
                    advanced_syntax, log_info_func, log_error_func, request_id,
                    USER_HOME_DIR, is_ai_call=is_ai_triggered
                ):
                    return
            
            if not check_dangerous_commands(clean_cmd_simple, user_mode, log_info_func, log_error_func, request_id, USER_HOME_DIR):
                return
            
            if user_mode and hasattr(user_mode, 'current_mode') and user_mode.current_mode != "adv":
                if global_config and not is_command_allowed_in_mode(
                    actual_head_simple, user_mode.current_mode, global_config, log_error_func, request_id
                ):
                    print(Fore.RED + msg["mode_deny"].format(mode=user_mode.current_mode, cmd=actual_head_simple) + Style.RESET_ALL)
                    return
            
            if user_mode and hasattr(user_mode, 'current_mode') and user_mode.current_mode != "adv":
                for path in all_paths:
                    if path and path != 'STDOUT':
                        if check_sandbox_path_func and not check_sandbox_path_func(path, request_id):
                            print(Fore.RED + msg["path_sandbox_blocked"].format(path=path) + Style.RESET_ALL)
                            return
            
            if ROOT_DIR:
                if not check_protected_dir_for_cmd(clean_cmd_simple, ROOT_DIR, get_virtual_path_func, log_error_func, request_id):
                    return
            
            _execute_command_unified(
                expanded_cmd, clean_cmd_simple, redirect_config_simple, 'system', None,
                request_id, True,
                run_cmd_sync_func,
                replace_virtual_path_in_cmd_func,
                sys_type,
                log_info_func, log_error_func,
                msg, Fore, Style,
                debug_parsecmd=debug_parsecmd,
                cmd_head=actual_head_simple
            )
            return
        
        # unknown — 回退到正常流程
        if debug_parsecmd:
            print(Fore.CYAN + f"[Debug] 简单命令未知类型: {actual_head_simple}，回退正常流程" + Style.RESET_ALL)
    
    # ========== Step 3.5: 检查变量命令是否在细颗粒度路径中 ==========
    current_dir = _cwd
    should_block, block_reason = check_variable_command_in_fine_grained_path(
        expanded_cmd, current_dir, ROOT_DIR, username, log_error_func, request_id, sys_type
    )
    if should_block:
        print(Fore.RED + block_reason + Style.RESET_ALL)
        if log_error_func:
            log_error_func(f"变量命令细颗粒度拦截: {expanded_cmd}", request_id)
        return
    
    # ========== Step 4: 处理单行花括号块和子shell ==========
    is_single_block = _is_single_line_block(expanded_cmd, sys_type)
    is_subshell = _is_subshell(expanded_cmd, sys_type)
    
    if is_single_block and not is_shell_logic_structure(expanded_cmd, sys_type):
        if debug_parsecmd:
            print(Fore.CYAN + f"[Debug] 检测到单行花括号块" + Style.RESET_ALL)
        
        inner_cmd = expanded_cmd.strip()[1:-1].strip()
        if inner_cmd:
            parse_and_execute(inner_cmd, is_recursive=True, is_ai_triggered=is_ai_triggered, **_ctx)
        return
    
    if is_subshell and not is_shell_logic_structure(expanded_cmd, sys_type):
        if debug_parsecmd:
            print(Fore.CYAN + f"[Debug] 检测到子shell" + Style.RESET_ALL)
        
        inner_cmd = expanded_cmd.strip()[1:-1].strip()
        if inner_cmd:
            parse_and_execute(inner_cmd, is_recursive=True, is_ai_triggered=is_ai_triggered, **_ctx)
        return
    
    # ========== Step 5: shell逻辑结构检测 ==========
    if is_shell_logic_structure(expanded_cmd, sys_type):
        if debug_parsecmd:
            print(Fore.CYAN + f"[Debug] 检测到 {sys_type} 逻辑结构" + Style.RESET_ALL)
        
        if resolve_path_func:
            expanded_cmd = _resolve_paths_in_bash_structure(expanded_cmd, resolve_path_func)
            if debug_parsecmd:
                print(Fore.CYAN + f"[Debug] shell结构路径解析后: {expanded_cmd[:200]}..." + Style.RESET_ALL)
        
        # 使用本地工具解析
        resolved_multiline = _resolve_multiline_commands_tools(
            expanded_cmd, tool_cache, cmd_mapping, sys_type,
            OTHER_TERMINAL_CMDS, current_sys_cmds, BUILTIN_COMMANDS,
            resolve_path_func, ROOT_DIR
        )
        if resolved_multiline != expanded_cmd:
            if debug_parsecmd:
                print(Fore.CYAN + f"[Debug] 多行工具解析：{expanded_cmd[:100]}... → {resolved_multiline[:100]}..." + Style.RESET_ALL)
            if log_info_func:
                log_info_func(msg["multiline_tool_resolved"].format(
                    line=expanded_cmd[:100], resolved=resolved_multiline[:100]
                ), request_id)
            expanded_cmd = resolved_multiline
        
        if adv_need_confirm:
            if not _adv_confirm_prompt(expanded_cmd, current_lang, msg, log_info_func, request_id):
                print(Fore.YELLOW + msg["adv_confirm_cancelled"] + Style.RESET_ALL)
                return
        
        if not check_dangerous_commands(expanded_cmd, user_mode, log_info_func, log_error_func, request_id, USER_HOME_DIR):
            return
        
        all_paths = _collect_all_paths_from_bash_structure(expanded_cmd, resolve_path_func)
        current_dir = _cwd
        if resolve_path_func:
            current_dir = resolve_path_func(current_dir) or current_dir
        all_paths.append(current_dir)
        
        resolved_paths = list(set(p for p in all_paths if p and p != 'STDOUT'))
        
        if resolved_paths:
            advanced_syntax = {
                'has_pipeline': has_pipeline(expanded_cmd, sys_type),
                'has_redirect': False,
                'has_here_doc': False,
                'has_logical_operators': has_logical_operators(expanded_cmd, sys_type),
                'has_any_advanced': has_pipeline(expanded_cmd, sys_type) or has_logical_operators(expanded_cmd, sys_type)
            }
            
            if not check_fine_grained_advanced_syntax(
                resolved_paths, ROOT_DIR, username, user_mode,
                advanced_syntax, log_info_func, log_error_func, request_id,
                USER_HOME_DIR, is_ai_call=is_ai_triggered
            ):
                return
            
            if not check_path_permission_for_cmd(
                'bash_structure', resolved_paths, username, user_mode,
                log_info_func, log_error_func, request_id, msg,
                USER_HOME_DIR, is_ai_call=is_ai_triggered
            ):
                return
        
        if log_info_func:
            log_info_func(f"执行shell逻辑结构", request_id)
        
        if run_cmd_sync_func:
            run_cmd_sync_func(expanded_cmd, request_id, True, 3)
        
        _sync_cwd_from_shell(log_info_func, request_id, 'system')
        return
    
    # ========== Step 6-7: ; && || 拆分已移除 ==========
    # PTY 直通模式下，bash 自己处理 ; && ||，Onyx 不再拆分。
    # 安全扫描（check_dangerous_commands）会整体检查完整命令行。
    current_cmd = expanded_cmd
    final_cmd = expanded_cmd
    
    # ========== Step 8: 解析重定向 ==========
    clean_cmd, redirect_config = parse_redirects_from_command(final_cmd, sys_type)
    
    # ========== Step 9: 解析工具命令（使用本地缓存）==========
    resolved_cmd = _resolve_tool_command_in_line(
        clean_cmd, tool_cache, cmd_mapping, sys_type,
        OTHER_TERMINAL_CMDS, current_sys_cmds, BUILTIN_COMMANDS,
        resolve_path_func, ROOT_DIR
    )
    if resolved_cmd != clean_cmd:
        if debug_parsecmd:
            print(Fore.CYAN + f"[Debug] 工具解析：{clean_cmd} → {resolved_cmd}" + Style.RESET_ALL)
        clean_cmd = resolved_cmd
    
    # ========== Step 9.5: 处理已标记的工具命令 ==========
    if clean_cmd.startswith("__TOOL_CMD__:"):
        actual_cmd = clean_cmd[len("__TOOL_CMD__:"):]
        if debug_parsecmd:
            print(Fore.CYAN + f"[Debug] 检测到标记的工具命令，跳过类型判断直接执行: {actual_cmd[:100]}..." + Style.RESET_ALL)
        
        if not check_dangerous_commands(actual_cmd, user_mode, log_info_func, log_error_func, request_id, USER_HOME_DIR):
            return
        
        # 快速路径：命令已完全解析，跳过 _execute_command_unified 的开销
        _execute_resolved_command(
            actual_cmd, redirect_config,
            run_cmd_sync_func, log_info_func, log_error_func,
            request_id, 'tool'
        )
        return
    
    # ========== Step 10: 判断命令类型（使用本地函数）==========
    cmd_parts = smart_shlex_split(clean_cmd, sys_type)
    if not cmd_parts:
        return
    
    original_cmd_head = cmd_parts[0]
    cmd_head = cmd_parts[0].lower()
    
    is_sudo = _is_sudo_command(cmd_head)
    
    actual_cmd_head = cmd_head
    if is_sudo and len(cmd_parts) > 1:
        actual_cmd_head = cmd_parts[1].lower()
    
    cmd_type = _determine_cmd_type_local(actual_cmd_head, BUILTIN_COMMANDS,
                                          tool_cache, cmd_mapping,
                                          sys_type, OTHER_TERMINAL_CMDS,
                                          is_sudo=is_sudo)
    
    # === 如果未知命令但是变量赋值，当作系统命令处理 ===
    if cmd_type == 'unknown' and _is_variable_assignment(original_cmd_head) and not is_sudo:
        if debug_parsecmd:
            print(Fore.CYAN + f"[Debug] 检测到变量赋值: {original_cmd_head}，作为系统命令处理" + Style.RESET_ALL)
        cmd_type = 'system'
    
    if debug_parsecmd:
        sudo_prefix = "sudo " if is_sudo else ""
        print(Fore.CYAN + f"[Debug] 命令类型: {cmd_type}, 命令: {sudo_prefix}{actual_cmd_head}, 终端类型: {sys_type}" + Style.RESET_ALL)
    
    # ========== Step 11a: 未知命令 — 送 PTY 让 bash 判断 ==========
    # 不再在 Python 层直接报"未找到命令"并返回。
    # 正常终端行为：Onyx 识别不了的命令，交给底层 shell 处理，
    # 由 bash 自然返回 "command not found" 或正确执行。
    # 这样：shell 函数、alias、新装的程序、缓存遗漏的命令 — 都能正常工作。
    _unknown_origin = False
    if cmd_type == 'unknown':
        if actual_cmd_head.startswith('#'):
            if debug_parsecmd:
                print(Fore.CYAN + f"[Debug] 检测到注释命令，忽略执行" + Style.RESET_ALL)
            return
        
        # 路径解析：./script.sh 或 /absolute/path 等
        if resolve_path_func and ('/' in actual_cmd_head or actual_cmd_head.startswith('.')):
            actual_args_start = 2 if is_sudo else 1
            if actual_args_start <= len(cmd_parts) - 1:
                args = cmd_parts[actual_args_start:]
                args_str = ' '.join(args)
            else:
                stripped = clean_cmd.lstrip()
                if is_sudo:
                    first_space = stripped.find(' ', len('sudo'))
                    if first_space != -1:
                        stripped = stripped[first_space:].lstrip()
                first_space = stripped.find(' ')
                if first_space != -1:
                    args_str = stripped[first_space:].lstrip()
                else:
                    args_str = ''
            
            resolved = handle_executable_path(actual_cmd_head, resolve_path_func, ROOT_DIR)
            if resolved != actual_cmd_head:
                if is_sudo:
                    clean_cmd = f"sudo {resolved} {args_str}".strip()
                else:
                    clean_cmd = f"{resolved} {args_str}".strip()
                
                if debug_parsecmd:
                    print(Fore.CYAN + f"[Debug] 文件路径解析：{actual_cmd_head} → {clean_cmd}" + Style.RESET_ALL)
                
                parse_and_execute(clean_cmd, is_recursive=True, is_ai_triggered=is_ai_triggered, **_ctx)
                return
        
        # 非路径类未知命令 → 当作系统命令送 PTY，让 bash 自己判断
        if debug_parsecmd:
            print(Fore.CYAN + f"[Debug] 未知命令 '{actual_cmd_head}' → 送 PTY 交给 shell 处理" + Style.RESET_ALL)
        cmd_type = 'system'
        _unknown_origin = True   # 标记：此命令原为 unknown，执行后需检查是否需要提示
    
    # ========== Step 11b: 内置命令 ==========
    if cmd_type == 'builtin':
        if is_sudo:
            if current_lang == "chinese":
                print(Fore.RED + f"❌ sudo 不能执行内置命令「{actual_cmd_head}」" + Style.RESET_ALL)
            else:
                print(Fore.RED + f"❌ sudo cannot execute builtin command '{actual_cmd_head}'" + Style.RESET_ALL)
            return
        
        if actual_cmd_head == 'sado':
            if debug_parsecmd:
                print(Fore.CYAN + f"[Debug] 检测到 sado 命令，直接调用内置函数: {final_cmd}" + Style.RESET_ALL)
            
            if log_info_func:
                log_info_func(f"执行 sado 命令：{final_cmd}", request_id)
            
            try:
                BUILTIN_COMMANDS['sado'](cmd_parts, request_id)
            except Exception as e:
                err_msg = f"sado 命令执行失败：{str(e)}"
                print(Fore.RED + err_msg + Style.RESET_ALL)
                if log_error_func:
                    log_error_func(err_msg, request_id)
            
            _sync_cwd_from_shell(log_info_func, request_id, 'builtin')
            return
        
        advanced_syntax = check_advanced_syntax(clean_cmd, redirect_config, sys_type)
        has_advanced = advanced_syntax.get('has_any_advanced', False)
        
        if has_advanced:
            error_msg = msg["builtin_no_advanced"].format(cmd=actual_cmd_head)
            print(Fore.YELLOW + error_msg + Style.RESET_ALL)
            if log_error_func:
                log_error_func(f"内置命令使用了高级语法：{actual_cmd_head}", request_id)
            return
        
        if user_mode and hasattr(user_mode, 'current_mode') and user_mode.current_mode != "adv":
            if global_config and not is_command_allowed_in_mode(
                actual_cmd_head, user_mode.current_mode, global_config, log_error_func, request_id
            ):
                print(Fore.RED + msg["mode_deny"].format(mode=user_mode.current_mode, cmd=actual_cmd_head) + Style.RESET_ALL)
                return
        
        all_paths, resolved_paths = _collect_and_resolve_paths(clean_cmd, redirect_config, resolve_path_func, _cwd)
        
        if resolved_paths:
            if not check_path_permission_for_cmd(
                actual_cmd_head, resolved_paths, username, user_mode,
                log_info_func, log_error_func, request_id, msg,
                USER_HOME_DIR, is_ai_call=is_ai_triggered
            ):
                return
        
        if log_info_func:
            log_info_func(f"执行内置命令：{actual_cmd_head}", request_id)
        
        try:
            BUILTIN_COMMANDS[actual_cmd_head](cmd_parts, request_id)
        except Exception as e:
            err_msg = f"内置命令执行失败：{str(e)}"
            print(Fore.RED + err_msg + Style.RESET_ALL)
            if log_error_func:
                log_error_func(err_msg, request_id)
        
        _sync_cwd_from_shell(log_info_func, request_id, 'builtin')
        return
    
    # ========== Step 11c: 其余终端内置命令 ==========
    if cmd_type == 'other_terminal':
        if adv_need_confirm:
            if not _adv_confirm_prompt(clean_cmd, current_lang, msg, log_info_func, request_id):
                print(Fore.YELLOW + msg["adv_confirm_cancelled"] + Style.RESET_ALL)
                return
        
        all_paths, resolved_paths = _collect_and_resolve_paths(clean_cmd, redirect_config, resolve_path_func, _cwd)
        
        if resolved_paths:
            if not check_path_permission_for_cmd(
                actual_cmd_head, resolved_paths, username, user_mode,
                log_info_func, log_error_func, request_id, msg,
                USER_HOME_DIR, is_ai_call=is_ai_triggered
            ):
                return
        
        advanced_syntax = check_advanced_syntax(clean_cmd, redirect_config, sys_type)
        if resolved_paths:
            if not check_fine_grained_advanced_syntax(
                resolved_paths, ROOT_DIR, username, user_mode,
                advanced_syntax, log_info_func, log_error_func, request_id,
                USER_HOME_DIR, is_ai_call=is_ai_triggered
            ):
                return
        
        if not check_dangerous_commands(clean_cmd, user_mode, log_info_func, log_error_func, request_id, USER_HOME_DIR):
            return
        
        if user_mode and hasattr(user_mode, 'current_mode') and user_mode.current_mode != "adv":
            if global_config and not is_command_allowed_in_mode(
                actual_cmd_head, user_mode.current_mode, global_config, log_error_func, request_id
            ):
                print(Fore.RED + msg["mode_deny"].format(mode=user_mode.current_mode, cmd=actual_cmd_head) + Style.RESET_ALL)
                return
        
        if user_mode and hasattr(user_mode, 'current_mode') and user_mode.current_mode != "adv":
            for path in all_paths:
                if path and path != 'STDOUT':
                    if check_sandbox_path_func and not check_sandbox_path_func(path, request_id):
                        print(Fore.RED + msg["path_sandbox_blocked"].format(path=path) + Style.RESET_ALL)
                        return
        
        if ROOT_DIR:
            if not check_protected_dir_for_cmd(clean_cmd, ROOT_DIR, get_virtual_path_func, log_error_func, request_id):
                return
        
        if redirect_config.get('here_delimiter') and redirect_config.get('here_doc') is None:
            print(Fore.CYAN + msg["here_doc_waiting"].format(delim=redirect_config['here_delimiter']) + Style.RESET_ALL)
            lines = []
            try:
                while True:
                    try:
                        line = safe_input("")
                        if line.strip() == redirect_config['here_delimiter']:
                            break
                        lines.append(line)
                    except EOFError:
                        break
            except Exception:
                pass
            redirect_config['here_doc'] = '\n'.join(lines)
        
        success, result = _execute_command_unified(
            final_cmd, clean_cmd, redirect_config, 'other_terminal', None,
            request_id, True,
            run_cmd_sync_func,
            replace_virtual_path_in_cmd_func,
            sys_type,
            log_info_func, log_error_func,
            msg, Fore, Style,
            debug_parsecmd=debug_parsecmd,
            cmd_head=actual_cmd_head
        )
        return
    
    # ========== Step 11d: 工具命令 ==========
    if cmd_type == 'tool':
        if adv_need_confirm:
            if not _adv_confirm_prompt(clean_cmd, current_lang, msg, log_info_func, request_id):
                print(Fore.YELLOW + msg["adv_confirm_cancelled"] + Style.RESET_ALL)
                return
        
        is_tool, tool_info = _is_tool_command_local(actual_cmd_head, tool_cache, cmd_mapping)
        
        if not tool_info:
            print(Fore.RED + msg["cmd_not_found"].format(cmd=actual_cmd_head) + Style.RESET_ALL)
            return
        
        # 兼容 dict 和对象格式
        if isinstance(tool_info, dict):
            tool_perm = tool_info.get("tool_perm", 3)
        else:
            tool_perm = tool_info.tool_perm if hasattr(tool_info, 'tool_perm') else 3
        
        if check_tool_permission_func:
            if not check_tool_permission_func(tool_perm):
                return
        
        all_paths, resolved_paths = _collect_and_resolve_paths(clean_cmd, redirect_config, resolve_path_func, _cwd)
        
        advanced_syntax = check_advanced_syntax(clean_cmd, redirect_config, sys_type)
        if resolved_paths:
            if not check_fine_grained_advanced_syntax(
                resolved_paths, ROOT_DIR, username, user_mode,
                advanced_syntax, log_info_func, log_error_func, request_id,
                USER_HOME_DIR, is_ai_call=is_ai_triggered
            ):
                return
        
        if resolved_paths:
            if not check_path_permission_for_cmd(
                actual_cmd_head, resolved_paths, username, user_mode,
                log_info_func, log_error_func, request_id, msg,
                USER_HOME_DIR, is_ai_call=is_ai_triggered
            ):
                return
        
        if not check_dangerous_commands(clean_cmd, user_mode, log_info_func, log_error_func, request_id, USER_HOME_DIR):
            return
        
        success, result = _execute_command_unified(
            final_cmd, clean_cmd, redirect_config, 'tool', tool_info,
            request_id, True,
            run_cmd_sync_func,
            replace_virtual_path_in_cmd_func,
            sys_type,
            log_info_func, log_error_func,
            msg, Fore, Style,
            debug_parsecmd=debug_parsecmd,
            cmd_head=actual_cmd_head
        )
        return
    
    # ========== Step 11e: 系统命令 ==========
    if cmd_type == 'system':
        if adv_need_confirm:
            if not _adv_confirm_prompt(clean_cmd, current_lang, msg, log_info_func, request_id):
                print(Fore.YELLOW + msg["adv_confirm_cancelled"] + Style.RESET_ALL)
                return
        
        all_paths, resolved_paths = _collect_and_resolve_paths(clean_cmd, redirect_config, resolve_path_func, _cwd)
        
        if resolved_paths:
            if not check_path_permission_for_cmd(
                actual_cmd_head, resolved_paths, username, user_mode,
                log_info_func, log_error_func, request_id, msg,
                USER_HOME_DIR, is_ai_call=is_ai_triggered
            ):
                return
        
        advanced_syntax = check_advanced_syntax(clean_cmd, redirect_config, sys_type)
        if resolved_paths:
            if not check_fine_grained_advanced_syntax(
                resolved_paths, ROOT_DIR, username, user_mode,
                advanced_syntax, log_info_func, log_error_func, request_id,
                USER_HOME_DIR, is_ai_call=is_ai_triggered
            ):
                return
        
        if not check_dangerous_commands(
            clean_cmd, user_mode, log_info_func, log_error_func, request_id,
            USER_HOME_DIR
        ):
            return
        
        # `_unknown_origin` 的命令（原本 unknown，让 shell 判断）跳过模式权限检查
        if not _unknown_origin and user_mode and hasattr(user_mode, 'current_mode') and user_mode.current_mode != "adv":
            if global_config and not is_command_allowed_in_mode(
                actual_cmd_head, user_mode.current_mode, global_config, log_error_func, request_id
            ):
                print(Fore.RED + msg["mode_deny"].format(mode=user_mode.current_mode, cmd=actual_cmd_head) + Style.RESET_ALL)
                return
        
        if not _unknown_origin and user_mode and hasattr(user_mode, 'current_mode') and user_mode.current_mode != "adv":
            for path in all_paths:
                if path and path != 'STDOUT':
                    if check_sandbox_path_func and not check_sandbox_path_func(path, request_id):
                        print(Fore.RED + msg["path_sandbox_blocked"].format(path=path) + Style.RESET_ALL)
                        return
        
        if ROOT_DIR:
            if not check_protected_dir_for_cmd(clean_cmd, ROOT_DIR, get_virtual_path_func, log_error_func, request_id):
                return
        
        if redirect_config.get('here_delimiter') and redirect_config.get('here_doc') is None:
            print(Fore.CYAN + msg["here_doc_waiting"].format(delim=redirect_config['here_delimiter']) + Style.RESET_ALL)
            lines = []
            try:
                while True:
                    try:
                        line = safe_input("")
                        if line.strip() == redirect_config['here_delimiter']:
                            break
                        lines.append(line)
                    except EOFError:
                        break
            except Exception:
                pass
            redirect_config['here_doc'] = '\n'.join(lines)
        
        success, result = _execute_command_unified(
            final_cmd, clean_cmd, redirect_config, 'system', None,
            request_id, True,
            run_cmd_sync_func,
            replace_virtual_path_in_cmd_func,
            sys_type,
            log_info_func, log_error_func,
            msg, Fore, Style,
            debug_parsecmd=debug_parsecmd,
            cmd_head=actual_cmd_head
        )

        # === 原为 unknown 的命令：bash 已尝试执行，若不存在则提示相似命令 ===
        if _unknown_origin and actual_cmd_head:
            import shutil as _shutil
            # bash 执行完了，用 which 快速判断命令是否真的不存在
            # （只查 PATH，shell 函数/alias 不在此列，但不影响体验）
            if not _shutil.which(actual_cmd_head):
                similar_tools = _find_similar_tools_local(actual_cmd_head, tool_cache)
                similar_cmds = _find_similar_cmds_local(actual_cmd_head, cmd_mapping)
                if similar_tools or similar_cmds:
                    if current_lang == "chinese":
                        print(Fore.YELLOW + "你是不是想输入：" + Style.RESET_ALL)
                    else:
                        print(Fore.YELLOW + "Did you mean:" + Style.RESET_ALL)
                    for name, tool_type in similar_tools[:10]:
                        print(Fore.YELLOW + f"  {name}" + Style.RESET_ALL)
                    for name in similar_cmds[:10]:
                        print(Fore.YELLOW + f"  {name}" + Style.RESET_ALL)
        return