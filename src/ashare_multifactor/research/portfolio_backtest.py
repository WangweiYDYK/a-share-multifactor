"""Monthly weight-level portfolio research from saved factor snapshots.

This module evaluates signal portfolios before a full share/account simulation.
Backward-adjusted open-to-open returns keep corporate actions from creating
artificial jumps, while turnover-based costs provide a conservative first view
of implementation drag.
"""

from __future__ import annotations

import html
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence

import pandas as pd

from ashare_multifactor.reports.performance import write_csv, write_json

PORTFOLIO_RESEARCH_VERSION = "price-factor-portfolio-v1"
CASH = "__CASH__"
STRATEGIES = (
    "reversal_20d",
    "low_vol_60d",
    "dual_factor",
    "three_factor",
    "eligible_equal_weight",
)
STRATEGY_NAMES = {
    "reversal_20d": "20日反转",
    "low_vol_60d": "60日低波",
    "dual_factor": "反转+低波",
    "three_factor": "原三因子",
    "eligible_equal_weight": "合格股票等权基准",
}
SNAPSHOT_COLUMNS = (
    "decision_date",
    "entry_date",
    "exit_date",
    "symbol",
    "forward_return",
    "score_reversal_20d",
    "score_momentum_60d",
    "score_low_vol_60d",
    "composite_score",
)


@dataclass(frozen=True)
class PortfolioResearchConfig:
    """Assumptions for the price-factor portfolio comparison."""

    top_n: int = 30
    cash_buffer: float = 0.05
    commission_rate: float = 0.0003
    stamp_tax_sell: float = 0.0005
    transfer_fee: float = 0.00001
    slippage_bps: float = 10.0
    development_end: str = "2023-12-31"


def run_portfolio_research(
    factor_snapshot: Path,
    output_dir: Path,
    config: PortfolioResearchConfig | None = None,
) -> Path:
    """Compare four signal portfolios and an eligible-universe benchmark."""
    cfg = config or PortfolioResearchConfig()
    _validate_config(cfg)
    snapshot_path = Path(factor_snapshot).resolve()
    output = Path(output_dir).resolve()
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"Factor snapshot not found: {snapshot_path}")
    if output.exists():
        raise ValueError(f"Output directory already exists: {output}")

    frame = pd.read_csv(snapshot_path, usecols=SNAPSHOT_COLUMNS)
    _prepare_snapshot(frame)
    if frame.empty:
        raise ValueError("Factor snapshot has no observations.")

    monthly_rows: list[dict[str, Any]] = []
    holding_rows: list[dict[str, Any]] = []
    metrics_rows: list[dict[str, Any]] = []
    yearly_rows: list[dict[str, Any]] = []

    for strategy in STRATEGIES:
        rows, holdings = _simulate(frame, strategy, cfg, cost_multiplier=1.0)
        monthly_rows.extend(rows)
        if strategy != "eligible_equal_weight":
            holding_rows.extend(holdings)
        strategy_metrics = _period_metrics(rows, cfg.development_end)
        metrics_rows.extend(strategy_metrics)
        yearly_rows.extend(_yearly_returns(rows, strategy))

    _attach_excess_returns(metrics_rows)
    sensitivity_rows: list[dict[str, Any]] = []
    for multiplier in (0.0, 1.0, 2.0):
        rows, _ = _simulate(frame, "dual_factor", cfg, cost_multiplier=multiplier)
        for metric in _period_metrics(rows, cfg.development_end):
            if metric["period"] in ("full", "validation"):
                sensitivity_rows.append(
                    {
                        "cost_multiplier": multiplier,
                        "period": metric["period"],
                        "months": metric["months"],
                        "total_return": metric["total_return"],
                        "annualized_return": metric["annualized_return"],
                        "max_drawdown": metric["max_drawdown"],
                        "total_cost": metric["total_cost"],
                    }
                )

    output.mkdir(parents=True)
    write_csv(output / "portfolio_metrics.csv", metrics_rows)
    write_csv(output / "monthly_portfolio.csv", monthly_rows)
    write_csv(output / "portfolio_holdings.csv", holding_rows)
    write_csv(output / "yearly_returns.csv", yearly_rows)
    write_csv(output / "cost_sensitivity.csv", sensitivity_rows)
    report = _render_report(
        metrics_rows,
        monthly_rows,
        yearly_rows,
        sensitivity_rows,
        cfg,
    )
    (output / "portfolio_report.html").write_text(report, encoding="utf-8")

    manifest = {
        "research_version": PORTFOLIO_RESEARCH_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "factor_snapshot": str(snapshot_path),
        "config": asdict(cfg),
        "strategies": list(STRATEGIES),
        "strategy_names": STRATEGY_NAMES,
        "decision_months": int(frame["decision_date"].nunique()),
        "symbols": int(frame["symbol"].nunique()),
        "outputs": [
            "portfolio_report.html",
            "portfolio_metrics.csv",
            "monthly_portfolio.csv",
            "portfolio_holdings.csv",
            "yearly_returns.csv",
            "cost_sensitivity.csv",
        ],
        "limitations": [
            "This is a monthly weight-level research backtest, not a share/account ledger.",
            (
                "Returns use backward-adjusted open prices; exact corporate-action cash and "
                "shares are absent."
            ),
            "Missing selected-symbol returns are conservatively treated as zero and reported.",
            "Costs are proportional estimates and do not include minimum commission per order.",
            "The benchmark is the eligible universe equal-weighted, not a published market index.",
            (
                "Industry and market-cap constraints are absent because point-in-time inputs "
                "are unavailable."
            ),
        ],
    }
    write_json(output / "manifest.json", manifest)
    return output / "manifest.json"


def _validate_config(config: PortfolioResearchConfig) -> None:
    if config.top_n < 1:
        raise ValueError("top_n must be positive.")
    if not 0 <= config.cash_buffer < 1:
        raise ValueError("cash_buffer must be in [0, 1).")
    costs = (
        config.commission_rate,
        config.stamp_tax_sell,
        config.transfer_fee,
        config.slippage_bps,
    )
    if any(value < 0 for value in costs):
        raise ValueError("Trading costs must be non-negative.")


def _prepare_snapshot(frame: pd.DataFrame) -> None:
    frame["decision_date"] = frame["decision_date"].astype(str)
    frame["entry_date"] = frame["entry_date"].astype(str)
    frame["exit_date"] = frame["exit_date"].astype(str)
    frame["symbol"] = frame["symbol"].astype(str)
    for column in SNAPSHOT_COLUMNS[4:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    duplicates = frame.duplicated(("decision_date", "symbol"))
    if duplicates.any():
        raise ValueError(
            "Factor snapshot contains duplicate decision/symbol rows: "
            f"{int(duplicates.sum())}"
        )
    frame.sort_values(["decision_date", "symbol"], inplace=True)


def _simulate(
    frame: pd.DataFrame,
    strategy: str,
    config: PortfolioResearchConfig,
    *,
    cost_multiplier: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    monthly: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    previous_targets: dict[str, float] | None = None
    previous_returns: dict[str, float] = {}
    nav = 1.0
    gross_nav = 1.0
    peak = 1.0

    for decision, group in frame.groupby("decision_date", sort=True):
        ranked = _rank_group(group, strategy)
        if ranked.empty:
            continue
        if strategy != "eligible_equal_weight":
            ranked = ranked.head(config.top_n)
        investable = 1.0 - config.cash_buffer
        weight = investable / len(ranked)
        targets = {str(symbol): weight for symbol in ranked["symbol"]}
        targets[CASH] = config.cash_buffer
        pretrade = _drifted_weights(previous_targets, previous_returns)
        turnover, buy_turnover, sell_turnover = _turnover(pretrade, targets)
        buy_rate = (
            config.commission_rate
            + config.transfer_fee
            + config.slippage_bps / 10_000.0
        )
        sell_rate = buy_rate + config.stamp_tax_sell
        cost = cost_multiplier * (
            buy_turnover * buy_rate + sell_turnover * sell_rate
        )

        returns: dict[str, float] = {}
        missing = 0
        gross_return = 0.0
        for rank_position, row in enumerate(ranked.itertuples(index=False), start=1):
            symbol = str(row.symbol)
            raw_return = row.forward_return
            if pd.isna(raw_return):
                value = 0.0
                missing += 1
            else:
                value = float(raw_return)
            returns[symbol] = value
            gross_return += weight * value
            holdings.append(
                {
                    "decision_date": decision,
                    "entry_date": str(row.entry_date),
                    "exit_date": str(row.exit_date),
                    "strategy": strategy,
                    "strategy_name": STRATEGY_NAMES[strategy],
                    "symbol": symbol,
                    "rank": rank_position,
                    "score": row.strategy_score,
                    "target_weight": weight,
                    "forward_return": None if pd.isna(raw_return) else float(raw_return),
                    "missing_return": bool(pd.isna(raw_return)),
                    "gross_contribution": weight * value,
                }
            )

        net_return = (1.0 - cost) * (1.0 + gross_return) - 1.0
        nav *= 1.0 + net_return
        gross_nav *= 1.0 + gross_return
        peak = max(peak, nav)
        first = ranked.iloc[0]
        monthly.append(
            {
                "decision_date": decision,
                "entry_date": str(first["entry_date"]),
                "exit_date": str(first["exit_date"]),
                "strategy": strategy,
                "strategy_name": STRATEGY_NAMES[strategy],
                "candidate_count": len(group),
                "position_count": len(ranked),
                "missing_return_positions": missing,
                "cash_weight": config.cash_buffer,
                "turnover_one_way": turnover,
                "buy_turnover": buy_turnover,
                "sell_turnover": sell_turnover,
                "trading_cost": cost,
                "gross_return": gross_return,
                "net_return": net_return,
                "gross_nav": gross_nav,
                "nav": nav,
                "drawdown": nav / peak - 1.0,
                "cost_multiplier": cost_multiplier,
            }
        )
        previous_targets = targets
        previous_returns = returns
    return monthly, holdings


def _rank_group(group: pd.DataFrame, strategy: str) -> pd.DataFrame:
    data = group.copy()
    if strategy == "reversal_20d":
        data["strategy_score"] = data["score_reversal_20d"]
    elif strategy == "low_vol_60d":
        data["strategy_score"] = data["score_low_vol_60d"]
    elif strategy == "dual_factor":
        data["strategy_score"] = (
            data["score_reversal_20d"] + data["score_low_vol_60d"]
        ) / 2.0
    elif strategy == "three_factor":
        data["strategy_score"] = data["composite_score"]
    elif strategy == "eligible_equal_weight":
        data["strategy_score"] = 0.0
    else:
        raise KeyError(f"Unknown strategy: {strategy}")
    if strategy != "eligible_equal_weight":
        data = data.loc[data["strategy_score"].notna()]
        data = data.sort_values(
            ["strategy_score", "symbol"], ascending=[False, True]
        )
    else:
        data = data.sort_values("symbol")
    return data


def _drifted_weights(
    previous_targets: Mapping[str, float] | None,
    previous_returns: Mapping[str, float],
) -> dict[str, float]:
    if previous_targets is None:
        return {CASH: 1.0}
    values = {
        symbol: weight
        * (1.0 if symbol == CASH else 1.0 + previous_returns.get(symbol, 0.0))
        for symbol, weight in previous_targets.items()
    }
    total = sum(values.values())
    if total <= 0:
        return {CASH: 1.0}
    return {symbol: value / total for symbol, value in values.items()}


def _turnover(
    current: Mapping[str, float], target: Mapping[str, float]
) -> tuple[float, float, float]:
    symbols = set(current) | set(target)
    turnover = 0.5 * sum(
        abs(target.get(symbol, 0.0) - current.get(symbol, 0.0))
        for symbol in symbols
    )
    stock_symbols = symbols - {CASH}
    buy = sum(
        max(0.0, target.get(symbol, 0.0) - current.get(symbol, 0.0))
        for symbol in stock_symbols
    )
    sell = sum(
        max(0.0, current.get(symbol, 0.0) - target.get(symbol, 0.0))
        for symbol in stock_symbols
    )
    return turnover, buy, sell


def _period_metrics(
    rows: Sequence[Mapping[str, Any]], development_end: str
) -> list[dict[str, Any]]:
    periods = {
        "full": list(rows),
        "development": [row for row in rows if row["decision_date"] <= development_end],
        "validation": [row for row in rows if row["decision_date"] > development_end],
    }
    return [
        _metrics_for_rows(subset, str(rows[0]["strategy"]), period)
        for period, subset in periods.items()
        if subset
    ]


def _metrics_for_rows(
    rows: Sequence[Mapping[str, Any]], strategy: str, period: str
) -> dict[str, Any]:
    net_returns = [float(row["net_return"]) for row in rows]
    gross_returns = [float(row["gross_return"]) for row in rows]
    navs = _compound_series(net_returns)
    gross_navs = _compound_series(gross_returns)
    months = len(rows)
    total_return = navs[-1] - 1.0
    gross_total_return = gross_navs[-1] - 1.0
    spread = pstdev(net_returns) if len(net_returns) > 1 else 0.0
    peak = 1.0
    drawdowns = []
    for nav in navs:
        peak = max(peak, nav)
        drawdowns.append(nav / peak - 1.0)
    return {
        "strategy": strategy,
        "strategy_name": STRATEGY_NAMES[strategy],
        "period": period,
        "start_decision": rows[0]["decision_date"],
        "end_decision": rows[-1]["decision_date"],
        "months": months,
        "gross_total_return": gross_total_return,
        "total_return": total_return,
        "cost_drag": gross_total_return - total_return,
        "annualized_return": (1.0 + total_return) ** (12.0 / months) - 1.0,
        "annualized_volatility": spread * math.sqrt(12.0),
        "sharpe": fmean(net_returns) / spread * math.sqrt(12.0) if spread > 0 else None,
        "max_drawdown": min(drawdowns) if drawdowns else 0.0,
        "monthly_win_rate": sum(value > 0 for value in net_returns) / months,
        "average_turnover": fmean(float(row["turnover_one_way"]) for row in rows),
        "total_cost": sum(float(row["trading_cost"]) for row in rows),
        "average_positions": fmean(float(row["position_count"]) for row in rows),
        "missing_return_positions": sum(
            int(row["missing_return_positions"]) for row in rows
        ),
        "excess_total_return_vs_benchmark": None,
    }


def _attach_excess_returns(metrics: list[dict[str, Any]]) -> None:
    benchmark = {
        str(row["period"]): float(row["total_return"])
        for row in metrics
        if row["strategy"] == "eligible_equal_weight"
    }
    for row in metrics:
        base = benchmark.get(str(row["period"]))
        if base is not None and 1.0 + base > 0:
            row["excess_total_return_vs_benchmark"] = (
                (1.0 + float(row["total_return"])) / (1.0 + base) - 1.0
            )


def _compound_series(returns: Sequence[float]) -> list[float]:
    nav = 1.0
    result = []
    for value in returns:
        nav *= 1.0 + value
        result.append(nav)
    return result


def _yearly_returns(
    rows: Sequence[Mapping[str, Any]], strategy: str
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["exit_date"])[:4], []).append(row)
    result = []
    for year, year_rows in sorted(grouped.items()):
        net = _compound_series([float(row["net_return"]) for row in year_rows])[-1] - 1.0
        gross = _compound_series([float(row["gross_return"]) for row in year_rows])[-1] - 1.0
        result.append(
            {
                "year": year,
                "strategy": strategy,
                "strategy_name": STRATEGY_NAMES[strategy],
                "months": len(year_rows),
                "gross_return": gross,
                "net_return": net,
                "trading_cost": sum(float(row["trading_cost"]) for row in year_rows),
            }
        )
    return result


def _render_report(
    metrics: Sequence[Mapping[str, Any]],
    monthly: Sequence[Mapping[str, Any]],
    yearly: Sequence[Mapping[str, Any]],
    sensitivity: Sequence[Mapping[str, Any]],
    config: PortfolioResearchConfig,
) -> str:
    full = [row for row in metrics if row["period"] == "full"]
    validation = [row for row in metrics if row["period"] == "validation"]
    conclusion = _report_conclusion(validation)
    sections = [
        "<p class=note>这是权重级研究回测，不是精确股票账户。信号在月末生成，"
        "下一交易日开盘建立组合，收益使用后复权开盘价，交易成本按换手估算。</p>",
        "<h2>这套回测怎么运行</h2>",
        (
            f"<ol><li>每月筛选合格股票并计算因子。</li><li>选择前{config.top_n}只，"
            f"投入{1-config.cash_buffer:.0%}，保留{config.cash_buffer:.0%}现金。</li>"
            "<li>持有到下个月调仓日，按换手扣除佣金、印花税、过户费和滑点。</li>"
            "<li>同时运行四个因子方案和合格股票等权基准。</li></ol>"
        ),
        "<h2>本次结果怎么理解</h2>",
        conclusion,
        "<h2>全区间结果</h2>",
        _metric_table(full),
        "<h2>验证区间结果</h2>",
        _metric_table(validation),
        "<h2>净值曲线</h2>",
        _nav_svg(monthly),
        "<p class=caption>净值从1开始；曲线越高代表累计收益越高。必须结合最大回撤和"
        "验证区间一起判断，不能只看终点。</p>",
        "<h2>分年度收益</h2>",
        _yearly_table(yearly),
        "<h2>双因子成本敏感性</h2>",
        _sensitivity_table(sensitivity),
        "<h2>重要限制</h2>",
        (
            "<ul><li>当前没有精确模拟股数、最低佣金、分红现金和送转股份。</li>"
            "<li>缺失收益按0处理并单独计数，没有使用未来数据重新挑股票。</li>"
            "<li>基准是合格股票等权，不是沪深300或中证全指。</li>"
            "<li>尚未做历史行业、市值中性化，也没有完整涨跌停和部分成交模型。</li>"
            "<li>研究结果不构成投资建议。</li></ul>"
        ),
    ]
    return _page("价格双因子组合回测", "".join(sections))


def _metric_table(rows: Sequence[Mapping[str, Any]]) -> str:
    display = []
    for row in rows:
        display.append(
            {
                "方案": row["strategy_name"],
                "月数": row["months"],
                "累计收益": _percent(row["total_return"]),
                "年化收益": _percent(row["annualized_return"]),
                "最大回撤": _percent(row["max_drawdown"]),
                "夏普": _decimal(row["sharpe"]),
                "月胜率": _percent(row["monthly_win_rate"]),
                "平均换手": _percent(row["average_turnover"]),
                "成本拖累": _percent(row["cost_drag"]),
                "相对基准": _percent(row["excess_total_return_vs_benchmark"]),
            }
        )
    return _table(
        display,
        (
            "方案",
            "月数",
            "累计收益",
            "年化收益",
            "最大回撤",
            "夏普",
            "月胜率",
            "平均换手",
            "成本拖累",
            "相对基准",
        ),
    )


def _report_conclusion(validation: Sequence[Mapping[str, Any]]) -> str:
    by_strategy = {str(row["strategy"]): row for row in validation}
    dual = by_strategy.get("dual_factor")
    three = by_strategy.get("three_factor")
    benchmark = by_strategy.get("eligible_equal_weight")
    if not dual or not three or not benchmark:
        return "<p>验证区间数据不足，暂时不能形成结论。</p>"
    dual_return = float(dual["total_return"])
    three_return = float(three["total_return"])
    base_return = float(benchmark["total_return"])
    statements = [
        "双因子验证区间累计收益为{}，最大回撤为{}。".format(
            _percent(dual_return), _percent(dual["max_drawdown"])
        )
    ]
    if dual_return > three_return:
        statements.append("双因子优于原三因子，说明移除60日动量在验证区间有帮助。")
    else:
        statements.append("双因子没有优于原三因子，移除60日动量尚未带来组合改善。")
    if (1.0 + dual_return) / (1.0 + base_return) - 1.0 > 0:
        statements.append("双因子跑赢合格股票等权基准，具备继续完善真实撮合的价值。")
    else:
        statements.append("双因子未跑赢合格股票等权基准，暂不宜升级为复杂账户模型。")
    return "<ul class=conclusions>" + "".join(
        f"<li>{html.escape(statement)}</li>" for statement in statements
    ) + "</ul>"


def _yearly_table(rows: Sequence[Mapping[str, Any]]) -> str:
    display = [
        {
            "年份": row["year"],
            "方案": row["strategy_name"],
            "月数": row["months"],
            "成本前收益": _percent(row["gross_return"]),
            "成本后收益": _percent(row["net_return"]),
        }
        for row in rows
    ]
    return _table(display, ("年份", "方案", "月数", "成本前收益", "成本后收益"))


def _sensitivity_table(rows: Sequence[Mapping[str, Any]]) -> str:
    display = [
        {
            "成本倍数": f"{float(row['cost_multiplier']):.0f}×",
            "区间": "全区间" if row["period"] == "full" else "验证区间",
            "累计收益": _percent(row["total_return"]),
            "年化收益": _percent(row["annualized_return"]),
            "最大回撤": _percent(row["max_drawdown"]),
        }
        for row in rows
    ]
    return _table(display, ("成本倍数", "区间", "累计收益", "年化收益", "最大回撤"))


def _nav_svg(rows: Sequence[Mapping[str, Any]]) -> str:
    series: dict[str, list[tuple[str, float]]] = {strategy: [] for strategy in STRATEGIES}
    for row in rows:
        series[str(row["strategy"])].append((str(row["exit_date"]), float(row["nav"])))
    values = [value for entries in series.values() for _, value in entries]
    if not values:
        return "<p>没有净值数据。</p>"
    width, height, pad = 1000, 360, 45
    lower, upper = min(values + [1.0]), max(values + [1.0])
    spread = upper - lower or 1.0
    colors = ("#d9485f", "#5f3dc4", "#087f5b", "#e67700", "#4263eb")
    paths = []
    legends = []
    for slot, (strategy, entries) in enumerate(series.items()):
        points = []
        for index, (_, value) in enumerate(entries):
            x = pad + index * (width - 2 * pad) / max(1, len(entries) - 1)
            y = pad + (upper - value) * (height - 2 * pad) / spread
            points.append(f"{x:.1f},{y:.1f}")
        color = colors[slot]
        paths.append(
            f'<polyline points="{" ".join(points)}" fill="none" '
            f'stroke="{color}" stroke-width="2.5"/>'
        )
        legends.append(
            f'<span><i style="background:{color}"></i>{STRATEGY_NAMES[strategy]}</span>'
        )
    baseline_y = pad + (upper - 1.0) * (height - 2 * pad) / spread
    return (
        '<div class=legend>' + "".join(legends) + "</div>"
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="组合净值曲线">'
        f'<line x1="{pad}" y1="{baseline_y:.1f}" x2="{width-pad}" '
        f'y2="{baseline_y:.1f}" class="zero"/>'
        + "".join(paths)
        + "</svg>"
    )


def _table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{html.escape(str(row.get(column, '—')))}</td>" for column in columns)
        + "</tr>"
        for row in rows
    )
    return (
        f"<div class=table-wrap><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _percent(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    return f"{number:.2%}" if math.isfinite(number) else "—"


def _decimal(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    return f"{number:.3f}" if math.isfinite(number) else "—"


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(title)}</title><style>
body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;
background:#f4f6f5;color:#18332b}}
main{{max-width:1180px;margin:0 auto;padding:32px 24px 64px}}
h1{{font-size:30px}}h2{{margin-top:34px}}li{{line-height:1.65;margin:7px 0}}
.note{{padding:15px 17px;background:#fff5d6;border-left:4px solid #d89d00}}
.caption{{font-size:13px;color:#586b64;line-height:1.7}}
.table-wrap{{overflow:auto;background:white;border:1px solid #dce5e1;border-radius:12px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{padding:9px 11px;border-bottom:1px solid #e8eeeb;text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}}th{{background:#eaf2ee}}
svg{{width:100%;background:white;border:1px solid #dce5e1;border-radius:12px}}
.zero{{stroke:#9fafaa;stroke-dasharray:5 5}}.legend{{display:flex;gap:18px;flex-wrap:wrap}}
.legend span{{font-size:13px}}.legend i{{display:inline-block;width:12px;height:12px;
margin-right:5px;border-radius:50%}}
</style></head><body><main><h1>{html.escape(title)}</h1>{body}</main></body></html>"""
