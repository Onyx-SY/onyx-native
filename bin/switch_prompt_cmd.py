import os
import sys
import json
import socket
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

def load_prompt_config(PROMPT_STORAGE_PATH: str, prompt_templates: Dict[str, str]) -> str:
    """加载已保存的提示符模板（默认def）"""
    if os.path.exists(PROMPT_STORAGE_PATH):
        try:
            with open(PROMPT_STORAGE_PATH, "r", encoding="utf-8") as f:
                saved_template = f.read().strip()
            return saved_template if saved_template in prompt_templates else "def"
        except Exception:
            return "def"
    return "def"

def save_prompt_config(PROMPT_STORAGE_PATH: str, template_name: str, sys_type: str, log_info: Callable[[str, str], None], log_error: Callable[[str, str], None], request_id: str) -> bool:
    """保存提示符模板到配置文件"""
    try:
        parent_dir = os.path.dirname(PROMPT_STORAGE_PATH)
        if not os.path.exists(parent_dir):
            os.makedirs(parent_dir, mode=0o755)
        
        with open(PROMPT_STORAGE_PATH, "w", encoding="utf-8") as f:
            f.write(template_name)
        
        if sys_type in ["Linux/macOS", "Termux", "SpecialLinux"]:
            os.chmod(PROMPT_STORAGE_PATH, 0o600)
        
        log_info(f"已保存提示符模板：{template_name}", request_id)
        return True
    except PermissionError:
        log_error("保存失败：权限不足", request_id)
        return False
    except Exception as e:
        log_error(f"保存失败：{str(e)}", request_id)
        return False

def format_virtual_path(virtual_path: str, max_len: int = 15) -> str:
    """辅助函数：缩短超长虚拟路径"""
    from Onyx import get_virtual_path
    
    virtual_path = get_virtual_path(virtual_path)
    if virtual_path in ["/", "~", "/（外部）", "/（路径异常）"] or len(virtual_path) <= max_len:
        return virtual_path
    path_parts = [p for p in virtual_path.split("/") if p.strip()]
    last_part = path_parts[-1]
    if len(path_parts) >= 2:
        second_last = path_parts[-2]
        shortened = f".../{second_last}/{last_part}"
        return shortened if len(shortened) <= max_len else f".../{last_part}"
    return f".../{last_part}"

def get_prompt_preview(template_str: str, user_info: Dict[str, Any], user_mode: Any, OS_OR_TBS: str, virtual_path: str) -> str:
    """生成提示符预览效果"""
    
    try:
        return template_str.format(
            user=user_info["name"],
            mode_per=user_mode.current_mode,
            mode_TS=OS_OR_TBS,
            sys_type="Onyx",
            relative_path=format_virtual_path(virtual_path),
            permission=user_info["permission_flag"],
            host=socket.gethostname(),
            venv_git="",  # switch-prompt 预览不需要显示真实状态
            exit_mark="",
            accent=Fore.GREEN,
            accent_reset=Style.RESET_ALL,
            BLUE=Fore.BLUE,
            RED=Fore.RED,
            GREEN=Fore.GREEN,
            YELLOW=Fore.YELLOW,
            RESET=Style.RESET_ALL
        )
    except Exception as e:
        return f"模板解析错误：{str(e)[:30]}"

def handle_switch_prompt_core(
    cmd_parts: List[str],
    request_id: str,
    USER_HOME_DIR: str,
    sys_type: str,
    OS_OR_TBS: str,
    user_info: Dict[str, Any],
    user_mode: Any,
    global_config: Dict[str, Any],
    log_info: Callable[[str, str], None],
    log_error: Callable[[str, str], None],
    Fore: Any,
    Style: Any
) -> None:
    """switch-prompt命令核心逻辑（切换/列出/查看提示符模板）"""
    PROMPT_STORAGE_PATH = os.path.join(USER_HOME_DIR, ".config", "onyx", ".prompt")
    prompt_templates = global_config.get("display_info", {}).get("command_prompts", {})
    current_lang = load_global_lang(USER_HOME_DIR)
    current_dir = os.getcwd()
    virtual_path = get_virtual_path(current_dir) if "get_virtual_path" in globals() else current_dir

    lang_msgs = {
        "chinese": {
            "usage": "用法：",
            "usage_switch": "  switch-prompt switch <模板名>    # 切换提示符模板（例：switch-prompt switch subuntu）",
            "usage_list": "  switch-prompt list             # 列出所有可用模板",
            "usage_preview": "  switch-prompt preview <模板名>  # 预览模板效果（例：switch-prompt preview def）",
            "config_missing": "配置缺失：display_info.command_prompts（检查config.json）",
            "list_title": "可用提示符模板列表：",
            "current_default": "【当前使用】",
            "preview_title": "模板预览效果：",
            "switch_success": "✅ 提示符模板切换成功！新效果：",
            "switch_fail": "❌ 切换失败：{}",
            "template_not_exist": "❌ 模板不存在！可用模板：{}",
            "invalid_subcmd": "无效子命令！支持：switch/list/preview",
            "hint_switch": "提示：使用 {} 切换模板"
        },
        "english": {
            "usage": "Usage: ",
            "usage_switch": "  switch-prompt switch <template>    # Switch prompt template (e.g. switch-prompt switch subuntu)",
            "usage_list": "  switch-prompt list             # List all available templates",
            "usage_preview": "  switch-prompt preview <template>  # Preview template effect (e.g. switch-prompt preview def)",
            "config_missing": "Config missing: display_info.command_prompts (check config.json)",
            "list_title": "Available Prompt Templates: ",
            "current_default": "[Current]",
            "preview_title": "Template Preview: ",
            "switch_success": "✅ Prompt template switched successfully! New effect: ",
            "switch_fail": "❌ Switch failed: {}",
            "template_not_exist": "❌ Template does not exist! Available templates: {}",
            "invalid_subcmd": "Invalid subcommand! Supported: switch/list/preview",
            "hint_switch": "Hint: Use {} to switch template"
        }
    }
    msg = lang_msgs[current_lang]

    if not prompt_templates:
        print(Fore.RED + msg["config_missing"] + Style.RESET_ALL)
        log_error(msg["config_missing"], request_id)
        return

    current_template = load_prompt_config(PROMPT_STORAGE_PATH, prompt_templates)

    if len(cmd_parts) < 2:
        print(Fore.RED + msg["usage"] + Style.RESET_ALL)
        print(msg["usage_switch"])
        print(msg["usage_list"])
        print(msg["usage_preview"])
        return

    subcmd = cmd_parts[1].lower()

    # 列出所有模板
    if subcmd == "list":
        print(Fore.CYAN + msg["list_title"] + Style.RESET_ALL)
        print("=" * 60)
        for template_name, template_str in prompt_templates.items():
            default_tag = msg["current_default"] if template_name == current_template else ""
            preview = get_prompt_preview(template_str, user_info, user_mode, OS_OR_TBS, virtual_path)
            print(f"\n{Fore.YELLOW}[{template_name}]{default_tag}" + Style.RESET_ALL)
            print(f"{preview}")
        # 提取 switch 命令示例用于提示
        switch_example = msg["usage_switch"].split("    ")[0].strip() if "    " in msg["usage_switch"] else "switch-prompt switch <模板名>"
        print("\n" + Fore.YELLOW + msg["hint_switch"].format(switch_example) + Style.RESET_ALL)
        return

    # 预览模板效果
    if subcmd == "preview":
        if len(cmd_parts) != 3:
            print(Fore.RED + msg["usage_preview"] + Style.RESET_ALL)
            return
        target_template = cmd_parts[2]
        if target_template not in prompt_templates:
            available = list(prompt_templates.keys())
            print(Fore.RED + msg["template_not_exist"].format(available) + Style.RESET_ALL)
            return
        preview = get_prompt_preview(prompt_templates[target_template], user_info, user_mode, OS_OR_TBS, virtual_path)
        print(Fore.CYAN + msg["preview_title"] + Style.RESET_ALL)
        print(Fore.GREEN + preview + Style.RESET_ALL)
        return

    # 切换模板
    if subcmd == "switch":
        if len(cmd_parts) != 3:
            print(Fore.RED + msg["usage_switch"] + Style.RESET_ALL)
            return
        target_template = cmd_parts[2]
        if target_template not in prompt_templates:
            available = list(prompt_templates.keys())
            print(Fore.RED + msg["template_not_exist"].format(available) + Style.RESET_ALL)
            return
        if save_prompt_config(PROMPT_STORAGE_PATH, target_template, sys_type, log_info, log_error, request_id):
            global_config["system_info"]["current_prompt_type"] = target_template
            # 同步更新 ~/.prompt.conf（如果存在），否则 generate_prompt() 会忽略切换
            _prompt_conf_path = os.path.join(os.path.expanduser("~"), ".prompt.conf")
            try:
                template_str = prompt_templates[target_template]
                # 写入新的 prompt.conf
                lines = []
                if os.path.exists(_prompt_conf_path):
                    with open(_prompt_conf_path, "r", encoding="utf-8") as pf:
                        lines = pf.readlines()
                # 替换 prompt= 行
                new_lines = []
                found = False
                for line in lines:
                    if line.startswith("prompt=") and not line.strip().startswith("#"):
                        new_lines.append("prompt=" + template_str.replace("\n", "\\n") + "\n")
                        found = True
                    else:
                        new_lines.append(line)
                if not found:
                    new_lines.append("prompt=" + template_str.replace("\n", "\\n") + "\n")
                with open(_prompt_conf_path, "w", encoding="utf-8") as pf:
                    pf.writelines(new_lines)
                # 清除 Onyx 模块缓存，让下次 prompt 重新加载
                import Onyx as _onyx_mod
                _onyx_mod._CACHED_PROMPT_CONF = template_str  # 模板已有真实 \n
            except Exception:
                pass
            preview = get_prompt_preview(prompt_templates[target_template], user_info, user_mode, OS_OR_TBS, virtual_path)
            print(Fore.GREEN + msg["switch_success"] + Style.RESET_ALL)
            print(Fore.CYAN + preview + Style.RESET_ALL)
        else:
            print(Fore.RED + msg["switch_fail"].format("保存配置失败 / Failed to save configuration") + Style.RESET_ALL)
        return

    # 无效子命令
    print(Fore.RED + msg["invalid_subcmd"] + Style.RESET_ALL)