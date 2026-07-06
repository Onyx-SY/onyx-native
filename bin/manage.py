import sys
import os
import json
import shutil
import time
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any
from lib.terminal.colors import Fore, Style

# Windows 环境适配配置
WINDOWS = os.name == "nt"
DEFAULT_DIR_MODE = 0o777
DEFAULT_FILE_MODE = 0o666

# 静默模式标志 - 默认关闭
SILENT_MODE = False

def init_color_safe() -> None:
    # colorama 已替换为 lib.terminal.colors（纯 ANSI），无需 fallback
    pass

init_color_safe()

# 路径配置（跨平台兼容）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
USER = os.getlogin()
USER_HOME_DIR = os.path.join(ROOT_DIR, "root") if USER == "root" else os.path.join(ROOT_DIR, "home", USER)
CONFIG_DIR = os.path.join(USER_HOME_DIR, ".config", "onyx")
CONFIG_JSON_PATH = os.path.join(ROOT_DIR, "onyx", "etc", "config.json")
LOG_DIR = os.path.join(ROOT_DIR, "onyxlog", "onyx")
CACHE_DIR = os.path.join(USER_HOME_DIR,  ".cache", "onyx", "onyx")

# 缓存文件路径
PATH_CACHE_PATH = os.path.join(CACHE_DIR, "path_cache.json")
CMD_MAP_CACHE_PATH = os.path.join(CACHE_DIR, "cmd_map_cache.json")
DIR_CACHE_PATH = os.path.join(CACHE_DIR, "dir_cache.json")
TOOL_INDEX_CACHE_PATH = os.path.join(CACHE_DIR, "tool_index_cache.json")

# 配置文件路径
DEBUG_TIMES_PATH = os.path.join(CONFIG_DIR, "debug-times")
DEBUG_PARSECMD_PATH = os.path.join(CONFIG_DIR, "debug-parsecmd")
LANGUAGE_PATH = os.path.join(CONFIG_DIR, "language")
CLEAN_LOG_TIME_PATH = os.path.join(CONFIG_DIR, "clean-log-time")
ADV_DANGER_CMD_PROMPT_PATH = os.path.join(CONFIG_DIR, "adv_danger_cmd_prompt")
MCP_ENABLED_PATH = os.path.join(CONFIG_DIR, "mcp_enabled")
MOOD_ENABLED_PATH = os.path.join(CONFIG_DIR, "mood_enabled")
SPRING_MODE_PATH = os.path.join(CONFIG_DIR, "spring_mode")

# ========== 沙箱配置文件路径 ==========
SANDBOX_CONFIG_PATH = os.path.join(ROOT_DIR, "etc", "onyx", "sandbox")

# 默认配置
DEFAULT_CONFIGS = {
    DEBUG_TIMES_PATH: "false",
    DEBUG_PARSECMD_PATH: "false",
    LANGUAGE_PATH: "english",
    CLEAN_LOG_TIME_PATH: "3",
    ADV_DANGER_CMD_PROMPT_PATH: "true",
    MCP_ENABLED_PATH: "true",
    MOOD_ENABLED_PATH: "true",
    SPRING_MODE_PATH: "true",
}

# 默认 config.json 模板
DEFAULT_CONFIG_JSON = {
    "display_info": {
        "language": {
            "default": "Chinese"
        }
    }
}

def print_silent(content: str, color: str = "") -> None:
    if not SILENT_MODE:
        if color:
            print(color + content + Style.RESET_ALL)
        else:
            print(content)

def get_current_language() -> str:
    return read_config_file(LANGUAGE_PATH, "chinese").lower()

def _get_msg(msg_cn: str, msg_en: str) -> str:
    return msg_cn if get_current_language() == "chinese" else msg_en

def init_config_dir() -> None:
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR, mode=DEFAULT_DIR_MODE)
        print_silent(Fore.GREEN + _get_msg(f"已创建配置目录：{CONFIG_DIR}", f"Config directory created: {CONFIG_DIR}"))

    if not os.path.exists(CONFIG_JSON_PATH):
        try:
            with open(CONFIG_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG_JSON, f, ensure_ascii=False, indent=2)
            print_silent(Fore.GREEN + _get_msg(f"已创建默认 config.json", f"Default config.json created"))
        except Exception as e:
            print_silent(Fore.RED + _get_msg(f"创建 config.json 失败：{str(e)}", f"Failed to create config.json: {str(e)}"))
            sys.exit(1)

def init_default_configs() -> None:
    init_config_dir()
    created_count = 0
    for config_path, default_value in DEFAULT_CONFIGS.items():
        if not os.path.exists(config_path):
            try:
                with open(config_path, "w", encoding="utf-8") as f:
                    f.write(default_value)
                created_count += 1
            except Exception as e:
                print_silent(Fore.YELLOW + _get_msg(f"创建默认配置失败：{os.path.basename(config_path)} → {str(e)}", f"Failed to create default config: {os.path.basename(config_path)} → {str(e)}"))
    if created_count > 0 and not SILENT_MODE:
        print_silent(Fore.GREEN + _get_msg(f"自动创建默认配置：{created_count} 个文件", f"Auto created default configs: {created_count} files"))

def init_storage_dirs() -> None:
    for dir_path in [LOG_DIR, CACHE_DIR]:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, mode=DEFAULT_DIR_MODE)
            print_silent(Fore.GREEN + _get_msg(f"已创建目录：{dir_path}", f"Directory created: {dir_path}"))

def read_config_file(path: str, default: Any = None) -> Any:
    init_config_dir()
    if not os.path.exists(path):
        default_val = DEFAULT_CONFIGS.get(path, default)
        log_info(_get_msg(f"配置文件不存在，使用默认值：{os.path.basename(path)} = {default_val}", f"Config file not found, using default: {os.path.basename(path)} = {default_val}"), str(uuid.uuid4()))
        return default_val
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip().lower()
            if content in ["true", "false"]:
                return content == "true"
            if content.isdigit():
                return int(content)
            return content
    except Exception:
        return default

def write_config_file(path: str, value: Any) -> bool:
    init_config_dir()
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(value).lower())
        return True
    except Exception:
        return False

def sync_language_to_configjson() -> None:
    lang = read_config_file(LANGUAGE_PATH, "chinese")
    lang = "Chinese" if lang.lower() == "chinese" else "English"
    try:
        with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        with open(CONFIG_JSON_PATH, "w", encoding="utf-8") as f:
            config["display_info"]["language"]["default"] = lang
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def log_info(content: str, request_id: str) -> None:
    try:
        from bin.onyx import log_base, LOG_LEVELS, CURRENT_LOG_LEVEL
        log_base("INFO", content, request_id)
    except ImportError:
        if not SILENT_MODE:
            print_silent(Fore.GREEN + f"[INFO] {content} (Request ID: {request_id})")

def auto_clean_expired_logs() -> None:
    clean_days = read_config_file(CLEAN_LOG_TIME_PATH, 3)
    if not clean_days or not isinstance(clean_days, int) or clean_days <= 0:
        clean_days = 3
    init_storage_dirs()

    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, mode=DEFAULT_DIR_MODE)
        log_info(_get_msg(f"创建日志目录：{LOG_DIR}", f"Log directory created: {LOG_DIR}"), str(uuid.uuid4()))
        return

    expire_time = datetime.now() - timedelta(days=clean_days)
    deleted_count = 0
    for filename in os.listdir(LOG_DIR):
        file_path = os.path.join(LOG_DIR, filename)
        if os.path.isfile(file_path):
            try:
                file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                if file_mtime < expire_time:
                    os.remove(file_path)
                    deleted_count += 1
            except PermissionError:
                print_silent(Fore.YELLOW + _get_msg(f"无权限删除过期日志：{filename}", f"No permission to delete expired log: {filename}"))
            except Exception:
                pass

# ========== 沙箱配置相关函数 ==========
def get_sandbox_status() -> bool:
    """获取当前沙箱状态"""
    if not os.path.exists(SANDBOX_CONFIG_PATH):
        return True  # 默认启用
    try:
        with open(SANDBOX_CONFIG_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip().lower()
            return content == "true"
    except Exception:
        return True

def set_sandbox_status(enabled: bool, request_id: str) -> bool:
    """设置沙箱状态（需要验证 ADV 密码）"""
    # 导入验证密码所需的函数和路径
    try:
        from Onyx import verify_admin_password, ADMIN_PASSWORD_PATH
    except ImportError:
        # 如果无法导入，尝试从 bin.onyx 导入
        try:
            from bin.onyx import verify_admin_password, ADMIN_PASSWORD_PATH
        except ImportError:
            print_silent(Fore.RED + _get_msg(
                "错误：无法导入密码验证模块",
                "Error: Cannot import password verification module"
            ))
            return False
    
    # 检查密码文件是否存在
    if not os.path.exists(ADMIN_PASSWORD_PATH):
        print_silent(Fore.RED + _get_msg(
            "错误：管理员密码未初始化，请先以 ADV 模式运行一次来设置密码",
            "Error: Admin password not initialized, please run in ADV mode once to set password"
        ))
        return False
    
    # 验证 ADV 密码
    print_silent(Fore.YELLOW + _get_msg(
        "需要验证 ADV 密码以修改沙箱配置",
        "ADV password required to modify sandbox configuration"
    ), Fore.YELLOW)
    
    if not verify_admin_password():
        print_silent(Fore.RED + _get_msg(
            "密码验证失败，无法修改沙箱配置",
            "Password verification failed, cannot modify sandbox configuration"
        ))
        return False
    
    # 确保配置目录存在
    config_dir = os.path.dirname(SANDBOX_CONFIG_PATH)
    if not os.path.exists(config_dir):
        os.makedirs(config_dir, mode=0o755)
    
    try:
        with open(SANDBOX_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("true" if enabled else "false")
        if os.name == "posix":
            os.chmod(SANDBOX_CONFIG_PATH, 0o644)
        log_info(f"沙箱配置已修改：enabled={enabled}", request_id)
        print_silent(Fore.GREEN + _get_msg(
            f"✓ 沙箱已{('启用' if enabled else '禁用')}（需要重启 Onyx 生效）",
            f"✓ Sandbox {('enabled' if enabled else 'disabled')} (requires Onyx restart to take effect)"
        ))
        return True
    except Exception as e:
        log_info(f"沙箱配置修改失败：{str(e)}", request_id)
        print_silent(Fore.RED + _get_msg(
            f"沙箱配置修改失败：{str(e)}",
            f"Failed to modify sandbox configuration: {str(e)}"
        ))
        return False

def handle_sandbox_option(options: List[str], request_id: str) -> None:
    """处理 sandbox 子命令"""
    if len(options) < 1:
        # 显示当前状态
        current_status = get_sandbox_status()
        status_text = _get_msg("启用", "enabled") if current_status else _get_msg("禁用", "disabled")
        status_color = Fore.GREEN if current_status else Fore.RED
        print_silent(status_color + _get_msg(
            f"当前沙箱状态：{status_text}",
            f"Current sandbox status: {status_text}"
        ))
        
        # 提示如何修改
        print_silent(Fore.CYAN + _get_msg(
            "修改方式：manage set sandbox true/false（需要验证 ADV 密码）",
            "To modify: manage set sandbox true/false (requires ADV password verification)"
        ))
        return
    
    action = options[0].lower()
    
    if action == "true":
        set_sandbox_status(True, request_id)
    elif action == "false":
        set_sandbox_status(False, request_id)
    else:
        print_silent(Fore.RED + _get_msg(
            f"未知参数：{action}，支持 true/false",
            f"Unknown argument: {action}, supported: true/false"
        ))

def handle_set_option(options: List[str], request_id: str) -> None:
    if len(options) < 1:
        print_silent(Fore.RED + _get_msg(
            "用法：manage set <选项> <值>\n支持选项：debug-times/debug-parsecmd/language/clean-log-time/adv_danger_cmd_prompt/sandbox/mcp/spring-mode",
            "Usage: manage set <option> <value>\nSupported options: debug-times/debug-parsecmd/language/clean-log-time/adv_danger_cmd_prompt/sandbox/mcp/spring-mode"
        ))
        return

    opt_name = options[0]
    
    # sandbox 选项处理
    if opt_name == "sandbox":
        if len(options) < 2:
            print_silent(Fore.RED + _get_msg(
                "用法：manage set sandbox true/false",
                "Usage: manage set sandbox true/false"
            ))
            return
        handle_sandbox_option([options[1]], request_id)
        return
    
    if len(options) < 2:
        print_silent(Fore.RED + _get_msg(
            "用法：manage set <选项> <值>",
            "Usage: manage set <option> <value>"
        ))
        return
    
    opt_value = options[1]
    success = False

    if opt_name == "debug-times":
        if opt_value not in ["true", "false"]:
            print_silent(Fore.RED + _get_msg("值必须为 true/false", "Value must be true/false"))
            return
        success = write_config_file(DEBUG_TIMES_PATH, opt_value)

    elif opt_name == "debug-parsecmd":
        if opt_value not in ["true", "false"]:
            print_silent(Fore.RED + _get_msg("值必须为 true/false", "Value must be true/false"))
            return
        success = write_config_file(DEBUG_PARSECMD_PATH, opt_value)

    elif opt_name == "language":
        if opt_value.lower() not in ["chinese", "english"]:
            print_silent(Fore.RED + _get_msg("值必须为 Chinese/English", "Value must be Chinese/English"))
            return
        success = write_config_file(LANGUAGE_PATH, opt_value.lower())
        sync_language_to_configjson()

    elif opt_name == "clean-log-time":
        if opt_value not in ["false"] and not opt_value.isdigit():
            print_silent(Fore.RED + _get_msg("值必须为 false 或正整数（单位：天）", "Value must be false or positive integer (unit: days)"))
            return
        success = write_config_file(CLEAN_LOG_TIME_PATH, opt_value)

    elif opt_name == "adv_danger_cmd_prompt":
        if opt_value not in ["true", "false"]:
            print_silent(Fore.RED + _get_msg("值必须为 true/false", "Value must be true/false"))
            return
        success = write_config_file(ADV_DANGER_CMD_PROMPT_PATH, opt_value)

    elif opt_name == "mcp":
        if opt_value not in ["true", "false"]:
            print_silent(Fore.RED + _get_msg("值必须为 true/false", "Value must be true/false"))
            return
        success = write_config_file(MCP_ENABLED_PATH, opt_value)

    elif opt_name == "mood":
        if opt_value not in ["true", "false"]:
            print_silent(Fore.RED + _get_msg("值必须为 true/false", "Value must be true/false"))
            return
        success = write_config_file(MOOD_ENABLED_PATH, opt_value)

    elif opt_name == "spring-mode":
        if opt_value not in ["true", "false"]:
            print_silent(Fore.RED + _get_msg("值必须为 true/false", "Value must be true/false"))
            return
        success = write_config_file(SPRING_MODE_PATH, opt_value)

    else:
        print_silent(Fore.RED + _get_msg(f"未知选项：{opt_name}", f"Unknown option: {opt_name}"))
        return

    if success:
        print_silent(Fore.GREEN + _get_msg(f"设置成功：{opt_name} = {opt_value}", f"Set successfully: {opt_name} = {opt_value}"))
    else:
        print_silent(Fore.RED + _get_msg(f"设置失败：{opt_name}", f"Set failed: {opt_name}"))

def handle_clean_option(options: List[str], request_id: str) -> None:
    if len(options) < 1:
        print_silent(Fore.RED + _get_msg(
            "用法：manage clean <选项>\n支持选项：cache[path/cmdmap/dircache/toolindex/all]、log[all/other]",
            "Usage: manage clean <option>\nSupported options: cache[path/cmdmap/dircache/toolindex/all], log[all/other]"
        ))
        return

    opt_type = options[0]
    init_storage_dirs()

    if opt_type == "cache":
        if len(options) < 2:
            print_silent(Fore.RED + _get_msg(
                "缓存清理子选项：path/cmdmap/dircache/toolindex/all",
                "Cache clean sub-options: path/cmdmap/dircache/toolindex/all"
            ))
            return
        cache_type = options[1]
        deleted = []

        try:
            if cache_type in ["path", "all"] and os.path.exists(PATH_CACHE_PATH):
                os.remove(PATH_CACHE_PATH)
                deleted.append(_get_msg("path缓存", "path cache"))
            if cache_type in ["cmdmap", "all"] and os.path.exists(CMD_MAP_CACHE_PATH):
                os.remove(CMD_MAP_CACHE_PATH)
                deleted.append(_get_msg("命令映射缓存", "command map cache"))
            if cache_type in ["dircache", "all"] and os.path.exists(DIR_CACHE_PATH):
                os.remove(DIR_CACHE_PATH)
                deleted.append(_get_msg("目录缓存", "directory cache"))
            if cache_type in ["toolindex", "all"] and os.path.exists(TOOL_INDEX_CACHE_PATH):
                os.remove(TOOL_INDEX_CACHE_PATH)
                deleted.append(_get_msg("工具索引缓存", "tool index cache"))
        except Exception:
            print_silent(Fore.RED + _get_msg("清理缓存失败", "Failed to clean cache"))
            return

        if deleted:
            print_silent(Fore.GREEN + _get_msg(f"清理成功：{', '.join(deleted)}", f"Cleaned successfully: {', '.join(deleted)}"))
        else:
            print_silent(Fore.YELLOW + _get_msg("无缓存可清理", "No cache to clean"))

    elif opt_type == "log":
        if len(options) < 2:
            print_silent(Fore.RED + _get_msg(
                "日志清理子选项：all(按配置天数)/other(仅允许的非onyx日志)",
                "Log clean sub-options: all(by config days)/other(allowed non-onyx logs only)"
            ))
            return
        log_type = options[1]
        deleted_count = 0

        try:
            if log_type == "all":
                clean_days = read_config_file(CLEAN_LOG_TIME_PATH, 3)
                if not isinstance(clean_days, int) or clean_days <= 0:
                    clean_days = 3
                expire_time = datetime.now() - timedelta(days=clean_days)
                for filename in os.listdir(LOG_DIR):
                    file_path = os.path.join(LOG_DIR, filename)
                    if os.path.isfile(file_path):
                        try:
                            file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                            if file_mtime < expire_time:
                                os.remove(file_path)
                                deleted_count += 1
                        except PermissionError:
                            pass
            elif log_type == "other":
                onyxlog_root = os.path.dirname(LOG_DIR)
                allowed_other_dirs = ["tool_logs", "plugin_logs", "temp_logs", "third_party"]
                for dirname in os.listdir(onyxlog_root):
                    dir_path = os.path.join(onyxlog_root, dirname)
                    if os.path.isdir(dir_path) and dirname != "onyx" and dirname in allowed_other_dirs:
                        shutil.rmtree(dir_path, ignore_errors=True)
                        deleted_count += 1
            else:
                print_silent(Fore.RED + _get_msg("日志子选项仅支持 all/other", "Log sub-options only support all/other"))
                return
        except Exception:
            print_silent(Fore.RED + _get_msg("清理日志失败", "Failed to clean logs"))
            return

        print_silent(Fore.GREEN + _get_msg(f"日志清理成功：{deleted_count} 个文件/目录", f"Logs cleaned successfully: {deleted_count} files/directories"))
    else:
        print_silent(Fore.RED + _get_msg(f"未知清理选项：{opt_type}", f"Unknown clean option: {opt_type}"))

def handle_manage(cmd_parts: List[str], request_id: str) -> None:
    global SILENT_MODE
    original_silent_mode = SILENT_MODE
    SILENT_MODE = False

    filtered_cmd_parts = []
    i = 1 if len(cmd_parts) > 0 and cmd_parts[0] == "manage" else 0
    while i < len(cmd_parts):
        part = cmd_parts[i]
        if part in ["-q", "--quiet"]:
            SILENT_MODE = True
        else:
            filtered_cmd_parts.append(part)
        i += 1

    debug_parsecmd = read_config_file(DEBUG_PARSECMD_PATH, False)
    if debug_parsecmd and not SILENT_MODE:
        print(Fore.CYAN + f"[DEBUG] 原始命令部分: {cmd_parts}")
        print(Fore.CYAN + f"[DEBUG] 过滤后命令部分: {filtered_cmd_parts}")
        print(Fore.CYAN + f"[DEBUG] 静默模式: {SILENT_MODE}")

    init_default_configs()
    init_storage_dirs()

    if not filtered_cmd_parts:
        print_silent(Fore.RED + _get_msg(
            "用法：manage [-q] [set/clean] [子选项] [值/子选项]\n输入 help manage 查看详细说明",
            "Usage: manage [-q] [set/clean] [sub-option] [value/sub-option]\nType help manage for detailed instructions"
        ))
        SILENT_MODE = original_silent_mode
        return

    main_opt = filtered_cmd_parts[0]
    sub_opt = filtered_cmd_parts[1:]

    if main_opt == "set":
        handle_set_option(sub_opt, request_id)
    elif main_opt == "clean":
        handle_clean_option(sub_opt, request_id)
    else:
        print_silent(Fore.RED + _get_msg(f"未知主选项：{main_opt}，支持 set/clean", f"Unknown main option: {main_opt}, supports set/clean"))

    SILENT_MODE = original_silent_mode

def main(cmd_parts: List[str], request_id: str) -> None:
    handle_manage(cmd_parts, request_id)