#!/usr/bin/env python3
"""离线验证子代理 tasks 数组支持对象元素：每个子代理独立 prompt/type/model/name。

运行: python3 test/virtual/test_subagent_tasks.py
（patch ExploreManager.submit，不启动真实子代理线程/API 调用）
"""
import os
import sys
import threading
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import bin.ai_lib.subagent as sa  # noqa: E402

_calls = []
_orig_manager = sa._manager


def _fake_submit(prompt, name="", mode="sync", model=None, agent_type="explore", block=True):
    _calls.append((prompt, name, mode, model, agent_type))
    t = types.SimpleNamespace(done=threading.Event())
    t.done.set()
    return t


def _setup():
    global _calls
    _calls = []
    mgr = sa.ExploreManager()
    mgr.submit = _fake_submit
    sa._manager = mgr


def test_mixed_str_and_dict():
    _setup()
    sa.run_agent(
        agent_type="explore",
        tasks=[
            "查一下 simple_forum 的路由",
            {"prompt": "分析 DeepSeek-Reasonix 的架构", "type": "plan", "model": "pro-x", "name": "架构分析"},
        ],
    )
    assert len(_calls) == 2, f"期望 2 个子代理, 实际 {len(_calls)}"
    p0, n0, m0, md0, t0 = _calls[0]
    assert p0 == "查一下 simple_forum 的路由" and t0 == "explore" and md0 is None
    p1, n1, m1, md1, t1 = _calls[1]
    assert p1 == "分析 DeepSeek-Reasonix 的架构" and t1 == "plan" and md1 == "pro-x" and n1 == "架构分析"
    print("PASS 混合 str+dict：dict 独立透传 prompt/type/model/name")


def test_dict_illegal_type_normalized():
    _setup()
    sa.run_agent(tasks=[{"prompt": "x", "type": "hack"}])
    assert _calls[0][4] == "explore", f"非法 type 应回落 explore, 实际 {_calls[0][4]}"
    print("PASS dict 非法 type → 回落 explore")


def test_dict_missing_prompt_filtered():
    _setup()
    sa.run_agent(tasks=[{"name": "无指令"}, "有效任务"])
    assert len(_calls) == 1 and _calls[0][0] == "有效任务"
    print("PASS dict 缺 prompt → 跳过该任务，其余正常")


def test_string_only_uses_call_level():
    _setup()
    sa.run_agent(agent_type="lint", model="flash-x", tasks=["a", "b"])
    assert [c[4] for c in _calls] == ["lint", "lint"]
    assert [c[3] for c in _calls] == ["flash-x", "flash-x"]
    print("PASS 纯字符串任务 → 使用调用级 type/model")


def test_count_split_unaffected():
    _setup()
    sa.run_agent(prompt="1. 查 A\n2. 查 B", count=2)
    assert len(_calls) == 2 and _calls[0][0] == "查 A" and _calls[1][0] == "查 B"
    print("PASS count 拆分逻辑不受影响")


def test_max_concurrent_cap():
    _setup()
    sa.run_agent(tasks=[f"任务{i}" for i in range(8)])
    assert len(_calls) == 5, f"上限 5 个, 实际 {len(_calls)}"
    print("PASS 8 个任务 → 截断为 5 个")


def test_name_default_from_call_name():
    _setup()
    sa.run_agent(name="并行演示", tasks=["a"])
    assert _calls[0][1] == "并行演示#1"
    _setup()
    sa.run_agent(name="并行演示", tasks=[{"prompt": "b", "name": "自定义"}])
    assert _calls[0][1] == "自定义"
    print("PASS 名称：dict 自带 name 优先，否则 调用名#N")


def test_count_unsplittable_returns_empty():
    """回归：count>1 且 prompt 拆不动（无编号列表/无 --- 分隔）时返回空列表，
    由调用方报错；不再复制同题并行（N 份相同总结注入主上下文）。"""
    _setup()
    sa.run_agent(prompt="单条指令", count=3)
    assert _calls == [], f"拆不动时不应复制同题并行，实际派发了 {len(_calls)} 个"
    print("PASS count>1 且 prompt 不可拆分 → 不复制，返回空列表由调用方报错")


def test_last_round_keeps_tools_with_fallback_tail():
    """回归（前缀缓存）：plan 预算最后一轮保留 tools 定义（请求前缀稳定）；
    若模型最后一轮仍调工具，追加无工具的强制收尾轮，任务仍产出总结而非判失败。
    （explore/lint/test/web_search_agent 已无轮次上限，本机制仅 plan 保留）"""
    import bin.ai_lib.api as api_mod
    import bin.ai_cmd as ai_cmd_mod

    _orig_rounds = sa.MAX_PLAN_ROUNDS
    _orig_api = api_mod.call_ai_api_sse
    _orig_exec = ai_cmd_mod.execute_mcp_tool
    sa.MAX_PLAN_ROUNDS = 2
    _api_calls = []
    _round = [0]

    def _fake_api(question="", messages=None, tools=None, **kw):
        _round[0] += 1
        _api_calls.append((list(messages), tools))
        if _round[0] in (1, 2):
            # 第 1 轮正常调工具；第 2 轮（预算最后一轮）模型违规仍调工具
            return {
                "tool_calls": [{
                    "name": "read_file", "id": f"call_{_round[0]}",
                    "params_str": '{"path": "a.txt"}',
                    "raw_arguments": '{"path": "a.txt"}',
                    "_native": True,
                }],
                "txt": "", "answer": "yes", "_reasoning": "",
            }
        # 第 3 轮：强制收尾轮（无工具），必须输出总结
        return {"tool_calls": [], "txt": "## Plan Summary\n计划完成", "answer": "yes", "_reasoning": ""}

    def _fake_exec(name, params, *a, **kw):
        return True, "file content"

    api_mod.call_ai_api_sse = _fake_api
    ai_cmd_mod.execute_mcp_tool = _fake_exec
    try:
        t = sa.ExploreTask("任务", agent_type="plan")
        mgr = sa.ExploreManager()
        mgr._execute(t)
        assert len(_api_calls) == 3, f"期望 3 轮 API 调用, 实际 {len(_api_calls)}"
        assert _api_calls[0][1] is not None, "第 1 轮应保留 tools（前缀缓存稳定）"
        assert _api_calls[1][1] is not None, "第 2 轮（预算最后一轮）应保留 tools"
        assert not _api_calls[2][1], "强制收尾轮应移除 tools"
        assert "计划完成" in t.summary, f"任务应成功产出总结, 实际 summary={t.summary!r}"
        assert t.status != "error", f"任务不应判失败, 实际 status={t.status}"
    finally:
        sa.MAX_PLAN_ROUNDS = _orig_rounds
        api_mod.call_ai_api_sse = _orig_api
        ai_cmd_mod.execute_mcp_tool = _orig_exec
    print("PASS plan 预算最后一轮保留 tools；违规调工具后有强制收尾轮兜底")


def _restore():
    sa._manager = _orig_manager


if __name__ == "__main__":
    try:
        test_mixed_str_and_dict()
        test_dict_illegal_type_normalized()
        test_dict_missing_prompt_filtered()
        test_string_only_uses_call_level()
        test_count_split_unaffected()
        test_max_concurrent_cap()
        test_name_default_from_call_name()
        test_count_unsplittable_returns_empty()
        test_last_round_keeps_tools_with_fallback_tail()
        print("\nALL PASS")
    finally:
        _restore()
