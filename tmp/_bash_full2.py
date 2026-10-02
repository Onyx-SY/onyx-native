# -*- coding: utf-8 -*-
"""bash 语法全量检查 v2（期望值按实际契约修正，只报真问题）。"""
import sys

sys.path.insert(0, '.')
from lib import parse as P                        # noqa: E402
from lib.terminal.mul_line import validate_bash as VB   # noqa: E402

FAILS = []


def chk(name, got, want):
    if got != want:
        FAILS.append(name)
        print('❌ %-30s got=%r want=%r' % (name, got, want))


# 1. 分词（契约：保留引号与转义原样，元字符独立成 token）
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

# 2. 引号配平（返回 (bool, msg)）
QUOTES = [
    ('echo "a"',       True), ('echo "a',        False),
    ("echo 'a'",       True), ("echo 'a",        False),
    ('echo "a\'b"',    True), ("echo 'a\"b'",    True),
    ('echo "a\\"b"',   True), ("echo $'a'",      True),
]
for text, want in QUOTES:
    try:
        got = P.check_quotes_balanced(text, 'bash')[0]
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('quotes: ' + text, got, want)

# 3. 注释
COMMENTS = [
    ('echo a # b',    'echo a'),
    ('echo a#b',      'echo a#b'),
    ('echo "#b"',     'echo "#b"'),
    ('echo x#y # z',  'echo x#y'),
]
for text, want in COMMENTS:
    try:
        got = P.remove_comments(text, 'bash')
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('comment: ' + text, got, want)

# 4. 管道 / 逻辑 / 重定向
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

# 5. 多行完整性（bash -n）
VALID = [
    ('if true; then echo y; fi',         True),
    ('{ echo a; echo b; }',              True),
    ('f() { echo a; }',                  True),
    ('echo "unclosed',                   False),
    ('if true; then',                    False),
    ('for i in 1 2; do echo $i; done',   True),
    ('case x in a) ;; esac',             True),
    ('cat <<EOF\nhi\nEOF',               True),
]
for text, want in VALID:
    try:
        got = VB(text)
    except Exception as e:                              # noqa: BLE001
        got = 'ERR:%s' % e
    chk('validate: ' + text[:26], got, want)

print()
print('❌ FAILED: %d -> %s' % (len(FAILS), FAILS) if FAILS else '✅ bash 语法全量 ALL PASS')
