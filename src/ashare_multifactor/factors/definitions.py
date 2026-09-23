"""Raw factor definitions for the first monthly multifactor slice.

Factors are computed from a fixed backward-adjusted close series that has
already been truncated at the decision ``as_of`` time. Each security uses its
own observed trading days: suspended days are not imputed, and the resulting
coverage is reported by the preprocessing layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import pstdev
from typing import Mapping, Sequence

FACTOR_VERSION = "factor-definitions-v1"
TRADING_DAYS_PER_YEAR = 252
MIN_VOLATILITY_OBSERVATIONS = 20


@dataclass(frozen=True)
class FactorSpec:
    """One raw factor, before cross-sectional preprocessing."""

    name: str
    category: str
    direction: int
    lookback: int
    version: str = FACTOR_VERSION


def default_factor_specs() -> tuple[FactorSpec, ...]:
    """The three-factor slice defined by the implementation document."""
    return (
        FactorSpec("reversal_20d", "reversal", 1, 20),
        FactorSpec("momentum_60d", "momentum", 1, 60),
        FactorSpec("low_vol_60d", "low_volatility", -1, 60),
    )


def factor_version(specs: Sequence[FactorSpec]) -> str:
    """Return a stable version string for a factor set."""
    versions = sorted({spec.version for spec in specs})
    return "|".join(versions)


def compute_raw_factor(spec: FactorSpec, closes: Sequence[float]) -> float | None:
    """Return the raw factor value, or None when history is insufficient."""
    window = _window(closes, spec.lookback)
    if window is None:
        return None

    if spec.name == "reversal_20d":
        return -(window[-1] / window[0] - 1.0)
    if spec.name == "momentum_60d":
        return window[-1] / window[0] - 1.0
    if spec.name == "low_vol_60d":
        returns = [window[index + 1] / window[index] - 1.0 for index in range(len(window) - 1)]
        if len(returns) < MIN_VOLATILITY_OBSERVATIONS:
            return None
        return pstdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)
    raise KeyError(f"Unknown factor {spec.name!r}")


def compute_raw_factors(
    specs: Sequence[FactorSpec],
    closes_by_symbol: Mapping[str, Sequence[float]],
) -> dict[str, dict[str, float | None]]:
    """Compute every requested factor for every supplied security."""
    return {
        spec.name: {
            symbol: compute_raw_factor(spec, closes)
            for symbol, closes in closes_by_symbol.items()
        }
        for spec in specs
    }


def _window(closes: Sequence[float], lookback: int) -> list[float] | None:
    if lookback < 1 or len(closes) < lookback + 1:
        return None
    window = [float(value) for value in closes[-(lookback + 1) :]]
    if any(not math.isfinite(value) or value <= 0 for value in window):
        return None
    return window
