"""Monthly price-factor research over partitioned BaoStock history.

The workflow intentionally stops before portfolio simulation. It validates the
downloaded raw/backward price pairs, evaluates the three baseline price factors
with next-rebalance labels, and writes reproducible CSV/HTML artifacts.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import math
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from ashare_multifactor.factors.composite import composite_score
from ashare_multifactor.factors.definitions import (
    FactorSpec,
    compute_raw_factor,
    default_factor_specs,
    factor_version,
)
from ashare_multifactor.factors.preprocess import process_factor, preprocess_version
from ashare_multifactor.reports.performance import pearson, spearman, write_csv, write_json

RESEARCH_VERSION = "price-factor-research-v1"
SHANGHAI_TZ = "Asia/Shanghai"
LOGGER = logging.getLogger(__name__)
QUALITY_COLUMNS = (
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "trade_status",
    "is_st",
    "available_at",
)
FACTOR_COLUMNS = (
    "trade_date",
    "open",
    "close",
    "amount",
    "trade_status",
    "is_st",
)


@dataclass(frozen=True)
class PriceFactorResearchConfig:
    """Frozen assumptions for one research run."""

    start_date: str | None = None
    end_date: str | None = None
    min_listing_observations: int = 120
    liquidity_window: int = 20
    min_average_amount: float = 50_000_000.0
    quantiles: int = 5
    max_symbols: int | None = None


def run_price_factor_research(
    input_root: Path,
    output_dir: Path,
    config: PriceFactorResearchConfig | None = None,
) -> Path:
    """Run data quality and monthly factor analysis, returning the manifest path."""
    cfg = config or PriceFactorResearchConfig()
    root = Path(input_root).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError(f"Output directory already exists: {output}")
    raw_dir = root / "daily_prices" / "none"
    adjusted_dir = root / "daily_prices" / "backward"
    calendar_path = root / "trade_calendar.csv"
    for required in (raw_dir, adjusted_dir, calendar_path):
        if not required.exists():
            raise FileNotFoundError(f"Required BaoStock input is missing: {required}")

    open_dates = _load_open_dates(calendar_path, cfg.start_date, cfg.end_date)
    if len(open_dates) < 3:
        raise ValueError("The selected range has too few open trading days.")
    decisions = _month_end_dates(open_dates)
    execution_dates = _execution_pairs(decisions, open_dates)
    symbols = sorted(path.stem for path in raw_dir.glob("*.csv"))
    declared_symbols = _declared_symbols(root) if cfg.max_symbols is None else set(symbols)
    if cfg.max_symbols is not None:
        symbols = symbols[: cfg.max_symbols]
    if not symbols:
        raise ValueError(f"No raw daily-price partitions found in {raw_dir}")

    quality_by_symbol: dict[str, dict[str, Any]] = {}
    for index, symbol in enumerate(symbols, start=1):
        frame = pd.read_csv(raw_dir / f"{symbol}.csv", usecols=QUALITY_COLUMNS)
        quality_by_symbol[symbol] = _quality_row(symbol, frame, open_dates)
        if index % 500 == 0 or index == len(symbols):
            LOGGER.info("Data quality progress: %s/%s symbols", index, len(symbols))

    observations: dict[str, list[dict[str, Any]]] = {day: [] for day in decisions}
    for index, symbol in enumerate(symbols, start=1):
        adjusted_path = adjusted_dir / f"{symbol}.csv"
        if not adjusted_path.is_file():
            quality_by_symbol[symbol].update(
                {"backward_rows": 0, "price_date_match": False, "backward_missing": True}
            )
            continue
        frame = pd.read_csv(adjusted_path, usecols=FACTOR_COLUMNS)
        _update_adjustment_quality(quality_by_symbol[symbol], frame)
        for row in _symbol_factor_observations(symbol, frame, decisions, execution_dates, cfg):
            observations[row["decision_date"]].append(row)
        if index % 500 == 0 or index == len(symbols):
            LOGGER.info("Factor calculation progress: %s/%s symbols", index, len(symbols))

    quality_rows = [quality_by_symbol[symbol] for symbol in symbols]
    factor_rows, quantile_rows, correlation_rows, snapshots = _evaluate_cross_sections(
        observations, cfg.quantiles
    )
    summary_rows = _factor_summary(factor_rows, quantile_rows, cfg.quantiles)
    quality_summary = _quality_summary(
        quality_rows,
        open_dates,
        declared_symbols=len(declared_symbols),
        symbols_without_raw_partition=len(declared_symbols - set(symbols)),
    )

    output.mkdir(parents=True)
    write_csv(output / "data_quality.csv", quality_rows)
    write_csv(output / "factor_ic.csv", factor_rows)
    write_csv(output / "factor_quantiles.csv", quantile_rows)
    write_csv(output / "factor_correlation.csv", correlation_rows)
    write_csv(output / "factor_summary.csv", summary_rows)
    write_csv(output / "factor_snapshot.csv", snapshots)
    (output / "data_quality_report.html").write_text(
        _render_quality_html(quality_summary, quality_rows), encoding="utf-8"
    )
    (output / "factor_report.html").write_text(
        _render_factor_html(summary_rows, factor_rows, quantile_rows, cfg.quantiles),
        encoding="utf-8",
    )

    manifest = {
        "research_version": RESEARCH_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_root": str(root),
        "config": asdict(cfg),
        "factor_version": factor_version(default_factor_specs()),
        "preprocess_version": preprocess_version(),
        "quality_summary": quality_summary,
        "decision_months": len(decisions),
        "evaluated_months": sum(
            1 for row in factor_rows if row["factor"] == "composite" and row["rank_ic"] is not None
        ),
        "outputs": [
            "data_quality.csv",
            "data_quality_report.html",
            "factor_ic.csv",
            "factor_quantiles.csv",
            "factor_correlation.csv",
            "factor_summary.csv",
            "factor_snapshot.csv",
            "factor_report.html",
        ],
        "limitations": [
            "This is factor research, not a tradable portfolio backtest or investment advice.",
            "The historical universe and status coverage depend on BaoStock's security master.",
            (
                "No industry or market-cap neutralization is applied because point-in-time "
                "data is absent."
            ),
            (
                "PE, PB and other valuation fields are excluded pending historical-visibility "
                "validation."
            ),
            (
                "Labels use backward-adjusted next-rebalance open prices and omit costs and "
                "fill constraints."
            ),
            (
                "A symbol needs valid quotes on both execution dates to enter IC and "
                "quantile statistics."
            ),
        ],
    }
    write_json(output / "manifest.json", manifest)
    return output / "manifest.json"


def _load_open_dates(path: Path, start: str | None, end: str | None) -> list[str]:
    calendar = pd.read_csv(path, usecols=("trade_date", "is_open"))
    dates = calendar.loc[pd.to_numeric(calendar["is_open"], errors="coerce") == 1, "trade_date"]
    result = sorted(set(dates.astype(str)))
    if start:
        result = [day for day in result if day >= start]
    if end:
        result = [day for day in result if day <= end]
    return result


def _declared_symbols(root: Path) -> set[str]:
    symbols: set[str] = set()
    for state_path in root.glob("batch_*_state.json"):
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        symbols.update(str(symbol) for symbol in payload.get("symbols", ()))
    return symbols


def _month_end_dates(open_dates: Sequence[str]) -> list[str]:
    by_month: dict[str, str] = {}
    for day in open_dates:
        by_month[day[:7]] = day
    return list(by_month.values())


def _execution_pairs(
    decisions: Sequence[str], open_dates: Sequence[str]
) -> dict[str, tuple[str, str] | None]:
    next_open = {
        day: open_dates[index + 1] if index + 1 < len(open_dates) else None
        for index, day in enumerate(open_dates)
    }
    pairs: dict[str, tuple[str, str] | None] = {}
    for index, decision in enumerate(decisions):
        next_decision = decisions[index + 1] if index + 1 < len(decisions) else None
        entry = next_open.get(decision)
        exit_day = next_open.get(next_decision) if next_decision else None
        pairs[decision] = (entry, exit_day) if entry and exit_day else None
    return pairs


def _quality_row(
    symbol: str, frame: pd.DataFrame, open_dates: Sequence[str]
) -> dict[str, Any]:
    dates = frame["trade_date"].astype(str)
    parsed_dates = pd.to_datetime(dates, errors="coerce")
    numeric = {
        name: pd.to_numeric(frame[name], errors="coerce")
        for name in ("open", "high", "low", "close", "volume", "amount", "trade_status", "is_st")
    }
    active = numeric["trade_status"].fillna(0) == 1
    missing_critical = (
        parsed_dates.isna()
        | (
            active
            & (
                numeric["open"].isna()
                | numeric["high"].isna()
                | numeric["low"].isna()
                | numeric["close"].isna()
            )
        )
    )
    invalid_prices = active & (
        (numeric["open"] <= 0)
        | (numeric["high"] <= 0)
        | (numeric["low"] <= 0)
        | (numeric["close"] <= 0)
    )
    invalid_ohlc = active & (
        (numeric["high"] < numeric["low"])
        | (numeric["high"] < numeric["open"])
        | (numeric["high"] < numeric["close"])
        | (numeric["low"] > numeric["open"])
        | (numeric["low"] > numeric["close"])
    )
    availability = pd.to_datetime(frame["available_at"], errors="coerce", utc=True)
    valid_dates = sorted(set(day for day in dates if len(day) == 10 and day[4] == "-"))
    first_date = valid_dates[0] if valid_dates else None
    last_date = valid_dates[-1] if valid_dates else None
    expected = _calendar_count(open_dates, first_date, last_date)
    returns = numeric["close"].where(active).pct_change(fill_method=None)
    digest = _date_digest(valid_dates)
    return {
        "symbol": symbol,
        "rows": len(frame),
        "first_date": first_date,
        "last_date": last_date,
        "expected_market_days": expected,
        "calendar_coverage": len(valid_dates) / expected if expected else None,
        "duplicate_dates": int(dates.duplicated().sum()),
        "missing_critical_rows": int(missing_critical.sum()),
        "invalid_price_rows": int(invalid_prices.fillna(False).sum()),
        "invalid_ohlc_rows": int(invalid_ohlc.fillna(False).sum()),
        "negative_volume_amount_rows": int(
            ((numeric["volume"] < 0) | (numeric["amount"] < 0)).fillna(False).sum()
        ),
        "return_over_30pct_rows": int((returns.abs() > 0.30).fillna(False).sum()),
        "suspension_rows": int((numeric["trade_status"].fillna(0) != 1).sum()),
        "st_rows": int((numeric["is_st"].fillna(0) == 1).sum()),
        "invalid_available_at_rows": int(availability.isna().sum()),
        "raw_date_digest": digest,
        "backward_rows": None,
        "price_date_match": None,
        "backward_missing": False,
    }


def _update_adjustment_quality(row: dict[str, Any], frame: pd.DataFrame) -> None:
    dates = sorted(set(frame["trade_date"].astype(str)))
    row["backward_rows"] = len(frame)
    row["price_date_match"] = (
        row["rows"] == len(frame) and row["raw_date_digest"] == _date_digest(dates)
    )
    row["backward_missing"] = False


def _date_digest(dates: Iterable[str]) -> str:
    payload = "\n".join(dates).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _calendar_count(open_dates: Sequence[str], start: str | None, end: str | None) -> int:
    if not start or not end:
        return 0
    return max(0, bisect_right(open_dates, end) - bisect_left(open_dates, start))


def _symbol_factor_observations(
    symbol: str,
    frame: pd.DataFrame,
    decisions: Sequence[str],
    execution_dates: Mapping[str, tuple[str, str] | None],
    config: PriceFactorResearchConfig,
) -> list[dict[str, Any]]:
    data = frame.copy()
    data["trade_date"] = data["trade_date"].astype(str)
    data = data.drop_duplicates("trade_date", keep="last").sort_values("trade_date")
    for name in ("open", "close", "amount", "trade_status", "is_st"):
        data[name] = pd.to_numeric(data[name], errors="coerce")
    dates = data["trade_date"].tolist()
    index_by_date = {day: index for index, day in enumerate(dates)}
    closes = data["close"].tolist()
    specs = default_factor_specs()
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        index = index_by_date.get(decision)
        pair = execution_dates.get(decision)
        if index is None or pair is None or index + 1 < config.min_listing_observations:
            continue
        current = data.iloc[index]
        if current["trade_status"] != 1 or current["is_st"] == 1:
            continue
        liquidity_start = max(0, index + 1 - config.liquidity_window)
        average_amount = data.iloc[liquidity_start : index + 1]["amount"].mean()
        if not math.isfinite(float(average_amount)) or average_amount < config.min_average_amount:
            continue
        entry_day, exit_day = pair
        entry_index = index_by_date.get(entry_day)
        exit_index = index_by_date.get(exit_day)
        label = None
        if entry_index is not None and exit_index is not None:
            entry = data.iloc[entry_index]
            exit_row = data.iloc[exit_index]
            entry_open = float(entry["open"])
            exit_open = float(exit_row["open"])
            if (
                entry["trade_status"] == 1
                and exit_row["trade_status"] == 1
                and math.isfinite(entry_open)
                and math.isfinite(exit_open)
                and entry_open > 0
                and exit_open > 0
            ):
                label = exit_open / entry_open - 1.0
        factors = {
            spec.name: compute_raw_factor(
                spec, closes[max(0, index - spec.lookback) : index + 1]
            )
            for spec in specs
        }
        rows.append(
            {
                "decision_date": decision,
                "entry_date": entry_day,
                "exit_date": exit_day,
                "symbol": symbol,
                "average_amount": float(average_amount),
                "label": label,
                **factors,
            }
        )
    return rows


def _evaluate_cross_sections(
    observations: Mapping[str, Sequence[Mapping[str, Any]]], quantiles: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    factor_rows: list[dict[str, Any]] = []
    quantile_rows: list[dict[str, Any]] = []
    correlation_rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    specs = default_factor_specs()

    for decision, records in observations.items():
        if not records:
            continue
        labels = {
            str(row["symbol"]): float(row["label"])
            for row in records
            if row.get("label") is not None and math.isfinite(float(row["label"]))
        }
        processed = []
        for spec in specs:
            raw = {str(row["symbol"]): row.get(spec.name) for row in records}
            processed.append(process_factor(spec, raw))
        composite = composite_score(processed)
        values_by_factor = {factor.name: factor.values for factor in processed}
        values_by_factor["composite"] = composite.values

        for factor_name, values in values_by_factor.items():
            shared = sorted(set(values) & set(labels))
            left = [values[symbol] for symbol in shared]
            right = [labels[symbol] for symbol in shared]
            factor_rows.append(
                {
                    "decision_date": decision,
                    "entry_date": records[0]["entry_date"],
                    "exit_date": records[0]["exit_date"],
                    "label_available_at": f"{records[0]['exit_date']}T18:00:00+08:00",
                    "factor": factor_name,
                    "eligible_symbols": len(records),
                    "scored_symbols": len(values),
                    "labelled_symbols": len(shared),
                    "coverage": len(values) / len(records) if records else None,
                    "ic": pearson(left, right),
                    "rank_ic": spearman(left, right),
                }
            )
            quantile_rows.extend(
                _quantile_rows(decision, factor_name, values, labels, quantiles)
            )

        for left_name, right_name in combinations(values_by_factor, 2):
            left_values = values_by_factor[left_name]
            right_values = values_by_factor[right_name]
            shared = sorted(set(left_values) & set(right_values))
            correlation_rows.append(
                {
                    "decision_date": decision,
                    "left_factor": left_name,
                    "right_factor": right_name,
                    "sample_size": len(shared),
                    "pearson": pearson(
                        [left_values[symbol] for symbol in shared],
                        [right_values[symbol] for symbol in shared],
                    ),
                    "spearman": spearman(
                        [left_values[symbol] for symbol in shared],
                        [right_values[symbol] for symbol in shared],
                    ),
                }
            )

        for row in records:
            symbol = str(row["symbol"])
            snapshots.append(
                {
                    "decision_date": decision,
                    "symbol": symbol,
                    "entry_date": row["entry_date"],
                    "exit_date": row["exit_date"],
                    "forward_return": row["label"],
                    "average_amount": row["average_amount"],
                    **{f"raw_{spec.name}": row.get(spec.name) for spec in specs},
                    **{
                        f"score_{factor.name}": factor.values.get(symbol)
                        for factor in processed
                    },
                    "composite_score": composite.values.get(symbol),
                    "composite_rank": composite.ranks.get(symbol),
                }
            )
    return factor_rows, quantile_rows, correlation_rows, snapshots


def _quantile_rows(
    decision: str,
    factor: str,
    values: Mapping[str, float],
    labels: Mapping[str, float],
    quantiles: int,
) -> list[dict[str, Any]]:
    ranked = sorted(
        ((symbol, values[symbol], labels[symbol]) for symbol in set(values) & set(labels)),
        key=lambda item: (item[1], item[0]),
    )
    buckets: dict[int, list[float]] = {number: [] for number in range(1, quantiles + 1)}
    for index, (_, _, label) in enumerate(ranked):
        bucket = min(quantiles, index * quantiles // max(1, len(ranked)) + 1)
        buckets[bucket].append(label)
    rows = []
    for bucket, returns in buckets.items():
        rows.append(
            {
                "decision_date": decision,
                "factor": factor,
                "quantile": bucket,
                "count": len(returns),
                "mean_forward_return": fmean(returns) if returns else None,
                "positive_rate": (
                    sum(value > 0 for value in returns) / len(returns) if returns else None
                ),
            }
        )
    return rows


def _factor_summary(
    factor_rows: Sequence[Mapping[str, Any]],
    quantile_rows: Sequence[Mapping[str, Any]],
    quantiles: int,
) -> list[dict[str, Any]]:
    result = []
    factors = sorted(set(str(row["factor"]) for row in factor_rows))
    for factor in factors:
        rows = [row for row in factor_rows if row["factor"] == factor]
        ics = [float(row["ic"]) for row in rows if row["ic"] is not None]
        rank_ics = [float(row["rank_ic"]) for row in rows if row["rank_ic"] is not None]
        coverages = [float(row["coverage"]) for row in rows if row["coverage"] is not None]
        by_date: dict[str, dict[int, float]] = {}
        for row in quantile_rows:
            if row["factor"] != factor or row["mean_forward_return"] is None:
                continue
            by_date.setdefault(str(row["decision_date"]), {})[int(row["quantile"])] = float(
                row["mean_forward_return"]
            )
        spreads = [
            buckets[quantiles] - buckets[1]
            for buckets in by_date.values()
            if 1 in buckets and quantiles in buckets
        ]
        rank_std = pstdev(rank_ics) if len(rank_ics) > 1 else 0.0
        result.append(
            {
                "factor": factor,
                "months": len(rank_ics),
                "mean_ic": fmean(ics) if ics else None,
                "mean_rank_ic": fmean(rank_ics) if rank_ics else None,
                "rank_ic_positive_rate": (
                    sum(value > 0 for value in rank_ics) / len(rank_ics) if rank_ics else None
                ),
                "rank_ic_ir": fmean(rank_ics) / rank_std if rank_std > 0 else None,
                "mean_coverage": fmean(coverages) if coverages else None,
                "mean_top_minus_bottom_return": fmean(spreads) if spreads else None,
            }
        )
    return result


def _quality_summary(
    rows: Sequence[Mapping[str, Any]],
    open_dates: Sequence[str],
    *,
    declared_symbols: int,
    symbols_without_raw_partition: int,
) -> dict[str, Any]:
    return {
        "declared_symbols": declared_symbols,
        "symbols_with_raw_data": len(rows),
        "symbols_without_raw_partition": symbols_without_raw_partition,
        "raw_rows": sum(int(row["rows"]) for row in rows),
        "backward_rows": sum(int(row["backward_rows"] or 0) for row in rows),
        "calendar_start": open_dates[0],
        "calendar_end": open_dates[-1],
        "duplicate_dates": sum(int(row["duplicate_dates"]) for row in rows),
        "missing_critical_rows": sum(int(row["missing_critical_rows"]) for row in rows),
        "invalid_price_rows": sum(int(row["invalid_price_rows"]) for row in rows),
        "invalid_ohlc_rows": sum(int(row["invalid_ohlc_rows"]) for row in rows),
        "invalid_available_at_rows": sum(int(row["invalid_available_at_rows"]) for row in rows),
        "price_date_mismatch_symbols": sum(row["price_date_match"] is not True for row in rows),
        "mean_calendar_coverage": fmean(
            float(row["calendar_coverage"])
            for row in rows
            if row["calendar_coverage"] is not None
        ),
    }


def _render_quality_html(
    summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> str:
    worst = sorted(
        rows,
        key=lambda row: (
            int(row["missing_critical_rows"])
            + int(row["invalid_price_rows"])
            + int(row["invalid_ohlc_rows"]),
            1.0 - float(row["calendar_coverage"] or 0.0),
        ),
        reverse=True,
    )[:30]
    cards = "".join(
        (
            f'<div class="card"><span>{html.escape(str(key))}</span>'
            f"<strong>{_fmt(value)}</strong></div>"
        )
        for key, value in summary.items()
    )
    table = _html_table(
        worst,
        (
            "symbol",
            "rows",
            "first_date",
            "last_date",
            "calendar_coverage",
            "missing_critical_rows",
            "invalid_price_rows",
            "invalid_ohlc_rows",
            "price_date_match",
        ),
    )
    return _html_page(
        "BaoStock 数据质量报告",
        '<div class="cards">' + cards + "</div><h2>优先检查的证券</h2>" + table,
    )


def _render_factor_html(
    summary_rows: Sequence[Mapping[str, Any]],
    factor_rows: Sequence[Mapping[str, Any]],
    quantile_rows: Sequence[Mapping[str, Any]],
    quantiles: int,
) -> str:
    overview_rows = [_factor_overview_row(row) for row in summary_rows]
    sections = [
        "<p class=note>这是一份因子排序有效性报告，不是账户回测。月末收盘后计算信号；"
        "收益标签为下一交易日开盘至下月下一交易日开盘的后复权收益。结果未计交易成本，"
        "也未做行业或市值中性化。</p>",
        "<h2>如何阅读这份报告</h2>",
        _factor_help_html(),
        "<h2>本次结果的直白结论</h2>",
        _factor_conclusion_html(summary_rows),
        "<h2>因子总览</h2>",
        _html_table(
            overview_rows,
            (
                "因子",
                "有效月份",
                "平均IC",
                "平均RankIC",
                "RankIC为正比例",
                "RankIC IR",
                "覆盖率",
                "Q5-Q1月均收益",
            ),
        ),
    ]
    for summary in summary_rows:
        factor = str(summary["factor"])
        series = [
            (str(row["decision_date"]), float(row["rank_ic"]))
            for row in factor_rows
            if row["factor"] == factor and row["rank_ic"] is not None
        ]
        quantile_means = []
        for bucket in range(1, quantiles + 1):
            values = [
                float(row["mean_forward_return"])
                for row in quantile_rows
                if row["factor"] == factor
                and int(row["quantile"]) == bucket
                and row["mean_forward_return"] is not None
            ]
            quantile_means.append(
                {"quantile": f"Q{bucket}", "mean_forward_return": fmean(values) if values else None}
            )
        sections.extend(
            [
                f"<h2>{html.escape(factor)}</h2>",
                f'<p class="factor-note">{html.escape(_factor_direction(factor))}</p>',
                _line_svg(series),
                "<p class=caption>月度RankIC：零线上方表示该月因子排序方向正确；"
                "零线下方表示当月失效或反向。应关注长期稳定性，而不是单个极端月份。</p>",
                "<h3>各分位平均下期收益</h3>",
                _html_table(
                    [
                        {
                            "分位": row["quantile"],
                            "平均下期收益": _percent(row["mean_forward_return"]),
                        }
                        for row in quantile_means
                    ],
                    ("分位", "平均下期收益"),
                ),
                "<p class=caption>Q1是因子分数最低组，Q5是因子分数最高组。"
                "理想状态是收益从Q1到Q5逐步上升；Q5-Q1为正只说明两端方向正确，"
                "不一定代表中间分组单调。</p>",
            ]
        )
    return _html_page("月频价格因子研究报告", "".join(sections))


def _factor_overview_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "因子": _factor_name(str(row["factor"])),
        "有效月份": row["months"],
        "平均IC": _decimal(row["mean_ic"]),
        "平均RankIC": _decimal(row["mean_rank_ic"]),
        "RankIC为正比例": _percent(row["rank_ic_positive_rate"]),
        "RankIC IR": _decimal(row["rank_ic_ir"]),
        "覆盖率": _percent(row["mean_coverage"]),
        "Q5-Q1月均收益": _percent(row["mean_top_minus_bottom_return"]),
    }


def _factor_help_html() -> str:
    rows = [
        {
            "指标": "平均IC",
            "含义": "因子数值与下期收益的线性相关性",
            "怎么理解": "正数方向正确，负数方向相反；容易受极端收益影响",
        },
        {
            "指标": "平均RankIC",
            "含义": "因子排名与下期收益排名的相关性",
            "怎么理解": "最应优先关注；正数越大，排序能力通常越强",
        },
        {
            "指标": "RankIC为正比例",
            "含义": "月度RankIC大于零的月份占比",
            "怎么理解": "衡量方向稳定性；50%附近通常说明不稳定",
        },
        {
            "指标": "RankIC IR",
            "含义": "平均RankIC除以月度RankIC标准差",
            "怎么理解": "越高越稳定；本报告没有年化，不能与年化ICIR直接比较",
        },
        {
            "指标": "覆盖率",
            "含义": "通过前置筛选的股票中成功算出因子的比例",
            "怎么理解": "不是全市场覆盖率；前置筛选含上市期、流动性、ST和交易状态",
        },
        {
            "指标": "Q5-Q1月均收益",
            "含义": "最高分组平均收益减最低分组平均收益",
            "怎么理解": "是研究价差，不是账户可以直接获得的净收益",
        },
    ]
    order = (
        "<ol><li>先看平均RankIC是否为正。</li>"
        "<li>再看RankIC为正比例和月度曲线，判断是否稳定。</li>"
        "<li>最后看Q1至Q5是否大致单调，以及Q5-Q1价差。</li></ol>"
    )
    return order + _html_table(rows, ("指标", "含义", "怎么理解"))


def _factor_conclusion_html(summary_rows: Sequence[Mapping[str, Any]]) -> str:
    by_factor = {str(row["factor"]): row for row in summary_rows}
    items = []
    descriptions = {
        "reversal_20d": "20日反转",
        "momentum_60d": "60日动量",
        "low_vol_60d": "60日低波",
        "composite": "三因子等权综合",
    }
    for factor in ("reversal_20d", "low_vol_60d", "momentum_60d", "composite"):
        row = by_factor.get(factor)
        if row is None:
            continue
        rank_ic = float(row["mean_rank_ic"])
        positive_rate = float(row["rank_ic_positive_rate"])
        spread = float(row["mean_top_minus_bottom_return"])
        if rank_ic > 0.03 and positive_rate >= 0.60 and spread > 0:
            verdict = "当前样本中方向为正，值得进入下一轮稳健性与成本后回测。"
        elif rank_ic < -0.03 and spread < 0:
            verdict = "当前定义呈明显反向，暂时不宜按正向因子加入组合。"
        else:
            verdict = "当前证据较弱或不稳定，需要继续分阶段检查。"
        items.append(
            "<li><strong>{}</strong>：RankIC {}，正值月份 {}，Q5-Q1 {}。{}</li>".format(
                html.escape(descriptions[factor]),
                _decimal(rank_ic),
                _percent(positive_rate),
                _percent(spread),
                html.escape(verdict),
            )
        )
    return "<ul class=conclusions>" + "".join(items) + "</ul>"


def _factor_name(factor: str) -> str:
    return {
        "reversal_20d": "20日反转",
        "momentum_60d": "60日动量",
        "low_vol_60d": "60日低波",
        "composite": "三因子等权综合",
    }.get(factor, factor)


def _factor_direction(factor: str) -> str:
    return {
        "reversal_20d": "分数越高，代表过去20个交易日跌得越多；Q5是短期跌幅较大的股票。",
        "momentum_60d": "分数越高，代表过去60个交易日涨得越多；Q5是中短期强势股票。",
        "low_vol_60d": "方向已经统一：分数越高，代表过去60个交易日波动越低。",
        "composite": "三个处理后因子类别等权；分数越高，代表综合排名越靠前。",
    }.get(factor, "分数越高代表项目定义下越好。")


def _decimal(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    return f"{number:.4f}" if math.isfinite(number) else "—"


def _percent(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    return f"{number:.2%}" if math.isfinite(number) else "—"


def _line_svg(series: Sequence[tuple[str, float]]) -> str:
    if not series:
        return "<p>没有可用序列。</p>"
    width, height, pad = 900, 220, 30
    values = [value for _, value in series]
    lower, upper = min(values + [-0.05]), max(values + [0.05])
    spread = upper - lower or 1.0
    points = []
    for index, (_, value) in enumerate(series):
        x = pad + index * (width - 2 * pad) / max(1, len(series) - 1)
        y = pad + (upper - value) * (height - 2 * pad) / spread
        points.append(f"{x:.1f},{y:.1f}")
    zero_y = pad + upper * (height - 2 * pad) / spread
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="RankIC 时间序列">'
        f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{width-pad}" y2="{zero_y:.1f}" class="zero"/>'
        f'<polyline points="{" ".join(points)}" class="series"/></svg>'
    )


def _html_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{html.escape(_fmt(row.get(column)))}</td>" for column in columns)
        + "</tr>"
        for row in rows
    )
    return (
        f"<div class=table-wrap><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "—"
        return f"{value:.4%}" if abs(value) <= 1.5 else f"{value:,.2f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(title)}</title><style>
body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;
background:#f3f6f4;color:#17332b}}
main{{max-width:1120px;margin:0 auto;padding:32px 24px 64px}}
h1{{font-size:30px}}h2{{margin-top:34px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.card{{background:white;border:1px solid #d9e3de;border-radius:12px;padding:16px}}
.card span{{display:block;color:#60756d;font-size:13px}}
.card strong{{display:block;margin-top:6px;font-size:20px}}
.table-wrap{{overflow:auto;background:white;border:1px solid #d9e3de;border-radius:12px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{padding:9px 11px;border-bottom:1px solid #e8eeeb;text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}}th{{background:#eaf2ee}}
.note{{padding:14px 16px;background:#fff8df;border-left:4px solid #d5a400}}
.factor-note,.caption{{color:#52665f;line-height:1.7}}
.caption{{font-size:13px;margin-top:8px}}
.conclusions li,ol li{{margin:8px 0;line-height:1.65}}
svg{{width:100%;background:white;border:1px solid #d9e3de;border-radius:12px}}
.series{{fill:none;stroke:#087f5b;stroke-width:3}}
.zero{{stroke:#9fb2aa;stroke-dasharray:5 5}}
</style></head><body><main><h1>{html.escape(title)}</h1>{body}</main></body></html>"""
