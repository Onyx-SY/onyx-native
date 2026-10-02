# -*- coding: utf-8 -*-
"""修 `source` 不被支持：AI 模式 subprocess 的 shell=True 默认用 /bin/sh(dash)，
不支持 source / [[ ]] / 数组 / $'...' 等 bash 扩展 → 显式指定 bash。"""
p = 'lib/terminal/exe.py'
s = open(p, encoding='utf-8').read()


def rep(a, b, cnt=1):
    global s
    n = s.count(a)
    assert n == cnt, (a[:60], n)
    s = s.replace(a, b)


rep("def list_available_shells() -> List[Tuple[str, str]]:",
    '''_SUBPROC_SHELL_CACHE = None


def _subprocess_shell() -> str:
    """AI 模式 shell=True 用的解释器（POSIX）。

    subprocess 的 shell=True 默认用 /bin/sh（dash）—— 它**不支持 source**，
    也没有 [[ ]] / 数组 / $'...' 等 bash 扩展，于是 `source xxx.sh` 会报
    "source: not found"、`[[ ... ]]` 会语法错误。这里优先选 bash，其次 zsh，
    再退到用户 shell（仅 POSIX 系），最后 /bin/sh。
    """
    global _SUBPROC_SHELL_CACHE
    if _SUBPROC_SHELL_CACHE:
        return _SUBPROC_SHELL_CACHE
    for _name in ('bash', 'zsh'):
        _p = shutil.which(_name)
        if _p:
            _SUBPROC_SHELL_CACHE = _p
            return _p
    try:
        _s = get_shell()
        if _s and os.path.basename(_s).lower() in ('sh', 'dash', 'ksh', 'ash'):
            _SUBPROC_SHELL_CACHE = _s
            return _s
    except Exception:
        pass
    _SUBPROC_SHELL_CACHE = '/bin/sh'
    return _SUBPROC_SHELL_CACHE


def list_available_shells() -> List[Tuple[str, str]]:''')

rep("""            cmd, shell=True, stdout=_sp.PIPE, stderr=_sp.PIPE,
            text=True, errors="replace", cwd=cwd,""",
    """            cmd, shell=True, stdout=_sp.PIPE, stderr=_sp.PIPE,
            # 显式指定解释器：默认 /bin/sh（dash）不支持 source / [[ ]] 等
            # bash 扩展 → 用 bash（Windows 上传 None，走系统默认）。
            executable=(None if platform.system() == "Windows"
                        else _subprocess_shell()),
            text=True, errors="replace", cwd=cwd,""")

open(p, 'w', encoding='utf-8').write(s)
print('SOURCE FIX APPLIED')
