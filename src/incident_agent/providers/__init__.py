"""Provider architecture: abstract interfaces + Stage-0 synthetic/dry-run backends."""

from incident_agent.providers.bundle import ProviderBundle, default_providers, learning_providers
from incident_agent.providers.context import PROVIDERS_CONFIG_KEY, resolve_providers
from incident_agent.providers.dry_run import DryRunExecutionProvider
from incident_agent.providers.fulfill import fulfill_evidence_requests
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

__all__ = [
    "PROVIDERS_CONFIG_KEY",
    "DryRunExecutionProvider",
    "ExecutionProvider",
    "LocalIncidentMemoryProvider",
    "MemoryProvider",
    "MetricsProvider",
    "NoMemoryProvider",
    "ObservationProvider",
    "ProviderBundle",
    "SyntheticMetricsProvider",
    "SyntheticObservationProvider",
    "default_providers",
    "fulfill_evidence_requests",
    "learning_providers",
    "resolve_providers",
]
