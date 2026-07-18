"""Dry-run execution provider (no cluster mutations)."""

from __future__ import annotations

from datetime import UTC

from incident_agent.contracts import ExecutionResult, FixPlan, IncidentState


class DryRunExecutionProvider:
    """
    Simulates execution without touching Kubernetes.

    Later: KubectlExecutionProvider will run real remediations post-approval.
    """

    def execute(self, plan: FixPlan, *, state: IncidentState) -> ExecutionResult:
        # Use incident created_at for deterministic timestamps (parity-friendly).
        stamp = (
            state.created_at
            if state.created_at.tzinfo
            else state.created_at.replace(tzinfo=UTC)
        )
        action_kinds = [a.action_type.value for a in plan.actions]
        return ExecutionResult(
            executed=False,
            success=True,
            summary=f"Dry-run OK for {len(plan.actions)} action(s): {action_kinds}",
            details={
                "provider": "dry_run",
                "incident_id": state.incident_id,
                "hypothesis_id": plan.hypothesis_id,
                "risk": plan.risk.value if hasattr(plan.risk, "value") else str(plan.risk),
                "actions": action_kinds,
            },
            started_at=stamp,
            finished_at=stamp,
        )
