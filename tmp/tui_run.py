#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runner: compare a TUI program direct vs through Onyx's PTY passthrough.

Usage: python3 -u tmp/tui_run.py [nano|less|top|vim] [rows] [cols] [fake_size]
  fake_size = "R,C"  -> force Onyx's get_terminal_size() to return R,C
                        (simulates the pre-fix 24,80 behaviour)
"""
import os
import sys

ROOT = os.getcwd()
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tmp'))

PROG = sys.argv[1] if len(sys.argv) > 1 else 'nano'
ROWS = int(sys.argv[2]) if len(sys.argv) > 2 else 61
COLS = int(sys.argv[3]) if len(sys.argv) > 3 else 66
FAKE = sys.argv[4] if len(sys.argv) > 4 else ''

import tui_sim as H

H.ROWS, H.COLS = ROWS, COLS

FILE = os.path.join(ROOT, 'tmp', '_tui_target.txt')
with open(FILE, 'w') as f:
    f.write(''.join('line %02d\n' % i for i in range(1, 40)))

SPECS = {
    'nano': (['nano', FILE], 'nano ' + FILE,
             [(6.0, b'\r'), (8.0, b'\x18'), (9.0, b'n')]),
    'less': (['less', FILE], 'less ' + FILE, [(2.5, b'q')]),
    'top': (['top'], 'top', [(3.0, b'q')]),
    'vim': (['vim', '-u', 'NONE', FILE], 'vim -u NONE ' + FILE, [(3.0, b'\x1b:q!\r')]),
}
argv, cmd, keys = SPECS[PROG]


def direct_child():
    os.execvp(argv[0], argv)


PTK = len(sys.argv) > 5 and sys.argv[5] == 'ptk'


def _ptk_prompt(text):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import create_output
    with create_pipe_input() as inp:
        inp.send_text(text)
        s = PromptSession(input=inp, output=create_output())
        s.prompt('onyx> ')


def onyx_child():
    from lib.terminal import exe
    if FAKE:
        r, c = (int(x) for x in FAKE.split(','))
        exe.get_terminal_size = lambda fd=None: (r, c)
    shell = exe.PersistentShell(cwd=ROOT)
    if PTK:
        _ptk_prompt('nano ' + FILE + '\n')
    buf = []
    shell.execute(cmd, buf)
    shell.cleanup()
    if PTK:
        _ptk_prompt('exit\n')


d1 = H.drive(direct_child, 'direct', keys)
d2 = H.drive(onyx_child, 'onyx', keys)
open(os.path.join(ROOT, 'tmp', 'raw_direct.bin'), 'wb').write(d1)
open(os.path.join(ROOT, 'tmp', 'raw_onyx.bin'), 'wb').write(d2)

_r1 = H.report('DIRECT %s' % PROG, d1)
_r2 = H.report('ONYX   %s%s' % (PROG, (' [fake size %s]' % FAKE) if FAKE else ''), d2)
s1 = _r1[1]   # screen DURING the TUI
s2 = _r2[1]

print('#' * 72)
print('SCREEN MATCH (during TUI): %s' % (s1.dump() == s2.dump()))
if s1.dump() != s2.dump():
    import difflib
    print('--- diff (direct -> onyx) ---')
    for line in list(difflib.unified_diff(s1.dump().splitlines(), s2.dump().splitlines(), lineterm=''))[:80]:
        print(line)
