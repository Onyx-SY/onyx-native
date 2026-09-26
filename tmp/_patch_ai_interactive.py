# -*- coding: utf-8 -*-
"""给 ai_interactive.py 加：⌨️ 按键设置菜单（_keymap_menu）+ /config 入口。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "ai_interactive.py")
src = io.open(P, encoding="utf-8").read()

FUNC = '''def _keymap_menu(lang: str, ctx: dict) -> None:
    """⌨️ AI 按键设置：列出全部 AI 动作的当前键，可逐个改键 / 恢复默认。

    TUI 下由 ui.capture_key 弹「按键捕获」框（按哪个键就是哪个键）；
    对话模式（非 TUI）下改为文本输入（如 alt+enter），由 keymap 校验。
    """
    from bin.ai_lib import keymap as km
    from bin.ai_lib.ui import select_option, capture_key

    km.init(ctx.get("user_home_dir") or "")

    while True:
        opts, ids = [], []
        for gid in km.GROUP_ORDER:
            glabel = km.GROUP_LABELS[gid][1 if lang == "english" else 0]
            for aid, g, _cn, _en, _d in km.ACTIONS:
                if g != gid:
                    continue
                mark = " *" if km.is_customized(aid) else ""
                opts.append(f"[{glabel}] {km.label(aid, lang)}  →  {km.pretty(aid)}{mark}")
                ids.append(aid)
        opts.append("♻️ 恢复全部默认" if lang == "chinese" else "♻️ Reset all")
        ids.append("__reset_all__")
        opts.append("❌ 关闭" if lang == "chinese" else "❌ Close")
        ids.append("__close__")

        choice = select_option(
            "选择要修改的动作（* = 已自定义）:" if lang == "chinese"
            else "Pick an action to rebind (* = customized):",
            opts, default=opts[0], lang=lang)
        if not choice or choice not in opts:
            return
        aid = ids[opts.index(choice)]
        if aid == "__close__":
            return
        if aid == "__reset_all__":
            km.reset_all()
            console.print("[green]" + ("✅ 已恢复全部默认按键" if lang == "chinese"
                                       else "✅ All bindings reset to default") + "[/]")
            continue

        cur = km.pretty(aid)
        act_opts = ([f"⌨️ 改键（当前：{cur}）", "♻️ 恢复该动作默认", "↩️ 返回"]
                    if lang == "chinese"
                    else [f"⌨️ Rebind (now: {cur})", "♻️ Reset this action", "↩️ Back"])
        act = select_option(km.label(aid, lang), act_opts, default=act_opts[0], lang=lang)
        if not act or act == act_opts[2]:
            continue
        if act == act_opts[1]:
            km.reset(aid)
            console.print("[green]" + ("✅ 已恢复默认" if lang == "chinese"
                                       else "✅ Reset to default") + "[/]")
            continue

        newkey = capture_key(
            "按下要绑定的按键（TUI 直接按键；对话模式请输入如 alt+enter）:"
            if lang == "chinese" else
            "Press the key to bind (TUI: press it; chat mode: type e.g. alt+enter):",
            lang=lang)
        if not newkey:
            console.print("[dim]" + ("已取消" if lang == "chinese" else "cancelled") + "[/]")
            continue
        ok, _err = km.set_binding(aid, [newkey])
        if ok:
            console.print("[green]" + (
                f"✅ 已绑定 {km.label(aid, lang)} → {newkey}" if lang == "chinese"
                else f"✅ Bound {km.label(aid, lang)} → {newkey}") + "[/]")
            console.print("[dim]" + (
                "（TUI 主界面键位重启 AI 后生效；多行框/补全键下次进入即生效）"
                if lang == "chinese" else
                "(TUI main-screen keys apply after restarting AI)") + "[/]")
        else:
            console.print("[red]" + (
                f"❌ 无效按键：{newkey}" if lang == "chinese"
                else f"❌ Invalid key: {newkey}") + "[/]")


'''

ANCHOR_FUNC = "def _dispatch_slash(cmd_line: str, ctx: Dict[str, Any]) -> bool:"

OLD_OPTS = '''            if lang == "chinese":
                opts = ["🔄 切换平台", "🤖 切换模型", "🔑 更换密钥",
                        "⚙️ 编辑参数", "🌐 自定义 URL", "❌ 关闭"]
            else:
                opts = ["🔄 Change platform", "🤖 Change model", "🔑 Change key",
                        "⚙️ Edit params", "🌐 Custom URL", "❌ Close"]'''

NEW_OPTS = '''            if lang == "chinese":
                opts = ["🔄 切换平台", "🤖 切换模型", "🔑 更换密钥",
                        "⚙️ 编辑参数", "🌐 自定义 URL", "⌨️ 按键设置", "❌ 关闭"]
            else:
                opts = ["🔄 Change platform", "🤖 Change model", "🔑 Change key",
                        "⚙️ Edit params", "🌐 Custom URL", "⌨️ Key bindings", "❌ Close"]'''

OLD_TAIL = '''                    conf["api_url"] = url
                    _save_conf(conf, ctx)
                    _ok = "✅ API URL updated" if lang == "english" else "✅ API 地址已更新"
                    console.print(f"[green]{_ok}[/]")

        return True'''

NEW_TAIL = '''                    conf["api_url"] = url
                    _save_conf(conf, ctx)
                    _ok = "✅ API URL updated" if lang == "english" else "✅ API 地址已更新"
                    console.print(f"[green]{_ok}[/]")

            # ── ⌨️ 按键设置 ──
            elif idx == 5:
                _keymap_menu(lang, ctx)

        return True'''

for name, old, new, dup in (
        ("_keymap_menu", ANCHOR_FUNC, FUNC + ANCHOR_FUNC, "def _keymap_menu("),
        ("opts", OLD_OPTS, NEW_OPTS, "⌨️ 按键设置"),
        ("handler", OLD_TAIL, NEW_TAIL, "elif idx == 5:")):
    if dup in src:
        print(f"SKIP {name}: 已存在")
        continue
    n = src.count(old)
    if n != 1:
        print(f"FAIL {name}: 锚点命中 {n} 次")
        sys.exit(1)
    src = src.replace(old, new, 1)
    print(f"OK   {name}")

tmp = P + ".tmp"
with io.open(tmp, "w", encoding="utf-8") as f:
    f.write(src)
os.replace(tmp, P)
print("已写回")
