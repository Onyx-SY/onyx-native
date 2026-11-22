# -*- coding: utf-8 -*-
"""补丁 7：主动投递 —— AI 空闲时把感官事件交还主线程 AI 执行。"""
import io
import sys


def patch(path, old, new, cnt=1):
    s = io.open(path, encoding="utf-8").read()
    c = s.count(old)
    if c != cnt:
        print("FAIL %s count=%d (expect %d) %r" % (path, c, cnt, old[:70]))
        sys.exit(1)
    io.open(path, "w", encoding="utf-8").write(s.replace(old, new, cnt))
    print("OK %s" % path)


# ══════════ 1. REPL：提示符边界投递（不强插输入） ══════════
patch("bin/ai_interactive.py",
      '''        while True:
            try:
                user_input = session.prompt()
            except KeyboardInterrupt:''',
      '''        while True:
            # ── 感官：AI 空闲时的事件驱动回合（用户说话 + 事件 联合驱动）──
            _wake_text = _senses_take_wake()
            if _wake_text:
                try:
                    _call_ai_engine(_wake_text, user_home_dir, onyx_module, global_config,
                                    user_info, user_mode, parse_and_execute, ctx, **kwargs)
                except KeyboardInterrupt:
                    console.print("\\n  [bold yellow]⏹ 已中断[/]" if current_lang == "chinese"
                                  else "\\n  [bold yellow]⏹ Interrupted[/]")
                except Exception as e:
                    console.print(f"[red]{_t('ai_error', current_lang, reason=str(e))}[/]")
                continue
            try:
                user_input = session.prompt()
            except KeyboardInterrupt:''')

# 辅助函数：放在 _call_ai_engine 之前
patch("bin/ai_interactive.py",
      '''def _call_ai_engine(
    question: str,''',
      '''def _senses_take_wake() -> str:
    """取一条待投递的感官事件（无事件/不可提醒 → 空串）。

    只在「AI 空闲 + 主循环回到提示符」时调用：事件驱动回合交还**主线程 AI**
    执行，不另开子线程/子代理。
    """
    try:
        from bin.ai_lib import senses as _s
        if _s.is_busy() or not _s.can_notify():
            return ""
        evs = _s.pop_events()
        if not evs:
            return ""
        _s.mark_notified()
        _lang = "chinese"
        try:
            _lang = ctx_lang()
        except Exception:
            pass
        console.print("[dim]" + ("👁 感官：检测到变化，主动唤醒 AI…"
                                 if _lang == "chinese" else
                                 "👁 senses: change detected, waking AI…") + "[/]")
        return _s.build_wake_prompt(evs)
    except Exception:
        return ""


def ctx_lang() -> str:
    try:
        from bin.ai_lib.config import get_current_lang
        return get_current_lang()
    except Exception:
        return "chinese"


def _call_ai_engine(
    question: str,''')

# ══════════ 2. TUI：worker 空闲时投递 ══════════
patch("bin/ai_tui.py",
      '''                try:
                    text = self._in_q.get(timeout=0.2)
                except queue.Empty:
                    continue''',
      '''                try:
                    text = self._in_q.get(timeout=0.2)
                except queue.Empty:
                    self._maybe_senses_wake()
                    continue''')

patch("bin/ai_tui.py",
      '''        def _run_one(self, text):
            ctx = self._ctx''',
      '''        def _maybe_senses_wake(self):
            """感官：空闲时把事件交还主线程 AI 引擎（用户说话 + 事件 联合驱动）。"""
            if self._busy or self._stop.is_set():
                return
            try:
                from bin.ai_lib import senses as _s
                if _s.is_busy() or not _s.can_notify():
                    return
                evs = _s.pop_events()
                if not evs:
                    return
                _s.mark_notified()
                text = _s.build_wake_prompt(evs)
            except Exception:
                return
            self._busy = True
            try:
                self._run_one(text)
            except BaseException:
                pass
            finally:
                self._busy = False

        def _run_one(self, text):
            ctx = self._ctx''')
