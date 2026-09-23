"""Build an auditable month-end research universe from one pinned snapshot.

Daily status is observed, never inferred from missing prices. Industry intervals
are [effective_from, effective_to). Financial filters and execution are not part
of this prototype. Inputs must have verified historical availability semantics.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
from bisect import bisect_left
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

from ashare_multifactor.data.contracts import CanonicalDataset
from ashare_multifactor.data.repository import DataRepository

SHANGHAI_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = Path(__file__).resolve().parents[3]
RULE_VERSION = "universe-prototype-1"
REQUIRED_DATASETS = (
    "trade_calendar", "security_master", "daily_prices", "daily_basic",
    "security_status", "industry_membership",
)


class UniverseBuildError(ValueError):
    """The input cannot support a reliable month-end decision."""


@dataclass(frozen=True)
class UniverseConfig:
    """Explicit research thresholds; amounts and market caps are in yuan."""

    min_average_amount: float
    industry_system: str
    min_listing_trading_days: int = 120
    liquidity_window: int = 20
    calendar_exchange: str = "SSE"


@dataclass(frozen=True)
class UniverseResult:
    """Deterministic decision rows and their input/configuration provenance."""

    decision_date: str
    decision_as_of: str
    eligible: tuple[dict[str, Any], ...]
    exclusions: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def build_universe_from_repository(
    repository: DataRepository,
    month: str,
    *,
    config: UniverseConfig,
    source: str | None = None,
) -> UniverseResult:
    """Read all datasets at the last session's 18:00 from one fixed snapshot."""
    if not repository.snapshot_id:
        raise UniverseBuildError("A fixed snapshot_id is required.")
    _validate_config(config)
    _, end = _month_bounds(month)
    calendar_data = repository.load(
        "trade_calendar", datetime.combine(end, time.max, SHANGHAI_TZ), source=source
    )
    cutoff, _ = _decision_calendar(calendar_data, month, config)
    datasets = {
        name: repository.load(name, as_of=cutoff, source=source)
        for name in REQUIRED_DATASETS
    }
    return build_universe(datasets, month=month, config=config)


def build_universe(
    datasets: Mapping[str, CanonicalDataset],
    *,
    month: str,
    config: UniverseConfig,
) -> UniverseResult:
    """Evaluate known securities without using current master status/industry.

    Require a complete calendar from the earliest visible listing through month
    end. Missing price days stay missing; suspended days are not imputed. An
    entirely absent decision-date dataset blocks the build.
    """
    _validate_config(config)
    missing = set(REQUIRED_DATASETS) - set(datasets)
    if missing:
        raise UniverseBuildError(f"Missing datasets: {sorted(missing)}")
    cutoff, sessions = _decision_calendar(datasets["trade_calendar"], month, config)
    day = cutoff.date().isoformat()
    rows = {name: _visible(datasets[name], cutoff) for name in REQUIRED_DATASETS}
    masters = _latest(rows["security_master"], ("symbol",))
    statuses = _latest(rows["security_status"], ("symbol", "trade_date"))
    basics = _latest(rows["daily_basic"], ("symbol", "trade_date"))
    prices = _latest(
        [row for row in rows["daily_prices"] if row.get("adjustment") == "none"],
        ("symbol", "trade_date"),
    )
    industries = _industries(rows["industry_membership"], day, config.industry_system)
    if not masters or not industries:
        raise UniverseBuildError("No visible security master or effective industry data.")
    symbols = {key[0] for key in masters}
    if not any((s, day) in statuses for s in symbols):
        raise UniverseBuildError("Decision-date status coverage is absent.")
    if not any((s, day) in basics for s in symbols):
        raise UniverseBuildError("Decision-date market-cap coverage is absent.")

    start, end = _month_bounds(month)
    listing_dates = [
        _date(row["list_date"]) for row in masters.values()
        if row.get("list_date") and _date(row["list_date"]) <= cutoff.date()
    ]
    _require_calendar(sessions, min([start, *listing_dates]), end)
    open_dates = sorted(d for d, row in sessions.items() if d <= day and row["is_open"] == 1)
    if len(open_dates) < config.liquidity_window:
        raise UniverseBuildError("Calendar does not cover the full liquidity window.")
    window = open_dates[-config.liquidity_window:]
    if not any(s in symbols and d in window for s, d in prices):
        raise UniverseBuildError("Raw-price coverage is absent for the liquidity window.")
    eligible, exclusions = [], []
    for (symbol,), master in sorted(masters.items()):
        status = statuses.get((symbol, day))
        basic = basics.get((symbol, day))
        industry = industries.get(symbol)
        reasons: list[str] = []
        listing_days = None
        if master.get("security_type") != "stock":
            reasons.append("not_stock")
        if not master.get("list_date"):
            reasons.append("missing_list_date")
        else:
            listed = _date(master["list_date"]).isoformat()
            listing_days = len(open_dates) - bisect_left(open_dates, listed)
            if listed > day:
                reasons.append("not_listed_at_decision")
            elif listing_days < config.min_listing_trading_days:
                reasons.append("listing_too_short")
        if master.get("delist_date") and _date(master["delist_date"]).isoformat() <= day:
            reasons.append("delisted_at_decision")
        reasons.extend(_status_reasons(status))
        total_mv = _number(basic.get("total_mv")) if basic else None
        circ_mv = _number(basic.get("circ_mv")) if basic else None
        if basic is None:
            reasons.append("missing_daily_basic")
        elif total_mv is None or circ_mv is None or min(total_mv, circ_mv) <= 0:
            reasons.append("invalid_market_cap")
        elif circ_mv > total_mv:
            reasons.append("inconsistent_market_cap")
        if industry is None:
            reasons.append("missing_industry")

        amounts = [_number(prices.get((symbol, d), {}).get("amount")) for d in window]
        valid = [value for value in amounts if value is not None and value >= 0]
        average = mean(valid) if len(valid) == config.liquidity_window else None
        if average is None:
            reasons.append("insufficient_price_history")
        elif average < config.min_average_amount:
            reasons.append("low_liquidity")
        row = {
            "symbol": symbol, "security_name": master.get("security_name"),
            "decision_date": day, "listing_trading_days": listing_days,
            "industry_code": industry.get("industry_code") if industry else None,
            "industry_name": industry.get("industry_name") if industry else None,
            "total_mv": total_mv, "circ_mv": circ_mv, "average_amount": average,
            "liquidity_observed_days": len(valid),
            "missing_amount_dates": [d for d, v in zip(window, amounts) if v is None or v < 0],
            "status": dict(status) if status else None,
            "industry": dict(industry) if industry else None,
            "security_master": dict(master),
            "daily_basic": dict(basic) if basic else None,
            "reasons": reasons,
        }
        (exclusions if reasons else eligible).append(row)

    counts = Counter(reason for row in exclusions for reason in row["reasons"])
    config_data = asdict(config)
    summary = {
        "mode": "research_prototype", "rule_version": RULE_VERSION,
        "synthetic": any(datasets[name].metadata.get("synthetic", False)
                         for name in REQUIRED_DATASETS),
        "factor_version": None, "random_seed": 0,
        "not_evaluated": ["financial_filters", "next_day_execution"],
        "month": month, "decision_date": day, "as_of": cutoff.isoformat(),
        "config": config_data, "config_version": _digest(config_data),
        "candidate_count": len(masters), "eligible_count": len(eligible),
        "excluded_count": len(exclusions), "exclusion_counts": dict(sorted(counts.items())),
        "liquidity_dates": window,
        "datasets": {
            name: {
                "snapshot_id": datasets[name].metadata.get("snapshot_id"),
                "source": _single(rows[name], "source"),
                "source_version": _single(rows[name], "source_version"),
                "availability_basis": datasets[name].metadata.get("availability_basis"),
                "visible_rows": len(rows[name]), "visible_content_sha256": _digest(rows[name]),
            }
            for name in REQUIRED_DATASETS
        },
    }
    return UniverseResult(day, cutoff.isoformat(), tuple(eligible), tuple(exclusions), summary)


def write_universe_result(output_dir: Path, result: UniverseResult) -> Path:
    """Write a new repository-local result directory, never overwrite a run."""
    output_dir = (PROJECT_ROOT / output_dir).resolve()
    try:
        output_dir.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise UniverseBuildError("Output must stay inside the repository.") from exc
    output_dir.mkdir(parents=True, exist_ok=False)
    for filename, rows in (("eligible", result.eligible), ("exclusions", result.exclusions)):
        (output_dir / f"{filename}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
    manifest = output_dir / "manifest.json"
    manifest.write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    return manifest


def _decision_calendar(
    dataset: CanonicalDataset, month: str, config: UniverseConfig
) -> tuple[datetime, dict[str, Mapping[str, Any]]]:
    start, end = _month_bounds(month)
    ceiling = datetime.combine(end, time.max, SHANGHAI_TZ)

    def known(at: datetime) -> dict[str, Mapping[str, Any]]:
        selected = _latest(
            [r for r in _visible(dataset, at) if r.get("exchange") == config.calendar_exchange],
            ("trade_date",),
        )
        result = {}
        for (day,), row in selected.items():
            _date(day)
            if row.get("is_open") not in (0, 1):
                raise UniverseBuildError(f"Invalid calendar state for {day}.")
            result[day] = row
        _require_calendar(result, start, end)
        return result

    def last_session(sessions: Mapping[str, Mapping[str, Any]]) -> str:
        dates = [d for d, r in sessions.items()
                 if start.isoformat() <= d <= end.isoformat() and r["is_open"] == 1]
        if not dates:
            raise UniverseBuildError("No trading session in requested month.")
        return max(dates)

    cutoff = datetime.combine(_date(last_session(known(ceiling))), time(18), SHANGHAI_TZ)
    sessions = known(cutoff)
    if last_session(sessions) != cutoff.date().isoformat():
        raise UniverseBuildError("Month-end calendar changed after the decision cutoff.")
    return cutoff, sessions


def _require_calendar(rows: Mapping[str, Any], start: date, end: date) -> None:
    day = start
    while day <= end:
        if day.isoformat() not in rows:
            raise UniverseBuildError(f"Calendar coverage missing for {day}.")
        day += timedelta(days=1)


def _visible(dataset: CanonicalDataset, cutoff: datetime) -> list[Mapping[str, Any]]:
    return [r for r in dataset.rows if _timestamp(r.get("available_at")) <= cutoff]


def _latest(
    rows: Sequence[Mapping[str, Any]], keys: tuple[str, ...]
) -> dict[tuple[str, ...], Mapping[str, Any]]:
    result: dict[tuple[str, ...], Mapping[str, Any]] = {}
    seen = set()
    for row in rows:
        if any(row.get(k) in (None, "") for k in keys):
            raise UniverseBuildError(f"Missing key fields {keys}.")
        key = tuple(str(row[k]) for k in keys)
        stamp = _timestamp(row["available_at"])
        identity = (key, stamp)
        if identity in seen:
            raise UniverseBuildError(f"Ambiguous duplicate version for {key} at {stamp}.")
        seen.add(identity)
        if key not in result or stamp > _timestamp(result[key]["available_at"]):
            result[key] = row
    return result


def _industries(
    rows: Sequence[Mapping[str, Any]], day: str, system: str
) -> dict[str, Mapping[str, Any]]:
    # Resolve revisions before intervals; an obsolete open-ended row must not reappear.
    revisions = _latest(
        [r for r in rows if r.get("industry_system") == system],
        ("symbol", "industry_system", "effective_from"),
    )
    result = {}
    for (symbol, _, start), row in revisions.items():
        end = _date(row["effective_to"]).isoformat() if row.get("effective_to") else None
        if end is not None and end <= _date(start).isoformat():
            raise UniverseBuildError(f"Invalid industry interval for {symbol}.")
        if _date(start).isoformat() <= day and (end is None or day < end):
            if symbol in result:
                raise UniverseBuildError(f"Overlapping industry intervals for {symbol}.")
            if row.get("industry_code") and row.get("industry_name"):
                result[symbol] = row
    return result


def _status_reasons(row: Mapping[str, Any] | None) -> list[str]:
    if row is None:
        return ["missing_security_status"]
    reasons = []
    fields = ("trade_status", "is_st", "is_suspended", "is_delisting")
    if any(row.get(k) not in (0, 1) for k in fields) or not row.get("list_status"):
        reasons.append("unknown_security_status")
    if row.get("list_status") not in (None, "", "listed"):
        reasons.append("not_listed")
    for field, excluded_value, reason in (
        ("trade_status", 0, "not_tradable"), ("is_st", 1, "st"),
        ("is_suspended", 1, "suspended"), ("is_delisting", 1, "delisting"),
    ):
        if row.get(field) == excluded_value:
            reasons.append(reason)
    return reasons


def _validate_config(config: UniverseConfig) -> None:
    for value in (config.min_listing_trading_days, config.liquidity_window):
        if type(value) is not int or value < 1:
            raise UniverseBuildError("Trading-day windows must be positive integers.")
    if _number(config.min_average_amount) is None or config.min_average_amount < 0:
        raise UniverseBuildError("Minimum average amount must be finite and nonnegative.")
    if not config.industry_system.strip() or not config.calendar_exchange.strip():
        raise UniverseBuildError("Industry system and calendar exchange are required.")


def _month_bounds(month: str) -> tuple[date, date]:
    try:
        first = date.fromisoformat(f"{month}-01")
        if first.strftime("%Y-%m") != month:
            raise ValueError
        return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])
    except (TypeError, ValueError) as exc:
        raise UniverseBuildError("Month must use YYYY-MM.") from exc


def _date(value: Any) -> date:
    try:
        result = date.fromisoformat(str(value))
    except ValueError as exc:
        raise UniverseBuildError(f"Invalid ISO date: {value!r}") from exc
    if result.isoformat() != value:
        raise UniverseBuildError(f"Expected YYYY-MM-DD: {value!r}")
    return result


def _timestamp(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise UniverseBuildError(f"Invalid available_at: {value!r}") from exc
    if result.tzinfo is None:
        raise UniverseBuildError("available_at must include a timezone.")
    return result


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _single(rows: Sequence[Mapping[str, Any]], field: str) -> str:
    values = {str(row[field]) for row in rows if row.get(field)}
    if len(values) != 1 or any(not row.get(field) for row in rows):
        raise UniverseBuildError(f"Expected one recorded {field} per dataset.")
    return next(iter(values))


def _digest(value: Any) -> str:
    content = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
