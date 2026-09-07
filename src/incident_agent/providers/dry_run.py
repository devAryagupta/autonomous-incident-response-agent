"""Dry-run execution provider (no cluster mutations)."""

from __future__ import annotations

from datetime import UTC

from incident_agent.contracts import ExecutionResult, FixPlan, IncidentState
from incident_agent.execution.plan import decision_action


def _action_name(plan: FixPlan, state: IncidentState) -> str:
    return decision_action(state, plan=plan)


def _applied_changes(plan: FixPlan, *, action: str, target: str) -> list[str]:
    changes: list[str] = []
    for a in plan.actions:
        params = a.params or {}
        change = params.get("change")
        if isinstance(change, str) and change:
            changes.append(f"{change} on {a.target}")
        else:
            changes.append(f"{a.action_type.value} applied to {a.target} (simulated)")
    if action == "increase_memory_limit":
        changes.append(f"memory limit updated for {target} (simulated dry-run)")
    elif action == "rollback_deployment":
        changes.append(f"deployment rolled back for {target} (simulated dry-run)")
    elif action == "rollout_restart":
        changes.append(f"deployment rollout restarted for {target} (simulated dry-run)")
    elif action == "restart_pod":
        changes.append(f"pod deleted/recreated for {target} (simulated dry-run)")
    elif action == "create_or_fix_secret":
        changes.append(f"secret created/fixed for {target} (simulated dry-run)")
    elif action == "patch_image_tag":
        changes.append(f"image tag patched for {target} (simulated dry-run)")
    if not changes:
        changes.append(f"{action} simulated for {target}")
    # Dedup preserve order
    out: list[str] = []
    seen: set[str] = set()
    for item in changes:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


class DryRunExecutionProvider:
    """
    Simulates execution without touching Kubernetes.

    Returns a structured ExecutionResult (status/action/applied_changes).
    Live path: KubectlExecutionProvider (allowlisted actions + ExecutionPolicy).
    """

    def execute(self, plan: FixPlan, *, state: IncidentState) -> ExecutionResult:
        stamp = (
            state.created_at
            if state.created_at.tzinfo
            else state.created_at.replace(tzinfo=UTC)
        )
        action = _action_name(plan, state)
        target = (
            state.execution_plan.target
            if state.execution_plan
            else (plan.actions[0].target if plan.actions else "<workload>")
        )
        applied = _applied_changes(plan, action=action, target=target)
        action_kinds = [a.action_type.value for a in plan.actions]

        if action in {"noop", "noop_investigate"} or not plan.actions:
            return ExecutionResult(
                executed=False,
                success=False,
                status="skipped",
                action=action,
                applied_changes=[],
                summary=f"Dry-run skipped: no actionable change for {target}",
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

        return ExecutionResult(
            executed=False,  # dry-run: not a live cluster mutation
            success=True,
            status="dry_run_success",
            action=action,
            applied_changes=applied,
            summary=(
                f"Dry-run success: action={action} target={target} "
                f"changes={len(applied)}"
            ),
            details={
                "provider": "dry_run",
                "incident_id": state.incident_id,
                "hypothesis_id": plan.hypothesis_id,
                "remediation_option_id": plan.remediation_option_id,
                "risk": plan.risk.value if hasattr(plan.risk, "value") else str(plan.risk),
                "actions": action_kinds,
                "target": target,
                "status": "success",
            },
            started_at=stamp,
            finished_at=stamp,
        )

    def resource_exists(self, kind: str, namespace: str, name: str) -> bool:
        """No cluster: a concrete name is treated as present."""
        _ = kind, namespace
        return bool(name) and name != "<workload>"
