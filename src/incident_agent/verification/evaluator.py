"""Deterministic metric trend analyzers (pure, no I/O)."""

from __future__ import annotations


def detect_monotonically_increasing(
    datapoints: list[float],
    threshold_ratio: float = 0.8,
) -> bool:
    """
    True when the series trends steadily upward without resetting.

    Compares consecutive pairs: at least ``threshold_ratio`` of steps must be
    strictly increasing, and the overall end must exceed the start.
    Indicates continuous memory growth (leak-shaped), not a one-shot spike.
    """
    if len(datapoints) < 2:
        return False
    if datapoints[-1] <= datapoints[0]:
        return False

    steps = len(datapoints) - 1
    increasing = sum(
        1 for left, right in zip(datapoints, datapoints[1:], strict=False) if right > left
    )
    return (increasing / steps) >= threshold_ratio


def detect_step_increase_or_spike(datapoints: list[float]) -> bool:
    """
    True when a sudden jump dominates the series (external-load shaped).

    A step/spike is present when the largest single relative jump is large
    compared to the median step, or when peak/baseline exceeds 3×.
    """
    if len(datapoints) < 2:
        return False

    baseline = min(datapoints)
    peak = max(datapoints)
    if baseline <= 0:
        return peak > 0 and (peak >= 3.0 * max(datapoints[0], 1.0))

    if peak >= 3.0 * baseline:
        return True

    deltas = [
        right - left
        for left, right in zip(datapoints, datapoints[1:], strict=False)
    ]
    if not deltas:
        return False
    max_jump = max(deltas)
    positive = [d for d in deltas if d > 0]
    if not positive:
        return False
    median_step = sorted(positive)[len(positive) // 2]
    if median_step <= 0:
        return max_jump > 0
    return max_jump >= 3.0 * median_step
