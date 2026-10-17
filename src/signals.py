"""Descriptive surveillance signals, independent of statistical significance."""

import math
from dataclasses import dataclass

from src.anomalies import AnomalyResult, AnomalyScan


@dataclass(frozen=True)
class PreliminarySignal:
    comparison: AnomalyResult
    threshold_pp: float

    @property
    def statistically_supported(self):
        result = self.comparison
        return (result.status.startswith("potential_") and result.adjusted_p_value is not None
                and result.adjusted_p_value < result.alpha)


def surveillance_signals(scan: AnomalyScan, threshold_pp: float = 10.0) -> tuple[PreliminarySignal, ...]:
    if isinstance(threshold_pp, bool) or not isinstance(threshold_pp, (float, int)) or not math.isfinite(threshold_pp) or not 0 <= threshold_pp <= 100:
        raise ValueError("Signal threshold must be a finite number between 0 and 100 pp")
    signals = []
    for result in scan.results:
        minimum = result.current.minimum_sample_size
        change = abs(result.difference_pp) if result.difference_pp is not None else None
        if (result.current.n_tested >= minimum and result.baseline.n_tested >= minimum
                and change is not None and change > threshold_pp
                and not math.isclose(change, threshold_pp, rel_tol=0, abs_tol=1e-12)):
            signals.append(PreliminarySignal(result, threshold_pp))
    return tuple(sorted(signals, key=lambda signal: abs(signal.comparison.difference_pp), reverse=True))
