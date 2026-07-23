"""Level 3 (MEDIUM): scale Deployment replicas within safe bounds."""

from __future__ import annotations

from incident_agent.execution.actions.base import (
    AbstractActionHandler,
    ActionContext,
    ActionResult,
    ActionStatus,
    RiskLevel,
)
from incident_agent.execution.kubectl_client import BaseKubectlClient

DEFAULT_MAX_REPLICAS = 20


def _deployment_name(target: str) -> str:
    if "/" in target:
        kind, name = target.split("/", 1)
        if kind.lower() in {"deployment", "deploy"}:
            return name
    return target


class ScaleDeploymentAction(AbstractActionHandler):
    action_type = "scale_deployment"
    risk_level = RiskLevel.MEDIUM

    def __init__(
        self,
        client: BaseKubectlClient,
        *,
        max_allowed_replicas: int = DEFAULT_MAX_REPLICAS,
    ) -> None:
        self._client = client
        self._max_allowed_replicas = max_allowed_replicas

    def validate(self, context: ActionContext) -> bool:
        name = _deployment_name(context.target)
        if not name or not context.namespace:
            return False
        if not self._client.resource_exists("deployment", context.namespace, name):
            return False
        replicas = context.parameters.get("desired_replicas")
        if not isinstance(replicas, int):
            return False
        return 0 < replicas <= self._max_allowed_replicas

    def execute(self, context: ActionContext, *, dry_run: bool = False) -> ActionResult:
        name = _deployment_name(context.target)
        if not self.validate(context):
            return ActionResult(
                status=ActionStatus.VALIDATION_FAILED,
                success=False,
                action_type=self.action_type,
                target=name,
                namespace=context.namespace,
                reason=(
                    "scale_deployment validation failed "
                    f"(require 0 < desired_replicas <= {self._max_allowed_replicas})"
                ),
                dry_run=dry_run,
                details={
                    "desired_replicas": context.parameters.get("desired_replicas"),
                    "max_allowed_replicas": self._max_allowed_replicas,
                },
            )
        replicas = int(context.parameters["desired_replicas"])
        previous = self._client.get_deployment_replicas(context.namespace, name)
        result = self._client.scale_deployment(
            context.namespace, name, replicas, dry_run=dry_run
        )
        # Stash previous for rollback.
        context.parameters.setdefault("_previous_replicas", previous)
        return ActionResult(
            status=ActionStatus.DRY_RUN if dry_run else ActionStatus.SUCCESS,
            success=True,
            action_type=self.action_type,
            target=name,
            namespace=context.namespace,
            reason=f"scaled to {replicas}" if not dry_run else "dry-run",
            dry_run=dry_run,
            applied_changes=[]
            if dry_run
            else [f"scaled deployment/{name} to {replicas} in {context.namespace}"],
            details=result,
        )

    def rollback(self, context: ActionContext) -> ActionResult:
        name = _deployment_name(context.target)
        previous = context.parameters.get("_previous_replicas")
        if previous is None:
            previous = context.parameters.get("previous_replicas")
        if not isinstance(previous, int) or previous < 1:
            return ActionResult(
                status=ActionStatus.ROLLBACK_FAILED,
                success=False,
                action_type=self.action_type,
                target=name,
                namespace=context.namespace,
                reason="no previous replica count available for rollback",
            )
        result = self._client.scale_deployment(context.namespace, name, previous)
        return ActionResult(
            status=ActionStatus.ROLLBACK_SUCCESS,
            success=True,
            action_type=self.action_type,
            target=name,
            namespace=context.namespace,
            reason=f"scaled back to {previous}",
            applied_changes=[f"scaled deployment/{name} back to {previous}"],
            details=result,
        )
