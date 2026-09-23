"""Target portfolio construction for the first no-buffer baseline.

Selection is a pure cross-sectional ranking inside the decision-time universe.
Weights start from equal weight, then respect the per-name and per-industry
caps. Any weight that cannot be allocated stays in cash and is recorded in
``constraint_state`` instead of being forced into other names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ashare_multifactor.factors.composite import CompositeScore

PORTFOLIO_VERSION = "target-portfolio-v1"
WEIGHT_EPSILON = 1e-12


@dataclass(frozen=True)
class PortfolioConstraints:
    top_n: int = 30
    max_weight: float = 0.04
    max_industry_weight: float = 0.20
    cash_buffer: float = 0.05
    version: str = PORTFOLIO_VERSION


@dataclass(frozen=True)
class TargetPortfolio:
    decision_date: str
    execution_date: str | None
    weights: dict[str, float]
    scores: dict[str, float]
    ranks: dict[str, int]
    reasons: dict[str, str]
    constraint_state: dict[str, object]


def construct_target_portfolio(
    score: CompositeScore,
    industries: Mapping[str, str],
    constraints: PortfolioConstraints,
    *,
    decision_date: str,
    execution_date: str | None,
) -> TargetPortfolio:
    """Select the top ranked names and cap weights without forcing reallocation."""
    if constraints.top_n < 1:
        raise ValueError("top_n must be at least 1.")

    ranked = [
        symbol
        for symbol, _ in sorted(
            score.values.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    selected = ranked[: constraints.top_n]

    if not selected:
        return TargetPortfolio(
            decision_date=decision_date,
            execution_date=execution_date,
            weights={},
            scores={},
            ranks={},
            reasons={},
            constraint_state={
                "target_n": 0,
                "top_n": constraints.top_n,
                "cash_weight": 1.0,
                "weight_capped": [],
                "industry_scaled": [],
                "method": score.method,
            },
        )

    investable = max(0.0, 1.0 - constraints.cash_buffer)
    base_weight = investable / len(selected)
    weights = {
        symbol: min(base_weight, constraints.max_weight) for symbol in selected
    }
    weight_capped = [
        symbol for symbol in selected if base_weight > constraints.max_weight
    ]

    industry_scaled: list[str] = []
    groups: dict[str, list[str]] = {}
    for symbol in selected:
        groups.setdefault(str(industries.get(symbol) or "UNKNOWN"), []).append(symbol)
    if constraints.max_industry_weight > 0:
        for members in groups.values():
            total = sum(weights[symbol] for symbol in members)
            if total > constraints.max_industry_weight + WEIGHT_EPSILON:
                scale = constraints.max_industry_weight / total
                for symbol in members:
                    weights[symbol] *= scale
                industry_scaled.extend(members)

    cash_weight = 1.0 - sum(weights.values())
    return TargetPortfolio(
        decision_date=decision_date,
        execution_date=execution_date,
        weights=weights,
        scores={symbol: score.values[symbol] for symbol in selected},
        ranks={symbol: score.ranks[symbol] for symbol in selected},
        reasons={
            symbol: f"rank_{score.ranks[symbol]}_of_{len(score.values)}"
            for symbol in selected
        },
        constraint_state={
            "target_n": len(selected),
            "top_n": constraints.top_n,
            "candidate_n": len(score.values),
            "base_weight": base_weight,
            "cash_weight": cash_weight,
            "weight_capped": sorted(weight_capped),
            "industry_scaled": sorted(industry_scaled),
            "industry_weights": {
                industry: sum(weights[symbol] for symbol in members)
                for industry, members in sorted(groups.items())
            },
            "method": score.method,
            "version": constraints.version,
        },
    )
