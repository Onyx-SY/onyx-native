# -*- coding: utf-8 -*-
"""G1：修「没确认就当成已确认」的失败开放点。

A. bin/ai_lib/helpers.py:521-525 —— confirm_dangerous_command **无条件 return True**
   （"所有危险命令一律自动放行"）→ 后面三级策略 + 弹窗全是死代码，危险命令从不询问。
B. 同函数 542-543：软确认 10s 无操作 timeout_default=True → 自动放行。
C. bin/ai_cmd.py:2584：plan 模式未确认时 `interaction_count > 2` 整体放行前 2 轮 →
   未确认计划就能跑 shell、写文件。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = []


def patch(rel, old, new):
    P.append((os.path.join(ROOT, rel), old, new))


# ── A：删掉无条件放行（保留显式逃生舱）──
patch(
    "bin/ai_lib/helpers.py",
    '''    # 2026-09：所有危险命令一律自动放行（开发者工具，影响可控）。
    # 不再弹 y/N 确认；硬安全仍由沙盒边界（路径越界拦截）提供。
    if log_info:
        log_info(f"AI dangerous command auto-allowed: {cmd_str}", session_id)
    return True, "auto", ""

    # ── 第二级：300k ≤ 上下文 ≤ 600k → 弹窗询问，10s 无操作默认放行 ──''',
    '''    # ── 逃生舱（显式、可审计）──
    # 旧实现在这里无条件 `return True, "auto"`，把下面整段三级策略变成了**死代码** ——
    # 后果：危险命令从不询问、直接执行（用户实测「没确认就当成已确认」）。
    # 现在把「全部自动放行」变成显式开关，默认关闭；需要时一个环境变量即可回到老行为。
    if os.environ.get("ONYX_DANGER_AUTO_ALLOW", "").strip().lower() in ("1", "true", "yes", "on"):
        if log_info:
            log_info(f"AI dangerous command auto-allowed (ONYX_DANGER_AUTO_ALLOW=1): {cmd_str}", session_id)
        return True, "auto", ""

    # ── 第二级：300k ≤ 上下文 ≤ 600k → 弹窗询问，超时**默认拒绝**（fail-closed）──''',
)

# ── B：超时改默认拒绝 + 提示语 ──
patch(
    "bin/ai_lib/helpers.py",
    '''        console.print(
            (f"[yellow]⚠️ 上下文 {context_tokens // 1000}k：危险命令需确认，10 秒无操作默认执行[/]"
             if current_lang == "chinese"
             else f"[yellow]⚠️ Context {context_tokens // 1000}k: confirm required, auto-executes after 10s[/]")
        )''',
    '''        console.print(
            (f"[yellow]⚠️ 上下文 {context_tokens // 1000}k：危险命令需确认，"
             f"{int(_CONFIRM_TIMEOUT_SECONDS)} 秒无操作默认**拒绝**[/]"
             if current_lang == "chinese"
             else f"[yellow]⚠️ Context {context_tokens // 1000}k: confirmation required, "
                  f"auto-**denied** after {int(_CONFIRM_TIMEOUT_SECONDS)}s[/]")
        )''',
)

patch(
    "bin/ai_lib/helpers.py",
    '''                timeout=_CONFIRM_TIMEOUT_SECONDS,
                timeout_default=True,   # 10s 无操作默认放行（用户可能离开）
            )
        if user_resp == "timeout":
            refuse_reason = (
                "确认超时（默认执行）" if current_lang == "chinese"
                else "Confirmation timeout (auto-executed)"
            )''',
    '''                timeout=_CONFIRM_TIMEOUT_SECONDS,
                # ⚠️ fail-closed：超时 = 拒绝。
                # 旧值 True 意味着「人不在 → 危险命令照样执行」，正是「没确认就当成已确认」。
                timeout_default=False,
            )
        if user_resp == "timeout":
            refuse_reason = (
                "确认超时（已默认拒绝，未执行）" if current_lang == "chinese"
                else "Confirmation timeout (denied, not executed)"
            )''',
)

# ── C：plan 模式探索期硬闸门 ──
patch(
    "bin/ai_cmd.py",
    '''        # Plan 模式安全限制：未确认计划前，拦截非计划类命令和工具调用
        # 既支持 mode=="plan"（用户输入 ai plan），也支持 _PLAN_MODE_ACTIVE（AI 调用 EnterPlanMode）
        # 前 2 轮交互不拦截，让 AI 有机会探索代码库并生成计划
        # ⚠️ 注意：submit_plan / mark_step_complete / ExitPlanMode / choose_ask
        # 是 AI 在 plan 模式下唯一能用的工具，不能拦截它们
        _plan_tools = {"submit_plan", "mark_step_complete", "ExitPlanMode", "choose_ask"}
        if (mode == "plan" or _PLAN_MODE_ACTIVE) and not plan_confirmed and interaction_count > 2:''',
    '''        # Plan 模式安全限制：未确认计划前，拦截非计划类命令和工具调用
        # 既支持 mode=="plan"（用户输入 ai plan），也支持 _PLAN_MODE_ACTIVE（AI 调用 EnterPlanMode）
        # ⚠️ 注意：submit_plan / mark_step_complete / ExitPlanMode / choose_ask
        # 是 AI 在 plan 模式下唯一能用的工具，不能拦截它们
        _plan_tools = {"submit_plan", "mark_step_complete", "ExitPlanMode", "choose_ask"}

        # ── 探索期硬闸门（2026-09 修复）──
        # 旧逻辑用 `interaction_count > 2` 把**前 2 轮整体放行**，本意是让 AI 探索代码库，
        # 但「整体放行」等于未确认计划时也能跑 shell、写文件 —— 用户实测「没确认就开干」。
        # 现在从第 1 轮起就拦住**执行类**动作；只读探索（读文件/搜索/查环境）不受影响。
        _plan_mutating_tools = {"write_file", "edit_file", "RunCommand", "apply_patch",
                                "CronCreate", "CronDelete", "TaskRemove", "TaskStop"}
        if (mode == "plan" or _PLAN_MODE_ACTIVE) and not plan_confirmed:
            _mut_calls = [tc for tc in tool_calls if tc.get("name", "") in _plan_mutating_tools]
            if ai_commands or _mut_calls:
                _blocked_names = sorted({tc.get("name", "") for tc in _mut_calls}
                                        | ({"shell"} if ai_commands else set()))
                console.print(lang_text.get(
                    "plan_blocked_early",
                    "⛔ Plan 模式（计划未确认）：已拦截执行类动作 —— "
                    + ", ".join(_blocked_names)
                    + "。只读探索不受影响；请先提交并确认计划。"), style="bold red")
                ai_commands = []
                tool_calls = [tc for tc in tool_calls
                              if tc.get("name", "") not in _plan_mutating_tools]

        if (mode == "plan" or _PLAN_MODE_ACTIVE) and not plan_confirmed and interaction_count > 2:''',
)


def main():
    cache = {}
    for path, old, new in P:
        if path not in cache:
            with open(path, encoding="utf-8") as f:
                cache[path] = f.read()
        text = cache[path]
        n = text.count(old)
        if n != 1:
            print(f"FAIL {os.path.relpath(path, ROOT)}: 命中 {n} 次\n{old[:200]}")
            return 1
        cache[path] = text.replace(old, new, 1)
    for path, text in cache.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"OK   {os.path.relpath(path, ROOT)}")
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
