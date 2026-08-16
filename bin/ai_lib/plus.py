# -*- coding: utf-8 -*-
"""
plus.py — Onyx Plus 高级模式思考流水线

/plus 一次性高级模式：任务开始前，主 agent 先跑「分析→模拟→自检→规划」4 步思考。

设计（复用 subagent 制作原理）：
- 4 步独立 LLM 调用（文本传递：上一步输出喂给下一步），彼此隔离、可审计、省 token。
- 每步使用当前系列最贵模型（resolve_best_model）——思考质量优先，成本接受。
- 提示词 = etc/ai/self.md（纯净自我认知，不带 skill/Onyx 介绍，思考阶段保持纯洁）。
- 每步可调用只读工具（复用 subagent 工具集，exclude_agent=True 禁止派生子代理）。
- 4 步完成后的最终规划文本写入主记忆（library/<uuid>.txt + time 树），
  并返回给调用方注入干活阶段（主 agent 上下文）。
- 任何一步失败：整体降级为「返回已有步骤结果 + 空规划」，不阻塞主流程。
"""
import os
import json
import time
import uuid
from typing import Dict, List, Optional

from .subagent import (
    build_agent_tools,
    _param_log,
    _run_subagent_command,
    _run_subagent_web_tool,
    TOOL_OUTPUT_CAP,
    _SUBAGENT_TOKEN_WATERLINE,
    _estimate_msgs_tokens,
    _is_ctx_overflow,
)

# ── 4 步思考定义 ──
PLUS_STEPS: List[Dict] = [
    {
        "key": "analysis",
        "title": "分析任务",
        "heading": "## 任务分析",
        "role": (
            "你现在是 **Plus 思考流水线第 1 步：任务分析师**。\n"
            "- 你的唯一职责是**深入分析用户任务**：目标是什么、约束有哪些、风险点、"
            "需要哪些信息/工具、可能的难点。\n"
            "- 可以调用只读工具查证（读文件/搜索/查环境），但**不要派生子代理，不要修改任何文件**。\n"
            "- **工具最多查证 1~2 次**，不要反复调用；查证后立即输出分析结论。\n"
            "- 输出：分析结论（300 字以内，中文），必须以此标题开头：\n"
            "  ## 任务分析\n"
        ),
    },
    {
        "key": "simulate",
        "title": "模拟执行",
        "heading": "## 模拟推演",
        "role": (
            "你现在是 **Plus 思考流水线第 2 步：推演者**。\n"
            "- 基于上一步的任务分析，**在脑中模拟执行一遍任务**：按什么顺序做、每步会得到什么、"
            "会遇到什么障碍、如何应对。\n"
            "- 可以调用只读工具查证细节，但**不要派生子代理，不要修改任何文件**。\n"
            "- **工具最多查证 1~2 次**，不要反复调用；查证后立即输出推演结论。\n"
            "- 输出：推演过程与关键节点（300 字以内，中文），必须以此标题开头：\n"
            "  ## 模拟推演\n"
        ),
    },
    {
        "key": "selfcheck",
        "title": "自检",
        "heading": "## 自检结论",
        "role": (
            "你现在是 **Plus 思考流水线第 3 步：自检员**。\n"
            "- 对前两步（分析 + 模拟）进行**批判性检查**：有没有遗漏、矛盾、过度乐观、"
            "风险低估？结论是否站得住？需要修正什么？\n"
            "- 可以调用只读工具复核，但**不要派生子代理，不要修改任何文件**。\n"
            "- **工具最多查证 1~2 次**，不要反复调用；查证后立即输出自检结论。\n"
            "- 输出：自检发现与修正（200 字以内，中文），必须以此标题开头：\n"
            "  ## 自检结论\n"
        ),
    },
    {
        "key": "plan",
        "title": "规划",
        "heading": "## 执行规划",
        "role": (
            "你现在是 **Plus 思考流水线第 4 步：规划者**。\n"
            "- 综合前三步（分析/模拟/自检），产出**可执行的分步规划**：每步做什么、"
            "涉及哪些文件/命令、如何验证、风险与回退。\n"
            "- 可以调用只读工具确认细节，但**不要派生子代理，不要修改任何文件**。\n"
            "- **工具最多查证 1~2 次**，不要反复调用；查证后立即输出完整规划。\n"
            "- 输出：完整执行规划（500 字以内，中文），必须以此标题开头：\n"
            "  ## 执行规划\n"
            "- 这是思考流水线最后一步，规划要具体到可执行，不要泛泛而谈。\n"
        ),
    },
]

_STEP_TIMEOUT = 600.0     # 单步总超时保险丝（秒）——非轮次限制，超时进强制收尾轮


def _load_self_prompt() -> str:
    """读取 etc/ai/self.md（纯净自我认知）；失败回退简短身份。"""
    try:
        from .config import ROOT_DIR
        for _ap in (
            os.path.join(ROOT_DIR, "onyx", "etc", "ai", "self.md"),
            os.path.join("etc", "ai", "self.md"),
        ):
            if os.path.exists(_ap):
                with open(_ap, "r", encoding="utf-8") as f:
                    return f.read().strip()
    except Exception:
        pass
    return ("You are Onyx, an interactive AI assistant inside the Onyx terminal. "
            "Follow the user's task carefully.")


def _pick_best_model(platform: str) -> str:
    """当前系列最贵模型；失败回退默认模型。"""
    try:
        from .cost import resolve_best_model, resolve_default_model
        return resolve_best_model(platform) or resolve_default_model(platform) or ""
    except Exception:
        return ""


def _run_one_step(step: Dict, question: str, previous: str, mem_home: str,
                  platform: str, model: str, on_log=None) -> str:
    """执行单个思考步骤：LLM 循环 + 只读工具，返回该步最终文本。

    与 subagent._execute 同机制：**不设轮次上限**——直到模型主动输出纯文本
    （无工具调用）才判定该步结束。仅有的强制收尾触发点：
    - 上下文水位线（_SUBAGENT_TOKEN_WATERLINE）超过 → 强制收尾轮；
    - 上下文超限 API 报错 → 下一轮强制收尾轮；
    - 单步总超时（_STEP_TIMEOUT，保险丝，非轮次）→ 强制收尾轮。
    """
    from .api import call_ai_api_sse

    system_prompt = _load_self_prompt() + "\n\n" + step["role"]
    tools = build_agent_tools("explore", exclude_agent=True)  # 只读 + 禁 Agent
    user_content = f"用户任务：\n{question}\n"
    if previous:
        user_content += f"\n\n上一步结果：\n{previous}\n"
    user_content += (
        f"\n现在执行第 {step['key']} 步：{step['title']}。\n"
        f"完成思考后直接输出最终结果（{step['heading']} 开头），不要输出工具调用以外的多余内容。"
    )
    messages: List[Dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    acc_text = ""          # 累积工具轮之间模型输出的文本（兜底不丢）
    _ctx_retried = False   # 上下文超限兜底已触发（下一次直接进强制收尾轮）
    t0 = time.time()
    rnd = 0
    while True:
        rnd += 1
        # ── 收尾判定：水位线 / 上下文超限兜底 / 总超时 → 不带 tools 的强制收尾轮 ──
        _over_water = _ctx_retried or _estimate_msgs_tokens(messages) >= _SUBAGENT_TOKEN_WATERLINE
        _over_time = time.time() - t0 > _STEP_TIMEOUT
        if _over_water or _over_time:
            _round_tools = []
            if on_log:
                on_log(f"⚠️ {step['title']} " + (
                    "上下文超限，进入强制收尾轮" if _ctx_retried else
                    (f"上下文水位过高（{_SUBAGENT_TOKEN_WATERLINE}），进入强制收尾轮"
                     if _over_water else "超时，进入强制收尾轮")))
            messages.append({
                "role": "system",
                "content": (
                    f"⚠️ 这是 {step['title']} 的最终强制收尾轮：你没有工具可用，"
                    f"必须立即输出本步最终结果（{step['heading']} 开头），"
                    "覆盖所有要点，不要遗漏。"
                ),
            })
        else:
            _round_tools = tools
        try:
            result = call_ai_api_sse(
                question="",
                messages=messages,
                tools=_round_tools,
                ai_tools_prompt="",
                user_home_dir=mem_home,
                memory_block="",
                session_id=f"plus_{step['key']}_{uuid.uuid4().hex[:6]}",
                model_override=model,
                platform_override=platform,
            )
        except Exception as e:
            if on_log:
                on_log(f"⚠️ {step['title']} API 错误: {e}")
            break
        if result.get("error"):
            # ── 上下文超限兜底：标记后下一轮强制收尾，不判失败（与 subagent 一致）──
            if not _ctx_retried and _is_ctx_overflow(str(result.get("error"))):
                _ctx_retried = True
                if on_log:
                    on_log(f"⚠️ {step['title']} 上下文超限 → 下一轮强制收尾输出结果")
                continue
            if on_log:
                on_log(f"⚠️ {step['title']} 错误: {result['error']}")
            break
        txt = (result.get("txt") or "").strip()
        reasoning = (result.get("_reasoning") or "").strip()
        tool_calls = result.get("tool_calls") or []
        if txt:
            acc_text = (acc_text + "\n\n" + txt if acc_text else txt).strip()
        if not tool_calls:
            # 该步结束：模型输出纯文本（无工具调用）。txt 为空时（thinking 模式
            # 内容全在 reasoning_content）用累积文本/推理文本兜底，避免返回空。
            return txt or acc_text or reasoning[-1500:]
        # ── 执行只读工具并回填 ──
        tc_ids = []
        tc_items = []
        for i, tc in enumerate(tool_calls):
            raw_id = tc.get("id") or f"plus_{step['key']}_{rnd}_{i}"
            tc_ids.append(raw_id)
            raw_args = tc.get("raw_arguments") or tc.get("params_str") or "{}"
            tc_items.append({
                "id": raw_id,
                "type": "function",
                "function": {"name": tc.get("name", ""), "arguments": raw_args},
            })
        # 关键修复：assistant 消息的 content 保留本轮 txt（工具调用前的思考文本），
        # 不再写死 None —— 否则强制收尾兜底找不到任何文本。
        messages.append({
            "role": "assistant",
            "content": txt or None,
            "tool_calls": tc_items,
            "reasoning_content": reasoning,
        })
        for i, tc in enumerate(tool_calls):
            name = tc.get("name", "")
            params_str = tc.get("params_str") or "{}"
            params = {}
            try:
                if params_str.strip().startswith("{"):
                    params = json.loads(params_str)
            except Exception:
                params = {}
            if on_log:
                on_log(f"🔧 {step['title']}: {name}{_param_log(params)}")
            if name == "RunCommand":
                cmd = params.get("command", "")
                ok, output = (False, "RunCommand: 缺少 command 参数") if not cmd \
                    else (True, _run_subagent_command(cmd))
            elif name in ("web_search",):
                ok, output = True, _run_subagent_web_tool(name, params)
            else:
                try:
                    from bin.ai_cmd import execute_mcp_tool
                    ok, output = execute_mcp_tool(name, params, "filesystem", "low")
                except Exception as e:
                    ok, output = False, f"tool execution error: {e}"
            try:
                _trunc = output[:TOOL_OUTPUT_CAP] if isinstance(output, str) else str(output)
                if not ok or (isinstance(output, str) and output.startswith("error:")):
                    _trunc = "error: " + _trunc
            except Exception:
                _trunc = output
            messages.append({
                "role": "tool",
                "tool_call_id": tc_ids[i],
                "content": _trunc,
                "is_error": not ok,
            })


def run_plus_think(question: str, mem_home: str, on_log=None) -> Dict:
    """Plus 思考流水线主入口。

    返回：
      {"ok": bool, "steps": {key: text}, "plan": str, "session_id": str}
    plan = 第 4 步规划文本（或前三步综合），注入干活阶段用。
    任何一步失败都不抛异常，逐步降级。
    """
    from .config import load_key_conf

    conf = load_key_conf() or {}
    platform = conf.get("platform", "deepseek")
    model = _pick_best_model(platform) or conf.get("model", "")

    if on_log:
        on_log(f"🧠 Plus 思考开始（模型: {model or '默认'}）")
    results: Dict[str, str] = {}
    previous = ""
    for step in PLUS_STEPS:
        t0 = time.time()
        if on_log:
            on_log(f"▶️ {step['title']}")
        try:
            out = _run_one_step(step, question, previous, mem_home, platform, model, on_log)
        except Exception as e:
            out = ""
            if on_log:
                on_log(f"⚠️ {step['title']} 异常: {e}")
        if out:
            results[step["key"]] = out
            previous = out
        if on_log:
            on_log(f"✅ {step['title']} 完成（{len(out)} 字，{time.time()-t0:.1f}s）")

    plan = results.get("plan", "")
    if not plan:
        # 降级：用已有步骤拼接（至少要有分析；全失败则空）
        plan = "\n\n".join(
            f"{PLUS_STEPS[i]['heading']}\n{results[k]}"
            for i, (k, v) in enumerate(results.items()) if v
        )

    # ── 写入主记忆：library/<uuid>.txt + time 树（复用 record_ai_session）──
    session_id = ""
    try:
        from .storage import record_ai_session
        session_id = uuid.uuid4().hex
        record_ai_session(
            mem_home, session_id, f"[plus 思考] {question}",
            {"txt": plan, "tag": "plus", "class": "1", "memory": ""},
            user_answer="",
            cmd_results={},
            referenced_memory="",
            native_results={},
            markup_results={},
        )
    except Exception:
        session_id = ""

    if on_log:
        on_log(f"💾 思考结果已写入主记忆（{len(plan)} 字）")
    return {"ok": bool(plan), "steps": results, "plan": plan, "session_id": session_id}
