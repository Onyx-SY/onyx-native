"""core/path_ops.py — 路径解析、虚拟路径转换、校验"""

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import AppContext


def get_real_current_dir() -> str:
    """获取当前真实工作目录（物理路径），对齐原生 pwd"""
    return os.getcwd()


def resolve_path(ctx: "AppContext", path: str) -> str:
    """路径解析接口：根据沙箱状态动态决定根目录"""
    effective_root = ctx.ROOT_DIR if ctx._SANDBOX_ENABLED else "/"

    if not hasattr(resolve_path, "_initialized"):
        from lib import resolve_path as resolve_path_lib
        resolve_path_lib.init_resolve_path(effective_root, ctx.USER_HOME_DIR)
        resolve_path._initialized = True
        resolve_path._lib = resolve_path_lib
    else:
        if getattr(resolve_path, "_last_root", None) != effective_root:
            from lib import resolve_path as resolve_path_lib
            resolve_path._lib.init_resolve_path(effective_root, ctx.USER_HOME_DIR)
            resolve_path._last_root = effective_root

    return resolve_path._lib.resolve_path(path)


def validate_param_path(
    ctx: "AppContext",
    param: str,
    request_id: str,
    *,
    check_sandbox_path_fn,
    log_info_fn,
    log_warning_fn,
) -> str:
    """校验参数路径：选项过滤 → resolve_path → 沙箱校验 → 返回结果"""
    if param.startswith('-'):
        log_info_fn(f"参数{param}为选项参数，跳过路径解析", request_id)
        return param

    if not param.startswith(('./', '/', '../', '~/')) and '.' not in param:
        log_info_fn(f"参数{param}不是路径参数，跳过路径解析", request_id)
        return param

    try:
        resolved_path = resolve_path(ctx, param)

        if not ctx._SANDBOX_ENABLED:
            log_info_fn(f"沙箱已禁用，参数{param}解析后直接返回：{resolved_path}", request_id)
            return resolved_path

        if check_sandbox_path_fn(resolved_path, request_id):
            log_info_fn(f"参数路径解析通过：输入={param} → 解析后={resolved_path}", request_id)
            return resolved_path
        else:
            log_warning_fn(f"参数{param}对应路径{resolved_path}触发沙箱拦截，返回空值使命令失败", request_id)
            return ""
    except Exception as e:
        log_warning_fn(f"参数{param}路径解析失败：{str(e)}，返回原始值", request_id)
        return param


def get_physical_path(ctx: "AppContext", virtual_path: str) -> str:
    """虚拟路径 → 物理路径"""
    return resolve_path(ctx, virtual_path)


def get_virtual_path(ctx: "AppContext", physical_path: str) -> str:
    """物理路径 → 虚拟路径"""
    if ctx.OS_OR_TBS == "OS":
        user_abs = os.path.normpath(os.path.realpath(ctx.USER_HOME_DIR))
        phys_abs = os.path.normpath(os.path.realpath(physical_path))
        if phys_abs == user_abs:
            return "~"
        elif phys_abs.startswith(user_abs + os.sep):
            rel_path = os.path.relpath(phys_abs, user_abs)
            rel_path = rel_path.replace(os.sep, "/")
            return f"~/{rel_path}"
        else:
            return physical_path

    # 非 OS 模式
    try:
        phys_abs = os.path.normpath(os.path.realpath(physical_path))
        effective_root = ctx.ROOT_DIR if ctx._SANDBOX_ENABLED else "/"
        root_abs = os.path.normpath(os.path.realpath(effective_root))
        user_abs = os.path.normpath(os.path.realpath(ctx.USER_HOME_DIR))

        if not ctx._SANDBOX_ENABLED:
            if phys_abs == user_abs:
                return "~"
            elif phys_abs.startswith(user_abs + os.sep):
                rel_path = os.path.relpath(phys_abs, user_abs).replace(os.sep, "/")
                return f"~/{rel_path}"
            else:
                return phys_abs

        if phys_abs == root_abs:
            return "/"
        if phys_abs == user_abs:
            return "~"
        if phys_abs.startswith(user_abs + os.sep):
            rel_path = os.path.relpath(phys_abs, user_abs).replace(os.sep, "/")
            return f"~/{rel_path}" if rel_path != "." else "~"
        if phys_abs.startswith(root_abs + os.sep):
            rel_path = os.path.relpath(phys_abs, root_abs).replace(os.sep, "/")
            return f"/{rel_path}" if rel_path != "." else "/"

        return phys_abs
    except Exception:
        return phys_abs if 'phys_abs' in locals() else "/ERROR"


def format_virtual_path(virtual_path: str, max_len: int = 15) -> str:
    """缩短超长虚拟路径（>15字符），保留核心路径格式"""
    if virtual_path in ["/", "~", "/Not in virtual path", "/（路径异常）"]:
        return virtual_path
    if len(virtual_path) <= max_len:
        return virtual_path

    path_parts = [p for p in virtual_path.split("/") if p.strip()]
    if not path_parts:
        return virtual_path
    last_part = path_parts[-1]
    if len(path_parts) >= 2:
        shortened = f".../{path_parts[-2]}/{last_part}"
        if len(shortened) <= max_len:
            return shortened
    return f".../{last_part}"


def replace_virtual_path_in_cmd(cmd: str, path_resolver) -> str:
    """替换命令中的虚拟路径为物理路径"""
    from lib.parse import resolve_paths_in_multiline_text
    return resolve_paths_in_multiline_text(cmd, path_resolver)
