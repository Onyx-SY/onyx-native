"""core/handlers/adv_pwd_handler.py — set-adv-pwd 命令处理（从 Onyx.py 提取）"""

import os
import shutil
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext


def handle_set_adv_pwd(ctx: "AppContext", cmd_parts: List[str], request_id: str) -> None:
    """修改高级模式管理员密码"""
    from core.log_manager import log_info, log_error
    from core.i18n import t, set_lang
    set_lang(ctx.global_config["display_info"]["language"]["current"])

    if len(cmd_parts) != 1:
        print(t("adv_pwd_handler.usage")); print(t("adv_pwd_handler.desc")); return
    if ctx.user_mode.current_mode != "adv":
        print(ctx.Fore.RED + t("adv_pwd_handler.only_adv_mode") + ctx.Style.RESET_ALL); return

    from getpass import getpass
    from Onyx import argon2id_verify, generate_salt, argon2id_hash
    current_pwd = getpass(t("adv_pwd_handler.input_current"))
    try:
        with open(ctx.ADMIN_PASSWORD_PATH, "r", encoding="utf-8") as f:
            stored_hash = f.read().strip()
        if not argon2id_verify(current_pwd, stored_hash):
            print(ctx.Fore.RED + t("adv_pwd_handler.pwd_incorrect") + ctx.Style.RESET_ALL)
            log_error("修改ADV密码失败：当前密码错误", request_id)
            return
    except Exception as e:
        print(ctx.Fore.RED + t("adv_pwd_handler.verify_fail", err=str(e)) + ctx.Style.RESET_ALL)
        log_error(f"修改ADV密码失败：{str(e)}", request_id)
        return

    print(ctx.Fore.YELLOW + t("adv_pwd_handler.prompt_new") + ctx.Style.RESET_ALL)
    while True:
        new1 = getpass(t("adv_pwd_handler.input_new1")); new2 = getpass(t("adv_pwd_handler.input_new2"))
        if new1 != new2:
            print(ctx.Fore.RED + t("adv_pwd_handler.pwd_not_match") + ctx.Style.RESET_ALL)
        elif len(new1) < ctx.MIN_PASSWORD_LEN:
            print(ctx.Fore.RED + t("adv_pwd_handler.pwd_too_short") + ctx.Style.RESET_ALL)
        else:
            break

    backup_path = f"{ctx.ADMIN_PASSWORD_PATH}.bak"
    try:
        if os.path.exists(ctx.ADMIN_PASSWORD_PATH):
            shutil.copy2(ctx.ADMIN_PASSWORD_PATH, backup_path)
            print(ctx.Fore.YELLOW + t("adv_pwd_handler.backup_old") + ctx.Style.RESET_ALL)
        new_salt = generate_salt()
        new_hashed = argon2id_hash(new1, new_salt)
        with open(ctx.ADMIN_PASSWORD_PATH, "w", encoding="utf-8") as f:
            f.write(new_hashed)
        if os.name == "posix":
            os.chmod(ctx.ADMIN_PASSWORD_PATH, ctx.FILE_PERMISSION)
        print(ctx.Fore.GREEN + t("adv_pwd_handler.set_ok") + ctx.Style.RESET_ALL)
        log_info("ADV模式管理员密码修改成功", request_id)
    except Exception as e:
        print(ctx.Fore.RED + t("adv_pwd_handler.set_fail", err=str(e)) + ctx.Style.RESET_ALL)
        log_error(f"修改ADV密码失败：{str(e)}", request_id)
        try:
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, ctx.ADMIN_PASSWORD_PATH)
                print(ctx.Fore.YELLOW + t("adv_pwd_handler.restore_old") + ctx.Style.RESET_ALL)
        except Exception:
            pass
