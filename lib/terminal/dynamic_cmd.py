# lib/terminal/dynamic_cmd.py
"""
动态命令补全引擎（Dynamic Command Completion Engine）

让用户通过写 Python 脚本，为任意命令提供「动态补全能力」：
    - git 分支 / tag / remote / commit hash
    - docker 容器 / 镜像
    - kubectl pod / namespace / resource
    - 任何需要实时查询的补全

脚本发现路径（优先级从高到低）：
    1. <cwd>/cmd_com/*.py            （随项目）
    2. <virtual_root>/cmd_com/*.py   （Onyx 虚拟根）
    3. ~/.cmd_com/*.py               （用户自定义，可覆盖内置）
    4. <lib/terminal>/cmd_com/*.py   （内置兜底，始终可用）
    5. <pkg_root>/cmd_com/*.py       （兼容：项目根下的用户脚本）

Python 脚本接口（三选一，按顺序探测）：

  接口 A —— 显式 register（推荐）：
      def register(reg):
          reg.register("git", completer=git_completer, description="...")

  接口 B —— 模块级 COMMANDS 字典：
      COMMANDS = {"git": {"completer": git_completer}}

  接口 C —— 模块级 COMPLETERS 简写：
      COMPLETERS = {"git": git_completer}

补全器签名：
      def git_completer(ctx: CompletionContext) -> Iterable[Any]

  返回值可以是：
      - str                                → 文本
      - (text, meta)                       → 文本 + 元信息
      - (text, meta, style)                → + ANSI 样式
      - (text, meta, style, start_position)
      - CompletionItem(...)                → 完整
      - prompt_toolkit 的 Completion 对象  → 原样透传

安全 / 兼容性：
    - 所有脚本在独立命名空间 import，任何异常都被吞掉
    - shell 插件只做「探测 + 登记」，不主动执行（避免污染用户环境）
      如需启用 shell 补全调用，设 ONYX_SHELL_COMPLETION=1

抽象设计（动态优先 · 脚本完全接管）：
    - com.py 只负责「有脚本 → 调脚本 → 原样透传结果」，不做任何兜底
    - 是否要合并静态树、是否要路径兜底，由脚本自己决定：
        · 用 @static_fallback 装饰器   → 空结果时自动回退静态树
        · 用 @merge_static 装饰器     → 动态结果 + 静态树合并（动态优先）
        · 直接调 ctx.static_candidates() → 手工取静态树候选
        · 什么都不用 → 完全接管，返回空就是空
    - 新增任何命令语义，只需写脚本，com.py 永不需要修改
"""

import os
import re
import sys
import time
import shutil
import hashlib
import threading
import subprocess
import traceback
import importlib.util
from dataclasses import dataclass, field
from typing import (
    List, Dict, Any, Optional, Callable, Iterable, Tuple, Union
)


# ============================================================
# 数据对象
# ============================================================

@dataclass
class CompletionItem:
    """补全结果的统一表示。

    start_position = None → 由上层使用「当前词起始位置」
    start_position = 整数 → 直接使用该相对偏移（负数表示向前）
    """
    text: str
    meta: str = ""
    style: str = ""
    start_position: Optional[int] = None


@dataclass
class CompletionContext:
    """传给动态补全器的上下文。

    除了命令/参数/当前词外，还注入了「静态树数据源」：
    - static_node        当前层级的 CommandNode（或 None）
    - static_candidates() 主动取静态树的候选列表 [(text, meta, style)]
    - static_yield()     直接 yield 静态树的候选（语法糖）

    脚本可以：
      1) 完全无视静态树（不调用上述方法）→ 动态完全接管
      2) 主动合并（ctx.static_candidates()）
      3) 用装饰器自动合并 / 自动兜底（static_fallback / merge_static）
    """
    cmd: str = ""                            # 命令名 (e.g. "git")
    args: List[str] = field(default_factory=list)   # 命令名之后的已完成参数
    current: str = ""                        # 正在补全的当前词（可能为空）
    raw: str = ""                            # 完整命令行文本（光标前）
    cwd: str = ""                            # 当前工作目录
    virtual_root: str = ""                   # 虚拟根目录

    # ── 静态树数据源（由 com.py 注入，脚本可选使用）──
    static_node: Any = None                          # 当前层级的 CommandNode（或 None）
    _static_provider: Optional[Callable] = None      # (node, current) -> [(text, meta, style)]

    @property
    def arg_index(self) -> int:
        """当前补全的是第几个参数（从 0 计）"""
        return len(self.args)

    @property
    def prev(self) -> str:
        """上一个已完成参数（用于上下文判断）"""
        return self.args[-1] if self.args else ""

    def is_first_arg(self) -> bool:
        return not self.args and not self.current.startswith('-')

    def static_candidates(self) -> List[Tuple[str, str, str]]:
        """主动取用静态树的候选列表 [(text, meta, style)]。

        脚本想合并静态树（而不是完全绕过它）时调用。
        返回空列表表示静态树没有可用信息。
        """
        if self._static_provider is None or self.static_node is None:
            return []
        try:
            return list(self._static_provider(self.static_node, self.current))
        except Exception:
            return []

    def static_yield(self):
        """语法糖：把 static_candidates() 转成 CompletionItem 直接 yield。"""
        for text, meta, style in self.static_candidates():
            yield CompletionItem(text=text, meta=meta, style=style)


# ============================================================
# 通用工具（供动态脚本 from lib.terminal.dynamic_cmd import ...）
# ============================================================

_cache: Dict[str, Tuple[Any, float]] = {}
_cache_lock = threading.RLock()


def cached(key: str, ttl: float, producer: Callable[[], Any]) -> Any:
    """带 TTL 的进程内缓存。异常时返回空列表。"""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            value, ts = hit
            if now - ts < ttl:
                return value
    try:
        value = producer()
    except Exception:
        value = []
    with _cache_lock:
        _cache[key] = (value, now)
    return value


def run_command(cmd: List[str], timeout: float = 2.0) -> List[str]:
    """运行外部命令并返回按行拆分的 stdout；失败/超时返回 []。"""
    if not cmd:
        return []
    if not shutil.which(cmd[0]):
        return []
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def prefix_match(current: str, candidate: str) -> bool:
    """大小写不敏感的前缀匹配（current 为空时永远 True）。"""
    if not current:
        return True
    return candidate.lower().startswith(current.lower())


# ============================================================
# 归一化
# ============================================================

def _normalize_completions(raw: Iterable[Any]) -> Iterable[CompletionItem]:
    """把补全器返回的各种形态统一成 CompletionItem。"""
    for item in raw:
        if item is None:
            continue
        if isinstance(item, CompletionItem):
            yield item
        elif isinstance(item, str):
            yield CompletionItem(text=item)
        elif isinstance(item, tuple):
            if not item:
                continue
            text = str(item[0])
            meta = str(item[1]) if len(item) >= 2 and item[1] is not None else ""
            style = str(item[2]) if len(item) >= 3 and item[2] is not None else ""
            sp: Optional[int] = None
            if len(item) >= 4 and isinstance(item[3], int):
                sp = item[3]
            yield CompletionItem(text=text, meta=meta, style=style, start_position=sp)
        else:
            # 尝试 prompt_toolkit 的 Completion
            text = getattr(item, "text", None)
            if not text:
                continue
            meta = ""
            try:
                m = getattr(item, "display_meta", None)
                if m is not None:
                    meta = getattr(m, "text", None) or str(m)
            except Exception:
                pass
            sp_raw = getattr(item, "start_position", None)
            sp = int(sp_raw) if isinstance(sp_raw, int) else None
            style = getattr(item, "style", "") or ""
            yield CompletionItem(text=str(text), meta=meta, style=str(style),
                                 start_position=sp)


# ============================================================
# 装饰器：让脚本声明「要不要兜底 / 要不要合并静态树」
# ============================================================

def static_fallback(fn: Callable[[CompletionContext], Iterable[Any]]):
    """装饰器：动态补全器返回空时，自动回退到静态树的候选。

    用法：
        @static_fallback
        def some_completer(ctx):
            if ctx.args and ctx.args[0] == "known":
                yield CompletionItem("foo", meta="known")
            # 其它情况什么都不 yield → 装饰器补上静态树候选
    """
    def wrapper(ctx: CompletionContext):
        produced = False
        try:
            for item in fn(ctx) or []:
                produced = True
                yield item
        finally:
            if not produced:
                yield from ctx.static_yield()
    return wrapper


def merge_static(fn: Callable[[CompletionContext], Iterable[Any]]):
    """装饰器：动态结果 + 静态树候选合并（动态优先、按 text 去重）。

    用法：
        @merge_static
        def git_completer(ctx):
            ...  # 自己的产出会排在前面
    """
    def wrapper(ctx: CompletionContext):
        seen: set = set()
        for item in fn(ctx) or []:
            t = None
            if isinstance(item, CompletionItem):
                t = item.text
            elif isinstance(item, str):
                t = item
            elif isinstance(item, tuple) and item:
                t = str(item[0])
            else:
                t = getattr(item, "text", None)
            if t:
                if t in seen:
                    continue
                seen.add(t)
            yield item
        for text, meta, style in ctx.static_candidates():
            if text in seen:
                continue
            seen.add(text)
            yield CompletionItem(text=text, meta=meta, style=style)
    return wrapper


# ============================================================
# 注册表
# ============================================================

class DynamicCommandRegistry:
    """存储所有动态命令补全器，并提供查询接口。"""

    def __init__(self):
        self._completers: Dict[str, Callable[[CompletionContext], Iterable[Any]]] = {}
        self._scripts_loaded: List[str] = []
        self._failed: List[Tuple[str, str]] = []
        self._lock = threading.RLock()

    # ---- 注册 API（给脚本调用）----
    def register(self, cmd: str,
                 completer: Callable[[CompletionContext], Iterable[Any]],
                 description: str = "") -> None:
        if not cmd or not callable(completer):
            return
        with self._lock:
            self._completers[cmd] = completer

    def unregister(self, cmd: str) -> None:
        with self._lock:
            self._completers.pop(cmd, None)

    # ---- 查询 ----
    def has(self, cmd: str) -> bool:
        with self._lock:
            return cmd in self._completers

    def get(self, cmd: str) -> Optional[Callable]:
        with self._lock:
            return self._completers.get(cmd)

    def commands(self) -> List[str]:
        with self._lock:
            return list(self._completers.keys())

    def complete(self, ctx: CompletionContext) -> List[CompletionItem]:
        fn = self.get(ctx.cmd)
        if fn is None:
            return []
        try:
            raw = fn(ctx)
        except Exception:
            return []
        if raw is None:
            return []
        try:
            return list(_normalize_completions(raw))
        except Exception:
            return []

    # ---- 发现 ----
    def discover(self, paths: Iterable[str]) -> None:
        seen = set()
        for path in paths:
            if not path or path in seen:
                continue
            seen.add(path)
            self._load_from_dir(path)

    def _load_from_dir(self, dir_path: str) -> None:
        if not dir_path or not os.path.isdir(dir_path):
            return
        try:
            entries = sorted(os.listdir(dir_path))
        except OSError:
            return
        for name in entries:
            if not name.endswith(".py") or name.startswith("_"):
                continue
            self._load_script(os.path.join(dir_path, name))

    def _load_script(self, script_path: str) -> None:
        if not os.path.isfile(script_path):
            return
        base = os.path.splitext(os.path.basename(script_path))[0]
        digest = hashlib.md5(script_path.encode("utf-8")).hexdigest()[:8]
        mod_name = f"onyx_dyn_{base}_{digest}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, script_path)
            if spec is None or spec.loader is None:
                return
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)

            registered_any = False

            register_fn = getattr(mod, "register", None)
            if callable(register_fn):
                register_fn(self)
                registered_any = True

            commands = getattr(mod, "COMMANDS", None)
            if isinstance(commands, dict):
                for cmd_name, spec_dict in commands.items():
                    if isinstance(spec_dict, dict):
                        fn = spec_dict.get("completer")
                        if callable(fn):
                            self.register(cmd_name, fn,
                                          spec_dict.get("description", ""))
                            registered_any = True

            completers = getattr(mod, "COMPLETERS", None)
            if isinstance(completers, dict):
                for cmd_name, fn in completers.items():
                    if callable(fn):
                        self.register(cmd_name, fn)
                        registered_any = True

            if registered_any:
                self._scripts_loaded.append(script_path)
        except Exception as e:
            self._failed.append((script_path, f"{type(e).__name__}: {e}"))

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "commands": list(self._completers.keys()),
                "scripts_loaded": list(self._scripts_loaded),
                "failed": list(self._failed),
            }


# ============================================================
# Shell 插件适配器（bash / zsh / fish）
# ============================================================

class ShellCompletionAdapter:
    """探测并登记 bash/zsh/fish 补全脚本。

    默认只做「探测 + 登记」——完整的 shell 补全脚本执行涉及
    环境依赖与安全风险，不能盲目 source 进来。需要主动启用时
    设置 ONYX_SHELL_COMPLETION=1 才会走 subprocess 调用路径。

    支持的探测语法：
        bash:   complete -[A-Za-z]*F _func cmd
        zsh:    compdef _func cmd
        fish:   complete -c cmd
    """

    SHELL_EXTS = (".bash", ".zsh", ".fish", ".sh")

    _BASH_RE = re.compile(r"complete\s+-\w*F\s+(\S+)\s+(\S+)")
    _ZSH_RE = re.compile(r"compdef\s+(\S+)\s+(\S+)")
    _FISH_RE = re.compile(r"complete\s+-c\s+(\S+)")

    def __init__(self):
        self.discovered: Dict[str, Dict[str, str]] = {}
        self.enabled = os.environ.get("ONYX_SHELL_COMPLETION", "") == "1"
        self._lock = threading.RLock()

    def discover(self, dirs: Iterable[str]) -> None:
        for d in dirs:
            if d and os.path.isdir(d):
                self._scan_dir(d)

    def _scan_dir(self, dir_path: str) -> None:
        try:
            for name in os.listdir(dir_path):
                path = os.path.join(dir_path, name)
                if not os.path.isfile(path):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext in self.SHELL_EXTS:
                    self._scan_script(path)
        except OSError:
            return

    def _scan_script(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            return
        with self._lock:
            for m in self._BASH_RE.finditer(content):
                self.discovered.setdefault(m.group(2), {
                    "shell": "bash", "func": m.group(1), "path": path,
                })
            for m in self._ZSH_RE.finditer(content):
                self.discovered.setdefault(m.group(2), {
                    "shell": "zsh", "func": m.group(1), "path": path,
                })
            for m in self._FISH_RE.finditer(content):
                self.discovered.setdefault(m.group(1), {
                    "shell": "fish", "func": "", "path": path,
                })

    def has(self, cmd: str) -> bool:
        return cmd in self.discovered

    def commands(self) -> List[str]:
        return list(self.discovered.keys())

    def try_complete(self, ctx: CompletionContext) -> List[CompletionItem]:
        """可选：真正调用 shell 补全器（默认关闭）。

        启用条件：ONYX_SHELL_COMPLETION=1，且当前命令存在登记项。
        """
        if not self.enabled or not self.has(ctx.cmd):
            return []
        info = self.discovered.get(ctx.cmd) or {}
        shell = info.get("shell", "")
        func = info.get("func", "")
        path = info.get("path", "")
        if not (shell and path and func):
            return []
        try:
            if shell == "bash":
                return self._run_bash(path, func, ctx)
            if shell == "zsh":
                return self._run_zsh(path, func, ctx)
            if shell == "fish":
                return self._run_fish(path, ctx)
        except Exception:
            return []
        return []

    def _run_bash(self, script: str, func: str,
                  ctx: CompletionContext) -> List[CompletionItem]:
        words = [ctx.cmd] + list(ctx.args) + [ctx.current]
        script_body = (
            'source "$1" >/dev/null 2>&1; '
            'shift; '
            'COMP_WORDS=("$@"); '
            'COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 )); '
            f'{func} 2>/dev/null; '
            'for x in "${COMPREPLY[@]}"; do printf "%s\\n" "$x"; done'
        )
        try:
            result = subprocess.run(
                ["bash", "--norc", "-c", script_body, "bash", script, *words],
                capture_output=True, text=True, timeout=3.0, check=False,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []
        return [CompletionItem(text=line, meta="shell")
                for line in result.stdout.splitlines() if line.strip()]

    def _run_zsh(self, script: str, func: str,
                 ctx: CompletionContext) -> List[CompletionItem]:
        words = [ctx.cmd] + list(ctx.args) + [ctx.current]
        script_body = (
            'autoload -Uz compinit && compinit -u >/dev/null 2>&1; '
            'source "$1" >/dev/null 2>&1; shift; '
            'words=("$@"); '
            'CURRENT=$(( ${#words[@]} )); '
            f'{func} 2>/dev/null; '
            'for x in "${reply[@]}"; do print -r -- "$x"; done'
        )
        try:
            result = subprocess.run(
                ["zsh", "-f", "-c", script_body, "zsh", script, *words],
                capture_output=True, text=True, timeout=3.0, check=False,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []
        return [CompletionItem(text=line, meta="shell")
                for line in result.stdout.splitlines() if line.strip()]

    def _run_fish(self, script: str,
                  ctx: CompletionContext) -> List[CompletionItem]:
        line = " ".join([ctx.cmd] + list(ctx.args) + [ctx.current]).rstrip()
        script_body = (
            'source "$1" >/dev/null 2>&1; shift; '
            'complete -C "$*"'
        )
        try:
            result = subprocess.run(
                ["fish", "-c", script_body, "fish", script, line],
                capture_output=True, text=True, timeout=3.0, check=False,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []
        return [CompletionItem(text=line, meta="shell")
                for line in result.stdout.splitlines() if line.strip()]


# ============================================================
# 顶层管理器
# ============================================================

class DynamicCommandManager:
    """动态命令补全的统一入口。组合 Python 注册表 + shell 适配器。"""

    def __init__(self):
        self.registry = DynamicCommandRegistry()
        self.shell_adapter = ShellCompletionAdapter()
        self._discovered = False

    def discover(self, paths: Iterable[str]) -> None:
        self.registry.discover(paths)
        self.shell_adapter.discover(paths)
        self._discovered = True

    def has(self, cmd: str) -> bool:
        return self.registry.has(cmd)

    def complete(self, ctx: CompletionContext) -> List[CompletionItem]:
        items = self.registry.complete(ctx)
        if not items and self.shell_adapter.enabled:
            items = self.shell_adapter.try_complete(ctx)
        return items

    def stats(self) -> Dict[str, Any]:
        s = self.registry.stats()
        s["shell_commands"] = self.shell_adapter.commands()
        s["shell_enabled"] = self.shell_adapter.enabled
        return s


# ============================================================
# 默认发现路径
# ============================================================

def default_script_dirs(virtual_root: str = "",
                        user_home_dir: str = "") -> List[str]:
    """返回默认的脚本搜索路径（按优先级从高到低）。

    同时包含 <dir>/cmd_com/*.py 与 <dir>/.cmd_com/*.py 两种命名，
    照顾「用户主目录用 . 隐藏」与「项目内用普通目录」两种习惯。
    """
    raw: List[str] = []

    # 1) 当前工作目录
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = ""
    if cwd:
        raw.append(os.path.join(cwd, "cmd_com"))
        raw.append(os.path.join(cwd, ".cmd_com"))

    # 2) 虚拟根目录
    if virtual_root:
        raw.append(os.path.join(virtual_root, "cmd_com"))
        raw.append(os.path.join(virtual_root, ".cmd_com"))

    # 3) 用户主目录
    home = user_home_dir or os.path.expanduser("~")
    if home:
        raw.append(os.path.join(home, ".cmd_com"))
        raw.append(os.path.join(home, "cmd_com"))

    # 4) 项目内置 —— 保证「跨环境可用」的兜底脚本始终存在
    #    内置脚本就放在 lib/terminal/cmd_com/（与 dynamic_cmd.py 同级），
    #    所以这里是 <here>/cmd_com，而不是 <pkg_root>/cmd_com。
    here = os.path.dirname(os.path.abspath(__file__))          # lib/terminal/
    raw.append(os.path.join(here, "cmd_com"))
    # 兼容：项目根下若另有 cmd_com/ 也一并纳入（用户脚本可放这里）
    pkg_root = os.path.dirname(os.path.dirname(here))          # <项目>/
    raw.append(os.path.join(pkg_root, "cmd_com"))

    # 去重保序
    seen = set()
    result: List[str] = []
    for p in raw:
        if p and p not in seen:
            seen.add(p)
            result.append(p)
    return result


__all__ = [
    "CompletionItem",
    "CompletionContext",
    "DynamicCommandRegistry",
    "DynamicCommandManager",
    "ShellCompletionAdapter",
    "default_script_dirs",
    "cached",
    "run_command",
    "prefix_match",
    "static_fallback",
    "merge_static",
]