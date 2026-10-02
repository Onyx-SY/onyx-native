# -*- coding: utf-8 -*-
"""source 矩阵测试：复杂脚本 × (系统 bash / Onyx-bash / Onyx-zsh)。"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, '.')
from lib.terminal import exe   # noqa: E402

os.makedirs('tmp/有 空格', exist_ok=True)

with open('tmp/_t1.sh', 'w') as f:
    f.write('export T1_VAR="hello world"\n'
            't1_func() { echo "func:$1"; }\n'
            'T1_ARR=(a b c)\n')

with open('tmp/_t2.sh', 'w') as f:
    f.write('source tmp/_t1.sh\n'
            '[[ -n "$T1_VAR" ]] && T2_COND=yes || T2_COND=no\n'
            "T2_ESC=$'line1\\nline2'\n"
            'case "$T1_VAR" in hello*) T2_CASE=match;; *) T2_CASE=no;; esac\n'
            'T2_SUM=0; for i in 1 2 3; do T2_SUM=$((T2_SUM + i)); done\n')

with open('tmp/_t3.sh', 'w') as f:
    f.write('source tmp/_t1.sh\n'
            'T3_CN="中文变量"\n')

with open('tmp/有 空格/_t4.sh', 'w') as f:
    f.write('T4_SPACE=ok\n')

with open('tmp/_t5.sh', 'w') as f:
    f.write('T5_ARG="$1"\n')

CASES = [
    ("变量",      "source tmp/_t1.sh; echo $T1_VAR", "hello world"),
    ("函数",      "source tmp/_t1.sh; t1_func ARG", "func:ARG"),
    ("数组长度",  "source tmp/_t1.sh; echo ${#T1_ARR[@]}", "3"),
    ("条件[[ ]]", "source tmp/_t2.sh; echo $T2_COND", "yes"),
    ("$'转义'",   "source tmp/_t2.sh; echo $T2_ESC | head -1", "line1"),
    ("case",      "source tmp/_t2.sh; echo $T2_CASE", "match"),
    ("for循环",   "source tmp/_t2.sh; echo $T2_SUM", "6"),
    ("嵌套source", "source tmp/_t3.sh; echo $T3_CN", "中文变量"),
    ("空格路径",  'source "tmp/有 空格/_t4.sh"; echo $T4_SPACE', "ok"),
    ("参数传递",  "source tmp/_t5.sh AA BB; echo $T5_ARG", "AA"),
]


class FB:
    def __init__(self):
        self.data = bytearray()

    def write(self, b):
        self.data.extend(b)

    def flush(self):
        pass


class FS:
    def __init__(self):
        self.buffer = FB()


class SS:
    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd


_r, _w = os.pipe()
sys.__stdout__ = FS()
sys.stdin = SS(_r)
exe.DEBUG_ENABLED = False

fails = []


def run_pty(shell_path, label):
    print('\n=== %s ===' % label)
    sh = exe.PersistentShell(shell_path=shell_path, cwd=os.getcwd())
    for name, cmd, want in CASES:
        buf = []
        try:
            rc, _ = sh.execute(cmd, buf)
        except Exception as e:                      # noqa: BLE001
            rc, buf = -99, [str(e)]
        out = ''.join(buf)
        ok = (want in out)
        print('  %s %-12s rc=%-4s got=%r' % ('✅' if ok else '❌', name, rc, out[-46:]))
        if not ok:
            fails.append('%s/%s' % (label, name))
    sh.cleanup()


def run_sys(shell, label):
    print('\n=== %s ===' % label)
    for name, cmd, want in CASES:
        try:
            r = subprocess.run([shell, '-c', cmd], capture_output=True,
                               text=True, timeout=15)
            out = (r.stdout or '') + (r.stderr or '')
        except Exception as e:                      # noqa: BLE001
            out = str(e)
        ok = want in out
        print('  %s %-12s got=%r' % ('✅' if ok else '❌', name, out.strip()[-46:]))
        if not ok:
            fails.append('%s/%s' % (label, name))


bash = shutil.which('bash')
zsh = shutil.which('zsh')
run_sys(bash, '系统 bash -c')
if zsh:
    run_sys(zsh, '系统 zsh -c')
run_pty(bash, 'Onyx PTY (bash)')
if zsh:
    run_pty(zsh, 'Onyx PTY (zsh)')

print('\n' + ('❌ FAILED: ' + ', '.join(fails) if fails else '✅ ALL PASS'))
sys.exit(1 if fails else 0)
