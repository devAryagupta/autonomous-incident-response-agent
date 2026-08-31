"""Evidence specification catalog for hypothesis verification."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VerificationSpec:
    supporting_evidence: tuple[str, ...]
    contradicting_evidence: tuple[str, ...]
    support_patterns: tuple[re.Pattern[str], ...]
    required_evidence: tuple[str, ...] = ()
    contradict_patterns: tuple[re.Pattern[str], ...] = ()
    required_patterns: tuple[re.Pattern[str], ...] = ()


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), flags=re.IGNORECASE)


# Keys must match Hypothesis.description values from the hypothesis engine.
VERIFICATION_SPECS: dict[str, VerificationSpec] = {
    "Memory leak": VerificationSpec(
        supporting_evidence=(
            "Memory grows continuously",
            "Repeated OOM under similar conditions",
        ),
        contradicting_evidence=(
            "Traffic/load spike explains memory pressure better",
            "OOM occurs only at startup",
        ),
        required_evidence=("Memory growth trend in logs/metrics",),
        support_patterns=(
            _rx(
                r"gradual|grew|growth|increasing|increased from",
                r"\d+\s*mi.+\d+\s*mi",
                r"heap|leak",
                r"repeated|again|multiple",
                r"back-?off restarting",
            ),
        ),
        contradict_patterns=(
            _rx(r"traffic spike|surge in requests|rps=\d{3,}|high load"),
            _rx(r"single startup|only at startup"),
        ),
        required_patterns=(
            _rx(r"memory increased from \d+\s*mi to \d+\s*mi", r"heap usage grew"),
        ),
    ),
    "Memory limit too low": VerificationSpec(
        supporting_evidence=(
            "OOM near configured memory limit",
            "Failure during startup / init",
        ),
        contradicting_evidence=(
            "Monotonic memory growth over restarts indicates leak",
            "Load spike better explains OOM",
        ),
        required_evidence=("Peak RSS close to configured memory limit",),
        support_patterns=(
            _rx(r"startup|during start|init|insufficient memory"),
            _rx(r"limit|requests/limits|memory limit|near memory limit"),
        ),
        contradict_patterns=(
            _rx(r"gradual|grew|growth|leak|increased from \d+mi to"),
            _rx(r"traffic spike|surge in requests|rps=\d{3,}"),
        ),
        required_patterns=(
            _rx(r"peak rss .* near memory limit", r"memory limit exceeded"),
        ),
    ),
    "Traffic spike": VerificationSpec(
        supporting_evidence=(
            "Request rate increased around the OOM window",
            "Load-related memory pressure",
        ),
        contradicting_evidence=("Request rate remained stable around crash window",),
        required_evidence=("Traffic/load spike signal present",),
        support_patterns=(
            _rx(r"traffic spike|surge|\brps=\d+|high load|throughput spike"),
        ),
        contradict_patterns=(
            _rx(r"request rate stable|no traffic|zero requests"),
        ),
        required_patterns=(
            _rx(r"traffic spike|surge|\brps=\d{3,}|high load|throughput spike"),
        ),
    ),
    "Secret not created": VerificationSpec(
        supporting_evidence=(
            "FailedMount reports secret not found",
            "Secret existence probe reports missing secret",
        ),
        contradicting_evidence=(
            "No FailedMount secret events",
            "Secret existence probe does not report missing secret",
        ),
        required_evidence=("Missing secret is explicitly reported",),
        support_patterns=(
            _rx(
                r"failedmount.+secret.+not found",
                r"secret .* not found",
                r"secret referenced by .* does not exist",
                r"secret missing",
            ),
        ),
        contradict_patterns=(
            _rx(r"no failedmount secret events"),
            _rx(
                r"secret existence check inconclusive",
                r"\bsecret exists\b",
                r"\bsecret is present\b",
            ),
        ),
        required_patterns=(
            _rx(r"secret .* not found", r"secret referenced by .* does not exist"),
        ),
    ),
    "Bad configuration": VerificationSpec(
        supporting_evidence=("Config parse/validation error",),
        contradicting_evidence=(
            "Specific missing-env evidence points to env issue",
            "Specific secret-missing evidence points to secret issue",
        ),
        required_evidence=("Config parser/load failure signal",),
        support_patterns=(
            _rx(r"failed to load config|invalid configuration|configuration missing|\byaml:\b|\byaml parse error\b|\bjson:\b|keyerror"),
        ),
        contradict_patterns=(
            _rx(r"missing environment variable\s+[A-Z][A-Z0-9_]*"),
            _rx(r"secret .* not found|failedmount.+secret.+not found"),
            _rx(r"traceback|unhandled exception"),
        ),
        required_patterns=(
            _rx(r"failed to load config|yaml parse error|invalid configuration|\bjson:\b"),
        ),
    ),
    "Missing environment variable": VerificationSpec(
        supporting_evidence=(
            "App reports missing required environment variable",
            "Startup throws KeyError/lookup failure for env var",
        ),
        contradicting_evidence=(
            "Secret missing / mount failure explains startup crash",
            "Primary failure is config parser syntax/format error",
        ),
        required_evidence=(
            "Missing env var name appears in logs",
            "KeyError/lookup failure references env var",
        ),
        support_patterns=(
            _rx(
                r"environment variable.*(?:missing|not set)",
                r"required .* env",
                r"keyerror",
            ),
        ),
        contradict_patterns=(
            _rx(r"secret .* not found|failedmount.+secret.+not found"),
            _rx(r"yaml parse error|failed to load config"),
        ),
        required_patterns=(
            _rx(
                r"missing environment variable\s+[A-Z][A-Z0-9_]*",
                r"environment variable .* not set",
            ),
            _rx(r"keyerror:\s*['\"]?[A-Z][A-Z0-9_]*['\"]?"),
        ),
    ),
    "Unhandled exception in application": VerificationSpec(
        supporting_evidence=("Stack trace / unhandled exception in logs",),
        contradicting_evidence=("Failure is better explained by config/env issue",),
        required_evidence=("Unhandled exception or traceback appears in logs",),
        support_patterns=(_rx(r"traceback|unhandled exception|application exception|exception:"),),
        contradict_patterns=(_rx(r"failed to load config|environment variable.*not set"),),
        required_patterns=(_rx(r"traceback|unhandled exception|application exception"),),
    ),
    "Fatal runtime error": VerificationSpec(
        supporting_evidence=("Panic/fatal runtime signal in logs",),
        contradicting_evidence=("Failure is explicitly config/env related",),
        required_evidence=("Panic/fatal runtime signal appears in logs",),
        support_patterns=(_rx(r"panic|fatal error|segmentation fault"),),
        contradict_patterns=(_rx(r"invalid configuration|missing environment variable"),),
        required_patterns=(_rx(r"panic|fatal error|segmentation fault"),),
    ),
    "Startup regression after deploy": VerificationSpec(
        supporting_evidence=("Recent deploy/revision change aligns with startup failure",),
        contradicting_evidence=("No deploy/revision-change evidence around crash window",),
        required_evidence=("Deploy/revision change signal is present",),
        support_patterns=(
            _rx(
                r"(?:new|updated)\s+revision",
                r"updated\s+replicaset",
                r"rollout\s+(?:completed|updated|changed|to|revision)",
                r"after deploy",
                r"(?:new|updated)\s+image",
                r"deployed\s+revision",
            ),
        ),
        contradict_patterns=(
            _rx(r"no recent deploy changes", r"deployment unchanged"),
        ),
        required_patterns=(
            _rx(
                r"(?:new|updated)\s+revision",
                r"updated\s+replicaset",
                r"rollout\s+(?:completed|updated|changed|to|revision)",
                r"after deploy",
                r"(?:new|updated)\s+image",
                r"deployed\s+revision",
            ),
        ),
    ),
}


def spec_for(cause: str) -> VerificationSpec:
    return VERIFICATION_SPECS.get(
        cause,
        VerificationSpec(
            supporting_evidence=("Supporting evidence for this hypothesis",),
            contradicting_evidence=(),
            required_evidence=(),
            support_patterns=(),
            contradict_patterns=(),
            required_patterns=(),
        ),
    )
