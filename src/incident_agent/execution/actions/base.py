"""Allowlisted action contract (Command Pattern).

Every mutation is an explicit handler with validate / execute / rollback.
No shell-string kubectl invocations — structured API via KubectlClient only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActionModelBase(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RiskLevel(IntEnum):
    """Action risk used by ExecutionPolicy thresholds (not contracts.RiskLevel)."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class ActionStatus(StrEnum):
    SUCCESS = "SUCCESS"
    DRY_RUN = "DRY_RUN"
    FAILED = "FAILED"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    ROLLBACK_SUCCESS = "ROLLBACK_SUCCESS"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


class ActionContext(ActionModelBase):
    """Typed inputs for one allowlisted mutation."""

    target: str
    namespace: str = "default"
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionResult(ActionModelBase):
    """Outcome of validate / execute / rollback for one action."""

    status: str
    success: bool
    action_type: str
    target: str
    namespace: str = "default"
    reason: str = ""
    dry_run: bool = False
    applied_changes: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    requires_human_approval: bool = False


class ActionRequest(ActionModelBase):
    """Inbound request to the execution provider (allowlisted type only)."""

    type: str
    target: str
    namespace: str = "default"
    parameters: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class AbstractActionHandler(ABC):
    """Command-pattern handler for one allowlisted mutation type."""

    action_type: str
    risk_level: RiskLevel

    @abstractmethod
    def validate(self, context: ActionContext) -> bool:
        """Validate namespace, resource shape, and parameter bounds."""
        ...

    @abstractmethod
    def execute(self, context: ActionContext, *, dry_run: bool = False) -> ActionResult:
        """Apply the mutation (or simulate when dry_run=True)."""
        ...

    @abstractmethod
    def rollback(self, context: ActionContext) -> ActionResult:
        """Revert the mutation when post-execution verification fails."""
        ...
