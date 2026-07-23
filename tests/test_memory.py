from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from incident_agent.contracts import (
    Alert,
    Diagnosis,
    Evidence,
    IncidentState,
    Observations,
)
from incident_agent.memory import (
    EpisodeOutcome,
    IncidentEpisode,
    InMemoryMemoryStore,
    JSONLMemoryStore,
    MemoryRetriever,
    episode_from_state,
    prior_adjustments_from_memory,
)
from incident_agent.memory.models import MemoryRetrievalResult
from incident_agent.nodes.hypothesize import hypothesize
from incident_agent.providers import LocalIncidentMemoryProvider, learning_providers
from incident_agent.hypothesis.candidates import OOM_CANDIDATES


def _episode(
    *,
    incident_id: str,
    symptoms: list[str],
    diagnosis: str = "OOMKilled",
    confirmed: str = "Memory limit too low",
    action: str = "increase_memory_limit",
    outcome: EpisodeOutcome = EpisodeOutcome.SUCCESS,
) -> IncidentEpisode:
    return IncidentEpisode(
        episode_id=f"ep-{incident_id}",
        incident_id=incident_id,
        symptoms=symptoms,
        diagnosis=diagnosis,
        confirmed_hypothesis=confirmed,
        remediation_action=action,
        outcome=outcome,
        evidence={"note": "synthetic history"},
        timestamp=datetime.now(tz=UTC),
    )


def test_jsonl_store_persists_and_reads_back(tmp_path: Path) -> None:
    path = tmp_path / "incidents.jsonl"
    store = JSONLMemoryStore(path)
    episode = _episode(
        incident_id="INC-001",
        symptoms=["CrashLoopBackOff", "OOMKilled", "Exit 137"],
    )
    store.save(episode)
    loaded = store.fetch_all()
    assert len(loaded) == 1
    assert loaded[0].incident_id == "INC-001"
    assert loaded[0].diagnosis == "OOMKilled"
    assert loaded[0].remediation_action == "increase_memory_limit"
    assert loaded[0].outcome == EpisodeOutcome.SUCCESS


def test_retriever_ranks_overlapping_symptoms_higher() -> None:
    store = InMemoryMemoryStore()
    store.save(
        _episode(
            incident_id="oom-1",
            symptoms=["CrashLoopBackOff", "OOMKilled", "Exit 137"],
            confirmed="Memory limit too low",
            action="increase_memory_limit",
        )
    )
    store.save(
        _episode(
            incident_id="secret-1",
            symptoms=["CrashLoopBackOff", "MissingSecret"],
            diagnosis="Missing Secret",
            confirmed="Secret not created",
            action="create_or_fix_secret",
        )
    )
    retriever = MemoryRetriever(store)
    results = retriever.retrieve_similar(
        ["CrashLoopBackOff", "OOMKilled"],
        top_k=5,
        include_failures=False,
    )
    assert results
    assert results[0].episode.incident_id == "oom-1"
    assert results[0].similarity_score > 0.3
    assert "oomkilled" in {s.lower() for s in results[0].matched_symptoms} or any(
        "oom" in s.lower() for s in results[0].matched_symptoms
    )
    if len(results) > 1:
        assert results[0].similarity_score >= results[1].similarity_score


def test_hypothesis_prior_shifts_toward_historically_successful_fix() -> None:
    """75% of similar OOM episodes fixed by increasing limits → boost Low-limit prior."""
    store = InMemoryMemoryStore()
    for i in range(15):
        store.save(
            _episode(
                incident_id=f"oom-limit-{i}",
                symptoms=["CrashLoopBackOff", "OOMKilled", "Exit 137"],
                confirmed="Memory limit too low",
                action="increase_memory_limit",
            )
        )
    for i in range(5):
        store.save(
            _episode(
                incident_id=f"oom-leak-{i}",
                symptoms=["CrashLoopBackOff", "OOMKilled", "Exit 137"],
                confirmed="Memory leak",
                action="rollback_deployment",
            )
        )

    memory = LocalIncidentMemoryProvider(store=store)
    state = IncidentState(
        incident_id="inc-new-oom",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
            labels={"service": "payment-service"},
        ),
        observations=Observations(
            logs=["OOMKilled: Container was killed due to memory usage", "exit status 137"],
            events=[
                "Warning  OOMKilled  kubelet  Container killed due to OOM",
                "Warning  BackOff  kubelet  Back-off restarting failed container",
            ],
            extra={"top_n": 3, "target_ref": "deployment/payment-service"},
        ),
        diagnosis=Diagnosis(
            summary="Resource Constraint (OOMKilled)",
            category="OOMKilled",
            confidence=0.9,
            evidence=[
                Evidence(source="events", text="OOMKilled event detected"),
                Evidence(source="other", text="Container terminated with exit code 137"),
            ],
        ),
    )

    similar = memory.query_similar(state, k=10)
    assert similar
    boosts = prior_adjustments_from_memory(
        candidate_slugs=[c.slug for c in OOM_CANDIDATES],
        similar_incidents=similar,
    )
    assert boosts.get("memory_limit_too_low", 0.0) > boosts.get("memory_leak", 0.0)
    assert boosts["memory_limit_too_low"] >= 0.2

    baseline = hypothesize(state)["hypotheses"]
    state.similar_incidents = similar
    with_memory = hypothesize(state)["hypotheses"]

    baseline_limit = next(h.likelihood for h in baseline if h.description == "Memory limit too low")
    memory_limit = next(h.likelihood for h in with_memory if h.description == "Memory limit too low")
    assert memory_limit > baseline_limit
    assert memory_limit - baseline_limit >= 0.05


def test_local_provider_store_and_retrieve_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "memory.jsonl"
    providers = learning_providers(memory_path=path)
    state = IncidentState(
        incident_id="INC-pay-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(
            logs=["exit status 137"],
            events=["OOMKilled"],
            extra={"target_ref": "deployment/payment-service"},
        ),
        diagnosis=Diagnosis(summary="OOMKilled", category="OOMKilled", confidence=0.9),
        incident_resolved=True,
        execution=__import__("incident_agent.contracts", fromlist=["ExecutionResult"]).ExecutionResult(
            executed=False,
            success=True,
            status="dry_run_success",
            action="increase_memory_limit",
            applied_changes=["memory limit updated"],
            summary="ok",
        ),
    )
    # Attach a confirmed hypothesis for learning quality.
    from incident_agent.contracts import Hypothesis, HypothesisVerification

    state.hypotheses = [
        Hypothesis(
            hypothesis_id="h1-memory_limit_too_low",
            description="Memory limit too low",
            likelihood=0.8,
            remediation_key="Resource Constraint (OOMKilled)",
        )
    ]
    state.hypothesis_verifications = [
        HypothesisVerification(
            hypothesis_id="h1-memory_limit_too_low",
            hypothesis="Memory limit too low",
            result="confirmed",
            expected_evidence=["limit"],
            observed_evidence=["near memory limit"],
        )
    ]

    episode_id = providers.memory.store(state)
    assert episode_id
    assert path.exists()

    again = IncidentState(
        incident_id="INC-pay-2",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(
            logs=["exit status 137", "OOMKilled"],
            events=["Back-off restarting failed container"],
        ),
        diagnosis=Diagnosis(summary="OOMKilled", category="OOMKilled", confidence=0.85),
    )
    hits = providers.memory.query_similar(again, k=3)
    assert hits
    assert hits[0]["episode"]["incident_id"] == "INC-pay-1"
    assert hits[0]["similarity_score"] > 0.0


def test_episode_from_state_captures_resolution() -> None:
    from incident_agent.contracts import ExecutionResult, Hypothesis, HypothesisVerification

    state = IncidentState(
        incident_id="INC-003",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(logs=["exit code 137"], events=["OOMKilled"]),
        diagnosis=Diagnosis(category="OOMKilled", summary="OOMKilled", confidence=0.9),
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1-memory_leak",
                description="Memory leak",
                likelihood=0.7,
            )
        ],
        hypothesis_verifications=[
            HypothesisVerification(
                hypothesis_id="h1-memory_leak",
                hypothesis="Memory leak",
                result="confirmed",
            )
        ],
        execution=ExecutionResult(
            executed=False,
            success=True,
            status="dry_run_success",
            action="increase_memory_limit",
            applied_changes=["updated"],
            summary="ok",
        ),
        incident_resolved=True,
    )
    episode = episode_from_state(state)
    assert episode is not None
    assert episode.diagnosis == "OOMKilled"
    assert episode.confirmed_hypothesis == "Memory leak"
    assert episode.remediation_action == "increase_memory_limit"
    assert episode.outcome == EpisodeOutcome.SUCCESS
    assert "OOMKilled" in episode.symptoms or "Exit 137" in episode.symptoms


def test_failed_episodes_filtered_by_default() -> None:
    store = InMemoryMemoryStore()
    store.save(
        _episode(
            incident_id="fail-1",
            symptoms=["CrashLoopBackOff", "OOMKilled"],
            outcome=EpisodeOutcome.FAILURE,
        )
    )
    store.save(
        _episode(
            incident_id="ok-1",
            symptoms=["CrashLoopBackOff", "OOMKilled"],
            outcome=EpisodeOutcome.SUCCESS,
        )
    )
    results = MemoryRetriever(store).retrieve_similar(
        ["CrashLoopBackOff", "OOMKilled"],
        include_failures=False,
    )
    assert all(r.episode.outcome != EpisodeOutcome.FAILURE for r in results)
    assert results[0].episode.incident_id == "ok-1"
