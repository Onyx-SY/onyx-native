import os
import sys
from typing import List, Callable, Dict
from lib.terminal.colors import Fore, Style

# -------------------------- 依赖导入（复用 Onyx 核心能力，确保解析一致性） --------------------------
def get_onyx_deps():
    """延迟导入 Onyx 核心依赖，避免循环导入"""
    try:
        from Onyx import (
            parse_options, validate_param_path, get_virtual_path,USER_HOME_DIR,
            log_error, global_config
        )
        return parse_options, validate_param_path, get_virtual_path, log_error, global_config
    except ImportError as e:
        raise ImportError("核心依赖缺失：Onyx.py 模块异常") from e

# -------------------------- 通用语言工具函数 --------------------------
def get_lang_msgs(current_lang: str) -> Dict[str, Dict[str, str]]:
    return {
        "which": {
            "chinese": {
                "usage": "用法：which [选项] <命令/工具>...",
                "example": "示例：which ls、which python3、which -a du、which nmap（查询Onyx工具）",
                "no_such_cmd": "which: 没有找到 '{}'（系统命令/Onyx工具/Onyx内置命令中均无匹配）",
                "invalid_opt": "which: 无效选项 -- '{}'",
                "try_help": "请尝试 'which --help' 获取更多信息。",
                "error": "which: 错误：{}"
            },
            "english": {
                "usage": "Usage: which [OPTION]... COMMAND/TOOL...",
                "example": "Example: which ls、which python3、which -a du、which nmap（search Onyx tool）",
                "no_such_cmd": "which: no '{}' in (system commands/Onyx tools/Onyx builtins)",
                "invalid_opt": "which: invalid option -- '{}'",
                "try_help": "Try 'which --help' for more information.",
                "error": "which: error: {}"
            }
        }
    }

# -------------------------- which 命令 --------------------------
def handle_which(cmd_parts: List[str], request_id: str) -> None:
    parse_options, validate_param_path, get_virtual_path, log_error, global_config = get_onyx_deps()
    current_lang = global_config["display_info"]["language"]["current"]
    lang_msgs = get_lang_msgs(current_lang)["which"][current_lang]
    
    try:
        supported_short = ['-a']
        supported_long = ['--all']
        opt_list, targets = parse_options(cmd_parts[1:], supported_short, supported_long)
        show_all = any(opt in ['-a', '--all'] for opt in opt_list)
        
        if not targets:
            print(Fore.RED + lang_msgs["usage"] + "\n" + lang_msgs["example"] + Style.RESET_ALL)
            return
        
        from Onyx import BUILTIN_COMMANDS, TOOL_INDEX_CACHE, CMD_MAPPING_CACHE, sys_type, OS_OR_TBS
        
        def search_target(target: str) -> List[str]:
            matches = []
            target_lower = target.lower()
            
            builtin_matches = [
                cmd for cmd in BUILTIN_COMMANDS.keys()
                if cmd.lower() == target_lower
            ]
            for cmd in builtin_matches:
                matches.append(f"/onyx/bin/{cmd}")
                if not show_all:
                    return matches
            
            tool_matches = []
            for cache_key, tool_info in TOOL_INDEX_CACHE.items():
                if sys_type == "Windows":
                    tool_name = cache_key.split(f"_{sys_type}")[0].lower()
                else:
                    tool_name = cache_key.split(f"_{sys_type}")[0] if f"_{sys_type}" in cache_key else cache_key
                tool_name = tool_name.lower()
                
                if tool_name == target_lower:
                    virtual_path = get_virtual_path(tool_info.path)
                    tool_matches.append(virtual_path)
                    if not show_all:
                        break
            
            matches.extend(tool_matches)
            if not show_all and matches:
                return matches
            
            path_dirs = os.environ.get('PATH', '').split(os.pathsep)
            path_dirs = [dir for dir in path_dirs if dir]
            
            for dir_path in path_dirs:
                exe_path = os.path.join(dir_path, target)
                if sys_type == "Windows":
                    exe_suffixes = ['.exe', '.com', '.bat', '.cmd']
                    found = False
                    for suffix in exe_suffixes:
                        win_exe_path = exe_path + suffix
                        if os.path.isfile(win_exe_path) and os.access(win_exe_path, os.X_OK):
                            virtual_path = get_virtual_path(win_exe_path)
                            matches.append(virtual_path)
                            found = True
                            if not show_all:
                                return matches
                    if found and not show_all:
                        break
                else:
                    if os.path.isfile(exe_path) and os.access(exe_path, os.X_OK):
                        virtual_path = get_virtual_path(exe_path)
                        matches.append(virtual_path)
                        if not show_all:
                            return matches
            
            return list(dict.fromkeys(matches))
        
        for target in targets:
            matches = search_target(target)
            if matches:
                for match in matches:
                    print(match)
            else:
                if current_lang == "chinese":
                    print(Fore.RED + lang_msgs["no_such_cmd"].format(target) + Style.RESET_ALL)
                else:
                    path_str = os.pathsep.join(os.environ.get('PATH', '').split(os.pathsep))
                    print(Fore.RED + lang_msgs["no_such_cmd"].format(target, path_str) + Style.RESET_ALL)
    
    except ValueError as e:
        print(Fore.RED + f"which: {str(e)}" + Style.RESET_ALL)
        print(Fore.YELLOW + lang_msgs["try_help"] + Style.RESET_ALL)
    except Exception as e:
        err_msg = lang_msgs["error"].format(str(e))
        print(Fore.RED + err_msg + Style.RESET_ALL)
        log_error(err_msg, request_id)

