# -*- coding: utf-8 -*-
"""help_info/commands/manage.json：补上 shell 子命令的说明（中英）。"""
import json
import shutil

P = 'bin/help/help_info/commands/manage.json'
shutil.copy(P, P + '.bak')

with open(P, encoding='utf-8') as f:
    d = json.load(f)

node = d['命令']['manage']

# 中文
node['Chinese'] = node['Chinese'].replace(
    '用法：manage [-q/--quiet] [set/clean] [子选项] [值/子选项]',
    '用法：manage [-q/--quiet] [set/clean/shell] [子选项] [值/子选项]'
).replace(
    '  -q/--quiet          - 静默模式，执行时不输出提示信息',
    '  shell [名称]        - 设定「输入模块 + 底层 PTY」共用的 shell'
    '（bash/zsh/fish/sh/dash/ksh/pwsh/powershell/cmd，或可执行文件路径）；'
    '不带参数则显示当前值\n'
    '  -q/--quiet          - 静默模式，执行时不输出提示信息'
)

# 英文
node['English'] = node['English'].replace(
    'Usage: manage [-q/--quiet] [set/clean] [sub-option] [value/sub-option]',
    'Usage: manage [-q/--quiet] [set/clean/shell] [sub-option] [value/sub-option]'
).replace(
    '  -q/--quiet          - Silent mode, no prompt output',
    '  shell [name]        - Set the shell shared by the input layer and the'
    ' underlying PTY (bash/zsh/fish/sh/dash/ksh/pwsh/powershell/cmd, or a path);'
    ' without an argument it prints the current value\n'
    '  -q/--quiet          - Silent mode, no prompt output'
)

with open(P, 'w', encoding='utf-8') as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

print('manage.json updated')
print('CN has shell:', 'shell [名称]' in node['Chinese'])
print('EN has shell:', 'shell [name]' in node['English'])
