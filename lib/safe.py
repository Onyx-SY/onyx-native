# lib/safe.py
"""
安全检查模块
包含：模式权限、路径权限（perm_path.json）、保护目录检查、高危命令拦截、细颗粒度路径检测
"""

import os
import json
import sys
import re
import uuid
from typing import List, Tuple, Dict, Any, Optional
from lib.terminal.colors import Fore, Style


# 全局调试标志
_DEBUG_FLAG = False

# 全局根目录
_ROOT_DIR = None

# 全局路径权限配置
PERM_PATH_CONFIG = []
PERM_PATH_CONFIG_LOADED = False

# 当前语言
_CURRENT_LANG = "chinese"

# === adv 模式危险命令二次确认控制 ===
_ADV_DANGER_CMD_PROMPT_ENABLED = True  # 默认开启
_ADV_DANGER_CMD_PROMPT_MEMORY = {}  # 内存缓存，key=cmd_type，value=是否询问
_ADV_FORCE_CONFIRM_COMMANDS = {'rm', 'redirect', 'here_doc'}  # 强制二次确认的命令类型


def _get_adv_danger_cmd_config_path(user_home: str = None) -> str:
    """获取 adv_danger_cmd_prompt 配置文件路径"""
    if user_home:
        return os.path.join(user_home, '.config', 'onyx', 'adv_danger_cmd_prompt')
    # 默认路径
    home = os.path.expanduser('~')
    return os.path.join(home, '.config', 'onyx', 'adv_danger_cmd_prompt')


def load_adv_danger_cmd_config(user_home: str = None) -> bool:
    """
    加载 adv_danger_cmd_prompt 配置
    
    返回：
    - True：启用二次确认
    - False：关闭二次确认
    """
    global _ADV_DANGER_CMD_PROMPT_ENABLED
    
    config_path = _get_adv_danger_cmd_config_path(user_home)
    
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read().strip().lower()
                _ADV_DANGER_CMD_PROMPT_ENABLED = (content == 'true')
                _debug_print(f"加载 adv_danger_cmd_prompt 配置：{_ADV_DANGER_CMD_PROMPT_ENABLED}")
                return _ADV_DANGER_CMD_PROMPT_ENABLED
        else:
            # 文件不存在，默认启用（true）
            _ADV_DANGER_CMD_PROMPT_ENABLED = True
            _debug_print("adv_danger_cmd_prompt 配置文件不存在，默认启用二次确认")
            return True
    except Exception as e:
        _debug_print(f"加载 adv_danger_cmd_prompt 配置失败：{e}")
        _ADV_DANGER_CMD_PROMPT_ENABLED = True
        return True


def save_adv_danger_cmd_config(enabled: bool, user_home: str = None) -> bool:
    """
    保存 adv_danger_cmd_prompt 配置
    
    参数：
    - enabled: True 启用二次确认，False 关闭二次确认
    
    返回：
    - True：保存成功
    - False：保存失败
    """
    global _ADV_DANGER_CMD_PROMPT_ENABLED
    
    config_path = _get_adv_danger_cmd_config_path(user_home)
    
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        
        content = 'true' if enabled else 'false'
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(content + '\n')
        
        _ADV_DANGER_CMD_PROMPT_ENABLED = enabled
        _debug_print(f"保存 adv_danger_cmd_prompt 配置：{enabled}")
        return True
    except Exception as e:
        _debug_print(f"保存 adv_danger_cmd_prompt 配置失败：{e}")
        return False


def should_skip_adv_confirm(cmd_type: str) -> bool:
    """
    检查是否应该跳过 adv 二次确认（同时检查文件和内存缓存）
    
    参数：
    - cmd_type: 命令类型标识（如 'rm', 'redirect', 'here_doc', 'path' 等）
    
    返回：
    - True：跳过询问（直接允许）
    - False：需要询问
    """
    global _ADV_DANGER_CMD_PROMPT_ENABLED, _ADV_DANGER_CMD_PROMPT_MEMORY, _ADV_FORCE_CONFIRM_COMMANDS
    
    # === 强制二次确认的命令类型永远需要询问，并给出明确提示 ===
    if cmd_type in _ADV_FORCE_CONFIRM_COMMANDS:
        force_messages = {
            'rm': "rm 命令属于强制确认类型，即使关闭二次确认也需要确认",
            'redirect': "重定向操作属于强制确认类型，即使关闭二次确认也需要确认",
            'here_doc': "Here Document 属于强制确认类型，即使关闭二次确认也需要确认"
        }
        if _DEBUG_FLAG:
            print(Fore.YELLOW + f"[INFO] {force_messages.get(cmd_type, '强制确认类型')}" + Style.RESET_ALL)
        return False
    
    # === 先检查全局开关 ===
    if not _ADV_DANGER_CMD_PROMPT_ENABLED:
        return True
    
    # 检查内存缓存
    if cmd_type in _ADV_DANGER_CMD_PROMPT_MEMORY:
        return not _ADV_DANGER_CMD_PROMPT_MEMORY[cmd_type]
    
    return False


def save_adv_confirm_result(cmd_type: str, should_prompt: bool) -> None:
    """保存 adv 二次确认结果到内存缓存"""
    global _ADV_DANGER_CMD_PROMPT_MEMORY
    _ADV_DANGER_CMD_PROMPT_MEMORY[cmd_type] = should_prompt


def clear_adv_confirm_memory() -> None:
    """清空 adv 二次确认内存缓存"""
    global _ADV_DANGER_CMD_PROMPT_MEMORY
    _ADV_DANGER_CMD_PROMPT_MEMORY = {}


def is_adv_danger_cmd_prompt_enabled() -> bool:
    """检查 adv 危险命令二次确认是否启用"""
    return _ADV_DANGER_CMD_PROMPT_ENABLED


def set_adv_danger_cmd_prompt_enabled(enabled: bool) -> None:
    """设置 adv 危险命令二次确认开关"""
    global _ADV_DANGER_CMD_PROMPT_ENABLED
    _ADV_DANGER_CMD_PROMPT_ENABLED = enabled


def set_perm_debug_flag(flag: bool):
    """设置调试输出开关"""
    global _DEBUG_FLAG
    _DEBUG_FLAG = flag


def set_lang(lang: str):
    """设置当前语言"""
    global _CURRENT_LANG
    _CURRENT_LANG = lang


def _debug_print(msg: str, color=Fore.CYAN):
    """统一调试输出"""
    if _DEBUG_FLAG:
        print(color + f"[PERM_DEBUG] {msg}" + Style.RESET_ALL)


# ========== 多语言消息定义 ==========
LANG_MESSAGES = {
    "chinese": {
        "variable_cmd_fine_grained_blocked": "❌ 命令 '{cmd}' 包含变量（如 $VAR），当前目录 '{dir}' 在细颗粒度控制中，无法确定变量展开后的路径",
        "config_file_not_found": "致命错误：权限路径配置文件不存在：{path}",
        "config_json_error": "致命错误：解析 perm_path.json 失败（JSON 格式错误）：{error}",
        "config_load_error": "致命错误：加载 perm_path.json 异常：{error}",
        "config_value_not_list": "路径权限配置项 '{path}' 的值不是列表，已忽略",
        "config_invalid_pattern": "无效路径模式（缺少斜杠）：{path}",
        "config_format_error": "路径模式格式错误（应为 <name:depth> 或 <name>）：{path}",
        "config_missing_brackets": "路径模式缺少尖括号，忽略：{path}",
        "fine_grained_path_denied": "❌ 细颗粒度路径权限拦截：命令 '{cmd}' 不允许在路径 '{path}' 上执行",
        "fine_grained_redirect_denied": "❌ 当前在细颗粒控制路径中，不允许使用重定向或 Here Document",
        "fine_grained_pipeline_denied": "❌ 当前在细颗粒控制路径中，不允许使用管道",
        "fine_grained_logic_denied": "❌ 当前在细颗粒控制路径中，不允许使用逻辑操作符（&&, ||）",
        "adv_confirm_exec": "⚠️ 高级模式，确认执行？(y/N): ",
        "adv_path_permission_warning": "⚠️ Onyx: {cmd} Permission Denied path:{path}",
        "adv_confirm_path": "⚠️ 高级模式，该命令在细颗粒控制路径中，确认执行？(y/N): ",
        "adv_confirm_redirect": "⚠️ 高级模式，细颗粒控制路径中使用了重定向/Here Doc，确认执行？(y/N): ",
        "adv_confirm_pipeline": "⚠️ 高级模式，细颗粒控制路径中使用了管道，确认执行？(y/N): ",
        "adv_confirm_logic": "⚠️ 高级模式，细颗粒控制路径中使用了逻辑操作符，确认执行？(y/N): ",
        "user_cancelled": "用户取消执行",
        "user_confirmed": "用户确认执行",
        "dangerous_cmd_blocked": "❌ 高危命令被拦截：{cmd} (匹配模式: {pattern})",
        "dangerous_cmd_warning": "⚠️ 高危命令：{cmd} (匹配模式: {pattern})",
        "dangerous_cmd_confirm": "确认执行？(y/N): ",
        "protected_dir_blocked": "❌ 当前目录「{dir}」是核心保护目录，不允许使用 {cmd} 命令",
        "tool_perm_denied": "❌ 工具权限不足：工具 '{tool}' 需要权限等级 {required}，当前模式权限等级上限为 {max_perm}",
        "mode_cmd_denied": "❌ 当前{mode}模式不允许执行命令「{cmd}」",
        # === 新增消息 ===
        "adv_confirm_disable_ask": "是否关闭后续此类危险操作的二次确认？(y/N): ",
        "adv_confirm_disabled": "✅ 二次确认已关闭，可通过 'manage set adv_danger_cmd_prompt true' 重新开启",
        "adv_confirm_keep_enabled": "二次确认保持开启",
        "adv_danger_cmd_prompt_disabled": "[adv] 危险命令二次确认已关闭，直接允许执行",
        # === 新增：强制确认提示 ===
        "force_confirm_info": "⚠️ 注意：即使关闭二次确认，{cmd_types} 等危险操作仍需要确认",
        "force_confirm_rm": "rm",
        "force_confirm_redirect": "重定向",
        "force_confirm_here_doc": "Here Document",
    },
    "english": {
        "variable_cmd_fine_grained_blocked_en": "❌ Command '{cmd}' contains variable (like $VAR), current directory '{dir}' is under fine-grained control, cannot determine expanded path",
        "config_file_not_found": "Fatal: Permission path config file not found: {path}",
        "config_json_error": "Fatal: Failed to parse perm_path.json (JSON format error): {error}",
        "config_load_error": "Fatal: Exception loading perm_path.json: {error}",
        "config_value_not_list": "Path permission config item '{path}' is not a list, ignored",
        "config_invalid_pattern": "Invalid path pattern (missing slash): {path}",
        "config_format_error": "Path pattern format error (expected <name:depth> or <name>): {path}",
        "config_missing_brackets": "Path pattern missing brackets, ignored: {path}",
        "fine_grained_path_denied": "❌ Fine-grained path permission denied: command '{cmd}' on path '{path}'",
        "fine_grained_redirect_denied": "❌ Redirection/Here Doc not allowed in fine-grained control path",
        "fine_grained_pipeline_denied": "❌ Pipeline not allowed in fine-grained control path",
        "fine_grained_logic_denied": "❌ Logical operators (&&, ||) not allowed in fine-grained control path",
        "adv_confirm_exec": "⚠️ Advanced mode, confirm execution? (y/N): ",
        "adv_path_permission_warning": "⚠️ Onyx: {cmd} Permission Denied path:{path}",
        "adv_confirm_path": "⚠️ Advanced mode, command in fine-grained path, confirm? (y/N): ",
        "adv_confirm_redirect": "⚠️ Advanced mode, redirect/Here Doc in fine-grained path, confirm? (y/N): ",
        "adv_confirm_pipeline": "⚠️ Advanced mode, pipeline in fine-grained path, confirm? (y/N): ",
        "adv_confirm_logic": "⚠️ Advanced mode, logical operators in fine-grained path, confirm? (y/N): ",
        "user_cancelled": "User cancelled execution",
        "user_confirmed": "User confirmed execution",
        "dangerous_cmd_blocked": "❌ Dangerous command blocked: {cmd} (pattern: {pattern})",
        "dangerous_cmd_warning": "⚠️ Dangerous command: {cmd} (pattern: {pattern})",
        "dangerous_cmd_confirm": "Confirm execution? (y/N): ",
        "protected_dir_blocked": "❌ Current directory '{dir}' is a protected directory, '{cmd}' command not allowed",
        "tool_perm_denied": "❌ Tool permission denied: tool '{tool}' requires level {required}, max level in current mode is {max_perm}",
        "mode_cmd_denied": "❌ Command '{cmd}' not allowed in {mode} mode",
        # === 新增消息 ===
        "adv_confirm_disable_ask": "Disable further dangerous operation confirmations? (y/N): ",
        "adv_confirm_disabled": "✅ Confirmation disabled, use 'manage set adv_danger_cmd_prompt true' to re-enable",
        "adv_confirm_keep_enabled": "Confirmation remains enabled",
        "adv_danger_cmd_prompt_disabled": "[adv] Dangerous command confirmation disabled, allowing execution",
        # === 新增：强制确认提示 ===
        "force_confirm_info": "⚠️ Note: Even if confirmation is disabled, {cmd_types} still require confirmation",
        "force_confirm_rm": "rm",
        "force_confirm_redirect": "redirect",
        "force_confirm_here_doc": "here document",
    }
}


def _get_msg(key: str, **kwargs) -> str:
    """获取多语言消息"""
    lang_msgs = LANG_MESSAGES.get(_CURRENT_LANG, LANG_MESSAGES["chinese"])
    msg_template = lang_msgs.get(key, LANG_MESSAGES["chinese"].get(key, key))
    if kwargs:
        return msg_template.format(**kwargs)
    return msg_template


def _adv_confirm_with_disable_option(warning_msg: str, confirm_msg: str, 
                                      cmd_type: str, user_home: str = None,
                                      force_confirm: bool = False) -> bool:
    """
    adv 模式下带"关闭二次确认"选项的确认逻辑
    
    参数：
    - warning_msg: 警告消息
    - confirm_msg: 确认消息
    - cmd_type: 命令类型标识（用于内存缓存和配置文件）
    - user_home: 用户主目录路径
    - force_confirm: 是否强制二次确认（rm、重定向、here-doc）
    
    返回：
    - True：确认执行
    - False：取消执行
    """
    global _ADV_DANGER_CMD_PROMPT_ENABLED
    
    # === 如果是强制确认类型，给出额外提示 ===
    if force_confirm:
        force_types = []
        if cmd_type == 'rm':
            force_types.append(_get_msg("force_confirm_rm"))
        elif cmd_type == 'redirect':
            force_types.append(_get_msg("force_confirm_redirect"))
        elif cmd_type == 'here_doc':
            force_types.append(_get_msg("force_confirm_here_doc"))
        
        if force_types:
            force_info = _get_msg("force_confirm_info", cmd_types=', '.join(force_types))
            print(Fore.YELLOW + force_info + Style.RESET_ALL)
    
    # ── 4 位 hex 验证码确认 ──
    import secrets as _safe_secrets
    captcha = _safe_secrets.token_hex(2).upper()
    print(Fore.RED + warning_msg + Style.RESET_ALL)
    print(Fore.YELLOW + f"验证码: [ {captcha} ]  — 请输入上方验证码以确认执行" + Style.RESET_ALL)
    try:
        confirm = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    
    if confirm.upper() != captcha:
        print(Fore.RED + "验证码错误，已取消" + Style.RESET_ALL)
        return False
    
    # === 强制确认类型不允许关闭二次确认 ===
    if force_confirm:
        if _DEBUG_FLAG:
            print(Fore.CYAN + f"[INFO] {cmd_type} 是强制确认类型，不允许关闭二次确认" + Style.RESET_ALL)
        return True
    
    # 用户确认执行，检查是否需要询问"关闭二次确认"
    if not _ADV_DANGER_CMD_PROMPT_ENABLED:
        _debug_print(f"adv_danger_cmd_prompt 已关闭，跳过后续询问")
        return True
    
    # 检查内存缓存
    if should_skip_adv_confirm(cmd_type):
        _debug_print(f"内存缓存中 {cmd_type} 已关闭二次确认，跳过询问")
        return True
    
    # 询问是否关闭后续此类危险操作的二次确认
    try:
        disable_msg = _get_msg("adv_confirm_disable_ask")
        disable_confirm = input(Fore.YELLOW + disable_msg + Style.RESET_ALL)
    except (EOFError, KeyboardInterrupt):
        disable_confirm = 'n'
    
    if disable_confirm.lower() == 'y':
        # === 用户选择关闭二次确认，保存到文件 ===
        success = save_adv_danger_cmd_config(False, user_home)
        if success:
            print(Fore.GREEN + _get_msg("adv_confirm_disabled") + Style.RESET_ALL)
            # 保存到内存缓存
            save_adv_confirm_result(cmd_type, False)
            # === 清空内存缓存，让下次重新从文件加载 ===
            clear_adv_confirm_memory()
        else:
            print(Fore.RED + "保存配置失败" + Style.RESET_ALL)
    else:
        # 用户选择保持开启
        _debug_print(f"用户选择保持 {cmd_type} 类型的二次确认")
        save_adv_confirm_result(cmd_type, True)
    
    return True


# ========== 模式权限等级映射 ==========
MODE_PERM_LEVELS = {
    "low": 2,   # 1-2级
    "mid": 4,   # 1-4级
    "adv": 5,   # 1-5级
}


def get_mode_max_perm(mode: str) -> int:
    """获取模式的最大权限等级"""
    if not mode:
        return 2  # 默认 low
    return MODE_PERM_LEVELS.get(mode.lower(), 2)


def load_perm_path_config(root_dir: str, username: str, log_error_func) -> bool:
    """
    加载并解析 perm_path.json
    支持变量 <username>、<other>、<*:depth>、<name:depth>、* 通配符
    路径模式示例：
      /home/<other>/*      - 匹配 /home/<其他用户>/ 下任意一级
      /home/<other:2>      - 同上，深度为 2
      /var/log/<*:10>      - 匹配 /var/log/ 下 10 级深度
      /home/<username:1>   - 匹配 /home/<当前用户>
    """
    global PERM_PATH_CONFIG, PERM_PATH_CONFIG_LOADED, _ROOT_DIR
    
    if PERM_PATH_CONFIG_LOADED:
        _debug_print("路径权限配置已加载，跳过")
        return True
    
    _ROOT_DIR = root_dir
    _debug_print(f"设置全局根目录: {_ROOT_DIR}")
    
    # === 新增：加载 adv_danger_cmd_prompt 配置 ===
    # 从 root_dir 推断 user_home
    user_home = None
    if root_dir:
        # 尝试从 root_dir 推断用户主目录
        potential_home = os.path.dirname(os.path.dirname(root_dir.rstrip('/')))
        if os.path.exists(os.path.join(potential_home, '.config')):
            user_home = potential_home
    
    # === 修改：加载配置后，如果配置为关闭，清空内存缓存 ===
    load_adv_danger_cmd_config(user_home)
    if not _ADV_DANGER_CMD_PROMPT_ENABLED:
        clear_adv_confirm_memory()
    
    a = os.path.abspath(__file__)
    b = os.path.dirname(a)
    c = os.path.dirname(b)
    perm_path_file = os.path.join(c, "etc", "perm_path.json")
    _debug_print(f"尝试加载权限配置文件：{perm_path_file}")
    
    if not os.path.exists(perm_path_file):
        print(Fore.RED + _get_msg("config_file_not_found", path=perm_path_file) + Style.RESET_ALL)
        print(perm_path_file)
        sys.exit(1)
    
    try:
        with open(perm_path_file, 'r', encoding='utf-8') as f:
            raw_config = json.load(f)
        _debug_print(f"成功读取配置文件，原始规则数量：{len(raw_config)}")
        
        # 编译正则
        pattern_with_depth = re.compile(r'<([^:]+):(\d+)>$')
        pattern_simple = re.compile(r'<([^>]+)>$')
        
        # 清空现有配置
        PERM_PATH_CONFIG = []
        
        for path_pattern, rule_value in raw_config.items():
            if path_pattern == "_config":
                continue  # 跳过顶层配置项
            
            # 新格式：dict {mode, allow_advanced_syntax, commands}
            if isinstance(rule_value, dict):
                allowed_cmds = rule_value.get("commands", [])
                mode = rule_value.get("mode", "whitelist")
                allow_adv = rule_value.get("allow_advanced_syntax", True)
            # 旧格式兼容：纯 list
            elif isinstance(rule_value, list):
                allowed_cmds = rule_value
                mode = "whitelist"
                allow_adv = True
            else:
                if log_error_func:
                    log_error_func(_get_msg("config_value_not_list", path=path_pattern), str(uuid.uuid4()))
                _debug_print(f"跳过无效配置项：{path_pattern}，值不是列表或字典")
                continue
            
            # 解析路径模式
            rule = _parse_single_path_pattern(
                path_pattern, allowed_cmds, root_dir, username,
                pattern_with_depth, pattern_simple, log_error_func,
                mode=mode, allow_advanced_syntax=allow_adv
            )
            
            if rule:
                PERM_PATH_CONFIG.append(rule)
                _debug_print(f"添加规则：virt={rule['fixed_virt']}, phys={rule['fixed_phys']}, "
                           f"name={rule['name_pattern']}, depth={rule['depth']}, "
                           f"mode={rule.get('mode', 'whitelist')}, adv_syntax={rule.get('allow_advanced_syntax', True)}")
            else:
                _debug_print(f"跳过无效模式：{path_pattern}")
        
        PERM_PATH_CONFIG_LOADED = True
        _debug_print(f"最终加载规则数：{len(PERM_PATH_CONFIG)}")
        return True
    
    except json.JSONDecodeError as e:
        print(Fore.RED + _get_msg("config_json_error", error=str(e)) + Style.RESET_ALL)
        sys.exit(1)
    except Exception as e:
        print(Fore.RED + _get_msg("config_load_error", error=str(e)) + Style.RESET_ALL)
        sys.exit(1)


def _parse_single_path_pattern(path_pattern: str, allowed_cmds: List[str],
                                root_dir: str, username: str,
                                pattern_with_depth, pattern_simple,
                                log_error_func,
                                mode: str = "whitelist",
                                allow_advanced_syntax: bool = True) -> Optional[Dict]:
    """
    解析单个路径模式
    
    支持的格式：
    1. /path/<name:depth>     - 指定名称和深度
    2. /path/<name>           - 指定名称，深度默认为 1
    3. /path/<name:depth>/*   - 指定名称和深度，尾部有 * 通配（多一层通配）
    4. /path/<name>/*         - 指定名称，默认深度 1，尾部有 * 通配（实际深度 +1）
    5. <name>                 - 全局匹配模式：匹配任意路径中包含该目录名的路径
    """
    # === 处理全局模式（如 <onyx>）===
    # 全局模式格式：<name> 且不包含任何斜杠
    if path_pattern.startswith('<') and path_pattern.endswith('>') and '/' not in path_pattern:
        name_pattern = path_pattern[1:-1]  # 提取 onyx
        _debug_print(f"检测到全局匹配模式：{path_pattern}，name_pattern={name_pattern}")
        
        return {
            'fixed_virt': None,
            'fixed_phys': None,
            'name_pattern': name_pattern,
            'depth': None,
            'allowed': [cmd.lower() for cmd in allowed_cmds],
            'raw_pattern': path_pattern,
            'extra_wild': False,
            'global_match': True,
            'mode': mode,
            'allow_advanced_syntax': allow_advanced_syntax,
        }
    
    # 检查是否有尾部 /*
    extra_wild = False
    working_pattern = path_pattern
    
    if path_pattern.endswith('/*'):
        extra_wild = True
        working_pattern = path_pattern[:-2]
        _debug_print(f"检测到尾部 /*，extra_wild=True，处理模式：{working_pattern}")
    
    # 检查是否包含 <>
    if '<' not in working_pattern or not working_pattern.endswith('>'):
        if log_error_func:
            log_error_func(_get_msg("config_missing_brackets", path=path_pattern), str(uuid.uuid4()))
        _debug_print(f"跳过缺少尖括号的模式：{path_pattern}")
        return None
    
    # 找到最后一个斜杠
    last_slash = working_pattern.rfind('/')
    if last_slash == -1:
        if log_error_func:
            log_error_func(_get_msg("config_invalid_pattern", path=path_pattern), str(uuid.uuid4()))
        _debug_print(f"跳过无效模式（缺少斜杠）：{path_pattern}")
        return None
    
    fixed = working_pattern[:last_slash]
    wild_part = working_pattern[last_slash+1:]
    
    # 解析通配部分
    m = pattern_with_depth.match(wild_part)
    if m:
        name_pattern = m.group(1)
        depth = int(m.group(2))
        _debug_print(f"解析带深度模式：fixed={fixed}, name_pattern={name_pattern}, depth={depth}")
    else:
        m2 = pattern_simple.match(wild_part)
        if m2:
            name_pattern = m2.group(1)
            depth = 1
            _debug_print(f"解析无深度模式（默认深度1）：fixed={fixed}, name_pattern={name_pattern}")
        else:
            if log_error_func:
                log_error_func(_get_msg("config_format_error", path=wild_part), str(uuid.uuid4()))
            _debug_print(f"跳过格式错误的模式：{wild_part}")
            return None
    
    # 如果有 extra_wild，深度 +1
    if extra_wild:
        depth += 1
        _debug_print(f"extra_wild=True，深度调整为：{depth}")
    
    fixed = fixed.rstrip('/')
    if fixed == '':
        fixed = '/'
    
    # 构建规则
    rule = {
        'fixed_virt': fixed,
        'fixed_phys': _resolve_virt_to_phys(fixed, root_dir),
        'name_pattern': name_pattern,
        'depth': depth,
        'allowed': [cmd.lower() for cmd in allowed_cmds],
        'raw_pattern': path_pattern,
        'extra_wild': extra_wild,
        'global_match': False,
        'mode': mode,
        'allow_advanced_syntax': allow_advanced_syntax,
    }
    
    return rule


def _resolve_virt_to_phys(virt_path: str, root_dir: str) -> str:
    """将虚拟路径转换为物理路径"""
    if not virt_path:
        return virt_path
    
    if virt_path == '/':
        return root_dir
    
    if virt_path.startswith('/') and root_dir:
        return os.path.abspath(os.path.join(root_dir, virt_path.lstrip('/')))
    
    return os.path.abspath(virt_path)


def is_path_under_fine_grained_control(phys_path: str, root_dir: str, username: str, 
                                        check_existence: bool = True) -> Tuple[bool, Optional[Dict]]:
    """
    检查给定物理路径是否在细颗粒控制路径中
    返回 (是否命中, 匹配的规则字典)
    
    参数：
    - phys_path: 物理路径
    - root_dir: 根目录
    - username: 用户名
    - check_existence: 是否检查路径真实存在。如果为 True 且路径不存在，则直接返回 False
    
    匹配逻辑：
    1. 遍历所有规则
    2. 对于全局匹配规则，检查路径中是否包含目标目录名
    3. 对于普通规则，检查物理路径是否以规则的固定前缀开头
    4. 检查剩余路径段是否在深度限制内
    5. 检查每一级目录名是否符合 name_pattern
    6. 只要有一个规则匹配就返回 True
    """
    if not PERM_PATH_CONFIG or not phys_path:
        _debug_print(f"没有规则或路径为空: config={bool(PERM_PATH_CONFIG)}, path={phys_path}")
        return False, None
    
    # 检查路径是否真实存在
    if check_existence:
        if not os.path.exists(phys_path):
            _debug_print(f"路径 {phys_path} 不存在，跳过细颗粒度检查 (check_existence=True)")
            return False, None
    
    # 标准化路径
    normalized_path = os.path.abspath(phys_path)
    if normalized_path != '/':
        normalized_path = normalized_path.rstrip('/')
    
    _debug_print(f"检查路径 {normalized_path} 是否在细颗粒控制中 (check_existence={check_existence})")
    
    for rule in PERM_PATH_CONFIG:
        # === 处理全局匹配模式（如 <onyx>）===
        if rule.get('global_match', False):
            target_name = rule['name_pattern']  # 例如 'onyx'
            # 分割路径，检查是否包含该目录名
            path_parts = normalized_path.split(os.sep)
            if target_name in path_parts:
                _debug_print(f"    ✓ 路径 {normalized_path} 命中全局匹配模式：包含目录 '{target_name}'")
                return True, rule
            else:
                _debug_print(f"    路径 {normalized_path} 不包含目录 '{target_name}'，跳过全局规则")
            continue  # 未命中，继续检查其他规则
        
        # === 处理普通规则 ===
        fixed_phys = rule['fixed_phys']
        if not fixed_phys:
            continue
        
        # 标准化固定前缀
        fixed_phys_norm = fixed_phys
        if fixed_phys_norm != '/' and fixed_phys_norm != root_dir:
            fixed_phys_norm = fixed_phys_norm.rstrip('/')
        
        _debug_print(f"  检查规则: fixed_phys={fixed_phys_norm}, depth={rule['depth']}, name_pattern={rule['name_pattern']}")
        
        # 检查路径是否以固定前缀开头（同时匹配物理路径和虚拟路径）
        matched = False
        rest = normalized_path
        fixed_virt = rule.get('fixed_virt', '')
        fixed_virt_norm = fixed_virt.rstrip('/') if fixed_virt and fixed_virt != '/' else fixed_virt
        
        if fixed_phys_norm == '/' or fixed_phys_norm == root_dir:
            if root_dir and normalized_path.startswith(root_dir):
                rest = normalized_path[len(root_dir):].lstrip('/')
            elif normalized_path.startswith('/'):
                rest = normalized_path.lstrip('/')
            matched = True
        elif normalized_path.startswith(fixed_phys_norm):
            matched = True
            rest = normalized_path[len(fixed_phys_norm):].lstrip('/')
        elif fixed_virt_norm and normalized_path.startswith(fixed_virt_norm):
            # 虚拟路径匹配（处理 _collect_and_resolve_paths 兜底返回虚拟路径的情况）
            matched = True
            rest = normalized_path[len(fixed_virt_norm):].lstrip('/')
        
        if matched:
            parts = rest.split('/') if rest and rest != '/' and rest != '' else []
            _debug_print(f"    匹配前缀 {fixed_phys_norm}, 剩余部分='{rest}', 分割为{parts}, 深度限制={rule['depth']}")
            
            # 深度必须 >= 1 才算细颗粒控制
            if rule['depth'] >= 1:
                if len(parts) <= rule['depth']:
                    # 检查 name_pattern
                    all_match = True
                    for idx, part in enumerate(parts):
                        pattern = rule['name_pattern']
                        if pattern == '*':
                            continue
                        elif pattern == 'username':
                            if part != username:
                                all_match = False
                                break
                        elif pattern == 'other':
                            if part == username:
                                all_match = False
                                break
                        else:
                            if part != pattern:
                                all_match = False
                                break
                    
                    if all_match:
                        _debug_print(f"    ✓ 路径 {normalized_path} 命中细颗粒控制规则")
                        return True, rule
                    else:
                        _debug_print(f"    路径 {normalized_path} 前缀匹配但 name_pattern 不匹配")
                else:
                    _debug_print(f"    路径 {normalized_path} 深度超出限制 ({len(parts)} > {rule['depth']})")
    
    _debug_print(f"路径 {normalized_path} 未命中任何细颗粒控制规则")
    return False, None


def is_any_path_under_fine_grained_control(phys_paths: List[str], root_dir: str, username: str,
                                             check_existence: bool = True) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """
    检查路径列表中是否有任何路径在细颗粒控制中
    返回 (是否命中, 命中的路径, 匹配的规则)
    
    参数：
    - phys_paths: 物理路径列表
    - root_dir: 根目录
    - username: 用户名
    - check_existence: 是否检查路径真实存在（传递给 is_path_under_fine_grained_control）
    """
    if not phys_paths:
        return False, None, None
    
    for path in phys_paths:
        if not path:
            continue
        is_hit, rule = is_path_under_fine_grained_control(path, root_dir, username, check_existence)
        if is_hit:
            return True, path, rule
    
    return False, None, None


def check_tool_permission(tool_name: str, tool_perm: int, user_mode, 
                          log_error_func=None, request_id: str = None) -> bool:
    """
    检查工具权限是否足够
    low: 1-2级, mid: 1-4级, adv: 1-5级
    
    返回 True 表示允许执行，False 表示拦截
    """
    if not user_mode:
        return True
    
    if hasattr(user_mode, 'current_mode'):
        current_mode = user_mode.current_mode
    else:
        current_mode = "low"
    
    max_perm = get_mode_max_perm(current_mode)
    
    if tool_perm > max_perm:
        msg = _get_msg("tool_perm_denied", tool=tool_name, required=tool_perm, max_perm=max_perm)
        print(Fore.RED + msg + Style.RESET_ALL)
        if log_error_func:
            log_error_func(f"工具权限不足：{tool_name} perm={tool_perm}, mode={current_mode}, max={max_perm}", request_id)
        return False
    
    return True


def check_path_permission_for_cmd(cmd_head: str, all_phys_paths: List[str], 
                                   username: str, user_mode,
                                   log_info_func=None, log_error_func=None,
                                   request_id: str = None, msg: Dict = None,
                                   user_home: str = None,
                                   is_ai_call: bool = False) -> bool:
    """
    检查命令对路径的权限
    遍历所有路径，检查是否命中规则且命令不在允许列表中
    
    - AI 调用: 保持严格拦截
    - 人类调用: 
      - 在家目录下 → 静默跳过（不拦截）
      - 不在家目录下 → low/mid 弹确认，adv 弹确认
    
    - adv: 使用增强的确认逻辑（先检查全局开关）
    
    返回 True 表示允许执行，False 表示拦截
    """
    if not PERM_PATH_CONFIG or not all_phys_paths:
        _debug_print("没有规则或没有路径，跳过权限检查")
        return True
    
    cmd_lower = cmd_head.lower()
    _debug_print(f"开始路径权限检查：cmd='{cmd_lower}', 路径={all_phys_paths}")
    
    if user_mode and hasattr(user_mode, 'current_mode'):
        current_mode = user_mode.current_mode
    else:
        current_mode = "low"
    
    # ── 人类在家目录下操作 → 静默跳过，不拦截 ──
    if not is_ai_call and user_home:
        safe_home = os.path.abspath(user_home).rstrip('/')
        for p in all_phys_paths:
            if p and os.path.abspath(p).rstrip('/').startswith(safe_home):
                _debug_print(f"人类命令，路径 {p} 在家目录下，静默跳过拦截")
                return True
    
    # 【修复重复询问】：收集所有被拦截的路径，统一处理
    denied_paths = []
    for phys_path in all_phys_paths:
        if not phys_path:
            continue
        
        is_hit, matched_rule = is_path_under_fine_grained_control(
            phys_path, _ROOT_DIR, username, check_existence=False
        )
        
        if is_hit and matched_rule:
            _debug_print(f"路径 {phys_path} 命中规则，mode={matched_rule.get('mode', 'whitelist')}, allowed={matched_rule['allowed']}")
            
            rule_mode = matched_rule.get('mode', 'whitelist')
            if rule_mode == 'blacklist':
                # 黑名单模式：在列表中的命令被拦截
                if cmd_lower in matched_rule['allowed']:
                    _debug_print(f"命令 '{cmd_lower}' 在黑名单中，拦截")
                    denied_paths.append(phys_path)
            else:
                # 白名单模式（默认）：不在列表中的命令被拦截
                if cmd_lower not in matched_rule['allowed']:
                    _debug_print(f"命令 '{cmd_lower}' 不在白名单中")
                    denied_paths.append(phys_path)
    
    if not denied_paths:
        _debug_print("所有路径检查通过")
        return True
    
    if current_mode == 'adv':
        # 先检查全局开关和内存缓存
        cmd_type = 'path'
        if cmd_lower == 'rm':
            cmd_type = 'rm'
        
        if should_skip_adv_confirm(cmd_type):
            _debug_print(f"跳过 adv 二次确认 (cmd_type={cmd_type})")
            return True
        
        # 使用增强的确认逻辑（合并所有路径）
        paths_str = ', '.join(denied_paths[:3])  # 最多显示3个路径
        if len(denied_paths) > 3:
            paths_str += f' ... (共 {len(denied_paths)} 个路径)'
        
        warning_msg = _get_msg("adv_path_permission_warning", cmd=cmd_head, path=paths_str)
        confirm_msg = _get_msg("adv_confirm_path")
        
        return _adv_confirm_with_disable_option(
            warning_msg, confirm_msg, cmd_type, user_home,
            force_confirm=(cmd_type in _ADV_FORCE_CONFIRM_COMMANDS)
        )
    else:
        # low/mid 模式：人类可确认执行，AI 直接拒绝
        if is_ai_call:
            error_msg = _get_msg("fine_grained_path_denied", cmd=cmd_head, path=denied_paths[0])
            print(Fore.RED + error_msg + Style.RESET_ALL)
            if log_error_func:
                log_error_func(f"细颗粒度路径权限拦截：命令 {cmd_head} 不允许在路径 {denied_paths[0]} 上执行", request_id)
            return False
        else:
            # 人类用户：弹确认框
            paths_str = ', '.join(denied_paths[:3])
            if len(denied_paths) > 3:
                paths_str += f' ... (共 {len(denied_paths)} 个路径)'
            warning_msg = _get_msg("adv_path_permission_warning", cmd=cmd_head, path=paths_str)
            print(Fore.YELLOW + warning_msg + Style.RESET_ALL)
            confirm_msg = _get_msg("adv_confirm_exec")
            try:
                confirm = input(Fore.YELLOW + confirm_msg + Style.RESET_ALL).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return False
            if confirm in ('y', 'yes'):
                _debug_print(f"人类用户确认执行：{cmd_head}")
                return True
            print(Fore.RED + _get_msg("user_cancelled") + Style.RESET_ALL)
            return False

def check_fine_grained_advanced_syntax(phys_paths: List[str], root_dir: str, username: str,
                                        user_mode, advanced_types: Dict[str, bool],
                                        log_info_func=None, log_error_func=None,
                                        request_id: str = None,
                                        user_home: str = None,
                                        is_ai_call: bool = False) -> bool:
    """
    检查细颗粒度路径 + 高级语法的组合
    
    规则：
    - 人类在家目录下 → 静默跳过
    - low/mid AI: 细颗粒度 + 重定向/here-doc/管道/逻辑操作符 → 直接拒绝
    - low/mid 人类: 弹确认框
    - adv: 先检查全局开关，再使用增强的确认逻辑
    
    advanced_types 字典包含：has_redirect, has_here_doc, has_pipeline, has_logical_operators
    
    返回 True 表示允许，False 表示拦截
    """
    if not phys_paths:
        return True
    
    # ── 人类在家目录下操作 → 静默跳过 ──
    if not is_ai_call and user_home:
        safe_home = os.path.abspath(user_home).rstrip('/')
        for p in phys_paths:
            if p and os.path.abspath(p).rstrip('/').startswith(safe_home):
                _debug_print(f"人类命令，路径 {p} 在家目录下，静默跳过高级语法检查")
                return True
    
    if user_mode and hasattr(user_mode, 'current_mode'):
        current_mode = user_mode.current_mode
    else:
        current_mode = "low"
    
    any_hit, hit_path, hit_rule = is_any_path_under_fine_grained_control(
        phys_paths, root_dir, username
    )
    
    if not any_hit:
        return True
    
    has_advanced = (
        advanced_types.get('has_redirect', False) or
        advanced_types.get('has_here_doc', False) or
        advanced_types.get('has_pipeline', False) or
        advanced_types.get('has_logical_operators', False)
    )
    
    # 命令没有使用任何高级语法 → 直接放行，无需检查规则的 allow_advanced_syntax
    if not has_advanced:
        return True
    
    # 命令使用了高级语法，检查规则是否允许
    if hit_rule and not hit_rule.get('allow_advanced_syntax', True):
        if current_mode == 'adv':
            confirm_key = "adv_confirm_redirect"
            cmd_type = 'redirect'
            warning = _get_msg("fine_grained_redirect_denied")
            return _adv_confirm_with_disable_option(warning, _get_msg(confirm_key), cmd_type, user_home)
        else:
            print(Fore.RED + _get_msg("fine_grained_redirect_denied") + Style.RESET_ALL)
            return False
    
    # 细颗粒度 + 高级语法
    if advanced_types.get('has_pipeline', False):
        deny_key = "fine_grained_pipeline_denied"
        confirm_key = "adv_confirm_pipeline"
        cmd_type = 'pipeline'
    elif advanced_types.get('has_redirect', False) or advanced_types.get('has_here_doc', False):
        deny_key = "fine_grained_redirect_denied"
        confirm_key = "adv_confirm_redirect"
        cmd_type = 'redirect' if advanced_types.get('has_redirect', False) else 'here_doc'
    elif advanced_types.get('has_logical_operators', False):
        deny_key = "fine_grained_logic_denied"
        confirm_key = "adv_confirm_logic"
        cmd_type = 'logic'
    else:
        return True
    
    if current_mode == 'adv':
        # 【修复1】：先检查全局开关和内存缓存
        force_confirm = cmd_type in _ADV_FORCE_CONFIRM_COMMANDS
        
        if should_skip_adv_confirm(cmd_type):
            _debug_print(f"跳过 adv 二次确认 (cmd_type={cmd_type})")
            return True
        
        # 使用增强的确认逻辑
        warning_msg = _get_msg(confirm_key)
        confirm_msg = _get_msg("adv_confirm_exec")
        
        return _adv_confirm_with_disable_option(
            warning_msg, confirm_msg, cmd_type, user_home,
            force_confirm=force_confirm
        )
    else:
        if is_ai_call:
            # AI 调用直接拒绝
            deny_msg = _get_msg(deny_key)
            print(Fore.RED + deny_msg + Style.RESET_ALL)
            if log_error_func:
                log_error_func(f"细颗粒度拦截：{current_mode}模式不允许高级语法，命中路径 {hit_path}", request_id)
            return False
        else:
            # 人类调用弹确认框
            warning_msg = _get_msg(deny_key)
            print(Fore.YELLOW + warning_msg + Style.RESET_ALL)
            try:
                confirm = input(Fore.YELLOW + _get_msg("adv_confirm_exec") + Style.RESET_ALL).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return False
            if confirm in ('y', 'yes'):
                _debug_print(f"人类用户确认执行高级语法命令")
                return True
            print(Fore.RED + _get_msg("user_cancelled") + Style.RESET_ALL)
            return False


def is_command_allowed_in_mode(cmd_name: str, mode: str, global_config: dict, 
                               log_error_func=None, request_id: str = None) -> bool:
    """根据 cmdal.json 的 perm_limit 判断当前命令是否允许在当前模式下执行
    
    宽松规则：
    - perm_limit 未配置时默认允许所有
    - 单字符命令（可能是误触/不完整输入）放行
    - 明确在 allow_commands 中的放行
    - 不在列表中的命令在 low/mid 模式下拦截
    """
    try:
        perm_config = global_config.get("mode_config", {}).get("perm_limit", {}).get(mode, {})
        allow_commands = perm_config.get("allow_commands", [])
        
        # perm_limit 未配置 → 放行（不阻塞任何命令）
        if not perm_config:
            return True
        
        # allow_commands 为 * 通配 → 放行所有
        if allow_commands == "*":
            return True
        
        if not isinstance(allow_commands, list):
            allow_commands = []
        
        cmd_lower = cmd_name.lower()
        
        # 单字符命令（可能是误触、不完整输入）→ 放行，让 bash 处理
        if len(cmd_name) <= 2:
            return True
        
        # 明确在 allow_commands 中的放行
        for allowed in allow_commands:
            if allowed.lower() == cmd_lower:
                return True
        
        # 不在列表中 → 拦截
        return False
    except Exception as e:
        if log_error_func:
            log_error_func(f"检查命令权限时出错：{str(e)}", request_id)
        return True  # 异常时放行，不阻塞用户操作


def is_in_protected_dir(root_dir: str, get_virtual_path_func=None) -> Tuple[bool, str, List[str]]:
    """校验当前目录是否在保护目录内"""
    if not root_dir:
        return False, "", []
    
    protected_dirs = [
        os.path.abspath(os.path.join(root_dir, "onyx")),
        os.path.abspath(os.path.join(root_dir, "etc", "pki")),
        os.path.abspath(os.path.join(root_dir, "onyxlog")),
        os.path.abspath(os.path.join(root_dir, "tools", "sys_tools"))
    ]
    
    current_dir = os.path.abspath(os.getcwd())
    in_protected = any(current_dir.startswith(d) for d in protected_dirs if d)
    
    virtual_dir = current_dir
    if get_virtual_path_func:
        virtual_dir = get_virtual_path_func(current_dir)
    
    protected_virtual_dirs = []
    for p in protected_dirs:
        if p and get_virtual_path_func:
            protected_virtual_dirs.append(get_virtual_path_func(p))
        elif p:
            protected_virtual_dirs.append(p)
    
    return in_protected, virtual_dir, protected_virtual_dirs


def check_dangerous_commands(cmd_str: str, user_mode, log_info_func=None, 
                            log_error_func=None, request_id: str = None,
                            user_home: str = None) -> bool:
    """
    检查高危命令
    返回 True 表示允许执行，False 表示拦截
    """
    if not cmd_str:
        return True
    
    # 默认的高危命令模式
    default_patterns = [
        "rm -rf /", "rm -r /", "mkfs", "fdisk", "format", 
        "dd if=/dev/zero", "shutdown", "reboot", "init 0", "init 6",
        "mv /", "chmod -R 777 /"
    ]
    
    # 尝试读取 dan_cmd 配置文件
    blocked_patterns = default_patterns
    if _ROOT_DIR:
        dan_cmd_path = os.path.join(_ROOT_DIR, "onyx", "etc", "dan_cmd")
        if os.path.exists(dan_cmd_path):
            try:
                with open(dan_cmd_path, "r", encoding="utf-8") as f:
                    blocked_patterns = [line.strip().lower() for line in f 
                                       if line.strip() and not line.startswith("#")]
            except:
                pass
    
    cmd_lower = cmd_str.lower().strip()
    
    if user_mode and hasattr(user_mode, 'current_mode'):
        current_mode = user_mode.current_mode
    else:
        current_mode = "low"
    
    for pattern in blocked_patterns:
        if pattern.lower() in cmd_lower:
            if current_mode == "adv":
                # 【修复1】：先检查全局开关
                cmd_type = 'dangerous'
                cmd_parts = cmd_lower.split()
                if cmd_parts and 'rm' in cmd_parts[0]:
                    cmd_type = 'rm'
                
                if should_skip_adv_confirm(cmd_type):
                    _debug_print(f"跳过 adv 二次确认 (cmd_type={cmd_type})")
                    return True  # 允许执行
                
                # 使用增强的确认逻辑
                warning_msg = _get_msg("dangerous_cmd_warning", cmd=cmd_str, pattern=pattern)
                confirm_msg = _get_msg("dangerous_cmd_confirm")
                
                result = _adv_confirm_with_disable_option(
                    warning_msg, confirm_msg, cmd_type, user_home,
                    force_confirm=(cmd_type in _ADV_FORCE_CONFIRM_COMMANDS or cmd_type == 'dangerous')
                )
                if result:
                    break  # 确认执行，跳出循环
                else:
                    return False
            else:
                # low/mid 直接拒绝
                block_msg = _get_msg("dangerous_cmd_blocked", cmd=cmd_str, pattern=pattern)
                print(Fore.RED + block_msg + Style.RESET_ALL)
                if log_error_func:
                    log_error_func(f"高危命令拦截：{cmd_str} (匹配模式: {pattern})", request_id)
                return False
    
    return True


def check_protected_dir_for_cmd(cmd_str: str, root_dir: str, 
                                 get_virtual_path_func=None,
                                 log_error_func=None, request_id: str = None) -> bool:
    """检查特定命令是否允许在保护目录中执行"""
    if not root_dir or not cmd_str:
        return True
    
    cmd_lower = cmd_str.strip().lower()
    
    # activite 命令在保护目录中的特殊检查
    if cmd_lower.startswith("activite"):
        in_protected, virtual_dir, _ = is_in_protected_dir(root_dir, get_virtual_path_func)
        if in_protected:
            msg = _get_msg("protected_dir_blocked", dir=virtual_dir, cmd="activite")
            print(Fore.RED + msg + Style.RESET_ALL)
            if log_error_func:
                log_error_func(f"保护目录拦截：{cmd_str}", request_id)
            return False
    
    return True


def safe_input(prompt: str) -> str:
    """安全的input函数"""
    print(prompt, end='', flush=True)
    try:
        return input()
    except EOFError:
        return ""
    except KeyboardInterrupt:
        raise


# === 其余终端内置命令加载函数 ===
# === 其余终端内置命令缓存 ===
_OTHER_TERMINAL_CMDS_CACHE: Optional[Dict] = None

def load_other_terminal_commands(root_dir: str) -> Dict[str, List[str]]:
    """
    加载其他终端内置命令配置文件（模块级缓存，全会话只读一次磁盘）
    
    配置文件路径：/onyx/etc/other_terminal_cmd.json
    
    文件格式示例：
    {
        "bash": ["set", "unset", "export", "source", ...],
        "zsh": ["set", "unset", "export", "source", ...],
        "fish": ["set", "unset", "export", ...],
        "powershell": ["Set-Variable", "Remove-Variable", ...],
        "cmd": ["set", "setlocal", "endlocal", ...]
    }
    
    返回：{终端类型: [命令列表]} 或空字典
    """
    global _OTHER_TERMINAL_CMDS_CACHE
    
    if _OTHER_TERMINAL_CMDS_CACHE is not None:
        return _OTHER_TERMINAL_CMDS_CACHE
    
    if not root_dir:
        return {}
    
    config_path = os.path.join(root_dir, "onyx", "etc", "other_terminal_cmd.json")
    
    if not os.path.exists(config_path):
        _debug_print(f"其余终端内置命令配置文件不存在：{config_path}")
        _OTHER_TERMINAL_CMDS_CACHE = {}
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 将所有命令转为小写
        result = {}
        for terminal, cmds in data.items():
            if isinstance(cmds, list):
                result[terminal] = [cmd.lower() for cmd in cmds]
            else:
                result[terminal] = []
        
        _debug_print(f"加载其余终端内置命令配置：{len(result)} 个终端")
        _OTHER_TERMINAL_CMDS_CACHE = result
        return result
    except json.JSONDecodeError as e:
        _debug_print(f"解析 other_terminal_cmd.json 失败：{e}")
        _OTHER_TERMINAL_CMDS_CACHE = {}
        return {}
    except Exception as e:
        _debug_print(f"加载 other_terminal_cmd.json 失败：{e}")
        _OTHER_TERMINAL_CMDS_CACHE = {}
        return {}


def is_other_terminal_command(cmd_head: str, sys_type: str, 
                               other_terminal_cmds: Dict[str, List[str]]) -> bool:
    if not other_terminal_cmds or not sys_type:
        return False
    
    cmd_lower = cmd_head.lower()
    
    # 检查当前终端类型的命令列表
    terminal_cmds = other_terminal_cmds.get(sys_type, [])
    if cmd_lower in terminal_cmds:
        return True
    
    # 也检查通用终端类型（不排除当前类型，因为可能别名不同）
    for terminal_type in ['bash', 'sh', 'common']:
        common_cmds = other_terminal_cmds.get(terminal_type, [])
        if cmd_lower in common_cmds:
            return True
    
    return False
    

def check_variable_command_in_fine_grained_path(cmd_str: str, current_dir: str, 
                                                  root_dir: str, username: str,
                                                  log_error_func=None, 
                                                  request_id: str = None,
                                                  sys_type: str = 'bash') -> Tuple[bool, str]:
    """
    检查变量命令（cd $VAR、rm $FILE等）是否在细颗粒度路径中执行
    
    返回 (是否拦截, 拦截原因)
    """
    if not cmd_str or not root_dir:
        return False, ""
    
    # 检测是否包含变量
    has_variable = False
    variable_patterns = [
        r'\$[a-zA-Z_][a-zA-Z0-9_]*',      # $VAR
        r'\$\{[a-zA-Z_][a-zA-Z0-9_]*\}',  # ${VAR}
        r'%[a-zA-Z_][a-zA-Z0-9_]*%',      # %VAR% (cmd)
    ]
    
    for pattern in variable_patterns:
        if re.search(pattern, cmd_str):
            has_variable = True
            break
    
    if not has_variable:
        return False, ""
    
    # 检测是否包含敏感命令
    cmd_lower = cmd_str.lower().strip()
    sensitive_commands = ['cd', 'rm', 'mv', 'cp', 'ln', 'unlink', 'rmdir']
    
    first_word = cmd_lower.split()[0] if cmd_lower.split() else ""
    is_sensitive = first_word in sensitive_commands
    
    if not is_sensitive:
        return False, ""
    
    # 检查当前目录是否在细颗粒度控制路径中
    is_hit, hit_rule = is_path_under_fine_grained_control(
        current_dir, root_dir, username, check_existence=True
    )
    
    if is_hit:
        # 使用双语消息
        msg = _get_msg("variable_cmd_fine_grained_blocked", 
                       cmd=first_word, dir=current_dir)
        if log_error_func:
            log_error_func(f"变量命令细颗粒度拦截: {cmd_str[:100]}... 当前目录: {current_dir}", request_id)
        return True, msg
    
    return False, ""