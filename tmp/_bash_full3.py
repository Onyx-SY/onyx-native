# -*- coding: utf-8 -*-
"""bash 语法全量检查 v3（可导入的函数 + 结构判断打印）。"""
import sys

sys.path.insert(0, '.')
from lib import parse as P                            # noqa: E402
from lib.parse import is_shell_logic_structure as ILS  # noqa: E402

FAILS = []


def chk(name, got, want):
    if got != want:
        FAILS.append(name)
        print('❌ %-30s got=%r want=%r' % (name, got, want))


SPLIT = [
    ('echo a b',         ['echo', 'a', 'b']),
    ('echo "a b"',       ['echo', '"a b"']),
    ("echo 'a b'",       ['echo', "'a b'"]),
    ('echo "it\'s"',     ['echo', '"it\'s"']),
    ('echo a\\ b',       ['echo', 'a\\ b']),
    ('echo $(date)',     ['echo', '$(date)']),
    ('A=1 B=2 cmd',      ['A=1', 'B=2', 'cmd']),
    ('echo a > f',       ['echo', 'a', '>', 'f']),
    ('echo "a;b"',       ['echo', '"a;b"']),
    ('echo a#b',         ['echo', 'a#b']),
    ('echo `date`',      ['echo', '`date`']),
]
for text, want in SPLIT:
    try:
        got = P.smart_shlex_split(text, 'bash')
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('split: ' + text, got, want)

QUOTES = [
    ('echo "a"', True), ('echo "a', False),
    ("echo 'a'", True), ("echo 'a", False),
    ('echo "a\'b"', True), ("echo 'a\"b'", True),
    ('echo "a\\"b"', True), ("echo $'a'", True),
]
for text, want in QUOTES:
    try:
        got = P.check_quotes_balanced(text, 'bash')[0]
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('quotes: ' + text, got, want)

COMMENTS = [
    ('echo a # b',   'echo a'),
    ('echo a#b',     'echo a#b'),
    ('echo "#b"',    'echo "#b"'),
    ('echo x#y # z', 'echo x#y'),
]
for text, want in COMMENTS:
    try:
        got = P.remove_comments(text, 'bash')
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('comment: ' + text, got, want)

BOOLS = [
    ('has_pipeline', 'a && b || c',   False),
    ('has_pipeline', 'ls | grep x',   True),
    ('has_pipeline', 'ls |& cat',     True),
    ('has_pipeline', 'echo "a|b"',    False),
    ('has_logical',  'a && b',        True),
    ('has_logical',  'a || b',        True),
    ('has_logical',  'ls | grep x',   False),
    ('has_logical',  'echo "a && b"', False),
    ('has_redirect', 'echo x > f',    True),
    ('has_redirect', 'echo ">"',      False),
]
for kind, text, want in BOOLS:
    try:
        got = (P.has_pipeline(text, 'bash') if kind == 'has_pipeline'
               else P.has_logical_operators(text, 'bash') if kind == 'has_logical'
               else P.has_redirect(text, 'bash'))
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('%s: %s' % (kind, text), got, want)

print()
print('--- 结构判断 is_shell_logic_structure（观察用）---')
for t in ['if true; then echo y; fi', '{ echo a; }', 'f() { echo a; }',
          'for i in 1 2; do echo $i; done', 'case x in a) ;; esac',
          'echo hello', 'cat <<EOF\nhi\nEOF']:
    try:
        print('   %-34s -> %s' % (repr(t)[:34], ILS(t, 'bash')))
    except Exception as e:                              # noqa: BLE001
        print('   %-34s -> ERR %s' % (repr(t)[:34], e))

print()
print('❌ FAILED: %d -> %s' % (len(FAILS), FAILS) if FAILS else '✅ bash 语法全量 ALL PASS')
