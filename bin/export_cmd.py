import os
import sys
import re
import shlex
from typing import List, Callable, Any, Dict
from lib.terminal.colors import Fore, Style

def handle_export_core(
    cmd_parts: List[str],
    request_id: str,
    log_info: Callable[[str, str], None],
    log_error: Callable[[str, str], None],
    Fore: Any,
    Style: Any
) -> None:
    """
    export命令核心逻辑 - 完全对齐BASH语法
    """
    # 初始化会话变量存储
    if not hasattr(handle_export_core, "SESSION_VARS"):
        handle_export_core.SESSION_VARS = {}
    
    SESSION_VARS = handle_export_core.SESSION_VARS
    
    # 双语提示映射（只保留错误信息）
    lang_msgs = {
        "chinese": {
            "invalid_option": "无效选项：{}",
            "var_not_exists": "变量 {} 未定义",
            "var_expand_error": "变量展开失败：{}"
        },
        "english": {
            "invalid_option": "Invalid option：{}",
            "var_not_exists": "Variable {} not defined",
            "var_expand_error": "Variable expansion failed：{}"
        }
    }
    
    # 自动识别当前语言
    current_lang = os.getenv("ONYX_LANG", "chinese").lower()
    current_lang = "chinese" if current_lang not in ["chinese", "english"] else current_lang
    msg = lang_msgs[current_lang]
    
    # 空命令处理（静默返回，不显示任何信息）
    if len(cmd_parts) < 2:
        return
    
    # 解析参数
    args = cmd_parts[1:]
    
    # 处理 -p 选项（静默返回，不显示）
    if "-p" in args:
        return
    
    # 处理选项（只支持 -p，其他选项报错）
    for arg in args:
        if arg.startswith('-') and arg != '-p':
            print(Fore.RED + msg["invalid_option"].format(arg) + Style.RESET_ALL)
            return
    
    # 批量处理变量
    for arg in args:
        # 跳过选项
        if arg.startswith('-'):
            continue
        
        # 解析变量赋值
        var_name, var_value = parse_export_arg(arg, SESSION_VARS, msg, request_id, log_error)
        
        if var_name is None:
            continue
        
        # 存储到会话变量和环境变量
        SESSION_VARS[var_name] = var_value
        os.environ[var_name] = var_value
        log_info(f"Exported variable: {var_name} = {var_value[:100]}", request_id)


def parse_export_arg(
    arg: str,
    session_vars: Dict[str, str],
    msg: dict,
    request_id: str,
    log_error: Callable[[str, str], None]
) -> tuple:
    """
    解析 export 参数，支持复杂的变量展开
    返回：(var_name, var_value) 或 (None, None) 表示失败
    """
    # 正则匹配变量名和赋值部分
    match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*=\s*(.*))?$', arg.strip())
    
    if not match:
        log_error(f"Invalid export format: {arg}", request_id)
        return None, None
    
    var_name = match.group(1)
    raw_value = match.group(2) if match.group(2) is not None else ""
    
    # 处理无赋值的情况（export VAR）
    if not raw_value and raw_value != "":
        # 优先从会话变量获取，再从环境变量获取
        if var_name in session_vars:
            return var_name, session_vars[var_name]
        elif var_name in os.environ:
            return var_name, os.environ[var_name]
        else:
            print(Fore.RED + msg["var_not_exists"].format(var_name) + Style.RESET_ALL)
            log_error(f"Export failed: Variable {var_name} not defined", request_id)
            return None, None
    
    # 处理有赋值的情况（export VAR=value）
    raw_value = raw_value.strip()
    
    # 处理引号包裹的值
    if raw_value:
        # 处理单引号（原样保留，不展开变量）
        if raw_value.startswith("'") and raw_value.endswith("'"):
            return var_name, raw_value[1:-1]
        
        # 处理双引号（需要展开变量）
        if raw_value.startswith('"') and raw_value.endswith('"'):
            inner_value = raw_value[1:-1]
            expanded_value = expand_variables(inner_value, session_vars, msg, request_id, log_error)
            return var_name, expanded_value
        
        # 无引号的值（需要展开变量）
        expanded_value = expand_variables(raw_value, session_vars, msg, request_id, log_error)
        return var_name, expanded_value
    
    # 空值
    return var_name, ""


def expand_variables(
    value: str,
    session_vars: Dict[str, str],
    msg: dict,
    request_id: str,
    log_error: Callable[[str, str], None]
) -> str:
    """
    展开字符串中的变量引用
    """
    result = []
    i = 0
    length = len(value)
    
    while i < length:
        ch = value[i]
        
        if ch == '$' and i + 1 < length:
            i += 1
            ch2 = value[i]
            
            # ${VAR} 格式
            if ch2 == '{':
                i += 1
                var_start = i
                brace_count = 1
                
                # 查找匹配的 }
                while i < length and brace_count > 0:
                    if value[i] == '{':
                        brace_count += 1
                    elif value[i] == '}':
                        brace_count -= 1
                    i += 1
                
                var_name = value[var_start:i-1] if brace_count == 0 else ""
                
                # 处理 ${VAR:-default} 格式
                if ':-' in var_name:
                    var_parts = var_name.split(':-', 1)
                    var_name = var_parts[0]
                    default_value = var_parts[1] if len(var_parts) > 1 else ""
                    
                    if var_name in session_vars:
                        var_value = session_vars[var_name]
                    elif var_name in os.environ:
                        var_value = os.environ[var_name]
                    else:
                        var_value = default_value
                    
                    result.append(var_value)
                else:
                    # 普通 ${VAR}
                    if var_name in session_vars:
                        result.append(session_vars[var_name])
                    elif var_name in os.environ:
                        result.append(os.environ[var_name])
                    else:
                        # 变量不存在，保留原样
                        result.append(f"${{{var_name}}}")
                        log_error(msg["var_expand_error"].format(var_name), request_id)
            
            # $VAR 格式（简单变量）
            elif ch2.isalpha() or ch2 == '_':
                var_start = i
                i += 1
                while i < length and (value[i].isalnum() or value[i] == '_'):
                    i += 1
                
                var_name = value[var_start:i]
                
                if var_name in session_vars:
                    result.append(session_vars[var_name])
                elif var_name in os.environ:
                    result.append(os.environ[var_name])
                else:
                    result.append(f"${var_name}")
                    log_error(msg["var_expand_error"].format(var_name), request_id)
                
                i -= 1
            
            # 其他 $ 后跟非变量字符
            else:
                result.append('$' + ch2)
        
        else:
            result.append(ch)
        
        i += 1
    
    return ''.join(result)


# 辅助函数
def get_exported_vars() -> Dict[str, str]:
    """获取当前所有导出的变量"""
    if hasattr(handle_export_core, "SESSION_VARS"):
        return handle_export_core.SESSION_VARS.copy()
    return {}


def set_exported_var(var_name: str, var_value: str) -> None:
    """设置导出变量"""
    if not hasattr(handle_export_core, "SESSION_VARS"):
        handle_export_core.SESSION_VARS = {}
    handle_export_core.SESSION_VARS[var_name] = var_value
    os.environ[var_name] = var_value


def unset_exported_var(var_name: str) -> bool:
    """清除导出变量"""
    if hasattr(handle_export_core, "SESSION_VARS"):
        if var_name in handle_export_core.SESSION_VARS:
            del handle_export_core.SESSION_VARS[var_name]
            if var_name in os.environ:
                del os.environ[var_name]
            return True
    return False