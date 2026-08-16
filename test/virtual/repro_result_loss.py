#!/usr/bin/env python3
"""离线复现：子代理结果丢失的两个缺口（不启动真实 API）。

缺口 A：end-of-turn 判定用 has_pending()（只看 status），
        已完成但尚未注入的 done 任务不在其中 → 回合结束直接退出，
        REPL 模式更是完全跳过等待/注入 → 总结滞留队列，下条消息/新会话才注入。
缺口 B：_execute 不识别 _interrupted（ESC 中断流式响应）→ 空 txt 被当作
        「成功完成」→ status=done + summary="" → 收集器注入的是「失败」而非总结。

运行: python3 test/virtual/repro_result_loss.py
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import bin.ai_lib.api as api_mod
import bin.ai_cmd as ai_cmd_mod
import bin.ai_lib.subagent as sa


def _repro_gap_a():
    """缺口 A：done 且已在队列的任务，has_pending() 为 False。

    模拟 handle_ai 回合结束判定：
      _mgr.has_pending() and not was_interrupted  → 才等待+注入
    """
    m = sa.ExploreManager()
    t = sa.ExploreTask("p")
    t.status = "done"
    t.summary = "这是子代理总结"
    with m._lock:
        m._tasks[t.id] = t
        m._done_queue.put(t.id)

    # 回合结束时的判定（bin/ai_cmd.py:8420 附近的实际条件）
    if not (m.has_pending()):
        print(f"[A] 复现成功：任务已 done 且在队列，但 has_pending()={m.has_pending()}")
        print(f"    → 回合结束判定为「无待办」→ continue_asking=False → 本回合不注入")
        print(f"    → 总结「{t.summary[:12]}…」滞留队列，等下一次会话/下一条消息")
        return True
    print("[A] 未复现")
    return False


def _repro_gap_b():
    """缺口 B：_execute 收到 _interrupted 响应 → done + 空总结。"""
    import bin.ai_lib.subagent as sa_mod

    _orig_api = api_mod.call_ai_api_sse
    _orig_exec = ai_cmd_mod.execute_mcp_tool

    def _fake_api(question="", messages=None, tools=None, **kw):
        # 模拟用户 ESC 中断子代理的流式调用：api.py 立即返回空 txt + _interrupted
        return {"txt": "", "analysis": "", "answer": "yes", "ask": "", "_interrupted": True}

    api_mod.call_ai_api_sse = _fake_api
    ai_cmd_mod.execute_mcp_tool = lambda name, params, *a, **kw: (True, "x")
    try:
        t = sa_mod.ExploreTask("任务", agent_type="explore")
        m = sa_mod.ExploreManager()
        m._run_task(t)  # 完整路径：_execute + finally（置 done + 入队）
        queued = [x.id for x in m.collect_done()]
        if t.status == "done" and t.summary == "":
            print(f"[B] 复现成功：_interrupted 被当成正常完成 → status={t.status}, summary={t.summary!r}, 已入队={queued}")
            print("    → 收集器注入「子代理任务失败：…失败：done」→ 主 AI 拿不到任何内容")
            return True
        print(f"[B] 未复现：status={t.status}, summary={t.summary!r}")
        return False
    finally:
        api_mod.call_ai_api_sse = _orig_api
        ai_cmd_mod.execute_mcp_tool = _orig_exec


def _repro_gap_c():
    """缺口 C：sync 超时（>300s）仍在运行的子代理 →「仍在运行」提示，
    但 REPL 模式下回合结束不等待 → 与缺口 A 相同，结果要等下一条消息。"""
    # 逻辑与 A 相同（都是 has_pending 判定），此处仅打印说明
    print("[C] 说明：sync 超时任务与 A 同路径；REPL 分支（_in_repl=True）直接 continue_asking=False，无等待无注入")
    return True


if __name__ == "__main__":
    _a = _repro_gap_a()
    _b = _repro_gap_b()
    _c = _repro_gap_c()
    print("\n结论:", "存在缺口" if (_a or _b) else "无缺口")
