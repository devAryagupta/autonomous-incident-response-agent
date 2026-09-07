"""Provider architecture: interfaces + Stage-0 synthetic/dry-run backends."""

from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    FixAction,
    FixActionType,
    FixPlan,
    IncidentState,
    Observations,
    RiskLevel,
)
from incident_agent.graph import GRAPH
from incident_agent.nodes.enrich import enrich
from incident_agent.providers import (
    DryRunExecutionProvider,
    ExecutionProvider,
    MemoryProvider,
    MetricsProvider,
    NoMemoryProvider,
    ObservationProvider,
    ProviderBundle,
    SyntheticMetricsProvider,
    SyntheticObservationProvider,
    default_providers,
)


def _state() -> IncidentState:
    return IncidentState(
        incident_id="inc-providers-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(
            logs=["secret db-credentials not found"],
            events=["Back-off restarting failed container"],
            extra={"top_n": 3, "target_ref": "deployment/demo"},
        ),
    )


def test_default_bundle_implements_protocols() -> None:
    bundle = default_providers()
    assert isinstance(bundle.observations, ObservationProvider)
    assert isinstance(bundle.metrics, MetricsProvider)
    assert isinstance(bundle.execution, ExecutionProvider)
    assert isinstance(bundle.memory, MemoryProvider)


def test_synthetic_observation_passthrough() -> None:
    state = _state()
    obs = SyntheticObservationProvider().fetch_observations(state)
    assert obs.logs == state.observations.logs
    assert obs.events == state.observations.events


def test_synthetic_metrics_stub() -> None:
    metrics = SyntheticMetricsProvider().fetch_metrics(_state())
    assert metrics["provider"] == "synthetic"
    assert metrics["series"] == {}


def test_no_memory_returns_empty() -> None:
    mem = NoMemoryProvider()
    assert mem.query_similar(_state()) == []
    assert mem.store(_state()) is None


def test_dry_run_execution_provider() -> None:
    state = _state()
    plan = FixPlan(
        hypothesis_id="h1",
        risk=RiskLevel.LOW,
        actions=[
            FixAction(
                action_type=FixActionType.ROLLBACK_DEPLOYMENT,
                target="deployment/demo",
                rationale="test",
            )
        ],
    )
    result = DryRunExecutionProvider().execute(plan, state=state)
    assert result.executed is False
    assert result.success is True
    assert result.status == "dry_run_success"
    assert result.action == "rollback_deployment"
    assert result.applied_changes
    assert "Dry-run success" in result.summary
    assert result.details["provider"] == "dry_run"
    assert result.details["status"] == "success"


def test_enrich_uses_injected_providers() -> None:
    state = _state()
    updates = enrich(state, providers=default_providers())
    obs = updates["observations"]
    assert isinstance(obs, Observations)
    assert "metrics" in obs.extra
    assert obs.extra["metrics"]["provider"] == "synthetic"
    assert updates["similar_incidents"] == []
    assert any(line.startswith("enrich:") for line in updates["log"])


class _SpyObservations(SyntheticObservationProvider):
    """Distinct type so GRAPH.invoke injection cannot hide behind Stage-0 defaults."""


def test_graph_accepts_provider_injection() -> None:
    state = _state()
    state.observations.extra["confidence_threshold"] = 0.0
    custom = ProviderBundle(
        observations=_SpyObservations(),
        metrics=SyntheticMetricsProvider(),
        execution=DryRunExecutionProvider(),
        memory=NoMemoryProvider(),
    )
    out = GRAPH.invoke(state, providers=custom)
    assert out.phase == "done"
    assert out.execution is not None
    assert out.execution.details["provider"] == "dry_run"
    assert out.observations.extra.get("observation_provider") == "_SpyObservations"
    assert out.observations.extra.get("metrics_provider") == "SyntheticMetricsProvider"
