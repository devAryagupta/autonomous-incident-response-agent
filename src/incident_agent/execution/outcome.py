"""Post-execution verification: re-observe and confirm incident resolution."""

from __future__ import annotations

import re

from incident_agent.contracts import (
    EvidenceRequest,
    IncidentState,
    Observations,
    OutcomeVerification,
)
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


def _lines_from_state(state: IncidentState, summaries: list[str]) -> list[str]:
    lines = list(summaries)
    lines.extend(state.observations.logs)
    lines.extend(state.observations.events)
    if state.execution is not None:
        lines.extend(state.execution.applied_changes)
        lines.append(state.execution.summary)
        lines.append(state.execution.action)
    return lines


def _expectation_met(expectation: str, lines: list[str]) -> bool:
    patterns = _OUTCOME_PATTERNS.get(expectation.lower())
    joined = "\n".join(lines)
    if patterns:
        return any(p.search(joined) for p in patterns)
    # Fallback: substring match
    return expectation.lower() in joined.lower()


def verify_execution_outcome(
    state: IncidentState,
    *,
    providers: ProviderBundle,
) -> tuple[OutcomeVerification, Observations]:
    """
    Re-collect evidence after execution and decide if the incident is resolved.

    Command success alone never marks the incident resolved.
    """
    plan = state.execution_plan
    expected = list(plan.expected_outcome) if plan else ["pod becomes healthy"]

    if state.execution is None or not state.execution.success:
        return (
            OutcomeVerification(
                resolved=False,
                expected_outcome=expected,
                observed_outcome=[],
                unmet_expectations=expected,
                evidence_summaries=[],
                reason="Execution did not succeed; cannot verify resolution",
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

    observed: list[str] = []
    unmet: list[str] = []
    for expectation in expected:
        if _expectation_met(expectation, lines):
            observed.append(expectation)
        else:
            unmet.append(expectation)

    # Critical: execution success without meeting outcomes ⇒ not resolved.
    resolved = bool(expected) and not unmet
    if not expected:
        resolved = False
        unmet = ["no expected_outcome defined on ExecutionPlan"]

    reason = (
        "Incident resolved: all expected outcomes observed after execution"
        if resolved
        else (
            "Execution succeeded but incident not resolved; "
            f"unmet={unmet}"
        )
    )

    # Merge probe summaries into observations for auditability.
    next_events = list(state.observations.events)
    for summary in summaries:
        if summary not in next_events:
            next_events.append(summary)
    next_extra = dict(extra)
    next_extra["post_execution_evidence"] = [r.model_dump() for r in results]
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
        ),
        observations,
    )
