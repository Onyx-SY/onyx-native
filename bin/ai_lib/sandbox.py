# -*- coding: utf-8 -*-
"""
AI 虚拟沙盒 — 把虚拟根 / 映射为用户当前 cwd

仿照 lib/resolve_path.py 虚拟路径转换系统，为 AI 的文件工具增加一层沙盒：
  - AI 视角的虚拟根 "/" = 用户启用 AI 时的 cwd
  - AI 传入任何路径（绝对/相对/../~）都先经 resolve() 转物理路径，越界直接拦截
  - 工具输出中的物理路径经 display() 反向映射回虚拟路径，彻底隐藏真实路径

用法：
    from .ai_lib import sandbox
    sandbox.init(cwd, user_home)       # handle_ai 启动时调用
    phys = sandbox.resolve("/foo")     # /foo → <cwd>/foo，越界抛 SandboxBlockError
    virt = sandbox.display("<cwd>/foo")  # → /foo
    sandbox.deactivate()               # AI 会话结束调用（可选）

注意：
  - 仅影响 AI 工具层；普通终端命令（非 AI 场景）不受影响
  - ~ 解析后若超出 cwd 范围同样拦截（用户主目录权限通过 display() 展示，但物理上限制在 cwd 内）
"""

import os
import threading
from typing import List, Optional, Tuple

# ── 沙盒状态（模块级单例，跨工具共享）──
_root: Optional[str] = None       # 物理沙盒根 = cwd
_user_home: Optional[str] = None  # 用户主目录（~ 语义）
_lock = threading.Lock()


class SandboxBlockError(Exception):
    """路径越界拦截异常"""

    def __init__(self, vpath: str, message: str = ""):
        self.vpath = vpath
        self.message = message or (
            f"⛔ 沙箱拦截：路径 '{vpath}' 超出当前工作目录范围（AI 沙盒）"
        )
        super().__init__(self.message)


# ────────────────── 生命周期 ──────────────────

def init(cwd: str = None, user_home: str = None, force: bool = False) -> None:
    """初始化 AI 沙盒。cwd 默认取当前工作目录。

    幂等：已激活时保持原根（AI 会话启动即固定，不随会话内 cd 漂移）；
    传 force=True 可强制重新固定（新 ai 命令入口在 deactivate 后自然重固定）。
    """
    global _root, _user_home
    with _lock:
        if _root is not None and not force:
            return
        _root = os.path.realpath(cwd or os.getcwd())
        _user_home = os.path.realpath(user_home) if user_home else os.path.expanduser("~")


def deactivate() -> None:
    """停用沙盒（恢复普通路径行为）"""
    global _root, _user_home
    with _lock:
        _root = None
        _user_home = None


def is_active() -> bool:
    """沙盒是否已激活"""
    return _root is not None


def get_root() -> Optional[str]:
    """返回物理沙盒根（cwd）"""
    return _root


# ────────────────── 判定 ──────────────────

def is_within(phys_path: str) -> bool:
    """物理路径是否在沙盒根内（== root 或 root 下）"""
    root = _root
    if not root:
        return True
    try:
        p = os.path.normpath(os.path.abspath(phys_path))
    except Exception:
        p = phys_path
    root = os.path.realpath(root)
    return p == root or p.startswith(root + os.sep)


# ────────────────── 虚拟 → 物理 ──────────────────

def resolve(vpath: str) -> str:
    """
    虚拟路径 → 物理路径。

    规则：
      /x    → <root>/x            （虚拟根 = cwd）
      ~/x   → <user_home>/x       （解析后仍须在 root 内，否则拦截）
      x     → <root>/x            （相对路径以 cwd 为基准）
      ./x   → <root>/x
      ../x  → <root>/../x         若结果仍在 root 内放行，否则拦截
      /绝对物理路径 → 一律视为虚拟路径映射到 <root> 下

    沙盒未激活时原样返回（兼容非 AI 场景）。
    """
    if _root is None:
        return vpath
    if not vpath or not isinstance(vpath, str):
        return vpath

    vpath = vpath.strip()
    if not vpath:
        return vpath

    # ~ 映射到用户主目录
    if vpath == "~":
        joined = os.path.join(_user_home or os.path.expanduser("~"), "")
    elif vpath.startswith("~/"):
        joined = os.path.join(_user_home or os.path.expanduser("~"), vpath[2:])
    # 绝对路径（/x）→ 虚拟根映射
    elif vpath.startswith("/"):
        rel = vpath.lstrip("/")
        joined = os.path.join(_root, rel) if rel else _root
    # 相对路径（x、./x、../x）→ 以 cwd 为基准
    else:
        joined = os.path.join(_root, vpath)

    norm = os.path.normpath(joined)
    if not is_within(norm):
        raise SandboxBlockError(vpath)
    return norm


def resolve_many(params: dict, keys: Optional[Tuple[str, ...]] = None) -> None:
    """
    就地转换 params 中路径参数的值（虚拟 → 物理）。
    keys 默认覆盖常见路径参数名；越界时抛 SandboxBlockError。
    """
    if not params or _root is None:
        return
    path_keys = keys or (
        "path", "paths", "source", "destination", "file_path",
        "directory", "dir_path", "target", "file", "dir",
    )
    for key in path_keys:
        val = params.get(key)
        if isinstance(val, str) and val:
            params[key] = resolve(val)
        elif isinstance(val, list):
            new_list = []
            for item in val:
                if isinstance(item, str) and item:
                    new_list.append(resolve(item))
                else:
                    new_list.append(item)
            params[key] = new_list


# ────────────────── 物理 → 虚拟（输出反向映射）──────────────────

def display(phys_path: str) -> str:
    """
    物理路径 → 虚拟路径（输出反向映射）。

      <root>/x      → /x
      <root>        → /
      <user_home>/x → ~/x   （仅当不在 root 内时）
      其他          → 原样返回
    """
    if _root is None:
        return phys_path
    try:
        p = os.path.normpath(os.path.abspath(phys_path))
    except Exception:
        return phys_path

    root = os.path.realpath(_root)

    # 虚拟根优先（root 内一律显示为 /x，保持 AI 视角一致）
    if p == root:
        return "/"
    if p.startswith(root + os.sep):
        rel = os.path.relpath(p, root)
        return "/" + rel.replace(os.sep, "/")

    # 不在 root 内但在用户主目录内 → ~/x
    home = os.path.realpath(_user_home) if _user_home else None
    if home and (p == home or p.startswith(home + os.sep)):
        rel = os.path.relpath(p, home)
        return "~" if rel == "." else ("~/" + rel.replace(os.sep, "/"))

    return phys_path


def display_text(text: str) -> str:
    """
    对多行文本做路径脱敏：把行内出现的物理沙盒根前缀替换为虚拟路径。
    仅供工具输出收尾使用；逐行处理避免误伤超长内容。
    """
    root = _root
    if not root or not text:
        return text
    root = os.path.realpath(root)
    home = os.path.realpath(_user_home) if _user_home else None
    out_lines = []
    for line in text.split("\n"):
        if not line:
            out_lines.append(line)
            continue
        line = line.replace(root, display(root))
        if home:
            line = line.replace(home, "~")
        out_lines.append(line)
    return "\n".join(out_lines)
