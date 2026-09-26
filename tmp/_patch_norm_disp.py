# -*- coding: utf-8 -*-
"""read_repl_keys：统一把 'escape, enter' 规整成 'escape,enter'（显示/比较一致）。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "repl_config.py")
s = io.open(P, encoding="utf-8").read()

if "def _norm_disp(" in s:
    print("SKIP：已存在")
    sys.exit(0)

OLD = '''def read_repl_keys() -> Dict[str, str]:
    """当前主 REPL 键位（缺失项回落到默认表）。"""'''
NEW = '''def _norm_disp(value: str) -> str:
    """统一键位显示形式：`escape, enter` → `escape,enter`（默认表与用户值才可比）。"""
    return ",".join(p.strip() for p in str(value or "").split(",") if p.strip())


def read_repl_keys() -> Dict[str, str]:
    """当前主 REPL 键位（缺失项回落到默认表；统一去逗号后空格）。"""'''
assert s.count(OLD) == 1
s = s.replace(OLD, NEW, 1)

OLD2 = '''        for k, v in kb_cfg.items():
            if isinstance(v, str) and v.strip():
                cur[k] = v.strip()
    return cur'''
NEW2 = '''        for k, v in kb_cfg.items():
            if isinstance(v, str) and v.strip():
                cur[k] = _norm_disp(v)
    for k in list(cur.keys()):
        cur[k] = _norm_disp(cur[k])
    return cur'''
assert s.count(OLD2) == 1
s = s.replace(OLD2, NEW2, 1)

tmp = P + ".tmp"
io.open(tmp, "w", encoding="utf-8").write(s)
os.replace(tmp, P)
print("OK   read_repl_keys 已统一显示形式")
