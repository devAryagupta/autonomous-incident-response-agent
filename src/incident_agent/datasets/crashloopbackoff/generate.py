"""
Synthetic dataset generator for CrashLoopBackOff incidents (writes JSONL).

What you can learn from this file:
- Deterministic data generation: same seed ⇒ same dataset (critical for reproducible experiments).
- "Structured realism": incidents include alert + logs + events + expected fix
  (mirrors how ops works).
- Signal vs noise: optional distractor lines teach models to ignore
  irrelevant-but-plausible log text.
- Forward references in type hints: we enable postponed evaluation of annotations to avoid
  "NameError/ForwardRef" style issues in projects with circular imports or Pydantic models.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from incident_agent.datasets.crashloopbackoff.schema import (
    Alert,
    CrashLoopBackOffIncident,
    ExpectedFix,
    K8sRef,
)


def _now() -> datetime:
    # Concept: timezone-aware timestamps.
    # Prefer UTC in datasets so ordering/serialization is stable across machines and locales.
    return datetime.now(tz=UTC)


def _pick(rng: random.Random, items: list[str]) -> str:
    # Concept: inject RNG as a dependency (instead of using global `random`).
    # That makes generation deterministic + testable and avoids cross-test interference.
    return items[rng.randrange(0, len(items))]


def _incident_id_from_rng(rng: random.Random) -> str:
    # Concept: deterministic IDs per record (reproducible datasets).
    # We avoid uuid4() because it's not seedable.
    return f"clb-{rng.getrandbits(48):012x}"


def _base_alert(target: K8sRef, *, rng: random.Random) -> Alert:
    # Concept: "base object builder" pattern.
    # Alerts are mostly consistent across categories;
    # we centralize shared fields to reduce duplication.
    return Alert(
        alert_name="CrashLoopBackOff",
        severity=_pick(rng, ["warning", "critical"]),
        starts_at=_now(),
        labels={
            "namespace": target.namespace,
            "workload_kind": target.kind,
            "workload": target.name,
        },
        annotations={
            "summary": f"Pod(s) for {target.kind}/{target.name} are CrashLoopBackOff",
        },
    )


def _mk_target(*, rng: random.Random) -> K8sRef:
    # Concept: controlled randomness with a "vocabulary".
    # We pick from curated lists so outputs look realistic (namespaces/kinds/workload names)
    # while still being diverse enough to prevent overfitting to a single string pattern.
    namespaces = ["default", "payments", "orders", "inventory", "platform"]
    kinds = ["Deployment", "StatefulSet"]
    name_prefixes = ["api", "worker", "sync", "ingest", "billing", "checkout"]
    suffixes = ["svc", "service", "app"]
    kind = _pick(rng, kinds)
    name = f"{_pick(rng, name_prefixes)}-{_pick(rng, suffixes)}"
    return K8sRef(namespace=_pick(rng, namespaces), kind=kind, name=name)


class _Fmt(dict[str, str]):
    def __missing__(self, key: str) -> str:  # pragma: no cover
        return f"<{key}>"


_FIXTURES_CACHE: dict[str, Any] | None = None


def _default_fixtures_path() -> Path:
    return Path(__file__).parent / "fixtures" / "crashloop_fixtures.json"


def _load_fixtures(path: Path) -> dict[str, list[dict[str, Any]]]:
    global _FIXTURES_CACHE
    if _FIXTURES_CACHE is None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        _FIXTURES_CACHE = raw
    fixtures = _FIXTURES_CACHE.get("fixtures", {})  # type: ignore[union-attr]
    if not isinstance(fixtures, dict):
        return {}
    # mypy: runtime-checked shape
    return fixtures  # type: ignore[return-value]


def _fmt_lines(lines: list[str], ctx: dict[str, str]) -> list[str]:
    fm = _Fmt(ctx)
    return [s.format_map(fm) for s in lines]


def _sample_fixture(
    *,
    rng: random.Random,
    category: str,
    ctx: dict[str, str],
    fixtures_path: Path,
) -> tuple[list[str], list[str], str, ExpectedFix] | None:
    fixtures = _load_fixtures(fixtures_path)
    options = fixtures.get(category)
    if not options:
        return None
    fx = options[rng.randrange(0, len(options))]
    if not isinstance(fx, dict):
        return None

    logs = _fmt_lines(list(fx.get("logs", [])), ctx)
    events = _fmt_lines(list(fx.get("events", [])), ctx)
    root_cause = str(fx.get("root_cause", "")).format_map(_Fmt(ctx))

    ef = fx.get("expected_fix", {})
    if not isinstance(ef, dict):
        ef = {}
    expected_fix = ExpectedFix(
        summary=str(ef.get("summary", "")).format_map(_Fmt(ctx)),
        kind=(str(ef.get("kind")) if ef.get("kind") is not None else None),
        kubectl_hint=(
            str(ef.get("kubectl_hint")).format_map(_Fmt(ctx))
            if ef.get("kubectl_hint") is not None
            else None
        ),
    )

    if not root_cause or not expected_fix.summary:
        return None
    return logs, events, root_cause, expected_fix


def make_incident(*, seed: int, idx: int) -> CrashLoopBackOffIncident:
    # Concept: reproducibility strategy.
    # Each record gets its own RNG stream (`seed + idx`) so:
    # - generating N incidents is stable even if you later reorder logic inside one category
    # - incident i is always the same for a given seed (great for debugging regressions)
    rng = random.Random(seed + idx)
    target = _mk_target(rng=rng)
    category = _pick(
        rng,
        [
            # Scoped CrashLoopBackOff causes:
            # OOMKilled / Invalid Configuration / Application Failure.
            "oom",
            "missing_secret",
            "missing_env_var",
            "bad_config",
            "startup_exception",
        ],
    )

    logs: list[str] = []
    events: list[str] = [
        # Concept: Kubernetes "events" are a separate signal source from app logs.
        # Many real incidents require combining both (events show scheduling/mount/probe issues).
        "Warning  BackOff  kubelet  Back-off restarting failed container",
        "Normal   Pulled   kubelet  Container image already present on machine",
    ]
    distractors: list[str] = []

    # ---- fixtures-driven realism ----
    # Default fixtures file can be replaced by providing your own captured logs later.
    fixtures_path = _default_fixtures_path()
    ctx: dict[str, str] = {
        "namespace": target.namespace,
        "workload_kind": target.kind,
        "workload_kind_lower": target.kind.lower(),
        "workload_name": target.name,
        # pseudo pod/node ids (deterministic-ish and plausible)
        "pod_name": f"{target.name}-{rng.getrandbits(20):05x}",
        "node_name": f"node-{rng.getrandbits(16):04x}",
        # common placeholders used across fixtures
        "secret_name": _pick(rng, ["db-credentials", "api-keys", "tls-cert"]),
        "env_var": _pick(rng, ["DB_HOST", "REDIS_URL", "API_URL", "S3_BUCKET"]),
        "config_path": _pick(rng, ["/etc/app/config.yaml", "/app/config.yml", "/config/app.json"]),
        "dependency_service": _pick(rng, ["postgres", "redis", "auth-service", "kafka"]),
        "image": (
            f"{_pick(rng, ['docker.io/library/nginx', 'ghcr.io/acme/demo', 'registry.example.com/app'])}:"
            f"{_pick(rng, ['v99.0.0', 'bad-tag', 'does-not-exist', 'prod'])}"
        ),
    }

    sampled = _sample_fixture(
        rng=rng,
        category=category,
        ctx=ctx,
        fixtures_path=fixtures_path,
    )
    if sampled is not None:
        logs, events, root_cause, expected_fix = sampled
    else:
        raise RuntimeError(
            f"Missing fixtures for category={category!r}. "
            f"Expected to find it in {fixtures_path}."
        )

    return CrashLoopBackOffIncident(
        incident_id=_incident_id_from_rng(rng),
        created_at=_now(),
        target=target,
        alert=_base_alert(target, rng=rng),
        logs=logs + distractors,
        events=events,
        # Concept: static typing vs dynamic data.
        # If the schema types `category` as a Literal/Enum,
        # the random string may not be provably safe
        # to the type-checker even though our curated list matches the allowed values.
        # The ignore keeps generation code simple
        # while runtime validation is still enforced by Pydantic.
        category=category,  # type: ignore[arg-type]
        root_cause=root_cause,
        expected_fix=expected_fix,
        distractors=distractors,
    )


def _ensure_parent(p: Path) -> None:
    # Concept: idempotent filesystem prep (safe to call repeatedly).
    p.parent.mkdir(parents=True, exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    # Concept: CLI-friendly entrypoint.
    # Accepting `argv` makes this callable from tests without touching sys.argv.
    ap = argparse.ArgumentParser(
        description="Generate CrashLoopBackOff synthetic incidents (JSONL)."
    )
    ap.add_argument("--out", type=str, required=True, help="Output JSONL path")
    ap.add_argument("--n", type=int, default=25, help="Number of incidents to generate")
    ap.add_argument("--seed", type=int, default=1337, help="RNG seed")
    args = ap.parse_args(argv)

    out = Path(args.out)
    _ensure_parent(out)

    with out.open("w", encoding="utf-8") as f:
        # Concept: write JSONL (one JSON object per line).
        # This format is streaming-friendly and works well with Unix tools and dataset pipelines.
        for i in range(args.n):
            incident = make_incident(seed=args.seed, idx=i)
            f.write(json.dumps(incident.model_dump(mode="json"), ensure_ascii=False) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

