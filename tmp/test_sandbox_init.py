# -*- coding: utf-8 -*-
"""验证 Onyx.py 的 init_sandbox_config()：只读、不询问、缺失时补齐 true。"""
import ast
import os
import sys
import uuid
import tempfile
import shutil
import builtins

SRC = open('Onyx.py', encoding='utf-8').read()
tree = ast.parse(SRC)
func = None
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == 'init_sandbox_config':
        func = node
assert func is not None, 'init_sandbox_config not found'
code = ast.get_source_segment(SRC, func)

ns = {
    'os': os, 'uuid': uuid, 'sys': sys,
    'log_info': lambda *a, **k: None,
    'log_error': lambda *a, **k: None,
    '_SANDBOX_ENABLED': True, 'ROOT_DIR': '/', '_SANDBOX_CONFIG_PATH': '',
    '__file__': os.path.abspath('Onyx.py'),
}
exec(compile(code, 'Onyx_init_sandbox', 'exec'), ns)
init = ns['init_sandbox_config']

# 禁止任何 stdin 读取
orig_input = builtins.input


def _no_input(*a, **k):
    raise AssertionError('init_sandbox_config tried to read stdin!')


builtins.input = _no_input

tmpdir = tempfile.mkdtemp()
pkg = os.path.join(tmpdir, 'pkg')
os.makedirs(pkg)
fake_file = os.path.join(pkg, 'Onyx.py')
open(fake_file, 'w').close()
cfg = os.path.join(tmpdir, 'etc', 'onyx', 'sandbox')


def run():
    ns['__file__'] = fake_file
    ns['_SANDBOX_ENABLED'] = None
    init()
    return ns['_SANDBOX_ENABLED']


try:
    r1 = run()
    c1 = open(cfg, encoding='utf-8').read().strip()
    open(cfg, 'w', encoding='utf-8').write('false')
    r2 = run()
    open(cfg, 'w', encoding='utf-8').write('true')
    r3 = run()
    open(cfg, 'w', encoding='utf-8').write('bogus')
    r4 = run()
finally:
    builtins.input = orig_input

print('case1(missing) ->', r1, 'written=', repr(c1))
print('case2(false)   ->', r2)
print('case3(true)    ->', r3)
print('case4(bogus)   ->', r4)

assert r1 is True and c1 == 'true', 'missing config must default to enabled and write true'
assert r2 is False, 'false must disable'
assert r3 is True, 'true must enable'
assert r4 is False, 'invalid content must fall back to disabled (same as before)'
print('ALL_SANDBOX_INIT_TESTS_PASSED')
shutil.rmtree(tmpdir, ignore_errors=True)
