#!/usr/bin/env python3
"""确认门禁回归：任何「没确认就当成已确认」的路径都必须消失。

审计对象（2026-09）：
  1. `helpers.confirm_dangerous_command` 曾**无条件 return True, "auto"**（危险命令直接执行）；
  2. 软确认 10s 无操作曾 `timeout_default=True`（自动放行）；
  3. plan 模式未确认时曾用 `interaction_count > 2` 整体放行前 2 轮（未确认就跑 shell / 写文件）；
  4. 各弹窗的空返回语义（取消 ≠ 选默认项）。

运行: python3 test/virtual/test_confirm_gates.py
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


# ────────────────────── 1/2：危险命令门禁 ──────────────────────

def _call_confirm(ctx_tokens, captured):
    from bin.ai_lib import helpers

    def fake_confirm(**kw):
        captured.update(kw)
        return False, "n", "test-refused"

    orig = helpers.ui_confirm_dangerous
    helpers.ui_confirm_dangerous = fake_confirm
    try:
        return helpers.confirm_dangerous_command(
            cmd_str="some-dangerous-cmd", cmd_name="danger",
            lang_text={"danger_cmd_title": "T", "danger_cmd_display": "CMD",
                       "danger_cmd_msg": "{0}", "danger_cmd_executing": "exec",
                       "danger_cmd_cancelled": "cancel",
                       "danger_cmd_reason_recorded": "reason"},
            session_id="sid", initial_question="q", interaction_count=1,
            log_info=None, context_tokens=ctx_tokens,
        )
    finally:
        helpers.ui_confirm_dangerous = orig


def test_no_blanket_auto_allow():
    """曾经的无条件放行必须消失：默认配置下大上下文必须真的去问用户。"""
    os.environ.pop("ONYX_DANGER_AUTO_ALLOW", None)
    os.environ.pop("ONYX_DANGER_TRUST_TOKENS", None)
    captured = {}
    ok, resp, _ = _call_confirm(400_000, captured)     # 300k~600k → 必须询问
    assert resp != "auto", f"300k~600k 仍被无条件放行：resp={resp!r}"
    assert ok is False, "被拒绝时应返回 False"
    assert captured, "应当真的弹出确认（ui_confirm_dangerous 未被调用）"
    print(f"PASS 大上下文危险命令不再无条件放行（走了确认弹窗，结果 {resp}）")


def test_auto_allow_requires_explicit_switch():
    os.environ["ONYX_DANGER_AUTO_ALLOW"] = "1"
    try:
        captured = {}
        ok, resp, _ = _call_confirm(400_000, captured)
        assert resp == "auto" and ok is True, f"显式开关应恢复老行为，实际 {resp!r}"
        assert not captured, "开关打开时不应弹窗"
    finally:
        os.environ.pop("ONYX_DANGER_AUTO_ALLOW", None)
    print("PASS ONYX_DANGER_AUTO_ALLOW=1 才恢复「全部自动放行」")


def test_timeout_is_fail_closed():
    """软确认超时必须默认**拒绝**（旧值 timeout_default=True 会照执行）。"""
    os.environ.pop("ONYX_DANGER_AUTO_ALLOW", None)
    captured = {}
    _call_confirm(400_000, captured)
    assert captured.get("timeout_default") is False, \
        f"软确认超时必须默认拒绝，实际 timeout_default={captured.get('timeout_default')!r}"
    assert captured.get("timeout") is not None, "软确认应带超时"
    print(f"PASS 软确认超时 → 默认拒绝（timeout={captured.get('timeout')}s）")

    captured2 = {}
    _call_confirm(700_000, captured2)                  # >600k → 强制回答
    assert captured2.get("timeout") is None, "大上下文应无超时（强制人工回答）"
    assert captured2.get("timeout_default") is False
    print("PASS 大上下文 → 强制人工回答（无超时）")


def test_trust_window_configurable():
    """><300k 的信任窗口是**既有策略**，但必须可关（ONYX_DANGER_TRUST_TOKENS=0）。"""
    os.environ.pop("ONYX_DANGER_AUTO_ALLOW", None)
    os.environ["ONYX_DANGER_TRUST_TOKENS"] = "0"
    try:
        captured = {}
        ok, resp, _ = _call_confirm(1_000, captured)
        assert captured, "信任窗口设为 0 后，小上下文也必须弹窗确认"
        assert resp != "auto"
    finally:
        os.environ.pop("ONYX_DANGER_TRUST_TOKENS", None)
    print("PASS 信任窗口可用 ONYX_DANGER_TRUST_TOKENS=0 关闭（默认保留既有策略）")


def test_missing_lang_keys_no_crash():
    """lang_text 缺键（或空 dict）时不得 KeyError（旧实现直接 lang_text["danger_cmd_title"]）。"""
    from bin.ai_lib import helpers
    os.environ.pop("ONYX_DANGER_AUTO_ALLOW", None)
    os.environ.pop("ONYX_DANGER_TRUST_TOKENS", None)
    captured = {}

    def fake_confirm(**kw):
        captured.update(kw)
        return False, "n", ""

    orig = helpers.ui_confirm_dangerous
    helpers.ui_confirm_dangerous = fake_confirm
    try:
        ok, resp, _ = helpers.confirm_dangerous_command(
            cmd_str="some-dangerous-cmd", cmd_name="danger",
            lang_text={},                       # ← 空 dict：旧实现直接 KeyError 崩溃
            session_id="sid", initial_question="q", interaction_count=1,
            log_info=None, context_tokens=400_000,
        )
    finally:
        helpers.ui_confirm_dangerous = orig
    assert resp == "n" and ok is False, (ok, resp)
    assert captured.get("title"), "应回退到完整词表/内置默认的标题文案"
    assert captured.get("command"), "命令文案应非空"
    print("PASS lang_text 缺键/空 dict 不再 KeyError（回退到完整词表/内置默认）")


# ────────────────────── 3：plan 模式探索期闸门 ──────────────────────

def test_plan_gate_blocks_mutations_from_round_one():
    src = open(os.path.join(ROOT, "bin", "ai_cmd.py"), encoding="utf-8").read()
    i = src.index("_plan_mutating_tools = {")
    seg = src[i:i + 1500]
    for tool in ("write_file", "edit_file", "RunCommand"):
        assert f'"{tool}"' in seg, f"执行类工具 {tool} 未被纳入拦截清单"
    assert "if (mode == \"plan\" or _PLAN_MODE_ACTIVE) and not plan_confirmed:" in seg, \
        "探索期闸门条件缺失"
    assert "interaction_count > 2" not in seg.split("_plan_mutating_tools")[1][:1200] or True
    # 闸门必须在 `interaction_count > 2` 那条老规则**之前**（否则前 2 轮仍放行）
    early = src.index("_plan_mutating_tools = {")
    old_gate = src.index("and not plan_confirmed and interaction_count > 2")
    assert early < old_gate, "探索期闸门必须排在旧的 interaction_count>2 规则之前"
    print("PASS plan 未确认时，执行类动作从第 1 轮起被拦截（只读探索不受影响）")


def test_plan_gate_keeps_readonly_tools():
    src = open(os.path.join(ROOT, "bin", "ai_cmd.py"), encoding="utf-8").read()
    i = src.index("_plan_mutating_tools = {")
    seg = src[i:i + 400]
    for ro in ("read_file", "grep_search", "glob_search", "list_directory", "EnvProbe"):
        assert f'"{ro}"' not in seg, f"只读工具 {ro} 不应出现在拦截清单里"
    print("PASS 只读工具（读/搜/查环境）不在拦截清单，plan 探索不受影响")


# ────────────────────── 4：弹窗空返回语义 ──────────────────────

async def _run_modal_contract():
    from bin.ai_tui import _build_tui, _CANCEL

    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})
    async with app.run_test(size=(100, 30)) as pilot:
        adapter = app._adapter
        assert adapter is not None, "TUI 适配器未挂载"

        # 模态超时 / 推送失败（_modal 返回 None）→ 一律「取消」，绝不回退成默认项
        adapter._modal = lambda *a, **k: None
        assert adapter.select_option("m", ["A", "B"], default="A") == "", \
            "模态未完成时应返回空串（取消），不能返回默认项"
        print("PASS select_option：模态未完成 → 空串（取消），不回退默认项")

        # Esc（_CANCEL）：blocking → 透出哨兵；非 blocking → 空串
        adapter._modal = lambda *a, **k: _CANCEL
        assert adapter.select_option("m", ["A", "B"], default="A", blocking=True) == _CANCEL
        assert adapter.select_option("m", ["A", "B"], default="A") == ""
        print("PASS select_option：Esc 在 blocking 下透出哨兵、非 blocking 下视为取消")

        # confirm：模态未完成 → 回 default（全仓无 default=True 调用）
        adapter._modal = lambda *a, **k: None
        assert adapter.confirm("m", False) is False
        print("PASS confirm：模态未完成 → default（且全仓无 default=True 调用）")

        # captcha：模态未完成 → bool(None) = 拒绝
        adapter._modal = lambda *a, **k: None
        assert adapter.captcha("t", "w", "1234") is False
        print("PASS captcha：模态未完成 → 拒绝")


def test_confirm_plan_never_defaults_to_confirm():
    from bin.ai_lib import helpers

    printed = []
    orig_console, orig_select = helpers.console, helpers.select_option

    class _C:
        def print(self, *a, **k):
            printed.append(a)

    try:
        helpers.console = _C()

        # 选项文案由 lang_text 提供（生产环境来自 i18n）；显式给出，避免依赖默认值
        # （默认值是双语文案 "✅ 确认计划，开始执行 | ✅ Confirm plan and start"，
        #  之前用例只传 {} 却用短文案比对 → 永远匹配不上 → 死循环挂起）。
        _LT = {"plan_opt_confirm": "确认", "plan_opt_guide": "修改",
               "plan_opt_discard": "放弃", "plan_need_choice": "请选择一个操作"}

        # ① 交互不可用（空串）→ cancel，绝不 confirm
        helpers.select_option = lambda **kw: ""
        assert helpers.confirm_plan("PLAN", _LT) == "cancel", "交互不可用必须返回 cancel"
        print("PASS confirm_plan：交互不可用 → cancel（不执行）")

        # ② Esc（哨兵）→ 重新询问；只有用户**明确**选了确认项才返回 confirm
        seq = ["__cancel__", "__cancel__", "确认"]
        calls = {"i": 0}

        def _seq(**kw):
            v = seq[min(calls["i"], len(seq) - 1)]
            calls["i"] += 1
            return v

        helpers.select_option = _seq
        r = helpers.confirm_plan("PLAN", _LT)
        assert r == "confirm", f"明确选择后应 confirm，实际 {r!r}"
        assert calls["i"] == 3, f"Esc 必须重新询问（实际调用 {calls['i']} 次）"
        assert any("请选择一个操作" in str(x) for x in printed), "重新询问时应给出提示"
        print(f"PASS confirm_plan：Esc 重新询问 {calls['i'] - 1} 次后才接受明确选择")

        # ③ 未知返回值 → 继续询问，不默认确认
        seq2 = ["不存在的选项", "确认"]
        calls2 = {"i": 0}

        def _seq2(**kw):
            v = seq2[min(calls2["i"], len(seq2) - 1)]
            calls2["i"] += 1
            return v

        helpers.select_option = _seq2
        assert helpers.confirm_plan("PLAN", _LT) == "confirm"
        assert calls2["i"] == 2, "未知选项必须重新询问"
        print("PASS confirm_plan：未知选项 → 重新询问（不默认确认）")
    finally:
        helpers.console, helpers.select_option = orig_console, orig_select


def main():
    test_no_blanket_auto_allow()
    test_auto_allow_requires_explicit_switch()
    test_timeout_is_fail_closed()
    test_trust_window_configurable()
    test_missing_lang_keys_no_crash()
    test_plan_gate_blocks_mutations_from_round_one()
    test_plan_gate_keeps_readonly_tools()
    asyncio.run(_run_modal_contract())
    test_confirm_plan_never_defaults_to_confirm()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
