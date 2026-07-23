"""Similarity retrieval over structured incident episodes."""

from __future__ import annotations

from incident_agent.memory.models import EpisodeOutcome, IncidentEpisode, MemoryRetrievalResult
from incident_agent.memory.store import BaseMemoryStore


def normalize_symptom(token: str) -> str:
    return " ".join(token.strip().lower().split())


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union)


def overlap_coefficient(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


class MemoryRetriever:
    """Retrieve historically similar episodes by symptom overlap."""

    def __init__(self, store: BaseMemoryStore) -> None:
        self._store = store

    def retrieve_similar(
        self,
        symptoms: list[str],
        *,
        top_k: int = 5,
        include_failures: bool = False,
        min_score: float = 0.15,
    ) -> list[MemoryRetrievalResult]:
        query = {normalize_symptom(s) for s in symptoms if s and s.strip()}
        if not query:
            return []

        ranked: list[MemoryRetrievalResult] = []
        for episode in self._store.fetch_all():
            if not include_failures and episode.outcome == EpisodeOutcome.FAILURE:
                continue
            episode_syms = {normalize_symptom(s) for s in episode.symptoms if s}
            if not episode_syms:
                continue
            # Blend Jaccard (set similarity) with overlap (handles subset queries).
            score = 0.6 * jaccard_similarity(query, episode_syms) + 0.4 * overlap_coefficient(
                query, episode_syms
            )
            if score < min_score:
                continue
            matched = sorted(query & episode_syms)
            ranked.append(
                MemoryRetrievalResult(
                    episode=episode,
                    similarity_score=round(float(score), 4),
                    matched_symptoms=matched,
                )
            )

        ranked.sort(key=lambda r: r.similarity_score, reverse=True)
        return ranked[: max(1, top_k)]


def extract_symptoms_from_text(*parts: str) -> list[str]:
    """Pull canonical symptom tokens from free-text logs/events/diagnosis."""
    joined = "\n".join(p for p in parts if p).lower()
    tokens: list[str] = []

    def _add(label: str, *needles: str) -> None:
        if any(n in joined for n in needles):
            tokens.append(label)

    _add("CrashLoopBackOff", "crashloopbackoff", "back-off restarting", "backoff restarting")
    _add("OOMKilled", "oomkilled", "killed due to oom", "out of memory")
    _add("Exit 137", "exit code 137", "exit status 137", "exitcode 137")
    _add("ErrImagePull", "errimagepull", "imagepullbackoff", "manifest unknown")
    _add("Traceback", "traceback", "unhandled exception", "panic:")
    _add("DiskPressure", "diskpressure", "no space left")
    if "secret" in joined and "not found" in joined:
        tokens.append("MissingSecret")

    # Dedup preserve order
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = normalize_symptom(token)
        if key not in seen:
            seen.add(key)
            out.append(token)
    return out
