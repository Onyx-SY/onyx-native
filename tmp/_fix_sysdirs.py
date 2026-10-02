# -*- coding: utf-8 -*-
"""补系统目录白名单：/proc /sys /dev /run 等也不该被虚拟路径拦截。

用户脚本里的 Python 代码 `os.listdir("/proc")` 被替换成了
`os.listdir("You cannot cross root dir. Onyx has intercepted.")`。
"""
import shutil

P = 'Onyx.py'
s = open(P, encoding='utf-8').read()

OLD = """        for _d in (os.environ.get('PREFIX', ''), '/usr', '/bin', '/sbin',
                   '/opt', '/system', '/apex'):"""
NEW = """        for _d in (os.environ.get('PREFIX', ''), '/usr', '/bin', '/sbin',
                   '/opt', '/system', '/apex',
                   # 内核/运行时伪文件系统：脚本里的 /proc、/dev 等不该被
                   # 虚拟路径解析拦截（否则 os.listdir("/proc") 会变成
                   # "You cannot cross root dir. Onyx has intercepted."）。
                   '/proc', '/sys', '/dev', '/run', '/var/run'):"""

n = s.count(OLD)
assert n == 1, ('occurrences', n)
shutil.copy(P, P + '.bak2')
open(P, 'w', encoding='utf-8').write(s.replace(OLD, NEW, 1))
print('SYSTEM DIRS ADDED')
