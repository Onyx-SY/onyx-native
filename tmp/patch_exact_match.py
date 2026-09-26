# -*- coding: utf-8 -*-
"""补丁：补全候选过滤掉「与当前输入完全相同」的项（补全后菜单应收起）。"""
import io
import os

p = "bin/ai_interactive.py"
s = io.open(p, encoding="utf-8").read()
old = "        return [(cmd, desc, len(word)) for cmd, desc in cmds.items() if cmd.lower().startswith(low)]"
new = ("        # 完全匹配时不再给候选（补全菜单该收起；虚影同理）\n"
       "        return [(cmd, desc, len(word)) for cmd, desc in cmds.items()\n"
       "                if cmd.lower().startswith(low) and cmd.lower() != low]")
assert s.count(old) == 1, s.count(old)
io.open(p + ".tmp", "w", encoding="utf-8").write(s.replace(old, new))
os.replace(p + ".tmp", p)
print("✅ 已过滤完全匹配的斜杠命令候选")
