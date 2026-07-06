# bin/sado_cmd.py
"""
sado 命令核心实现：临时提升权限执行命令
"""
import os
import re
from typing import List, Dict, Any, Callable

def _split_commands_simple(cmd_str: str) -> List[str]:
    """简单分割命令：按 ; && || | 分割，用于检查每个子命令是否需要确认"""
    if not cmd_str:
        return []
    
    parts = []
    current = []
    in_quote = False
    quote_char = None
    i = 0
    
    while i < len(cmd_str):
        ch = cmd_str[i]
        
        if ch in ('"', "'") and (i == 0 or cmd_str[i-1] != '\\'):
            if not in_quote:
                in_quote = True
                quote_char = ch
            elif ch == quote_char:
                in_quote = False
                quote_char = None
            current.append(ch)
            i += 1
            continue
        
        if not in_quote:
            # 分号 ;;
            if ch == ';' and i + 1 < len(cmd_str) and cmd_str[i+1] == ';':
                if current:
                    parts.append(''.join(current).strip())
                    current = []
                i += 2
                continue
            # 分号 ;
            if ch == ';':
                if current:
                    parts.append(''.join(current).strip())
                    current = []
                i += 1
                continue
            # &&
            if ch == '&' and i + 1 < len(cmd_str) and cmd_str[i+1] == '&':
                if current:
                    parts.append(''.join(current).strip())
                    current = []
                i += 2
                continue
            # ||
            if ch == '|' and i + 1 < len(cmd_str) and cmd_str[i+1] == '|':
                if current:
                    parts.append(''.join(current).strip())
                    current = []
                i += 2
                continue
            # 管道 |
            if ch == '|':
                if current:
                    parts.append(''.join(current).strip())
                    current = []
                i += 1
                continue
        
        current.append(ch)
        i += 1
    
    if current:
        parts.append(''.join(current).strip())
    
    return [p for p in parts if p]


def _expand_alias_for_check(cmd_str: str, alias_cache: Dict[str, Any]) -> str:
    """
    仅用于权限检查的别名展开（不修改原始命令）
    递归展开别名，返回展开后的命令字符串用于检查
    """
    if not alias_cache or not cmd_str:
        return cmd_str
    
    result = cmd_str
    max_iterations = 10  # 防止循环引用
    
    for _ in range(max_iterations):
        changed = False
        parts = result.split(maxsplit=1)
        if not parts:
            break
        
        cmd_head = parts[0]
        
        # 检查是否有匹配的别名
        if cmd_head in alias_cache:
            alias_info = alias_cache[cmd_head]
            target = alias_info.get("target", "")
            if target:
                # 别名替换：保留参数
                if len(parts) > 1:
                    result = f"{target} {parts[1]}"
                else:
                    result = target
                changed = True
            else:
                break
        
        if not changed:
            break
    
    return result


def _is_cmd_allowed_by_pattern(cmd: str, pattern: str) -> bool:
    """
    检查命令是否匹配权限模式（前缀匹配）
    
    规则：
    1. pattern == "*" → 匹配所有命令
    2. pattern 以空格结尾或完整匹配命令 → 精确前缀匹配
    3. 其他情况 → pattern 必须是 cmd 的前缀（按单词边界）
    
    示例：
    - pattern "nmap 127.0.0.1" 匹配 "nmap 127.0.0.1" 和 "nmap 127.0.0.1 -sV"
    - pattern "rm -rf /tmp/" 匹配 "rm -rf /tmp/a" 和 "rm -rf /tmp/ab/c"
    - pattern "rm -rf /tmp" 匹配 "rm -rf /tmp/a"（因为 /tmp 是前缀）
    - pattern "systemctl reload" 匹配 "systemctl reload nginx"
    """
    if pattern == "*":
        return True
    
    # 标准化：去除首尾空格，多个空格合并为一个
    cmd_normalized = ' '.join(cmd.strip().split())
    pattern_normalized = ' '.join(pattern.strip().split())
    
    # 精确前缀匹配
    # 如果 pattern 是 cmd 的前缀，则允许
    if cmd_normalized.startswith(pattern_normalized):
        # 额外检查：确保匹配在单词边界
        # 例如 pattern "rm" 不应该匹配 "rmdir"
        if len(pattern_normalized) < len(cmd_normalized):
            next_char = cmd_normalized[len(pattern_normalized)]
            # 如果下一个字符是空格或命令结束，则算匹配
            if next_char == ' ':
                return True
            # 如果 pattern 以空格结尾，直接允许
            if pattern_normalized.endswith(' '):
                return True
            # 否则，需要检查是否是完整单词匹配
            # pattern "rm" 匹配 "rm -rf"（下一个字符是空格）√
            # pattern "rm" 不匹配 "rmdir"（下一个字符是字母）✗
            return False
        return True
    
    return False


def _check_command_permission(cmd: str, need_con: List[str], no_con: List[str]) -> tuple:
    """
    检查命令权限
    
    返回: (need_confirm: bool, blocked: bool, matched_pattern: str)
    - need_confirm: 是否需要用户确认
    - blocked: 是否被拦截
    - matched_pattern: 匹配到的模式（用于调试）
    """
    # 提取命令的第一个单词（用于快捷匹配）
    first_word = cmd.strip().split()[0] if cmd.strip() else ""
    
    # 1. 检查 need_con（需要确认的命令）
    for pattern in need_con:
        if _is_cmd_allowed_by_pattern(cmd, pattern):
            return (True, False, pattern)
    
    # 2. 检查 no_con（不需要确认的命令）
    for pattern in no_con:
        if _is_cmd_allowed_by_pattern(cmd, pattern):
            return (False, False, pattern)
    
    # 3. 如果 need_con 和 no_con 都有通配符，检查是否在白名单中
    has_wildcard_need = any(p == "*" for p in need_con)
    has_wildcard_no = any(p == "*" for p in no_con)
    
    if has_wildcard_need or has_wildcard_no:
        # 有通配符，但没有匹配任何模式 → 需要确认（默认行为）
        return (True, False, None)
    
    # 4. 没有匹配任何模式 → 拦截
    return (False, True, None)


def handle_sado_core(
    cmd_parts: List[str],
    request_id: str,
    user_mode,
    global_config: dict,
    SADO_CONFIG: list,
    SADO_CONFIG_PATH: str,
    user_info: dict,
    OS_OR_TBS: str,
    sys_type: str,
    parse_and_execute,
    alias_cache: Dict[str, Any],           # ALIAS_CACHE
    log_info,
    log_error,
    get_current_lang,
    Fore,
    Style
):
    lang_msgs = {
        "chinese": {
            "usage": "用法：sado <命令...>",
            "desc": "临时提升权限执行命令（类似sudo）",
            "no_config": "sado配置文件不存在，请先由root用户（OS模式）或当前用户（TBS模式）初始化",
            "no_rule": "当前用户没有配置任何sado权限规则",
            "perm_denied": "权限不足：无法执行命令 '{}'",
            "perm_denied_detail": "  用户：{}，最高允许模式：{}，当前模式：{}",
            "need_confirm": "⚠️ 执行命令 '{}' 需要确认，是否继续？(y/N)：",
            "confirm_cancel": "已取消执行",
            "exec_start": "sado：临时提升权限执行命令",
            "exec_end": "sado：权限已恢复",
            "invalid_config": "配置文件格式错误：{}",
            "mode_limit": "无法提升到 adv 模式（配置限制最高为 {}）",
            "already_adv": "当前已是最高模式，无需使用 sado",
            "rule_not_found": "未找到匹配的权限规则",
            "alias_expanded": "别名展开：{} -> {}",
            "pattern_matched": "匹配模式：{} -> {}"
        },
        "english": {
            "usage": "Usage: sado <command...>",
            "desc": "Temporarily elevate permissions to execute command (like sudo)",
            "no_config": "Sado config file not found, please let root (OS mode) or current user (TBS mode) initialize first",
            "no_rule": "No sado permission rules configured for current user",
            "perm_denied": "Permission denied: cannot execute command '{}'",
            "perm_denied_detail": "  User: {}, max allowed mode: {}, current mode: {}",
            "need_confirm": "⚠️ Command '{}' requires confirmation, continue? (y/N): ",
            "confirm_cancel": "Execution cancelled",
            "exec_start": "sado: Temporarily elevating permissions to execute command",
            "exec_end": "sado: Permissions restored",
            "invalid_config": "Invalid config format: {}",
            "mode_limit": "Cannot elevate to adv mode (config limit is {})",
            "already_adv": "Already in highest mode, sado is not needed",
            "rule_not_found": "No matching permission rule found",
            "alias_expanded": "Alias expanded: {} -> {}",
            "pattern_matched": "Pattern matched: {} -> {}"
        }
    }
    current_lang = get_current_lang()
    msg = lang_msgs.get(current_lang, lang_msgs["chinese"])

    # 检查参数
    if len(cmd_parts) < 2:
        print(Fore.RED + msg["usage"] + Style.RESET_ALL)
        print(Fore.YELLOW + msg["desc"] + Style.RESET_ALL)
        return

    # 如果已经是 adv 模式
    if user_mode.current_mode == "adv":
        print(Fore.YELLOW + msg["already_adv"] + Style.RESET_ALL)
        target_cmd = " ".join(cmd_parts[1:])
        parse_and_execute(target_cmd)
        return

    # 检查配置文件
    if not SADO_CONFIG or not os.path.exists(SADO_CONFIG_PATH):
        print(Fore.RED + msg["no_config"] + Style.RESET_ALL)
        return

    # 获取当前用户名
    current_user = user_info.get("name", "default")

    # ========== 修改：支持通配符 "*" 匹配所有用户 ==========
    matched_rule = None
    
    # 优先级：精确用户匹配 > 通配符匹配
    for rule in SADO_CONFIG:
        # 精确匹配用户名
        if "user" in rule and rule["user"] == current_user:
            matched_rule = rule
            break
    
    # 如果没有精确匹配，查找通配符 "*" 规则
    if matched_rule is None:
        for rule in SADO_CONFIG:
            if "user" in rule and rule["user"] == "*":
                matched_rule = rule
                break
    
    # 如果没有用户匹配，尝试组匹配
    if matched_rule is None:
        for rule in SADO_CONFIG:
            if "group" in rule:
                # 组名通配符
                if rule["group"] == "*":
                    matched_rule = rule
                    break
                # 精确组名匹配
                if rule["group"] == current_user or rule["group"] == "users":
                    matched_rule = rule
                    break

    if not matched_rule:
        print(Fore.RED + msg["rule_not_found"] + Style.RESET_ALL)
        print(Fore.YELLOW + msg["no_rule"] + Style.RESET_ALL)
        return

    original_cmd = " ".join(cmd_parts[1:])
    
    # ========== 别名展开（仅用于权限检查，不修改原始命令）==========
    expanded_cmd_for_check = _expand_alias_for_check(original_cmd, alias_cache)
    if expanded_cmd_for_check != original_cmd:
        log_info(msg["alias_expanded"].format(original_cmd, expanded_cmd_for_check), request_id)
        if global_config.get("system_info", {}).get("debug_mode", False):
            print(Fore.CYAN + f"[sado] 别名展开（检查用）: {original_cmd} -> {expanded_cmd_for_check}" + Style.RESET_ALL)

    max_mode = matched_rule.get("max_mode", "low")
    need_con_cmds = matched_rule.get("need_con", [])
    no_con_cmds = matched_rule.get("no_con", [])

    # 计算可提升到的模式
    mode_order = ["low", "mid", "adv"]
    current_idx = mode_order.index(user_mode.current_mode)
    max_idx = mode_order.index(max_mode)
    target_mode = max_mode if max_idx > current_idx else user_mode.current_mode

    # ========== 使用展开后的命令进行权限检查 ==========
    cmd_for_check = expanded_cmd_for_check
    sub_commands = _split_commands_simple(cmd_for_check)

    need_confirm = False
    blocked = False
    blocked_cmd = None
    matched_pattern = None

    for sub_cmd in sub_commands:
        if not sub_cmd.strip():
            continue
        
        # 检查当前子命令的权限
        confirm_needed, is_blocked, pattern = _check_command_permission(sub_cmd, need_con_cmds, no_con_cmds)
        
        if is_blocked:
            # 提取第一个单词作为显示用
            blocked_cmd = sub_cmd.strip().split()[0] if sub_cmd.strip() else sub_cmd
            blocked = True
            break
        
        if confirm_needed:
            need_confirm = True
            if pattern:
                matched_pattern = pattern

    if blocked:
        print(Fore.RED + msg["perm_denied"].format(blocked_cmd) + Style.RESET_ALL)
        print(Fore.YELLOW + msg["perm_denied_detail"].format(
            current_user, max_mode, user_mode.current_mode
        ) + Style.RESET_ALL)
        return

    if need_confirm:
        # 显示匹配到的模式（如果有）
        if matched_pattern and global_config.get("system_info", {}).get("debug_mode", False):
            print(Fore.CYAN + msg["pattern_matched"].format(original_cmd[:50], matched_pattern) + Style.RESET_ALL)
        
        # ── 4 位 hex 验证码确认 ──
        import secrets as _sado_secrets
        captcha = _sado_secrets.token_hex(2).upper()
        print(Fore.RED + msg["need_confirm"].format(original_cmd[:50]) + Style.RESET_ALL)
        print(Fore.YELLOW + f"验证码: [ {captcha} ]  — 请输入上方验证码以确认执行" + Style.RESET_ALL)
        try:
            user_in = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if user_in.upper() != captcha:
            print(Fore.RED + "验证码错误，已取消" + Style.RESET_ALL)
            return

    # 保存原始模式
    original_mode = user_mode.current_mode
    original_tool_perm = user_mode.current_tool_perm

    try:
        if target_mode != original_mode:
            user_mode.current_mode = target_mode
            mode_perm_limit = global_config["mode_config"]["perm_limit"][target_mode]["max_tool_perm"]
            user_mode.current_tool_perm = mode_perm_limit
            log_info(f"sado: 临时提升权限 {original_mode} -> {target_mode}", request_id)
            if global_config.get("system_info", {}).get("debug_mode", False):
                print(Fore.CYAN + f"[sado] 权限临时提升: {original_mode} -> {target_mode}" + Style.RESET_ALL)

        # ========== 执行原始命令（让 parse_and_execute 自己处理别名）==========
        parse_and_execute(original_cmd)

    except Exception as e:
        log_error(f"sado 执行失败：{str(e)}", request_id)
        print(Fore.RED + f"sado: {str(e)}" + Style.RESET_ALL)
    finally:
        if target_mode != original_mode:
            user_mode.current_mode = original_mode
            user_mode.current_tool_perm = original_tool_perm
            log_info(f"sado: 权限已恢复 {target_mode} -> {original_mode}", request_id)