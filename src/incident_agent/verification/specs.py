"""Expected-evidence specs for hypothesis causes."""

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
            _rx(r"traffic spike|surge in requests|rps=\d{3,}|high load"),
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
            _rx(r"limit|requests/limits|memory limit|near memory limit"),
        ),
        contradict_patterns=(
            _rx(r"gradual|grew|growth|leak|increased from \d+mi to"),
            _rx(r"traffic spike|surge in requests|rps=\d{3,}"),
        ),
    ),
    "Traffic spike": VerificationSpec(
        expected_evidence=(
            "Request rate increased around the OOM window",
            "Load-related memory pressure",
        ),
        support_patterns=(
            _rx(r"traffic spike|surge|\brps=\d+|high load|throughput spike"),
        ),
        contradict_patterns=(
            _rx(r"request rate stable|no traffic|zero requests"),
        ),
    ),
    "Secret not created": VerificationSpec(
        expected_evidence=("Referenced secret does not exist",),
        support_patterns=(_rx(r"secret .* not found", r"secret missing", r"failedmount"),),
        contradict_patterns=(_rx(r"\bsecret exists\b", r"\bsecret is present\b"),),
    ),
    "Bad configuration": VerificationSpec(
        expected_evidence=("Config parse/validation error",),
        support_patterns=(
            _rx(r"failed to load config|invalid configuration|configuration missing|\byaml:\b|\byaml parse error\b|\bjson:\b|keyerror"),
        ),
        contradict_patterns=(_rx(r"traceback|unhandled exception"),),
    ),
    "Missing environment variable": VerificationSpec(
        expected_evidence=("Required environment variable is unset",),
        support_patterns=(
            _rx(r"environment variable.*(?:missing|not set)|required .* env|keyerror"),
        ),
        contradict_patterns=(_rx(r"traceback \(most recent"),),
    ),
    "Unhandled exception in application": VerificationSpec(
        expected_evidence=("Stack trace / unhandled exception in logs",),
        support_patterns=(_rx(r"traceback|unhandled exception|application exception|exception:"),),
        contradict_patterns=(_rx(r"failed to load config|environment variable.*not set"),),
    ),
    "Fatal runtime error": VerificationSpec(
        expected_evidence=("Panic/fatal runtime signal in logs",),
        support_patterns=(_rx(r"panic|fatal error|segmentation fault"),),
        contradict_patterns=(_rx(r"invalid configuration|missing environment variable"),),
    ),
    "Startup regression after deploy": VerificationSpec(
        expected_evidence=("Crash loop started after recent startup-path change",),
        support_patterns=(_rx(r"startup|back-?off restarting|crashloopbackoff"),),
        contradict_patterns=(),
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
