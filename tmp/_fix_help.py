# -*- coding: utf-8 -*-
"""help.md：manage 章节补上 `manage shell`。"""
import shutil

P = 'help.md'
shutil.copy(P, P + '.bak')
s = open(P, encoding='utf-8').read()


def rep(a, b, cnt=1):
    global s
    n = s.count(a)
    assert n == cnt, (a[:60], n)
    s = s.replace(a, b)


rep("| `manage set clean-log-time <days>` | Set the log auto-clean interval |",
    "| `manage set clean-log-time <days>` | Set the log auto-clean interval |\n"
    "| `manage shell <name>` | Set the shell used by **both** the input layer "
    "and the underlying PTY (bash/zsh/fish/sh/pwsh/cmd, or a path) |\n"
    "| `manage shell` | Show the current shell |")

rep("| `manage set clean-log-time <days>` | 设置日志自动清理天数 |",
    "| `manage set clean-log-time <days>` | 设置日志自动清理天数 |\n"
    "| `manage shell <name>` | 设定**输入模块与底层 PTY 共用**的 shell"
    "（bash/zsh/fish/sh/pwsh/cmd 或路径） |\n"
    "| `manage shell` | 查看当前 shell |")

open(P, 'w', encoding='utf-8').write(s)
print('HELP.MD UPDATED')
