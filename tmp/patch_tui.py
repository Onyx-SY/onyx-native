# -*- coding: utf-8 -*-
"""重做 Onyx AI TUI 视觉层：主题 + CSS + 组件（幂等，命中数不符即中止）。"""
import io
import sys

P = "bin/ai_tui.py"
src = io.open(P, encoding="utf-8").read()


def rep(old, new, tag):
    global src
    n = src.count(old)
    if n != 1:
        print(f"❌ {tag}: 命中 {n} 次（应为 1）")
        sys.exit(1)
    src = src.replace(old, new)
    print(f"✅ {tag}")


# ── 1) 主题 + 紧凑数字工具 ──────────────────────────────────────
OLD_MD_TAIL = '''    return _ONYX_MD_THEME


def _ai_history_path(user_home_dir: str) -> str:'''

NEW_MD_TAIL = '''    return _ONYX_MD_THEME


# ── Onyx 品牌主题（调色板与消息语言同源，见 bin/ai_lib/ui.py）──
_ONYX_THEME = None


def _onyx_theme():
    """注册用的 Textual 主题（深色宝石调；懒加载避免拖慢启动）。"""
    global _ONYX_THEME
    if _ONYX_THEME is None:
        from textual.theme import Theme
        from bin.ai_lib.ui import ONYX_PALETTE as _P, ONYX_VARS as _V
        _ONYX_THEME = Theme(
            name="onyx",
            dark=True,
            primary=_P["primary"],
            secondary=_P["secondary"],
            accent=_P["accent"],
            foreground=_P["foreground"],
            background=_P["background"],
            surface=_P["surface"],
            panel=_P["panel"],
            success=_P["success"],
            warning=_P["warning"],
            error=_P["error"],
            variables=dict(_V),
        )
    return _ONYX_THEME


def _fmt_compact(n) -> str:
    """紧凑数字：12345 → 12.3k（状态栏省宽度）。"""
    try:
        n = int(n)
    except Exception:
        return str(n)
    if n >= 1000000:
        return f"{n / 1000000:.1f}M"
    if n >= 10000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


def _ai_history_path(user_home_dir: str) -> str:'''
rep(OLD_MD_TAIL, NEW_MD_TAIL, "主题 + _fmt_compact")

# ── 2) CSS 重写 ────────────────────────────────────────────────
NEW_CSS = '''#body { height: 1fr; }
        #log-wrap { width: 1fr; height: 1fr; }

        /* 阅读区：一条左侧发丝线，去掉四边框 → 少盒子、多留白 */
        #log { width: 1fr; height: 1fr; border-left: solid $onyx-rail; padding: 0 2;
               background: $background; overflow-x: hidden;
               scrollbar-size-vertical: 1; scrollbar-color: $onyx-rail;
               scrollbar-color-hover: $accent; scrollbar-background: $background; }
        /* 活动行：左对齐、accent 转圈，不再是一条居中色带 */
        #thinking { height: 1; display: none; padding: 0 2; color: $accent;
                    background: $background; }
        /* 流式预览：顶部一条发丝线，与主日志同宽同缩进 */
        #stream { height: auto; max-height: 8; display: none; padding: 0 2;
                  border-top: solid $onyx-rule; background: $background;
                  overflow-x: hidden; }

        /* 侧栏：左分隔线 + 小节标题（无边框） */
        #sidebar { width: 34; display: none; border-left: solid $onyx-rail;
                   padding: 0 2; background: $background; }
        Screen.wide #sidebar { display: block; }
        .panel-title { text-style: bold; color: $onyx-muted; padding-top: 1; }
        #todo-body { color: $foreground; }
        #files { background: $background; padding: 0; }

        /* 底部输入区 */
        #prompt-wrap { dock: bottom; height: auto; background: $background; }
        #todo-strip { height: auto; display: none; padding: 0 2; color: $onyx-muted; }
        #status-bar { height: 1; color: $onyx-muted; padding: 0 2; }
        #prompt { width: 100%; background: $surface; border: round $onyx-rail;
                  color: $foreground; }
        #prompt:focus { border: round $accent; }
        #prompt-ml { width: 100%; display: none; background: $surface;
                     border: round $onyx-rail; }
        #prompt-ml:focus { border: round $accent; }
        #prompt-hint { height: 1; color: $onyx-dim; padding: 0 2; }
        #complete-menu { display: none; height: auto; max-height: 8;
                         border: round $accent; background: $panel; }

        /* 模态框 */
        ModalScreen { align: center middle; background: $background 70%; }
        #modal { width: 80%; max-width: 96; height: auto; max-height: 80%;
                 padding: 1 2; background: $panel; border: round $accent; }
        #modal-msg { padding-bottom: 1; color: $foreground; }
        #modal-warn { color: $warning; padding-bottom: 1; }
        #modal-code { color: $error; text-style: bold; padding-bottom: 1; }
        #modal-btns { height: auto; }
        #modal-btns Button { margin-right: 2; }
        #hist-list { max-height: 12; background: $surface; }
        '''

_i = src.index('        CSS = """')
_j = src.index('        """\n', _i + 16)
src = src[: _i + len('        CSS = """\n')] + NEW_CSS + src[_j + len('        """\n'):]
print("✅ CSS 重写")

# ── 3) on_mount：注册主题 + 品牌横幅 ────────────────────────────
rep('''            self._log(_t("tui_welcome", self._lang))''',
    '''            self._banner()''', "on_mount 欢迎语 → 横幅")

rep('''            # Markdown 主题：RichLog 是用 **Textual 自己的 console** 渲染的，
            # 必须推给它，否则标题只有「粗体+下划线」→ 看起来跟白字没区别。''',
    '''            # 品牌主题：注册并启用 onyx（调色板与消息语言同源，见 ai_lib/ui.py）
            try:
                self.register_theme(_onyx_theme())
                self.theme = "onyx"
            except Exception:
                pass
            # Markdown 主题：RichLog 是用 **Textual 自己的 console** 渲染的，
            # 必须推给它，否则标题只有「粗体+下划线」→ 看起来跟白字没区别。''',
    "on_mount 注册主题")

# ── 4) 新增 _banner / _turn_rule ────────────────────────────────
rep('''        def _measure(self):
            """在主线程量取日志区尺寸并缓存（worker 线程不得访问 widget）。"""''',
    '''        def _banner(self):
            """开场横幅：品牌行 + 一行引导（比一整段欢迎语更安静、更有辨识度）。"""
            try:
                from rich.text import Text as _T
                from bin.ai_lib.ui import ONYX_PALETTE as _P, ONYX_VARS as _V
                art = _T()
                art.append("◆ ", style="bold " + _P["accent"])
                art.append("ONYX", style="bold " + _P["foreground"])
                art.append("  " + _t("tui_banner_tag", self._lang),
                           style=_V["onyx-muted"])
                self._log(art)
                self._log(_T(_t("tui_welcome", self._lang), style=_V["onyx-muted"]))
                self._log("")
            except Exception:
                pass

        def _turn_rule(self):
            """轮次分隔线：一条发丝色横线，宽度跟随日志区。"""
            from rich.text import Text as _T
            from bin.ai_lib.ui import ONYX_VARS as _V
            w = max(8, int(self._log_w or 0) - 4)
            return _T("─" * w, style=_V["onyx-rule"])

        def _measure(self):
            """在主线程量取日志区尺寸并缓存（worker 线程不得访问 widget）。"""''',
    "新增 _banner / _turn_rule")

# ── 5) 任务行：几何字形 + 语义色 ────────────────────────────────
rep('''        @staticmethod
        def _todo_line(idx: int, t: dict):
            """一行任务（带序号）。当前进行项加粗高亮，已完成项置灰。"""
            from rich.text import Text as _T
            st = (t or {}).get("status", "pending")
            icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}.get(st, "⏳")
            content = (t or {}).get("content", "")
            if st == "in_progress":
                content = (t or {}).get("activeForm") or content
            style = {"in_progress": "bold", "completed": "dim", "pending": ""}.get(st, "")
            return _T(f"{idx}. {icon} {content}", style=style)''',
    '''        @staticmethod
        def _todo_line(idx: int, t: dict):
            """一行任务：序号置灰 + 状态字形（✓/▸/○）+ 语义色文本。"""
            from rich.text import Text as _T
            from bin.ai_lib.ui import ONYX_PALETTE as _P, ONYX_VARS as _V
            st = (t or {}).get("status", "pending")
            glyph, color = {
                "completed": ("✓", _P["success"]),
                "in_progress": ("▸", _P["accent"]),
            }.get(st, ("○", _V["onyx-dim"]))
            content = (t or {}).get("content", "")
            if st == "in_progress":
                content = (t or {}).get("activeForm") or content
            if st == "in_progress":
                text_style = "bold " + _P["foreground"]
            elif st == "completed":
                text_style = _V["onyx-muted"]
            else:
                text_style = _P["foreground"]
            out = _T()
            out.append(f"{idx:>2} ", style=_V["onyx-dim"])
            out.append(glyph + " ", style="bold " + color)
            out.append(str(content), style=text_style)
            return out''',
    "_todo_line")

# ── 6) 状态栏：去 emoji、加分隔与锚点 ───────────────────────────
OLD_STATUS_START = '''        def _render_status(self, status):
            """刷新状态栏：📂 路径 · 🧠 上下文 · 💰 缓存率 · 💳 余额（缺失项自动省略）。'''
_k = src.index(OLD_STATUS_START)
_end = src.index('''            except Exception:
                pass

        # ── 活动行（AI 思考转圈 / 子代理活动尾行）──''', _k)
NEW_STATUS = '''        def _render_status(self, status):
            """刷新状态栏：◆ 路径 │ 上下文 │ 缓存率 │ 余额（缺失项自动省略）。

            - 左侧一个 accent 菱形作视觉锚点，各段用发丝色 `│` 分隔；
            - 数字紧凑化（17,214 → 17.2k），窄屏按宽度从尾部依次丢弃。
            """
            try:
                s = status or {}
                wide = self.size.width >= 90
                segs = []   # [(文本, 颜色)]
                cwd = s.get("cwd") or ""
                if cwd:
                    try:
                        home = os.path.expanduser("~")
                        if home and cwd.startswith(home):
                            cwd = "~" + cwd[len(home):]
                    except Exception:
                        pass
                    if not wide:
                        cwd = os.path.basename(cwd.rstrip("/")) or cwd
                    segs.append((cwd, "cyan"))
                ctx = s.get("ctx") or 0
                if ctx:
                    segs.append(("ctx " + _fmt_compact(ctx), "magenta"))
                if s.get("cache_supported", True) and s.get("cache_pct") is not None:
                    segs.append(("cache " + f"{s['cache_pct']:.1f}%", "yellow"))
                bal = s.get("balance")
                if bal:
                    segs.append((str(bal), "green"))
                try:
                    from rich.cells import cell_len as _cl
                except Exception:
                    _cl = len
                avail = max(16, self.size.width - 4)
                while len(segs) > 1 and _cl("  │  ".join(t for t, _ in segs)) > avail:
                    segs.pop()
                bar = self.query_one("#status-bar", Static)
                if not segs:
                    bar.display = False
                else:
                    from rich.text import Text as _T
                    from bin.ai_lib.ui import ONYX_PALETTE as _P, ONYX_VARS as _V
                    out = _T()
                    out.append("◆ ", style="bold " + _P["accent"])
                    for i, (txt, color) in enumerate(segs):
                        if i:
                            out.append("  │  ", style=_V["onyx-rail"])
                        out.append(txt, style=color)
                    bar.update(out)
                    bar.display = True
'''
src = src[:_k] + NEW_STATUS + src[_end:]
print("✅ _render_status")

# ── 7) 用户轮次：分隔线 + ❯ 前缀 ────────────────────────────────
rep('''                try:
                    from rich.text import Text as _T
                    self.call_from_thread(self._log, _T(f"› {text}", style="bold bright_cyan"))
                except Exception:
                    self.call_from_thread(self._log, f"› {text}")
                from bin.ai_interactive import _call_ai_engine
                # 引擎模块是惰性导入的，此刻才真正加载 → 先让主线程重新量取日志区宽度
                # （on_mount 首帧 content_size 常为 0），再同步所有 Console；否则
                # ai_cmd/tool_executors 的 console 在本轮会是「非终端 + 全屏宽」→ 换行错乱。
                try:
                    self.call_from_thread(self._measure)
                except Exception:
                    pass
                self._sync_rich_width(full=True)''',
    '''                # 引擎模块是惰性导入的，此刻才真正加载 → 先让主线程重新量取日志区宽度
                # （on_mount 首帧 content_size 常为 0），再同步所有 Console；否则
                # ai_cmd/tool_executors 的 console 在本轮会是「非终端 + 全屏宽」→ 换行错乱。
                try:
                    self.call_from_thread(self._measure)
                except Exception:
                    pass
                self._sync_rich_width(full=True)
                try:
                    from rich.text import Text as _T
                    from bin.ai_lib.ui import ONYX_PALETTE as _P, ONYX_VARS as _V
                    self.call_from_thread(self._log, self._turn_rule())
                    line = _T()
                    line.append("❯ ", style="bold " + _V["onyx-user"])
                    line.append(text, style="bold " + _P["foreground"])
                    self.call_from_thread(self._log, line)
                    self.call_from_thread(self._log, "")
                except Exception:
                    self.call_from_thread(self._log, f"❯ {text}")
                from bin.ai_interactive import _call_ai_engine''',
    "用户轮次渲染")

io.open(P, "w", encoding="utf-8").write(src)
print("written", len(src), "chars")
