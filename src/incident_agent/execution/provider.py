"""KubectlExecutionProvider: allowlisted actions + policy gate + structured API client."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from incident_agent.calibration.models import CalibratedAssessment
from incident_agent.contracts import ExecutionResult, FixPlan, IncidentState
from incident_agent.execution.actions import (
    ALLOWLISTED_ACTION_TYPES,
    ActionContext,
    ActionRegistry,
    ActionRequest,
    ActionResult,
    ActionStatus,
    UnknownActionError,
)
from incident_agent.execution.kubectl_client import BaseKubectlClient, FakeKubectlClient
from incident_agent.execution.policy import ExecutionDecision, ExecutionPolicy

# Map Stage-0 FixActionType / remediation action strings → allowlisted types.
_FIX_ACTION_MAP: dict[str, str] = {
    "restart_pod": "restart_pod",
    "scale_deployment": "scale_deployment",
    "rollout_restart": "rollout_restart",
    "update_resource_limit": "update_resource_limit",
    "increase_memory_limit": "update_resource_limit",
    "patch_resource": "update_resource_limit",
    "rollback_deployment": "rollout_restart",
}


class KubectlExecutionProvider:
    """
    ExecutionProvider that mutates Kubernetes only through allowlisted handlers.

    Stage-0 defaults remain DryRunExecutionProvider. Opt in via bundle factory.
    """

    def __init__(
        self,
        client: BaseKubectlClient | None = None,
        *,
        registry: ActionRegistry | None = None,
        policy: ExecutionPolicy | None = None,
        default_dry_run: bool = True,
    ) -> None:
        self._client = client or FakeKubectlClient()
        self._registry = registry or ActionRegistry.default(self._client)
        self._policy = policy or ExecutionPolicy()
        self._default_dry_run = default_dry_run
        self.approval_queue: list[dict[str, Any]] = []

    def execute_action(
        self,
        request: ActionRequest,
        *,
        calibrated_assessment: CalibratedAssessment,
    ) -> ActionResult:
        """
        Registry lookup → policy → validate → execute (or BLOCKED_BY_POLICY).
        """
        try:
            handler = self._registry.get(request.type)
        except UnknownActionError as exc:
            raise UnknownActionError(str(exc)) from exc

        context = ActionContext(
            target=request.target,
            namespace=request.namespace,
            parameters=dict(request.parameters),
        )

        # Parameter / resource validation before policy (fail closed early).
        if not handler.validate(context):
            return ActionResult(
                status=ActionStatus.VALIDATION_FAILED,
                success=False,
                action_type=request.type,
                target=request.target,
                namespace=request.namespace,
                reason=f"action validation failed for {request.type}",
                dry_run=request.dry_run,
                requires_human_approval=True,
            )

        decision = self._policy.evaluate(
            calibrated_assessment=calibrated_assessment,
            action_handler=handler,
            action_context=context,
        )
        if not decision.allowed:
            self._enqueue_approval(request, decision, calibrated_assessment)
            return ActionResult(
                status=ActionStatus.BLOCKED_BY_POLICY,
                success=False,
                action_type=request.type,
                target=request.target,
                namespace=request.namespace,
                reason=decision.reason,
                dry_run=request.dry_run,
                requires_human_approval=decision.requires_human_approval,
                details={
                    "policy": decision.model_dump(),
                    "calibrated_confidence": calibrated_assessment.calibrated_confidence,
                },
            )

        return handler.execute(context, dry_run=request.dry_run)

    def execute(self, plan: FixPlan, *, state: IncidentState) -> ExecutionResult:
        """ExecutionProvider port: FixPlan → allowlisted ActionRequest → ExecutionResult."""
        stamp = datetime.now(tz=UTC)
        request = self._request_from_plan(plan, state)
        assessment = self._assessment_from_state(state)

        try:
            action_result = self.execute_action(
                request, calibrated_assessment=assessment
            )
        except UnknownActionError as exc:
            return ExecutionResult(
                executed=False,
                success=False,
                status="failed",
                action=request.type,
                applied_changes=[],
                summary=str(exc),
                details={
                    "provider": "kubectl",
                    "error": "unknown_action",
                    "allowlisted": sorted(ALLOWLISTED_ACTION_TYPES),
                },
                started_at=stamp,
                finished_at=datetime.now(tz=UTC),
            )

        return self._to_execution_result(action_result, plan=plan, started_at=stamp)

    def _request_from_plan(self, plan: FixPlan, state: IncidentState) -> ActionRequest:
        action_name = ""
        if state.execution_plan and state.execution_plan.action:
            action_name = state.execution_plan.action
        elif plan.actions:
            params = plan.actions[0].params or {}
            if isinstance(params.get("action"), str):
                action_name = str(params["action"])
            else:
                action_name = plan.actions[0].action_type.value

        mapped = _FIX_ACTION_MAP.get(action_name, action_name)
        target = (
            state.execution_plan.target
            if state.execution_plan
            else (plan.actions[0].target if plan.actions else "<workload>")
        )
        namespace = "default"
        if state.resource and state.resource.namespace:
            namespace = state.resource.namespace
        elif isinstance(state.observations.extra.get("namespace"), str):
            namespace = str(state.observations.extra["namespace"])

        parameters: dict[str, Any] = {}
        if plan.actions:
            parameters.update(plan.actions[0].params or {})
        # Normalize memory limit keys used by remediation catalog.
        if mapped == "update_resource_limit":
            parameters.setdefault("container_name", parameters.get("container", "app"))
            if "memory_limit" not in parameters and "limit" in parameters:
                parameters["memory_limit"] = parameters["limit"]
            parameters.setdefault("memory_limit", "512Mi")
        if mapped == "scale_deployment":
            if "desired_replicas" not in parameters:
                parameters["desired_replicas"] = int(parameters.get("replicas", 2))

        dry_run = bool(state.observations.extra.get("kubectl_dry_run", self._default_dry_run))
        # Strip deployment/ prefix for pod restart targets when needed.
        clean_target = target
        if mapped == "restart_pod" and target.startswith("pod/"):
            clean_target = target.split("/", 1)[1]
        elif mapped != "restart_pod" and target.startswith("deployment/"):
            clean_target = target  # handlers accept deployment/name

        return ActionRequest(
            type=mapped,
            target=clean_target,
            namespace=namespace,
            parameters=parameters,
            dry_run=dry_run,
        )

    def _assessment_from_state(self, state: IncidentState) -> CalibratedAssessment:
        extra = state.observations.extra
        raw_cal = extra.get("calibrated_assessment")
        if isinstance(raw_cal, CalibratedAssessment):
            return raw_cal
        if isinstance(raw_cal, dict):
            return CalibratedAssessment.model_validate(raw_cal)

        score = 0.0
        if state.confidence_score is not None:
            score = float(state.confidence_score)
        elif state.confidence is not None:
            score = float(state.confidence.score)

        # Without an explicit calibrated assessment, treat score as calibrated
        # and honor execution_gated if present.
        gated = bool(extra.get("execution_gated", False))
        if score < float(extra.get("safety_threshold", 0.80)):
            gated = True
        return CalibratedAssessment(
            raw_confidence=score,
            calibrated_confidence=score,
            execution_gated=gated,
            risk_explanation="derived from IncidentState.confidence_score",
            safety_threshold=float(extra.get("safety_threshold", 0.80)),
        )

    def _enqueue_approval(
        self,
        request: ActionRequest,
        decision: ExecutionDecision,
        assessment: CalibratedAssessment,
    ) -> None:
        self.approval_queue.append(
            {
                "request": request.model_dump(),
                "decision": decision.model_dump(),
                "calibrated_confidence": assessment.calibrated_confidence,
                "queued_at": datetime.now(tz=UTC).isoformat(),
            }
        )

    @staticmethod
    def _to_execution_result(
        action_result: ActionResult,
        *,
        plan: FixPlan,
        started_at: datetime,
    ) -> ExecutionResult:
        if action_result.status == ActionStatus.BLOCKED_BY_POLICY:
            status = "skipped"
            executed = False
            success = False
        elif action_result.status == ActionStatus.DRY_RUN:
            status = "dry_run_success"
            executed = False
            success = True
        elif action_result.success:
            status = "success"
            executed = not action_result.dry_run
            success = True
        else:
            status = "failed"
            executed = False
            success = False

        return ExecutionResult(
            executed=executed,
            success=success,
            status=status,  # type: ignore[arg-type]
            action=action_result.action_type,
            applied_changes=list(action_result.applied_changes),
            summary=action_result.reason or action_result.status,
            details={
                "provider": "kubectl",
                "action_status": action_result.status,
                "namespace": action_result.namespace,
                "target": action_result.target,
                "dry_run": action_result.dry_run,
                "requires_human_approval": action_result.requires_human_approval,
                "hypothesis_id": plan.hypothesis_id,
                "remediation_option_id": plan.remediation_option_id,
                **action_result.details,
            },
            started_at=started_at,
            finished_at=datetime.now(tz=UTC),
        )
