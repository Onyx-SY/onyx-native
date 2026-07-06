"""core/tool_registry.py — 工具索引、查找、执行、配置"""

import os
import shlex
import uuid
import threading
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext, ToolInfo


def is_cli_tool(tool_config: Dict[str, str]) -> bool:
    """判断工具是否为 CLI 类型"""
    try:
        cli_type = int(tool_config["cli"])
        return cli_type in [1, 2]
    except Exception:
        return True


def _get_sys_specific(ctx: "AppContext"):
    return ctx.global_config.get("sys_specific", {})


def load_tool_config(ctx: "AppContext", tool_dir: str) -> Dict[str, str]:
    """加载工具配置文件"""
    from core.log_manager import log_info, log_error
    ss = _get_sys_specific(ctx)
    config_file = os.path.join(tool_dir, ss.get("tool_config_rule", {}).get("config_file", "config.conf"))
    config_fields = ss.get("tool_config_rule", {}).get("fields", {})

    tool_config = {
        "author": "未知", "name": os.path.basename(tool_dir),
        "version": "1.0.0", "cli": "1", "type": "other",
        "introduction": "无介绍", "main": ""
    }

    if not os.path.exists(config_file):
        try:
            with open(config_file, "w", encoding="utf-8") as f:
                for key, prefix in config_fields.items():
                    if key == "introduction":
                        f.write(f'{prefix}"{tool_config[key]}"\n')
                    else:
                        f.write(f"{prefix}{tool_config[key]}\n")
                if "main" not in config_fields:
                    f.write(f"#main=\n")
            if ctx.sys_type in ["Linux/macOS", "Termux", "SpecialLinux"]:
                os.chmod(config_file, 0o644)
            log_info(f"工具配置文件创建：{config_file}", str(uuid.uuid4()))
        except Exception as e:
            log_error(f"工具配置文件创建失败：{str(e)}", str(uuid.uuid4()))
        return tool_config

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        for line in lines:
            matched = False
            for key, prefix in config_fields.items():
                if line.startswith(prefix):
                    value = line[len(prefix):].strip().strip('"').strip("'")
                    tool_config[key] = value
                    matched = True
                    break
            if not matched and line.startswith("#main="):
                tool_config["main"] = line[len("#main="):].strip().strip('"').strip("'")
    except Exception as e:
        log_error(f"工具配置加载失败：{str(e)}", str(uuid.uuid4()))

    return tool_config


def get_tool_permission_from_dir(ctx: "AppContext", tool_dir: str) -> int:
    """从工具目录读取权限等级"""
    from core.log_manager import log_info, log_error
    ss = _get_sys_specific(ctx)
    perm_file = os.path.join(tool_dir, ss.get("perm_file", ".perm"))
    try:
        if os.path.exists(perm_file):
            with open(perm_file, "r", encoding="utf-8") as f:
                perm = int(f.read().strip())
            return max(1, min(perm, 5))
        else:
            with open(perm_file, "w", encoding="utf-8") as f:
                f.write("3")
            if ctx.sys_type in ["Linux/macOS", "Termux", "SpecialLinux"]:
                os.chmod(perm_file, 0o600)
            return 3
    except Exception as e:
        log_error(f"读取工具权限失败：{str(e)}", str(uuid.uuid4()))
        return 3


def _get_tool_type(tool_name: str) -> str:
    """推断工具类型"""
    tool_type_map = {
        "scan": ["nmap", "arp-scan", "zaproxy", "scan"],
        "crack": ["hydra", "hashcat", "john", "crack"],
        "exploit": ["msfconsole", "sqlmap", "exploit"],
        "wireless": ["aircrack-ng", "wireless"],
        "web": ["burpsuite", "web"],
        "app": ["app", "应用", "desktop"],
        "web_app": ["web", "网页", "server"]
    }
    for tool_type, keywords in tool_type_map.items():
        if any(keyword in tool_name.lower() for keyword in keywords):
            return tool_type
    return "other"


def _find_tool_entry(ctx: "AppContext", tool_dir: str, files: List[str]) -> Optional[str]:
    """查找工具入口文件"""
    from core.log_manager import log_info
    tool_config = load_tool_config(ctx, tool_dir)
    main_file_config = tool_config.get("main", "").strip()
    if main_file_config:
        main_file_abs = os.path.join(tool_dir, main_file_config)
        if os.path.exists(main_file_abs) and os.path.isfile(main_file_abs):
            return main_file_config
        if main_file_config.endswith('.py'):
            pyc = main_file_config + 'c'
            if os.path.exists(os.path.join(tool_dir, pyc)):
                return pyc
        if main_file_config.endswith('.pyc'):
            py = main_file_config[:-1]
            if os.path.exists(os.path.join(tool_dir, py)):
                return py

    tool_dir_name = os.path.basename(tool_dir)
    sys_suffixes = ctx.SUPPORTED_EXEC_SUFFIXES.get(ctx.sys_type, [])
    fallback_suffixes = ['.py', '.pyc'] + sys_suffixes

    for file in files:
        file_base = os.path.splitext(file)[0].lower()
        file_ext = os.path.splitext(file)[1].lower()
        if file_base == tool_dir_name.lower() and file_ext in fallback_suffixes:
            return file

    for main_file in ctx.SUPPORTED_MAIN_FILES:
        if main_file in files:
            return main_file
        if main_file.endswith('.py') and main_file + 'c' in files:
            return main_file + 'c'

    for file in files:
        file_base = os.path.splitext(file)[0].lower()
        file_ext = os.path.splitext(file)[1].lower()
        if any(kw in file_base for kw in ctx.MAIN_FILE_KEYWORDS) and file_ext in fallback_suffixes:
            return file

    fallback = [f for f in files if os.path.splitext(f)[1].lower() in fallback_suffixes]
    return sorted(fallback)[0] if fallback else None


def find_tool(ctx: "AppContext", tool_name: str, request_id: str) -> "Optional[ToolInfo]":
    """查找工具，返回 ToolInfo 或 None"""
    from core.log_manager import log_error
    from core.context import ToolInfo

    if not tool_name:
        log_error("工具名为空，查找失败", request_id)
        return None

    ss = _get_sys_specific(ctx)

    # SpecialLinux 预装工具
    if ctx.sys_type == "SpecialLinux":
        preinstall = ss.get("preinstall_tools", {})
        if tool_name in preinstall:
            tool_path = preinstall[tool_name]
            if os.path.exists(tool_path):
                ttype = _get_tool_type(tool_name)
                cache_key = f"sys_{tool_name.lower()}_{ttype}"
                ctx.TOOL_INDEX_CACHE[cache_key] = ToolInfo(tool_path, is_cli=True, tool_perm=5, tool_type=ttype)
                return ctx.TOOL_INDEX_CACHE[cache_key]
            else:
                print(ctx.Fore.RED + f"系统预装工具不存在: {tool_path}" + ctx.Style.RESET_ALL)

    cache_key = f"{tool_name.lower()}_{ctx.sys_type}" if ctx.sys_type == "Windows" else f"{tool_name}_{ctx.sys_type}"
    if cache_key in ctx.TOOL_INDEX_CACHE:
        info = ctx.TOOL_INDEX_CACHE[cache_key]
        if os.path.exists(info.path):
            return info
        else:
            del ctx.TOOL_INDEX_CACHE[cache_key]

    for root, dirs, files in os.walk(ctx.TOOL_MAIN_DIR):
        rel = os.path.relpath(root, ctx.TOOL_MAIN_DIR)
        if rel == '.':
            continue
        depth = len(rel.split(os.sep))
        if depth != 2:
            if depth >= 2:
                dirs[:] = []
            continue

        dir_name = os.path.basename(root)
        if dir_name.startswith("."):
            continue

        match = (dir_name.lower() == tool_name.lower()) if ctx.sys_type == "Windows" else (dir_name == tool_name)
        if match:
            target = _find_tool_entry(ctx, root, files)
            if target:
                tool_path = os.path.join(root, target)
                tc = load_tool_config(ctx, root)
                tool_perm = get_tool_permission_from_dir(ctx, root)
                ctx.TOOL_INDEX_CACHE[cache_key] = ToolInfo(
                    tool_path, is_cli_tool(tc), tool_perm, tc["type"])
                return ctx.TOOL_INDEX_CACHE[cache_key]

    return None


def find_similar_tools(ctx: "AppContext", wrong_cmd: str) -> List[Tuple[str, str]]:
    """模糊查找相似工具"""
    similar = []
    for _key, info in ctx.TOOL_INDEX_CACHE.items():
        tname = os.path.basename(os.path.dirname(info.path))
        if wrong_cmd.lower() in tname.lower():
            similar.append((tname, info.tool_type))
    ss = _get_sys_specific(ctx)
    if ctx.sys_type == "SpecialLinux":
        for tool_key, tool_path in ss.get("preinstall_tools", {}).items():
            if wrong_cmd.lower() in tool_key.lower():
                alias_name = ss.get("quick_alias", {}).get(tool_key.replace("_tool", ""), tool_key)
                similar.append((alias_name, "system_preinstall"))
    return similar


def find_similar_cmds(ctx: "AppContext", wrong_cmd: str) -> List[str]:
    """模糊查找相似系统命令"""
    similar = []
    for cmd in ctx.current_sys_cmds.get(ctx.sys_type, []):
        if wrong_cmd.lower() in cmd.lower():
            similar.append(cmd)
    return similar


def execute_tool(ctx: "AppContext", tool_info: "ToolInfo", args: List[str], request_id: str) -> None:
    """执行工具：路径校验 → 权限校验 → 参数处理 → 同步执行"""
    from core.log_manager import log_error, log_warning, security_log
    from core.security import check_tool_permission, check_sandbox_path
    from core.path_ops import validate_param_path

    from core.i18n import t, set_lang
    set_lang(ctx.global_config["display_info"]["language"]["current"])

    # 1. 沙箱路径校验
    tool_phys = os.path.abspath(tool_info.path)
    root = os.path.abspath(ctx.ROOT_DIR)
    if not (tool_phys == root or tool_phys.startswith(root + os.sep)):
        err = t("tool_registry.execute_tool.sandbox_block_tool", path=tool_info.path)
        print(ctx.Fore.RED + err + ctx.Style.RESET_ALL)
        log_error(err, request_id)
        security_log(err, "PATH_BLOCK", request_id)
        return

    # 2. 权限校验
    if not check_tool_permission(ctx, tool_info.tool_perm):
        return

    # 3. 参数处理
    resolved_args = []
    for arg in args:
        processed = validate_param_path(ctx, arg, request_id,
                                        check_sandbox_path_fn=check_sandbox_path,
                                        log_info_fn=lambda m, r: None,
                                        log_warning_fn=log_warning)
        if not processed.startswith('-') and os.path.exists(processed):
            if check_sandbox_path(ctx, processed, request_id):
                resolved_args.append(processed)
            else:
                resolved_args.append(arg)
                log_warning(f"参数{arg}对应路径触发沙箱拦截" if current_lang == "chinese" else f"Path arg {arg} blocked", request_id)
        else:
            resolved_args.append(processed)

    # 4. 构建执行命令
    cmd_args = shlex.join(resolved_args) if resolved_args else ""
    if tool_info.path.endswith((".py", ".pyc")):
        full_cmd = f'{ctx.PYTHON_EXE} "{tool_info.path}" {cmd_args}'
    else:
        launch_cmd = ctx.executable_config.get("launch_cmd", {}).get(ctx.sys_type, "./")
        if ctx.sys_type == "Windows":
            full_cmd = f'{launch_cmd}"{tool_info.path}" {cmd_args}'
        else:
            full_cmd = f'{launch_cmd} {tool_info.path} {cmd_args}'

    # 5. 进程数限制
    with ctx.process_lock:
        if len(ctx.CURRENT_PROCESSES) >= ctx.SANDBOX_CONFIG.get("max_process_count", 10):
            err = t("tool_registry.execute_tool.process_limit", max=ctx.SANDBOX_CONFIG["max_process_count"])
            print(ctx.Fore.RED + err + ctx.Style.RESET_ALL)
            log_error(err, request_id)
            return

    # 6. 同步执行
    from lib.terminal.exe import run_cmd_sync as _run_cmd_sync
    _run_cmd_sync(full_cmd, request_id, is_tool=True, tool_perm=tool_info.tool_perm)


def build_tool_index(ctx: "AppContext", request_id: str) -> None:
    """构建工具索引（异步非阻塞）"""
    from core.log_manager import log_info, log_error, log_warning
    from lib.build_tool_index import build_tool_index as _build, init_tool_index, is_building

    if not ctx.TOOL_INDEX_MSG_PATH:
        log_warning("TOOL_INDEX_MSG_PATH 未初始化", request_id)
        return
    if not ctx.ROOT_DIR or not ctx.USER_HOME_DIR or not ctx.TOOL_MAIN_DIR:
        log_warning("必要路径变量未初始化", request_id)
        return

    tool_dict = {}
    for key, info in ctx.TOOL_INDEX_CACHE.items():
        tool_dict[key] = {"path": info.path, "is_cli": info.is_cli, "tool_perm": info.tool_perm, "tool_type": info.tool_type}

    try:
        init_tool_index(
            root_dir=ctx.ROOT_DIR, user_home_dir=ctx.USER_HOME_DIR,
            sys_type=ctx.sys_type, tool_main_dir=ctx.TOOL_MAIN_DIR,
            cache_path=ctx.TOOL_INDEX_MSG_PATH, log_info_func=log_info, request_id=request_id)
    except Exception as e:
        log_error(f"工具索引模块初始化失败：{str(e)}", request_id)
        return

    if is_building():
        return

    def bg_build():
        from core.context import ToolInfo
        try:
            result = _build(
                root_dir=ctx.ROOT_DIR, user_home_dir=ctx.USER_HOME_DIR,
                sys_type=ctx.sys_type, tool_main_dir=ctx.TOOL_MAIN_DIR,
                cache_path=ctx.TOOL_INDEX_MSG_PATH, log_info_func=log_info,
                log_error_func=log_error, request_id=request_id, force_rebuild=False)
            new_tools = {}
            for key, info in result.items():
                new_tools[key] = ToolInfo(
                    path=info.get('path', ''), is_cli=info.get('is_cli', True),
                    tool_perm=info.get('tool_perm', 3), tool_type=info.get('tool_type', 'other'))
            ctx.TOOL_INDEX_CACHE.clear()
            ctx.TOOL_INDEX_CACHE.update(new_tools)
            log_info(f"后台工具索引构建完成：共{len(new_tools)}个工具", request_id)
        except Exception as e:
            log_error(f"后台工具索引构建失败：{str(e)}", request_id)

    if ctx.TOOL_INDEX_CACHE:
        log_info(f"使用缓存的工具索引：共{len(ctx.TOOL_INDEX_CACHE)}个工具", request_id)
        t = threading.Thread(target=bg_build, daemon=True)
        t.start()
    else:
        log_info("无缓存，启动工具索引构建...", request_id)
        t = threading.Thread(target=bg_build, daemon=True)
        t.start()
