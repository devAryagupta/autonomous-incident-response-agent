"""Memory persistence: abstract store + JSONL implementation under runtime/."""

from __future__ import annotations

import json
import os
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path

from incident_agent.memory.models import IncidentEpisode


class BaseMemoryStore(ABC):
    """Port for episode persistence (JSONL today, SQLite/vector later)."""

    @abstractmethod
    def save(self, episode: IncidentEpisode) -> None:
        """Persist one episode."""

    @abstractmethod
    def fetch_all(self) -> list[IncidentEpisode]:
        """Return all stored episodes."""


class JSONLMemoryStore(BaseMemoryStore):
    """
    Append-only JSONL store with a simple lock file for concurrent writers.

    Path must live under runtime/ (or a test temp dir) — never under src/.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def save(self, episode: IncidentEpisode) -> None:
        line = episode.model_dump_json() + "\n"
        self._with_lock(lambda: self._append_line(line))

    def fetch_all(self) -> list[IncidentEpisode]:
        if not self._path.exists():
            return []
        episodes: list[IncidentEpisode] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                text = raw.strip()
                if not text:
                    continue
                episodes.append(IncidentEpisode.model_validate_json(text))
        return episodes

    def _append_line(self, line: str) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def _with_lock(self, fn) -> None:
        deadline = time.time() + 5.0
        while True:
            try:
                fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, str(os.getpid()).encode("ascii"))
                    fn()
                    return
                finally:
                    os.close(fd)
                    try:
                        self._lock_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            except FileExistsError:
                if time.time() >= deadline:
                    # Stale lock fallback: proceed without exclusive lock.
                    fn()
                    return
                time.sleep(0.02)


class InMemoryMemoryStore(BaseMemoryStore):
    """Process-local store for unit tests (no disk I/O)."""

    def __init__(self) -> None:
        self._episodes: list[IncidentEpisode] = []

    def save(self, episode: IncidentEpisode) -> None:
        self._episodes.append(episode)

    def fetch_all(self) -> list[IncidentEpisode]:
        return list(self._episodes)


def atomic_rewrite_jsonl(path: Path, episodes: list[IncidentEpisode]) -> None:
    """Replace a JSONL file atomically (utility for compaction / tests)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for episode in episodes:
                handle.write(episode.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
