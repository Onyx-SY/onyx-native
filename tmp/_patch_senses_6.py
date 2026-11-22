# -*- coding: utf-8 -*-
"""补丁 6：/config 增加「主动感知」开关 + 新增 /senses 命令。"""
import io
import sys

P = "bin/ai_interactive.py"
s = io.open(P, encoding="utf-8").read()


def rep(old, new, cnt=1):
    global s
    c = s.count(old)
    if c != cnt:
        print("FAIL count=%d (expect %d): %r" % (c, cnt, old[:70]))
        sys.exit(1)
    s = s.replace(old, new, cnt)


# ── 1. slash 命令表 ──
rep('''    "/config": "⚙️ 统一配置 AI 模型/密钥/参数",''',
    '''    "/config": "⚙️ 统一配置 AI 模型/密钥/参数",
    "/senses": "👁 主动感知/视觉注意力 (list / on / off / clear)",''')

rep('''    "/config": "⚙️ Unified AI config (model/key/params)",''',
    '''    "/config": "⚙️ Unified AI config (model/key/params)",
    "/senses": "👁 Proactive perception / visual attention (list / on / off / clear)",''')

# ── 2. /config 菜单加一项 ──
rep('''                opts = ["🔄 切换平台", "🤖 切换模型", "🔑 更换密钥",
                        "⚙️ 编辑参数", "🌐 自定义 URL", "⌨️ 按键设置", "❌ 关闭"]''',
    '''                opts = ["🔄 切换平台", "🤖 切换模型", "🔑 更换密钥",
                        "⚙️ 编辑参数", "🌐 自定义 URL", "⌨️ 按键设置",
                        "👁 主动感知", "❌ 关闭"]''')

rep('''                opts = ["🔄 Change platform", "🤖 Change model", "🔑 Change key",
                        "⚙️ Edit params", "🌐 Custom URL", "⌨️ Key bindings", "❌ Close"]''',
    '''                opts = ["🔄 Change platform", "🤖 Change model", "🔑 Change key",
                        "⚙️ Edit params", "🌐 Custom URL", "⌨️ Key bindings",
                        "👁 Proactive perception", "❌ Close"]''')

# ── 3. 分派：idx 6 = 主动感知 ──
rep('''            # ── ⌨️ 按键设置 ──
            elif idx == 5:
                _keymap_menu(lang, ctx)''',
    '''            # ── ⌨️ 按键设置 ──
            elif idx == 5:
                _keymap_menu(lang, ctx)

            # ── 👁 主动感知（视觉感官总开关）──
            elif idx == 6:
                from bin.ai_lib import senses as _senses
                _on = not _senses.is_enabled()
                _senses.set_enabled(_on, approve=_on)
                if lang == "chinese":
                    _ok = ("✅ 主动感知已开启：AI 可在你没对话时感知文件/工具变化"
                           "（会消耗 Token）" if _on else "✅ 主动感知已关闭（不再主动提醒）")
                else:
                    _ok = ("✅ Proactive perception ON (may consume tokens while idle)"
                           if _on else "✅ Proactive perception OFF")
                console.print(f"[green]{_ok}[/]")''')

# ── 4. /senses 命令 ──
rep('''    elif cmd == "/param":
        """快速查看/设置模型参数"""''',
    '''    elif cmd == "/senses":
        """👁 主动感知 / 视觉注意力管理"""
        from bin.ai_lib import senses as _senses
        sub = (args[0].lower() if args else "list")
        if sub in ("on", "enable", "1"):
            _senses.set_enabled(True, approve=True)
            console.print("👁 " + ("主动感知已开启（后台感官线程运行中）" if lang == "chinese"
                                   else "proactive perception ON"))
        elif sub in ("off", "disable", "0"):
            _senses.set_enabled(False)
            console.print("👁 " + ("主动感知已关闭（视觉不再注入、不再主动提醒）" if lang == "chinese"
                                   else "proactive perception OFF"))
        elif sub in ("clear", "reset"):
            _r = _senses.clear_attentions()
            console.print("👁 " + (("已清空 %d 个注意力" % _r.get("removed", 0)) if lang == "chinese"
                                   else ("cleared %d attention(s)" % _r.get("removed", 0))))
        elif sub in ("status", "st"):
            console.print(_senses.status_text())
        else:
            console.print(_senses.handle_eyes_action(action="list"))
        return True

    elif cmd == "/param":
        """快速查看/设置模型参数"""''')

io.open(P, "w", encoding="utf-8").write(s)
print("OK")
