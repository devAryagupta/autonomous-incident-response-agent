"""Provider bundle: the single DI unit passed into the graph/pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from incident_agent.memory.store import BaseMemoryStore
from incident_agent.providers.dry_run import DryRunExecutionProvider
from incident_agent.providers.memory import LocalIncidentMemoryProvider, NoMemoryProvider
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

if TYPE_CHECKING:
    from incident_agent.providers.kubernetes import KubernetesClient


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    """Injectable set of providers. Swap implementations without changing nodes."""

    observations: ObservationProvider
    metrics: MetricsProvider
    execution: ExecutionProvider
    memory: MemoryProvider


def default_providers() -> ProviderBundle:
    """Stage-0 defaults: synthetic observations/metrics, dry-run, no persistent memory."""
    return ProviderBundle(
        observations=SyntheticObservationProvider(),
        metrics=SyntheticMetricsProvider(),
        execution=DryRunExecutionProvider(),
        memory=NoMemoryProvider(),
    )


def learning_providers(
    *,
    memory_path: str | Path | None = None,
    memory_store: BaseMemoryStore | None = None,
) -> ProviderBundle:
    """Same Stage-0 backends with LocalIncidentMemoryProvider enabled."""
    memory: MemoryProvider
    if memory_store is not None:
        memory = LocalIncidentMemoryProvider(store=memory_store)
    else:
        memory = LocalIncidentMemoryProvider(path=memory_path)
    return ProviderBundle(
        observations=SyntheticObservationProvider(),
        metrics=SyntheticMetricsProvider(),
        execution=DryRunExecutionProvider(),
        memory=memory,
    )


def k8s_observation_providers(
    *,
    client: KubernetesClient | None = None,
    kube_context: str | None = None,
    memory_path: str | Path | None = None,
    memory_store: BaseMemoryStore | None = None,
) -> ProviderBundle:
    """
    Real Kubernetes observations; metrics/execution still Stage-0 stubs.

    Pass an injectable client for tests, or omit to load kubeconfig / in-cluster.
    """
    # Lazy import avoids providers.__init__ ↔ kubernetes circular load.
    from incident_agent.providers.kubernetes import (
        KubernetesObservationProvider,
        live_kubernetes_client,
    )

    k8s_client = client if client is not None else live_kubernetes_client(context=kube_context)
    memory: MemoryProvider
    if memory_store is not None:
        memory = LocalIncidentMemoryProvider(store=memory_store)
    elif memory_path is not None:
        memory = LocalIncidentMemoryProvider(path=memory_path)
    else:
        memory = NoMemoryProvider()
    return ProviderBundle(
        observations=KubernetesObservationProvider(k8s_client),
        metrics=SyntheticMetricsProvider(),
        execution=DryRunExecutionProvider(),
        memory=memory,
    )
