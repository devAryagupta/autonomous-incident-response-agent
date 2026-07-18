"""Provider bundle: the single DI unit passed into the graph/pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from incident_agent.providers.dry_run import DryRunExecutionProvider
from incident_agent.providers.memory import NoMemoryProvider
from incident_agent.providers.protocols import (
    ExecutionProvider,
    MemoryProvider,
    MetricsProvider,
    ObservationProvider,
)
from incident_agent.providers.synthetic import (
    SyntheticMetricsProvider,
    SyntheticObservationProvider,
)


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    """Injectable set of providers. Swap implementations without changing nodes."""

    observations: ObservationProvider
    metrics: MetricsProvider
    execution: ExecutionProvider
    memory: MemoryProvider


def default_providers() -> ProviderBundle:
    """Stage-0 defaults: synthetic observations/metrics, dry-run execution, no memory."""
    return ProviderBundle(
        observations=SyntheticObservationProvider(),
        metrics=SyntheticMetricsProvider(),
        execution=DryRunExecutionProvider(),
        memory=NoMemoryProvider(),
    )
