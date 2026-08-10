"""RecoveryRecipes — 故障自动恢复配方

当工具调用连续失败时，自动尝试恢复策略而不是傻重试。
"""

from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class FailureScenario(str, Enum):
    """故障场景"""
    TRUST_PROMPT_UNRESOLVED = "trust_prompt_unresolved"
    PROMPT_MISDELIVERY = "prompt_misdelivery"
    STALE_BRANCH = "stale_branch"
    COMPILE_FAILURE = "compile_failure"
    MCP_HANDSHAKE_FAILURE = "mcp_handshake_failure"
    TOOL_FAILURE = "tool_failure"
    PROVIDER_FAILURE = "provider_failure"


class RecoveryAction(str, Enum):
    """可执行的恢复步骤"""
    RETRY_TOOL = "retry_tool"
    SWITCH_STRATEGY = "switch_strategy"
    REDIRECT_PROMPT = "redirect_prompt"
    CLEAN_BUILD = "clean_build"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    RESTART_MCP = "restart_mcp"
    SKIP_AND_CONTINUE = "skip_and_continue"


class EscalationPolicy(str, Enum):
    ALERT_HUMAN = "alert_human"
    LOG_AND_CONTINUE = "log_and_continue"
    ABORT = "abort"


@dataclass
class RecoveryRecipe:
    """一个故障场景对应的恢复配方"""
    scenario: FailureScenario
    steps: list[RecoveryAction]
    max_attempts: int = 1
    escalation: EscalationPolicy = EscalationPolicy.ALERT_HUMAN


@dataclass
class RecoveryAttempt:
    """一次恢复尝试的记录"""
    scenario: FailureScenario
    action_taken: RecoveryAction
    success: bool
    message: str = ""


@dataclass
class RecoveryContext:
    """恢复上下文 — 跟踪每个场景的尝试次数和历史"""
    attempt_counts: dict[str, int] = field(default_factory=dict)
    history: list[RecoveryAttempt] = field(default_factory=list)
    last_successful_action: Optional[RecoveryAction] = None


# ── 预定义恢复配方 ──
RECIPES: dict[FailureScenario, RecoveryRecipe] = {
    FailureScenario.TOOL_FAILURE: RecoveryRecipe(
        scenario=FailureScenario.TOOL_FAILURE,
        steps=[RecoveryAction.SWITCH_STRATEGY, RecoveryAction.RETRY_TOOL],
        max_attempts=2,
        escalation=EscalationPolicy.ALERT_HUMAN,
    ),
    FailureScenario.MCP_HANDSHAKE_FAILURE: RecoveryRecipe(
        scenario=FailureScenario.MCP_HANDSHAKE_FAILURE,
        steps=[RecoveryAction.RESTART_MCP, RecoveryAction.RETRY_TOOL],
        max_attempts=1,
        escalation=EscalationPolicy.ABORT,
    ),
    FailureScenario.PROVIDER_FAILURE: RecoveryRecipe(
        scenario=FailureScenario.PROVIDER_FAILURE,
        steps=[RecoveryAction.SKIP_AND_CONTINUE],
        max_attempts=1,
        escalation=EscalationPolicy.LOG_AND_CONTINUE,
    ),
    FailureScenario.PROMPT_MISDELIVERY: RecoveryRecipe(
        scenario=FailureScenario.PROMPT_MISDELIVERY,
        steps=[RecoveryAction.REDIRECT_PROMPT],
        max_attempts=1,
        escalation=EscalationPolicy.ALERT_HUMAN,
    ),
    FailureScenario.STALE_BRANCH: RecoveryRecipe(
        scenario=FailureScenario.STALE_BRANCH,
        steps=[RecoveryAction.CLEAN_BUILD],
        max_attempts=1,
        escalation=EscalationPolicy.ALERT_HUMAN,
    ),
    FailureScenario.COMPILE_FAILURE: RecoveryRecipe(
        scenario=FailureScenario.COMPILE_FAILURE,
        steps=[RecoveryAction.CLEAN_BUILD, RecoveryAction.SWITCH_STRATEGY],
        max_attempts=2,
        escalation=EscalationPolicy.ALERT_HUMAN,
    ),
}


def classify_failure(tool_name: str, error_msg: str) -> FailureScenario:
    """根据工具名和错误信息自动分类故障场景。"""
    err_lower = error_msg.lower()
    if "mcp" in err_lower and ("handshake" in err_lower or "connect" in err_lower or "timeout" in err_lower):
        return FailureScenario.MCP_HANDSHAKE_FAILURE
    if "provider" in err_lower or "api" in err_lower or "model" in err_lower:
        return FailureScenario.PROVIDER_FAILURE
    if "compile" in err_lower or "build" in err_lower or "cargo" in err_lower:
        return FailureScenario.COMPILE_FAILURE
    if "branch" in err_lower or "stale" in err_lower or "merge" in err_lower:
        return FailureScenario.STALE_BRANCH
    if "prompt" in err_lower and ("redirect" in err_lower or "deliver" in err_lower):
        return FailureScenario.PROMPT_MISDELIVERY
    return FailureScenario.TOOL_FAILURE


def get_recovery_message(scenario: FailureScenario, ctx: RecoveryContext) -> str:
    """生成恢复建议消息，注入到 AI 上下文。"""
    recipe = RECIPES.get(scenario)
    if not recipe:
        return ""
    key = scenario.value
    attempt = ctx.attempt_counts.get(key, 0)
    if attempt >= recipe.max_attempts:
        # 超过最大尝试次数，升级
        if recipe.escalation == EscalationPolicy.ALERT_HUMAN:
            return f"[RECOVERY] 已尝试 {attempt} 次仍未解决 {scenario.value}，建议更换方案或询问用户"
        elif recipe.escalation == EscalationPolicy.ABORT:
            return f"[RECOVERY] {scenario.value} 无法自动恢复，终止当前操作"
        else:
            return ""
    # 建议恢复步骤
    steps_str = " → ".join(a.value for a in recipe.steps)
    return f"[RECOVERY] 检测到 {scenario.value}，尝试恢复（{steps_str}），第 {attempt + 1}/{recipe.max_attempts} 次"


def record_attempt(ctx: RecoveryContext, scenario: FailureScenario,
                   action: RecoveryAction, success: bool, message: str = ""):
    """记录一次恢复尝试。"""
    key = scenario.value
    ctx.attempt_counts[key] = ctx.attempt_counts.get(key, 0) + 1
    ctx.history.append(RecoveryAttempt(
        scenario=scenario,
        action_taken=action,
        success=success,
        message=message,
    ))
    if success:
        ctx.last_successful_action = action
