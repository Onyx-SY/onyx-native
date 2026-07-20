"""ApprovalTokens — 审批令牌链

为危险操作提供一次性审批令牌，支持：
- 作用域限定（策略 + 动作 + 仓库/分支）
- 一次性使用（默认）
- 过期机制
- 审批链审计（谁批准了什么）
"""

from __future__ import annotations
import time
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ApprovalTokenStatus(str, Enum):
    PENDING = "pending"
    GRANTED = "granted"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass
class ApprovalScope:
    """审批作用域"""
    policy: str = ""        # 策略名称，如 "dangerous_write"
    action: str = ""        # 动作描述，如 "write_file /etc/passwd"
    repository: Optional[str] = None
    branch: Optional[str] = None


@dataclass
class ApprovalDelegationHop:
    """授权链的一跳"""
    actor: str
    reason: str
    timestamp: float = 0.0


@dataclass
class ApprovalTokenGrant:
    """审批令牌"""
    token: str
    scope: ApprovalScope
    approving_actor: str        # 谁批准的
    approved_executor: str      # 谁可以使用
    status: ApprovalTokenStatus = ApprovalTokenStatus.PENDING
    expires_at: Optional[float] = None
    max_uses: int = 1
    uses: int = 0
    delegation_chain: list[ApprovalDelegationHop] = field(default_factory=list)


@dataclass
class ApprovalTokenAudit:
    """审批审计记录"""
    token: str
    status: ApprovalTokenStatus
    scope: ApprovalScope
    approving_actor: str
    approved_executor: str
    uses: int
    max_uses: int
    delegation_chain: list[ApprovalDelegationHop]


class ApprovalTokenLedger:
    """审批令牌账本"""

    def __init__(self):
        self._tokens: dict[str, ApprovalTokenGrant] = {}

    def create(self, scope: ApprovalScope, approving_actor: str,
               approved_executor: str = "ai", max_uses: int = 1,
               ttl_seconds: Optional[int] = 300) -> ApprovalTokenGrant:
        """创建新审批令牌。"""
        token = secrets.token_hex(16)
        expires_at = (time.time() + ttl_seconds) if ttl_seconds else None
        grant = ApprovalTokenGrant(
            token=token,
            scope=scope,
            approving_actor=approving_actor,
            approved_executor=approved_executor,
            status=ApprovalTokenStatus.GRANTED,
            expires_at=expires_at,
            max_uses=max_uses,
        )
        self._tokens[token] = grant
        return grant

    def verify(self, token: str, scope: ApprovalScope,
               executor: str = "ai") -> tuple[bool, str]:
        """校验令牌是否有效。"""
        grant = self._tokens.get(token)
        if not grant:
            return False, "令牌不存在"
        if grant.status != ApprovalTokenStatus.GRANTED:
            return False, f"令牌状态错误: {grant.status.value}"
        if grant.approved_executor != executor:
            return False, f"令牌执行者不匹配: {grant.approved_executor} != {executor}"
        if grant.expires_at and time.time() > grant.expires_at:
            grant.status = ApprovalTokenStatus.EXPIRED
            return False, "令牌已过期"
        if grant.uses >= grant.max_uses:
            grant.status = ApprovalTokenStatus.CONSUMED
            return False, "令牌已用完"
        # 作用域匹配：action 包含
        if scope.action and grant.scope.action and scope.action not in grant.scope.action:
            return False, f"令牌作用域不匹配: {scope.action} not in {grant.scope.action}"
        return True, "ok"

    def consume(self, token: str) -> tuple[bool, str]:
        """消费令牌（使用一次）。"""
        grant = self._tokens.get(token)
        if not grant:
            return False, "令牌不存在"
        if grant.status != ApprovalTokenStatus.GRANTED:
            return False, f"令牌状态错误: {grant.status.value}"
        if grant.expires_at and time.time() > grant.expires_at:
            grant.status = ApprovalTokenStatus.EXPIRED
            return False, "令牌已过期"
        grant.uses += 1
        if grant.uses >= grant.max_uses:
            grant.status = ApprovalTokenStatus.CONSUMED
        return True, "ok"

    def revoke(self, token: str) -> bool:
        """撤销令牌。"""
        grant = self._tokens.get(token)
        if not grant:
            return False
        grant.status = ApprovalTokenStatus.REVOKED
        return True

    def audit(self, token: str) -> Optional[ApprovalTokenAudit]:
        """获取审批审计记录。"""
        grant = self._tokens.get(token)
        if not grant:
            return None
        return ApprovalTokenAudit(
            token=grant.token,
            status=grant.status,
            scope=grant.scope,
            approving_actor=grant.approving_actor,
            approved_executor=grant.approved_executor,
            uses=grant.uses,
            max_uses=grant.max_uses,
            delegation_chain=grant.delegation_chain,
        )

    def add_delegation(self, token: str, actor: str, reason: str) -> bool:
        """添加授权链跳转。"""
        grant = self._tokens.get(token)
        if not grant:
            return False
        grant.delegation_chain.append(ApprovalDelegationHop(
            actor=actor, reason=reason, timestamp=time.time()
        ))
        return True
