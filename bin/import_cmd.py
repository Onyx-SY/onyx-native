import os
import json
import uuid
from typing import List, Dict, Callable, Any

def get_global_lang(USER_HOME_DIR: str) -> str:
    """独立语言检测（兼容原有逻辑，不依赖Onyx.py全局变量）"""
    LANGUAGE_CONFIG_PATH = os.path.join(USER_HOME_DIR, ".config", "onyx", "language")
    try:
        if os.path.exists(LANGUAGE_CONFIG_PATH):
            with open(LANGUAGE_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                lang = f.read().strip().lower()
            return lang if lang in ["chinese", "english"] else "chinese"
        else:
            return "chinese"
    except Exception:
        return "chinese"

def handle_import_core(
    cmd_parts: List[str],
    request_id: str,
    USER_HOME_DIR: str,
    current_sys_cmds: Dict[str, List[str]],
    sys_type: str,
    BUILTIN_COMMANDS: Dict[str, Callable],
    CMD_MAPPING_CACHE: Dict[str, Any],
    TOOL_INDEX_CACHE: Dict[str, Any],
    build_cmd_mapping_cache: Callable[[str], None],
    log_info: Callable[[str, str], None],
    log_error: Callable[[str, str], None],
    Fore: Any,
    Style: Any
) -> None:
    """
    导入命令核心逻辑（与Onyx.py解耦，通过参数注入依赖）
    功能：
    1. 将系统命令添加到用户扩展映射文件（USER_HOME_DIR/.mapping.json）
    2. 仅作用户层面扩展，不修改系统级映射
    3. 映射文件（不存在时）
    4. 严格去重校验（内置命令/系统命令/用户扩展/缓存均不重复）
    """
    current_lang = get_global_lang(USER_HOME_DIR)
    # 双语提示映射（与原逻辑一致）
    lang_msgs = {
        "chinese": {
            "usage": "用法：",
            "usage_desc1": "  import <系统命令名> - 将命令添加到用户扩展映射表（USER_HOME_DIR/.mapping.json）",
            "example1": "示例：import nano",
            "example2": "示例：import vim",
            "already_exists": "❌ 命令「{cmd}」已存在",
            "import_success": "✅ 命令「{cmd}」已成功导入到用户扩展映射表",
            "create_user_mapping": "📁 自动创建用户扩展映射文件：{path}",
            "save_fail": "❌ 保存用户扩展映射文件失败：{err}",
            "unsupported_sys": "不支持的系统类型：{sys}",
            "log_import": "导入命令到用户扩展映射：{cmd}",
            "log_create_file": "创建用户扩展映射文件：{path}",
            "log_save_file": "保存用户扩展映射文件：{path}"
        },
        "english": {
            "usage": "Usage：",
            "usage_desc1": "  import <system_command> - Add command to user extension mapping (USER_HOME_DIR/.mapping.json)",
            "example1": "Example：import nano",
            "example2": "Example：import vim",
            "already_exists": "❌ Command {cmd} already exists",
            "import_success": "✅ Command「{cmd}」successfully imported to user extension mapping",
            "create_user_mapping": "📁 Auto created user extension mapping file：{path}",
            "save_fail": "❌ Failed to save user extension mapping file：{err}",
            "unsupported_sys": "Unsupported system type：{sys}",
            "log_import": "Import command to user extension mapping：{cmd}",
            "log_create_file": "Create user extension mapping file：{path}",
            "log_save_file": "Save user extension mapping file：{path}"
        }
    }
    msg = lang_msgs[current_lang]

    # 1. 参数校验（必须传入命令名）
    if len(cmd_parts) < 2:
        print(Fore.RED + msg["usage"] + Style.RESET_ALL)
        print(msg["usage_desc1"])
        print(Fore.YELLOW + msg["example1"] + Style.RESET_ALL)
        print(Fore.YELLOW + msg["example2"] + Style.RESET_ALL)
        return
    cmd_name = cmd_parts[1].strip()
    cmd_name_lower = cmd_name.lower()

    # 2. 核心去重校验（覆盖所有缓存场景）
    # 2.1 检查内置命令缓存
    if cmd_name in BUILTIN_COMMANDS or cmd_name_lower in BUILTIN_COMMANDS:
        print(Fore.RED + msg["already_exists"].format(cmd=cmd_name) + Style.RESET_ALL)
        return
    # 2.2 检查系统命令缓存
    sys_cmds = current_sys_cmds.get(sys_type, [])
    if cmd_name in sys_cmds or cmd_name_lower in [cmd.lower() for cmd in sys_cmds]:
        print(Fore.RED + msg["already_exists"].format(cmd=cmd_name) + Style.RESET_ALL)
        return
    # 2.3 检查用户扩展映射文件
    user_mapping_path = os.path.join(USER_HOME_DIR, ".mapping.json")
    def check_user_mapping() -> bool:
        if not os.path.exists(user_mapping_path):
            return False
        try:
            with open(user_mapping_path, "r", encoding="utf-8") as f:
                user_mapping = json.load(f)
            if isinstance(user_mapping, dict) and sys_type in user_mapping:
                user_cmds = user_mapping[sys_type]
                return cmd_name in user_cmds or cmd_name_lower in [cmd.lower() for cmd in user_cmds]
            return False
        except Exception:
            return False
    if check_user_mapping():
        print(Fore.RED + msg["already_exists"].format(cmd=cmd_name) + Style.RESET_ALL)
        return
    # 2.4 检查命令映射缓存（确保无遗漏）
    def get_cached_cmd() -> tuple:
        if sys_type not in CMD_MAPPING_CACHE:
            return ("none", None)
        sys_cache = CMD_MAPPING_CACHE[sys_type]
        cmd_mapping = sys_cache["mapping"]
        # 检查内置命令
        if cmd_name_lower in cmd_mapping["builtins"]:
            for real_cmd, func in BUILTIN_COMMANDS.items():
                if func.__name__ == cmd_mapping["builtins"][cmd_name_lower]:
                    return ("builtins", func)
        # 检查系统命令
        if cmd_name in cmd_mapping["system"]:
            return ("system", cmd_name)
        # 检查工具箱工具
        if cmd_name_lower in cmd_mapping["tools"]:
            return ("tools", cmd_mapping["tools"][cmd_name_lower])
        return ("none", None)
    cmd_type, _ = get_cached_cmd()
    if cmd_type != "none":
        print(Fore.RED + msg["already_exists"].format(cmd=cmd_name) + Style.RESET_ALL)
        return

    # 3. 读取/创建用户扩展映射文件
    def load_user_mapping() -> dict:
        if not os.path.exists(user_mapping_path):
            return {}
        try:
            with open(user_mapping_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 校验格式合法性
            if isinstance(data, dict) and sys_type in data and isinstance(data[sys_type], list):
                return data
            else:
                return {sys_type: []}
        except Exception as e:
            log_error(f"读取用户扩展映射文件失败：{str(e)}", request_id)
            return {sys_type: []}

    # 4. 导入命令到映射表
    user_mapping = load_user_mapping()
    if sys_type not in user_mapping:
        user_mapping[sys_type] = []
    user_mapping[sys_type].append(cmd_name)
    log_info(msg["log_import"].format(cmd=cmd_name), request_id)

    # 5. 保存映射文件（自动创建+权限设置）
    try:
        # 自动创建文件（不存在时）
        if not os.path.exists(user_mapping_path):
            with open(user_mapping_path, "w", encoding="utf-8") as f:
                json.dump(user_mapping, f, ensure_ascii=False, indent=2)
            # Linux类系统设置权限（仅所有者可读写）
            if sys_type in ["Linux/macOS", "Termux", "SpecialLinux"]:
                os.chmod(user_mapping_path, 0o600)
            
            log_info(msg["log_create_file"].format(path=user_mapping_path), request_id)
        else:
            # 更新现有文件
            with open(user_mapping_path, "w", encoding="utf-8") as f:
                json.dump(user_mapping, f, ensure_ascii=False, indent=2)
        
        # ========== 成功提示已静默（注释/删除） ==========
        # print(Fore.GREEN + msg["import_success"].format(cmd=cmd_name) + Style.RESET_ALL)
        
        log_info(msg["log_save_file"].format(path=user_mapping_path), request_id)

        # 6. 同步到当前会话（无需重启生效）
        if sys_type in current_sys_cmds:
            current_sys_cmds[sys_type].append(cmd_name)
        else:
            current_sys_cmds[sys_type] = [cmd_name]
        # 7. 重建命令映射缓存（确保新导入命令立即可用）
        build_cmd_mapping_cache(request_id)
    except Exception as e:
        err_msg = msg["save_fail"].format(err=str(e))
        print(Fore.RED + err_msg + Style.RESET_ALL)
        log_error(err_msg, request_id)
