# -*- coding: utf-8 -*-
"""exe.get_shell() 读 etc/onyx/shell（`manage shell` 写入），
与 lib/parse.normalize_shell_type() 共用同一来源 → 输入模块与 PTY 同一种 shell。"""
import shutil

P = 'lib/terminal/exe.py'
shutil.copy(P, P + '.bak')
s = open(P, encoding='utf-8').read()

OLD = '        debug_log(f"Shell override {override!r} not found, falling back to auto-detect", \'error\')'
assert s.count(OLD) == 1

NEW = OLD + '''

    # ── 1. 持久化配置 etc/onyx/shell（`manage shell <name>` 写入）──
    # 与 lib/parse.normalize_shell_type() 共用同一来源，保证「输入模块」
    # 与「底层 PTY」用的是同一种 shell。
    try:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(_root, 'etc', 'onyx', 'shell'), encoding='utf-8') as _f:
            _cfg_shell = _f.read().strip()
    except Exception:
        _cfg_shell = ''
    if _cfg_shell:
        _r = _resolve_shell_candidate(_cfg_shell)
        if _r:
            _shell_cache = _r
            debug_log(f"Using configured shell: {_r}")
            return _r'''

s = s.replace(OLD, NEW, 1)
open(P, 'w', encoding='utf-8').write(s)
print('EXE SHELL CONFIG WIRED')
