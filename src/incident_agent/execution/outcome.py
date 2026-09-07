"""Post-execution verification: re-observe and confirm incident resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass

from incident_agent.contracts import (
    EvidenceRequest,
    EvidenceResult,
    IncidentState,
    Observations,
    OutcomeVerification,
    ResolutionAssessment,
)
from incident_agent.memory.resolution import assess_resolution, confirmed_cause
from incident_agent.providers.bundle import ProviderBundle
from incident_agent.providers.fulfill import fulfill_evidence_requests

_OUTCOME_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "restart count decreases": (
        re.compile(r"restart_count\s*=\s*0\b", re.I),
        re.compile(r"restart count decreases", re.I),
        re.compile(r"no recent restarts", re.I),
    ),
    "pod becomes healthy": (
        re.compile(r"pod becomes healthy", re.I),
        re.compile(r"\bReady\b", re.I),
        re.compile(r"pod healthy", re.I),
        re.compile(r"Running\b.*\bReady", re.I),
    ),
    "crashloop clears": (
        re.compile(r"crashloop clears", re.I),
        re.compile(r"no crashloop", re.I),
        re.compile(r"not in crashloopbackoff", re.I),
    ),
    "secret mount succeeds": (
        re.compile(r"secret mount succeeds", re.I),
        re.compile(r"secret .* (?:exists|mounted)", re.I),
        re.compile(r"no failedmount", re.I),
    ),
    "image pull succeeds": (
        re.compile(r"image pull succeeds", re.I),
        re.compile(r"pulled image|image already present", re.I),
        re.compile(r"no errimagepull", re.I),
    ),
}


def _post_execution_probe_requests(state: IncidentState) -> list[EvidenceRequest]:
    target = (
        state.execution_plan.target
        if state.execution_plan
        else str(state.observations.extra.get("target_ref", "<workload>"))
    )
    return [
        EvidenceRequest(
            request_id="post-er-1-restart",
            type="event",
            query="restart_history",
            target=target,
            rationale="Post-execution restart trend",
        ),
        EvidenceRequest(
            request_id="post-er-2-health",
            type="describe",
            query="pod_health",
            target=target,
            rationale="Post-execution pod health",
        ),
    ]


def _failed_execution_verification(
    *,
    expected: list[str],
    action: str,
    cause: str,
) -> OutcomeVerification:
    return OutcomeVerification(
        resolved=False,
        expected_outcome=expected,
        observed_outcome=[],
        unmet_expectations=expected,
        evidence_summaries=[],
        reason="Execution did not succeed; cannot verify resolution",
        assessment=assess_resolution(
            execution_success=False,
            service_recovered=False,
            observed_outcomes=[],
            action=action,
            confirmed_hypothesis=cause,
        ),
    )


def _lines_from_state(state: IncidentState, summaries: list[str]) -> list[str]:
    lines = list(summaries)
    lines.extend(state.observations.logs)
    lines.extend(state.observations.events)
    if state.execution is not None:
        lines.extend(state.execution.applied_changes)
        lines.append(state.execution.summary)
        lines.append(state.execution.action)
    return lines


@dataclass(frozen=True, slots=True)
class _ClusterWorkloadState:
    """Fields from Kubernetes evidence data — not log phrases."""

    phase: str | None = None
    ready: bool | None = None
    restart_count: int | None = None
    waiting_reason: str | None = None
    terminated_reason: str | None = None
    container_ready: bool | None = None


def _cluster_state_from_results(results: list[EvidenceResult]) -> _ClusterWorkloadState | None:
    """Prefer structured Kubernetes GET data when the observation provider is live."""
    k8s = [r for r in results if r.data.get("provider") == "kubernetes"]
    if not k8s:
        return None
    phase = None
    ready = None
    restart_count = None
    waiting_reason = None
    terminated_reason = None
    container_ready = None
    for result in k8s:
        data = result.data
        if "phase" in data:
            phase = data.get("phase")
        if "ready" in data:
            ready = data.get("ready")
        if "restart_count" in data:
            restart_count = data.get("restart_count")
        if "waiting_reason" in data:
            waiting_reason = data.get("waiting_reason")
        if "terminated_reason" in data:
            terminated_reason = data.get("terminated_reason")
        if "container_ready" in data:
            container_ready = data.get("container_ready")
    return _ClusterWorkloadState(
        phase=str(phase) if phase is not None else None,
        ready=bool(ready) if isinstance(ready, bool) else None,
        restart_count=int(restart_count) if isinstance(restart_count, int) else None,
        waiting_reason=str(waiting_reason) if waiting_reason else None,
        terminated_reason=str(terminated_reason) if terminated_reason else None,
        container_ready=bool(container_ready) if isinstance(container_ready, bool) else None,
    )


def _is_crashlooping(cluster: _ClusterWorkloadState) -> bool:
    reasons = " ".join(
        part for part in (cluster.waiting_reason, cluster.terminated_reason) if part
    ).lower()
    return "crashloop" in reasons


def _expectation_met_cluster(expectation: str, cluster: _ClusterWorkloadState) -> bool | None:
    """True/False from pod state, or None to fall back to synthetic regex."""
    key = expectation.lower()
    if key == "pod becomes healthy":
        if cluster.phase is None and cluster.ready is None:
            return None
        ready = cluster.container_ready if cluster.container_ready is not None else cluster.ready
        return (
            cluster.phase == "Running"
            and ready is True
            and not _is_crashlooping(cluster)
        )
    if key == "restart count decreases":
        if cluster.restart_count is None:
            return None
        return cluster.restart_count == 0
    if key == "crashloop clears":
        if (
            cluster.waiting_reason is None
            and cluster.terminated_reason is None
            and cluster.ready is None
            and cluster.phase is None
        ):
            return None
        return not _is_crashlooping(cluster)
    return None


def _expectation_met_text(expectation: str, lines: list[str]) -> bool:
    patterns = _OUTCOME_PATTERNS.get(expectation.lower())
    joined = "\n".join(lines)
    if patterns:
        return any(p.search(joined) for p in patterns)
    return expectation.lower() in joined.lower()


def _expectation_met(
    expectation: str,
    lines: list[str],
    cluster: _ClusterWorkloadState | None,
) -> bool:
    if cluster is not None:
        judged = _expectation_met_cluster(expectation, cluster)
        if judged is not None:
            return judged
    return _expectation_met_text(expectation, lines)


def verify_execution_outcome(
    state: IncidentState,
    *,
    providers: ProviderBundle,
) -> tuple[OutcomeVerification, Observations]:
    """
    Re-collect evidence after execution and decide if the service recovered.

    Command success alone never marks the incident resolved.
    Service recovery is not recorded as root-cause verification.
    """
    plan = state.execution_plan
    expected = list(plan.expected_outcome) if plan else ["pod becomes healthy"]
    action = state.execution.action if state.execution else ""
    cause = confirmed_cause(state)

    if state.execution is None or not state.execution.success:
        return (
            _failed_execution_verification(
                expected=expected, action=action, cause=cause
            ),
            state.observations,
        )

    # Mark post-execution so synthetic providers can return healed signals.
    extra = dict(state.observations.extra)
    extra["post_execution"] = True
    if state.execution.action:
        extra["post_execution_action"] = state.execution.action
    probe_state = state.model_copy(
        update={
            "observations": Observations(
                schema_version=state.observations.schema_version,
                logs=list(state.observations.logs),
                events=list(state.observations.events),
                extra=extra,
            )
        }
    )

    requests = _post_execution_probe_requests(probe_state)
    results = fulfill_evidence_requests(
        bundle=providers,
        requests=requests,
        state=probe_state,
    )
    summaries = [r.summary for r in results if r.summary]
    lines = _lines_from_state(probe_state, summaries)
    cluster = _cluster_state_from_results(results)

    observed: list[str] = []
    unmet: list[str] = []
    for expectation in expected:
        if _expectation_met(expectation, lines, cluster):
            observed.append(expectation)
        else:
            unmet.append(expectation)

    # Critical: execution success without meeting outcomes ⇒ not resolved.
    resolved = bool(expected) and not unmet
    if not expected:
        resolved = False
        unmet = ["no expected_outcome defined on ExecutionPlan"]

    assessment = assess_resolution(
        execution_success=True,
        service_recovered=resolved,
        observed_outcomes=observed,
        action=action,
        confirmed_hypothesis=cause,
    )
    reason = _outcome_reason(resolved, unmet, assessment, cluster=cluster)

    # Merge probe summaries into observations for auditability.
    next_events = list(state.observations.events)
    for summary in summaries:
        if summary not in next_events:
            next_events.append(summary)
    next_extra = dict(extra)
    next_extra["post_execution_evidence"] = [r.model_dump() for r in results]
    next_extra["outcome_source"] = "kubernetes" if cluster is not None else "synthetic"
    observations = Observations(
        schema_version=state.observations.schema_version,
        logs=list(state.observations.logs),
        events=next_events,
        extra=next_extra,
    )

    return (
        OutcomeVerification(
            resolved=resolved,
            expected_outcome=expected,
            observed_outcome=observed,
            unmet_expectations=unmet,
            evidence_summaries=summaries,
            reason=reason,
            assessment=assessment,
        ),
        observations,
    )


def _outcome_reason(
    resolved: bool,
    unmet: list[str],
    assessment: ResolutionAssessment,
    *,
    cluster: _ClusterWorkloadState | None = None,
) -> str:
    source = "cluster state" if cluster is not None else "synthetic evidence"
    if assessment.root_cause_verified:
        return f"Root cause verified: causal action plus recovered service ({source})"
    if resolved:
        return (
            "Service recovered after execution; root cause not verified "
            f"(stable_recovery={assessment.stable_recovery}, via {source})"
        )
    return (
        f"Execution succeeded but incident not resolved; unmet={unmet} (via {source})"
    )
