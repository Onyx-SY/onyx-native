# -*- coding: utf-8 -*-
"""bash 语法全量正确性检查（带期望值，只报不符项）。"""
import sys

sys.path.insert(0, '.')
from lib import parse as P   # noqa: E402

FAILS = []


def chk(name, got, want):
    if got != want:
        FAILS.append((name, got, want))
        print('❌ %-28s got=%r want=%r' % (name, got, want))


# ── 1. 分词 ──
SPLIT = [
    ('echo a b',            ['echo', 'a', 'b']),
    ('echo "a b"',          ['echo', 'a b']),
    ("echo 'a b'",          ['echo', 'a b']),
    ('''echo "it's"''',     ['echo', "it's"]),
    ('''echo 'a"b' ''',     ['echo', 'a"b']),
    ('echo a\\ b',          ['echo', 'a b']),
    ('echo "a\\"b"',        ['echo', 'a"b']),
    ("echo $'a\\nb'",       ['echo', 'a\nb']),
    ('echo $(date)',        ['echo', '$(date)']),
    ('echo "${V}"',         ['echo', '${V}']),
    ('A=1 B=2 cmd',         ['A=1', 'B=2', 'cmd']),
    ('cmd 2>&1',            ['cmd', '2>&1']),
    ('echo a > f',          ['echo', 'a', '>', 'f']),
    ('echo "a;b"',          ['echo', 'a;b']),
    ('echo a#b',            ['echo', 'a#b']),
    ('echo "a|b"',          ['echo', 'a|b']),
    ('cmd <<< "here"',      ['cmd', '<<<', 'here']),
    ('echo `date`',         ['echo', '`date`']),
]
for text, want in SPLIT:
    try:
        got = P.smart_shlex_split(text, 'bash')
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('split: ' + text, got, want)

# ── 2. 引号配平 ──
QUOTES = [
    ('echo "a"',      True),
    ('echo "a',       False),
    ("echo 'a'",      True),
    ("echo 'a",       False),
    ('''echo "a'b"''', True),
    ('''echo 'a"b' ''', True),
    ('echo "a\\"b"',  True),
    ("echo $'a'",     True),
]
for text, want in QUOTES:
    try:
        got = P.check_quotes_balanced(text, 'bash')
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('quotes: ' + text, got, want)

# ── 3. 注释移除 ──
COMMENTS = [
    ('echo a # b',    'echo a '),
    ('echo a#b',      'echo a#b'),
    ('echo "#b"',     'echo "#b"'),
]
for text, want in COMMENTS:
    try:
        got = P.remove_comments(text, 'bash')
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('comment: ' + text, got, want)

# ── 4. 管道 / 逻辑 / 重定向 ──
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
        if kind == 'has_pipeline':
            got = P.has_pipeline(text, 'bash')
        elif kind == 'has_logical':
            got = P.has_logical_operators(text, 'bash')
        else:
            got = P.has_redirect(text, 'bash')
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('%s: %s' % (kind, text), got, want)

# ── 5. 多行完整性（bash -n）──
VALID = [
    ('if true; then echo y; fi',            True),
    ('{ echo a; echo b; }',                 True),
    ('f() { echo a; }',                     True),
    ('echo "unclosed',                      False),
    ('if true; then',                       False),
    ('for i in 1 2; do echo $i; done',      True),
    ('cat <<EOF\nhi\nEOF',                  True),
    ('case x in a) ;; esac',                True),
]
for text, want in VALID:
    try:
        got = P.validate_bash(text)
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('validate: ' + text[:26], got, want)

print()
print('❌ FAILED: %d' % len(FAILS) if FAILS else '✅ bash 语法全量检查 ALL PASS')
