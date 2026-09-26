# -*- coding: utf-8 -*-
"""修复：计划未经确认就执行。

四处「默认放行」：
  ① SelectScreen 按 Esc → dismiss(default)，而 default 正是「确认计划」；
  ② confirm_plan 对任何未知/空选择都 `return "confirm"`；
  ③ _modal 120s 超时 → 适配器返回 "" → 又落到 ②；
  ④ select_option 在 Ctrl+C/EOF 时返回 default（= 确认项）。
"""
import io
import sys


def patch(path, pairs, tag):
    s = io.open(path, encoding="utf-8").read()
    for old, new, name in pairs:
        n = s.count(old)
        if n != 1:
            print(f"❌ {tag}/{name}: 命中 {n} 次（应为 1）")
            sys.exit(1)
        s = s.replace(old, new)
        print(f"✅ {tag}/{name}")
    io.open(path, "w", encoding="utf-8").write(s)


# ══════════ ① ui.py：blocking 参数 + 中断不再返回 default ══════════
patch("bin/ai_lib/ui.py", [
    ('''def select_option(
    message: str,
    options: List[str],
    default: str = "",
    lang: str = "chinese",
) -> str:''',
     '''def select_option(
    message: str,
    options: List[str],
    default: str = "",
    lang: str = "chinese",
    blocking: bool = False,
) -> str:''', "签名"),

    ('''    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "select_option"):
        return _adapter.select_option(message, options, default, lang)''',
     '''    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "select_option"):
        try:
            return _adapter.select_option(message, options, default, lang, blocking=blocking)
        except TypeError:
            # 旧适配器不认 blocking → 退回旧签名（仅兜底，TUI 适配器已支持）
            return _adapter.select_option(message, options, default, lang)''', "适配器调用"),

    ('''        except (KeyboardInterrupt, EOFError):
            console.print()
            return default
        except Exception:
            pass  # 回退到 prompt_toolkit''',
     '''        except (KeyboardInterrupt, EOFError):
            console.print()
            # 中断 = 取消。绝不返回 default：确认类调用（如计划确认）的 default 是
            # 「确认」，Ctrl+C 被当成确认会让 AI 未经许可直接执行。
            return ""
        except Exception:
            pass  # 回退到 prompt_toolkit''', "InquirerPy 中断"),

    ('''    except (KeyboardInterrupt, EOFError):
        console.print()
        return default

    return choice or default''',
     '''    except (KeyboardInterrupt, EOFError):
        console.print()
        return ""

    return choice if choice in options else ""''', "回退选择器中断"),
], "ui.py")

# ══════════ ② helpers.py：confirm_plan 绝不默认确认 ══════════
patch("bin/ai_lib/helpers.py", [
    ('''def confirm_plan(plan_text: str, lang_text: Dict[str, str]) -> str:
    """上下键选择 Plan 确认流程：Rich Panel 展示计划 + 箭头键选择。
    返回: "confirm" / "guide" / "discard"
    """
    console.print(render_plan_panel(plan_text))
    console.print()
    try:
        choice = select_option(
            message=lang_text.get("plan_prompt", "请选择操作: / Please choose:"),
            options=[
                lang_text.get("plan_opt_confirm", "✅ 确认计划，开始执行 | ✅ Confirm plan and start"),
                lang_text.get("plan_opt_guide", "💡 提出修改意见 | 💡 Suggest changes"),
                lang_text.get("plan_opt_discard", "🗑️ 摒弃计划，重新制定 | 🗑️ Discard and redo"),
            ],
            default=lang_text.get("plan_opt_confirm", "✅ 确认计划，开始执行 | ✅ Confirm plan and start"),
            lang=get_current_lang(),
        )
    except (KeyboardInterrupt, EOFError):
        console.print()
        return "confirm"
    if choice in (lang_text.get("plan_opt_discard", "🗑️ 摒弃计划，重新制定 | 🗑️ Discard and redo"),):
        return "discard"
    elif choice in (lang_text.get("plan_opt_guide", "💡 提出修改意见 | 💡 Suggest changes"),):
        return "guide"
    return "confirm"''',
     '''def confirm_plan(plan_text: str, lang_text: Dict[str, str]) -> str:
    """上下键选择 Plan 确认流程：Rich Panel 展示计划 + 箭头键选择。

    返回: "confirm" / "guide" / "discard" / "cancel"

    ⚠️ 确认必须来自用户的**明确选择**，绝不默认放行：
      - Esc 取消 / 未知选项 → 重新询问（不会变成确认）；
      - 交互不可用（App 已退出、模态推送失败）→ 返回 "cancel"，由调用方安全收尾；
      - Ctrl+C / EOF → 返回 "cancel"。
    旧实现把这些情况统统 `return "confirm"`，导致「计划还没确认，AI 就已经开始执行」。
    """
    console.print(render_plan_panel(plan_text))
    console.print()
    _opt_confirm = lang_text.get("plan_opt_confirm", "✅ 确认计划，开始执行 | ✅ Confirm plan and start")
    _opt_guide = lang_text.get("plan_opt_guide", "💡 提出修改意见 | 💡 Suggest changes")
    _opt_discard = lang_text.get("plan_opt_discard", "🗑️ 摒弃计划，重新制定 | 🗑️ Discard and redo")
    while True:
        try:
            choice = select_option(
                message=lang_text.get("plan_prompt", "请选择操作: / Please choose:"),
                options=[_opt_confirm, _opt_guide, _opt_discard],
                default=_opt_confirm,
                lang=get_current_lang(),
                blocking=True,   # 人工决策，不设超时：必须等到明确选择
            )
        except (KeyboardInterrupt, EOFError):
            console.print()
            return "cancel"
        if choice == _opt_confirm:
            return "confirm"
        if choice == _opt_guide:
            return "guide"
        if choice == _opt_discard:
            return "discard"
        if choice in ("", None):
            # 交互不可用（App 正在退出 / 模态推送失败）→ 明确不执行
            return "cancel"
        # Esc 取消或返回了未知选项 → 重新询问，绝不默认确认
        console.print(lang_text.get("plan_need_choice",
                      "请选择一个操作（Esc 不会确认计划） | Please choose an action (Esc does not confirm)"),
                      style="bold yellow")
        console.print()''', "confirm_plan"),
], "helpers.py")

# ══════════ ③ ai_tui.py：Esc 哨兵 + 无限等待 + blocking 透传 ══════════
patch("bin/ai_tui.py", [
    ('''_MODAL_TIMEOUT = 120.0''',
     '''_MODAL_TIMEOUT = 120.0
# Esc 取消的哨兵值：让「明确取消」与「交互不可用（空串）」可区分
_CANCEL = "__cancel__"''', "哨兵常量"),

    ('''        def action_cancel(self):
            self.dismiss(self.default)

    class TextScreen(ModalScreen):''',
     '''        def action_cancel(self):
            # Esc = 用户明确取消 → 送回哨兵。绝不能 dismiss(self.default)：
            # 计划确认菜单的 default 就是「确认计划」，按 Esc 会被当成确认执行。
            self.dismiss(_CANCEL)

    class TextScreen(ModalScreen):''', "SelectScreen Esc"),

    ('''            t = timeout if (timeout is not None and timeout > 0) else _MODAL_TIMEOUT''',
     '''            if timeout is None:
                t = _MODAL_TIMEOUT
            elif timeout < 0:
                t = None      # 无限等待（_wait 仍会检查 App 是否退出，不会死等）
            else:
                t = timeout or _MODAL_TIMEOUT''', "_modal 超时"),

    ('''        def select_option(self, message, options, default="", lang="chinese"):
            r = self._modal(SelectScreen(message, options, default))
            if r is None:
                # 模态未完成（App 正在退出 / 推送失败）→ 返回空串表示「取消」。
                # 不能回退成 default：调用方（/config 菜单）会把返回值当成用户选择，
                # 从而静默执行「切换平台」这类有副作用的动作。
                return ""
            return r''',
     '''        def select_option(self, message, options, default="", lang="chinese", blocking=False):
            # blocking=True（计划确认）：不设超时，且把「Esc 取消」原样透出，
            # 让 confirm_plan 能区分「用户取消（重新询问）」与「交互不可用（安全收尾）」。
            r = self._modal(SelectScreen(message, options, default),
                            timeout=-1 if blocking else None,
                            on_timeout=_CANCEL)
            if r is None:
                # 模态未完成（App 正在退出 / 推送失败）→ 返回空串表示「取消」。
                # 不能回退成 default：调用方（/config 菜单）会把返回值当成用户选择，
                # 从而静默执行「切换平台」这类有副作用的动作。
                return ""
            if r == _CANCEL:
                return _CANCEL if blocking else ""
            return r''', "适配器 select_option"),
], "ai_tui.py")

# ══════════ ④ ai_cmd.py：处理 cancel（绝不落到执行分支） ══════════
patch("bin/ai_cmd.py", [
    ('''                _pending_plan = ""
                plan_confirmed = True
                continue_asking = True
                continue
''',
     '''                _pending_plan = ""
                plan_confirmed = True
                continue_asking = True
                continue

            elif plan_choice == "cancel":
                # 用户没做出确认（Esc / Ctrl+C / 交互不可用）→ 绝不执行，
                # 结束本轮并保留 _pending_plan，等用户下次明确确认。
                console.print(lang_text.get("plan_not_confirmed",
                    "⏸️ 计划尚未确认，已暂停执行。请确认后再继续。"), style="bold yellow")
                continue_asking = False
                continue
''', "cancel 分支"),
], "ai_cmd.py")

print("\n全部替换完成")
