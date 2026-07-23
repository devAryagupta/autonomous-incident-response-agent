"""Memory provider implementations: NoMemory + local structured incident memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from incident_agent.contracts import IncidentState
from incident_agent.memory.episode import episode_from_state
from incident_agent.memory.retrieval import MemoryRetriever, extract_symptoms_from_text
from incident_agent.memory.store import BaseMemoryStore, JSONLMemoryStore

DEFAULT_MEMORY_PATH = Path("runtime/memory/incidents.jsonl")


class NoMemoryProvider:
    """
    Disabled memory backend (explicit opt-out).

    Prefer LocalIncidentMemoryProvider for learning across incidents.
    """

    def query_similar(self, state: IncidentState, *, k: int = 3) -> list[dict[str, Any]]:
        _ = state
        _ = k
        return []

    def store(self, state: IncidentState) -> str | None:
        _ = state
        return None


class LocalIncidentMemoryProvider:
    """
    Structured incident memory over a BaseMemoryStore (JSONL by default).

    Not an embedding store: persists full episodes (diagnosis, evidence, fix,
    resolution) and retrieves by symptom overlap. Swap the store later for
    SQLite / Chroma without changing MemoryProvider call sites.
    """

    def __init__(
        self,
        store: BaseMemoryStore | None = None,
        *,
        path: str | Path | None = None,
    ) -> None:
        if store is not None:
            self._store = store
        else:
            self._store = JSONLMemoryStore(path or DEFAULT_MEMORY_PATH)
        self._retriever = MemoryRetriever(self._store)

    @property
    def store(self) -> BaseMemoryStore:
        return self._store

    def query_similar(self, state: IncidentState, *, k: int = 3) -> list[dict[str, Any]]:
        symptoms = self._symptoms_from_state(state)
        results = self._retriever.retrieve_similar(symptoms, top_k=k, include_failures=False)
        return [r.model_dump(mode="json") for r in results]

    def store(self, state: IncidentState) -> str | None:
        episode = episode_from_state(state)
        if episode is None:
            return None
        self._store.save(episode)
        return episode.episode_id

    def _symptoms_from_state(self, state: IncidentState) -> list[str]:
        diagnosis = ""
        if state.diagnosis is not None:
            diagnosis = state.diagnosis.category or state.diagnosis.summary
        symptoms = extract_symptoms_from_text(
            state.alert.alert_name,
            diagnosis,
            *state.observations.logs,
            *state.observations.events,
        )
        if state.alert.alert_name and state.alert.alert_name not in symptoms:
            symptoms.insert(0, state.alert.alert_name)
        if diagnosis and diagnosis not in symptoms:
            symptoms.append(diagnosis)
        return symptoms
