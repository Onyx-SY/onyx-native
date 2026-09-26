#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：AI 模块文案必须「单语」——绝不允许中英拼在一块儿。

背景（用户实测 2026-09-24）：
  计划确认弹窗的选项是 "✅ 确认计划，开始执行 | ✅ Confirm plan and start"，
  中文和英文拼在一条里。根因有两层：
    1) bin/ai_lib/lang.py 缺 11 个键 → helpers.py 的 .get(key, 默认值) 一直命中
       那个「双语拼接」默认值；
    2) 默认值本身写成了 "中文 | English"。
  本测试同时锁死这两层：词表必须齐、值里不许出现拼接分隔符。

覆盖：
  A. lang.py 中英两表键集合一致，且被 lang_text[...] / lang_text.get(...) 用到的
     键**全部存在**（缺键 = 运行时回落到硬编码默认值，正是双语泄漏的来源）。
  B. 两表任何值都不含 " | "（中英拼接分隔符）。
  C. confirm_plan 在中文/英文下返回的选项都是单语（不含 " | "、且不是双语混合）。
  D. helpers.parse_arguments 的 -m 非法值错误为单语。
  E. TUI 有 /lang 之后的界面语言同步入口 _apply_lang。

运行：python3 test/virtual/test_i18n_no_bilingual.py   → 输出 ALL PASS
"""

import ast
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_LANG_PY = os.path.join(_ROOT, "bin", "ai_lib", "lang.py")
_BIN = os.path.join(_ROOT, "bin")

_FAILS = []


def _check(cond, msg):
    if cond:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")
        _FAILS.append(msg)


# ────────────────────────── A. 词表完整性 ──────────────────────────

def _lang_dicts():
    """从 lang.py 的 AST 里取出中/英两个大字典（按出现顺序：英文在前）。"""
    tree = ast.parse(open(_LANG_PY, encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            ks = [k.value for k in node.keys
                  if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if len(ks) > 50:
                out.append({k.value: v.value for k, v in zip(node.keys, node.values)
                            if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)})
    return out


def _used_keys():
    """扫描 bin/ 下所有 lang_text 取词，返回 {key: {文件名...}}。"""
    used = {}
    for dirpath, _dirs, files in os.walk(_BIN):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                txt = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for m in re.finditer(r'lang_text(?:\.get)?\(\s*"([a-z0-9_]+)"', txt):
                used.setdefault(m.group(1), set()).add(fn)
            for m in re.finditer(r'lang_text\[\s*"([a-z0-9_]+)"\s*\]', txt):
                used.setdefault(m.group(1), set()).add(fn)
    return used


def test_a_keys_complete():
    print("[A] 词表完整性（缺键 → 回落到硬编码默认值 → 双语泄漏）")
    ds = _lang_dicts()
    _check(len(ds) == 2, f"lang.py 含 2 个语言表（实际 {len(ds)}）")
    if len(ds) != 2:
        return
    en, cn = ds
    _check(set(en) == set(cn),
           f"中英键集合一致（en={len(en)} cn={len(cn)}，差异={sorted(set(en) ^ set(cn))}）")
    used = _used_keys()
    missing = sorted(k for k in used if k not in cn)
    _check(not missing, f"被引用的键全部存在（缺：{missing}）")


# ────────────────────────── B. 值里不许拼接 ──────────────────────────

def test_b_no_bilingual_values():
    print("[B] 词表值不得含中英拼接分隔符 ' | '")
    ds = _lang_dicts()
    if len(ds) != 2:
        _check(False, "无法取得两语言表")
        return
    bad = []
    for tag, d in zip(("en", "cn"), ds):
        for k, v in d.items():
            if isinstance(v, str) and " | " in v:
                bad.append(f"{tag}.{k}={v[:50]!r}")
    _check(not bad, f"无中英拼接值（发现：{bad}）")


# ────────────────────────── C. confirm_plan 单语 ──────────────────────────

def _confirm_plan_options(lang):
    """把当前语言固定为 lang，抓取 confirm_plan 传给 select_option 的选项。"""
    from bin.ai_lib import helpers
    from bin.ai_lib import config as _cfg

    captured = {}

    def _fake_select(message, options, default="", lang="chinese", blocking=False, body=""):
        captured["message"] = message
        captured["options"] = list(options)
        return options[0]          # 选「确认」→ 立即返回

    old_sel = helpers.select_option
    old_cfg_lang = _cfg.get_current_lang
    old_helpers_lang = helpers.get_current_lang
    helpers.select_option = _fake_select
    helpers.get_current_lang = lambda: lang
    _cfg.get_current_lang = lambda: lang
    try:
        res = helpers.confirm_plan("计划正文", {})   # 空词表 → 逼出兜底分支
    finally:
        helpers.select_option = old_sel
        helpers.get_current_lang = old_helpers_lang
        _cfg.get_current_lang = old_cfg_lang
    return res, captured


def test_c_confirm_plan_single_language():
    print("[C] confirm_plan 选项为单语（含空词表兜底路径）")
    for lang in ("chinese", "english"):
        res, cap = _confirm_plan_options(lang)
        opts = cap.get("options") or []
        _check(res == "confirm", f"{lang}: 选首项 → confirm（实际 {res!r}）")
        _check(len(opts) == 3, f"{lang}: 3 个选项（实际 {len(opts)}）")
        joined = " ".join(opts)
        _check(" | " not in joined, f"{lang}: 选项无 ' | ' 拼接 → {opts}")
        if lang == "chinese":
            _check(all(re.search(r"[\u4e00-\u9fff]", o) for o in opts),
                   f"chinese: 每个选项都含中文 → {opts}")
            _check(not any(re.search(r"[A-Za-z]{3,}", o) for o in opts),
                   f"chinese: 选项无英文残留 → {opts}")
        else:
            _check(all(not re.search(r"[\u4e00-\u9fff]", o) for o in opts),
                   f"english: 每个选项都不含中文 → {opts}")
        _check(" | " not in (cap.get("message") or ""),
               f"{lang}: 提示语无拼接 → {cap.get('message')!r}")


# ────────────────────────── D. -m 非法值错误单语 ──────────────────────────

def test_d_parse_error_single_language():
    print("[D] parse_arguments -m 非法值 → 单语错误")
    from bin.ai_lib import helpers
    from bin.ai_lib import config as _cfg
    for lang in ("chinese", "english"):
        old_cfg_lang = _cfg.get_current_lang
        old_helpers_lang = helpers.get_current_lang
        helpers.get_current_lang = lambda: lang
        _cfg.get_current_lang = lambda: lang
        try:
            r = helpers.parse_arguments(["ai", "-m", "bogus"], {})
        finally:
            helpers.get_current_lang = old_helpers_lang
            _cfg.get_current_lang = old_cfg_lang
        msg = str(r[1]) if isinstance(r, (tuple, list)) and len(r) > 1 else ""
        _check(" | " not in msg, f"{lang}: 无拼接 → {msg!r}")
        if lang == "english":
            _check(not re.search(r"[\u4e00-\u9fff]", msg), f"english 纯英文 → {msg!r}")
        else:
            _check(bool(re.search(r"[\u4e00-\u9fff]", msg)), f"chinese 含中文 → {msg!r}")


# ────────────────────────── E. TUI 语言切换入口 ──────────────────────────

def test_e_tui_lang_switch_hook():
    print("[E] TUI 提供 /lang 之后的界面语言同步入口")
    p = os.path.join(_ROOT, "bin", "ai_tui.py")
    src = open(p, encoding="utf-8").read()
    _check("def _apply_lang(self" in src, "_apply_lang 已定义")
    _check("self._apply_lang" in src, "_apply_lang 已被调用")
    _check("get_current_lang" in src, "启动语言取自 get_current_lang（与引擎同源）")


def main():
    test_a_keys_complete()
    test_b_no_bilingual_values()
    test_c_confirm_plan_single_language()
    test_d_parse_error_single_language()
    test_e_tui_lang_switch_hook()
    print()
    if _FAILS:
        print(f"FAIL ({len(_FAILS)} 项未通过)")
        for f in _FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
