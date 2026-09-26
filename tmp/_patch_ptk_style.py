# -*- coding: utf-8 -*-
"""set_repl_key 兼容 ptk 风格键名（c-l / s-tab / escape,up），恢复默认走直写。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "repl_config.py")
s = io.open(P, encoding="utf-8").read()

if "def _valid_ptk(" in s:
    print("SKIP：已存在")
    sys.exit(0)

# 1) 新增 ptk 风格校验 + 直写函数（插在 set_repl_key 之前）
OLD = "def set_repl_key(action: str, combo: str) -> tuple:"
NEW = '''def _valid_ptk(value: str) -> bool:
    """粗略校验 prompt_toolkit 风格键序列：c-r / s-tab / escape,up / pageup / f2。"""
    import re as _re
    parts = [p.strip() for p in str(value or "").split(",") if p.strip()]
    if not parts or len(parts) > 3:
        return False
    return all(_re.match(r"^(c-|a-|s-|m-)?[a-z0-9][a-z0-9-]*$", p) for p in parts)


def _write_ptk_key(action: str, value: str) -> bool:
    """把某个动作的键直接写进 ptk.json（不做风格转换，供恢复默认等已知合法值使用）。"""
    path = _ptk_path()
    cfg = _read_json(path, {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    kb_cfg = cfg.get("key_bindings")
    if not isinstance(kb_cfg, dict):
        kb_cfg = {}
    kb_cfg[action] = ",".join(p.strip() for p in str(value).split(",") if p.strip())
    cfg["key_bindings"] = kb_cfg
    return _write_json(path, cfg)


def set_repl_key(action: str, combo: str) -> tuple:'''
assert s.count(OLD) == 1
s = s.replace(OLD, NEW, 1)

# 2) set_repl_key：Textual 风格转换失败时，兼容 ptk 风格
OLD2 = '''    except Exception:
        norm = str(combo or "").strip()
        if not norm:
            return False, "invalid_key"
        value = norm'''
NEW2 = '''    except Exception:
        norm = ""
    if not norm:
        # 兼容直接写 ptk 风格（c-l / s-tab / escape,up）——恢复默认值走的就是这条路
        raw = str(combo or "").strip()
        if not _valid_ptk(raw):
            return False, "invalid_key"
        value = ",".join(p.strip() for p in raw.split(",") if p.strip())'''
assert s.count(OLD2) == 1, s.count(OLD2)
s = s.replace(OLD2, NEW2, 1)

# 3) 恢复默认：直接写默认值，不再绕风格转换
OLD3 = '''    if action not in defaults:
        return False
    return set_repl_key(action, str(defaults[action]).replace(",", ","))'''
NEW3 = '''    if action not in defaults:
        return False
    return _write_ptk_key(action, str(defaults[action]))'''
assert s.count(OLD3) == 1
s = s.replace(OLD3, NEW3, 1)

OLD4 = '''    for action in list(defaults.keys()):
        if set_repl_key(action, str(defaults[action])):
            n += 1
    return n'''
NEW4 = '''    for action in list(defaults.keys()):
        if _write_ptk_key(action, str(defaults[action])):
            n += 1
    return n'''
assert s.count(OLD4) == 1
s = s.replace(OLD4, NEW4, 1)

tmp = P + ".tmp"
io.open(tmp, "w", encoding="utf-8").write(s)
os.replace(tmp, P)
print("OK  set/reset 已兼容 ptk 风格键名")
