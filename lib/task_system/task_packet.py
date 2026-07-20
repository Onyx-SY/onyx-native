"""TaskPacket — 结构化任务定义"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class TaskScope(str, Enum):
    """任务作用域"""
    WORKSPACE = "workspace"
    MODULE = "module"
    SINGLE_FILE = "single_file"
    CUSTOM = "custom"


@dataclass
class TaskResource:
    """任务可访问的资源"""
    kind: str
    value: str


@dataclass
class TaskPacket:
    """完整任务包 — 定义任务的目标、范围、验收标准、验证计划等"""

    objective: str
    scope: TaskScope = TaskScope.WORKSPACE
    scope_path: Optional[str] = None

    # 仓库与分支
    repo: str = ""
    worktree: Optional[str] = None
    branch_policy: str = ""

    # 验收
    acceptance_tests: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    resources: list[TaskResource] = field(default_factory=list)

    # 模型
    model: Optional[str] = None
    provider: Optional[str] = None
    permission_profile: Optional[str] = None

    # 策略
    commit_policy: str = ""
    reporting_contract: str = ""
    reporting_targets: list[str] = field(default_factory=list)
    escalation_policy: str = ""
    recovery_policy: Optional[str] = None

    # 验证
    verification_plan: list[str] = field(default_factory=list)


class TaskPacketValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_packet(packet: TaskPacket) -> TaskPacket:
    """验证任务包，返回原包或抛出异常"""
    errors: list[str] = []

    def check_required(field: str, value: str):
        if not value.strip():
            errors.append(f"{field} must not be empty")

    check_required("objective", packet.objective)
    if packet.scope != TaskScope.WORKSPACE:
        if not packet.scope_path or not packet.scope_path.strip():
            errors.append(f"scope_path is required for scope '{packet.scope.value}'")

    # 验收标准
    if not packet.acceptance_tests and not packet.acceptance_criteria:
        errors.append("acceptance_tests or acceptance_criteria must not be empty")
    for i, t in enumerate(packet.acceptance_tests):
        if not t.strip():
            errors.append(f"acceptance_tests contains empty value at index {i}")
    for i, c in enumerate(packet.acceptance_criteria):
        if not c.strip():
            errors.append(f"acceptance_criteria contains empty value at index {i}")

    # 资源
    for i, r in enumerate(packet.resources):
        if not r.kind.strip() or not r.value.strip():
            errors.append(f"resources contains incomplete entry at index {i}")

    # 报告
    if not packet.reporting_contract.strip() and not packet.reporting_targets:
        errors.append("reporting_contract or reporting_targets must not be empty")

    # 升级策略
    if not packet.escalation_policy.strip() and (
        not packet.recovery_policy or not packet.recovery_policy.strip()
    ):
        errors.append("escalation_policy or recovery_policy must not be empty")

    # 可选字段非空检查
    for fname, val in [("model", packet.model), ("provider", packet.provider),
                       ("permission_profile", packet.permission_profile),
                       ("recovery_policy", packet.recovery_policy)]:
        if val is not None and not val.strip():
            errors.append(f"{fname} must not be empty when present")

    for i, s in enumerate(packet.verification_plan):
        if not s.strip():
            errors.append(f"verification_plan contains empty value at index {i}")

    if errors:
        raise TaskPacketValidationError(errors)
    return packet


def packet_to_dict(packet: TaskPacket) -> dict:
    """序列化 TaskPacket 为 dict（JSON 友好）"""
    d = asdict(packet)
    d["scope"] = packet.scope.value
    d["resources"] = [{"kind": r.kind, "value": r.value} for r in packet.resources]
    return d


def dict_to_packet(data: dict) -> TaskPacket:
    """从 dict 反序列化 TaskPacket"""
    if "scope" in data and isinstance(data["scope"], str):
        data["scope"] = TaskScope(data["scope"])
    resources = data.pop("resources", [])
    packet = TaskPacket(**data)
    packet.resources = [TaskResource(**r) if isinstance(r, dict) else r for r in resources]
    return packet
