"""Expected-evidence specs for existing hypothesis causes (no new categories)."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VerificationSpec:
    expected_evidence: tuple[str, ...]
    support_patterns: tuple[re.Pattern[str], ...]
    contradict_patterns: tuple[re.Pattern[str], ...] = ()


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), flags=re.IGNORECASE)


# Keys must match Hypothesis.description values from the hypothesis engine.
VERIFICATION_SPECS: dict[str, VerificationSpec] = {
    "Memory leak": VerificationSpec(
        expected_evidence=(
            "Memory grows continuously",
            "Repeated OOM under similar conditions",
        ),
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
            _rx(r"traffic spike|surge in requests|\brps\b|\bqps\b"),
            _rx(r"single startup|only at startup"),
        ),
    ),
    "Memory limit too low": VerificationSpec(
        expected_evidence=(
            "OOM near configured memory limit",
            "Failure during startup / init",
        ),
        support_patterns=(
            _rx(r"startup|during start|init|insufficient memory"),
            _rx(r"limit|requests/limits|memory limit"),
        ),
        contradict_patterns=(
            _rx(r"gradual|grew|growth|leak|increased from"),
            _rx(r"traffic spike|surge in requests|\brps\b|\bqps\b"),
        ),
    ),
    "Traffic spike": VerificationSpec(
        expected_evidence=(
            "Request rate increased around the OOM window",
            "Load-related memory pressure",
        ),
        support_patterns=(
            _rx(r"traffic|spike|surge|\brps\b|\bqps\b|throughput|high load"),
        ),
        contradict_patterns=(
            _rx(r"idle|no traffic|zero requests"),
        ),
    ),
    "Wrong image tag": VerificationSpec(
        expected_evidence=("Image manifest/tag cannot be resolved",),
        support_patterns=(_rx(r"manifest unknown", r"wrong tag|invalid tag"),),
        contradict_patterns=(_rx(r"unauthorized|authentication required|pull access denied"),),
    ),
    "Image deleted or repository missing": VerificationSpec(
        expected_evidence=("Registry reports repository/image missing",),
        support_patterns=(_rx(r"repository does not exist", r"image not found"),),
        contradict_patterns=(_rx(r"unauthorized|authentication required"),),
    ),
    "Missing registry credentials": VerificationSpec(
        expected_evidence=("Registry authentication / pull access failure",),
        support_patterns=(
            _rx(r"unauthorized|authentication required|pull access denied|imagepullsecret"),
        ),
        contradict_patterns=(_rx(r"manifest unknown"),),
    ),
    "Secret not created": VerificationSpec(
        expected_evidence=("Referenced secret does not exist",),
        support_patterns=(_rx(r"secret .* not found", r"does not exist"),),
        contradict_patterns=(_rx(r"\bsecret exists\b", r"\bsecret is present\b"),),
    ),
    "Wrong secret name or namespace": VerificationSpec(
        expected_evidence=("Secret reference points to wrong name/namespace",),
        support_patterns=(_rx(r"wrong namespace|namespace mismatch|name mismatch|misreferenc"),),
        contradict_patterns=(),
    ),
    "Incorrect volume or envFrom mount": VerificationSpec(
        expected_evidence=("Secret volume/envFrom mount path is incorrect",),
        support_patterns=(
            _rx(r"envfrom|wrong mount path|cannot open .*secrets|no such file.*secrets"),
        ),
        contradict_patterns=(_rx(r"secret .* not found"),),
    ),
    "Unhandled exception in application": VerificationSpec(
        expected_evidence=("Stack trace / unhandled exception in logs",),
        support_patterns=(_rx(r"traceback|unhandled exception|exception:"),),
        contradict_patterns=(_rx(r"failed to load config|environment variable.*not set"),),
    ),
    "Bad configuration": VerificationSpec(
        expected_evidence=("Config parse/validation error",),
        support_patterns=(
            _rx(r"failed to load config|invalid configuration|\byaml:\b|\bjson:\b"),
        ),
        contradict_patterns=(_rx(r"traceback|unhandled exception"),),
    ),
    "Missing environment variable": VerificationSpec(
        expected_evidence=("Required environment variable is unset",),
        support_patterns=(
            _rx(r"environment variable.*(?:missing|not set)|required .* env"),
        ),
        contradict_patterns=(_rx(r"traceback \(most recent"),),
    ),
    "Application crash on startup": VerificationSpec(
        expected_evidence=("Process exits during startup",),
        support_patterns=(_rx(r"traceback|panic:|fatal:|exit status|exit code"),),
    ),
    "Dependency unavailable": VerificationSpec(
        expected_evidence=("Dependency connectivity / DNS failure",),
        support_patterns=(
            _rx(r"connection refused|no such host|i/o timeout|could not connect"),
        ),
    ),
    "Misconfigured workload": VerificationSpec(
        expected_evidence=("Workload references missing or invalid config objects",),
        support_patterns=(_rx(r"failedmount|configmap|secret .* not found|invalid"),),
    ),
}


def spec_for(cause: str) -> VerificationSpec:
    return VERIFICATION_SPECS.get(
        cause,
        VerificationSpec(
            expected_evidence=("Supporting evidence for this hypothesis",),
            support_patterns=(),
            contradict_patterns=(),
        ),
    )
