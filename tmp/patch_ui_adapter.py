# -*- coding: utf-8 -*-
"""step-1：AI 斜杠命令里的裸 input() → bin/ai_lib/ui.py 适配器（TUI 下弹模态框）。

裸 input() 在 TUI 的 worker 线程里会抢 Textual 占用的 stdin → 永久阻塞，
表现为「/key、/model 之后界面完全没反应」。改用 ui.confirm / ui.text_input 后：
  - TUI：走 TUIAdapter 的模态框（主线程渲染）；
  - REPL/主 shell：无适配器 → 回退 inquirer/prompt_toolkit，行为与原来一致。

用法：python3 tmp/patch_ui_adapter.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPL = os.path.join(ROOT, "bin", "ai_interactive.py")
CONF = os.path.join(ROOT, "bin", "ai_lib", "config.py")

REPL_PAIRS = [
    # /key 的 y/N
    (
        '        choice = input(_t("change_key", lang)).strip().lower()\n'
        '        if choice == "y":\n'
        '            _setup_key_conf_interactive(lang)\n',
        '        # 经 ui 适配器取输入：TUI 下弹模态框（裸 input() 在 worker 线程会抢 stdin 卡死）\n'
        '        from bin.ai_lib.ui import confirm as _ui_confirm\n'
        '        if _ui_confirm(_t("change_key", lang), default=False, lang=lang):\n'
        '            _setup_key_conf_interactive(lang)\n',
    ),
    # /model 选择新模型
    (
        '            choice = input(_t("model_select_prompt", lang)).strip()\n',
        '            from bin.ai_lib.ui import text_input as _ui_text_input\n'
        '            choice = _ui_text_input(_t("model_select_prompt", lang), "", lang=lang).strip()\n',
    ),
    # /model 是否编辑参数
    (
        '                    if input(_t("edit_params_prompt", lang)).strip().lower() == "y":\n',
        '                    from bin.ai_lib.ui import confirm as _ui_confirm\n'
        '                    if _ui_confirm(_t("edit_params_prompt", lang), default=False, lang=lang):\n',
    ),
]

CONF_OLD = '''    # 参数（可选自定义）
    params = dict(info["params"])
    tune = input("自定义参数？(y/N): " if lang == "chinese" else "Customize params? (y/N): ").strip().lower()
    if tune == "y":
        try:
            t = input(f"  temperature [{params.get('temperature', 0.1)}]: ").strip()
            if t:
                params["temperature"] = float(t)
            tp = input(f"  top_p [{params.get('top_p', 0.2)}]: ").strip()
            if tp:
                params["top_p"] = float(tp)
            mt = input(f"  max_tokens [{params.get('max_tokens', 4096)}]: ").strip()
            if mt:
                params["max_tokens"] = int(mt)
            _tk_default = params.get("thinking", bool(info.get("thinking", False)))
            tk = input(f"  思考模式 [{'on' if _tk_default else 'off'}]: ").strip().lower()
'''

CONF_NEW = '''    # 参数（可选自定义）—— 全部经 ui 适配器取输入：TUI 下弹模态框，
    # 裸 input() 在 TUI 的 worker 线程会抢 Textual 占用的 stdin → 永久卡死。
    params = dict(info["params"])
    tune = ui_confirm(
        "自定义参数？" if lang == "chinese" else "Customize params?",
        default=False,
        lang=lang,
    )
    if tune:
        try:
            t = ui_text_input("temperature",
                              str(params.get("temperature", 0.1)), lang=lang).strip()
            if t:
                params["temperature"] = float(t)
            tp = ui_text_input("top_p",
                               str(params.get("top_p", 0.2)), lang=lang).strip()
            if tp:
                params["top_p"] = float(tp)
            mt = ui_text_input("max_tokens",
                               str(params.get("max_tokens", 4096)), lang=lang).strip()
            if mt:
                params["max_tokens"] = int(mt)
            _tk_default = params.get("thinking", bool(info.get("thinking", False)))
            tk = ui_text_input("思考模式" if lang == "chinese" else "Thinking",
                               "on" if _tk_default else "off", lang=lang).strip().lower()
'''


def patch(path, pairs):
    with io.open(path, "r", encoding="utf-8") as f:
        src = f.read()
    for i, (old, new) in enumerate(pairs, 1):
        cnt = src.count(old)
        if cnt != 1:
            print(f"❌ {os.path.basename(path)} 第 {i} 处：匹配 {cnt} 次")
            return False
        src = src.replace(old, new)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, path)
    print(f"✅ {os.path.basename(path)}：{len(pairs)} 处替换完成")
    return True


def main() -> int:
    # config.py 需要补 confirm 导入
    with io.open(CONF, "r", encoding="utf-8") as f:
        conf_src = f.read()
    old_imp = "from .ui import select_option, text_input as ui_text_input, secret_input as ui_secret_input\n"
    new_imp = ("from .ui import (select_option, text_input as ui_text_input,\n"
               "                   secret_input as ui_secret_input, confirm as ui_confirm)\n")
    if conf_src.count(old_imp) == 1:
        conf_src = conf_src.replace(old_imp, new_imp)
        with io.open(CONF, "w", encoding="utf-8") as f:
            f.write(conf_src)
        print("✅ config.py：已补 confirm 导入")
    elif "confirm as ui_confirm" not in conf_src:
        print("❌ config.py：未找到预期的 ui 导入行")
        return 1

    ok = patch(REPL, REPL_PAIRS)
    ok = patch(CONF, [(CONF_OLD, CONF_NEW)]) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
