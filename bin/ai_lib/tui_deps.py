# -*- coding: utf-8 -*-
"""TUI 依赖自举 —— 确保 Textual 可用（缺失则自动安装）。

用于「AI TUI 模式」所需的动态依赖：主程序启动时（默认模式为 TUI 时）以及
每次进入 TUI 会话前，都会调用 ensure_tui_deps() 保证依赖就绪；安装失败则
安全回退到 REPL 模式。
"""
import importlib
import importlib.util
import subprocess
import sys

_TUI_PACKAGE = "textual"       # import 名
_TUI_PIP_NAME = "textual"      # pip 包名
_INSTALL_TIMEOUT = 300         # 秒


def tui_available() -> bool:
    """Textual 是否可导入（轻量、无副作用）。"""
    try:
        importlib.invalidate_caches()
        return importlib.util.find_spec(_TUI_PACKAGE) is not None
    except Exception:
        return False


def _msg(text: str) -> None:
    try:
        print(text, flush=True)
    except Exception:
        pass


def ensure_tui_deps(auto_install: bool = True, quiet: bool = False) -> bool:
    """确保 TUI 依赖（textual）就绪。

    返回 True 表示可用；False 表示不可用（调用方应回退 REPL）。
    """
    if tui_available():
        return True
    if not auto_install:
        return False

    if not quiet:
        _msg("📦 TUI 模式需要 'textual' 库，正在安装… / TUI mode requires 'textual', installing…")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", _TUI_PIP_NAME],
            capture_output=True, text=True, timeout=_INSTALL_TIMEOUT,
        )
        ok = (proc.returncode == 0)
    except Exception as e:
        ok = False
        if not quiet:
            _msg(f"❌ 安装异常：{e}")

    if ok and tui_available():
        if not quiet:
            _msg("✅ 'textual' 安装成功 / installed successfully")
        return True

    if not quiet:
        _msg("❌ 'textual' 安装失败，TUI 模式不可用（可手动 `pip install textual`）。已回退 REPL。")
    return False


def ensure_tui_deps_at_startup(quiet: bool = True) -> bool:
    """启动钩子：仅在默认模式为 TUI 时才确保依赖（避免 REPL 用户被强行安装）。

    返回 TUI 依赖是否可用。
    """
    try:
        from bin.ai_lib.mode import resolve_ai_mode
        if resolve_ai_mode() != "tui":
            return tui_available()
    except Exception:
        pass
    return ensure_tui_deps(quiet=quiet)
