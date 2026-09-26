# -*- coding: utf-8 -*-
"""给 ai_tui.py 加：KeyCaptureScreen + capture_key 适配器 + keymap.init。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "ai_tui.py")
src = io.open(P, encoding="utf-8").read()

CAPTURE_CLASS = '''    class KeyCaptureScreen(ModalScreen):
        """按键捕获：按下任意键即返回该组合（Textual 键名），Esc / Ctrl+C 取消。"""

        def __init__(self, title: str = "", hint: str = ""):
            super().__init__()
            self._title = title or "按下要绑定的按键…"
            self._hint = hint or "（Esc 取消）"

        def compose(self):
            with Vertical(id="modal"):
                yield Static(self._title, id="modal-msg")
                yield Static(self._hint, id="modal-hint")

        def on_mount(self) -> None:
            try:
                self.focus()
            except Exception:
                pass

        def on_key(self, event) -> None:
            key = getattr(event, "key", "") or ""
            if not key:
                return
            if key in ("escape", "ctrl+c", "ctrl+q"):
                self.dismiss(None)
                return
            if key in ("shift", "ctrl", "alt", "meta", "super"):
                return          # 单独的修饰键不算
            self.dismiss(key)

'''

CAPTURE_METHOD = '''        def capture_key(self, prompt="", lang="chinese"):
            """TUI：弹出「按键捕获」框，返回按下的键（Textual 键名，如 ctrl+r / alt+enter）。

            Esc / Ctrl+C 取消 → None。供 /config → 按键设置 里自由改键。
            """
            try:
                return self._modal(KeyCaptureScreen(
                    prompt or _t("keymap_press_prompt", lang),
                    _t("keymap_press_hint", lang)))
            except Exception:
                return None

'''

ANCHOR_CLASS = "    # ────────────────────────── 模态框 ──────────────────────────\n"
ANCHOR_METHOD = "        # ── ui.py 适配器契约 ──\n"
ANCHOR_INIT = "    try:\n        AppClass = _build_tui()\n"
INIT_BLOCK = '''    # AI 按键注册表跟随运行时 home（/config → 按键设置 的读写都基于它）
    try:
        from bin.ai_lib import keymap as _keymap_mod
        _keymap_mod.init(user_home_dir)
    except Exception:
        pass
    try:
        AppClass = _build_tui()
'''

patches = [("KeyCaptureScreen", ANCHOR_CLASS, CAPTURE_CLASS + ANCHOR_CLASS),
           ("capture_key", ANCHOR_METHOD, CAPTURE_METHOD + ANCHOR_METHOD),
           ("keymap.init", ANCHOR_INIT, INIT_BLOCK)]

for name, anchor, repl in patches:
    n = src.count(anchor)
    if n != 1:
        print(f"FAIL {name}: 锚点命中 {n} 次，期望 1 次")
        sys.exit(1)
    if name == "KeyCaptureScreen" and "class KeyCaptureScreen" in src:
        print(f"SKIP {name}: 已存在")
        continue
    if name == "capture_key" and "def capture_key" in src:
        print(f"SKIP {name}: 已存在")
        continue
    if name == "keymap.init" and "_keymap_mod.init" in src:
        print(f"SKIP {name}: 已存在")
        continue
    src = src.replace(anchor, repl, 1)
    print(f"OK   {name}: 已插入")

tmp = P + ".tmp"
with io.open(tmp, "w", encoding="utf-8") as f:
    f.write(src)
os.replace(tmp, P)
print("已写回")
