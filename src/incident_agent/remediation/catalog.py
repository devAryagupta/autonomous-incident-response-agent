from __future__ import annotations

from dataclasses import dataclass

from incident_agent.contracts import FixAction, FixPlan, Hypothesis


@dataclass(frozen=True, slots=True)
class CatalogMatch:
    matched_hypothesis_id: str | None
    matched_rule: str | None


def _plan_missing_secret(*, hypothesis_id: str, target_ref: str) -> FixPlan:
    return FixPlan(
        hypothesis_id=hypothesis_id,
        risk="low",
        actions=[
            FixAction(
                kind="patch_resource",
                target_ref=target_ref,
                params={
                    "change": (
                        "Ensure referenced Secret exists and the workload references it correctly"
                    ),
                    "commands": [
                        "kubectl -n <ns> get secret <secret-name>",
                        "kubectl -n <ns> describe pod <pod>",
                        "kubectl -n <ns> apply -f <secret>.yaml",
                    ],
                },
                rationale="Pods crash when required Secrets are missing or misreferenced.",
            )
        ],
        notes="Validate secret name/namespace and volume/envFrom references.",
    )


def _plan_missing_env_var(*, hypothesis_id: str, target_ref: str) -> FixPlan:
    return FixPlan(
        hypothesis_id=hypothesis_id,
        risk="low",
        actions=[
            FixAction(
                kind="patch_resource",
                target_ref=target_ref,
                params={
                    "change": "Add missing environment variable(s) to workload spec",
                    "commands": [
                        "kubectl -n <ns> describe pod <pod> | findstr -i env",
                        "kubectl -n <ns> set env <workload> KEY=value",
                    ],
                },
                rationale="Missing required env vars can cause immediate process exit at startup.",
            )
        ],
        notes=(
            "Confirm the variable name from logs and ensure correct value source "
            "(Secret/ConfigMap)."
        ),
    )


def _plan_missing_configmap(*, hypothesis_id: str, target_ref: str) -> FixPlan:
    return FixPlan(
        hypothesis_id=hypothesis_id,
        risk="low",
        actions=[
            FixAction(
                kind="patch_resource",
                target_ref=target_ref,
                params={
                    "change": "Create referenced ConfigMap or fix its name/namespace",
                    "commands": [
                        "kubectl -n <ns> get configmap <configmap-name>",
                        "kubectl -n <ns> apply -f <configmap>.yaml",
                    ],
                },
                rationale=(
                    "Missing ConfigMaps can break volume mounts and app configuration at startup."
                ),
            )
        ],
        notes="Check volume mounts and envFrom references for correct ConfigMap name.",
    )


def _plan_dependency_unavailable(*, hypothesis_id: str, target_ref: str) -> FixPlan:
    return FixPlan(
        hypothesis_id=hypothesis_id,
        risk="medium",
        actions=[
            FixAction(
                kind="patch_resource",
                target_ref=target_ref,
                params={
                    "change": (
                        "Restore dependency connectivity (DNS/service/endpoints/network-policy)"
                    ),
                    "commands": [
                        "kubectl -n <ns> get svc,endpoints",
                        "kubectl -n <ns> get networkpolicy",
                        "kubectl -n <ns> describe pod <pod>",
                    ],
                },
                rationale="Startup failures can occur when upstream services are unreachable.",
            )
        ],
        notes="Prefer fixing dependency/service rather than restarting the crashing workload.",
    )


def _plan_oomkilled(*, hypothesis_id: str, target_ref: str) -> FixPlan:
    return FixPlan(
        hypothesis_id=hypothesis_id,
        risk="medium",
        actions=[
            FixAction(
                kind="patch_resource",
                target_ref=target_ref,
                params={
                    "change": "Increase memory requests/limits or reduce startup footprint",
                    "commands": [
                        "kubectl -n <ns> describe pod <pod> | findstr -i OOMKilled",
                        (
                            "kubectl -n <ns> set resources <workload> "
                            "--limits=memory=512Mi --requests=memory=256Mi"
                        ),
                    ],
                },
                rationale=(
                    "OOMKilled during startup indicates memory limits too low for initialization."
                ),
            )
        ],
        notes=(
            "If memory spike is temporary, tune startup behavior; otherwise increase limits safely."
        ),
    )


def _plan_app_bug(*, hypothesis_id: str, target_ref: str) -> FixPlan:
    return FixPlan(
        hypothesis_id=hypothesis_id,
        risk="high",
        actions=[
            FixAction(
                kind="rollback",
                target_ref=target_ref,
                params={
                    "change": "Rollback to last known good revision/image",
                    "commands": [
                        "kubectl -n <ns> rollout history <workload>",
                        "kubectl -n <ns> rollout undo <workload>",
                    ],
                },
                rationale="Unhandled exceptions/panics usually require code or image rollback.",
            )
        ],
        notes="Prefer rollback when the crash is deterministic and recent deploy introduced it.",
    )


_RULES: list[tuple[str, str, callable]] = [
    ("Missing Secret", "missing_secret.v1", _plan_missing_secret),
    ("Missing Environment Variable", "missing_env_var.v1", _plan_missing_env_var),
    ("Missing ConfigMap", "missing_configmap.v1", _plan_missing_configmap),
    (
        "Dependency Unavailable (DNS/Network)",
        "dependency_unavailable.v1",
        _plan_dependency_unavailable,
    ),
    ("Resource Constraint (OOMKilled)", "resource_constraint_oom.v1", _plan_oomkilled),
    ("Application Bug / Unhandled Exception", "app_bug_unhandled_exception.v1", _plan_app_bug),
]


def plan_from_hypotheses(
    *,
    hypotheses: list[Hypothesis],
    target_ref: str = "<workload>",
) -> tuple[FixPlan, CatalogMatch]:
    """
    Deterministic rule-based planner.

    Strategy (baseline):
    - pick the highest-likelihood hypothesis that has a catalog rule
    - emit a single FixPlan with one or more FixActions (still no execution)
    """
    for h in sorted(hypotheses, key=lambda x: x.likelihood, reverse=True):
        for cause, rule_id, fn in _RULES:
            if h.description == cause:
                return fn(hypothesis_id=h.hypothesis_id, target_ref=target_ref), CatalogMatch(
                    matched_hypothesis_id=h.hypothesis_id,
                    matched_rule=rule_id,
                )

    # Fallback: no match => noop plan (still deterministic)
    return (
        FixPlan(
            hypothesis_id=hypotheses[0].hypothesis_id if hypotheses else None,
            risk="low",
            actions=[
                FixAction(
                    kind="noop",
                    target_ref=target_ref,
                    params={},
                    rationale="No catalog rule matched; require human investigation.",
                )
            ],
            notes="Extend remediation catalog for this hypothesis.",
        ),
        CatalogMatch(matched_hypothesis_id=None, matched_rule=None),
    )

