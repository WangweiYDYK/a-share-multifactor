"""Monthly multifactor backtest engine.

One run wires together the frozen snapshot, the point-in-time universe, factor
computation and preprocessing, target construction, next-open order intents,
A-share day-line matching, and daily mark-to-market valuation. Every artifact
records the data, factor, rule, and configuration versions it was built from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ashare_multifactor.backtest.config import BacktestConfig
from ashare_multifactor.data.contracts import CanonicalDataset
from ashare_multifactor.data.universe import (
    REQUIRED_DATASETS,
    RULE_VERSION as UNIVERSE_RULE_VERSION,
    build_universe,
)
from ashare_multifactor.execution.account import Account
from ashare_multifactor.execution.orders import create_order_intents
from ashare_multifactor.execution.simulator import (
    ExecutionRules,
    LimitRules,
    OrderIntent,
    execute_orders,
)
from ashare_multifactor.factors.composite import COMPOSITE_VERSION, composite_score
from ashare_multifactor.factors.definitions import (
    compute_raw_factors,
    default_factor_specs,
    factor_version,
)
from ashare_multifactor.factors.preprocess import preprocess_version, process_factor
from ashare_multifactor.portfolio.constructor import (
    PORTFOLIO_VERSION,
    construct_target_portfolio,
)
from ashare_multifactor.reports.performance import (
    pearson,
    performance_metrics,
    spearman,
    write_csv,
    write_json,
)

SHANGHAI_TZ = timezone(timedelta(hours=8))
DECISION_HOUR = 18
ENGINE_VERSION = "monthly-backtest-engine-v1"
CORPORATE_ACTION_TYPES = ("split", "cash_dividend")
LIMITATIONS = (
    "day-line matching uses the next trading day open as the execution reference price",
    "delisting settlement is not modelled; delisted names are excluded from new targets",
    "factor prices use the backward-adjusted series while execution and valuation use raw prices",
    "cash dividends are credited to cash and are not re-applied through adjusted prices",
)


class BacktestError(ValueError):
    """Raised when the inputs cannot support a reproducible monthly run."""


@dataclass(frozen=True)
class BacktestResult:
    manifest: dict[str, Any]
    targets: tuple[dict[str, Any], ...]
    orders: tuple[dict[str, Any], ...]
    skips: tuple[dict[str, Any], ...]
    fills: tuple[dict[str, Any], ...]
    rejects: tuple[dict[str, Any], ...]
    daily: tuple[dict[str, Any], ...]
    factors: tuple[dict[str, Any], ...]
    monthly: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]


def run_backtest(
    datasets: Mapping[str, CanonicalDataset],
    config: BacktestConfig,
) -> BacktestResult:
    """Run one monthly multifactor backtest over an immutable snapshot."""
    _validate_datasets(datasets, config)

    specs = default_factor_specs()
    rules = config.execution_rules()
    limits = config.limit_rules()
    constraints = config.portfolio_constraints()

    bars_by_day = _raw_bar_index(datasets["daily_prices"])
    adjusted = _adjusted_index(datasets["daily_prices"])
    statuses_by_day = _status_index(datasets["security_status"])
    actions = _corporate_actions(datasets.get("corporate_actions"))

    open_days = _open_days(datasets["trade_calendar"], config)
    decision_days = _month_end_open_days(open_days)
    next_open = {
        open_days[index]: open_days[index + 1] for index in range(len(open_days) - 1)
    }

    account = Account(cash=config.initial_cash)
    pending: tuple[OrderIntent, ...] = ()
    last_close: dict[str, float] = {}
    peak_nav = 1.0

    targets: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    factor_rows: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    last_cutoff = ""

    for day in open_days:
        account.start_of_day()
        _apply_corporate_actions(account, actions, day)

        day_bars = bars_by_day.get(day, {})
        day_status = statuses_by_day.get(day, {})

        if pending:
            execution = execute_orders(
                pending, account, day_bars, day_status, rules, limits
            )
            for fill in execution.fills:
                fills.append(_fill_row(fill))
            for reject in execution.rejects:
                rejects.append(_reject_row(reject))
            pending = ()

        for symbol, bar in day_bars.items():
            close = _number(bar.get("close"))
            if close is not None and close > 0:
                last_close[symbol] = close

        if day in decision_days:
            cutoff = datetime.combine(day, time(DECISION_HOUR), SHANGHAI_TZ)
            last_cutoff = cutoff.isoformat()
            decision = _decide(
                datasets=datasets,
                config=config,
                specs=specs,
                day=day,
                cutoff=cutoff,
                execution_day=next_open.get(day),
                account=account,
                last_close=last_close,
                rules=rules,
                constraints=constraints,
            )
            decisions.append(decision)
            targets.extend(decision["targets"])
            factor_rows.extend(decision["factors"])
            orders.extend(decision["orders"])
            skips.extend(decision["skips"])
            pending = decision["intents"]

        equity = account.total_equity(last_close)
        nav = equity / config.initial_cash
        peak_nav = max(peak_nav, nav)
        daily.append(
            {
                "trade_date": day.isoformat(),
                "cash": round(account.cash, 4),
                "market_value": round(account.market_value(last_close), 4),
                "total_equity": round(equity, 4),
                "nav": round(nav, 8),
                "drawdown": round(nav / peak_nav - 1.0, 8) if peak_nav > 0 else 0.0,
                "position_count": len(account.positions),
                "stale_positions": sum(
                    1 for symbol in account.positions if symbol not in day_bars
                ),
                "fees_paid": round(account.fees_paid, 4),
                "realized_pnl": round(account.realized_pnl, 4),
            }
        )

    monthly = _monthly_rows(decisions, daily, fills, adjusted)
    metrics = performance_metrics(daily, monthly, fills, rejects, skips)
    manifest = _manifest(
        datasets=datasets,
        config=config,
        specs=specs,
        rules=rules,
        limits=limits,
        daily=daily,
        decisions=decisions,
        last_cutoff=last_cutoff,
        corporate_action_count=len(actions),
    )
    return BacktestResult(
        manifest=manifest,
        targets=tuple(targets),
        orders=tuple(orders),
        skips=tuple(skips),
        fills=tuple(fills),
        rejects=tuple(rejects),
        daily=tuple(daily),
        factors=tuple(factor_rows),
        monthly=tuple(monthly),
        metrics=metrics,
    )


def write_run_artifacts(result: BacktestResult, output_dir: Path) -> Path:
    """Write manifest, target, order, execution, valuation, and report files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "manifest.json", result.manifest)
    write_json(output_dir / "metrics.json", result.metrics)
    write_csv(output_dir / "targets.csv", result.targets)
    write_csv(output_dir / "orders.csv", result.orders)
    write_csv(output_dir / "order_skips.csv", result.skips)
    write_csv(output_dir / "fills.csv", result.fills)
    write_csv(output_dir / "unfilled.csv", result.rejects)
    write_csv(output_dir / "daily_account.csv", result.daily)
    write_csv(output_dir / "factor_snapshot.csv", result.factors)
    write_csv(output_dir / "monthly_evaluation.csv", result.monthly)
    return output_dir / "manifest.json"


def _decide(
    *,
    datasets: Mapping[str, CanonicalDataset],
    config: BacktestConfig,
    specs: Sequence[Any],
    day: date,
    cutoff: datetime,
    execution_day: date | None,
    account: Account,
    last_close: Mapping[str, float],
    rules: ExecutionRules,
    constraints: Any,
) -> dict[str, Any]:
    """Build one month-end target from data visible at the decision cutoff."""
    universe = build_universe(
        datasets, month=day.strftime("%Y-%m"), config=config.universe_config()
    )
    eligible = [row["symbol"] for row in universe.eligible]
    eligible_set = set(eligible)
    industries = {
        row["symbol"]: str(row.get("industry_code") or "UNKNOWN")
        for row in universe.eligible
    }
    closes_by_symbol = {
        symbol: closes
        for symbol, closes in _visible_closes(datasets["daily_prices"], day).items()
        if symbol in eligible_set
    }

    raw = compute_raw_factors(specs, closes_by_symbol)
    processed = [process_factor(spec, raw[spec.name], industries) for spec in specs]
    score = composite_score(processed)

    target = construct_target_portfolio(
        score,
        industries,
        constraints,
        decision_date=day.isoformat(),
        execution_date=execution_day.isoformat() if execution_day else None,
    )
    plan = create_order_intents(
        target,
        account,
        last_close,
        signal_time=cutoff.isoformat(),
        order_time=cutoff.isoformat(),
        execution_date=execution_day.isoformat() if execution_day else None,
        rules=rules,
    )

    targets = [
        {
            "decision_date": day.isoformat(),
            "execution_date": target.execution_date,
            "symbol": symbol,
            "weight": round(weight, 8),
            "score": round(target.scores[symbol], 8),
            "rank": target.ranks[symbol],
            "reason": target.reasons[symbol],
        }
        for symbol, weight in sorted(
            target.weights.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    order_rows = [
        {
            "decision_date": day.isoformat(),
            "execution_date": order.execution_date,
            "signal_time": order.signal_time,
            "order_time": order.order_time,
            "symbol": order.symbol,
            "side": order.side,
            "requested_volume": order.requested_volume,
            "requested_price": order.requested_price,
            "reason": order.reason,
        }
        for order in plan.orders
    ]
    skip_rows = [
        {
            "decision_date": day.isoformat(),
            "execution_date": target.execution_date,
            "symbol": skip.symbol,
            "side": skip.side,
            "volume": skip.volume,
            "reason": skip.reason,
        }
        for skip in plan.skipped
    ]

    factors = []
    for symbol in eligible:
        row: dict[str, Any] = {
            "decision_date": day.isoformat(),
            "symbol": symbol,
            "industry_code": industries[symbol],
            "composite_score": (
                round(score.values[symbol], 8) if symbol in score.values else None
            ),
            "rank": score.ranks.get(symbol),
            "target_weight": round(target.weights.get(symbol, 0.0), 8),
        }
        for factor in processed:
            row[f"raw_{factor.name}"] = factor.raw.get(symbol)
            row[f"processed_{factor.name}"] = factor.values.get(symbol)
        factors.append(row)

    return {
        "decision_date": day.isoformat(),
        "as_of": cutoff.isoformat(),
        "execution_date": target.execution_date,
        "eligible_count": len(universe.eligible),
        "excluded_count": len(universe.exclusions),
        "exclusion_counts": universe.summary.get("exclusion_counts", {}),
        "target_n": len(target.weights),
        "cash_weight": target.constraint_state.get("cash_weight"),
        "universe_synthetic": universe.summary.get("synthetic"),
        "factor_coverage_excluded": dict(score.excluded),
        "targets": targets,
        "factors": factors,
        "orders": order_rows,
        "skips": skip_rows,
        "intents": plan.orders,
    }


def _monthly_rows(
    decisions: Sequence[Mapping[str, Any]],
    daily: Sequence[Mapping[str, Any]],
    fills: Sequence[Mapping[str, Any]],
    adjusted: Mapping[str, Mapping[date, Mapping[str, float]]],
) -> list[dict[str, Any]]:
    """Attach realized monthly labels, IC, and turnover to each decision."""
    equity_by_day = {row["trade_date"]: float(row["total_equity"]) for row in daily}
    rows: list[dict[str, Any]] = []

    for index, decision in enumerate(decisions):
        entry = decision["execution_date"]
        next_decision = decisions[index + 1] if index + 1 < len(decisions) else None
        exit_day = next_decision["execution_date"] if next_decision else None

        scores: dict[str, float] = {}
        for row in decision["factors"]:
            if row["composite_score"] is not None:
                scores[row["symbol"]] = float(row["composite_score"])

        labels: dict[str, float] = {}
        if entry and exit_day:
            for symbol in scores:
                entry_price = _adjusted_price(
                    adjusted, symbol, date.fromisoformat(entry)
                )
                exit_price = _adjusted_price(
                    adjusted, symbol, date.fromisoformat(exit_day)
                )
                if entry_price and exit_price:
                    labels[symbol] = exit_price / entry_price - 1.0

        shared = sorted(set(scores) & set(labels))
        ic = pearson([scores[s] for s in shared], [labels[s] for s in shared])
        rank_ic = spearman([scores[s] for s in shared], [labels[s] for s in shared])

        executed = [row for row in fills if row["execution_date"] == entry]
        buy_value = sum(
            float(row["gross_amount"]) for row in executed if row["side"] == "buy"
        )
        sell_value = sum(
            float(row["gross_amount"]) for row in executed if row["side"] == "sell"
        )
        equity = equity_by_day.get(decision["decision_date"], 0.0)
        turnover = 0.5 * (buy_value + sell_value) / equity if equity > 0 else None

        rows.append(
            {
                "decision_date": decision["decision_date"],
                "as_of": decision["as_of"],
                "execution_date": entry,
                "next_execution_date": exit_day,
                "label_available_at": f"{exit_day}T18:00:00+08:00" if exit_day else None,
                "label_pending": exit_day is None,
                "eligible_count": decision["eligible_count"],
                "excluded_count": decision["excluded_count"],
                "target_n": decision["target_n"],
                "cash_weight": decision["cash_weight"],
                "scored_symbols": len(scores),
                "labelled_symbols": len(shared),
                "ic": ic,
                "rank_ic": rank_ic,
                "buy_value": round(buy_value, 4),
                "sell_value": round(sell_value, 4),
                "turnover_one_way": round(turnover, 8) if turnover is not None else None,
                "execution_fills": len(executed),
                "universe_synthetic": decision["universe_synthetic"],
                "exclusion_counts": decision["exclusion_counts"],
            }
        )
    return rows


def _visible_closes(
    dataset: CanonicalDataset,
    day: date,
) -> dict[str, list[float]]:
    """Backward-adjusted closes per symbol, strictly up to the decision day."""
    cutoff = day.isoformat()
    series: dict[str, list[tuple[date, float]]] = {}
    for row in dataset.rows:
        if row.get("adjustment") != "backward":
            continue
        trade_date = str(row.get("trade_date") or "")
        if not trade_date or trade_date > cutoff:
            continue
        close = _number(row.get("close"))
        if close is None or close <= 0:
            continue
        series.setdefault(str(row["symbol"]), []).append(
            (date.fromisoformat(trade_date), close)
        )
    return {
        symbol: [close for _, close in sorted(entries)]
        for symbol, entries in series.items()
    }


def _adjusted_index(
    dataset: CanonicalDataset,
) -> dict[str, dict[date, dict[str, float]]]:
    """Backward-adjusted open and close per symbol and trading day."""
    index: dict[str, dict[date, dict[str, float]]] = {}
    for row in dataset.rows:
        if row.get("adjustment") != "backward":
            continue
        trade_date = str(row.get("trade_date") or "")
        if not trade_date:
            continue
        index.setdefault(str(row["symbol"]), {})[date.fromisoformat(trade_date)] = {
            "open": _number(row.get("open")),
            "close": _number(row.get("close")),
        }
    return index


def _adjusted_price(
    adjusted: Mapping[str, Mapping[date, Mapping[str, float]]],
    symbol: str,
    day: date,
) -> float | None:
    entry = adjusted.get(symbol, {}).get(day)
    if entry is None:
        return None
    return entry.get("open") or entry.get("close")


def _raw_bar_index(
    dataset: CanonicalDataset,
) -> dict[date, dict[str, Mapping[str, Any]]]:
    """Raw (unadjusted) bars per trading day, used for matching and valuation."""
    index: dict[date, dict[str, Mapping[str, Any]]] = {}
    for row in dataset.rows:
        if row.get("adjustment") != "none":
            continue
        trade_date = str(row.get("trade_date") or "")
        if not trade_date:
            continue
        index.setdefault(date.fromisoformat(trade_date), {})[str(row["symbol"])] = row
    return index


def _status_index(
    dataset: CanonicalDataset,
) -> dict[date, dict[str, Mapping[str, Any]]]:
    index: dict[date, dict[str, Mapping[str, Any]]] = {}
    for row in dataset.rows:
        trade_date = str(row.get("trade_date") or "")
        if not trade_date:
            continue
        index.setdefault(date.fromisoformat(trade_date), {})[str(row["symbol"])] = row
    return index


def _corporate_actions(
    dataset: CanonicalDataset | None,
) -> list[dict[str, Any]]:
    if dataset is None:
        return []
    actions = []
    for row in dataset.rows:
        action_type = str(row.get("action_type") or "")
        if action_type not in CORPORATE_ACTION_TYPES:
            raise BacktestError(f"Unsupported corporate action {action_type!r}.")
        actions.append(dict(row))
    return sorted(actions, key=lambda row: (row["ex_date"], row["symbol"]))


def _apply_corporate_actions(
    account: Account,
    actions: Sequence[Mapping[str, Any]],
    day: date,
) -> None:
    cutoff = datetime.combine(day, time(DECISION_HOUR), SHANGHAI_TZ)
    for action in actions:
        if action["ex_date"] != day.isoformat():
            continue
        available_at = datetime.fromisoformat(str(action.get("available_at")))
        if available_at > cutoff:
            raise BacktestError(
                f"Corporate action for {action['symbol']} was not visible on its ex-date."
            )
        if action["action_type"] == "split":
            account.apply_split(
                str(action["symbol"]), float(action["ratio"]), day.isoformat()
            )
        else:
            account.apply_cash_dividend(
                str(action["symbol"]),
                float(action["dividend_per_share"]),
                day.isoformat(),
            )


def _open_days(
    dataset: CanonicalDataset,
    config: BacktestConfig,
) -> list[date]:
    days = sorted(
        {
            str(row["trade_date"])
            for row in dataset.rows
            if row.get("is_open") == 1
            and config.start_date <= str(row["trade_date"]) <= config.end_date
        }
    )
    if not days:
        raise BacktestError("No trading day inside the requested window.")
    return [date.fromisoformat(day) for day in days]


def _month_end_open_days(open_days: Sequence[date]) -> set[date]:
    last_per_month: dict[tuple[int, int], date] = {}
    for day in open_days:
        last_per_month[(day.year, day.month)] = day
    return set(last_per_month.values())


def _manifest(
    *,
    datasets: Mapping[str, CanonicalDataset],
    config: BacktestConfig,
    specs: Sequence[Any],
    rules: ExecutionRules,
    limits: LimitRules,
    daily: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    last_cutoff: str,
    corporate_action_count: int,
) -> dict[str, Any]:
    return {
        "engine_version": ENGINE_VERSION,
        "data_version": config.data_version,
        "config_version": config.version(),
        "config": config.payload(),
        "factor_version": factor_version(specs),
        "preprocess_version": preprocess_version(),
        "composite_version": COMPOSITE_VERSION,
        "portfolio_version": PORTFOLIO_VERSION,
        "universe_rule_version": UNIVERSE_RULE_VERSION,
        "execution_rule_version": rules.rule_version,
        "limit_rule_version": limits.rule_version,
        "start_date": config.start_date,
        "end_date": config.end_date,
        "as_of": last_cutoff,
        "random_seed": config.random_seed,
        "initial_cash": config.initial_cash,
        "decision_count": len(decisions),
        "daily_observations": len(daily),
        "corporate_actions": corporate_action_count,
        "datasets": sorted(datasets),
        "synthetic": any(
            dataset.metadata.get("synthetic") for dataset in datasets.values()
        ),
        "limitations": list(LIMITATIONS),
    }


def _validate_datasets(
    datasets: Mapping[str, CanonicalDataset],
    config: BacktestConfig,
) -> None:
    missing = [name for name in REQUIRED_DATASETS if name not in datasets]
    if missing:
        raise BacktestError(f"Missing datasets: {sorted(missing)}")
    if config.start_date > config.end_date:
        raise BacktestError("start_date must not be after end_date.")
    for name in REQUIRED_DATASETS:
        if not datasets[name].rows:
            raise BacktestError(f"Dataset {name!r} contains no rows.")


def _fill_row(fill: Any) -> dict[str, Any]:
    return {
        "execution_date": fill.execution_date,
        "symbol": fill.symbol,
        "side": fill.side,
        "volume": fill.volume,
        "price": fill.price,
        "gross_amount": fill.gross_amount,
        "fee": fill.fee,
        "slippage_cost": fill.slippage_cost,
        "status": fill.status,
        "reason": fill.reason,
    }


def _reject_row(reject: Any) -> dict[str, Any]:
    return {
        "execution_date": reject.execution_date,
        "symbol": reject.symbol,
        "side": reject.side,
        "requested_volume": reject.requested_volume,
        "filled_volume": reject.filled_volume,
        "reason": reject.reason,
    }


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number
