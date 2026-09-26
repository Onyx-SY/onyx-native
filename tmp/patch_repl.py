# -*- coding: utf-8 -*-
"""step-6：AI REPL（prompt_toolkit）三改。

1. →（右方向键）直接接受虚影（对齐 lib/terminal/kb.py 语义）
2. 输入 `/` 立刻弹出斜杠命令列表（不必手动按 Tab）
3. 底部工具栏 / 多行前缀走中英双语

用法：python3 tmp/patch_repl.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_interactive.py")

REPLACEMENTS = [
    # ── 1+2. 新增两个键位（挂在 Tab 补全绑定之后）──
    (
        "    @_kb.add('s-tab', eager=True, filter=~is_searching)\n"
        "    def _complete_prev(event):\n"
        "        b = event.current_buffer\n"
        "        b.suggestion = None\n"
        "        if b.complete_state:\n"
        "            b.complete_previous()\n"
        "        else:\n"
        "            b.start_completion(select_first=False)\n",
        "    @_kb.add('s-tab', eager=True, filter=~is_searching)\n"
        "    def _complete_prev(event):\n"
        "        b = event.current_buffer\n"
        "        b.suggestion = None\n"
        "        if b.complete_state:\n"
        "            b.complete_previous()\n"
        "        else:\n"
        "            b.start_completion(select_first=False)\n"
        "\n"
        "    # ── →（右方向键）：有虚影直接接受（对齐 lib/terminal/kb.py）──\n"
        "    # 注：prompt_toolkit 自带的 load_auto_suggest_bindings() 也把 right 绑成「接受建议」，\n"
        "    # 但它排在 load_key_bindings()（emacs 的 cursor-right）之前，而 key_processor 命中的是\n"
        "    # 匹配列表的最后一个 → 实际生效的一直是光标右移，虚影永远吃不到。_kb 排在 merge 最后，\n"
        "    # 这里显式绑定即可覆盖。\n"
        "    @_kb.add('right', filter=~is_searching)\n"
        "    def _accept_ghost(event):\n"
        "        \"\"\"→：光标在末尾且有虚影 → 直接接受；否则右移一格。\"\"\"\n"
        "        b = event.current_buffer\n"
        "        sug = b.suggestion\n"
        "        if sug is not None and sug.text and b.document.is_cursor_at_the_end:\n"
        "            b.suggestion = None\n"
        "            b.insert_text(sug.text)\n"
        "        else:\n"
        "            b.cursor_position = min(len(b.text), b.cursor_position + 1)\n"
        "\n"
        "    # ── 输入 `/` 立刻弹出斜杠命令列表（不必手动按 Tab）──\n"
        "    @_kb.add('/', eager=True, filter=~is_searching)\n"
        "    def _slash_menu(event):\n"
        "        \"\"\"输入 `/`：插入字符并立即打开补全菜单（后续字符实时过滤）。\"\"\"\n"
        "        b = event.current_buffer\n"
        "        b.insert_text('/')\n"
        "        b.suggestion = None\n"
        "        b.start_completion(select_first=False)\n",
    ),
    # ── 3. 提示符多行前缀 + 底部工具栏双语 ──
    (
        '    def _ai_prompt() -> str:\n'
        '        base = _make_ai_prompt()\n'
        '        if _ml_state["active"]:\n'
        '            return f"[多行] {base}"\n'
        '        return base\n'
        '\n'
        '    def _bottom_toolbar() -> List[Tuple[str, str]]:\n'
        '        if _ml_state["active"]:\n'
        '            return [("class:toolbar", " 📝 多行模式：Enter=换行暂存 · Alt+Enter=统一发送 · Ctrl+C=清空退出 ")]\n'
        '        return [("class:toolbar", " Enter=发送 · Alt+Enter=多行模式 · Esc/Ctrl+D=退出 · /help=帮助 ")]\n',
        '    def _ai_prompt() -> str:\n'
        '        base = _make_ai_prompt()\n'
        '        if _ml_state["active"]:\n'
        '            return _t("repl_multiline_prefix", ctx.get("lang", "chinese")) + base\n'
        '        return base\n'
        '\n'
        '    def _bottom_toolbar() -> List[Tuple[str, str]]:\n'
        '        _lang = ctx.get("lang", "chinese")\n'
        '        if _ml_state["active"]:\n'
        '            return [("class:toolbar", _t("repl_toolbar_multiline", _lang))]\n'
        '        return [("class:toolbar", _t("repl_toolbar_normal", _lang))]\n',
    ),
]


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()

    for i, (old, new) in enumerate(REPLACEMENTS, 1):
        cnt = src.count(old)
        if cnt != 1:
            print(f"❌ 第 {i} 处替换：匹配 {cnt} 次（要求恰好 1 次）")
            return 1
        src = src.replace(old, new)

    tmp = TARGET + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, TARGET)
    print(f"✅ 已应用 {len(REPLACEMENTS)} 处替换 → {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
