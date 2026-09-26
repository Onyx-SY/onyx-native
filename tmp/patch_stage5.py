# -*- coding: utf-8 -*-
"""Stage5 补丁：健壮性与美观（补全菜单复活 / 错误块统一 / 回复留白）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")
CMD = os.path.join(ROOT, "bin", "ai_cmd.py")

PATCHES = []


def patch(path, old, new):
    PATCHES.append((path, old, new))


# ── 1) on_input_changed 重复定义：后者覆盖前者 → 打字时补全菜单永不出现 ──
patch(
    TUI,
    """        def on_input_changed(self, event) -> None:
            \"\"\"单行框内容变化 → 刷新补全菜单。\"\"\"
            try:
                if getattr(event.input, "id", "") == "prompt":
                    self._menu_update(event.value)
            except Exception:
                pass""",
    """        def on_input_changed(self, event) -> None:
            \"\"\"输入变化：刷新补全菜单（仅单行框）+ 历史游标回到「最新」。

            ⚠️ 旧实现把本方法定义了**两次**（后面的覆盖前面）→ 打字时补全菜单永远不弹
            （只有按 Tab 才出现），前一份实现沦为死代码。这里合并成一份，两个行为都保留。
            \"\"\"
            try:
                if not getattr(self, "_hist_setting", False) and self._hist_idx != len(self._hist):
                    self._hist_idx = len(self._hist)
            except Exception:
                pass
            try:
                if getattr(getattr(event, "input", None), "id", "") == "prompt":
                    self._menu_update(event.value)
            except Exception:
                pass""",
)

patch(
    TUI,
    """
        def on_input_changed(self, event) -> None:
            \"\"\"用户手动编辑 → 历史游标回到「最新」（否则上下翻会跳回旧位置）。\"\"\"
            if getattr(self, "_hist_setting", False):
                return
            if self._hist_idx != len(self._hist):
                self._hist_idx = len(self._hist)

        # ── 多行模式：""",
    """
        # ── 多行模式：""",
)

# ── 2) TUI 错误提示：统一成「角色标签块」且只打印一次 ──
patch(
    CMD,
    """        if has_error:
            error_str = str(ai_result["error"])
            if "Request failed" in error_str or "Connection" in error_str or "timeout" in error_str.lower():
                console.print(lang_text["api_conn_fail"], style="bold red")
            else:
                console.print(f"❌ {lang_text['api_error'].format(error_str)}", style="bold red")""",
    """        if has_error:
            error_str = str(ai_result["error"])
            _conn_fail = ("Request failed" in error_str or "Connection" in error_str
                          or "timeout" in error_str.lower())
            if _tui_mode:
                # TUI：错误同样走「角色标签块」（与 AI 回复同一套视觉语言），
                # 并且只在这里打印一次（下方原有一个 elif 分支会再打一遍）。
                _err_body = (lang_text["api_conn_fail"] if _conn_fail
                             else f"❌ {lang_text['api_error'].format(error_str)}")
                _tui_write_rich(tui_plain(_err_body, title="🤖 AI",
                                          border_style="red", box=ROUNDED))
            elif _conn_fail:
                console.print(lang_text["api_conn_fail"], style="bold red")
            else:
                console.print(f"❌ {lang_text['api_error'].format(error_str)}", style="bold red")""",
)

patch(
    CMD,
    """        elif _tui_mode and has_error:
            # TUI：Live 为 transient，错误面板同样需要补打
            _err_short = str(ai_result.get("error", ""))[:200]
            console.print(tui_plain(f"❌ {_err_short}", title="🤖 AI",
                                    border_style="red", box=ROUNDED))""",
    """        elif _tui_mode and has_error:
            # 错误已在上面的 has_error 分支里以「角色标签块」打印过，这里不再重复。
            pass""",
)

# ── 3) 回复块之后留一行呼吸（与下一轮的轮次线/工具块拉开）──
patch(
    CMD,
    """            _tui_wrote = _tui_write_rich(_reply_panel) if _tui_mode else False
            if not _tui_wrote and (not _live_shown or _tui_mode):
                console.print(_reply_panel)""",
    """            _tui_wrote = _tui_write_rich(_reply_panel) if _tui_mode else False
            if not _tui_wrote and (not _live_shown or _tui_mode):
                console.print(_reply_panel)
            if _tui_mode:
                _tui_write_rich("")   # 回复块与后续内容之间留一行空白（统一呼吸感）""",
)


def main():
    cache = {}
    for path, old, new in PATCHES:
        if path not in cache:
            with open(path, encoding="utf-8") as f:
                cache[path] = f.read()
        text = cache[path]
        n = text.count(old)
        if n != 1:
            print(f"FAIL {os.path.relpath(path, ROOT)}: 命中 {n} 次（期望 1）")
            print(old[:300])
            return 1
        cache[path] = text.replace(old, new, 1)
        print(f"OK   {os.path.relpath(path, ROOT)}: {len(old)}B → {len(new)}B")
    for path, text in cache.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
