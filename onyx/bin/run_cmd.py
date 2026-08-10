import os
import sys
import shlex
from typing import List, Callable, Dict, Any
from lib.terminal.colors import Fore, Style
from argparse import ArgumentParser

def load_language_config(ROOT_DIR: str) -> Dict[str, Any]:
    lang_config_path = os.path.join(ROOT_DIR, "onyx", "etc", "mktool", "language.json")
    default_lang_config = {
        "default_language": "chinese",
        "language_file_path": "USER_HOME_DIR/.config/onyx/language",
        "supported_languages": [
            {"name": "chinese", "label": "中文", "extension": ".cn"},
            {"name": "english", "label": "English", "extension": ".en"}
        ]
    }
    if not os.path.exists(lang_config_path):
        return default_lang_config
    try:
        with open(lang_config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_lang_config

def get_global_lang(lang_config: Dict[str, Any], USER_HOME_DIR: str) -> str:
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

def handle_run_core(
    cmd_parts: List[str],
    request_id: str,
    ROOT_DIR: str,
    USER_HOME_DIR: str,
    OS_OR_TBS: str,
    sys_type: str,
    SUPPORTED_EXEC_SUFFIXES: Dict[str, List[str]],
    executable_config: Dict[str, Any],
    resolve_path: Callable[[str], str],
    get_virtual_path: Callable[[str], str],
    check_sandbox_path: Callable[[str, str], bool],
    validate_param_path: Callable[[str, str], str],
    run_cmd_sync: Callable[[str, str, bool, int], None],  # 同步执行函数
    PYTHON_EXE: str,
    log_info: Callable[[str, str], None],
    log_error: Callable[[str, str], None],
    Fore: Any,
    Style: Any
) -> None:
    """run 命令核心逻辑（同步执行+无额外提示）"""
    lang_config = load_language_config(ROOT_DIR)
    current_lang = get_global_lang(lang_config, USER_HOME_DIR)
    
    # 仅保留错误提示（成功无提示）
    lang_msgs = {
        "chinese": {
            "usage_error": "❌ 用法错误！正确格式：run <脚本路径> [参数]",
            "example": "✅ 示例：run ~/test.sh、run /tools/scan.py 127.0.0.1",
            "no_such_script": "❌ 脚本不存在或无权限：虚拟路径={}，原始参数={}",
            "unsupported_type": "❌ 不支持的脚本类型：{}，当前系统支持：{}",
            "windows_bash_tip": "❌ Windows系统运行Bash脚本需先安装WSL或Git Bash",
            "path_resolve_fail": "❌ 路径解析失败：{}",
            "sandbox_block": "❌ 沙箱拦截：路径不在允许范围内 → {}"
        },
        "english": {
            "usage_error": "❌ Usage error! Correct format: run <script_path> [arguments]",
            "example": "✅ Example: run ~/test.sh、run /tools/scan.py 127.0.0.1",
            "no_such_script": "❌ Script not found or no permission: virtual path={}, original parameter={}",
            "unsupported_type": "❌ Unsupported script type: {}, current system supports: {}",
            "windows_bash_tip": "❌ Windows system requires WSL or Git Bash to run Bash scripts",
            "path_resolve_fail": "❌ Path resolve failed: {}",
            "sandbox_block": "❌ Sandbox blocked: path not in allowed range → {}"
        }
    }
    msg = lang_msgs[current_lang]
    
    # 参数解析
    parser = ArgumentParser(description="Run script synchronously", add_help=False)
    parser.add_argument("script_path", help="Path of the script to run")
    parser.add_argument("arguments", nargs="*", help="Optional arguments for the script")
    
    try:
        args = parser.parse_args(cmd_parts[1:])
    except SystemExit:
        print(Fore.RED + msg["usage_error"] + Style.RESET_ALL)
        print(Fore.YELLOW + msg["example"] + Style.RESET_ALL)
        return
    
    input_path = args.script_path
    script_args = args.arguments
    
    # 路径解析
    try:
        virtual_script_path = resolve_path(input_path)
        processed_script_path = ""
        
        if os.path.exists(virtual_script_path) and check_sandbox_path(virtual_script_path, request_id):
            processed_script_path = virtual_script_path
            log_info(f"run: using virtual path script: {processed_script_path}", request_id)
        else:
            real_phys_path = os.path.abspath(input_path)
            if os.path.exists(real_phys_path) and check_sandbox_path(real_phys_path, request_id):
                processed_script_path = real_phys_path
                log_info(f"run: using physical path script: {processed_script_path}", request_id)
            else:
                virtual_path = get_virtual_path(virtual_script_path)
                err_msg = msg["no_such_script"].format(virtual_path, input_path)
                print(Fore.RED + err_msg + Style.RESET_ALL)
                log_error(err_msg, request_id)
                return
    except Exception as e:
        err_msg = msg["path_resolve_fail"].format(str(e))
        print(Fore.RED + err_msg + Style.RESET_ALL)
        log_error(err_msg, request_id)
        return
    
    # 脚本类型校验
    file_ext = os.path.splitext(processed_script_path)[1].lower()
    supported_suffix = SUPPORTED_EXEC_SUFFIXES.get(sys_type, [])
    if file_ext not in supported_suffix:
        err_msg = msg["unsupported_type"].format(file_ext, ", ".join(supported_suffix))
        print(Fore.RED + err_msg + Style.RESET_ALL)
        log_error(err_msg, request_id)
        return
    
    # 构建执行命令
    validated_args = [validate_param_path(arg, request_id) for arg in script_args]
    full_cmd_list = []
    
    if file_ext.endswith(".sh"):
        if sys_type in ["Linux/macOS", "Termux", "SpecialLinux"]:
            full_cmd_list = ["bash", processed_script_path] + validated_args
        else:
            print(Fore.RED + msg["windows_bash_tip"] + Style.RESET_ALL)
            log_error(msg["windows_bash_tip"], request_id)
            return
    elif file_ext in [".py", ".pyc"]:
        full_cmd_list = [PYTHON_EXE, processed_script_path] + validated_args
    else:
        launch_cmd = executable_config["launch_cmd"][sys_type]
        full_cmd_list = [launch_cmd, processed_script_path] + validated_args
    
    # 同步执行（无成功提示，仅输出脚本自身内容）
    full_cmd = shlex.join(full_cmd_list)
    run_cmd_sync(full_cmd, request_id, is_tool=True, tool_perm=3)
