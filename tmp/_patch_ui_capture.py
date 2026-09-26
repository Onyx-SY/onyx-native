# -*- coding: utf-8 -*-
"""给 ui.py 加 capture_key（TUI 走按键捕获框，REPL 走文本输入）。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "ai_lib", "ui.py")
src = io.open(P, encoding="utf-8").read()

ANCHOR = "def secret_input(\n"
NEW = '''def capture_key(message: str = "", lang: str = "chinese") -> Optional[str]:
    """捕获一次按键组合，返回 Textual 风格键名（如 ctrl+r / alt+enter / f2）。

    - TUI：走适配器的 capture_key（弹出「按键捕获」框，按哪个键就是哪个键，最直观）；
    - REPL：prompt_toolkit 抓不到 alt 等组合的可靠编码 → 改为文本输入，用户直接键入
      组合名（如 alt+enter / ctrl+r），再由 keymap.normalize_combo 校验。
    取消 / 非法 → None。
    """
    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "capture_key"):
        try:
            return _adapter.capture_key(message, lang) or None
        except Exception:
            return None
    try:
        from bin.ai_lib import keymap as _km
        raw = text_input(message, "", lang=lang)
        return _km.normalize_combo(raw)
    except Exception:
        return None


'''

if "def capture_key(" in src:
    print("SKIP: 已存在")
    sys.exit(0)
n = src.count(ANCHOR)
if n != 1:
    print(f"FAIL: 锚点命中 {n} 次")
    sys.exit(1)
src = src.replace(ANCHOR, NEW + ANCHOR, 1)
tmp = P + ".tmp"
with io.open(tmp, "w", encoding="utf-8") as f:
    f.write(src)
os.replace(tmp, P)
print("OK: capture_key 已插入")
