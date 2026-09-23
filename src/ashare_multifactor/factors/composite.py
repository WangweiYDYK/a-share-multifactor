"""Category-equal-weight composite scoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ashare_multifactor.factors.preprocess import ProcessedFactor

COMPOSITE_VERSION = "category-equal-weight-v1"
METHOD_CATEGORY_EQUAL_WEIGHT = "category_equal_weight"
MIN_FACTORS_PER_SYMBOL = 2


@dataclass(frozen=True)
class CompositeScore:
    """Composite score plus the weights and exclusions that produced it."""

    method: str
    version: str
    values: dict[str, float]
    ranks: dict[str, int]
    weights: dict[str, float]
    excluded: dict[str, str]
    category_weights: dict[str, float]


def category_weights(factors: Sequence[ProcessedFactor]) -> dict[str, float]:
    """Split one unit evenly across categories, then evenly inside a category."""
    categories: dict[str, list[str]] = {}
    for factor in factors:
        categories.setdefault(factor.category, []).append(factor.name)

    if not categories:
        return {}

    weights: dict[str, float] = {}
    per_category = 1.0 / len(categories)
    for names in categories.values():
        for name in names:
            weights[name] = per_category / len(names)
    return weights


def composite_score(
    factors: Sequence[ProcessedFactor],
    *,
    min_factors: int = MIN_FACTORS_PER_SYMBOL,
) -> CompositeScore:
    """Combine processed factors, renormalizing over the available ones."""
    weights = category_weights(factors)
    symbols = sorted({symbol for factor in factors for symbol in factor.values})

    values: dict[str, float] = {}
    excluded: dict[str, str] = {}
    for symbol in symbols:
        available = {
            factor.name: factor.values[symbol]
            for factor in factors
            if symbol in factor.values
        }
        if len(available) < min_factors:
            excluded[symbol] = "factor_coverage_too_low"
            continue
        total_weight = sum(weights[name] for name in available)
        if total_weight <= 0:
            excluded[symbol] = "factor_weights_unavailable"
            continue
        values[symbol] = sum(
            weights[name] * value / total_weight for name, value in available.items()
        )

    ranked = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    ranks = {symbol: position for position, (symbol, _) in enumerate(ranked, start=1)}

    category_totals: dict[str, float] = {}
    for factor in factors:
        category_totals[factor.category] = (
            category_totals.get(factor.category, 0.0) + weights[factor.name]
        )

    return CompositeScore(
        method=METHOD_CATEGORY_EQUAL_WEIGHT,
        version=COMPOSITE_VERSION,
        values=values,
        ranks=ranks,
        weights=weights,
        excluded=excluded,
        category_weights=category_totals,
    )
