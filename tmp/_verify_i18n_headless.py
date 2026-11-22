# -*- coding: utf-8 -*-
"""无头验证：AI TUI 的双语（绑定描述 / 命令面板 / 占位符 / 系统命令条目）。

用 Textual 的 run_test() 直接驱动 App，不依赖终端抓屏。

用法: python tmp/_verify_i18n_headless.py
"""
import asyncio
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bin.ai_tui import _build_tui  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def binding_descs(bmap):
    """BindingsMap → {action: description}（迭代 BindingsMap 产出 (key, binding) 元组）。"""
    out = {}
    try:
        for _key, blist in bmap.key_to_bindings.items():
            for b in blist:
                out.setdefault(b.action, b.description)
    except Exception:
        for item in bmap:
            b = item[1] if isinstance(item, tuple) else item
            out.setdefault(b.action, b.description)
    return out


async def main():
    App = _build_tui()
    home = tempfile.mkdtemp(prefix="onyx_i18n_")
    ctx = {
        "user_home_dir": home,
        "lang": "chinese",
        "session_id": "verify-i18n",
        "mode": "normal",
        "quiet": True,
        "show_time": False,
        "session_start": time.time(),
        "memory_mode": "global",
        "cwd": os.getcwd(),
        "_key_changed": False,
        "_chat_changed": False,
    }
    app = App({"user_home_dir": home}, ctx)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()

        # ── 中文初始态 ──
        inp = app.query_one("#prompt")
        check("启动语言=chinese", app._lang == "chinese", app._lang)
        check("中文占位符", "输入消息" in (inp.placeholder or ""), inp.placeholder)
        cn_descs = binding_descs(app._bindings)
        check("中文绑定描述（退出）", cn_descs.get("quit") == "退出", cn_descs.get("quit"))
        check("中文绑定描述（取消）", cn_descs.get("cancel") == "取消当前生成",
              cn_descs.get("cancel"))
        cn_cmds = [c.title for c in app.get_system_commands(app.screen)]
        check("中文系统命令条目", "切换主题" in cn_cmds and "退出" in cn_cmds, str(cn_cmds))

        # ── 命令面板（中文）──
        app.action_command_palette()
        await pilot.pause()
        scr = app.screen
        check("中文面板已打开", scr.__class__.__name__ == "OnyxCommandPalette",
              scr.__class__.__name__)
        check("中文面板 placeholder", getattr(scr, "_placeholder", "") == "搜索命令…",
              getattr(scr, "_placeholder", ""))
        scr.dismiss(None) if hasattr(scr, "dismiss") else None
        await pilot.pause()

        # ── 切英文 ──
        app._apply_lang("english")
        await pilot.pause()
        check("切换后语言=english", app._lang == "english", app._lang)
        check("英文占位符", "Type a message" in (inp.placeholder or ""), inp.placeholder)
        en_descs = binding_descs(app._bindings)
        check("英文绑定描述（Quit）", en_descs.get("quit") == "Quit", en_descs.get("quit"))
        check("英文绑定描述（Cancel）", en_descs.get("cancel") == "Cancel generation",
              en_descs.get("cancel"))
        en_cmds = [c.title for c in app.get_system_commands(app.screen)]
        check("英文系统命令条目", "Theme" in en_cmds and "Quit" in en_cmds, str(en_cmds))
        check("英文无中文条目", "切换主题" not in en_cmds, str(en_cmds))

        # ── 命令面板（英文）──
        app.action_command_palette()
        await pilot.pause()
        scr = app.screen
        check("英文面板 placeholder",
              getattr(scr, "_placeholder", "") == "Search for commands…",
              getattr(scr, "_placeholder", ""))
        try:
            scr.dismiss(None)
        except Exception:
            pass
        await pilot.pause()

        # ── 提示行（底部 hint）──
        _w = app.query_one("#prompt-hint")
        _c = getattr(_w, "content", "")
        hint_txt = _c if isinstance(_c, str) else str(getattr(_c, "plain", _c))
        check("英文提示行", "Enter=send" in hint_txt, hint_txt)

        # ── 绑定工厂本身 ──
        from bin.ai_lib import keymap as _km
        cn_b = {"menu_up": _km.label("tui.history_prev", "chinese")}
        en_b = {"menu_up": _km.label("tui.history_prev", "english")}
        check("工厂：中文描述", cn_b.get("menu_up") == "上一条历史", cn_b.get("menu_up"))
        check("工厂：英文描述", en_b.get("menu_up") == "Previous history", en_b.get("menu_up"))

    print("\n==== 无头验证 ====")
    ok = True
    for name, passed, detail in results:
        print(("  ✅ " if passed else "  ❌ ") + name + (("  [" + str(detail)[:60] + "]") if not passed else ""))
        ok = ok and passed
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
