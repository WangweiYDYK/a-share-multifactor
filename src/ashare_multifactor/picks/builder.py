"""Turn screened candidates into a ranked reference buy list."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ashare_multifactor.data.contracts import CanonicalDataset
from ashare_multifactor.factors.composite import COMPOSITE_VERSION, composite_score
from ashare_multifactor.factors.definitions import (
    compute_raw_factors,
    default_factor_specs,
    factor_version,
)
from ashare_multifactor.factors.preprocess import preprocess_version, process_factor
from ashare_multifactor.picks.screen import (
    SCREEN_VERSION,
    PicksConfig,
    ScreenResult,
    screen_snapshot,
)
from ashare_multifactor.portfolio.constructor import (
    PORTFOLIO_VERSION,
    PortfolioConstraints,
    construct_target_portfolio,
)

PICKS_VERSION = "monthly-picks-v1"
DISCLAIMER = (
    "参考清单，不是投资建议：结论只依赖快照中当时可见的数据，没有经过样本外验证，"
    "请自行复核后再决定是否使用。"
)


class PickListError(ValueError):
    """The candidates cannot be turned into a ranked list."""


class PickList:
    """One ranked reference list with its screens and provenance."""

    def __init__(
        self,
        *,
        decision_date: str,
        cutoff: str,
        picks: tuple[dict[str, Any], ...],
        exclusions: tuple[dict[str, Any], ...],
        screens: dict[str, str],
        summary: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        self.decision_date = decision_date
        self.cutoff = cutoff
        self.picks = picks
        self.exclusions = exclusions
        self.screens = screens
        self.summary = summary
        self.manifest = manifest


def build_pick_list(
    datasets: Mapping[str, CanonicalDataset],
    config: PicksConfig,
) -> PickList:
    """Rank every screened security and keep the best ``top_n`` names."""
    screen = screen_snapshot(datasets, config)
    if not screen.candidates:
        raise PickListError(
            "No security passed the screen; check the snapshot coverage first."
        )

    specs = default_factor_specs()
    industries = {
        candidate.symbol: candidate.industry_code
        for candidate in screen.candidates
        if candidate.industry_code
    }
    closes = {candidate.symbol: list(candidate.closes) for candidate in screen.candidates}
    raw = compute_raw_factors(specs, closes)
    processed = [process_factor(spec, raw[spec.name], industries) for spec in specs]
    score = composite_score(processed)
    if not score.values:
        raise PickListError("No security had enough factor coverage for a score.")

    constraints = PortfolioConstraints(
        top_n=config.top_n,
        max_weight=config.max_weight,
        # Without industry data every symbol would fall into one group and the
        # industry cap would silently gut the list, so it is disabled instead.
        max_industry_weight=config.max_industry_weight if industries else 0.0,
        cash_buffer=config.cash_buffer,
    )
    target = construct_target_portfolio(
        score,
        industries,
        constraints,
        decision_date=screen.decision_date,
        execution_date=None,
    )

    by_symbol = {candidate.symbol: candidate for candidate in screen.candidates}
    picks = []
    for symbol in sorted(target.weights, key=lambda name: score.ranks[name]):
        candidate = by_symbol[symbol]
        picks.append(
            {
                "rank": score.ranks[symbol],
                "symbol": symbol,
                "security_name": candidate.security_name,
                "industry_code": candidate.industry_code or "",
                "composite_score": round(score.values[symbol], 4),
                "weight": round(target.weights[symbol], 4),
                "last_close": round(candidate.last_close, 4),
                "last_quote_date": candidate.last_quote_date,
                "average_amount": round(candidate.average_amount, 2),
                "circ_mv": candidate.circ_mv,
                "return_20d": _scaled(raw, "reversal_20d", symbol, -1.0),
                "return_60d": _scaled(raw, "momentum_60d", symbol, 1.0),
                "volatility_60d": _scaled(raw, "low_vol_60d", symbol, 1.0),
            }
        )

    exclusions = [dict(row) for row in screen.exclusions]
    exclusions.extend(
        {
            "symbol": symbol,
            "security_name": by_symbol[symbol].security_name
            if symbol in by_symbol
            else "",
            "reason": reason,
        }
        for symbol, reason in sorted(score.excluded.items())
    )

    snapshot_id, sources = _dataset_meta(datasets)
    summary = {
        **screen.summary,
        "scored_count": len(score.values),
        "pick_count": len(picks),
        "factor_coverage_excluded": len(score.excluded),
    }
    manifest = {
        "picks_version": PICKS_VERSION,
        "screen_version": SCREEN_VERSION,
        "factor_version": factor_version(specs),
        "preprocess_version": preprocess_version(),
        "composite_version": COMPOSITE_VERSION,
        "portfolio_version": PORTFOLIO_VERSION,
        "decision_date": screen.decision_date,
        "decision_cutoff": screen.cutoff,
        "factor_basis": screen.factor_basis,
        "top_n": config.top_n,
        "config": asdict(config),
        "screens": screen.screens,
        "summary": summary,
        "snapshot_id": snapshot_id,
        "sources": sources,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "limitations": _limitations(screen, datasets),
        "disclaimer": DISCLAIMER,
    }
    return PickList(
        decision_date=screen.decision_date,
        cutoff=screen.cutoff,
        picks=tuple(picks),
        exclusions=tuple(exclusions),
        screens=screen.screens,
        summary=summary,
        manifest=manifest,
    )


def _scaled(
    raw: Mapping[str, Mapping[str, float | None]],
    name: str,
    symbol: str,
    sign: float,
) -> float | None:
    """Report the raw factor as a readable number (returns and volatility)."""
    value = raw.get(name, {}).get(symbol)
    return None if value is None else round(sign * float(value), 4)


def _dataset_meta(
    datasets: Mapping[str, CanonicalDataset],
) -> tuple[str | None, list[str]]:
    snapshot_id: str | None = None
    sources: set[str] = set()
    for dataset in datasets.values():
        metadata = dataset.metadata or {}
        if snapshot_id is None and metadata.get("snapshot_id"):
            snapshot_id = str(metadata["snapshot_id"])
        source = metadata.get("source") or _single_source(dataset.rows)
        if source:
            sources.add(str(source))
    return snapshot_id, sorted(sources)


def _single_source(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """A dataset written by one provider reports that provider."""
    values = {str(row.get("source")) for row in rows if row.get("source")}
    return next(iter(values)) if len(values) == 1 else None


def _limitations(
    screen: ScreenResult,
    datasets: Mapping[str, CanonicalDataset],
) -> list[str]:
    limitations = []
    if any(dataset.metadata.get("synthetic") for dataset in datasets.values()):
        limitations.append("本次清单来自合成数据夹具，只用于验证流程，不是真实市场结果。")
    if screen.factor_basis != "backward_adjusted":
        limitations.append(
            "因子使用未复权价格，除权除息日附近的价格变化会直接影响动量与波动。"
        )
    skipped = [name for name, status in screen.screens.items() if status.startswith("skipped")]
    for name in skipped:
        limitations.append("{0}: {1}".format(name, screen.screens[name]))
    return limitations
