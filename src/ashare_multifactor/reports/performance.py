"""Performance, rank-correlation, and run-artifact helpers."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence

REPORT_VERSION = "backtest-report-v1"
TRADING_DAYS_PER_YEAR = 252
MIN_ANNUALIZED_POINTS = 60


def pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Pearson correlation; None when either side is degenerate."""
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = fmean(left)
    right_mean = fmean(right)
    left_std = pstdev(left)
    right_std = pstdev(right)
    if left_std <= 0 or right_std <= 0:
        return None
    covariance = fmean(
        [(a - left_mean) * (b - right_mean) for a, b in zip(left, right)]
    )
    return covariance / (left_std * right_std)


def spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Rank correlation with average ranks for ties."""
    if len(left) != len(right) or len(left) < 2:
        return None
    return pearson(average_ranks(left), average_ranks(right))


def average_ranks(values: Sequence[float]) -> list[float]:
    """1-based average ranks, so ties share the mean of their positions."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        shared = (position + end) / 2.0 + 1.0
        for index in range(position, end + 1):
            ranks[order[index]] = shared
        position = end + 1
    return ranks


def drawdown_series(navs: Sequence[float]) -> list[float]:
    """Drawdown of each observation against the running peak."""
    peak = 0.0
    series = []
    for nav in navs:
        peak = max(peak, nav)
        series.append(nav / peak - 1.0 if peak > 0 else 0.0)
    return series


def performance_metrics(
    daily: Sequence[Mapping[str, Any]],
    monthly: Sequence[Mapping[str, Any]],
    fills: Sequence[Mapping[str, Any]],
    rejects: Sequence[Mapping[str, Any]],
    skipped: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize a completed run; costs are already reflected in NAV."""
    navs = [float(row["nav"]) for row in daily]
    if not navs:
        return {"report_version": REPORT_VERSION, "observations": 0}

    returns = [navs[index] / navs[index - 1] - 1.0 for index in range(1, len(navs))]
    drawdowns = drawdown_series(navs)
    total_return = navs[-1] - 1.0
    total_fees = sum(float(row.get("fee") or 0.0) for row in fills)
    total_slippage = sum(float(row.get("slippage_cost") or 0.0) for row in fills)
    initial_equity = float(daily[0]["total_equity"])

    metrics: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "observations": len(navs),
        "total_return": total_return,
        "max_drawdown": min(drawdowns) if drawdowns else 0.0,
        "final_nav": navs[-1],
        "trade_count": len(fills),
        "reject_count": len(rejects),
        "skipped_count": len(skipped),
        "total_fees": total_fees,
        "total_slippage_cost": total_slippage,
        "approximate_gross_return": (
            total_return + (total_fees + total_slippage) / initial_equity
            if initial_equity > 0
            else total_return
        ),
        "reject_reasons": dict(sorted(Counter(row["reason"] for row in rejects).items())),
    }

    if len(returns) >= MIN_ANNUALIZED_POINTS:
        return_std = pstdev(returns)
        metrics["annualized_return"] = (1.0 + total_return) ** (
            TRADING_DAYS_PER_YEAR / len(returns)
        ) - 1.0
        metrics["annualized_volatility"] = return_std * math.sqrt(TRADING_DAYS_PER_YEAR)
        metrics["sharpe"] = (
            fmean(returns) / return_std * math.sqrt(TRADING_DAYS_PER_YEAR)
            if return_std > 0
            else None
        )
    else:
        metrics["annualized_return"] = None
        metrics["annualized_volatility"] = None
        metrics["sharpe"] = None

    rank_ics = [row["rank_ic"] for row in monthly if row.get("rank_ic") is not None]
    ics = [row["ic"] for row in monthly if row.get("ic") is not None]
    metrics["monthly_observations"] = len(monthly)
    metrics["rank_ic_mean"] = fmean(rank_ics) if rank_ics else None
    metrics["ic_mean"] = fmean(ics) if ics else None
    metrics["rank_ic_series"] = rank_ics
    turnovers = [float(row.get("turnover_one_way") or 0.0) for row in monthly]
    metrics["turnover_one_way_mean"] = fmean(turnovers) if turnovers else None
    metrics["turnover_one_way_total"] = sum(turnovers)
    return metrics


def write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    fieldnames: Sequence[str] | None = None,
) -> None:
    """Write rows with a stable column order, or a header-only file when empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(fieldnames or (rows[0].keys() if rows else ()))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _cell(row.get(column)) for column in columns})


def _cell(value: Any) -> Any:
    """Keep nested values machine-readable instead of writing Python reprs."""
    if isinstance(value, Mapping):
        return json.dumps(
            dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))
    return value


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
