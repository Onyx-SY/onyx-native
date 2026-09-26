# -*- coding: utf-8 -*-
"""step-2/7 补充：把 TUI 危险确认框 + REPL /doctor 面板改为双语取词。

用法：python3 tmp/patch_i18n_gaps.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TUI = os.path.join(ROOT, "bin", "ai_tui.py")
REPL = os.path.join(ROOT, "bin", "ai_interactive.py")

TUI_REPLACEMENTS = [
    (
        '        def confirm_dangerous(self, title, command, reason, lang="chinese",\n'
        '                              timeout=None, timeout_default=False):\n'
        '            _cmd = "Command" if lang == "english" else "命令"\n'
        '            _risk = "Risk" if lang == "english" else "风险"\n'
        '            msg = f"{title}\\n\\n{_cmd}: {command}\\n{_risk}: {reason}"\n',
        '        def confirm_dangerous(self, title, command, reason, lang="chinese",\n'
        '                              timeout=None, timeout_default=False):\n'
        '            msg = (f"{title}\\n\\n{_t(\'tui_cmd_label\', lang)}: {command}\\n"\n'
        '                   f"{_t(\'tui_risk_label\', lang)}: {reason}")\n',
    ),
    (
        '            return False, "n", ("User refused" if lang == "english" else "用户拒绝执行")\n',
        '            return False, "n", _t("tui_user_refused", lang)\n',
    ),
    (
        '        try:\n'
        '            print(f"⚠️ TUI 启动失败（{type(e).__name__}: {e}），已回退 REPL。")\n'
        '        except Exception:\n'
        '            pass\n',
        '        try:\n'
        '            print(_t("tui_start_fail", current_lang, err=f"{type(e).__name__}: {e}"))\n'
        '        except Exception:\n'
        '            pass\n',
    ),
]

REPL_OLD = '''        if conf and conf.get("api_key"):
            plat = conf.get("platform", "?")
            model = conf.get("model", "未设置")
            proto = _resolve_protocol(plat, model)
            _item(True, "AI 密钥", f"平台={plat} 模型={model} 协议={proto}")
        else:
            _item(False, "AI 密钥", "未配置（运行 ai 命令引导配置）")
    except Exception as e:
        _item(False, "AI 密钥", f"读取失败: {e}")

    try:
        from bin.ai_cmd import _SUPPORTED_PLATFORMS
        _item(True, "平台模型", f"已加载 {len(_SUPPORTED_PLATFORMS)} 个平台")
    except Exception:
        _item(False, "平台模型", "models.json 加载失败")

    try:
        from bin.ai_cmd import list_mcp_servers
        mcp_text = str(list_mcp_servers())
        _item("error" not in mcp_text.lower(), "MCP 服务器", mcp_text[:60])
    except Exception:
        _item(False, "MCP 服务器", "模块异常")

    try:
        mem_root = _memory_base_dir(home, ctx.get("memory_mode", "global"), ctx.get("cwd"))
        lib_dir = os.path.join(mem_root, ".ai_s", "library")
        if os.path.isdir(lib_dir):
            count = len([f for f in os.listdir(lib_dir) if f.endswith(".txt")])
            _item(True, "记忆目录", f"{mem_root}（{count} 条记录）")
        else:
            _item(True, "记忆目录", f"{mem_root}（尚未创建记录）")
    except Exception as e:
        _item(False, "记忆目录", str(e))

    try:
        from bin.ai_cmd import ROOT_DIR
        tools_dir = os.path.join(ROOT_DIR, "tools")
        if os.path.isdir(tools_dir):
            n = len([d for d in os.listdir(tools_dir) if os.path.isdir(os.path.join(tools_dir, d))])
            _item(True, "工具目录", f"{n} 个工具")
        else:
            _item(True, "工具目录", "tools/ 不存在（TBS 模式）")
    except Exception:
        _item(False, "工具目录", "无法读取")

    _item(True, "运行环境", f"cwd={ctx.get('cwd', os.getcwd())}")
    _item(True, "记忆模式", "project（当前目录专属）" if ctx.get("memory_mode") == "project" else "global（全局 library）")
    _item(True, "语言", lang)
'''

REPL_NEW = '''        if conf and conf.get("api_key"):
            plat = conf.get("platform", "?")
            model = conf.get("model") or _t("unset", lang)
            proto = _resolve_protocol(plat, model)
            _item(True, _t("doctor_item_key", lang),
                  _t("doctor_key_info", lang, plat=plat, model=model, proto=proto))
        else:
            _item(False, _t("doctor_item_key", lang), _t("doctor_key_unconfigured", lang))
    except Exception as e:
        _item(False, _t("doctor_item_key", lang), _t("doctor_key_read_fail", lang, err=e))

    try:
        from bin.ai_cmd import _SUPPORTED_PLATFORMS
        _item(True, _t("doctor_item_platform", lang),
              _t("doctor_platforms_loaded", lang, n=len(_SUPPORTED_PLATFORMS)))
    except Exception:
        _item(False, _t("doctor_item_platform", lang), _t("doctor_models_load_fail", lang))

    try:
        from bin.ai_cmd import list_mcp_servers
        mcp_text = str(list_mcp_servers())
        _item("error" not in mcp_text.lower(), _t("doctor_item_mcp", lang), mcp_text[:60])
    except Exception:
        _item(False, _t("doctor_item_mcp", lang), _t("doctor_module_error", lang))

    try:
        mem_root = _memory_base_dir(home, ctx.get("memory_mode", "global"), ctx.get("cwd"))
        lib_dir = os.path.join(mem_root, ".ai_s", "library")
        if os.path.isdir(lib_dir):
            count = len([f for f in os.listdir(lib_dir) if f.endswith(".txt")])
            _item(True, _t("doctor_item_memory", lang),
                  _t("doctor_memory_records", lang, path=mem_root, n=count))
        else:
            _item(True, _t("doctor_item_memory", lang),
                  _t("doctor_memory_empty", lang, path=mem_root))
    except Exception as e:
        _item(False, _t("doctor_item_memory", lang), str(e))

    try:
        from bin.ai_cmd import ROOT_DIR
        tools_dir = os.path.join(ROOT_DIR, "tools")
        if os.path.isdir(tools_dir):
            n = len([d for d in os.listdir(tools_dir) if os.path.isdir(os.path.join(tools_dir, d))])
            _item(True, _t("doctor_item_tools", lang), _t("doctor_tools_count", lang, n=n))
        else:
            _item(True, _t("doctor_item_tools", lang), _t("doctor_tools_missing", lang))
    except Exception:
        _item(False, _t("doctor_item_tools", lang), _t("doctor_unreadable", lang))

    _item(True, _t("doctor_item_runtime", lang), f"cwd={ctx.get('cwd', os.getcwd())}")
    _item(True, _t("doctor_item_memmode", lang),
          _t("doctor_memmode_project", lang) if ctx.get("memory_mode") == "project"
          else _t("doctor_memmode_global", lang))
    _item(True, _t("doctor_item_lang", lang), lang)
'''


def patch(path, pairs):
    with io.open(path, "r", encoding="utf-8") as f:
        src = f.read()
    for i, (old, new) in enumerate(pairs, 1):
        cnt = src.count(old)
        if cnt != 1:
            print(f"❌ {os.path.basename(path)} 第 {i} 处：匹配 {cnt} 次（要求 1 次）")
            return False
        src = src.replace(old, new)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, path)
    print(f"✅ {os.path.basename(path)}：{len(pairs)} 处替换完成")
    return True


def main() -> int:
    ok = patch(TUI, TUI_REPLACEMENTS)
    ok = patch(REPL, [(REPL_OLD, REPL_NEW)]) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
