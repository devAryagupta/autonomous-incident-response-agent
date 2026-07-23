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
from incident_agent.execution.outcome import verify_execution_outcome
from incident_agent.execution.plan import build_execution_plan
from incident_agent.execution.policy import ExecutionDecision, ExecutionPolicy
from incident_agent.execution.preconditions import check_preconditions
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
    "live_kubectl_client",
    "verify_execution_outcome",
]
