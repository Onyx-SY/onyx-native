# -*- coding: utf-8 -*-
"""验证 Main.py：向导沙箱文案键齐全 + _save_sandbox_setting 行为正确。"""
import ast
import os
import tempfile
import shutil
import stat

SRC = open('Main.py', encoding='utf-8').read()
tree = ast.parse(SRC)

# ── 1. LANGUAGE_TEXT 文案键 ──
KEYS = ['setup_step_sandbox', 'setup_sandbox_title', 'setup_sandbox_option_on',
        'setup_sandbox_option_off', 'setup_sandbox_saved']
lang_text = None
for node in tree.body:
    if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'LANGUAGE_TEXT' for t in node.targets):
        lang_text = ast.literal_eval(node.value)
assert lang_text is not None, 'LANGUAGE_TEXT not found'
for lang in ('chinese', 'english'):
    msgs = lang_text[lang]['messages']
    for k in KEYS:
        assert k in msgs and isinstance(msgs[k], str) and msgs[k].strip(), f'{lang} missing {k}'
    print(f'{lang}: ' + ' | '.join(f'{k}={msgs[k]}' for k in KEYS))

# ── 2. 向导函数包含沙箱步骤 ──
cls = None
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == 'UltraFastEnvironmentChecker':
        cls = node
assert cls is not None
funcs = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
assert '_save_sandbox_setting' in funcs, '_save_sandbox_setting missing'
wizard_src = ast.get_source_segment(SRC, funcs['_first_time_setup_wizard'])
for token in ('setup_step_sandbox', '_save_sandbox_setting', 'setup_sandbox_option_on',
              'setup_sandbox_option_off', 'setup_sandbox_saved'):
    assert token in wizard_src, f'wizard missing {token}'
print('wizard step OK (language -> sandbox)')

# ── 3. _save_sandbox_setting 行为 ──
save_code = ast.get_source_segment(SRC, funcs['_save_sandbox_setting'])
tmpdir = tempfile.mkdtemp()
ns = {'os': os, 'ROOT_DIR': tmpdir, 'log_print': lambda *a, **k: None}
exec(compile(save_code, 'save_sandbox', 'exec'), ns)
save = ns['_save_sandbox_setting']
cfg = os.path.join(tmpdir, 'etc', 'onyx', 'sandbox')

save(None, True)  # 第一个参数为 self（方法体内未使用）
assert open(cfg, encoding='utf-8').read().strip() == 'true'
mode = stat.S_IMODE(os.stat(cfg).st_mode)
print('save(True)  -> true, mode=%o' % mode)
save(None, False)
assert open(cfg, encoding='utf-8').read().strip() == 'false'
print('save(False) -> false')
shutil.rmtree(tmpdir, ignore_errors=True)

print('ALL_MAIN_WIZARD_TESTS_PASSED')
