from __future__ import annotations

from incident_agent.contracts import IncidentState, Observations
from incident_agent.nodes import compute_confidence, diagnose, hypothesize, plan_fix, validate_fix

try:
    from langgraph.graph import END, START, StateGraph
except Exception as e:  # pragma: no cover
    raise ImportError(
        "LangGraph is not installed. Install with `pip install langgraph` "
        "or `pip install .[agent]`."
    ) from e


def _apply_node(*, phase: str, fn):
    def _wrapped(state: IncidentState) -> dict[str, object]:
        updates = dict(fn(state))
        updates["phase"] = phase
        return updates

    return _wrapped


def ingest(state: IncidentState) -> dict[str, object]:
    """
    Ingest node.

    Keeps orchestration trivial: only ensures defaults exist in `observations.extra`.
    """
    obs: Observations = state.observations.model_copy(deep=True)
    obs.extra.setdefault("top_n", 3)
    obs.extra.setdefault("target_ref", "<workload>")
    return {"observations": obs, "phase": "ingest"}


def build_graph():
    """
    Straight-line orchestration graph.

    ingest -> diagnose -> hypothesize -> plan_fix -> validate_fix -> confidence
    """
    g = StateGraph(IncidentState, input_schema=IncidentState, output_schema=IncidentState)

    g.add_node("ingest", ingest)
    g.add_node("diagnose", _apply_node(phase="diagnose", fn=diagnose))
    g.add_node("hypothesize", _apply_node(phase="hypothesize", fn=hypothesize))
    g.add_node("plan_fix", _apply_node(phase="plan_fix", fn=plan_fix))
    g.add_node("validate_fix", _apply_node(phase="validate_fix", fn=validate_fix))

    # Final node: compute confidence and mark done (matches pipeline's final state).
    def _confidence(state: IncidentState) -> dict[str, object]:
        updates = dict(compute_confidence(state))
        updates["phase"] = "done"
        return updates

    g.add_node("confidence", _confidence)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "diagnose")
    g.add_edge("diagnose", "hypothesize")
    g.add_edge("hypothesize", "plan_fix")
    g.add_edge("plan_fix", "validate_fix")
    g.add_edge("validate_fix", "confidence")
    g.add_edge("confidence", END)

    return g.compile()

class IncidentStateGraph:
    """Tiny wrapper so `invoke()` returns an IncidentState contract."""

    def __init__(self, app) -> None:
        self._app = app

    def invoke(self, state: IncidentState) -> IncidentState:
        out = self._app.invoke(state)
        if isinstance(out, IncidentState):
            return out
        # LangGraph returns raw dicts for state; normalize back to contract.
        return IncidentState.model_validate(out)


GRAPH = IncidentStateGraph(build_graph())

