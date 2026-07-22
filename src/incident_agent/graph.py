from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from incident_agent.contracts import IncidentState
from incident_agent.nodes import (
    compute_confidence,
    diagnose,
    hypothesize,
    plan_fix,
    validate_fix,
    verify_hypotheses,
)
from incident_agent.nodes.enrich import enrich
from incident_agent.providers import PROVIDERS_CONFIG_KEY, ProviderBundle, default_providers
from incident_agent.routing import bump_replan, finalize, route_on_confidence

try:
    from langgraph.graph import END, START, StateGraph
except Exception as e:  # pragma: no cover
    raise ImportError(
        "LangGraph is not installed. Install with `pip install langgraph` "
        "or `pip install .[agent]`."
    ) from e


def _config_from_runnable(config: RunnableConfig | None) -> dict[str, Any] | None:
    if config is None:
        return None
    if isinstance(config, dict):
        return dict(config)
    try:
        return dict(config)
    except Exception:
        return None


def _with_phase(*, phase: str, fn):
    """Wrap a pure reasoning node (state → partial update) and set phase."""

    def _wrapped(
        state: IncidentState,
        config: RunnableConfig | None = None,
    ) -> dict[str, object]:
        _ = config
        updates = dict(fn(state))
        updates["phase"] = phase
        return updates

    return _wrapped


def _enrich_node(
    state: IncidentState,
    config: RunnableConfig | None = None,
) -> dict[str, object]:
    return enrich(state, config=_config_from_runnable(config))


def _confidence_node(
    state: IncidentState,
    config: RunnableConfig | None = None,
) -> dict[str, object]:
    _ = config
    updates = dict(compute_confidence(state))
    updates["phase"] = "score_confidence"
    return updates


def _finalize_node(
    state: IncidentState,
    config: RunnableConfig | None = None,
) -> dict[str, object]:
    return finalize(state, config=_config_from_runnable(config))


def build_graph():
    """
    Provider-aware LangGraph with confidence replan loop.

    START -> enrich -> diagnose -> hypothesize -> verify_hypotheses -> plan_fix
          -> validate_fix -> confidence
      ├── high confidence / max retries → finalize → END
      └── low confidence & retries left → replan → hypothesize ↺

    Providers are injected via:
      invoke(state, config={"configurable": {"providers": ProviderBundle(...)}})
    """
    g = StateGraph(IncidentState)

    g.add_node("enrich", _enrich_node)
    g.add_node("diagnose", _with_phase(phase="diagnose", fn=diagnose))
    g.add_node("hypothesize", _with_phase(phase="hypothesize", fn=hypothesize))
    g.add_node(
        "verify_hypotheses",
        _with_phase(phase="verify_hypotheses", fn=verify_hypotheses),
    )
    g.add_node("plan_fix", _with_phase(phase="plan_fix", fn=plan_fix))
    g.add_node("validate_fix", _with_phase(phase="validate_fix", fn=validate_fix))
    g.add_node("confidence", _confidence_node)
    g.add_node("replan", bump_replan)
    g.add_node("finalize", _finalize_node)

    g.add_edge(START, "enrich")
    g.add_edge("enrich", "diagnose")
    g.add_edge("diagnose", "hypothesize")
    g.add_edge("hypothesize", "verify_hypotheses")
    g.add_edge("verify_hypotheses", "plan_fix")
    g.add_edge("plan_fix", "validate_fix")
    g.add_edge("validate_fix", "confidence")

    g.add_conditional_edges(
        "confidence",
        route_on_confidence,
        {
            "end": "finalize",
            "replan": "replan",
        },
    )
    g.add_edge("replan", "hypothesize")
    g.add_edge("finalize", END)

    return g.compile()


class IncidentStateGraph:
    """Thin wrapper so `invoke()` always returns an `IncidentState` contract."""

    def __init__(self, app, *, providers: ProviderBundle | None = None) -> None:
        self._app = app
        self._providers = providers or default_providers()

    def invoke(
        self,
        state: IncidentState,
        *,
        providers: ProviderBundle | None = None,
    ) -> IncidentState:
        bundle = providers or self._providers
        out = self._app.invoke(
            state,
            config={"configurable": {PROVIDERS_CONFIG_KEY: bundle}},
        )
        if isinstance(out, IncidentState):
            return out
        return IncidentState.model_validate(out)


GRAPH = IncidentStateGraph(build_graph())
