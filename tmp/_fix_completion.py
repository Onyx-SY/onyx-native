# -*- coding: utf-8 -*-
"""补全文件 etc/cmd.json：manage 子命令补上 shell。"""
import json
import shutil

P = 'etc/cmd.json'
shutil.copy(P, P + '.bak')

with open(P, encoding='utf-8') as f:
    data = json.load(f)

mg = data['manage']['subcommands']
assert 'shell' not in mg, 'already added'

# 保持插入顺序：重建 dict，把 shell 放在 clean 之后
new_sub = {}
for k, v in mg.items():
    new_sub[k] = v
    if k == 'clean':
        new_sub['shell'] = {
            "arguments": ["bash", "zsh", "fish", "sh", "dash", "ksh",
                          "pwsh", "powershell", "cmd"]
        }
if 'shell' not in new_sub:                     # clean 不存在时的兜底
    new_sub['shell'] = {"arguments": ["bash", "zsh", "fish", "sh", "pwsh", "cmd"]}
data['manage']['subcommands'] = new_sub

with open(P, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print('cmd.json: manage shell added ->',
      list(data['manage']['subcommands'].keys())[:6])
