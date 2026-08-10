#!/usr/bin/env python3
"""web_search_agent 子代理：工具集 / web 工具执行路由 / 无轮次上限。

离线验证（mock API，不访问真实网络）。
运行: python3 test/virtual/test_subagent_web.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin.ai_lib import api as api_mod  # noqa: E402
from bin import ai_cmd as ai_cmd_mod  # noqa: E402
from bin.ai_lib import subagent as sa  # noqa: E402

_orig_api = api_mod.call_ai_api_sse
_orig_exec = ai_cmd_mod.execute_mcp_tool
_orig_est = sa._estimate_msgs_tokens
_orig_web_exec = ai_cmd_mod.get_subagent_web_executor()
_orig_cmd_exec = ai_cmd_mod.get_subagent_command_executor()


def _fake_api(round1_tools):
    """fake call_ai_api_sse：第一轮返回 round1_tools，之后输出总结。"""
    state = {"n": 0}

    def fake_api(**kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"tool_calls": round1_tools, "_reasoning": ""}
        return {"tool_calls": [], "txt": "## Web Search Summary\n调研完成", "_reasoning": ""}

    return fake_api


def _run(task, fake_api):
    api_mod.call_ai_api_sse = fake_api
    mgr = sa.ExploreManager()
    try:
        mgr._run_task(task)
    finally:
        api_mod.call_ai_api_sse = _orig_api
    return task


def test_web_agent_toolset():
    names = [t.get("function", {}).get("name") for t in sa.build_agent_tools("web_search_agent")]
    for want in ("web_search", "RunCommand", "read_file"):
        assert want in names, f"web_search_agent 缺工具 {want}: {names}"
    assert len(names) == len(set(names)), f"工具集不应有重复: {names}"
    # 已合并下线 / 非白名单工具不得混入
    for bad in ("write_file", "edit_file", "Agent", "WebSearch", "WebFetch"):
        assert bad not in names, f"web_search_agent 不应有 {bad}"
    print("PASS web_search_agent 工具集：web_search + RunCommand + 只读文件工具（旧 Web 工具已合并）")


def test_web_tools_route_through_web_executor():
    """web_search 经子代理 web 执行器执行（不走 MCP、不弹确认）。"""
    mcp_calls = []
    web_calls = []
    ai_cmd_mod.execute_mcp_tool = lambda *a, **k: (mcp_calls.append(a[0]), (True, "x"))[1]
    ai_cmd_mod.set_subagent_web_executor(
        lambda name, params: (web_calls.append((name, params)), "web result")[1])
    fake_api = _fake_api([
        {"name": "web_search", "params_str": '{"action": "mixed", "query": "deepseek api", "fetch_pages": true}'},
        {"name": "read_file", "params_str": '{"path": "/x"}'},
    ])
    task = _run(sa.ExploreTask("调研", agent_type="web_search_agent"), fake_api)
    assert task.status == "done"
    assert [c[0] for c in web_calls] == ["web_search"], \
        f"web 工具应走 web 执行器: {web_calls}"
    assert mcp_calls == ["read_file"], f"只有 read_file 走 MCP: {mcp_calls}"
    # web 执行器未初始化时的报错路径
    ai_cmd_mod.set_subagent_web_executor(None)
    out = sa._run_subagent_web_tool("web_search", {"query": "x"})
    assert "web 执行器未初始化" in out
    print("PASS web 工具路由：web_search 走子代理 web 执行器，read_file 走 MCP")


def test_unbounded_rounds_until_summary():
    """无轮次上限：模型持续调工具 25 轮（远超旧 lint/test=20、plan=10 上限）也能跑完，
    直到模型主动输出总结才结束（仅水位线会强制收尾，这里压住水位线只测轮次）。"""
    ai_cmd_mod.set_subagent_web_executor(lambda name, params: "web result")
    ai_cmd_mod.execute_mcp_tool = lambda *a, **k: (True, "x")
    sa._estimate_msgs_tokens = lambda messages: 100  # 压住水位线（600K），只测轮次行为
    _api_calls = []
    _round = [0]

    def fake_api(question="", messages=None, tools=None, **kw):
        _round[0] += 1
        _api_calls.append(tools)
        if _round[0] <= 25:
            return {"tool_calls": [{"name": "web_search", "id": f"c{_round[0]}",
                                    "params_str": '{"query": "q"}',
                                    "raw_arguments": '{"query": "q"}'}],
                    "txt": "", "_reasoning": ""}
        return {"tool_calls": [], "txt": "## Web Search Summary\n调研完成", "_reasoning": ""}

    api_mod.call_ai_api_sse = fake_api
    try:
        t = sa.ExploreTask("调研", agent_type="web_search_agent")
        mgr = sa.ExploreManager()
        mgr._execute(t)
        assert len(_api_calls) == 26, f"25 轮工具后应正常收尾, 实际 {len(_api_calls)} 轮"
        # 无上限类型没有「预算耗尽移除 tools」机制：模型主动输出总结即结束，
        # 全部轮次保留 tools（前缀缓存稳定）；仅水位线/超限兜底才走无工具收尾轮。
        assert all(tools is not None for tools in _api_calls), "无上限类型全程保留 tools"
        assert "调研完成" in t.summary, f"应产出总结: {t.summary!r}"
        assert t.status != "error", f"不应判失败: {t.status}"
    finally:
        api_mod.call_ai_api_sse = _orig_api
    print("PASS 无轮次上限：web_search_agent 连续 25 轮工具后正常收尾（26 轮 API）")


if __name__ == "__main__":
    try:
        test_web_agent_toolset()
        test_web_tools_route_through_web_executor()
        test_unbounded_rounds_until_summary()
        print("\nALL PASS")
    finally:
        api_mod.call_ai_api_sse = _orig_api
        ai_cmd_mod.execute_mcp_tool = _orig_exec
        sa._estimate_msgs_tokens = _orig_est
        ai_cmd_mod.set_subagent_web_executor(_orig_web_exec)
        ai_cmd_mod.set_subagent_command_executor(_orig_cmd_exec)
