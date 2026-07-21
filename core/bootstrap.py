"""core/bootstrap.py — 引导初始化函数集合（从 Onyx.py 提取）"""

import os
import json
import uuid
import shutil
import time
import ctypes
import threading
from typing import Dict, List, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext


# ============================================================
# init_sado_config
# ============================================================

def init_sado_config(ctx: "AppContext", request_id: str) -> None:
    """初始化 sado 配置文件"""
    from core.log_manager import log_info, log_error, log_warning
    from core.i18n import t, set_lang
    set_lang(ctx.global_config.get("display_info", {}).get("language", {}).get("current", "chinese"))

    sado_path = ctx.SADO_CONFIG_PATH
    if not sado_path:
        sado_path = os.path.join(ctx.USER_HOME_DIR, ".config", "onyx", "sado.json")
        ctx.SADO_CONFIG_PATH = sado_path

    # OS 模式非 root 跳过
    if ctx.OS_OR_TBS == "OS" and not ctx.user_info.get("is_admin", False):
        log_info(t("bootstrap.init_sado_config.os_mode_non_root"), request_id)
        return

    if os.path.exists(sado_path):
        log_info(t("bootstrap.init_sado_config.config_exists", path=sado_path), request_id)
        try:
            with open(sado_path, "r", encoding="utf-8") as f:
                ctx.SADO_CONFIG = json.load(f)
            log_info(t("bootstrap.init_sado_config.config_load_success", count=len(ctx.SADO_CONFIG)), request_id)
        except Exception as e:
            log_error(t("bootstrap.init_sado_config.config_load_fail", err=str(e)), request_id)
        return

    # 创建默认 sado 配置
    default_config = [{
        "need_con": ["*"],
        "max_mode": "mid",
        "description": "默认规则：所有用户可在 mid 模式下执行所有命令"
    }]

    try:
        sado_dir = os.path.dirname(sado_path)
        if not os.path.exists(sado_dir):
            os.makedirs(sado_dir, mode=0o755)
        with open(sado_path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, ensure_ascii=False, indent=2)
        ctx.SADO_CONFIG = default_config
        log_info(t("bootstrap.init_sado_config.config_created", path=sado_path), request_id)
    except Exception as e:
        log_error(f"创建 sado 配置失败：{str(e)}", request_id)


# ============================================================
# init_admin_password
# ============================================================

def init_admin_password(ctx: "AppContext") -> bool:
    """初始化管理员密码"""
    from core.log_manager import log_info, log_error
    from core.i18n import t, set_lang
    set_lang(ctx.global_config.get("display_info", {}).get("language", {}).get("current", "chinese"))

    from Onyx import generate_salt, argon2id_hash

    root_dir = os.path.dirname(ctx.ADMIN_PASSWORD_PATH)
    if not os.path.exists(root_dir):
        try:
            os.makedirs(root_dir, mode=ctx.DIR_PERMISSION)
        except Exception as e:
            print(ctx.Fore.RED + t("bootstrap.init_admin_password.dir_create_fail", err=str(e)) + ctx.Style.RESET_ALL)
            return False

    if os.path.exists(ctx.ADMIN_PASSWORD_PATH):
        return True

    if ctx.OS_OR_TBS == "OS":
        if not ctx.user_info.get("is_admin", False):
            log_info(t("bootstrap.init_admin_password.os_mode_non_root"), str(uuid.uuid4()))
            return False
        print(ctx.Fore.YELLOW + t("bootstrap.init_admin_password.os_mode_root_creating") + ctx.Style.RESET_ALL)
    else:
        print(ctx.Fore.YELLOW + t("bootstrap.init_admin_password.tbs_mode_creating") + ctx.Style.RESET_ALL)

    print(ctx.Fore.YELLOW + t("bootstrap.init_admin_password.prompt_set") + ctx.Style.RESET_ALL)
    from getpass import getpass
    while True:
        pwd1 = getpass(t("bootstrap.init_admin_password.input_pwd1"))
        pwd2 = getpass(t("bootstrap.init_admin_password.input_pwd2"))
        if pwd1 != pwd2:
            print(ctx.Fore.RED + t("bootstrap.init_admin_password.pwd_not_match") + ctx.Style.RESET_ALL)
        elif len(pwd1) < ctx.MIN_PASSWORD_LEN:
            print(ctx.Fore.RED + t("bootstrap.init_admin_password.pwd_too_short") + ctx.Style.RESET_ALL)
        else:
            break

    try:
        salt = generate_salt()
        hashed = argon2id_hash(pwd1, salt)
        with open(ctx.ADMIN_PASSWORD_PATH, "w", encoding="utf-8") as f:
            f.write(hashed)
        if os.name == "posix":
            os.chmod(ctx.ADMIN_PASSWORD_PATH, ctx.FILE_PERMISSION)
        log_info("ADV管理员密码初始化成功", str(uuid.uuid4()))
        return True
    except Exception as e:
        print(ctx.Fore.RED + t("bootstrap.init_admin_password.pwd_save_fail", err=str(e)) + ctx.Style.RESET_ALL)
        return False


# ============================================================
# verify_admin_password
# ============================================================

def verify_admin_password(ctx: "AppContext") -> bool:
    """验证管理员密码"""
    from getpass import getpass
    from Onyx import argon2id_verify
    current_lang = ctx.global_config.get("display_info", {}).get("language", {}).get("current", "chinese")
    prompts = {
        "chinese": {"input": "请输入管理员密码：", "incorrect": "密码错误！", "verify_fail": "密码验证失败：{}"},
        "english": {"input": "Please enter administrator password：", "incorrect": "Password incorrect！", "verify_fail": "Password verification failed：{}"}
    }
    p = prompts.get(current_lang, prompts["chinese"])
    try:
        pwd = getpass(p["input"])
        with open(ctx.ADMIN_PASSWORD_PATH, "r", encoding="utf-8") as f:
            stored = f.read().strip()
        if argon2id_verify(pwd, stored):
            return True
        print(ctx.Fore.RED + p["incorrect"] + ctx.Style.RESET_ALL)
        return False
    except Exception as e:
        print(ctx.Fore.RED + p["verify_fail"].format(str(e)) + ctx.Style.RESET_ALL)
        return False


# ============================================================
# init_tool_dirs
# ============================================================

def init_tool_dirs(ctx: "AppContext") -> bool:
    """创建工具目录结构"""
    from core.log_manager import log_info, log_error
    current_lang = ctx.global_config.get("display_info", {}).get("language", {}).get("current", "chinese")
    try:
        if not os.path.exists(ctx.TOOL_MAIN_DIR):
            os.makedirs(ctx.TOOL_MAIN_DIR, mode=0o755 if ctx.sys_type != "Windows" else 0o777)
        for cat in ctx.global_config.get("tool_category_dirs", []):
            cat_path = os.path.join(ctx.TOOL_MAIN_DIR, cat)
            if not os.path.exists(cat_path):
                os.makedirs(cat_path, mode=0o755 if ctx.sys_type != "Windows" else 0o777)
        return True
    except Exception as e:
        log_error(f"工具目录创建失败：{str(e)}", ctx.user_info.get("session_id", str(uuid.uuid4())))
        return False


# ============================================================
# load_user_config
# ============================================================

def load_user_config(ctx: "AppContext") -> None:
    """加载用户配置"""
    from core.log_manager import log_info
    if os.path.exists(ctx.USER_CONFIG_PATH):
        try:
            with open(ctx.USER_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            ctx.ALIAS_CACHE = config.get("aliases", {})
            ctx.MAX_HISTORY_LEN = config.get("max_history_len", ctx.MAX_HISTORY_LEN)
            ctx.global_config["system_info"]["max_history_len"] = ctx.MAX_HISTORY_LEN
            if "current_prompt_type" in config:
                ctx.global_config["system_info"]["current_prompt_type"] = config["current_prompt_type"]
                ctx._CACHED_PROMPT_CONF = None
            log_info(f"用户配置加载成功：{ctx.USER_CONFIG_PATH}", str(uuid.uuid4()))
        except Exception:
            pass


# ============================================================
# init_user_home
# ============================================================

def init_user_home(ctx: "AppContext") -> bool:
    """初始化用户主目录"""
    from core.log_manager import log_info, log_error
    if ctx.OS_OR_TBS == "OS":
        ctx.USER_HOME_DIR = os.path.expanduser("~")
    else:
        ctx.USER_HOME_DIR = os.path.join(ctx.ROOT_DIR, "home", ctx.user_info.get("name", "default"))

    try:
        if not os.path.exists(ctx.USER_HOME_DIR):
            os.makedirs(ctx.USER_HOME_DIR, mode=0o755)
        ctx.CURRENT_VIRTUAL_PATH = "~"
        # 设置缓存路径
        cache_base = os.path.join(ctx.USER_HOME_DIR, ".cache", "onyx", "onyx")
        if not os.path.exists(cache_base):
            os.makedirs(cache_base, mode=0o755)
        ctx.CACHE_DIR = cache_base
        ctx.TOOL_INDEX_MSG_PATH = os.path.join(cache_base, "tool_index.msgpack")
        ctx.PATH_INDEX_MSG_PATH = os.path.join(cache_base, "path_index.msgpack")
        ctx.CMD_MAPPING_MSG_PATH = os.path.join(cache_base, "cmd_mapping.msgpack")
        ctx.DIR_CACHE_MSG_PATH = os.path.join(cache_base, "dir_cache.msgpack")
        ctx.SADO_CONFIG_PATH = os.path.join(ctx.USER_HOME_DIR, ".config", "onyx", "sado.json")
        ctx.USER_CONFIG_PATH = os.path.join(ctx.USER_HOME_DIR, ".onyx_user_config.json")
        ctx.USER_HISTORY_PATH = os.path.join(ctx.USER_HOME_DIR, ".onyx_cmd_history")
        log_info(f"用户主目录初始化成功：{ctx.USER_HOME_DIR}", ctx.user_info.get("session_id", ""))
        return True
    except Exception as e:
        log_error(f"用户主目录初始化失败：{str(e)}", ctx.user_info.get("session_id", ""))
        return False
