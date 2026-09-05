"""Remediation candidate templates (decision layer — no execution)."""

from __future__ import annotations

from dataclasses import dataclass

from incident_agent.contracts import FixActionType, RiskLevel


@dataclass(frozen=True, slots=True)
class RemediationTemplate:
    """Catalog row for one cause → action pairing.

    ``base_confidence`` (alias ``base_effectiveness``) is the catalog weight
    for how well this action fits the cause — not P(success).
    Suitability = base_effectiveness × current hypothesis belief (posterior).
    """

    action: str
    expected_effect: str
    risk: RiskLevel
    blast_radius: str
    reversibility: str
    rollback_possible: bool
    # Catalog baseline effectiveness in [0, 1]. Not P(success).
    base_confidence: float
    action_type: FixActionType
    change: str
    commands: tuple[str, ...]
    rationale: str

    @property
    def base_effectiveness(self) -> float:
        """Catalog baseline effectiveness (same stored value as ``base_confidence``)."""
        return self.base_confidence


def _t(
    *,
    action: str,
    expected_effect: str,
    risk: RiskLevel,
    blast_radius: str,
    reversibility: str,
    rollback_possible: bool,
    base_confidence: float,
    action_type: FixActionType,
    change: str,
    commands: tuple[str, ...],
    rationale: str,
) -> RemediationTemplate:
    return RemediationTemplate(
        action=action,
        expected_effect=expected_effect,
        risk=risk,
        blast_radius=blast_radius,
        reversibility=reversibility,
        rollback_possible=rollback_possible,
        base_confidence=base_confidence,
        action_type=action_type,
        change=change,
        commands=commands,
        rationale=rationale,
    )


# Keyed by hypothesis.description (SRE cause) with remediation_key fallbacks.
# this is the list of the possible remediation options for a given cause.
_OPTIONS_BY_CAUSE: dict[str, tuple[RemediationTemplate, ...]] = {
    "Memory leak": (
        #_t is a helper function to create a RemediationTemplate object.
        _t(
            action="increase_memory_limit",
            expected_effect="Prevent OOM while investigating the leak",
            risk=RiskLevel.MEDIUM,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.85,
            action_type=FixActionType.PATCH_RESOURCE,
            change="Increase memory requests/limits for the workload",
            commands=(
                "kubectl -n <ns> set resources <workload> --limits=memory=512Mi --requests=memory=256Mi",
            ),
            rationale="Lowest blast-radius mitigation: scoped to one workload and reversible.",
        ),
        _t(
            action="rollback_deployment",
            expected_effect="Restore previous revision if leak was introduced by a deploy",
            risk=RiskLevel.MEDIUM,
            blast_radius="medium",
            reversibility="medium",
            rollback_possible=True,
            base_confidence=0.60,
            action_type=FixActionType.ROLLBACK_DEPLOYMENT,
            change="Rollback deployment to last known-good revision",
            commands=("kubectl -n <ns> rollout undo <workload>",),
            rationale="Broader blast radius than a resource patch; use when deploy regression is likely.",
        ),
        _t(
            action="restart_pod",
            expected_effect="Temporary relief only; does not fix memory growth",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.25,
            action_type=FixActionType.RESTART_POD,
            change="Restart the crashing pod",
            commands=("kubectl -n <ns> delete pod <pod>",),
            rationale="Low risk but low effectiveness for a leak.",
        ),
    ),
    "Memory limit too low": (
        _t(
            action="increase_memory_limit",
            expected_effect="Prevent OOM",
            risk=RiskLevel.MEDIUM,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.90,
            action_type=FixActionType.PATCH_RESOURCE,
            change="Increase memory requests/limits for the workload",
            commands=(
                "kubectl -n <ns> set resources <workload> --limits=memory=512Mi --requests=memory=256Mi",
            ),
            rationale="Direct fix for undersized limits with minimal blast radius.",
        ),
        _t(
            action="restart_pod",
            expected_effect="Retry startup; unlikely to fix chronic OOM",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.30,
            action_type=FixActionType.RESTART_POD,
            change="Restart the crashing pod",
            commands=("kubectl -n <ns> delete pod <pod>",),
            rationale="Safe but weak if the limit remains too low.",
        ),
    ),
    "Traffic spike": (
        _t(
            action="scale_deployment",
            expected_effect="Absorb load and reduce per-pod memory pressure",
            risk=RiskLevel.MEDIUM,
            blast_radius="medium",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.75,
            action_type=FixActionType.SCALE_DEPLOYMENT,
            change="Scale deployment replicas to handle traffic spike",
            commands=("kubectl -n <ns> scale <workload> --replicas=<n>",),
            rationale="Reversible scale-out; blast radius limited to the service.",
        ),
        _t(
            action="increase_memory_limit",
            expected_effect="Survive peak memory during the spike",
            risk=RiskLevel.MEDIUM,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.65,
            action_type=FixActionType.PATCH_RESOURCE,
            change="Temporarily raise memory limits during the spike",
            commands=(
                "kubectl -n <ns> set resources <workload> --limits=memory=512Mi --requests=memory=256Mi",
            ),
            rationale="Smaller blast radius than cluster-wide changes; may mask the spike.",
        ),
    ),
    "Secret not created": (
        _t(
            action="create_or_fix_secret",
            expected_effect="Restore required credentials so the pod can start",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.90,
            action_type=FixActionType.PATCH_CONFIG,
            change="Create missing Secret or fix the secret reference",
            commands=(
                "kubectl -n <ns> get secret <secret-name>",
                "kubectl -n <ns> apply -f <secret>.yaml",
            ),
            rationale="Config-scoped fix with low blast radius and high reversibility.",
        ),
        _t(
            action="restart_pod",
            expected_effect="Retry mount after secret is fixed",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.35,
            action_type=FixActionType.RESTART_POD,
            change="Restart pod after secret remediation",
            commands=("kubectl -n <ns> delete pod <pod>",),
            rationale="Follow-up only; ineffective alone if the secret is still missing.",
        ),
    ),
    "Wrong secret name or namespace": (
        _t(
            action="fix_secret_reference",
            expected_effect="Point workload at the correct secret",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.85,
            action_type=FixActionType.PATCH_CONFIG,
            change="Correct secret name/namespace reference on the workload",
            commands=("kubectl -n <ns> edit <workload>",),
            rationale="Scoped config patch; easy to revert.",
        ),
    ),
    "Incorrect volume or envFrom mount": (
        _t(
            action="fix_volume_mount",
            expected_effect="Mount secret/config at the path the app expects",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.80,
            action_type=FixActionType.PATCH_CONFIG,
            change="Fix volumeMounts / envFrom paths",
            commands=("kubectl -n <ns> edit <workload>",),
            rationale="Workload-local config change with high reversibility.",
        ),
    ),
    "Wrong image tag": (
        _t(
            action="patch_image_tag",
            expected_effect="Pull a valid image tag and stop ImagePullBackOff",
            risk=RiskLevel.MEDIUM,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.85,
            action_type=FixActionType.PATCH_CONFIG,
            change="Set workload image to an existing tag",
            commands=("kubectl -n <ns> set image <workload> *=<valid-image>",),
            rationale="Single-workload change; roll back by restoring previous image.",
        ),
        _t(
            action="rollback_deployment",
            expected_effect="Restore last known-good image",
            risk=RiskLevel.MEDIUM,
            blast_radius="medium",
            reversibility="medium",
            rollback_possible=True,
            base_confidence=0.70,
            action_type=FixActionType.ROLLBACK_DEPLOYMENT,
            change="Rollback deployment revision",
            commands=("kubectl -n <ns> rollout undo <workload>",),
            rationale="Larger blast radius than a targeted image patch.",
        ),
    ),
    "Image deleted or repository missing": (
        _t(
            action="patch_image_tag",
            expected_effect="Point at an existing repository/tag",
            risk=RiskLevel.MEDIUM,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.80,
            action_type=FixActionType.PATCH_CONFIG,
            change="Update image reference to an existing repository/tag",
            commands=("kubectl -n <ns> set image <workload> *=<valid-image>",),
            rationale="Minimal blast radius relative to broader rollbacks.",
        ),
    ),
    "Missing registry credentials": (
        _t(
            action="create_image_pull_secret",
            expected_effect="Allow private registry pulls",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.85,
            action_type=FixActionType.PATCH_CONFIG,
            change="Create/attach imagePullSecret for the registry",
            commands=(
                "kubectl -n <ns> create secret docker-registry <name> --docker-server=...",
            ),
            rationale="Namespace-scoped credential fix; easy to remove.",
        ),
    ),
    "Unhandled exception in application": (
        _t(
            action="rollback_deployment",
            expected_effect="Restore last known-good application revision",
            risk=RiskLevel.HIGH,
            blast_radius="medium",
            reversibility="medium",
            rollback_possible=True,
            base_confidence=0.80,
            action_type=FixActionType.ROLLBACK_DEPLOYMENT,
            change="Rollback deployment to last known-good revision",
            commands=("kubectl -n <ns> rollout undo <workload>",),
            rationale="Best available automated recovery for a bad deploy; higher risk.",
        ),
        _t(
            action="restart_pod",
            expected_effect="Retry process; unlikely to fix deterministic bugs",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.20,
            action_type=FixActionType.RESTART_POD,
            change="Restart the crashing pod",
            commands=("kubectl -n <ns> delete pod <pod>",),
            rationale="Low blast radius but poor expected effect for unhandled exceptions.",
        ),
    ),
    "Bad configuration": (
        _t(
            action="patch_config",
            expected_effect="Fix invalid configuration so the app can start",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.85,
            action_type=FixActionType.PATCH_CONFIG,
            change="Fix ConfigMap/env values and restart rollout",
            commands=("kubectl -n <ns> edit configmap <name>",),
            rationale="Config-scoped, reversible, low blast radius.",
        ),
    ),
    "Missing environment variable": (
        _t(
            action="patch_env_var",
            expected_effect="Provide required environment variable",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.85,
            action_type=FixActionType.PATCH_CONFIG,
            change="Add missing environment variable to workload",
            commands=("kubectl -n <ns> set env <workload> KEY=value",),
            rationale="Single-workload env patch with high reversibility.",
        ),
    ),
}


# Map catalog remediation_key → default option family when cause-specific missing.
_OPTIONS_BY_REMEDIATION_KEY: dict[str, tuple[RemediationTemplate, ...]] = {
    "Resource Constraint (OOMKilled)": _OPTIONS_BY_CAUSE["Memory limit too low"],
    "Missing Secret": _OPTIONS_BY_CAUSE["Secret not created"],
    "Invalid Image Tag / Image Pull Error": _OPTIONS_BY_CAUSE["Wrong image tag"],
    "Application Bug / Unhandled Exception": _OPTIONS_BY_CAUSE["Unhandled exception in application"],
    "Bad Configuration / Config Parse Error": _OPTIONS_BY_CAUSE["Bad configuration"],
    "Missing Environment Variable": _OPTIONS_BY_CAUSE["Missing environment variable"],
}


_GENERIC: tuple[RemediationTemplate, ...] = (
    _t(
        action="noop_investigate",
        expected_effect="Avoid unsafe automated change; gather more evidence",
        risk=RiskLevel.LOW,
        blast_radius="low",
        reversibility="high",
        rollback_possible=True,
        base_confidence=0.20,
        action_type=FixActionType.NOOP,
        change="No automated change; require human investigation",
        commands=(),
        rationale="Safest option when no remediation template matches.",
    ),
)


def templates_for(*, cause: str, remediation_key: str | None) -> tuple[RemediationTemplate, ...]:
    if cause in _OPTIONS_BY_CAUSE:
        return _OPTIONS_BY_CAUSE[cause]
    if remediation_key and remediation_key in _OPTIONS_BY_REMEDIATION_KEY:
        return _OPTIONS_BY_REMEDIATION_KEY[remediation_key]
    return _GENERIC
