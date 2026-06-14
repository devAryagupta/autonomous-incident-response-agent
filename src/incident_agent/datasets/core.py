"""
Dataset utilities (JSONL ↔ Pydantic models).

This module is intentionally small but teaches a few practical concepts:
- JSONL ingestion: stream lines, parse JSON, validate schema, collect errors.
- "Strict vs permissive" loading: fail-fast for training/CI, best-effort for exploration.
- Pydantic as a schema boundary: raw JSON becomes typed/validated objects early.
- Error aggregation: report many bad records at once (better than failing on first).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True, slots=True)
class DatasetRecordError:
    # Concept: "Value object" for diagnostics.
    # - frozen=True makes it immutable (safer + hashable-like behavior).
    # - slots=True reduces per-instance memory overhead (nice when you may collect many errors).
    path: str
    line: int
    message: str
    raw: str | None = None


class DatasetLoadError(RuntimeError):
    def __init__(self, *, path: str, errors: list[DatasetRecordError]) -> None:
        # Concept: raise one exception, carry structured details.
        # This keeps call-sites simple ("try/except DatasetLoadError")
        # while preserving rich context.
        self.path = path
        self.errors = errors
        super().__init__(self._render())

    def _render(self) -> str:
        # Concept: keep exception __str__ human-friendly.
        # We cap printed errors so logs remain readable,
        # while still keeping all errors in `self.errors`.
        head = f"Dataset load failed: {self.path} ({len(self.errors)} error(s))"
        details = "\n".join(f"- L{e.line}: {e.message}" for e in self.errors[:10])
        more = "" if len(self.errors) <= 10 else f"\n- ... and {len(self.errors) - 10} more"
        return f"{head}\n{details}{more}"


def iter_jsonl_lines(path: Path) -> Iterator[tuple[int, str]]:
    # Concept: streaming I/O (iterator) instead of loading entire file.
    # JSONL is naturally line-oriented, so we yield (line_number, stripped_line)
    # for good diagnostics.
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            s = line.strip()
            if not s:
                continue
            yield i, s


def load_jsonl[T: BaseModel](
    model: type[T],
    path: str | Path,
    *,
    strict: bool = True,
    max_errors: int = 50,
) -> list[T]:
    """
    Load a JSONL file into a list of pydantic models.

    - strict=True: any invalid line raises DatasetLoadError (aggregated errors)
    - strict=False: invalid lines are skipped; valid records returned
    """
    # Concept: PEP 695 type parameters (Python 3.12+):
    # `load_jsonl[T: BaseModel](model: type[T], ...) -> list[T]`
    # means: "given a specific Pydantic model class, return a list of that model type".
    p = Path(path)
    errors: list[DatasetRecordError] = []
    out: list[T] = []

    for line_no, raw in iter_jsonl_lines(p):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            # Concept: separate "syntax" errors (bad JSON) from "schema" errors
            # (valid JSON, wrong shape).
            # For permissive mode we keep the raw line to help debugging;
            # for strict mode we avoid huge errors.
            errors.append(
                DatasetRecordError(
                    path=str(p),
                    line=line_no,
                    message=f"Invalid JSON: {e.msg}",
                    raw=raw if not strict else None,
                )
            )
            if strict and len(errors) >= max_errors:
                # Concept: bounded error collection.
                # Prevents massive datasets from producing enormous exception payloads/logs.
                break
            continue

        try:
            # Concept: schema boundary.
            # Once validated, downstream code can assume the record matches `model`
            # (types + required fields).
            out.append(model.model_validate(obj))
        except ValidationError:
            # Concept: ValidationError is "expected" data quality feedback,
            # not a crash-worthy surprise.
            errors.append(
                DatasetRecordError(
                    path=str(p),
                    line=line_no,
                    message="Schema validation failed",
                    raw=raw if not strict else None,
                )
            )
            if strict and len(errors) >= max_errors:
                break

    if strict and errors:
        # Concept: fail at the end in strict mode.
        # You get *all* problems (up to max_errors) instead of fixing one line per run.
        raise DatasetLoadError(path=str(p), errors=errors)

    return out


def write_jsonl(records: Iterable[BaseModel], path: str | Path) -> None:
    # Concept: reversible serialization.
    # `model_dump(mode="json")` ensures values are JSON-compatible (e.g., datetimes become strings).
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.model_dump(mode="json"), ensure_ascii=False) + "\n")

