# -*- coding: utf-8 -*-
"""统一「输入模块」与「底层 PTY」的 shell 类型。

根因：调用方把 detect_system() 的结果（Termux / Linux/macOS / Windows…）
当 shell 类型传给 lib/parse.py，导致所有 `sys_type == 'bash'` 分支不命中
→ source / [[ ]] / 数组 / $'...' 等 bash 内置被当成未知语法（"解析不明白"）。

做法：在 parse.py 加 normalize_shell_type()（读 ONYX_SHELL / etc/onyx/shell
配置，与 exe.get_shell() 同一来源），并在每个 sys_type 参数函数的入口归一化。
"""
import re
import shutil

P = 'lib/parse.py'
shutil.copy(P, P + '.bak')
s = open(P, encoding='utf-8').read()

ANCHOR = "def smart_shlex_split("
assert s.count(ANCHOR) == 1

HELPER = '''# ── shell 类型归一化（输入模块与 PTY 共用同一来源）──
import os as _os
_SHELL_TYPE_FILE = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "etc", "onyx", "shell")
_KNOWN_SHELL_TYPES = ("bash", "zsh", "fish", "sh", "dash", "ksh", "cmd",
                      "powershell", "pwsh")
# 历史遗留：调用方把 detect_system() 的结果当 shell 类型传进来
_SYSTEM_TO_SHELL = {
    "termux": "bash", "linux": "bash", "linux/macos": "bash",
    "speciallinux": "bash", "macos": "bash", "windows": "cmd",
}


def _read_shell_config() -> str:
    try:
        with open(_SHELL_TYPE_FILE, "r", encoding="utf-8") as _f:
            return _f.read().strip()
    except Exception:
        return ""


def _canon(name: str) -> str:
    n = _os.path.basename(str(name)).strip().lower()
    if n.endswith(".exe"):
        n = n[:-4]
    return "powershell" if n == "pwsh" else n


def get_configured_shell_type() -> str:
    """当前生效的 shell 类型（bash/zsh/fish/...）。

    优先级：ONYX_SHELL 环境变量 → etc/onyx/shell（`manage shell` 写入）
    → 检测到的终端类型 → bash。与 exe.get_shell() 共用同一来源，
    保证「输入模块」与「底层 PTY」用的是同一种 shell。
    """
    for _src in (_os.environ.get("ONYX_SHELL"), _read_shell_config()):
        if not _src:
            continue
        _n = _canon(_src)
        if _n in _KNOWN_SHELL_TYPES:
            return _n
    try:
        from lib.get_terminal_type import get_terminal_type
        _t = _canon(get_terminal_type() or "")
        if _t in _KNOWN_SHELL_TYPES:
            return _t
    except Exception:
        pass
    return "bash"


def normalize_shell_type(sys_type) -> str:
    """把任意「系统类型 / 终端类型 / 空值」归一化为 shell 类型。

    这是 `source` 解析错误的根：调用方长期把 detect_system() 的结果
    （Termux / Linux/macOS / Windows …）当 shell 类型传进来，parse.py 里
    所有 `sys_type == 'bash'` 分支都不命中 → bash 内置被当成未知语法。
    """
    if not sys_type:
        return get_configured_shell_type()
    t = _canon(sys_type)
    if t in _KNOWN_SHELL_TYPES:
        return t
    if t in _SYSTEM_TO_SHELL:
        return _SYSTEM_TO_SHELL[t]
    if "termux" in t or "linux" in t or "macos" in t or "darwin" in t:
        return get_configured_shell_type()
    if "win" in t:
        return "cmd"
    return get_configured_shell_type()


'''

s = s.replace(ANCHOR, HELPER + ANCHOR, 1)

PAT = re.compile(
    r"^(def \w+\([^)]*sys_type: str = 'bash'[^)]*\)(?: -> [^:]+)?:\n)"
    r"(    \"\"\".*?\"\"\"\n)?",
    re.S | re.M)


def _ins(m):
    return m.group(1) + (m.group(2) or "") + \
        "    sys_type = normalize_shell_type(sys_type)\n"


s2, n = PAT.subn(_ins, s)
print('normalized functions:', n)
assert n > 10, n
open(P, 'w', encoding='utf-8').write(s2)
print('PARSE.PY UPDATED')
