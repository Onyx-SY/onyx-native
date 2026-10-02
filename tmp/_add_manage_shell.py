# -*- coding: utf-8 -*-
"""manage shell <name>：设定「输入模块 + 底层 PTY」共用的 shell。"""
import shutil

P = 'bin/manage.py'
shutil.copy(P, P + '.bak')
s = open(P, encoding='utf-8').read()


def rep(a, b, cnt=1):
    global s
    n = s.count(a)
    assert n == cnt, (a[:60], n)
    s = s.replace(a, b)


rep("def handle_manage(cmd_parts: List[str], request_id: str) -> None:",
    '''SHELL_CONFIG_PATH = os.path.join(ROOT_DIR, "etc", "onyx", "shell")
_KNOWN_SHELLS = ("bash", "zsh", "fish", "sh", "dash", "ksh", "pwsh", "powershell", "cmd")


def handle_shell_option(options: List[str], request_id: str) -> None:
    """`manage shell [name]` —— 设定「输入模块 + 底层 PTY」共用的 shell。

    不带参数 → 显示当前值；带参数 → 校验存在性后写入 etc/onyx/shell。
    合法值：bash/zsh/fish/sh/dash/ksh/pwsh/powershell/cmd，或可执行文件路径。
    写入后：输入层解析（lib/parse）与底层 PTY（lib/terminal/exe）都会用它。
    """
    if not options:
        try:
            with open(SHELL_CONFIG_PATH, encoding="utf-8") as f:
                cur = f.read().strip()
        except Exception:
            cur = ""
        shown = cur or _get_msg("(未设置，自动检测)", "(unset, auto-detect)")
        print_silent(Fore.CYAN + _get_msg(f"当前 shell：{shown}",
                                          f"Current shell: {shown}"))
        return

    name = options[0].strip()
    resolved = ""
    if os.sep in name or (os.altsep and os.altsep in name):
        if os.path.isfile(name) and os.access(name, os.X_OK):
            resolved = os.path.abspath(name)
    else:
        if name.lower() in _KNOWN_SHELLS:
            resolved = shutil.which(name) or ""
    if not resolved:
        print_silent(Fore.RED + _get_msg(
            f"找不到 shell：{name}（配置未改动）",
            f"Shell not found: {name} (config unchanged)"))
        return

    os.makedirs(os.path.dirname(SHELL_CONFIG_PATH), exist_ok=True)
    with open(SHELL_CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(resolved + "\\n")
    print_silent(Fore.GREEN + _get_msg(
        f"✓ shell 已设为：{resolved}\\n"
        f"  输入模块与底层 PTY 都会使用它（重启 Onyx 生效）。",
        f"OK shell set to: {resolved}\\n"
        f"  Both the input layer and the PTY will use it (restart Onyx)."))


def handle_manage(cmd_parts: List[str], request_id: str) -> None:''')

rep('''    elif main_opt == "clean":
        handle_clean_option(sub_opt, request_id)
    else:
        print_silent(Fore.RED + _get_msg(f"未知主选项：{main_opt}，支持 set/clean", f"Unknown main option: {main_opt}, supports set/clean"))''',
    '''    elif main_opt == "clean":
        handle_clean_option(sub_opt, request_id)
    elif main_opt == "shell":
        handle_shell_option(sub_opt, request_id)
    else:
        print_silent(Fore.RED + _get_msg(f"未知主选项：{main_opt}，支持 set/clean/shell", f"Unknown main option: {main_opt}, supports set/clean/shell"))''')

open(P, 'w', encoding='utf-8').write(s)
print('MANAGE SHELL ADDED')
