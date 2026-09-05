"""The two Bayesian stacks must stay separate at the import boundary."""

from __future__ import annotations

import inspect

from incident_agent.nodes.verify_hypotheses import verify_hypotheses
from incident_agent.verification import engine, loop


def test_live_engine_does_not_bind_loop_api() -> None:
    assert "HypothesisState" not in engine.__dict__
    assert "bayesian_update" not in engine.__dict__
    assert "run_bayesian_verification_loop" not in engine.__dict__
    assert "IncidentState" in engine.__dict__


def test_oom_loop_does_not_bind_live_engine_api() -> None:
    assert "HypothesisState" in loop.__dict__
    assert "IncidentState" not in loop.__dict__
    assert "verify_hypotheses_from_state" not in loop.__dict__
    assert "HypothesisVerification" not in loop.__dict__


def test_graph_verify_node_calls_live_engine_only() -> None:
    source = inspect.getsource(verify_hypotheses)
    assert "verify_hypotheses_from_state" in source
    assert "run_bayesian_verification_loop" not in source
