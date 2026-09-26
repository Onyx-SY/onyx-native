# -*- coding: utf-8 -*-
"""step-4b：TUI 配置界面加「⌨️ 主 REPL 按键」栏（按一下键即绑定）。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "repl_config.py")
s = io.open(P, encoding="utf-8").read()

if "class _KeysScreen" in s:
    print("SKIP：已存在")
    sys.exit(0)

SCREENS = '''    class _Capture(ModalScreen):
        """按一下要绑定的键（Esc 取消）。"""

        def compose(self):
            with Vertical(id="box"):
                yield Static(_msg("⌨️ 按下要绑定的按键…", "⌨️ Press the key to bind…"),
                             id="box-title")
                yield Static(_msg("（Esc 取消；直接按你想用的那个键）",
                                  "(Esc to cancel; just press the key you want)"),
                             id="box-help")

        def on_mount(self):
            try:
                self.focus()
            except Exception:
                pass

        def on_key(self, event):
            k = getattr(event, "key", "") or ""
            if not k:
                return
            if k in ("escape", "ctrl+c", "ctrl+q"):
                self.dismiss(None)
                return
            if k in ("shift", "ctrl", "alt", "meta", "super"):
                return
            self.dismiss(k)

    class _KeysScreen(ModalScreen):
        """主 REPL 键位列表：Enter 按新键绑定，r 恢复该项，R 恢复全部。"""

        BINDINGS = [Binding("escape", "back", "Back", show=False),
                    Binding("r", "reset_one", "Reset", show=False),
                    Binding("R", "reset_all", "Reset all", show=False)]

        def compose(self):
            yield Static(_msg("⌨️ 主 REPL 按键（写回 ~/.config/onyx/ptk.json）",
                              "⌨️ Main REPL keys (writes ~/.config/onyx/ptk.json)"),
                         id="cfg-title")
            yield OptionList(*self._options(), id="cfg-items")
            yield Static(_msg("↑↓ 选择 · Enter 按新键绑定 · r 恢复该项 · R 恢复全部 · Esc 返回",
                              "↑↓ move · Enter rebind · r reset · R reset all · Esc back"),
                         id="cfg-hint")

        def _options(self):
            cur = read_repl_keys()
            out = []
            for aid, pkey, cn, en in repl_key_actions():
                label = en if _is_en(lang) else cn
                out.append(Option(f" {pkey:<22} = {str(cur.get(pkey, '')):<16} {label}"))
            return out

        def on_mount(self):
            try:
                self.query_one("#cfg-items", OptionList).focus()
            except Exception:
                pass

        def _refresh(self):
            ol = self.query_one("#cfg-items", OptionList)
            keep = ol.highlighted
            ol.clear_options()
            for o in self._options():
                ol.add_option(o)
            if keep is not None and ol.option_count:
                ol.highlighted = min(keep, ol.option_count - 1)

        def on_option_list_option_selected(self, event):
            acts = repl_key_actions()
            idx = event.option_index
            if idx is None or idx >= len(acts):
                return
            aid = acts[idx][0]

            def _done(key):
                if key:
                    set_repl_key(aid, key)
                self._refresh()

            self.push_screen(_Capture(), _done)

        def action_back(self):
            self.dismiss(None)

        def action_reset_one(self):
            acts = repl_key_actions()
            idx = self.query_one("#cfg-items", OptionList).highlighted
            if idx is None or idx >= len(acts):
                return
            reset_repl_key(acts[idx][0])
            self._refresh()

        def action_reset_all(self):
            reset_all_repl_keys()
            self._refresh()

'''

ANCHOR = "    class _App(App):"
assert s.count(ANCHOR) == 1
s = s.replace(ANCHOR, SCREENS + ANCHOR, 1)
print("OK   已插入 _Capture / _KeysScreen")

# _options() 末尾追加「主 REPL 按键」入口
OLD_OPT = '''                out.append(Option(f"{mark} {s['id']:<22} = {str(val):<12} {label_of(s, lang)}"))
            return out'''
NEW_OPT = '''                out.append(Option(f"{mark} {s['id']:<22} = {str(val):<12} {label_of(s, lang)}"))
            out.append(Option(_msg("⌨️ 主 REPL 按键（Enter 进入，按一下键即可改）",
                                   "⌨️ Main REPL keys (Enter to open, press a key to rebind)")))
            return out'''
assert s.count(OLD_OPT) == 1
s = s.replace(OLD_OPT, NEW_OPT, 1)
print("OK   _options 追加按键入口")

# 选中处理：最后一项 → 打开按键界面
OLD_SEL = '''        def on_option_list_option_selected(self, event):
            idx = event.option_index
            if idx is None or idx >= len(SETTINGS):
                return
            s = SETTINGS[idx]'''
NEW_SEL = '''        def on_option_list_option_selected(self, event):
            idx = event.option_index
            if idx is not None and idx == len(SETTINGS):
                self.push_screen(_KeysScreen(), lambda _v: self._refresh())
                return
            if idx is None or idx >= len(SETTINGS):
                return
            s = SETTINGS[idx]'''
assert s.count(OLD_SEL) == 1
s = s.replace(OLD_SEL, NEW_SEL, 1)
print("OK   选中处理")

tmp = P + ".tmp"
io.open(tmp, "w", encoding="utf-8").write(s)
os.replace(tmp, P)
print("已写回 repl_config.py")
