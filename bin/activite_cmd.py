#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
activite 命令核心实现：切换模式、设置工具权限
"""
import os
import json
import time
from typing import List


def get_mode_cache_path(cache_dir: str = None) -> str:
    """获取模式缓存文件路径"""
    if cache_dir is None:
        # 默认路径：~/.cache/onyx/onyx/Mode.cache
        home = os.path.expanduser("~")
        cache_dir = os.path.join(home, ".cache", "onyx", "onyx")

    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "Mode.cache")


def save_mode_to_cache(mode: str, cache_dir: str = None) -> bool:
    """保存模式到缓存文件"""
    cache_path = get_mode_cache_path(cache_dir)
    try:
        data = {
            "mode": mode,
            "timestamp": time.time(),
            "version": 1
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 设置文件权限（仅所有者可读写）
        if os.name == "posix":
            os.chmod(cache_path, 0o600)
        return True
    except Exception:
        return False


def load_mode_from_cache(cache_dir: str = None) -> str:
    """从缓存文件加载模式，失败返回空字符串"""
    cache_path = get_mode_cache_path(cache_dir)
    if not os.path.exists(cache_path):
        return ""
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mode = data.get("mode", "").strip().lower()
        # 验证模式有效性
        if mode in ["low", "mid", "adv"]:
            return mode
        return ""
    except Exception:
        return ""


def clear_mode_cache(cache_dir: str = None) -> bool:
    """清除模式缓存文件"""
    cache_path = get_mode_cache_path(cache_dir)
    if os.path.exists(cache_path):
        try:
            os.remove(cache_path)
            return True
        except Exception:
            return False
    return True


def handle_activite_core(
    cmd_parts: List[str],
    request_id: str,
    user_mode,
    user_info,
    global_config: dict,
    ROOT_DIR: str,
    SANDBOX_CONFIG: dict,
    get_virtual_path,
    set_tool_permission,
    find_tool,
    verify_admin_password,
    ADMIN_PASSWORD_PATH: str,
    get_current_lang,
    log_info,
    log_error,
    Fore,
    Style
):
    lang_msgs = {
        "chinese": {
            "usage": "用法：",
            "usage_mode": "  切换模式：activite -m <模式>（low/mid/adv）",
            "usage_perm": "  设置工具权限：activite -t <工具名> <权限>（1-5，仅adv模式）",
            "usage_unset": "  清除缓存模式：activite --unset-cache",
            "invalid_mode": "无效模式，支持：{modes}",
            "adv_pass_deny": "密码错误，无法进入adv模式",
            "adv_pass_not_exist": "⚠️ ADV密码未初始化，无法进入adv模式\n  请让root用户（OS模式）或当前用户（TBS模式）先运行一次任意需要adv权限的命令来初始化密码",
            "mode_switch_ok": "模式切换成功！",
            "switch_dir_notice": "⚠️  当前目录为核心保护目录，已自动切换到用户主目录",
            "only_adv_perm": "仅adv模式可设置工具权限，请先通过activite -m adv切换",
            "invalid_perm_cmd": "用法错误！正确格式：activite -t <工具名> <权限>（例：activite -t nmap 5）",
            "perm_range_err": "权限需为1-5的整数（1最低，5最高）",
            "perm_not_num": "权限必须是数字（例：3、5），不可为文字或符号",
            "high_perm_confirm": "设置工具「{tool}」为{perm}级权限（高风险），确认执行？(y/n)：",
            "confirm_cancel": "操作已取消",
            "tool_not_found": "工具「{tool}」查找失败！可能原因：",
            "tool_not_found_reason": "  1. 工具名错误；2. 工具未添加到TOOL_MAIN_DIR目录；3. 索引未更新（执行refresh命令更新）",
            "perm_set_ok": "工具「{tool}」权限已更新为{perm}级，立即生效",
            "log_adv_pass_fail": "adv模式密码验证失败",
            "log_mode_switch": "用户模式切换：{mode}（权限上限{perm}）",
            "log_tool_perm_set": "工具「{tool}」权限设置完成：{perm}级",
            "protected_dir_deny": "❌ 禁止操作：当前目录「{dir}」是核心保护目录，不允许修改工具权限",
            "protected_dir_list": "✅ 核心保护目录列表：{}",
            "cache_updated": "✅ 持久化模式已保存: {}（下次启动自动生效）",
            "cache_removed": "✅ 持久化模式已清除（下次启动将使用默认模式）",
            "cache_not_exist": "⚠️ 持久化模式缓存不存在",
            "mid_confirm_prompt": "Switch to mid mode? (y/n): ",
        },
        "english": {
            "usage": "Usage:",
            "usage_mode": "  Switch mode: activite -m <mode> (low/mid/adv)",
            "usage_perm": "  Set tool permission: activite -t <tool_name> <perm> (1-5, adv only)",
            "usage_unset": "  Clear cached mode: activite --unset-cache",
            "invalid_mode": "Invalid mode, supported: {modes}",
            "adv_pass_deny": "Password incorrect, cannot enter adv mode",
            "adv_pass_not_exist": "⚠️ ADV password not initialized, cannot enter adv mode\n  Please let root user (OS mode) or current user (TBS mode) run any adv-required command first to initialize password",
            "mode_switch_ok": "Mode switched successfully!",
            "switch_dir_notice": "⚠️ Current directory is protected, auto switched to home",
            "only_adv_perm": "Only adv mode can set permissions, please switch via activite -m adv",
            "invalid_perm_cmd": "Usage: activite -t <tool_name> <perm> (e.g., activite -t nmap 5)",
            "perm_range_err": "Permission must be 1-5 (1=lowest, 5=highest)",
            "perm_not_num": "Permission must be a number (e.g., 3, 5), not text or symbols",
            "high_perm_confirm": "Set tool {tool} to level {perm} (high risk)? (y/n): ",
            "confirm_cancel": "Action cancelled",
            "tool_not_found": "Tool {tool} not found! Possible reasons:",
            "tool_not_found_reason": "  1. Wrong tool name; 2. Tool not in TOOL_MAIN_DIR; 3. Index outdated (run refresh)",
            "perm_set_ok": "Tool {tool} permission updated to level {perm}",
            "log_adv_pass_fail": "adv mode password verification failed",
            "log_mode_switch": "User mode switched to: {mode} (permission limit {perm})",
            "log_tool_perm_set": "Tool {tool} permission set to level {perm}",
            "protected_dir_deny": "❌ Cannot modify tool permissions in protected directory: {dir}",
            "protected_dir_list": "✅ Protected directories: {}",
            "cache_updated": "✅ Persistent mode saved: {} (will auto-apply on next startup)",
            "cache_removed": "✅ Persistent mode cleared (will use default mode on next startup)",
            "cache_not_exist": "⚠️ Persistent mode cache does not exist",
            "mid_confirm_prompt": "Switch to mid mode? (y/n): ",
        }
    }
    current_lang = get_current_lang()
    msg = lang_msgs[current_lang]

    def get_protected_dirs() -> List[str]:
        protected = [
            os.path.abspath(os.path.join(ROOT_DIR, "onyx")),
            os.path.abspath(os.path.join(ROOT_DIR, "etc", "pki")),
            os.path.abspath(os.path.join(ROOT_DIR, "onyxlog")),
            os.path.abspath(os.path.join(ROOT_DIR, "tools", "sys_tools"))
        ]
        if SANDBOX_CONFIG.get("extra_protected_dirs"):
            protected.extend([os.path.abspath(p) for p in SANDBOX_CONFIG["extra_protected_dirs"]])
        return list(set(protected))

    def is_in_protected_dir():
        protected_dirs = get_protected_dirs()
        current_dir = os.path.abspath(os.getcwd())
        in_protected = any(current_dir.startswith(p) for p in protected_dirs)
        virtual_dir = get_virtual_path(current_dir)
        protected_virtual = [get_virtual_path(p) for p in protected_dirs]
        return in_protected, virtual_dir, protected_virtual

    # 获取缓存目录（使用用户主目录下的缓存）
    cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "onyx")

    # 处理 --unset-cache 选项
    if len(cmd_parts) >= 2 and cmd_parts[1] == "--unset-cache":
        if clear_mode_cache(cache_dir):
            log_info("Persistent mode cache cleared", request_id)
        return

    if len(cmd_parts) < 3 or cmd_parts[1] not in ["-m", "-t"]:
        print(msg["usage"])
        print(msg["usage_mode"])
        print(msg["usage_perm"])
        print(msg["usage_unset"])
        return

    if cmd_parts[1] == "-m":
        target_mode = cmd_parts[2].lower()
        valid = ", ".join(global_config["mode_config"]["levels"])
        if target_mode not in global_config["mode_config"]["levels"]:
            print(Fore.RED + msg["invalid_mode"].format(modes=valid) + Style.RESET_ALL)
            return

        # 切换到 mid 模式确认
        if target_mode == "mid":
            # 避免在自动化流程中重复询问
            is_auto = os.environ.get("_ONYX_ENV_MODE_DONE") == "1"
            if not is_auto:
                from prompt_toolkit import prompt
                confirm = prompt(msg["mid_confirm_prompt"]).strip().lower()
                if confirm != "y":
                    print(Fore.BLUE + msg["confirm_cancel"] + Style.RESET_ALL)
                    return

        # 切换到 adv 模式需要密码
        if target_mode == "adv":
            if not os.path.exists(ADMIN_PASSWORD_PATH):
                print(Fore.RED + msg["adv_pass_not_exist"] + Style.RESET_ALL)
                log_error("ADV password file not found, cannot enter adv mode", request_id)
                return
            # 密码校验失败：清除持久化缓存
            if not verify_admin_password():
                print(Fore.RED + msg["adv_pass_deny"] + Style.RESET_ALL)
                log_error(msg["log_adv_pass_fail"], request_id)
                # ========== 新增逻辑：密码错误时清除持久化模式缓存 ==========
                clear_mode_cache(cache_dir)
                # ==========================================================
                return

        old_mode = user_mode.current_mode
        user_mode.current_mode = target_mode
        if target_mode != "adv":
            lim = global_config["mode_config"]["perm_limit"][target_mode]["max_tool_perm"]
            user_mode.current_tool_perm = lim

        # 保存模式到持久化缓存（供下次启动时使用）
        if save_mode_to_cache(target_mode, cache_dir):
            log_info(f"Persistent mode saved: {target_mode}", request_id)

        in_prot, virt_dir, prot_virt = is_in_protected_dir()
        if user_mode.current_mode != "adv" and in_prot:
            home_dir = os.path.join(ROOT_DIR, "home", user_info.get("name", "default"))
            if os.path.exists(home_dir):
                os.chdir(home_dir)
                os.environ["PWD"] = home_dir

        log_info(msg["log_mode_switch"].format(mode=target_mode, perm=user_mode.current_tool_perm), request_id)

    elif cmd_parts[1] == "-t":
        if user_mode.current_mode != "adv":
            print(Fore.RED + msg["only_adv_perm"] + Style.RESET_ALL)
            return
        if len(cmd_parts) < 4:
            print(Fore.RED + msg["invalid_perm_cmd"] + Style.RESET_ALL)
            return
        tname = cmd_parts[2]
        try:
            tperm = int(cmd_parts[3])
            if not 1 <= tperm <= 5:
                print(Fore.RED + msg["perm_range_err"] + Style.RESET_ALL)
                return
        except ValueError:
            print(Fore.RED + msg["perm_not_num"] + Style.RESET_ALL)
            return

        in_prot, virt_dir, prot_virt = is_in_protected_dir()
        if in_prot:
            print(Fore.RED + msg["protected_dir_deny"].format(dir=virt_dir) + Style.RESET_ALL)
            print(Fore.YELLOW + msg["protected_dir_list"].format(", ".join(prot_virt)) + Style.RESET_ALL)
            return

        if tperm >= 4:
            confirm = input(msg["high_perm_confirm"].format(tool=tname, perm=tperm)).strip().lower()
            if confirm != "y":
                print(Fore.GREEN + msg["confirm_cancel"] + Style.RESET_ALL)
                return

        tool = find_tool(tname, request_id)
        if not tool:
            print(Fore.RED + msg["tool_not_found"].format(tool=tname) + Style.RESET_ALL)
            print(msg["tool_not_found_reason"])
            return
        tdir = os.path.dirname(tool.path)
        if set_tool_permission(tdir, tperm, request_id):
            log_info(msg["log_tool_perm_set"].format(tool=tname, perm=tperm), request_id)
