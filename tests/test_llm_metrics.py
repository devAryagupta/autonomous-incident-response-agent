from __future__ import annotations

from incident_agent.eval.llm_metrics import (
    aggregate_llm_snapshots,
    hypotheses_match,
    snapshot_from_audit,
)


def test_hypotheses_match_is_label_exact() -> None:
    assert hypotheses_match("Memory leak", "Memory leak")
    assert not hypotheses_match("Memory leak", "Memory limit too low")
    assert not hypotheses_match(None, "Memory leak")


def test_snapshot_preserves_raw_suggestion_and_rejection() -> None:
    audit = {
        "provider": "scripted",
        "model": "mock-v1",
        "generated": [
            {
                "suggestion_id": "s-good",
                "kind": "evidence",
                "summary": "request previous_container_logs",
                "rationale": "Need crash logs",
            },
            {
                "suggestion_id": "s-bad",
                "kind": "evidence",
                "summary": "execute rollout_restart immediately",
                "rationale": "just fix it",
            },
        ],
        "accepted": [
            {
                "suggestion_id": "s-good",
                "query": "previous_container_logs",
                "rationale": "Need crash logs",
            }
        ],
        "rejections": [
            {
                "suggestion_id": "s-bad",
                "reason": "control_plane_intent",
                "detail": "Suggestion attempts control-plane action intent.",
            }
        ],
        "generated_count": 2,
        "accepted_count": 1,
        "rejected_count": 1,
        "unsafe_count": 1,
        "useful_evidence_ids": ["er-llm-1"],
        "warnings": [],
        "latency_ms": 12,
    }
    snap = snapshot_from_audit(audit, stage="collect_evidence")
    assert snap.generated_count == 2
    assert snap.accepted_count == 1
    assert snap.rejected_count == 1
    by_id = {item.suggestion_id: item for item in snap.suggestions}
    assert by_id["s-good"].accepted is True
    assert by_id["s-bad"].accepted is False
    assert by_id["s-bad"].rejection_reason == "control_plane_intent"
    assert by_id["s-bad"].unsafe is True
    assert "rollout_restart" in by_id["s-bad"].summary

    totals = aggregate_llm_snapshots([snap])
    assert totals.suggestions_generated == 2
    assert totals.suggestions_accepted == 1
    assert totals.unsafe_control_suggestions == 1
    assert totals.unsafe_accepted == 0
    assert totals.acceptance_rate == 0.5
