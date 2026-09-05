"""Remediation candidate templates (decision layer — no execution).

Each row is a cause → action pairing with an explicit purpose:

- root_cause: potential root-cause remediation
- mitigation: symptom control; not a root-cause fix
- temporary_recovery: service may recover; the cause is unchanged
- investigate: no automated change
"""

from __future__ import annotations

from dataclasses import dataclass

from incident_agent.contracts import FixActionType, RemediationPurpose, RiskLevel


@dataclass(frozen=True, slots=True)
class RemediationTemplate:
    """Catalog row for one cause → action pairing.

    ``base_confidence`` (alias ``base_effectiveness``) is the catalog weight
    for how well this action fits the cause — not P(success).
    ``purpose`` is mitigation vs root-cause vs temporary recovery for this cause.
    Suitability = base_effectiveness × current hypothesis belief (posterior).
    """

    action: str
    purpose: RemediationPurpose
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
    purpose: RemediationPurpose,
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
        purpose=purpose,
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
_OPTIONS_BY_CAUSE: dict[str, tuple[RemediationTemplate, ...]] = {
    "Memory leak": (
        _t(
            action="increase_memory_limit",
            purpose=RemediationPurpose.MITIGATION,
            expected_effect=(
                "Mitigation: raise the limit so OOM is delayed. Not a root-cause fix."
            ),
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
            rationale="Purpose=mitigation. Memory still grows; this only buys time.",
        ),
        _t(
            action="rollback_deployment",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect=(
                "Potential root-cause remediation: undo a revision that introduced the leak."
            ),
            risk=RiskLevel.MEDIUM,
            blast_radius="medium",
            reversibility="medium",
            rollback_possible=True,
            base_confidence=0.60,
            action_type=FixActionType.ROLLBACK_DEPLOYMENT,
            change="Rollback deployment to last known-good revision",
            commands=("kubectl -n <ns> rollout undo <workload>",),
            rationale=(
                "Purpose=root_cause (potential). Only if the leak arrived with this deploy."
            ),
        ),
        _t(
            action="restart_pod",
            purpose=RemediationPurpose.TEMPORARY_RECOVERY,
            expected_effect=(
                "Temporary recovery only: pod may become Ready; the leak is unchanged."
            ),
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.25,
            action_type=FixActionType.RESTART_POD,
            change="Restart the crashing pod",
            commands=("kubectl -n <ns> delete pod <pod>",),
            rationale="Purpose=temporary_recovery. Not remediation and not mitigation of the leak.",
        ),
    ),
    "Memory limit too low": (
        _t(
            action="increase_memory_limit",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect="Potential root-cause remediation: size the limit to actual need.",
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
            rationale="Purpose=root_cause. This addresses undersized limits, not a leak.",
        ),
        _t(
            action="restart_pod",
            purpose=RemediationPurpose.TEMPORARY_RECOVERY,
            expected_effect="Temporary recovery only: retry startup; the limit is unchanged.",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.30,
            action_type=FixActionType.RESTART_POD,
            change="Restart the crashing pod",
            commands=("kubectl -n <ns> delete pod <pod>",),
            rationale="Purpose=temporary_recovery. Chronic OOM returns if the limit stays low.",
        ),
    ),
    "Traffic spike": (
        _t(
            action="scale_deployment",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect=(
                "Potential root-cause remediation: add replicas to absorb the load."
            ),
            risk=RiskLevel.MEDIUM,
            blast_radius="medium",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.75,
            action_type=FixActionType.SCALE_DEPLOYMENT,
            change="Scale deployment replicas to handle traffic spike",
            commands=("kubectl -n <ns> scale <workload> --replicas=<n>",),
            rationale="Purpose=root_cause. Addresses capacity, not an application leak.",
        ),
        _t(
            action="increase_memory_limit",
            purpose=RemediationPurpose.MITIGATION,
            expected_effect=(
                "Mitigation: survive peak memory during the spike. Not a root-cause fix."
            ),
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
            rationale="Purpose=mitigation. Masks load pressure; does not absorb the spike.",
        ),
    ),
    "Secret not created": (
        _t(
            action="create_or_fix_secret",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect="Potential root-cause remediation: create the missing secret.",
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
            rationale="Purpose=root_cause. The missing credential is the cause.",
        ),
        _t(
            action="restart_pod",
            purpose=RemediationPurpose.TEMPORARY_RECOVERY,
            expected_effect="Temporary recovery only: retry mount; useless if the secret is still missing.",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.35,
            action_type=FixActionType.RESTART_POD,
            change="Restart pod after secret remediation",
            commands=("kubectl -n <ns> delete pod <pod>",),
            rationale="Purpose=temporary_recovery. Follow-up only, not a secret fix.",
        ),
    ),
    "Wrong secret name or namespace": (
        _t(
            action="fix_secret_reference",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect="Potential root-cause remediation: point the workload at the correct secret.",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.85,
            action_type=FixActionType.PATCH_CONFIG,
            change="Correct secret name/namespace reference on the workload",
            commands=("kubectl -n <ns> edit <workload>",),
            rationale="Purpose=root_cause. The wrong reference is the cause.",
        ),
    ),
    "Incorrect volume or envFrom mount": (
        _t(
            action="fix_volume_mount",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect="Potential root-cause remediation: mount at the path the app expects.",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.80,
            action_type=FixActionType.PATCH_CONFIG,
            change="Fix volumeMounts / envFrom paths",
            commands=("kubectl -n <ns> edit <workload>",),
            rationale="Purpose=root_cause. The mount path/ref is the cause.",
        ),
    ),
    "Wrong image tag": (
        _t(
            action="patch_image_tag",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect="Potential root-cause remediation: set a tag that exists.",
            risk=RiskLevel.MEDIUM,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.85,
            action_type=FixActionType.PATCH_CONFIG,
            change="Set workload image to an existing tag",
            commands=("kubectl -n <ns> set image <workload> *=<valid-image>",),
            rationale="Purpose=root_cause. The bad tag is the cause.",
        ),
        _t(
            action="rollback_deployment",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect="Potential root-cause remediation: restore the last known-good image.",
            risk=RiskLevel.MEDIUM,
            blast_radius="medium",
            reversibility="medium",
            rollback_possible=True,
            base_confidence=0.70,
            action_type=FixActionType.ROLLBACK_DEPLOYMENT,
            change="Rollback deployment revision",
            commands=("kubectl -n <ns> rollout undo <workload>",),
            rationale="Purpose=root_cause (potential). Broader than a targeted image patch.",
        ),
    ),
    "Image deleted or repository missing": (
        _t(
            action="patch_image_tag",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect="Potential root-cause remediation: point at a repository/tag that exists.",
            risk=RiskLevel.MEDIUM,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.80,
            action_type=FixActionType.PATCH_CONFIG,
            change="Update image reference to an existing repository/tag",
            commands=("kubectl -n <ns> set image <workload> *=<valid-image>",),
            rationale="Purpose=root_cause. The missing repository/tag is the cause.",
        ),
    ),
    "Missing registry credentials": (
        _t(
            action="create_image_pull_secret",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect="Potential root-cause remediation: attach pull credentials.",
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
            rationale="Purpose=root_cause. Missing pull auth is the cause.",
        ),
    ),
    "Unhandled exception in application": (
        _t(
            action="rollback_deployment",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect=(
                "Potential root-cause remediation: restore the last known-good revision."
            ),
            risk=RiskLevel.HIGH,
            blast_radius="medium",
            reversibility="medium",
            rollback_possible=True,
            base_confidence=0.80,
            action_type=FixActionType.ROLLBACK_DEPLOYMENT,
            change="Rollback deployment to last known-good revision",
            commands=("kubectl -n <ns> rollout undo <workload>",),
            rationale="Purpose=root_cause (potential). Removes a bad deploy; does not patch the bug.",
        ),
        _t(
            action="restart_pod",
            purpose=RemediationPurpose.TEMPORARY_RECOVERY,
            expected_effect="Temporary recovery only: retry; deterministic bugs crash again.",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.20,
            action_type=FixActionType.RESTART_POD,
            change="Restart the crashing pod",
            commands=("kubectl -n <ns> delete pod <pod>",),
            rationale="Purpose=temporary_recovery. Not a fix for an unhandled exception.",
        ),
    ),
    "Bad configuration": (
        _t(
            action="patch_config",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect="Potential root-cause remediation: correct the invalid config.",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.85,
            action_type=FixActionType.PATCH_CONFIG,
            change="Fix ConfigMap/env values and restart rollout",
            commands=("kubectl -n <ns> edit configmap <name>",),
            rationale="Purpose=root_cause. The bad config is the cause.",
        ),
    ),
    "Missing environment variable": (
        _t(
            action="patch_env_var",
            purpose=RemediationPurpose.ROOT_CAUSE,
            expected_effect="Potential root-cause remediation: set the required variable.",
            risk=RiskLevel.LOW,
            blast_radius="low",
            reversibility="high",
            rollback_possible=True,
            base_confidence=0.85,
            action_type=FixActionType.PATCH_CONFIG,
            change="Add missing environment variable to workload",
            commands=("kubectl -n <ns> set env <workload> KEY=value",),
            rationale="Purpose=root_cause. The missing env var is the cause.",
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
        purpose=RemediationPurpose.INVESTIGATE,
        expected_effect="Investigate only: no automated change.",
        risk=RiskLevel.LOW,
        blast_radius="low",
        reversibility="high",
        rollback_possible=True,
        base_confidence=0.20,
        action_type=FixActionType.NOOP,
        change="No automated change; require human investigation",
        commands=(),
        rationale="Purpose=investigate. No mitigation, recovery, or root-cause action selected.",
    ),
)


def templates_for(*, cause: str, remediation_key: str | None) -> tuple[RemediationTemplate, ...]:
    if cause in _OPTIONS_BY_CAUSE:
        return _OPTIONS_BY_CAUSE[cause]
    if remediation_key and remediation_key in _OPTIONS_BY_REMEDIATION_KEY:
        return _OPTIONS_BY_REMEDIATION_KEY[remediation_key]
    return _GENERIC


def purpose_for(*, cause: str, action: str) -> RemediationPurpose:
    """Purpose of this action for this cause. Cause-specific; same action can differ."""
    act = action.strip()
    key = cause.strip()
    templates = _OPTIONS_BY_CAUSE.get(key)
    if templates is None:
        canon = next((name for name in _OPTIONS_BY_CAUSE if name.lower() == key.lower()), None)
        templates = _OPTIONS_BY_CAUSE.get(canon) if canon else None
    if templates:
        for template in templates:
            if template.action == act:
                return template.purpose
    if act in {"restart_pod", "rollout_restart"}:
        return RemediationPurpose.TEMPORARY_RECOVERY
    return RemediationPurpose.INVESTIGATE
