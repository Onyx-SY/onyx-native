"""core/config_loader.py — 配置加载：config.json / executable.json / cmdal.json"""

import os
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext


def load_config(ctx: "AppContext") -> bool:
    """加载所有核心配置文件，填充 AppContext"""
    from core.log_manager import log_info, log_error, log_warning, init_logger

    from core.i18n import set_lang
    set_lang(_get_language(ctx))

    # 1. config.json
    if not os.path.exists(ctx.CONFIG_FILE_PATH):
        print(ctx.Fore.RED + _t("config_not_found", path=ctx.CONFIG_FILE_PATH) + ctx.Style.RESET_ALL)
        return False
    try:
        with open(ctx.CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
            ctx.global_config = json.load(f)
    except json.JSONDecodeError as e:
        print(ctx.Fore.RED + _t("config_format_error", file="config.json", err=str(e), line=e.lineno, col=e.colno) + ctx.Style.RESET_ALL)
        return False
    except Exception as e:
        print(ctx.Fore.RED + _t("config_read_fail", file="config.json", err=str(e)) + ctx.Style.RESET_ALL)
        return False

    # 初始化日志（依赖 global_config）
    init_logger(ctx)
    log_info(_t("config_loaded_success", file="config.json"), ctx.user_info["session_id"])

    # 2. executable.json
    exec_path = os.path.join(ctx.ROOT_DIR, "onyx", "etc", "executable.json")
    if not os.path.exists(exec_path):
        print(ctx.Fore.RED + _t("config_not_found", path=exec_path) + ctx.Style.RESET_ALL)
        return False
    try:
        with open(exec_path, "r", encoding="utf-8") as f:
            ctx.executable_config = json.load(f)
        required = ["sys_suffix", "launch_cmd", "search_depth", "tool_search_rule"]
        missing = [n for n in required if n not in ctx.executable_config]
        if missing:
            print(ctx.Fore.RED + _t("missing_required_nodes", file="executable.json", nodes=", ".join(missing)) + ctx.Style.RESET_ALL)
            return False
    except json.JSONDecodeError as e:
        print(ctx.Fore.RED + _t("config_format_error", file="executable.json", err=str(e), line=e.lineno, col=e.colno) + ctx.Style.RESET_ALL)
        return False
    except Exception as e:
        print(ctx.Fore.RED + _t("config_read_fail", file="executable.json", err=str(e)) + ctx.Style.RESET_ALL)
        return False

    # 3. cmdal.json
    _DEFAULT_LOW_ALLOW = [
        "ls","cd","cat","echo","pwd","clear","history","exit","help",
        "ai","pip","python3","bash","sh","less","more","head","tail",
        "grep","find","sort","uniq","wc","diff","tee","mkdir","rm",
        "cp","mv","touch","chmod","chown","tar","gzip","gunzip",
        "unzip","zip","ssh","curl","wget","git","make","nano","vim",
        "sudo","date","whoami","id","uname","which","whereis",
        "ps","top","htop","df","du","free","uptime","ping",
        "ip","ss","netstat","systemctl","journalctl","service",
        "docker","kubectl","man","info","stat","file","time",
    ]
    _DEFAULT_PERM_LIMIT = {
        "low": {"max_tool_perm": 2, "allow_commands": _DEFAULT_LOW_ALLOW, "block_advanced": True},
        "mid": {"max_tool_perm": 3, "allow_commands": "*", "block_advanced": False},
        "adv": {"max_tool_perm": 5, "allow_commands": "*", "block_advanced": False},
    }

    cmdal_path = os.path.join(ctx.ROOT_DIR, "onyx", "etc", "cmdal.json")
    ctx.global_config.setdefault("mode_config", {})
    if not os.path.exists(cmdal_path):
        print(ctx.Fore.YELLOW + _t("config_not_found", path=cmdal_path) + ctx.Style.RESET_ALL)
        print(ctx.Fore.YELLOW + "⚠️ 使用默认权限配置（low 模式基础命令白名单）" + ctx.Style.RESET_ALL)
        ctx.global_config["mode_config"]["perm_limit"] = _DEFAULT_PERM_LIMIT
    else:
        try:
            with open(cmdal_path, "r", encoding="utf-8") as f:
                cmdal = json.load(f)
            if "perm_limit" not in cmdal:
                print(ctx.Fore.YELLOW + _t("missing_required_nodes", file="cmdal.json", nodes="perm_limit") + ctx.Style.RESET_ALL)
                print(ctx.Fore.YELLOW + "⚠️ 使用默认权限配置" + ctx.Style.RESET_ALL)
                ctx.global_config["mode_config"]["perm_limit"] = _DEFAULT_PERM_LIMIT
            else:
                ctx.global_config["mode_config"]["perm_limit"] = cmdal["perm_limit"]
        except json.JSONDecodeError as e:
            print(ctx.Fore.YELLOW + _t("config_format_error", file="cmdal.json", err=str(e), line=e.lineno, col=e.colno) + ctx.Style.RESET_ALL)
            print(ctx.Fore.YELLOW + "⚠️ cmdal.json 格式错误，使用默认权限配置" + ctx.Style.RESET_ALL)
            ctx.global_config["mode_config"]["perm_limit"] = _DEFAULT_PERM_LIMIT
        except Exception as e:
            print(ctx.Fore.YELLOW + _t("config_read_fail", file="cmdal.json", err=str(e)) + ctx.Style.RESET_ALL)
            print(ctx.Fore.YELLOW + "⚠️ cmdal.json 读取失败，使用默认权限配置" + ctx.Style.RESET_ALL)
            ctx.global_config["mode_config"]["perm_limit"] = _DEFAULT_PERM_LIMIT

    # Termux OS 模式适配
    if ctx.sys_type == "Termux" and ctx.OS_OR_TBS == "OS":
        if "Linux/macOS" in ctx.current_sys_cmds:
            ctx.current_sys_cmds["Termux"] = ctx.current_sys_cmds["Linux/macOS"].copy()

    # 4. 从 config 填充 AppContext 字段
    ctx.MAX_HISTORY_LEN = ctx.global_config["system_info"]["max_history_len"]
    ctx._TEMPLATES = ctx.global_config["display_info"]["command_prompts"]
    ctx.SANDBOX_CONFIG = ctx.global_config["security"]
    ctx.SUPPORTED_EXEC_SUFFIXES = ctx.executable_config["sys_suffix"]

    tool_main_name = ctx.global_config["program_info"]["tool_main_dir_name"]
    ctx.TOOL_MAIN_DIR = os.path.join(ctx.ROOT_DIR, tool_main_name)

    ctx.USER_CONFIG_PATH = os.path.join(ctx.USER_HOME_DIR, ".onyx_user_config.json")
    ctx.USER_HISTORY_PATH = os.path.join(ctx.USER_HOME_DIR, ".onyx_cmd_history")

    ctx.executor = ThreadPoolExecutor(max_workers=ctx.SANDBOX_CONFIG["max_process_count"])
    ctx.PYTHON_EXE = "python"

    # prompt 模板修复
    current_prompt = ctx.global_config["system_info"].get("current_prompt_type", "def")
    if current_prompt not in ctx._TEMPLATES:
        ctx.global_config["system_info"]["current_prompt_type"] = "def"
        with open(ctx.CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(ctx.global_config, f, ensure_ascii=False, indent=2)
        log_warning(_t("template_reset"), ctx.user_info["session_id"])

    # 安全日志初始化
    if "log_path" in ctx.SANDBOX_CONFIG:
        log_dir = os.path.dirname(ctx.SANDBOX_CONFIG["log_path"])
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, mode=0o700)
        if not os.path.exists(ctx.SANDBOX_CONFIG["log_path"]):
            with open(ctx.SANDBOX_CONFIG["log_path"], "w", encoding="utf-8") as f:
                f.write("")
            if ctx.sys_type != "Windows":
                os.chmod(ctx.SANDBOX_CONFIG["log_path"], 0o600)

    # 同步语言配置
    try:
        ctx.global_config["display_info"]["language"]["current"] = current_lang
    except Exception:
        ctx.global_config["display_info"]["language"]["current"] = "chinese"

    return True


def _get_language(ctx: "AppContext") -> str:
    """读取语言配置"""
    lang_path = os.path.join(ctx.USER_HOME_DIR, ".config", "onyx", "language")
    try:
        lang_dir = os.path.dirname(lang_path)
        if not os.path.exists(lang_dir):
            os.makedirs(lang_dir, mode=0o755 if ctx.sys_type != "Windows" else 0o777)
        if os.path.exists(lang_path):
            with open(lang_path, "r", encoding="utf-8") as f:
                lang = f.read().strip().lower()
                return lang if lang in ["chinese", "english"] else "chinese"
        else:
            with open(lang_path, "w", encoding="utf-8") as f:
                f.write("chinese")
            return "chinese"
    except Exception:
        return "chinese"


def _t(key: str, **kwargs) -> str:
    from core.i18n import t
    return t(f"config_loader.{key}", **kwargs)
