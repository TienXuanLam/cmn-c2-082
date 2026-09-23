"""
StatisticalBaselineComparator — CMN-C2-082 WebServiceAnomalyDetectionAgent

Shared utility for computing per-metric deviation scores against a baseline.
Designed for reuse elsewhere in the fleet (design in sync).

Algorithm per metric m:
  delta      = current[m] - baseline[m]
  pct_change = delta / baseline[m] * 100   (0.0 if baseline[m] == 0)
  sigma      = delta / stddev[m]            (None if stddev not provided)
"""

from __future__ import annotations

from typing import Any


REQUIRED_METRICS = ("response_time_ms", "error_rate_pct", "availability_pct")


class DeviationScore:
    """Deviation score for a single metric."""

    __slots__ = ("delta", "pct_change", "sigma")

    def __init__(self, delta: float, pct_change: float, sigma: float | None):
        self.delta = delta
        self.pct_change = pct_change
        self.sigma = sigma

    def to_dict(self) -> dict[str, float | None]:
        return {"delta": self.delta, "pct_change": self.pct_change, "sigma": self.sigma}


class StatisticalBaselineComparator:
    """Compute per-metric deviation scores between current metrics and a baseline.

    Usage:
        comparator = StatisticalBaselineComparator(baseline, stddev)
        scores = comparator.compare(current_metrics)
    """

    def __init__(self, baseline: dict[str, Any], stddev: dict[str, Any] | None = None):
        """
        Args:
            baseline: dict of {metric_name: float} for baseline values.
                      Required keys: response_time_ms, error_rate_pct, availability_pct.
            stddev:   Optional dict of {metric_name: float} for per-metric standard deviations.
                      When provided, sigma = delta / stddev[m] is computed.
        """
        self._baseline = baseline
        self._stddev = stddev or {}

    def compare(self, current: dict[str, Any]) -> dict[str, DeviationScore]:
        """Compute deviation scores for all required metrics.

        Args:
            current: dict of {metric_name: float} for current observed values.

        Returns:
            dict of {metric_name: DeviationScore}

        Raises:
            KeyError: if a required metric is missing from current or baseline.
        """
        scores: dict[str, DeviationScore] = {}
        for metric in REQUIRED_METRICS:
            baseline_val = float(self._baseline[metric])
            current_val = float(current[metric])

            delta = current_val - baseline_val
            pct_change = (delta / baseline_val * 100.0) if baseline_val != 0.0 else 0.0

            stddev_val = self._stddev.get(metric)
            sigma: float | None = None
            if stddev_val is not None and float(stddev_val) > 0:
                sigma = delta / float(stddev_val)

            scores[metric] = DeviationScore(
                delta=round(delta, 4),
                pct_change=round(pct_change, 4),
                sigma=round(sigma, 4) if sigma is not None else None,
            )
        return scores

    @staticmethod
    def scores_to_dict(scores: dict[str, DeviationScore]) -> dict[str, dict[str, float | None]]:
        """Serialise scores to a plain dict (JSON-safe)."""
        return {metric: score.to_dict() for metric, score in scores.items()}
