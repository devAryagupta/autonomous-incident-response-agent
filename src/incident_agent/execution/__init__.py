"""Execution lifecycle: plan → pre-checks → approve → act → verify outcome."""

from incident_agent.execution.outcome import verify_execution_outcome
from incident_agent.execution.plan import build_execution_plan
from incident_agent.execution.preconditions import check_preconditions

__all__ = [
    "build_execution_plan",
    "check_preconditions",
    "verify_execution_outcome",
]
