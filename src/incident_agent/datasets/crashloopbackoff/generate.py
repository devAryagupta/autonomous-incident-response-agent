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
import uuid
from datetime import UTC, datetime
from pathlib import Path

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


def _incident_id() -> str:
    # Concept: IDs are stable-format but unique;
    # prefix helps quick visual filtering in logs/datasets.
    return f"clb-{uuid.uuid4().hex[:12]}"


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
            "missing_env_var",
            "bad_env_var_value",
            "missing_configmap",
            "missing_secret",
            "misconfigured_volume_mount",
            "dependency_unavailable",
            "app_bug_unhandled_exception",
            "resource_constraint_oom",
            "resource_constraint_cpu",
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

    if category == "missing_env_var":
        # Concept: a "category" is a label you can use for evaluation and training.
        # Each branch builds a coherent story: symptom (logs/events) ⇒ root cause ⇒ expected fix.
        missing = _pick(rng, ["DB_HOST", "REDIS_URL", "API_URL", "S3_BUCKET"])
        logs = [
            f"Error: {missing} environment variable missing",
            "Process exiting with code 1",
        ]
        root_cause = f"Missing required environment variable {missing}"
        expected_fix = ExpectedFix(
            summary=f"Patch workload env var {missing}",
            kind="patch_deployment_env",
            kubectl_hint=(
                f"kubectl -n {target.namespace} set env "
                f"{target.kind.lower()}/{target.name} {missing}=<value>"
            ),
        )
    elif category == "bad_env_var_value":
        var = _pick(rng, ["API_URL", "DB_PORT", "LOG_LEVEL"])
        bad = _pick(rng, ["htp://bad-url", "99999", "VERBOSEST"])
        logs = [
            f"ConfigError: invalid value for {var}={bad}",
            "Failed to start application",
        ]
        root_cause = f"Incorrect environment variable value ({var} invalid)"
        expected_fix = ExpectedFix(
            summary=f"Correct env var value for {var}",
            kind="patch_deployment_env",
            kubectl_hint=(
                f"kubectl -n {target.namespace} set env "
                f"{target.kind.lower()}/{target.name} {var}=<valid>"
            ),
        )
    elif category == "missing_configmap":
        cm = _pick(rng, ["app-config", "runtime-config", "feature-flags"])
        events.append(
            'Warning  FailedMount  kubelet  MountVolume.SetUp failed for volume "config": '
            f'configmap "{cm}" not found'
        )
        logs = ["FATAL: required configuration file not found at /etc/app/config.yaml"]
        root_cause = f"ConfigMap {cm} missing or misnamed"
        expected_fix = ExpectedFix(
            summary=f"Create or reference correct ConfigMap {cm}",
            kind="create_configmap",
            kubectl_hint=(
                f"kubectl -n {target.namespace} get configmap {cm} || "
                f"kubectl -n {target.namespace} apply -f <configmap>.yaml"
            ),
        )
    elif category == "missing_secret":
        secret = _pick(rng, ["db-credentials", "api-keys", "tls-cert"])
        events.append(
            'Warning  FailedMount  kubelet  MountVolume.SetUp failed for volume "secret": '
            f'secret "{secret}" not found'
        )
        logs = ["FATAL: could not load credentials from /etc/secrets/creds.json"]
        root_cause = f"Secret {secret} missing or wrong namespace"
        expected_fix = ExpectedFix(
            summary=f"Create secret {secret} or fix secret reference",
            kind="create_secret",
            kubectl_hint=(
                f"kubectl -n {target.namespace} get secret {secret} || "
                f"kubectl -n {target.namespace} create secret generic {secret} "
                "--from-literal=..."
            ),
        )
    elif category == "misconfigured_volume_mount":
        path = _pick(rng, ["/var/lib/app", "/data", "/etc/app"])
        logs = [
            f"panic: cannot open {path}/state.db: no such file or directory",
            "stacktrace: ...",
        ]
        events.append(
            "Warning  FailedMount  kubelet  MountVolume.SetUp failed: path does not exist"
        )
        root_cause = "Misconfigured volume mount (path mismatch or missing directory)"
        expected_fix = ExpectedFix(
            summary="Fix volumeMount path / volume configuration",
            kind="patch_resource",
            kubectl_hint=f"kubectl -n {target.namespace} edit {target.kind.lower()}/{target.name}",
        )
    elif category == "dependency_unavailable":
        dep = _pick(rng, ["redis", "postgres", "kafka", "auth-service"])
        logs = [
            f"ERROR: failed to connect to {dep}: dial tcp: lookup {dep}: no such host",
            "Retrying (1/3)...",
            "Exiting after retries",
        ]
        # Concept: distractors (noise) improve robustness.
        # The incident still contains the true clue, but also plausible "everything looks OK" lines.
        # This discourages simplistic keyword matching and teaches reasoning over multiple signals.
        distractors = ["INFO: starting HTTP server on :8080", "INFO: loaded config OK"]
        root_cause = f"Dependency unavailable or DNS/network misconfiguration for {dep}"
        expected_fix = ExpectedFix(
            summary=f"Restore dependency {dep} / fix Service DNS / network policy",
            kind="restore_dependency",
            kubectl_hint=f"kubectl -n {target.namespace} get svc,endpoints | findstr {dep}",
        )
    elif category == "app_bug_unhandled_exception":
        logs = [
            "Unhandled exception: NullReferenceError at startup",
            "Traceback (most recent call last):",
            "  at main() ...",
            "Process terminated",
        ]
        root_cause = "Application bug causing crash on startup (unhandled exception)"
        expected_fix = ExpectedFix(
            summary="Rollback to last known good image or hotfix application",
            kind="rollback_deployment",
            kubectl_hint=(
                f"kubectl -n {target.namespace} rollout undo "
                f"{target.kind.lower()}/{target.name}"
            ),
        )
    elif category == "resource_constraint_oom":
        logs = [
            "Killed",
            "OOMKilled: Container was killed due to memory usage",
        ]
        events.append("Warning  OOMKilled  kubelet  Container killed due to OOM")
        root_cause = "Insufficient memory limit causing OOMKilled during startup"
        expected_fix = ExpectedFix(
            summary="Increase memory requests/limits or reduce startup memory usage",
            kind="patch_resources",
            kubectl_hint=(
                f"kubectl -n {target.namespace} set resources "
                f"{target.kind.lower()}/{target.name} "
                "--limits=memory=512Mi --requests=memory=256Mi"
            ),
        )
    else:  # resource_constraint_cpu
        logs = [
            "ERROR: startup timed out waiting for background tasks",
            "Hint: CPU starvation suspected",
        ]
        events.append("Warning  Unhealthy  kubelet  Startup probe failed: timeout")
        root_cause = "Insufficient CPU request/limit causing startup timeouts"
        expected_fix = ExpectedFix(
            summary="Increase CPU requests/limits or relax startup probe thresholds",
            kind="patch_resources",
            kubectl_hint=(
                f"kubectl -n {target.namespace} set resources "
                f"{target.kind.lower()}/{target.name} "
                "--limits=cpu=500m --requests=cpu=200m"
            ),
        )

    return CrashLoopBackOffIncident(
        incident_id=_incident_id(),
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

