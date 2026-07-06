# bin/nanosado_cmd.py
"""
nanosado 命令核心实现：编辑 sado 配置文件
支持指定编辑器：nanosado vim, nanosado nano, nanosado code 等
支持命令行管理：nanosado add, nanosado remove, nanosado list 等
"""
import os
import json
import shutil
import subprocess

def _get_available_editors(sys_type: str) -> dict:
    """获取可用编辑器列表（按系统类型分类）"""
    all_editors = {
        "vim": ["vim", "vim.exe"],
        "nano": ["nano", "nano.exe"],
        "code": ["code", "code.cmd", "code.exe"],
        "gedit": ["gedit", "gedit.exe"],
        "vi": ["vi", "vi.exe"],
        "notepad": ["notepad.exe"],
        "notepad++": ["notepad++.exe"],
        "subl": ["subl", "sublime_text.exe"],
        "emacs": ["emacs", "emacs.exe"],
        "ne": ["ne", "ne.exe"],
        "micro": ["micro", "micro.exe"],
        "helix": ["hx", "helix.exe"],
        "leafpad": ["leafpad"],
        "mousepad": ["mousepad"],
        "kate": ["kate"],
        "pluma": ["pluma"]
    }
    
    # 按系统类型排序的编辑器优先级
    if sys_type.startswith("win32"):
        priority = ["notepad++", "notepad", "code", "subl", "vim", "nano", "micro", "emacs"]
    else:
        priority = ["vim", "nano", "code", "gedit", "vi", "micro", "helix", "emacs", "ne", "leafpad", "mousepad", "kate", "pluma"]
    
    return {"all": all_editors, "priority": priority}


def _find_editor(editor_name: str, sys_type: str) -> str:
    """查找编辑器命令"""
    editors_info = _get_available_editors(sys_type)
    all_editors = editors_info["all"]
    
    if editor_name in all_editors:
        for cmd_name in all_editors[editor_name]:
            if shutil.which(cmd_name):
                return cmd_name
    else:
        # 用户指定的编辑器不在预设列表中，直接检查命令是否存在
        if shutil.which(editor_name):
            return editor_name
        # 尝试添加常见后缀
        for suffix in ["", ".exe", ".cmd", ".bat"]:
            test_cmd = editor_name + suffix
            if shutil.which(test_cmd):
                return test_cmd
    return None


def _get_default_editor(sys_type: str) -> str:
    """获取默认编辑器"""
    editors_info = _get_available_editors(sys_type)
    all_editors = editors_info["all"]
    priority = editors_info["priority"]
    
    for editor_name in priority:
        for cmd_name in all_editors.get(editor_name, []):
            if shutil.which(cmd_name):
                return cmd_name
    
    return None


def _list_rules(lang_msgs: dict, SADO_CONFIG: list, current_lang: str):
    """列出所有规则"""
    if not SADO_CONFIG:
        print("暂无规则")
        return
    
    for idx, rule in enumerate(SADO_CONFIG):
        user = rule.get("user", "*")
        group = rule.get("group", "")
        max_mode = rule.get("max_mode", "mid")
        need_con = rule.get("need_con", [])
        no_con = rule.get("no_con", [])
        
        print(f"[{idx}] user={user}, group={group}, max_mode={max_mode}")
        if need_con:
            print(f"    need_con: {need_con}")
        if no_con:
            print(f"    no_con: {no_con}")


def _add_rule(SADO_CONFIG: list, args: list, lang_msgs: dict, current_lang: str) -> bool:
    """添加新规则"""
    # 解析参数：-u user -g group -m max_mode -n need_con -x no_con
    import argparse
    
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("-u", "--user", default="*")
        parser.add_argument("-g", "--group", default="*")
        parser.add_argument("-m", "--max-mode", choices=["low", "mid", "adv"], default="mid")
        parser.add_argument("-n", "--need-con", action="append", default=[])
        parser.add_argument("-x", "--no-con", action="append", default=[])
        
        parsed_args, unknown = parser.parse_known_args(args)
        
        # 合并 need_con 和 no_con（因为 action="append" 会产生列表的列表）
        need_con = []
        for item in parsed_args.need_con:
            if item:
                need_con.append(item)
        no_con = []
        for item in parsed_args.no_con:
            if item:
                no_con.append(item)
        
        # 创建新规则
        new_rule = {
            "user": parsed_args.user,
            "group": parsed_args.group,
            "max_mode": parsed_args.max_mode,
            "need_con": need_con if need_con else ["*"],
            "no_con": no_con
        }
        
        SADO_CONFIG.append(new_rule)
        print(f"✓ 规则已添加: user={parsed_args.user}, max_mode={parsed_args.max_mode}")
        return True
        
    except Exception as e:
        print(f"添加规则失败: {e}")
        return False


def _remove_rule(SADO_CONFIG: list, args: list, lang_msgs: dict, current_lang: str) -> bool:
    """删除规则"""
    if len(args) < 1:
        print("用法: nanosado remove <索引> 或 nanosado remove -u <用户名>")
        return False
    
    if args[0] == "-u" and len(args) > 1:
        # 按用户名删除
        username = args[1]
        removed = False
        new_config = []
        for rule in SADO_CONFIG:
            if rule.get("user") != username:
                new_config.append(rule)
            else:
                removed = True
        if removed:
            SADO_CONFIG[:] = new_config
            print(f"✓ 已删除用户 {username} 的规则")
            return True
        else:
            print(f"未找到用户 {username} 的规则")
            return False
    else:
        # 按索引删除
        try:
            idx = int(args[0])
            if 0 <= idx < len(SADO_CONFIG):
                removed_rule = SADO_CONFIG.pop(idx)
                print(f"✓ 已删除规则: user={removed_rule.get('user', '*')}, max_mode={removed_rule.get('max_mode', 'mid')}")
                return True
            else:
                print(f"索引 {idx} 超出范围 (0-{len(SADO_CONFIG)-1})")
                return False
        except ValueError:
            print("无效的索引")
            return False


def _modify_rule(SADO_CONFIG: list, args: list, lang_msgs: dict, current_lang: str) -> bool:
    """修改现有规则"""
    if len(args) < 1:
        print("用法: nanosado modify <索引> [-u user] [-g group] [-m max_mode] [-n need_con] [-x no_con]")
        return False
    
    try:
        idx = int(args[0])
        if idx < 0 or idx >= len(SADO_CONFIG):
            print(f"索引 {idx} 超出范围 (0-{len(SADO_CONFIG)-1})")
            return False
        
        rule = SADO_CONFIG[idx]
        
        # 解析剩余参数
        i = 1
        while i < len(args):
            opt = args[i]
            if opt == "-u" and i + 1 < len(args):
                rule["user"] = args[i + 1]
                i += 2
            elif opt == "-g" and i + 1 < len(args):
                rule["group"] = args[i + 1]
                i += 2
            elif opt == "-m" and i + 1 < len(args):
                if args[i + 1] in ["low", "mid", "adv"]:
                    rule["max_mode"] = args[i + 1]
                else:
                    print(f"无效的 max_mode: {args[i+1]}, 必须是 low/mid/adv")
                    return False
                i += 2
            elif opt == "-n" and i + 1 < len(args):
                # 支持多个 need_con 规则
                if "need_con" not in rule:
                    rule["need_con"] = []
                rule["need_con"].append(args[i + 1])
                i += 2
            elif opt == "-x" and i + 1 < len(args):
                if "no_con" not in rule:
                    rule["no_con"] = []
                rule["no_con"].append(args[i + 1])
                i += 2
            elif opt == "--clear-need":
                rule["need_con"] = []
                i += 1
            elif opt == "--clear-no":
                rule["no_con"] = []
                i += 1
            else:
                print(f"未知选项: {opt}")
                return False
        
        print(f"✓ 规则 {idx} 已修改: user={rule.get('user', '*')}, max_mode={rule.get('max_mode', 'mid')}")
        return True
        
    except ValueError:
        print("无效的索引")
        return False


def _save_config(SADO_CONFIG_PATH: str, SADO_CONFIG: list, original_content: str, lang_msgs: dict, current_lang: str, log_info, request_id) -> tuple:
    """
    保存配置并验证
    返回: (success: bool, new_content: str, error_msg: str)
    """
    try:
        new_content = json.dumps(SADO_CONFIG, ensure_ascii=False, indent=2)
        
        # 验证 JSON 格式
        json.loads(new_content)
        
        # 验证规则格式
        for idx, rule in enumerate(SADO_CONFIG):
            if not isinstance(rule, dict):
                raise ValueError(f"规则 {idx} 必须是对象")
            if "max_mode" not in rule:
                raise ValueError(f"规则 {idx} 缺少 max_mode 字段")
            if rule["max_mode"] not in ["low", "mid", "adv"]:
                raise ValueError(f"规则 {idx} 的 max_mode 必须是 low/mid/adv")
            if "need_con" not in rule:
                rule["need_con"] = []
            if "no_con" not in rule:
                rule["no_con"] = []
            if not isinstance(rule["need_con"], list):
                raise ValueError(f"规则 {idx} 的 need_con 必须是数组")
            if not isinstance(rule["no_con"], list):
                raise ValueError(f"规则 {idx} 的 no_con 必须是数组")
        
        # 写入文件
        with open(SADO_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(new_content)
        
        return True, new_content, None
        
    except json.JSONDecodeError as e:
        return False, None, f"JSON 解析错误: {e.msg} (行 {e.lineno}, 列 {e.colno})"
    except ValueError as e:
        return False, None, str(e)
    except Exception as e:
        return False, None, str(e)


def handle_nanosado_core(
    cmd_parts,
    request_id,
    user_mode,
    OS_OR_TBS,
    sys_type,
    SADO_CONFIG,
    SADO_CONFIG_PATH,
    user_info,
    get_current_lang,
    log_info,
    log_error,
    Fore,
    Style
):
    lang_msgs = {
        "chinese": {
            "usage": "用法：nanosado [命令|编辑器]",
            "desc": "编辑或管理 sado 配置文件（/etc/sado.json）\n"
                    "  命令：\n"
                    "    nanosado list                     - 列出所有规则\n"
                    "    nanosado add -u <用户> -m <模式>   - 添加规则\n"
                    "    nanosado remove <索引>            - 删除规则\n"
                    "    nanosado remove -u <用户名>       - 删除用户规则\n"
                    "    nanosado modify <索引> [选项]     - 修改规则\n"
                    "  编辑器：\n"
                    "    nanosado                         - 自动检测编辑器\n"
                    "    nanosado vim                      - 使用 vim 编辑\n"
                    "    nanosado nano                     - 使用 nano 编辑",
            "add_help": "添加规则选项：\n"
                        "  -u, --user <用户名>    用户名（默认 * 表示所有用户）\n"
                        "  -g, --group <组名>      组名（默认 *）\n"
                        "  -m, --max-mode <模式>   最高允许模式（low/mid/adv，默认 mid）\n"
                        "  -n, --need-con <命令>   需要确认的命令（支持通配符 *）\n"
                        "  -x, --no-con <命令>     不需要确认的命令",
            "modify_help": "修改规则选项：\n"
                           "  -u <用户名>             修改用户名\n"
                           "  -g <组名>               修改组名\n"
                           "  -m <模式>               修改最高模式（low/mid/adv）\n"
                           "  -n <命令>               添加 need_con 规则\n"
                           "  -x <命令>               添加 no_con 规则\n"
                           "  --clear-need            清空 need_con\n"
                           "  --clear-no              清空 no_con",
            "perm_denied_os": "OS模式下只有 root 用户且在 adv 模式才能编辑 sado 配置",
            "perm_denied_tbs": "TBS模式下需要 adv 模式才能编辑 sado 配置",
            "not_adv": "当前不是 adv 模式，请先执行 'activite -m adv'",
            "not_root": "当前不是 root 用户，无法编辑 sado 配置",
            "config_not_exist": "sado 配置文件不存在，请先让 root 用户（OS模式）或当前用户（TBS模式）初始化",
            "backup_created": "已备份原配置文件：{}",
            "syntax_error": "配置文件语法错误：{}",
            "save_success": "配置文件保存成功",
            "save_fail": "配置文件保存失败：{}",
            "open_editor": "正在打开编辑器...",
            "editor_not_found": "未找到编辑器 '{}'，可用编辑器：{}",
            "default_editor_not_found": "未找到可用编辑器，请指定编辑器（如：nanosado vim）",
            "unknown_command": "未知命令: {}，可用命令: list, add, remove, modify",
            "invalid_args": "参数错误：{}",
            "rollback": "已回退到修改前的配置"
        },
        "english": {
            "usage": "Usage: nanosado [command|editor]",
            "desc": "Edit or manage sado config file (/etc/sado.json)\n"
                    "  Commands:\n"
                    "    nanosado list                     - List all rules\n"
                    "    nanosado add -u <user> -m <mode>   - Add rule\n"
                    "    nanosado remove <index>            - Remove rule by index\n"
                    "    nanosado remove -u <username>      - Remove rule by user\n"
                    "    nanosado modify <index> [options]  - Modify rule\n"
                    "  Editor:\n"
                    "    nanosado                          - Auto-detect editor\n"
                    "    nanosado vim                       - Edit with vim\n"
                    "    nanosado nano                      - Edit with nano",
            "add_help": "Add rule options:\n"
                        "  -u, --user <username>    Username (default * for all users)\n"
                        "  -g, --group <group>      Group name (default *)\n"
                        "  -m, --max-mode <mode>    Max allowed mode (low/mid/adv, default mid)\n"
                        "  -n, --need-con <cmd>     Commands requiring confirmation (supports *)\n"
                        "  -x, --no-con <cmd>       Commands not requiring confirmation",
            "modify_help": "Modify rule options:\n"
                           "  -u <username>            Modify username\n"
                           "  -g <group>                Modify group name\n"
                           "  -m <mode>                 Modify max mode (low/mid/adv)\n"
                           "  -n <cmd>                  Add need_con rule\n"
                           "  -x <cmd>                  Add no_con rule\n"
                           "  --clear-need              Clear need_con list\n"
                           "  --clear-no                Clear no_con list",
            "perm_denied_os": "OS mode: Only root user in adv mode can edit sado config",
            "perm_denied_tbs": "TBS mode: adv mode is required to edit sado config",
            "not_adv": "Not in adv mode, please run 'activite -m adv' first",
            "not_root": "Not root user, cannot edit sado config",
            "config_not_exist": "Sado config file not found, please let root (OS mode) or current user (TBS mode) initialize first",
            "backup_created": "Backup created: {}",
            "syntax_error": "Config syntax error: {}",
            "save_success": "Config saved successfully",
            "save_fail": "Failed to save config: {}",
            "open_editor": "Opening editor...",
            "editor_not_found": "Editor '{}' not found, available editors: {}",
            "default_editor_not_found": "No editor found, please specify editor (e.g.: nanosado vim)",
            "unknown_command": "Unknown command: {}, available commands: list, add, remove, modify",
            "invalid_args": "Invalid arguments: {}",
            "rollback": "Rolled back to previous config"
        }
    }
    current_lang = get_current_lang()
    msg = lang_msgs.get(current_lang, lang_msgs["chinese"])

    # 权限检查
    if user_mode.current_mode != "adv":
        print(Fore.RED + msg["not_adv"] + Style.RESET_ALL)
        return

    if OS_OR_TBS == "OS":
        is_root = False
        try:
            if sys_type.startswith("linux") or "termux" in sys.prefix.lower():
                is_root = os.geteuid() == 0
            elif sys_type.startswith("win32"):
                import ctypes
                is_root = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            is_root = False
        if not is_root:
            print(Fore.RED + msg["not_root"] + Style.RESET_ALL)
            print(Fore.YELLOW + msg["perm_denied_os"] + Style.RESET_ALL)
            return

    if not os.path.exists(SADO_CONFIG_PATH):
        print(Fore.RED + msg["config_not_exist"] + Style.RESET_ALL)
        return

    # 读取当前配置
    try:
        with open(SADO_CONFIG_PATH, "r", encoding="utf-8") as f:
            original_content = f.read()
        SADO_CONFIG[:] = json.loads(original_content)
    except Exception as e:
        log_error(f"读取配置失败：{str(e)}", request_id)
        return

    # 解析命令
    if len(cmd_parts) == 1:
        # 无参数：打开编辑器
        command = "edit"
    else:
        command = cmd_parts[1].lower()

    # 命令处理
    if command == "list":
        _list_rules(msg, SADO_CONFIG, current_lang)
        return

    elif command == "add":
        if len(cmd_parts) < 3:
            print(Fore.YELLOW + msg["add_help"] + Style.RESET_ALL)
            return
        
        if not _add_rule(SADO_CONFIG, cmd_parts[2:], msg, current_lang):
            return
        
        # 保存配置
        success, new_content, error = _save_config(SADO_CONFIG_PATH, SADO_CONFIG, original_content, msg, current_lang, log_info, request_id)
        if success:
            print(Fore.GREEN + msg["save_success"] + Style.RESET_ALL)
            log_info(f"nanosado add: 添加新规则", request_id)
        else:
            print(Fore.RED + msg["save_fail"].format(error) + Style.RESET_ALL)
            # 回滚
            SADO_CONFIG[:] = json.loads(original_content)
        return

    elif command == "remove":
        if len(cmd_parts) < 3:
            print(Fore.YELLOW + "用法: nanosado remove <索引> 或 nanosado remove -u <用户名>" + Style.RESET_ALL)
            return
        
        if not _remove_rule(SADO_CONFIG, cmd_parts[2:], msg, current_lang):
            return
        
        # 保存配置
        success, new_content, error = _save_config(SADO_CONFIG_PATH, SADO_CONFIG, original_content, msg, current_lang, log_info, request_id)
        if success:
            print(Fore.GREEN + msg["save_success"] + Style.RESET_ALL)
            log_info(f"nanosado remove: 删除规则", request_id)
        else:
            print(Fore.RED + msg["save_fail"].format(error) + Style.RESET_ALL)
            # 回滚
            SADO_CONFIG[:] = json.loads(original_content)
        return

    elif command == "modify":
        if len(cmd_parts) < 3:
            print(Fore.YELLOW + msg["modify_help"] + Style.RESET_ALL)
            return
        
        if not _modify_rule(SADO_CONFIG, cmd_parts[2:], msg, current_lang):
            return
        
        # 保存配置
        success, new_content, error = _save_config(SADO_CONFIG_PATH, SADO_CONFIG, original_content, msg, current_lang, log_info, request_id)
        if success:
            print(Fore.GREEN + msg["save_success"] + Style.RESET_ALL)
            log_info(f"nanosado modify: 修改规则", request_id)
        else:
            print(Fore.RED + msg["save_fail"].format(error) + Style.RESET_ALL)
            # 回滚
            SADO_CONFIG[:] = json.loads(original_content)
        return

    elif command == "edit" or command in ["vim", "nano", "vi", "code", "gedit", "notepad", "notepad++", "subl", "emacs", "ne", "micro", "helix", "leafpad", "mousepad", "kate", "pluma"]:
        # 编辑器模式
        requested_editor = None if command == "edit" else command
        
        # 备份原配置
        backup_path = f"{SADO_CONFIG_PATH}.bak"
        try:
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write(original_content)
            print(Fore.YELLOW + msg["backup_created"].format(backup_path) + Style.RESET_ALL)
        except Exception as e:
            log_error(f"备份失败：{str(e)}", request_id)

        print(Fore.GREEN + msg["open_editor"] + Style.RESET_ALL)
        
        # 查找编辑器
        if requested_editor:
            editor_cmd = _find_editor(requested_editor, sys_type)
            if not editor_cmd:
                available = ", ".join(["vim", "nano", "vi", "code", "gedit", "notepad", "notepad++", "subl", "emacs", "micro", "helix", "ne", "leafpad", "mousepad", "kate", "pluma"])
                print(Fore.RED + msg["editor_not_found"].format(requested_editor, available) + Style.RESET_ALL)
                return
        else:
            editor_cmd = _get_default_editor(sys_type)
            if not editor_cmd:
                print(Fore.RED + msg["default_editor_not_found"] + Style.RESET_ALL)
                return

        # 启动编辑器
        try:
            if sys_type.startswith("win32"):
                subprocess.run([editor_cmd, SADO_CONFIG_PATH], shell=True, check=False)
            else:
                terminal_editors = ["vim", "nano", "vi", "ne", "micro", "helix", "hx"]
                if os.path.basename(editor_cmd) in terminal_editors:
                    subprocess.run([editor_cmd, SADO_CONFIG_PATH], check=False)
                else:
                    subprocess.Popen([editor_cmd, SADO_CONFIG_PATH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log_error(f"启动编辑器失败：{str(e)}", request_id)

        # 验证修改后的配置
        try:
            with open(SADO_CONFIG_PATH, "r", encoding="utf-8") as f:
                new_content = f.read()
            if new_content == original_content:
                print(Fore.YELLOW + "配置未修改" + Style.RESET_ALL)
                return
            
            new_config = json.loads(new_content)
            # 验证规则格式
            for idx, rule in enumerate(new_config):
                if not isinstance(rule, dict):
                    raise ValueError(f"规则 {idx} 必须是对象")
                if "max_mode" not in rule:
                    raise ValueError(f"规则 {idx} 缺少 max_mode 字段")
                if rule["max_mode"] not in ["low", "mid", "adv"]:
                    raise ValueError(f"规则 {idx} 的 max_mode 必须是 low/mid/adv")
                if "need_con" not in rule:
                    rule["need_con"] = []
                if "no_con" not in rule:
                    rule["no_con"] = []
                if not isinstance(rule["need_con"], list):
                    raise ValueError(f"规则 {idx} 的 need_con 必须是数组")
                if not isinstance(rule["no_con"], list):
                    raise ValueError(f"规则 {idx} 的 no_con 必须是数组")
            
            SADO_CONFIG[:] = new_config
            print(Fore.GREEN + msg["save_success"] + Style.RESET_ALL)
            log_info(f"nanosado: 配置文件已更新，共{len(SADO_CONFIG)}条规则", request_id)
            
        except json.JSONDecodeError as e:
            print(Fore.RED + msg["syntax_error"].format(str(e)) + Style.RESET_ALL)
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    with open(SADO_CONFIG_PATH, "w", encoding="utf-8") as f_out:
                        f_out.write(f.read())
                print(Fore.YELLOW + msg["rollback"] + Style.RESET_ALL)
                SADO_CONFIG[:] = json.loads(original_content)
            except Exception as restore_err:
                log_error(f"回退失败：{str(restore_err)}", request_id)
        except ValueError as e:
            print(Fore.RED + msg["syntax_error"].format(str(e)) + Style.RESET_ALL)
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    with open(SADO_CONFIG_PATH, "w", encoding="utf-8") as f_out:
                        f_out.write(f.read())
                print(Fore.YELLOW + msg["rollback"] + Style.RESET_ALL)
                SADO_CONFIG[:] = json.loads(original_content)
            except Exception as restore_err:
                log_error(f"回退失败：{str(restore_err)}", request_id)
        except Exception as e:
            print(Fore.RED + msg["save_fail"].format(str(e)) + Style.RESET_ALL)
        return

    else:
        print(Fore.RED + msg["unknown_command"].format(command) + Style.RESET_ALL)
        print(Fore.YELLOW + msg["usage"] + Style.RESET_ALL)
        print(Fore.YELLOW + msg["desc"] + Style.RESET_ALL)
        return