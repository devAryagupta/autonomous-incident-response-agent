"""Classify a memory time series: leak-shaped vs repeated startup allocation."""

from __future__ import annotations

_CYCLE_DROP_FRACTION = 0.4
_NEAR_LIMIT_FRACTION = 0.9
_SIMILAR_PEAK_FRACTION = 0.10
_MIN_RISES_FOR_LEAK = 2
_MIN_SAMPLES_FOR_SUSTAINED = 3


def restart_cycles(series: list[float]) -> list[list[float]]:
    """Split a CrashLoop sawtooth where usage collapses after a peak."""
    if not series:
        return []
    cycles: list[list[float]] = []
    current = [series[0]]
    peak = series[0]
    for value in series[1:]:
        if peak > 0 and value < peak and value <= _CYCLE_DROP_FRACTION * peak:
            cycles.append(current)
            current = [value]
            peak = value
            continue
        current.append(value)
        peak = max(peak, value)
    cycles.append(current)
    return cycles


def cycle_peaks(series: list[float]) -> list[float]:
    return [max(cycle) for cycle in restart_cycles(series) if cycle]


def rising_cycle_peaks(peaks: list[float]) -> bool:
    """Cycle N peak > cycle N-1, repeated (at least two rises)."""
    if len(peaks) < _MIN_RISES_FOR_LEAK + 1:
        return False
    rises = sum(1 for prior, nxt in zip(peaks[:-1], peaks[1:], strict=True) if nxt > prior)
    return rises >= _MIN_RISES_FOR_LEAK and peaks[-1] > peaks[0]


def similar_cycle_peaks(peaks: list[float]) -> bool:
    if len(peaks) < 2:
        return False
    high = max(peaks)
    if high <= 0:
        return True
    return (high - min(peaks)) / high <= _SIMILAR_PEAK_FRACTION


def sustained_growth(series: list[float]) -> bool:
    if len(series) < _MIN_SAMPLES_FOR_SUSTAINED:
        return False
    positive = sum(
        1 for prior, nxt in zip(series[:-1], series[1:], strict=True) if nxt > prior
    )
    return positive >= 2 and max(series) > series[0]


def near_limit(peak_mi: float, limit_mi: float | None) -> bool:
    if limit_mi is None or limit_mi <= 0:
        return False
    return peak_mi >= _NEAR_LIMIT_FRACTION * limit_mi


def summarize_memory_usage(
    series: list[float],
    *,
    limit_mi: float | None = None,
) -> str:
    """Observation text for leak verification. Generic climb is not leak evidence."""
    if len(series) < 2:
        return "Insufficient memory samples"
    start_mi = series[0]
    peak_mi = max(series)
    peaks = cycle_peaks(series)
    if rising_cycle_peaks(peaks):
        joined = " → ".join(format_mi(peak) for peak in peaks)
        return f"Cycle peaks rose {joined}"
    if similar_cycle_peaks(peaks):
        typical = sum(peaks) / len(peaks)
        return f"Repeated startup allocation (peaks ~{format_mi(typical)})"
    if sustained_growth(series) and near_limit(peak_mi, limit_mi):
        return (
            f"Sustained memory growth from {format_mi(start_mi)} to {format_mi(peak_mi)}"
        )
    if peak_mi > start_mi:
        return f"Memory increased from {format_mi(start_mi)} to {format_mi(peak_mi)}"
    return f"Memory usage stable (~{format_mi(start_mi)}-{format_mi(peak_mi)})"


def format_mi(value: float) -> str:
    return f"{int(round(value))}Mi"
