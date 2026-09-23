"""Cross-sectional preprocessing for raw factor values.

Every factor is processed inside one trading day only: winsorize, unify
direction, standardize, then neutralize against the decision-time industry
classification. Missing values are never filled with zero; symbols without a
raw value stay missing and are handled by the composite score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean, median, pstdev
from typing import Mapping, Sequence

from ashare_multifactor.factors.definitions import FactorSpec

PREPROCESS_VERSION = "preprocess-v1"
MAD_TO_SIGMA = 1.4826
WINSOR_SIGMA = 3.0
MIN_INDUSTRY_GROUP = 3
NEUTRALIZE_INDUSTRY = "industry"
NEUTRALIZE_NONE = "none"


@dataclass(frozen=True)
class ProcessedFactor:
    """One factor after winsorizing, direction unification, and neutralization."""

    name: str
    category: str
    direction: int
    version: str
    raw: dict[str, float]
    values: dict[str, float]
    missing: tuple[str, ...]
    lower: float | None
    upper: float | None
    neutralized: str


def process_factor(
    spec: FactorSpec,
    raw_values: Mapping[str, float | None],
    industries: Mapping[str, str] | None = None,
) -> ProcessedFactor:
    """Process one raw factor cross-section."""
    raw: dict[str, float] = {}
    missing: list[str] = []
    for symbol in sorted(raw_values):
        value = raw_values[symbol]
        if value is None or not math.isfinite(float(value)):
            missing.append(symbol)
            continue
        raw[symbol] = float(value)

    if not raw:
        return ProcessedFactor(
            name=spec.name,
            category=spec.category,
            direction=spec.direction,
            version=spec.version,
            raw={},
            values={},
            missing=tuple(missing),
            lower=None,
            upper=None,
            neutralized=NEUTRALIZE_NONE,
        )

    lower, upper = winsorize_bounds(list(raw.values()))
    clipped = {symbol: min(max(value, lower), upper) for symbol, value in raw.items()}
    directed = {symbol: value * spec.direction for symbol, value in clipped.items()}
    standardized = standardize(directed)

    if industries:
        values = neutralize_by_industry(standardized, industries)
        neutralized = NEUTRALIZE_INDUSTRY
    else:
        values = standardized
        neutralized = NEUTRALIZE_NONE

    return ProcessedFactor(
        name=spec.name,
        category=spec.category,
        direction=spec.direction,
        version=spec.version,
        raw=raw,
        values=values,
        missing=tuple(missing),
        lower=lower,
        upper=upper,
        neutralized=neutralized,
    )


def winsorize_bounds(values: Sequence[float]) -> tuple[float, float]:
    """Return MAD-based clipping bounds; identical values are left untouched."""
    center = median(values)
    deviations = [abs(value - center) for value in values]
    mad = median(deviations)
    if mad <= 0:
        return min(values), max(values)
    spread = WINSOR_SIGMA * MAD_TO_SIGMA * mad
    return center - spread, center + spread


def standardize(values: Mapping[str, float]) -> dict[str, float]:
    """Z-score a cross-section; a degenerate spread maps to zero."""
    if not values:
        return {}
    numbers = list(values.values())
    if len(numbers) < 2:
        return {symbol: 0.0 for symbol in values}
    spread = pstdev(numbers)
    if spread <= 0:
        return {symbol: 0.0 for symbol in values}
    center = fmean(numbers)
    return {symbol: (value - center) / spread for symbol, value in values.items()}


def neutralize_by_industry(
    values: Mapping[str, float],
    industries: Mapping[str, str],
    *,
    min_group: int = MIN_INDUSTRY_GROUP,
) -> dict[str, float]:
    """Demean within industry groups, falling back to the cross-section mean."""
    overall = fmean(values.values()) if values else 0.0
    groups: dict[str, list[str]] = {}
    for symbol in values:
        groups.setdefault(str(industries.get(symbol) or "UNKNOWN"), []).append(symbol)

    result: dict[str, float] = {}
    for members in groups.values():
        reference = fmean(values[symbol] for symbol in members) if len(members) >= min_group else overall
        for symbol in members:
            result[symbol] = values[symbol] - reference
    return result


def preprocess_version() -> str:
    """Version tag recorded in run manifests."""
    return PREPROCESS_VERSION
