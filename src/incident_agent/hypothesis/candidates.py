"""SRE-style root-cause candidates keyed by scoped diagnosis category."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HypothesisCandidate:
    cause: str
    slug: str
    prior: float
    # Patterns that support this underlying cause (not the symptom).
    patterns: tuple[re.Pattern[str], ...]
    # Fallback evidence texts when patterns do not hit raw lines.
    default_evidence: tuple[str, ...]
    verification_checks: tuple[str, ...]
    # Existing remediation catalog cause string (planner bridge).
    remediation_key: str


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), flags=re.IGNORECASE)


OOM_CANDIDATES: tuple[HypothesisCandidate, ...] = (
    HypothesisCandidate(
        cause="Memory leak",
        slug="memory_leak",
        prior=0.55,
        patterns=(
            _rx(
                r"repeated|again|multiple|restart",
                r"gradual|grew|growth|increasing",
                r"heap|leak",
            ),
        ),
        default_evidence=("Repeated OOMKilled events",),
        verification_checks=(
            "Check heap usage over time",
            "Check memory graph for monotonic growth",
            "Capture heap dump before next OOM",
        ),
        remediation_key="Resource Constraint (OOMKilled)",
    ),
    HypothesisCandidate(
        cause="Memory limit too low",
        slug="memory_limit_too_low",
        prior=0.35,
        patterns=(
            _rx(
                r"startup|during start|init",
                r"limit|requests/limits|memory limit",
                r"insufficient memory",
            ),
        ),
        default_evidence=("OOM during container start suggests limit too low",),
        verification_checks=(
            "Check configured memory requests/limits",
            "Compare peak RSS to the memory limit",
            "Review startup memory footprint",
        ),
        remediation_key="Resource Constraint (OOMKilled)",
    ),
    HypothesisCandidate(
        cause="Traffic spike",
        slug="traffic_spike",
        prior=0.10,
        patterns=(
            _rx(
                r"traffic|spike|surge",
                r"\brps\b|requests?/s|qps|throughput",
                r"high load|load spike",
            ),
        ),
        default_evidence=("Possible load-driven memory pressure",),
        verification_checks=(
            "Check request rate metrics around the OOM window",
            "Correlate OOM timestamps with traffic spikes",
            "Inspect horizontal pod autoscaler events",
        ),
        remediation_key="Resource Constraint (OOMKilled)",
    ),
)


INVALID_CONFIGURATION_CANDIDATES: tuple[HypothesisCandidate, ...] = (
    HypothesisCandidate(
        cause="Secret not created",
        slug="secret_not_created",
        prior=0.40,
        patterns=(
            _rx(r"secret .* not found", r"secret missing", r"failedmount", r"does not exist"),
        ),
        default_evidence=("Referenced secret was not found",),
        verification_checks=(
            "kubectl get secret <name> -n <namespace>",
            "Confirm secret was created in the correct namespace",
            "Check recent apply/helm install for omitted secrets",
        ),
        remediation_key="Missing Secret",
    ),
    HypothesisCandidate(
        cause="Bad configuration",
        slug="bad_config",
        prior=0.35,
        patterns=(
            _rx(
                r"invalid configuration",
                r"configuration missing",
                r"failed to load config",
                r"\byaml parse error\b|\byaml:\b",
                r"\bjson:\b",
                r"keyerror",
            ),
        ),
        default_evidence=("Configuration parse/validation error at startup",),
        verification_checks=(
            "Validate ConfigMap/env values against the app schema",
            "Diff current config with last known-good revision",
        ),
        remediation_key="Bad Configuration / Config Parse Error",
    ),
    HypothesisCandidate(
        cause="Missing environment variable",
        slug="missing_env_var",
        prior=0.25,
        patterns=(
            _rx(r"environment variable.*(?:missing|not set)", r"required .* env", r"keyerror"),
        ),
        default_evidence=("Required environment variable may be unset",),
        verification_checks=(
            "Compare required env vars to the pod spec",
            "Check Secret/ConfigMap keys used by env references",
        ),
        remediation_key="Missing Environment Variable",
    ),
)


APPLICATION_FAILURE_CANDIDATES: tuple[HypothesisCandidate, ...] = (
    HypothesisCandidate(
        cause="Unhandled exception in application",
        slug="unhandled_exception",
        prior=0.60,
        patterns=(
            _rx(r"traceback", r"unhandled exception", r"application exception", r"exception:"),
        ),
        default_evidence=("Application raised an unhandled exception",),
        verification_checks=(
            "Inspect previous container logs for the stack trace",
            "Identify the failing code path and recent deploy",
            "Reproduce crash locally with the same config",
        ),
        remediation_key="Application Bug / Unhandled Exception",
    ),
    HypothesisCandidate(
        cause="Fatal runtime error",
        slug="fatal_runtime_error",
        prior=0.25,
        patterns=(
            _rx(r"panic", r"fatal error", r"segmentation fault"),
        ),
        default_evidence=("Fatal runtime signal observed in application logs",),
        verification_checks=(
            "Inspect panic/fatal stack output from previous container logs",
            "Check for native dependency/runtime mismatch in the latest release",
        ),
        remediation_key="Application Bug / Unhandled Exception",
    ),
    HypothesisCandidate(
        cause="Startup regression after deploy",
        slug="startup_regression",
        prior=0.15,
        patterns=(
            _rx(r"startup", r"crashloopbackoff", r"back-?off restarting failed container"),
        ),
        default_evidence=("Crash loop started after application startup path changed",),
        verification_checks=(
            "Check deployment timeline for recent application changes",
            "Rollback to last known-good revision and verify recovery",
        ),
        remediation_key="Application Bug / Unhandled Exception",
    ),
)


CANDIDATES_BY_CATEGORY: dict[str, tuple[HypothesisCandidate, ...]] = {
    "OOMKilled": OOM_CANDIDATES,
    "Invalid Configuration": INVALID_CONFIGURATION_CANDIDATES,
    "Application Failure": APPLICATION_FAILURE_CANDIDATES,
    # Backward-compatible aliases for older scenario fixtures.
    "Missing Secret": INVALID_CONFIGURATION_CANDIDATES,
    "Invalid Image": INVALID_CONFIGURATION_CANDIDATES,
    "Application Crash": APPLICATION_FAILURE_CANDIDATES,
    "Unknown": APPLICATION_FAILURE_CANDIDATES,
}
