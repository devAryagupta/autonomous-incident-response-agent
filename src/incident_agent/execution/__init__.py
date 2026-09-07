"""Execution lifecycle: plan → pre-checks → approve → act → verify outcome."""

from incident_agent.execution.actions import (
    ALLOWLISTED_ACTION_TYPES,
    ActionRegistry,
    ActionRequest,
    ActionResult,
    ActionStatus,
    RiskLevel,
    UnknownActionError,
)
from incident_agent.execution.kubectl_client import (
    BaseKubectlClient,
    FakeKubectlClient,
    LiveKubectlClient,
    live_kubectl_client,
)
from incident_agent.execution.plan import build_execution_plan, decision_action
from incident_agent.execution.policy import ExecutionDecision, ExecutionPolicy
from incident_agent.execution.preconditions import (
    check_preconditions,
    execution_namespace,
    parse_execution_target,
    target_probe_ref,
)
from incident_agent.execution.provider import KubectlExecutionProvider

__all__ = [
    "ALLOWLISTED_ACTION_TYPES",
    "ActionRegistry",
    "ActionRequest",
    "ActionResult",
    "ActionStatus",
    "BaseKubectlClient",
    "ExecutionDecision",
    "ExecutionPolicy",
    "FakeKubectlClient",
    "KubectlExecutionProvider",
    "LiveKubectlClient",
    "RiskLevel",
    "UnknownActionError",
    "build_execution_plan",
    "check_preconditions",
    "decision_action",
    "execution_namespace",
    "live_kubectl_client",
    "parse_execution_target",
    "target_probe_ref",
]
