"""Extract scoped CrashLoopBackOff signals from logs/events/metrics."""

from __future__ import annotations
# import re is a regular expression module that is used to search for patterns in text.
import re
from dataclasses import dataclass, field 
# dataclass is a decorator that is used to create a class that is used to store data.
# similar to the all args constructor in the class.
# field is a decorator that is used to create a field that is used to store data.
# similar to the __init__ method in the class.
from typing import Literal

from incident_agent.contracts import Evidence, Observations

EvidenceSource = Literal["logs", "events", "describe", "metrics", "config", "other"]
# private variables are prefixed with an underscore.
_EXIT_CODE_REGEX = re.compile(
    r"(?:exit(?:ed)?(?:\s+with)?(?:\s+status|\s+code)?|ExitCode)\s*[:=]?\s*(\d+)",
    re.IGNORECASE,
)
# private variables are prefixed with an underscore.
_REASON_REGEX = re.compile(
    r"(?:reason|lastState|terminated)\s*[:=]\s*([A-Za-z0-9_]+)",
    re.IGNORECASE,
)

_OOM_REGEX = re.compile(r"oomkilled|memory limit exceeded|out of memory|container killed", re.IGNORECASE)
_CONFIG_REGEX = re.compile(
    r"missing environment variable|environment variable.*(?:missing|not set)|"
    r"keyerror|configuration missing|yaml parse error|invalid configuration|"
    r"secret missing|\bsecret\b.*\bnot found\b|failedmount",
    re.IGNORECASE,
)
_APP_FAILURE_REGEX = re.compile(
    r"unhandled exception|traceback|panic\b|fatal error|application exception",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True) 
# frozen=True means that the class is immutable. now they are the readonly values.
# slots=True means that the class is memory efficient. you can't add new attributes to the class.
class ExtractedEvidence:
    """Normalized signals the diagnosis engine scores against."""

    items: tuple[Evidence, ...] = ()
    exit_codes: tuple[int, ...] = ()
    reasons: tuple[str, ...] = ()
    has_oomkilled: bool = False
    has_invalid_configuration: bool = False
    has_application_failure: bool = False
    has_generic_failure: bool = False


@dataclass
# private class is prefixed with an underscore.

class _Accumulator:
    items: list[Evidence] = field(default_factory=list)
    exit_codes: list[int] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    has_oomkilled: bool = False
    has_invalid_configuration: bool = False
    has_application_failure: bool = False
    has_generic_failure: bool = False
    _seen_text: set[str] = field(default_factory=set)
    # add 
    def add(self, source: EvidenceSource, text: str) -> None:
        # strip is a method that is used to remove the whitespace from the text. it is used to remove the leading and trailing whitespace.
        cleaned = text.strip()
        # if the text is empty or the text is already in the seen text, return.
        if not cleaned or cleaned in self._seen_text:
            # if the text is empty or the text is already in the seen text, return.
            return
        self._seen_text.add(cleaned)
        self.items.append(Evidence(source=source, text=cleaned))


def _scan_line(acc: _Accumulator, source: EvidenceSource, line: str) -> None:
    for match in _EXIT_CODE_REGEX.finditer(line):
        # finditer is a method that is used to find all the matches in the line.
        # group(1) is the first group of the match.
        
        code = int(match.group(1))

        if code not in acc.exit_codes:
            acc.exit_codes.append(code)
        acc.add(source, f"Container terminated with exit code {code}")
        if code != 0:
            acc.has_generic_failure = True

    for match in _REASON_REGEX.finditer(line):
        reason = match.group(1)
        if reason not in acc.reasons:
            acc.reasons.append(reason)
        if reason and reason.lower() not in {"completed", "success"}:
            acc.has_generic_failure = True

    if _OOM_REGEX.search(line):
        acc.has_oomkilled = True
        acc.add(source, "OOMKilled event detected")
        if "memory limit exceeded" in line.lower():
            acc.add(source, "Memory limit exceeded")
        if "container killed" in line.lower():
            acc.add(source, "Container killed")

    if _CONFIG_REGEX.search(line):
        acc.has_invalid_configuration = True
        lower = line.lower()
        if "missing environment variable" in lower or "environment variable" in lower and "not set" in lower:
            acc.add(source, "Missing environment variable")
        elif "keyerror" in lower:
            acc.add(source, "KeyError")
        elif "configuration missing" in lower:
            acc.add(source, "Configuration missing")
        elif "yaml parse error" in lower:
            acc.add(source, "YAML parse error")
        elif "invalid configuration" in lower:
            acc.add(source, "Invalid configuration")
        elif "failedmount" in lower:
            acc.add(source, "FailedMount")
        elif "secret missing" in lower or ("secret" in lower and "not found" in lower):
            acc.add(source, "Secret missing")

    if _APP_FAILURE_REGEX.search(line):
        acc.has_application_failure = True
        lower = line.lower()
        if "traceback" in lower:
            acc.add(source, "Traceback")
        elif "panic" in lower:
            acc.add(source, "panic")
        elif "fatal error" in lower:
            acc.add(source, "fatal error")
        elif "application exception" in lower:
            acc.add(source, "application exception")
        elif "unhandled exception" in lower:
            acc.add(source, "Unhandled exception")


def _scan_extra(acc: _Accumulator, extra: dict) -> None:
    """Optional structured hints in observations.extra (describe / lastState)."""
    for key in ("exit_code", "exitCode", "container_exit_code"):
        raw = extra.get(key)
        if isinstance(raw, int) and raw not in acc.exit_codes:
            acc.exit_codes.append(raw)
            acc.add("describe", f"Container terminated with exit code {raw}")

    for key in ("reason", "last_state_reason", "terminated_reason"):
        raw = extra.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        if raw not in acc.reasons:
            acc.reasons.append(raw)
        if raw.lower() == "oomkilled":
            acc.has_oomkilled = True
            acc.add("describe", "OOMKilled event detected")

    restarts = extra.get("restart_count")
    if isinstance(restarts, int) and restarts > 0:
        acc.add("describe", f"{restarts} restarts")

    memory_limit = extra.get("memory_limit")
    if isinstance(memory_limit, str) and memory_limit.strip():
        acc.add("describe", f"memory limit {memory_limit.strip()}")

    metrics = extra.get("metrics")
    if isinstance(metrics, dict):
        _scan_metrics_dict(acc, metrics)


def _scan_metrics_dict(acc: _Accumulator, metrics: dict) -> None:
    for key, value in metrics.items():
        if isinstance(value, dict):
            _scan_metrics_dict(acc, value)
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    _scan_line(acc, "metrics", item)
            continue
        if isinstance(value, str):
            _scan_line(acc, "metrics", f"{key}: {value}")


def extract_evidence(observations: Observations) -> ExtractedEvidence:
    acc = _Accumulator()
    for line in observations.events:
        _scan_line(acc, "events", line)
    for line in observations.logs:
        _scan_line(acc, "logs", line)
    _scan_extra(acc, observations.extra)

    if 137 in acc.exit_codes or any(r.lower() == "oomkilled" for r in acc.reasons):
        acc.has_oomkilled = True
        if 137 in acc.exit_codes:
            acc.add("other", "Exit code 137 indicates SIGKILL / OOMKilled")

    return ExtractedEvidence(
        items=tuple(acc.items),
        exit_codes=tuple(acc.exit_codes),
        reasons=tuple(acc.reasons),
        has_oomkilled=acc.has_oomkilled,
        has_invalid_configuration=acc.has_invalid_configuration,
        has_application_failure=acc.has_application_failure,
        has_generic_failure=acc.has_generic_failure,
    )
