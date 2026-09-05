"""Allowlisted action registry (Command Pattern)."""

from __future__ import annotations

from incident_agent.execution.actions.base import (
    AbstractActionHandler,
    ActionContext,
    ActionRequest,
    ActionResult,
    ActionStatus,
    RiskLevel,
)
from incident_agent.execution.actions.restart_pod import RestartPodAction
from incident_agent.execution.actions.rollback_deployment import RollbackDeploymentAction
from incident_agent.execution.actions.rollout_restart import RolloutRestartAction
from incident_agent.execution.actions.scale_deployment import ScaleDeploymentAction
from incident_agent.execution.actions.update_resource_limit import UpdateResourceLimitAction
from incident_agent.execution.kubectl_client import BaseKubectlClient

ALLOWLISTED_ACTION_TYPES: frozenset[str] = frozenset(
    {
        RestartPodAction.action_type,
        RollbackDeploymentAction.action_type,
        RolloutRestartAction.action_type,
        ScaleDeploymentAction.action_type,
        UpdateResourceLimitAction.action_type,
        # Same-operation identities handled by UpdateResourceLimitAction.
        "increase_memory_limit",
        "patch_resource",
    }
)


class UnknownActionError(ValueError):
    """Raised when an action type is not in the allowlist registry."""


class ActionRegistry:
    """Maps allowlisted action type → handler instance."""

    def __init__(self, handlers: dict[str, AbstractActionHandler]) -> None:
        unknown = set(handlers) - ALLOWLISTED_ACTION_TYPES
        if unknown:
            raise UnknownActionError(
                f"cannot register non-allowlisted action types: {sorted(unknown)}"
            )
        self._handlers = dict(handlers)

    @classmethod
    def default(cls, client: BaseKubectlClient) -> ActionRegistry:
        limits = UpdateResourceLimitAction(client)
        return cls(
            {
                RestartPodAction.action_type: RestartPodAction(client),
                RollbackDeploymentAction.action_type: RollbackDeploymentAction(client),
                RolloutRestartAction.action_type: RolloutRestartAction(client),
                ScaleDeploymentAction.action_type: ScaleDeploymentAction(client),
                UpdateResourceLimitAction.action_type: limits,
                "increase_memory_limit": limits,
                "patch_resource": limits,
            }
        )

    def get(self, action_type: str) -> AbstractActionHandler:
        if action_type not in ALLOWLISTED_ACTION_TYPES:
            raise UnknownActionError(
                f"action type {action_type!r} is not allowlisted; "
                f"allowed={sorted(ALLOWLISTED_ACTION_TYPES)}"
            )
        handler = self._handlers.get(action_type)
        if handler is None:
            raise UnknownActionError(f"no handler registered for {action_type!r}")
        return handler

    def register(self, handler: AbstractActionHandler) -> None:
        if handler.action_type not in ALLOWLISTED_ACTION_TYPES:
            raise UnknownActionError(
                f"cannot register non-allowlisted action type {handler.action_type!r}"
            )
        self._handlers[handler.action_type] = handler


__all__ = [
    "ALLOWLISTED_ACTION_TYPES",
    "AbstractActionHandler",
    "ActionContext",
    "ActionRegistry",
    "ActionRequest",
    "ActionResult",
    "ActionStatus",
    "RestartPodAction",
    "RiskLevel",
    "RollbackDeploymentAction",
    "RolloutRestartAction",
    "ScaleDeploymentAction",
    "UnknownActionError",
    "UpdateResourceLimitAction",
]
