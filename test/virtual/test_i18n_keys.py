#!/usr/bin/env python3
"""离线验证 i18n 完整性：lang.json 中英键集合一致，且 bin/ 内所有 _t/_i18n 引用的键都存在。

运行: python3 test/virtual/test_i18n_keys.py
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

LANG_JSON = os.path.join(ROOT, "bin", "ai_lib", "lang.json")

_PAT = re.compile(r"""\b_?i18n?\(\s*["']([A-Za-z0-9_.\-]+)["']|\b_t\(\s*["']([A-Za-z0-9_.\-]+)["']""")


def _load():
    return json.load(open(LANG_JSON, encoding="utf-8"))


def test_key_sets_equal():
    d = _load()
    zh, en = set(d["chinese"]), set(d["english"])
    assert zh == en, f"键集合不一致：仅中文 {sorted(zh - en)[:10]} / 仅英文 {sorted(en - zh)[:10]}"
    assert len(zh) > 300, f"键数异常：{len(zh)}"
    print(f"PASS 中英键集合一致（{len(zh)} 键）")


def test_no_empty_values():
    d = _load()
    for lang in ("chinese", "english"):
        empty = [k for k, v in d[lang].items() if not str(v).strip()]
        assert not empty, f"{lang} 存在空值：{empty[:10]}"
    print("PASS 无空值")


def test_referenced_keys_exist():
    d = _load()
    keys = set(d["chinese"]) | set(d["english"])
    missing = {}
    for dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, "bin")):
        if "__pycache__" in dirpath:
            continue
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                src = open(p, encoding="utf-8").read()
            except Exception:
                continue
            for m in _PAT.finditer(src):
                k = m.group(1) or m.group(2)
                if k not in keys:
                    missing.setdefault(k, set()).add(os.path.relpath(p, ROOT))
    assert not missing, "缺失键：" + "; ".join(f"{k} ({','.join(sorted(v))})" for k, v in sorted(missing.items()))
    print("PASS 所有 _t/_i18n 引用键均存在")


if __name__ == "__main__":
    test_key_sets_equal()
    test_no_empty_values()
    test_referenced_keys_exist()
    print("\nALL PASS")
