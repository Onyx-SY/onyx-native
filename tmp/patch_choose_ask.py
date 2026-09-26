# -*- coding: utf-8 -*-
"""choose_ask：Esc 取消不再被当成「选了第一项」，而是明确回传「用户未选择」。"""
import io

P = "bin/ai_cmd.py"
s = io.open(P, encoding="utf-8").read()

old = '''        selected = select_option(
            message=question,
            options=all_options,
            default=all_options[0],
        )

        if selected == none_label:'''
new = '''        selected = select_option(
            message=question,
            options=all_options,
            default=all_options[0],
        )

        if not selected:
            # Esc 取消 / 交互不可用 → 不能当成「选了第一项」，明确回传「未选择」
            return _mcp_t("⏹ 用户未选择（已取消）", "⏹ User made no selection (cancelled)")

        if selected == none_label:'''

assert s.count(old) == 1, s.count(old)
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
print("ok")
