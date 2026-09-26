# -*- coding: utf-8 -*-
"""step-3/4/5 验证：TUI 多行输入框（折叠/Alt+Enter/发送）+ 历史虚影 + → 接受。

无头运行（Textual run_test），不需要真实终端。

用法：python3 tmp/tui_multiline_check.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bin.ai_tui import _build_tui, _t  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(f"{'✅' if cond else '❌'} {name}{(' | ' + str(extra)) if extra else ''}")
    if not cond:
        FAILS.append(name)
    return cond


def strip_text(strip):
    return "".join(seg.text for seg in strip)


async def main():
    App = _build_tui()
    home = tempfile.mkdtemp(prefix="onyx_tui_test_")
    # 造一份历史（prompt_toolkit FileHistory 格式：每行前缀 '+'）
    hist_dir = os.path.join(home, ".config", "onyx", "ai")
    os.makedirs(hist_dir, exist_ok=True)
    with open(os.path.join(hist_dir, "history"), "w", encoding="utf-8") as f:
        f.write("+检查一下当前目录\n+/help\n+/exit\n+帮我把 lib 里的日志改成双语\n")

    app = App({"user_home_dir": home}, {"lang": "chinese", "session_id": "t",
                                        "memory_mode": "global", "cwd": os.getcwd()})

    async with app.run_test(size=(100, 30)) as pilot:
        inp = app.query_one("#prompt")
        ml = app.query_one("#prompt-ml")
        hint = app.query_one("#prompt-hint")

        # 记录提交（避免工作线程把队列消费掉、也不真的去调 AI）
        submitted = []
        app._submit = lambda text: submitted.append(text)

        # ── 1. 双控件初始状态 ──
        check("初始：单行框可见、多行框隐藏", inp.display and not ml.display)
        check("初始：单行框挂了虚影 suggester", inp.suggester is not None)
        check("初始：提示行为单行文案", strip_text_hint(hint) == _t("tui_single_hint", "chinese"),
              strip_text_hint(hint))

        # ── 2. 虚影：斜杠命令 + 历史 ──
        sug = app._suggester
        check("虚影：/he → /help", await sug.get_suggestion("/he") == "/help")
        check("虚影：/ 有候选", (await sug.get_suggestion("/")) is not None)
        check("虚影：历史前缀命中", await sug.get_suggestion("检查一下") == "检查一下当前目录")
        check("虚影：无候选返回 None", await sug.get_suggestion("zzz不存在") is None)

        # ── 3. → 直接接受虚影（Textual Input 原生行为）──
        inp.value = "/he"
        await pilot.pause(0.3)
        check("→ 之前：输入框仍是 /he", inp.value == "/he")
        await pilot.press("right")
        await pilot.pause(0.2)
        check("→ 直接接受虚影 → /help", inp.value == "/help", inp.value)
        inp.value = ""

        # ── 4. Alt+Enter 进入多行模式 ──
        inp.value = "第一行"
        inp.focus()
        await pilot.pause(0.1)
        await pilot.press("alt+enter")
        await pilot.pause(0.2)
        check("Alt+Enter：切到多行框", ml.display and not inp.display)
        check("Alt+Enter：文本带过去并另起一行", ml.text == "第一行\n", repr(ml.text))
        check("Alt+Enter：提示行切为多行文案", strip_text_hint(hint) == _t("tui_ml_hint", "chinese"),
              strip_text_hint(hint))

        # ── 5. 折叠：4 行以内不折叠 ──
        ml.text = "1\n2\n3\n4"
        await pilot.pause(0.2)
        check("4 行：高度=4、无省略行", ml.styles.height is not None and len(ml._row_map()) == 4,
              len(ml._row_map()))

        # ── 6. 折叠：6 行 → 上2 + 省略 + 下2（共 5 行）──
        ml.text = "L1\nL2\nL3\nL4\nL5\nL6"
        await pilot.pause(0.2)
        rows = ml._row_map()
        check("6 行：显示行数=5", len(rows) == 5, len(rows))
        check("6 行：上两行=文档0/1", rows[0][1] == 0 and rows[1][1] == 1, rows[:2])
        check("6 行：第3行是省略行", rows[2][1] is None, rows[2])
        check("6 行：下两行=文档4/5", rows[3][1] == 4 and rows[4][1] == 5, rows[3:])
        omitted = strip_text(ml._render_omitted())
        check("省略行文案含「省略 2 行」", "省略 2 行" in omitted, omitted.strip())
        check("省略行是暗色", "dim" in str(list(ml._render_omitted())[0].style), )
        # 实际渲染：第 3 行应是省略行
        rendered = strip_text(ml.render_line(2))
        check("render_line(2) 输出省略提示", "省略" in rendered, rendered.strip())
        check("render_line(0) 是第 1 行内容", "L1" in strip_text(ml.render_line(0)),
              strip_text(ml.render_line(0)).strip())
        check("render_line(4) 是最后一行内容", "L6" in strip_text(ml.render_line(4)),
              strip_text(ml.render_line(4)).strip())
        check("光标行映射：末行→显示行4", ml._display_row_of(5) == 4)

        # ── 7. Alt+Enter 发送（多行整体提交）──
        await pilot.press("alt+enter")
        await pilot.pause(0.3)
        check("Alt+Enter 发送：退回单行框", inp.display and not ml.display)
        check("Alt+Enter 发送：多行内容整体提交",
              len(submitted) == 1 and submitted[0] == "L1\nL2\nL3\nL4\nL5\nL6", submitted)
        check("Alt+Enter 发送：输入框已清空", ml.text == "" and inp.value == "")
        check("Alt+Enter 发送：提示行回到单行文案",
              strip_text_hint(hint) == _t("tui_single_hint", "chinese"))

        # ── 8. 单行 Enter 发送 ──
        inp.value = "你好"
        await pilot.press("enter")
        await pilot.pause(0.2)
        check("单行 Enter 发送提交", len(submitted) == 2 and submitted[1] == "你好", submitted)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：{FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


def strip_text_hint(static_widget):
    """取 Static 当前内容（Static 把内容存在 _Static__content）。"""
    try:
        r = getattr(static_widget, "_Static__content", None)
        return r if isinstance(r, str) else str(r)
    except Exception:
        return ""


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
