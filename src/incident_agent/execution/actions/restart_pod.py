"""Level 1 (LOW): delete pod → ReplicaSet recreates it."""

from __future__ import annotations

from incident_agent.execution.actions.base import (
    AbstractActionHandler,
    ActionContext,
    ActionResult,
    ActionStatus,
    RiskLevel,
)
from incident_agent.execution.kubectl_client import BaseKubectlClient


class RestartPodAction(AbstractActionHandler):
    action_type = "restart_pod"
    risk_level = RiskLevel.LOW

    def __init__(self, client: BaseKubectlClient) -> None:
        self._client = client

    def validate(self, context: ActionContext) -> bool:
        if not context.target or not context.namespace:
            return False
        # Pod may already be gone; allow if deployment-owned name pattern exists or pod exists.
        return self._client.resource_exists("pod", context.namespace, context.target) or bool(
            context.target
        )

    def execute(self, context: ActionContext, *, dry_run: bool = False) -> ActionResult:
        if not self.validate(context):
            return ActionResult(
                status=ActionStatus.VALIDATION_FAILED,
                success=False,
                action_type=self.action_type,
                target=context.target,
                namespace=context.namespace,
                reason="restart_pod validation failed",
                dry_run=dry_run,
            )
        result = self._client.delete_pod(
            context.namespace, context.target, dry_run=dry_run
        )
        return ActionResult(
            status=ActionStatus.DRY_RUN if dry_run else ActionStatus.SUCCESS,
            success=True,
            action_type=self.action_type,
            target=context.target,
            namespace=context.namespace,
            reason="pod delete issued (ReplicaSet will recreate)" if not dry_run else "dry-run",
            dry_run=dry_run,
            applied_changes=[]
            if dry_run
            else [f"deleted pod/{context.target} in {context.namespace}"],
            details=result,
        )

    def rollback(self, context: ActionContext) -> ActionResult:
        # Pod restart is self-healing via controllers; no explicit undo.
        return ActionResult(
            status=ActionStatus.ROLLBACK_SUCCESS,
            success=True,
            action_type=self.action_type,
            target=context.target,
            namespace=context.namespace,
            reason="rollback automatic via Kubernetes pod lifecycle",
            details={"rollback": "noop_controller_recreate"},
        )
