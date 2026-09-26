# -*- coding: utf-8 -*-
"""keymap：多行/换行默认键改为「平台相关」（桌面 Shift+Enter / Termux Alt+Enter）。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "ai_lib", "keymap.py")
src = io.open(P, encoding="utf-8").read()

if "def multiline_default_keys(" in src:
    print("SKIP: 已存在")
    sys.exit(0)

OLD_DOC = """    连接，如 `escape,enter`），REPL(prompt_toolkit) 侧由 `to_ptk()` 转换；"""
NEW_DOC = """    连接，如 `escape,enter`），REPL(prompt_toolkit) 侧由 `to_ptk()` 转换；
  · **多行/换行键的默认值随平台变化**：标准 Linux/Windows/macOS 桌面终端默认
    `shift+enter`；手机 Termux（软键盘没有 Shift+Enter）默认沿用原来的 `alt+enter`。
    两种键互为别名，都能用。"""
assert src.count(OLD_DOC) == 1
src = src.replace(OLD_DOC, NEW_DOC, 1)

OLD_ML = '''    ("tui.multiline", "tui", "进入多行输入", "Enter multiline",
     ["alt+enter", "shift+enter", "alt+ctrl+j", "ctrl+alt+j"]),'''
NEW_ML = '''    ("tui.multiline", "tui", "进入多行输入 / 换行", "Enter multiline / newline",
     None),   # None = 平台相关默认（见 multiline_default_keys）'''
OLD_SEND = '''    ("multiline.send", "multiline", "多行框：发送", "Multiline: send",
     ["alt+enter", "shift+enter", "alt+ctrl+j", "ctrl+alt+j"]),'''
NEW_SEND = '''    ("multiline.send", "multiline", "多行框：发送", "Multiline: send",
     None),   # None = 平台相关默认（见 multiline_default_keys）'''
for old, new in ((OLD_ML, NEW_ML), (OLD_SEND, NEW_SEND)):
    assert src.count(old) == 1, old[:60]
    src = src.replace(old, new, 1)

ANCHOR = "_ACTIONS_BY_ID: Dict[str, Tuple[str, str, str, str, List[str]]] = {\n"
HELPERS = '''# ── 多行/换行键的平台相关默认值 ──
# 手机 Termux 的软键盘没有 Shift+Enter → 沿用原来的 Alt+Enter；
# 桌面终端（Linux/Windows/macOS）→ 默认 Shift+Enter。
# 说明：TUI 字节层会把 ESC+CR / ESC+LF 改写成 CSI-u shift+enter，所以桌面按
#       Shift+Enter 或 Alt+Enter 都能命中；两种键同时保留为别名。
_TERMUX_CACHE = None


def is_termux() -> bool:
    """是否运行在 Android/Termux（手机端）。"""
    global _TERMUX_CACHE
    if _TERMUX_CACHE is None:
        try:
            if (os.path.exists("/data/data/com.termux/files/home")
                    and os.path.exists("/data/data/com.termux/files/usr")):
                _TERMUX_CACHE = True
            elif "termux" in getattr(sys, "prefix", "").lower():
                _TERMUX_CACHE = True
            elif "com.termux" in (os.environ.get("PREFIX") or ""):
                _TERMUX_CACHE = True
            else:
                _TERMUX_CACHE = False
        except Exception:
            _TERMUX_CACHE = False
    return _TERMUX_CACHE


_DESKTOP_ML_KEYS = ["shift+enter", "alt+enter", "alt+ctrl+j", "ctrl+alt+j"]
_TERMUX_ML_KEYS = ["alt+enter", "shift+enter", "alt+ctrl+j", "ctrl+alt+j"]


def multiline_default_keys() -> List[str]:
    """多行/换行键的默认值：桌面 Shift+Enter，Termux Alt+Enter。"""
    return list(_TERMUX_ML_KEYS if is_termux() else _DESKTOP_ML_KEYS)


def _resolve_defaults(defaults) -> List[str]:
    """把 ACTIONS 里的默认值解析成实际键列表（None = 平台相关）。"""
    if defaults is None:
        return multiline_default_keys()
    return list(defaults)


'''
assert src.count(ANCHOR) == 1
src = src.replace(ANCHOR, HELPERS + ANCHOR, 1)

OLD_DEFAULTS = """def defaults() -> Dict[str, List[str]]:
    return {a[0]: list(a[4]) for a in ACTIONS}"""
NEW_DEFAULTS = """def defaults() -> Dict[str, List[str]]:
    return {a[0]: _resolve_defaults(a[4]) for a in ACTIONS}"""
OLD_COMBOS = """    spec = _ACTIONS_BY_ID.get(action_id)
    if not spec:
        return []
    return list(_load().get(action_id) or spec[4])"""
NEW_COMBOS = """    spec = _ACTIONS_BY_ID.get(action_id)
    if not spec:
        return []
    return list(_load().get(action_id) or _resolve_defaults(spec[4]))"""
for old, new in ((OLD_DEFAULTS, NEW_DEFAULTS), (OLD_COMBOS, NEW_COMBOS)):
    assert src.count(old) == 1, old[:50]
    src = src.replace(old, new, 1)

if "\nimport sys\n" not in src:
    src = src.replace("import os\nimport re\n", "import os\nimport re\nimport sys\n", 1)

tmp = P + ".tmp"
io.open(tmp, "w", encoding="utf-8").write(src)
os.replace(tmp, P)
print("OK: keymap 已改为平台相关默认")
