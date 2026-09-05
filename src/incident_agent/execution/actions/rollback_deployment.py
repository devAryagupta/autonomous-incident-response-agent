"""MEDIUM: restore the previous Deployment revision (`kubectl rollout undo`)."""

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


class RollbackDeploymentAction(AbstractActionHandler):
    action_type = "rollback_deployment"
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
                reason="rollback_deployment validation failed (deployment missing)",
                dry_run=dry_run,
            )
        result = self._client.rollout_undo_deployment(
            context.namespace, name, dry_run=dry_run
        )
        return ActionResult(
            status=ActionStatus.DRY_RUN if dry_run else ActionStatus.SUCCESS,
            success=True,
            action_type=self.action_type,
            target=name,
            namespace=context.namespace,
            reason="deployment revision rolled back" if not dry_run else "dry-run",
            dry_run=dry_run,
            applied_changes=[]
            if dry_run
            else [f"rollout undo deployment/{name} in {context.namespace}"],
            details=result,
        )

    def rollback(self, context: ActionContext) -> ActionResult:
        name = _deployment_name(context.target)
        return ActionResult(
            status=ActionStatus.ROLLBACK_FAILED,
            success=False,
            action_type=self.action_type,
            target=name,
            namespace=context.namespace,
            reason="roll-forward after undo requires an explicit revision",
        )
