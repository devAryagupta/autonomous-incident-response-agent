"""Extract failure signals from observation logs/events (no live I/O)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from incident_agent.contracts import Evidence, Observations

EvidenceSource = Literal["logs", "events", "describe", "metrics", "config", "other"]

_EXIT_CODE_RE = re.compile(
    r"(?:exit(?:ed)?(?:\s+with)?(?:\s+status|\s+code)?|ExitCode)\s*[:=]?\s*(\d+)",
    re.IGNORECASE,
)
_REASON_RE = re.compile(
    r"(?:reason|lastState|terminated)\s*[:=]\s*([A-Za-z0-9_]+)",
    re.IGNORECASE,
)

_OOM_RE = re.compile(
    r"oomkilled|killed due to (?:oom|memory)|out of memory",
    re.IGNORECASE,
)
_IMAGE_PULL_RE = re.compile(
    r"errimagepull|imagepullbackoff|manifest unknown|failed to pull image|"
    r"pull access denied|authentication required|image not found",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"\bsecret\b.*\bnot found\b|failedmount.*\bsecret\b|no such file.*secrets",
    re.IGNORECASE,
)
_APP_CRASH_RE = re.compile(
    r"traceback|unhandled exception|panic:|segmentation fault",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ExtractedEvidence:
    """Normalized signals the diagnosis engine scores against."""

    items: tuple[Evidence, ...] = ()
    exit_codes: tuple[int, ...] = ()
    reasons: tuple[str, ...] = ()
    has_oomkilled: bool = False
    has_image_pull_failure: bool = False
    has_missing_secret: bool = False
    has_application_crash: bool = False


@dataclass
class _Accumulator:
    items: list[Evidence] = field(default_factory=list)
    exit_codes: list[int] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    has_oomkilled: bool = False
    has_image_pull_failure: bool = False
    has_missing_secret: bool = False
    has_application_crash: bool = False
    _seen_text: set[str] = field(default_factory=set)

    def add(self, source: EvidenceSource, text: str) -> None:
        cleaned = text.strip()
        if not cleaned or cleaned in self._seen_text:
            return
        self._seen_text.add(cleaned)
        self.items.append(Evidence(source=source, text=cleaned))


def _scan_line(acc: _Accumulator, source: EvidenceSource, line: str) -> None:
    for match in _EXIT_CODE_RE.finditer(line):
        code = int(match.group(1))
        if code not in acc.exit_codes:
            acc.exit_codes.append(code)
        acc.add(source, f"Container terminated with exit code {code}")

    for match in _REASON_RE.finditer(line):
        reason = match.group(1)
        if reason not in acc.reasons:
            acc.reasons.append(reason)

    if _OOM_RE.search(line):
        acc.has_oomkilled = True
        acc.add(source, "OOMKilled event detected")

    if _IMAGE_PULL_RE.search(line):
        acc.has_image_pull_failure = True
        lower = line.lower()
        if "errimagepull" in lower:
            acc.add(source, "ErrImagePull event detected")
        elif "imagepullbackoff" in lower:
            acc.add(source, "ImagePullBackOff event detected")
        elif "manifest unknown" in lower:
            acc.add(source, "Image manifest unknown")
        else:
            acc.add(source, "Image pull failure detected")

    if _SECRET_RE.search(line):
        acc.has_missing_secret = True
        acc.add(source, "Missing secret / FailedMount secret detected")

    if _APP_CRASH_RE.search(line):
        acc.has_application_crash = True
        lower = line.lower()
        if "traceback" in lower:
            acc.add(source, "Application traceback detected")
        elif "panic:" in lower:
            acc.add(source, "Application panic detected")
        elif "unhandled exception" in lower:
            acc.add(source, "Unhandled exception detected")
        else:
            acc.add(source, "Application crash pattern detected")


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

    # Non-zero exit without a more specific cause still supports Application Crash.
    non_zero = [c for c in acc.exit_codes if c not in (0, 137)]
    if non_zero and not (
        acc.has_oomkilled or acc.has_image_pull_failure or acc.has_missing_secret
    ):
        acc.has_application_crash = True

    return ExtractedEvidence(
        items=tuple(acc.items),
        exit_codes=tuple(acc.exit_codes),
        reasons=tuple(acc.reasons),
        has_oomkilled=acc.has_oomkilled,
        has_image_pull_failure=acc.has_image_pull_failure,
        has_missing_secret=acc.has_missing_secret,
        has_application_crash=acc.has_application_crash,
    )
