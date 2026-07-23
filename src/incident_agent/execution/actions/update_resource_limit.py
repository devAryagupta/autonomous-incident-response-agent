"""Level 4 (HIGH): patch Deployment container memory limits."""

from __future__ import annotations

import re

from incident_agent.execution.actions.base import (
    AbstractActionHandler,
    ActionContext,
    ActionResult,
    ActionStatus,
    RiskLevel,
)
from incident_agent.execution.kubectl_client import BaseKubectlClient

_MEMORY_RE = re.compile(r"^[1-9]\d*(Ei|Pi|Ti|Gi|Mi|Ki|E|P|T|G|M|K)?$")
# Soft quota ceiling for Stage-1 autonomous mutations (bytes-ish ordinal).
_MAX_MEMORY_MI = 8192  # 8Gi


def _deployment_name(target: str) -> str:
    if "/" in target:
        kind, name = target.split("/", 1)
        if kind.lower() in {"deployment", "deploy"}:
            return name
    return target


def _memory_to_mi(value: str) -> float | None:
    match = re.match(r"^([1-9]\d*)(Ei|Pi|Ti|Gi|Mi|Ki|E|P|T|G|M|K)?$", value)
    if match is None:
        return None
    amount = float(match.group(1))
    unit = match.group(2) or ""
    factors = {
        "Ki": 1 / 1024,
        "Mi": 1.0,
        "Gi": 1024.0,
        "Ti": 1024.0 ** 2,
        "Pi": 1024.0 ** 3,
        "Ei": 1024.0 ** 4,
        "K": 1000 / (1024**2),
        "M": 1000**2 / (1024**2),
        "G": 1000**3 / (1024**2),
        "T": 1000**4 / (1024**2),
        "P": 1000**5 / (1024**2),
        "E": 1000**6 / (1024**2),
        "": 1 / (1024**2),  # raw bytes → Mi
    }
    return amount * factors.get(unit, 1.0)


def is_valid_memory_quantity(value: str) -> bool:
    return bool(_MEMORY_RE.match(value.strip()))


class UpdateResourceLimitAction(AbstractActionHandler):
    action_type = "update_resource_limit"
    risk_level = RiskLevel.HIGH

    def __init__(
        self,
        client: BaseKubectlClient,
        *,
        max_memory_mi: int = _MAX_MEMORY_MI,
    ) -> None:
        self._client = client
        self._max_memory_mi = max_memory_mi

    def validate(self, context: ActionContext) -> bool:
        name = _deployment_name(context.target)
        container = context.parameters.get("container_name")
        memory = context.parameters.get("memory_limit")
        if not name or not context.namespace:
            return False
        if not isinstance(container, str) or not container:
            return False
        if not isinstance(memory, str) or not is_valid_memory_quantity(memory):
            return False
        mi = _memory_to_mi(memory)
        if mi is None or mi > self._max_memory_mi:
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
                reason=(
                    "update_resource_limit validation failed "
                    "(container_name / memory_limit syntax / quota)"
                ),
                dry_run=dry_run,
                details={
                    "container_name": context.parameters.get("container_name"),
                    "memory_limit": context.parameters.get("memory_limit"),
                    "max_memory_mi": self._max_memory_mi,
                },
            )
        container = str(context.parameters["container_name"])
        memory = str(context.parameters["memory_limit"])
        result = self._client.update_container_memory_limit(
            context.namespace,
            name,
            container_name=container,
            memory_limit=memory,
            dry_run=dry_run,
        )
        if result.get("previous_memory_limit"):
            context.parameters.setdefault(
                "_previous_memory_limit", result["previous_memory_limit"]
            )
        return ActionResult(
            status=ActionStatus.DRY_RUN if dry_run else ActionStatus.SUCCESS,
            success=True,
            action_type=self.action_type,
            target=name,
            namespace=context.namespace,
            reason=f"memory limit → {memory}" if not dry_run else "dry-run",
            dry_run=dry_run,
            applied_changes=[]
            if dry_run
            else [
                f"updated memory limit for {container} on deployment/{name} to {memory}"
            ],
            details=result,
        )

    def rollback(self, context: ActionContext) -> ActionResult:
        name = _deployment_name(context.target)
        previous = context.parameters.get("_previous_memory_limit") or context.parameters.get(
            "previous_memory_limit"
        )
        container = context.parameters.get("container_name")
        if not isinstance(previous, str) or not isinstance(container, str):
            return ActionResult(
                status=ActionStatus.ROLLBACK_FAILED,
                success=False,
                action_type=self.action_type,
                target=name,
                namespace=context.namespace,
                reason="no previous memory limit available for rollback",
            )
        result = self._client.update_container_memory_limit(
            context.namespace,
            name,
            container_name=container,
            memory_limit=previous,
        )
        return ActionResult(
            status=ActionStatus.ROLLBACK_SUCCESS,
            success=True,
            action_type=self.action_type,
            target=name,
            namespace=context.namespace,
            reason=f"memory limit restored to {previous}",
            applied_changes=[f"restored memory limit to {previous} on deployment/{name}"],
            details=result,
        )
