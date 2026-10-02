# -*- coding: utf-8 -*-
"""修 shebang 脚本执行：解释器路径被虚拟路径解析判为「越界」→ 替换成
拦截提示 → `bash: You cannot cross root dir. Onyx has intercepted.: command not found`。

修法：resolve_path() 对「系统可执行目录」下的绝对路径直接放行，不做虚拟路径映射。
这些是 shebang 解释器（/usr/bin/env、$PREFIX/bin/bash…）与系统命令，本就不属于虚拟根。
"""
import shutil

P = 'Onyx.py'
shutil.copy(P, P + '.bak')
s = open(P, encoding='utf-8').read()

OLD = '''def resolve_path(path: str) -> str:
    """路径解析接口：根据 _SANDBOX_ENABLED 动态决定根目录（若未启用沙箱则使用 /）"""'''

NEW = '''_SYSTEM_BIN_PREFIXES = None


def _is_system_bin_path(p: str) -> bool:
    """判断是否是「系统可执行目录」下的路径（shebang 解释器 / 系统命令）。

    这些路径不属于虚拟根，若交给虚拟路径解析会被判为越界并替换成
    「You cannot cross root dir. Onyx has intercepted.」，从而让
    `./script.sh`（带 shebang）报 "…: command not found"。
    """
    global _SYSTEM_BIN_PREFIXES
    if _SYSTEM_BIN_PREFIXES is None:
        _pfx = []
        for _d in (os.environ.get('PREFIX', ''), '/usr', '/bin', '/sbin',
                   '/opt', '/system', '/apex'):
            if _d:
                _pfx.append(_d.rstrip('/') + '/')
        _SYSTEM_BIN_PREFIXES = tuple(_pfx)
    return p.startswith(_SYSTEM_BIN_PREFIXES)


def resolve_path(path: str) -> str:
    """路径解析接口：根据 _SANDBOX_ENABLED 动态决定根目录（若未启用沙箱则使用 /）"""
    # ── 系统可执行目录下的路径不做虚拟路径映射 ──
    # shebang 解释器（/usr/bin/env、$PREFIX/bin/bash …）与系统命令不属于
    # 虚拟根；硬解析会被判越界并替换成拦截提示 → 脚本执行报 command not found。
    try:
        if path and os.path.isabs(path) and _is_system_bin_path(path):
            return path
    except Exception:
        pass'''

assert s.count(OLD) == 1, s.count(OLD)
s = s.replace(OLD, NEW, 1)
open(P, 'w', encoding='utf-8').write(s)
print('SHEBANG PATH FIX APPLIED')
