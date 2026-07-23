"""Level 2 (MEDIUM): patch Deployment restartedAt annotation → rolling restart."""

from __future__ import annotations

from incident_agent.execution.actions.base import (
    AbstractActionHandler,
    ActionContext,
    ActionResult,
    ActionStatus,
    RiskLevel,
)
from incident_agent.execution.kubectl_client import BaseKubectlClient


def _deployment_name(target: str) -> str:
    if "/" in target:
        kind, name = target.split("/", 1)
        if kind.lower() in {"deployment", "deploy"}:
            return name
    return target


class RolloutRestartAction(AbstractActionHandler):
    action_type = "rollout_restart"
    risk_level = RiskLevel.MEDIUM

    def __init__(self, client: BaseKubectlClient) -> None:
        self._client = client

    def validate(self, context: ActionContext) -> bool:
        name = _deployment_name(context.target)
        if not name or not context.namespace:
            return False
        return self._client.resource_exists("deployment", context.namespace, name)

    def execute(self, context: ActionContext, *, dry_run: bool = False) -> ActionResult:
        name = _deployment_name(context.target)
        if not self.validate(context):
            return ActionResult(
                status=ActionStatus.VALIDATION_FAILED,
                success=False,
                action_type=self.action_type,
                target=name,
                namespace=context.namespace,
                reason="rollout_restart validation failed (deployment missing)",
                dry_run=dry_run,
            )
        result = self._client.rollout_restart_deployment(
            context.namespace, name, dry_run=dry_run
        )
        return ActionResult(
            status=ActionStatus.DRY_RUN if dry_run else ActionStatus.SUCCESS,
            success=True,
            action_type=self.action_type,
            target=name,
            namespace=context.namespace,
            reason="deployment rollout restart patched" if not dry_run else "dry-run",
            dry_run=dry_run,
            applied_changes=[]
            if dry_run
            else [f"rollout restart deployment/{name} in {context.namespace}"],
            details=result,
        )

    def rollback(self, context: ActionContext) -> ActionResult:
        name = _deployment_name(context.target)
        try:
            result = self._client.rollout_undo_deployment(context.namespace, name)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(
                status=ActionStatus.ROLLBACK_FAILED,
                success=False,
                action_type=self.action_type,
                target=name,
                namespace=context.namespace,
                reason=f"rollout undo failed: {exc}",
            )
        return ActionResult(
            status=ActionStatus.ROLLBACK_SUCCESS,
            success=True,
            action_type=self.action_type,
            target=name,
            namespace=context.namespace,
            reason="deployment rollout undone",
            applied_changes=[f"rollout undo deployment/{name}"],
            details=result,
        )
