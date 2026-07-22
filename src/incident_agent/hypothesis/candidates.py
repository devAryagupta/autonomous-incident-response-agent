"""SRE-style root-cause candidates keyed by diagnosis category."""

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

INVALID_IMAGE_CANDIDATES: tuple[HypothesisCandidate, ...] = (
    HypothesisCandidate(
        cause="Wrong image tag",
        slug="wrong_image_tag",
        prior=0.50,
        patterns=(
            _rx(r"manifest unknown", r"not found", r"wrong tag|invalid tag|tag .* not"),
        ),
        default_evidence=("Image reference could not be resolved",),
        verification_checks=(
            "Verify image tag exists in the registry",
            "Compare deployed tag to last known-good release",
            "Check recent deployment image changes",
        ),
        remediation_key="Invalid Image Tag / Image Pull Error",
    ),
    HypothesisCandidate(
        cause="Image deleted or repository missing",
        slug="image_deleted",
        prior=0.30,
        patterns=(
            _rx(r"repository does not exist", r"image not found", r"deleted"),
        ),
        default_evidence=("Registry reports image/repository missing",),
        verification_checks=(
            "Confirm repository still exists in the registry",
            "Check whether the image was garbage-collected or retagged",
        ),
        remediation_key="Invalid Image Tag / Image Pull Error",
    ),
    HypothesisCandidate(
        cause="Missing registry credentials",
        slug="missing_registry_auth",
        prior=0.20,
        patterns=(
            _rx(
                r"unauthorized",
                r"authentication required",
                r"pull access denied",
                r"imagepullsecret",
            ),
        ),
        default_evidence=("Registry authentication failure",),
        verification_checks=(
            "Check imagePullSecrets on the ServiceAccount/Pod",
            "Validate registry credentials secret contents",
            "Test registry login from a debug pod",
        ),
        remediation_key="Invalid Image Tag / Image Pull Error",
    ),
)

MISSING_SECRET_CANDIDATES: tuple[HypothesisCandidate, ...] = (
    HypothesisCandidate(
        cause="Secret not created",
        slug="secret_not_created",
        prior=0.55,
        patterns=(
            _rx(r"secret .* not found", r"does not exist"),
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
        cause="Wrong secret name or namespace",
        slug="wrong_secret_ref",
        prior=0.30,
        patterns=(
            _rx(r"wrong namespace|namespace", r"name mismatch|misreferenc"),
        ),
        default_evidence=("Secret reference may point to the wrong name/namespace",),
        verification_checks=(
            "Compare pod secretRef/volume name to existing secrets",
            "Check secret exists in the pod namespace",
        ),
        remediation_key="Missing Secret",
    ),
    HypothesisCandidate(
        cause="Incorrect volume or envFrom mount",
        slug="incorrect_secret_mount",
        prior=0.15,
        patterns=(
            _rx(r"failedmount", r"volume", r"envfrom|/etc/secrets"),
        ),
        default_evidence=("Secret mount/envFrom wiring may be incorrect",),
        verification_checks=(
            "Inspect volumeMounts and envFrom in the pod spec",
            "Confirm mount path matches what the app expects",
        ),
        remediation_key="Missing Secret",
    ),
)

APPLICATION_CRASH_CANDIDATES: tuple[HypothesisCandidate, ...] = (
    HypothesisCandidate(
        cause="Unhandled exception in application",
        slug="unhandled_exception",
        prior=0.50,
        patterns=(
            _rx(r"traceback", r"unhandled exception", r"exception:"),
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
        cause="Bad configuration",
        slug="bad_config",
        prior=0.30,
        patterns=(
            _rx(r"failed to load config", r"invalid configuration", r"\byaml:\b", r"\bjson:\b"),
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
        prior=0.20,
        patterns=(
            _rx(r"environment variable.*(?:missing|not set)", r"required .* env"),
        ),
        default_evidence=("Required environment variable may be unset",),
        verification_checks=(
            "Compare required env vars to the pod spec",
            "Check Secret/ConfigMap keys used by env references",
        ),
        remediation_key="Missing Environment Variable",
    ),
)

UNKNOWN_CANDIDATES: tuple[HypothesisCandidate, ...] = (
    HypothesisCandidate(
        cause="Application crash on startup",
        slug="startup_crash_unknown",
        prior=0.40,
        patterns=(_rx(r"traceback|panic:|fatal:|exit status|exit code"),),
        default_evidence=("Container exits during startup without a clear category",),
        verification_checks=(
            "Collect kubectl describe + previous logs",
            "Check recent deploys and config changes",
        ),
        remediation_key="Application Bug / Unhandled Exception",
    ),
    HypothesisCandidate(
        cause="Dependency unavailable",
        slug="dependency_unavailable_unknown",
        prior=0.35,
        patterns=(
            _rx(r"connection refused", r"no such host", r"i/o timeout", r"could not connect"),
        ),
        default_evidence=("Possible dependency connectivity failure",),
        verification_checks=(
            "Check dependent Service/Endpoints",
            "Verify DNS and network policies",
        ),
        remediation_key="Dependency Unavailable (DNS/Network)",
    ),
    HypothesisCandidate(
        cause="Misconfigured workload",
        slug="misconfigured_workload",
        prior=0.25,
        patterns=(_rx(r"failedmount|configmap|secret|invalid"),),
        default_evidence=("Workload configuration may be incorrect",),
        verification_checks=(
            "Review pod events for FailedMount/Failed scheduling",
            "Validate references to Secrets/ConfigMaps/volumes",
        ),
        remediation_key="Missing Secret",
    ),
)

CANDIDATES_BY_CATEGORY: dict[str, tuple[HypothesisCandidate, ...]] = {
    "OOMKilled": OOM_CANDIDATES,
    "Invalid Image": INVALID_IMAGE_CANDIDATES,
    "Missing Secret": MISSING_SECRET_CANDIDATES,
    "Application Crash": APPLICATION_CRASH_CANDIDATES,
    "Unknown": UNKNOWN_CANDIDATES,
}
