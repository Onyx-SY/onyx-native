# -*- coding: utf-8 -*-
"""修 _is_system_bin_path：`/proc` 本身（无尾斜杠）也要放行。

前缀列表存的是 `/proc/`，`p.startswith('/proc/')` 对 `'/proc'` 为假 →
`os.listdir("/proc")` 里的 `"/proc"` 仍被替换成拦截提示。
改为「完全相等 或 以 d + '/' 开头」。
"""
import shutil

P = 'Onyx.py'
s = open(P, encoding='utf-8').read()

OLD = """        _SYSTEM_BIN_PREFIXES = tuple(_pfx)
    return p.startswith(_SYSTEM_BIN_PREFIXES)"""
NEW = """        _SYSTEM_BIN_PREFIXES = tuple(_pfx)
    # 完全相等（如 "/proc" 本身）或位于其下（如 "/proc/net/tcp"）都放行
    return any(p == _d or p.startswith(_d + '/') for _d in _SYSTEM_BIN_PREFIXES)"""

OLD2 = """            if _d:
                _pfx.append(_d.rstrip('/') + '/')"""
NEW2 = """            if _d:
                _pfx.append(_d.rstrip('/'))"""

for a, b in ((OLD, NEW), (OLD2, NEW2)):
    n = s.count(a)
    assert n == 1, (a[:40], n)
    s = s.replace(a, b, 1)

open(P, 'w', encoding='utf-8').write(s)
print('SYSDIR EXACT MATCH FIXED')
