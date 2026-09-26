# -*- coding: utf-8 -*-
"""P0 修复：确认框/验证码框防误触（Enter 不再等于「是」）+ 会话级豁免不越过强制确认。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 1) bin/ai_tui.py：ConfirmScreen ──
P = os.path.join(ROOT, "bin", "ai_tui.py")
s = io.open(P, encoding="utf-8").read()

OLD_CONFIRM = '''    class ConfirmScreen(ModalScreen):
        BINDINGS = [Binding("y", "yes", "Yes"), Binding("n", "no", "No"),
                    Binding("escape", "no", "No")]

        def __init__(self, message: str, default: bool = False, lang: str = "chinese"):
            super().__init__()
            self.message = message
            self.default = default
            self.lang = lang

        def compose(self):
            with Vertical(id="modal"):
                yield Static(self.message, id="modal-msg")
                with Horizontal(id="modal-btns"):
                    yield Button(_t("tui_yes", self.lang), variant="success", id="yes")
                    yield Button(_t("tui_no", self.lang), variant="error", id="no")

        def action_yes(self):
            self.dismiss(True)

        def action_no(self):
            self.dismiss(False)

        def on_button_pressed(self, event):
            self.dismiss(event.button.id == "yes")'''

NEW_CONFIRM = '''    class ConfirmScreen(ModalScreen):
        """确认框（危险命令 / 是否关闭二次确认 等）。

        ⚠️ 安全要点（实测踩过的坑）：**弹窗默认绝不能把焦点放在「是」按钮上** ——
        Textual 的 Button 自带 `enter → press`，于是弹窗瞬间到达的残留回车（或用户
        「按回车关掉弹窗」的习惯动作）会被「是」吃掉，等价于**未经确认就放行**：
        危险命令直接执行完、结果回传 AI。两层防护：
          1. `_MODAL_KEY_GRACE` 宽限窗口（0.3s）内的「是」按键/点击一律不生效 → 挡掉误触；
          2. 默认焦点落在 `default` 对应的一侧：危险确认 `default=False` → 焦点在「否」，
             回车只会「拒绝」，永远不会「确认」。
        只有显式按 `y` 或点击「是」才算确认（`escape`/`n` = 拒绝）。
        """

        BINDINGS = [Binding("y", "yes", "Yes"), Binding("n", "no", "No"),
                    Binding("escape", "no", "No")]

        _MODAL_KEY_GRACE = 0.30

        def __init__(self, message: str, default: bool = False, lang: str = "chinese"):
            super().__init__()
            self.message = message
            self.default = default
            self.lang = lang
            self._grace_t = 0.0

        def compose(self):
            with Vertical(id="modal"):
                yield Static(self.message, id="modal-msg")
                with Horizontal(id="modal-btns"):
                    yield Button(_t("tui_yes", self.lang), variant="success", id="yes")
                    yield Button(_t("tui_no", self.lang), variant="error", id="no")

        def on_mount(self):
            import time as _time
            self._grace_t = _time.monotonic()
            try:
                self.query_one("#yes" if self.default else "#no", Button).focus()
            except Exception:
                pass

        def _grace_blocked(self) -> bool:
            import time as _time
            return (_time.monotonic() - self._grace_t) < self._MODAL_KEY_GRACE

        def action_yes(self):
            if self._grace_blocked():
                return
            self.dismiss(True)

        def action_no(self):
            self.dismiss(False)

        def on_button_pressed(self, event):
            if self._grace_blocked() and event.button.id == "yes":
                return                      # 宽限期内点/按「是」不生效（防误触、防连发）
            self.dismiss(event.button.id == "yes")'''

if 'class ConfirmScreen(ModalScreen):\n        """确认框' in s:
    print("SKIP ConfirmScreen：已修")
elif s.count(OLD_CONFIRM) == 1:
    s = s.replace(OLD_CONFIRM, NEW_CONFIRM, 1)
    print("OK   ConfirmScreen：已加防误触 + 默认焦点改为安全侧")
else:
    print(f"FAIL ConfirmScreen：锚点命中 {s.count(OLD_CONFIRM)} 次")
    sys.exit(1)

# ── 2) bin/ai_tui.py：CaptchaScreen 加宽限窗口 ──
OLD_CAP = '''        def compose(self):
            with Vertical(id="modal"):
                yield Static(self.title_text, id="modal-msg")
                yield Static(self.warning, id="modal-warn")
                yield Static(f"{_t('tui_captcha_label', self.lang)}: [ {self.code} ]", id="modal-code")
                yield Input(placeholder=_t("tui_captcha_hint", self.lang), id="modal-input")

        def on_mount(self):
            self.query_one("#modal-input", Input).focus()

        def on_input_submitted(self, event):
            self.dismiss((event.value or "").strip().upper() == self.code.upper())'''

NEW_CAP = '''        _MODAL_KEY_GRACE = 0.30

        def compose(self):
            with Vertical(id="modal"):
                yield Static(self.title_text, id="modal-msg")
                yield Static(self.warning, id="modal-warn")
                yield Static(f"{_t('tui_captcha_label', self.lang)}: [ {self.code} ]", id="modal-code")
                yield Input(placeholder=_t("tui_captcha_hint", self.lang), id="modal-input")

        def on_mount(self):
            import time as _time
            self._grace_t = _time.monotonic()
            self.query_one("#modal-input", Input).focus()

        def _grace_blocked(self) -> bool:
            import time as _time
            return (_time.monotonic() - getattr(self, "_grace_t", 0.0)) < self._MODAL_KEY_GRACE

        def on_input_submitted(self, event):
            # 宽限窗口内忽略回车：挡掉「弹窗瞬间到达的残留回车」被当成提交
            if self._grace_blocked():
                return
            self.dismiss((event.value or "").strip().upper() == self.code.upper())'''

if "def _grace_blocked(self) -> bool:" in s and "modal-input" in s and "tui_captcha_label" in s:
    print("SKIP CaptchaScreen：已修")
elif s.count(OLD_CAP) == 1:
    s = s.replace(OLD_CAP, NEW_CAP, 1)
    print("OK   CaptchaScreen：已加宽限窗口")
else:
    print(f"FAIL CaptchaScreen：锚点命中 {s.count(OLD_CAP)} 次")
    sys.exit(1)

tmp = P + ".tmp"
io.open(tmp, "w", encoding="utf-8").write(s)
os.replace(tmp, P)

# ── 3) lib/safe.py：会话级豁免不得越过「强制确认」类型 ──
S = os.path.join(ROOT, "lib", "safe.py")
t = io.open(S, encoding="utf-8").read()
OLD_SESS = '''    # === 会话级跳过：本会话已验证过一次验证码，后续命令执行不再重复弹验证码 ===
    if _SESSION_CAPTCHA_VERIFIED:'''
NEW_SESS = '''    # === 会话级跳过：本会话已验证过一次验证码，后续命令执行不再重复弹验证码 ===
    # ⚠️ 但**强制确认类型（rm / 重定向 / here-doc）不享受豁免**：会话标记可能来自
    #    另一次确认，不能让 `rm -rf` 这类命令靠它静默执行。
    if _SESSION_CAPTCHA_VERIFIED and not force_confirm:'''
if "if _SESSION_CAPTCHA_VERIFIED and not force_confirm:" in t:
    print("SKIP safe.py：已修")
elif t.count(OLD_SESS) == 1:
    t = t.replace(OLD_SESS, NEW_SESS, 1)
    tmp = S + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(t)
    os.replace(tmp, S)
    print("OK   safe.py：强制确认类型不再享受会话级豁免")
else:
    print(f"FAIL safe.py：锚点命中 {t.count(OLD_SESS)} 次")
    sys.exit(1)
