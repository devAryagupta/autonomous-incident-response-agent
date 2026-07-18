"""Resolve ProviderBundle from LangGraph RunnableConfig or fall back to defaults."""

from __future__ import annotations

from typing import Any

from incident_agent.providers.bundle import ProviderBundle, default_providers

PROVIDERS_CONFIG_KEY = "providers"


def resolve_providers(config: dict[str, Any] | None = None) -> ProviderBundle:
    """
    Dependency-injection lookup.

    LangGraph invoke:
      GRAPH.invoke(state, config={"configurable": {"providers": bundle}})
    """
    if not config:
        return default_providers()

    configurable = config.get("configurable") or {}
    providers = configurable.get(PROVIDERS_CONFIG_KEY)
    if isinstance(providers, ProviderBundle):
        return providers
    return default_providers()
