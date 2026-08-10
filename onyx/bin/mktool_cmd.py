import os
import json
import sys
import shutil
from typing import Dict, Any, List, Callable
from lib.terminal.colors import Fore, Style
from argparse import ArgumentParser
from datetime import datetime


def load_language_config(ROOT_DIR: str) -> Dict[str, Any]:
    """加载mktool语言配置文件（从onyx/etc/mktool/language.json）"""
    lang_config_path = os.path.join(ROOT_DIR, "onyx", "etc", "mktool", "language.json")
    if not os.path.exists(lang_config_path):
        raise FileNotFoundError(f"语言配置文件缺失：{lang_config_path}")
    with open(lang_config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_global_lang(lang_config: Dict[str, Any], USER_HOME_DIR: str) -> str:
    """读取全局语言配置（兼容原逻辑）"""
    lang_path = lang_config["language_file_path"].replace("USER_HOME_DIR", USER_HOME_DIR)
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


def handle_mktool_core(
    cmd_parts: List[str],
    request_id: str,
    ROOT_DIR: str,
    USER_HOME_DIR: str,
    SYS_SPECIFIC: Dict[str, Any],
    check_sandbox_path: Callable[[str, str], bool],
    get_virtual_path: Callable[[str], str],
    log_info: Callable[[str, str], None],
    log_error: Callable[[str, str], None],
    Fore: Any,
    Style: Any
) -> None:
    """mktool核心逻辑（移除无效导入+完善异常处理+新增help.json创建）"""
    # 加载语言配置与提示文本
    try:
        lang_config = load_language_config(ROOT_DIR)
        supported_langs = {item["name"]: item for item in lang_config["supported_languages"]}
        default_lang = lang_config["default_language"]
    except Exception as e:
        print(Fore.RED + f"❌ 加载语言配置失败：{str(e)}" + Style.RESET_ALL)
        log_error(f"mktool load lang config failed: {str(e)}", request_id)
        return
    # 双语提示映射
    current_lang = get_global_lang(lang_config, USER_HOME_DIR)
    lang_msgs = {
        "chinese": {
            "usage_error": "❌ 用法错误！正确格式：mktool -n <工具名> -l <语言>",
            "support_lang_title": "✅ 支持语言：",
            "support_lang_item": "  - {name}（{label}）",
            "example": "✅ 示例：mktool -n port_scanner -l python",
            "invalid_name": "❌ 无效工具名：不可为空、含空格，或以 . / \\ - 开头",
            "no_permission": "❌ 工具目录无权限：{}",
            "sandbox_block": "❌ 沙箱拦截：工具路径不在允许范围内",
            "tool_exist": "❌ 工具已存在（路径：{}）（删除后重试）",
            "create_dir": "📂 已创建工具目录（路径：{}）",
            "success": "✅ 工具创建成功！",
            "name_label": "工具名称：",
            "lang_label": "开发语言：",
            "edit_cmd": "编辑工具命令：nano {}",
            "create_fail": "❌ 创建失败：{}（已清理残留文件）",
            "lang_not_supported": "❌ 不支持的语言！支持的语言：{}",
            "formwork_missing": "❌ 模板文件缺失：{}（请检查formwork文件是否在 ROOT_DIR/onyx/etc/mktool/ 目录下）",
            "create_help": "📄 已创建教学文件：{}"
        },
        "english": {
            "usage_error": "❌ Usage error! Correct format: mktool -n <tool_name> -l <language>",
            "support_lang_title": "✅ Supported languages：",
            "support_lang_item": "  - {name}（{label}）",
            "example": "✅ Example: mktool -n port_scanner -l python",
            "invalid_name": "❌ Invalid tool name: cannot be empty, contain spaces, or start with . / \\ -",
            "no_permission": "❌ No permission for tool directory: {}",
            "sandbox_block": "❌ Sandbox blocked: tool path not in allowed range",
            "tool_exist": "❌ Tool already exists (path: {}) (delete and try again)",
            "create_dir": "📂 Tool directory created (path: {})",
            "success": "✅ Tool created successfully!",
            "name_label": "Tool name: ",
            "lang_label": "Development language: ",
            "edit_cmd": "Edit tool command: nano {}",
            "create_fail": "❌ Creation failed: {} (residual files cleaned)",
            "lang_not_supported": "❌ Unsupported language! Supported languages: {}",
            "formwork_missing": "❌ Formwork file missing: {}（please check if formwork file is in ROOT_DIR/onyx/etc/mktool/）",
            "create_help": "📄 Teaching file created: {}"
        }
    }
    msg = lang_msgs[current_lang]
    # 解析命令行参数（移除SystemExit导入，直接捕获内置异常）
    parser = ArgumentParser(description="Create bilingual tool with auto-derived paths", add_help=False)
    parser.add_argument("-n", required=True, help="Tool name")
    parser.add_argument("-l", default=default_lang, help=f"Programming language (default: {default_lang})")
    
    try:
        args = parser.parse_args(cmd_parts[1:])
    except SystemExit:  # SystemExit是内置异常，无需导入
        print(Fore.RED + msg["usage_error"] + Style.RESET_ALL)
        print(Fore.YELLOW + msg["support_lang_title"] + Style.RESET_ALL)
        for lang_name, lang_info in supported_langs.items():
            print(Fore.YELLOW + msg["support_lang_item"].format(
                name=lang_name, label=lang_info["label"]
            ) + Style.RESET_ALL)
        print(Fore.YELLOW + msg["example"] + Style.RESET_ALL)
        return
    tool_name = args.n.strip()
    target_lang = args.l.lower()
    # 验证工具名合法性
    if not tool_name or tool_name.startswith((".", "/", "\\", "-")) or " " in tool_name:
        print(Fore.RED + msg["invalid_name"] + Style.RESET_ALL)
        return
    # 验证语言合法性
    if target_lang not in supported_langs:
        supported_lang_names = ", ".join(supported_langs.keys())
        print(Fore.RED + msg["lang_not_supported"].format(supported_lang_names) + Style.RESET_ALL)
        return
    # 获取语言配置与模板路径
    lang_info = supported_langs[target_lang]
    formwork_filename = f"formwork{lang_info['extension']}"
    formwork_path = os.path.join(ROOT_DIR, "onyx", "etc", "mktool", formwork_filename)
    # 验证模板文件
    if not os.path.exists(formwork_path):
        print(Fore.RED + msg["formwork_missing"].format(formwork_path) + Style.RESET_ALL)
        log_error(f"mktool formwork missing: {formwork_path}", request_id)
        return
    # 工具路径配置
    tool_root = os.path.join(ROOT_DIR, "tools", "plugin")
    tool_dir_real = os.path.abspath(os.path.join(tool_root, tool_name))
    main_file_name = f"Main{lang_info['extension']}"
    nano_edit_path = os.path.join(get_virtual_path(tool_dir_real), main_file_name).replace("\\", "/")
    # 路径预处理与权限检查
    if not os.path.exists(tool_root):
        os.makedirs(tool_root, mode=0o755 if sys.platform != "win32" else 0o777)
        log_info(f"Created tool parent directory: {tool_root}", request_id)
    if not os.access(tool_root, os.W_OK | os.X_OK):
        print(Fore.RED + msg["no_permission"].format(tool_root) + Style.RESET_ALL)
        print(Fore.YELLOW + f"✅ Please run: chmod 755 {tool_root}" + Style.RESET_ALL)
        log_error(f"No permission for tool directory: {tool_root}", request_id)
        return
    if not check_sandbox_path(tool_dir_real, request_id):
        print(Fore.RED + msg["sandbox_block"] + Style.RESET_ALL)
        return
    if os.path.exists(tool_dir_real):
        print(Fore.RED + msg["tool_exist"].format(get_virtual_path(tool_dir_real)) + Style.RESET_ALL)
        log_error(f"mktool failed: {tool_name} already exists", request_id)
        return
    # 核心创建逻辑
    try:
        # 创建工具目录
        os.makedirs(tool_dir_real, mode=0o755 if sys.platform != "win32" else 0o777)
        if sys.platform != "win32":
            os.chmod(tool_dir_real, 0o755)
        print(Fore.CYAN + msg["create_dir"].format(get_virtual_path(tool_dir_real)) + Style.RESET_ALL)
        # 创建权限文件
        perm_file_name = SYS_SPECIFIC.get("perm_file", ".perm")
        perm_file_real = os.path.join(tool_dir_real, perm_file_name)
        with open(perm_file_real, "w", encoding="utf-8") as f:
            f.write("1")
        if sys.platform != "win32":
            os.chmod(perm_file_real, 0o644)
        # 创建配置文件
        config_rule = SYS_SPECIFIC.get("tool_config_rule", {})
        config_file_name = config_rule.get("config_file", "config.conf")
        config_file_real = os.path.join(tool_dir_real, config_file_name)
        intro_cn = f"自动推导根目录+双语切换工具（{lang_info['label']}）"
        intro_en = f"Auto-derived root path + bilingual tool（{lang_info['label']}）"
        with open(config_file_real, "w", encoding="utf-8") as f:
            f.write(f"author={os.getlogin() if hasattr(os, 'getlogin') else 'default_user'}\n")
            f.write(f"name={tool_name}\n")
            f.write(f"version=1.0.0\n")
            f.write(f"cli=1\n")
            f.write(f"type=plugin\n")
            f.write(f"language={target_lang}\n")
            f.write(f"introduction={intro_cn}\\n{intro_en}\n")
        if sys.platform != "win32":
            os.chmod(config_file_real, 0o644)
        # 生成主程序文件
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(formwork_path, "r", encoding="utf-8") as f:
            formwork_content = f.read()
        formwork_content = formwork_content.replace("{{TOOL_NAME}}", tool_name)
        formwork_content = formwork_content.replace("{{CREATE_TIME}}", current_time)
        main_file_real = os.path.join(tool_dir_real, main_file_name)
        with open(main_file_real, "w", encoding="utf-8") as f:
            f.write(formwork_content)
        if sys.platform != "win32":
            os.chmod(main_file_real, 0o755)
        # 新增：创建help.json教学文件
        help_file_name = "help.json"
        help_file_real = os.path.join(tool_dir_real, help_file_name)
        # 构建指定格式的JSON内容
        help_json_content = {
            "命令": {
                tool_name: {
                    "Chinese": "",
                    "English": ""
                }
            }
        }
        # 写入help.json，保证格式化输出
        with open(help_file_real, "w", encoding="utf-8") as f:
            json.dump(help_json_content, f, ensure_ascii=False, indent=2)
        if sys.platform != "win32":
            os.chmod(help_file_real, 0o644)
        # 打印help.json创建提示
        help_virtual_path = get_virtual_path(help_file_real)
        print(Fore.BLUE + msg["create_help"].format(help_virtual_path) + Style.RESET_ALL)
        # 输出结果
        print(Fore.GREEN + f"\n{msg['success']}" + Style.RESET_ALL)
        print(Fore.WHITE + f"  {msg['name_label']}{tool_name}" + Style.RESET_ALL)
        print(Fore.WHITE + f"  {msg['lang_label']}{lang_info['label']}（{target_lang}）" + Style.RESET_ALL)
        print(Fore.YELLOW + f"\n{msg['edit_cmd'].format(nano_edit_path)}" + Style.RESET_ALL)
        log_info(f"mktool success: name={tool_name}, language={target_lang}, formwork={formwork_path}, help_json={help_file_real}", request_id)
    except Exception as e:
        # 异常清理
        if os.path.exists(tool_dir_real):
            shutil.rmtree(tool_dir_real)
        err_msg = msg["create_fail"].format(str(e))
        print(Fore.RED + err_msg + Style.RESET_ALL)
        log_error(f"mktool failed: {str(e)}", request_id)
