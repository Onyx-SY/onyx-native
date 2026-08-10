#!/usr/bin/env python3
"""离线验证子代理执行层白名单闸 + 命令路由 + 内置命令拦截。

- explore 不能调 Agent（嵌套套娃）；RunCommand 经安全管线放行（不再拒绝）
- 内置命令（exit/cd/sudo/clear 等）在子代理命令管线中被拒绝（不暴露给子代理）
- lint 的 RunCommand 经安全管线执行（不被白名单拒绝）

运行: python3 test/virtual/test_subagent_whitelist.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin.ai_lib import api as api_mod  # noqa: E402
from bin import ai_cmd as ai_cmd_mod  # noqa: E402
from bin.ai_lib import subagent as sa  # noqa: E402

_orig_api = api_mod.call_ai_api_sse
_orig_exec = ai_cmd_mod.execute_mcp_tool
_orig_cmd_exec = ai_cmd_mod.get_subagent_command_executor()
_orig_web_exec = ai_cmd_mod.get_subagent_web_executor()
# 与 bin/ai_lib/config.py 相同的 ROOT_DIR 推导（含 onyx/etc/other_terminal_cmd.json）
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _fake_api_factory(round1_calls, round1_tools):
    """返回 fake call_ai_api_sse：第一轮返回 round1_tools，之后返回总结。"""
    state = {"n": 0, "seen_msgs": None}

    def fake_api(**kw):
        state["n"] += 1
        if state["n"] == 1:
            return {"tool_calls": round1_tools, "_reasoning": ""}
        state["seen_msgs"] = kw.get("messages") or []
        return {"txt": "## Explore Summary\n调查完成", "_reasoning": ""}

    return fake_api, state


def _run(task, fake_api):
    api_mod.call_ai_api_sse = fake_api
    mgr = sa.ExploreManager()
    try:
        mgr._run_task(task)  # 走完整入口（信号量 + finally 置 done）
    finally:
        api_mod.call_ai_api_sse = _orig_api
    return task


def test_explore_rejects_agent_but_allows_runcommand():
    exec_calls = []
    cmd_calls = []
    ai_cmd_mod.execute_mcp_tool = lambda *a, **k: (exec_calls.append(a[0]), (True, "ok"))[1]
    ai_cmd_mod.set_subagent_command_executor(
        lambda c: (cmd_calls.append(c), f"命令: {c}\n退出码: 0\n执行结果:\n(ok)")[1])
    fake_api, state = _fake_api_factory(1, [
        {"name": "Agent", "params_str": '{"prompt":"套娃测试"}'},
        {"name": "RunCommand", "params_str": '{"command":"pytest -q"}'},
        {"name": "read_file", "params_str": '{"path":"/x"}'},
    ])
    task = _run(sa.ExploreTask("测试", agent_type="explore"), fake_api)
    assert task.status == "done" and task.summary
    # Agent 被白名单拒绝（防嵌套套娃）；read_file 走 MCP；RunCommand 经命令管线
    assert exec_calls == ["read_file"], f"应只执行 read_file, 实际 {exec_calls}"
    assert cmd_calls == ["pytest -q"], f"RunCommand 应经安全管线执行, 实际 {cmd_calls}"
    # 第二轮发给模型的 tool 结果里含拒绝消息
    tool_msgs = [m.get("content", "") for m in state["seen_msgs"] if m.get("role") == "tool"]
    joined = "\n".join(tool_msgs)
    assert "白名单" in joined and "Agent" in joined
    assert "pytest" in joined and "read_file" in joined
    print("PASS explore：Agent 被拒（防套娃），RunCommand 经安全管线放行，read_file 正常")


def test_builtin_commands_blocked_in_pipeline():
    """子代理命令管线的内置命令拦截（模块级函数即 handle_ai 闭包使用的真实逻辑）。"""
    from bin.ai_cmd import build_subagent_blocked_commands, extract_subagent_command_head
    _blocked = build_subagent_blocked_commands({"exit": None, "ai": None}, _ROOT)
    for c in ("exit", "cd", "sudo", "clear", "pwd", "sado", "source", "export"):
        assert c in _blocked, f"内置命令 {c} 应在拦截集（当前: {len(_blocked)} 个）"
    for c in ("git", "python", "pytest", "ls", "grep", "find"):
        assert c not in _blocked, f"系统命令 {c} 不应在拦截集"
    assert extract_subagent_command_head("  sudo rm -rf /\nfoo") == "sudo"
    assert extract_subagent_command_head("") == ""
    assert extract_subagent_command_head("git status") == "git"
    assert extract_subagent_command_head("\npwd") == "pwd"
    print("PASS 内置命令拦截：exit/cd/sudo/... 在列，git/python/... 放行")


def test_builtin_denied_in_subagent_execution():
    """端到端：子代理调用 RunCommand(exit) → 命令管线拒绝（不执行、不退出）。"""
    calls = []

    def _pipeline(cmd):
        """模拟 handle_ai 闭包内的拦截逻辑（真实函数 + 真实拦截集）。"""
        from bin.ai_cmd import build_subagent_blocked_commands, extract_subagent_command_head
        _blocked = build_subagent_blocked_commands({"exit": None, "ai": None}, _ROOT)
        calls.append(cmd)
        _head = extract_subagent_command_head(cmd)
        if _head and _head in _blocked:
            return f"⛔ 命令被拒绝：`{_head}` 是 Onyx/终端内置命令，子代理不可用。"
        return f"命令: {cmd}\n退出码: 0\n执行结果:\n(ok)"

    ai_cmd_mod.set_subagent_command_executor(_pipeline)
    ai_cmd_mod.execute_mcp_tool = lambda *a, **k: (True, "ok")
    fake_api, state = _fake_api_factory(1, [
        {"name": "RunCommand", "params_str": '{"command":"exit"}'},
        {"name": "RunCommand", "params_str": '{"command":"cd /tmp"}'},
        {"name": "RunCommand", "params_str": '{"command":"git status"}'},
    ])
    task = _run(sa.ExploreTask("测试", agent_type="lint"), fake_api)
    assert task.status == "done"
    tool_msgs = [m.get("content", "") for m in state["seen_msgs"] if m.get("role") == "tool"]
    joined = "\n".join(tool_msgs)
    assert "exit" in joined and "内置命令" in joined, "exit 应被内置命令拦截"
    assert "cd" in joined and "内置命令" in joined, "cd 应被内置命令拦截"
    assert "git status" in joined and "退出码: 0" in joined, "git status 应正常执行"
    print("PASS 子代理执行层：exit/cd 被拒，git status 放行")


def test_lint_allows_runcommand_via_pipeline():
    exec_calls = []
    ai_cmd_mod.execute_mcp_tool = lambda *a, **k: (exec_calls.append(a[0]), (True, "ok"))[1]
    fake_api, state = _fake_api_factory(1, [
        {"name": "RunCommand", "params_str": '{"command":"pytest"}'},
    ])
    task = _run(sa.ExploreTask("测试", agent_type="lint"), fake_api)
    assert task.status == "done"
    # lint 白名单含 RunCommand → 不走 execute_mcp_tool，走安全管线
    assert exec_calls == [], f"RunCommand 不应进 execute_mcp_tool, 实际 {exec_calls}"
    tool_msgs = [m.get("content", "") for m in state["seen_msgs"] if m.get("role") == "tool"]
    joined = "\n".join(tool_msgs)
    assert "白名单" not in joined, "lint 的 RunCommand 不应被白名单拒绝"
    print("PASS lint：RunCommand 经安全管线放行（不被白名单拒绝）")


if __name__ == "__main__":
    try:
        test_explore_rejects_agent_but_allows_runcommand()
        test_builtin_commands_blocked_in_pipeline()
        test_builtin_denied_in_subagent_execution()
        test_lint_allows_runcommand_via_pipeline()
        print("\nALL PASS")
    finally:
        api_mod.call_ai_api_sse = _orig_api
        ai_cmd_mod.execute_mcp_tool = _orig_exec
        ai_cmd_mod.set_subagent_command_executor(_orig_cmd_exec)
        ai_cmd_mod.set_subagent_web_executor(_orig_web_exec)
