import os
import sys
import json
import time
from typing import List, Callable, Dict, Any
from lib.terminal.colors import Fore, Style

def load_global_lang(USER_HOME_DIR: str) -> str:
    """读取全局语言配置（与Onyx主程序保持一致）"""
    lang_path = os.path.join(USER_HOME_DIR, ".config", "onyx", "language")
    try:
        lang_dir = os.path.dirname(lang_path)
        if not os.path.exists(lang_dir):
            os.makedirs(lang_dir, mode=0o755 if sys.platform != "win32" else 0o777)
        
        if os.path.exists(lang_path):
            with open(lang_path, "r", encoding="utf-8-sig") as f:
                lang = f.read().strip().lower()
            return lang if lang in ["chinese", "english"] else "chinese"
        else:
            with open(lang_path, "w", encoding="utf-8") as f:
                f.write("chinese")
            return "chinese"
    except Exception:
        return "chinese"

def load_autocmd_core(AUTO_CMD_PATH: str, log_info: Callable[[str, str], None], log_error: Callable[[str, str], None], request_id: str) -> List[Dict[str, Any]]:
    """加载开机自启命令（提取原逻辑）"""
    AUTO_CMDS = []
    if os.path.exists(AUTO_CMD_PATH):
        try:
            with open(AUTO_CMD_PATH, "r", encoding="utf-8") as f:
                AUTO_CMDS = json.load(f)
                # 校验格式（确保包含id、cmd、create_time）
                AUTO_CMDS = [cmd for cmd in AUTO_CMDS if all(k in cmd for k in ["id", "cmd", "create_time"])]
            log_info(f"加载开机自启命令：共{len(AUTO_CMDS)}条", request_id)
        except Exception as e:
            log_error(f"加载自启命令失败：{str(e)}", request_id)
            AUTO_CMDS = []
    else:
        log_info("开机自启命令文件不存在，已初始化空列表", request_id)
    return AUTO_CMDS

def save_autocmd_core(AUTO_CMD_PATH: str, AUTO_CMDS: List[Dict[str, Any]], sys_type: str, log_info: Callable[[str, str], None], log_error: Callable[[str, str], None], request_id: str) -> None:
    """保存开机自启命令（提取原逻辑）"""
    try:
        with open(AUTO_CMD_PATH, "w", encoding="utf-8") as f:
            json.dump(AUTO_CMDS, f, ensure_ascii=False, indent=2)
        # Linux类系统设置文件权限（仅所有者可读写）
        if sys_type in ["Linux/macOS", "Termux", "SpecialLinux"]:
            os.chmod(AUTO_CMD_PATH, 0o600)
        log_info(f"保存开机自启命令：共{len(AUTO_CMDS)}条", request_id)
    except Exception as e:
        log_error(f"保存自启命令失败：{str(e)}", request_id)
        print(Fore.RED + f"保存开机自启命令失败：{str(e)}" + Style.RESET_ALL)

def handle_autocmd_core(
    cmd_parts: List[str],
    request_id: str,
    USER_HOME_DIR: str,
    sys_type: str,
    log_info: Callable[[str, str], None],
    log_error: Callable[[str, str], None],
    Fore: Any,
    Style: Any
) -> None:
    """autocmd命令核心逻辑（添加/删除/列出开机自启命令）"""
    # 初始化自启命令存储路径
    AUTO_CMD_PATH = os.path.join(USER_HOME_DIR, ".onyx_autocmd.json")
    # 加载自启命令
    AUTO_CMDS = load_autocmd_core(AUTO_CMD_PATH, log_info, log_error, request_id)
    # 读取语言配置
    current_lang = load_global_lang(USER_HOME_DIR)
    lang_msgs = {
        "chinese": {
            "usage": "用法：",
            "usage_add": "  autocmd add [command]    # 添加开机自启命令（例：autocmd add ls /user）",
            "usage_remove": "  autocmd remove [ID]      # 删除指定ID的自启命令（例：autocmd remove 1）",
            "usage_list": "  autocmd list             # 列出所有开机自启命令",
            "no_cmds": "无已配置的开机自启命令",
            "list_title": "开机自启命令列表：",
            "list_header": "{:<4} {:<20} {:<20}",
            "list_header_cols": ["ID", "命令", "添加时间"],
            "add_usage": "用法：autocmd add [command]（例：autocmd add ls /user）",
            "add_success": "已添加开机自启命令（ID：{}）：{}",
            "remove_usage": "用法：autocmd remove [ID]（例：autocmd remove 1，ID为数字）",
            "remove_notfound": "未找到ID为 {} 的开机自启命令",
            "remove_success": "已删除ID为 {} 的开机自启命令",
            "invalid_subcmd": "无效子命令！支持：add/remove/list"
        },
        "english": {
            "usage": "Usage：",
            "usage_add": "  autocmd add [command]    # Add startup command (e.g. autocmd add ls /user)",
            "usage_remove": "  autocmd remove [ID]      # Delete specified ID startup command (e.g. autocmd remove 1)",
            "usage_list": "  autocmd list             # List all startup commands",
            "no_cmds": "No configured startup commands",
            "list_title": "Startup Commands List：",
            "list_header": "{:<4} {:<20} {:<20}",
            "list_header_cols": ["ID", "Command", "Add Time"],
            "add_usage": "Usage：autocmd add [command] (e.g. autocmd add ls /user)",
            "add_success": "Added startup command (ID：{})：{}",
            "remove_usage": "Usage：autocmd remove [ID] (e.g. autocmd remove 1, ID is number)",
            "remove_notfound": "Startup command with ID {} not found",
            "remove_success": "Deleted startup command with ID {}",
            "invalid_subcmd": "Invalid subcommand! Supported: add/remove/list"
        }
    }
    msg = lang_msgs[current_lang]

    # 参数校验
    if len(cmd_parts) < 2:
        print(Fore.RED + msg["usage"] + Style.RESET_ALL)
        print(msg["usage_add"])
        print(msg["usage_remove"])
        print(msg["usage_list"])
        return

    # 处理 list
    if cmd_parts[1] == "list":
        if not AUTO_CMDS:
            print(Fore.YELLOW + msg["no_cmds"] + Style.RESET_ALL)
            return
        print(Fore.CYAN + msg["list_title"] + Style.RESET_ALL)
        header = msg["list_header"].format(*msg["list_header_cols"])
        print(header)
        print("-" * 50)
        for cmd in AUTO_CMDS:
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cmd["create_time"]))
            print(msg["list_header"].format(cmd["id"], cmd["cmd"], time_str))
        return

    # 处理 add
    if cmd_parts[1] == "add":
        if len(cmd_parts) < 3:
            print(Fore.RED + msg["add_usage"] + Style.RESET_ALL)
            return
        new_cmd = " ".join(cmd_parts[2:])
        max_id = max([cmd["id"] for cmd in AUTO_CMDS], default=0)
        new_cmd_info = {
            "id": max_id + 1,
            "cmd": new_cmd,
            "create_time": time.time()
        }
        AUTO_CMDS.append(new_cmd_info)
        save_autocmd_core(AUTO_CMD_PATH, AUTO_CMDS, sys_type, log_info, log_error, request_id)
        print(Fore.GREEN + msg["add_success"].format(new_cmd_info["id"], new_cmd) + Style.RESET_ALL)
        log_info(f"Added startup command: ID={new_cmd_info['id']}, cmd={new_cmd}", request_id)
        return

    # 处理 remove
    if cmd_parts[1] == "remove":
        if len(cmd_parts) != 3 or not cmd_parts[2].isdigit():
            print(Fore.RED + msg["remove_usage"] + Style.RESET_ALL)
            return
        target_id = int(cmd_parts[2])
        original_len = len(AUTO_CMDS)
        AUTO_CMDS = [cmd for cmd in AUTO_CMDS if cmd["id"] != target_id]
        if len(AUTO_CMDS) == original_len:
            print(Fore.RED + msg["remove_notfound"].format(target_id) + Style.RESET_ALL)
            return
        save_autocmd_core(AUTO_CMD_PATH, AUTO_CMDS, sys_type, log_info, log_error, request_id)
        print(Fore.GREEN + msg["remove_success"].format(target_id) + Style.RESET_ALL)
        log_info(f"Deleted startup command: ID={target_id}", request_id)
        return

    # 无效子命令
    print(Fore.RED + msg["invalid_subcmd"] + Style.RESET_ALL)
