# -*- coding: utf-8 -*-
"""把 Onyx.py 中的 init_sandbox_config() 整段替换为只读版本（不交互询问）。"""
p = 'Onyx.py'
s = open(p, encoding='utf-8').read()
start = s.index('def init_sandbox_config() -> None:')
end = s.index('def check_admin_permission() -> None:', start)
new = open('tmp/onyx_init_sandbox_new.py', encoding='utf-8').read()
assert 0 < start < end, (start, end)
out = s[:start] + new + s[end:]
with open(p, 'w', encoding='utf-8') as f:
    f.write(out)
print('patched: start=%d end=%d removed=%d added=%d' % (start, end, end - start, len(new)))
