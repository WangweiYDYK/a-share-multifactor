"""Reference-only screen over whatever point-in-time data a snapshot provides.

The research backtest keeps the strict six-dataset universe. This screen is the
lighter path behind the manual buy list: it runs every check the snapshot can
support and reports the checks that could not run, instead of letting missing
data look like a pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Sequence

from ashare_multifactor.data.contracts import CanonicalDataset

SHANGHAI_TZ = timezone(timedelta(hours=8))
DECISION_HOUR = 18
SCREEN_VERSION = "picks-screen-v1"

REQUIRED_DATASETS = ("trade_calendar", "security_master", "daily_prices")
OPTIONAL_DATASETS = ("daily_basic", "security_status", "industry_membership")
ST_MARKERS = ("ST", "退")
MIN_FACTOR_HISTORY = 61


class PicksError(ValueError):
    """The snapshot cannot support a point-in-time pick list."""


@dataclass(frozen=True)
class PicksConfig:
    """Thresholds for one reference list; amounts and market caps are in yuan.

    `industry_system` may be left unset; the screen then uses the snapshot's
    own classification when it carries exactly one system.
    """

    as_of: str | None = None
    top_n: int = 20
    min_average_amount: float = 50_000_000.0
    min_listing_trading_days: int = 120
    liquidity_window: int = 20
    max_weight: float = 0.05
    cash_buffer: float = 0.05
    max_industry_weight: float = 0.20
    industry_system: str | None = None
    stale_quote_sessions: int = 5


@dataclass(frozen=True)
class Candidate:
    """One screened security plus everything the ranked list needs."""

    symbol: str
    security_name: str
    industry_code: str | None
    closes: tuple[float, ...]
    last_close: float
    last_quote_date: str
    average_amount: float
    listing_sessions: int | None
    circ_mv: float | None


@dataclass(frozen=True)
class ScreenResult:
    decision_date: str
    cutoff: str
    factor_basis: str
    candidates: tuple[Candidate, ...]
    exclusions: tuple[dict[str, Any], ...]
    screens: dict[str, str]
    summary: dict[str, Any]


def resolve_decision(
    datasets: Mapping[str, CanonicalDataset],
    config: PicksConfig,
) -> tuple[date, datetime]:
    """Return the decision day and its 18:00 cutoff inside the calendar."""
    calendar = datasets["trade_calendar"]
    open_days = sorted(
        str(row["trade_date"])
        for row in calendar.rows
        if _int(row.get("is_open")) == 1
        and (config.as_of is None or str(row["trade_date"]) <= config.as_of)
    )
    if not open_days:
        raise PicksError("No open trading day at or before the requested as_of.")
    day = date.fromisoformat(open_days[-1])
    cutoff = datetime.combine(day, time(DECISION_HOUR), SHANGHAI_TZ)
    return day, cutoff


def screen_snapshot(
    datasets: Mapping[str, CanonicalDataset],
    config: PicksConfig,
) -> ScreenResult:
    """Screen visible securities, reporting every check that cannot run."""
    _validate(datasets, config)
    day, cutoff = resolve_decision(datasets, config)

    status_rows = _visible(datasets.get("security_status"), cutoff)
    basic_rows = _visible(datasets.get("daily_basic"), cutoff)
    industry_rows = _visible(datasets.get("industry_membership"), cutoff)
    price_rows = _visible(datasets["daily_prices"], cutoff)

    backward = [row for row in price_rows if row.get("adjustment") == "backward"]
    raw = [row for row in price_rows if row.get("adjustment") == "none"]
    if backward:
        factor_rows, factor_basis = backward, "backward_adjusted"
    elif raw:
        factor_rows, factor_basis = raw, "raw_prices_no_adjustment"
    else:
        raise PicksError(
            "daily_prices has neither backward-adjusted nor raw rows in this window."
        )

    industry_system, industry_status = _select_industry_system(
        industry_rows, config.industry_system
    )
    industries = (
        _industries(industry_rows, day.isoformat(), industry_system)
        if industry_system is not None
        else {}
    )

    screens = _screen_coverage(
        config=config,
        day=day,
        has_backward=bool(backward),
        has_status=bool(status_rows),
        has_basic=bool(basic_rows),
        industry_status=industry_status,
        has_quote_status=any(_int(row.get("trade_status")) is not None for row in raw),
        has_quote_st=any(_int(row.get("is_st")) is not None for row in raw),
    )

    masters = _latest_by_symbol(datasets["security_master"].rows)
    latest_status = _latest_by_symbol(status_rows)
    latest_basic = _latest_by_symbol(basic_rows)
    sessions = _open_sessions(datasets["trade_calendar"], day)
    first_session = sessions[0] if sessions else day
    recent = set(sessions[-config.stale_quote_sessions :])
    amount_window = {
        session.isoformat() for session in sessions[-config.liquidity_window :]
    }

    closes_by_symbol: dict[str, list[tuple[str, float]]] = {}
    for row in _latest_by_day(factor_rows, "symbol", "trade_date").values():
        close = _number(row.get("close"))
        if close is None or close <= 0:
            continue
        closes_by_symbol.setdefault(str(row["symbol"]), []).append(
            (str(row["trade_date"]), close)
        )

    quotes = _latest_by_day(raw, "symbol", "trade_date")
    quote_by_symbol: dict[str, Mapping[str, Any]] = {}
    amounts: dict[str, dict[str, float]] = {}
    for (symbol, trade_date), row in quotes.items():
        current = quote_by_symbol.get(symbol)
        if current is None or trade_date > str(current.get("trade_date") or ""):
            quote_by_symbol[symbol] = row
        if trade_date not in amount_window:
            continue
        amount = _number(row.get("amount"))
        if amount is None or amount < 0:
            continue
        amounts.setdefault(symbol, {})[trade_date] = amount

    candidates: list[Candidate] = []
    exclusions: list[dict[str, Any]] = []
    for symbol in sorted(masters):
        master = masters[symbol]
        name = str(master.get("security_name") or symbol)
        series = sorted(closes_by_symbol.get(symbol, []))
        symbol_amounts = amounts.get(symbol, {})
        reason = _exclusion_reason(
            master=master,
            name=name,
            quote=quote_by_symbol.get(symbol),
            status=latest_status.get(symbol),
            day=day,
            first_session=first_session,
            sessions=sessions,
            recent=recent,
            series=series,
            amounts=symbol_amounts,
            config=config,
        )
        if reason is not None:
            exclusions.append(
                {"symbol": symbol, "security_name": name, "reason": reason}
            )
            continue
        basic = latest_basic.get(symbol) or {}
        candidates.append(
            Candidate(
                symbol=symbol,
                security_name=name,
                industry_code=industries.get(symbol),
                closes=tuple(close for _, close in series),
                last_close=series[-1][1],
                last_quote_date=series[-1][0],
                average_amount=sum(symbol_amounts.values()) / len(symbol_amounts),
                listing_sessions=_listing_sessions(
                    master, sessions, first_session, day
                ),
                circ_mv=_number(basic.get("circ_mv")) or _number(basic.get("total_mv")),
            )
        )

    return ScreenResult(
        decision_date=day.isoformat(),
        cutoff=cutoff.isoformat(),
        factor_basis=factor_basis,
        candidates=tuple(candidates),
        exclusions=tuple(exclusions),
        screens=screens,
        summary={
            "security_count": len(masters),
            "candidate_count": len(candidates),
            "excluded_count": len(exclusions),
            "exclusion_counts": _count_reasons(exclusions),
            "screen_version": SCREEN_VERSION,
        },
    )


def _validate(
    datasets: Mapping[str, CanonicalDataset],
    config: PicksConfig,
) -> None:
    missing = [
        name
        for name in REQUIRED_DATASETS
        if name not in datasets or not datasets[name].rows
    ]
    if missing:
        raise PicksError(
            "Missing required datasets {0}. The pick list needs a trade calendar, "
            "a security master, and daily prices.".format(sorted(missing))
        )
    if config.top_n < 1:
        raise PicksError("top_n must be at least 1.")
    if config.liquidity_window < 1:
        raise PicksError("liquidity_window must be at least 1.")


def _screen_coverage(
    *,
    config: PicksConfig,
    day: date,
    has_backward: bool,
    has_status: bool,
    has_basic: bool,
    industry_status: str,
    has_quote_status: bool,
    has_quote_st: bool,
) -> dict[str, str]:
    def _flag(applied: str, skipped: str, available: bool) -> str:
        return applied if available else skipped

    screens = {
        "decision_day": day.isoformat(),
        "listing_age": (
            "applied: trade_calendar sessions since list_date; unknown when the "
            "calendar starts after the listing date"
        ),
        "liquidity": "applied: daily_prices.amount over the last {0} sessions".format(
            config.liquidity_window
        ),
        "price_history": "applied: needs {0} closes".format(MIN_FACTOR_HISTORY),
        "price_adjustment": "applied: backward-adjusted prices"
        if has_backward
        else "applied: raw prices, dividends and splits are not adjusted",
        "suspension": "applied: security_status"
        if has_status
        else _flag(
            "applied: daily_prices.trade_status",
            "skipped: no suspension field in the snapshot",
            has_quote_status,
        ),
        "st": "applied: security_status"
        if has_status
        else _flag(
            "applied: daily_prices.is_st",
            "skipped: no ST field in the snapshot",
            has_quote_st,
        ),
        "market_cap": "displayed: daily_basic market value"
        if has_basic
        else "skipped: daily_basic is not in the snapshot",
        "industry": industry_status,
        "industry_neutralization": (
            "applied: demean inside industry groups"
            if industry_status.startswith("applied")
            else industry_status
        ),
        "delisting": "applied: security_status and security_master"
        if has_status
        else "applied: security_master only",
    }
    return screens


def _exclusion_reason(
    *,
    master: Mapping[str, Any],
    name: str,
    quote: Mapping[str, Any] | None,
    status: Mapping[str, Any] | None,
    day: date,
    first_session: date,
    sessions: Sequence[date],
    recent: set[date],
    series: Sequence[tuple[str, float]],
    amounts: Mapping[str, float],
    config: PicksConfig,
) -> str | None:
    list_date = _date(master.get("list_date"))
    if list_date is not None and list_date > day:
        return "not_listed_yet"
    delist = _date(master.get("delist_date"))
    if delist is not None and delist <= day:
        return "delisted"
    if str(master.get("list_status") or "") == "delisted":
        return "delisted"
    if _is_st(name):
        return "st_by_name"
    if str((status or {}).get("list_status") or "") == "delisted":
        return "delisted"
    if _int((status or {}).get("is_delisting")) == 1:
        return "delisting"

    st_flag = _int((status or {}).get("is_st"))
    if st_flag is None:
        st_flag = _int((quote or {}).get("is_st"))
    if st_flag == 1:
        return "st_flag"

    suspended = _int((status or {}).get("is_suspended"))
    if suspended is None:
        # A missing trade_status is unknown, not a suspension.
        trade_status = _int((quote or {}).get("trade_status"))
        if trade_status is not None:
            suspended = 1 if trade_status == 0 else 0
    if suspended == 1:
        return "suspended"

    if not series:
        return "no_price_history"
    if date.fromisoformat(series[-1][0]) not in recent:
        return "stale_quote"
    if len(series) < MIN_FACTOR_HISTORY:
        return "insufficient_price_history"
    if not amounts:
        return "missing_liquidity"
    if len(amounts) < max(1, config.liquidity_window // 2):
        return "partial_liquidity_window"
    if sum(amounts.values()) / len(amounts) < config.min_average_amount:
        return "low_liquidity"

    listing = _listing_sessions(master, sessions, first_session, day)
    if listing is not None and listing < config.min_listing_trading_days:
        return "listing_too_short"
    return None


def _listing_sessions(
    master: Mapping[str, Any],
    sessions: Sequence[date],
    first_session: date,
    day: date,
) -> int | None:
    """Trading sessions since listing, or None when the calendar starts later."""
    list_date = _date(master.get("list_date"))
    if list_date is None:
        return None
    if list_date < first_session:
        # The calendar does not reach back to the listing date, so the age is a
        # lower bound at best; report it as unknown instead of guessing.
        return None
    return sum(1 for session in sessions if list_date <= session <= day)


def _open_sessions(dataset: CanonicalDataset, day: date) -> list[date]:
    return sorted(
        {
            date.fromisoformat(str(row["trade_date"]))
            for row in dataset.rows
            if _int(row.get("is_open")) == 1
            and str(row["trade_date"]) <= day.isoformat()
        }
    )


def _industries(
    rows: Sequence[Mapping[str, Any]],
    day: str,
    industry_system: str,
) -> dict[str, str]:
    """Map symbols to the industry effective on the decision day."""
    result: dict[str, str] = {}
    for row in rows:
        if str(row.get("industry_system") or "") != industry_system:
            continue
        effective_from = str(row.get("effective_from") or "")
        effective_to = str(row.get("effective_to") or "")
        if not effective_from or effective_from > day:
            continue
        if effective_to and effective_to <= day:
            continue
        symbol = str(row.get("symbol") or "")
        code = str(row.get("industry_code") or "")
        if symbol and code:
            result[symbol] = code
    return result


def _select_industry_system(
    rows: Sequence[Mapping[str, Any]],
    requested: str | None,
) -> tuple[str | None, str]:
    """Return the classification to use and how that choice is reported."""
    systems = sorted(
        {
            str(row.get("industry_system"))
            for row in rows
            if str(row.get("industry_system") or "")
        }
    )
    if not systems:
        return None, "skipped: industry_membership is not in the snapshot"
    if requested:
        if requested not in systems:
            return None, (
                "skipped: industry_membership has no {0} rows (found {1})".format(
                    requested, ", ".join(systems)
                )
            )
        return requested, "applied: industry cap inside {0}".format(requested)
    if len(systems) == 1:
        return systems[0], "applied: industry cap inside {0}".format(systems[0])
    return None, (
        "skipped: industry_membership mixes {0}; set industry_system".format(
            ", ".join(systems)
        )
    )


def _visible(
    dataset: CanonicalDataset | None,
    cutoff: datetime,
) -> list[Mapping[str, Any]]:
    """Rows whose timezone-aware availability stamp is not in the future."""
    if dataset is None:
        return []
    rows = []
    for row in dataset.rows:
        stamp = _timestamp(row.get("available_at"))
        if stamp is not None and stamp <= cutoff:
            rows.append(row)
    return rows


def _latest_by_symbol(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        current = result.get(symbol)
        if current is None or _timestamp(row.get("available_at")) > _timestamp(
            current.get("available_at")
        ):
            result[symbol] = row
    return result


def _latest_by_day(
    rows: Sequence[Mapping[str, Any]],
    symbol_key: str,
    day_key: str,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    """One row per symbol and trading day, keeping the most recent revision."""
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (str(row.get(symbol_key) or ""), str(row.get(day_key) or ""))
        if not key[0] or not key[1]:
            continue
        current = result.get(key)
        if current is None or _timestamp(row.get("available_at")) > _timestamp(
            current.get("available_at")
        ):
            result[key] = row
    return result


def _count_reasons(exclusions: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in exclusions:
        reason = str(row.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _is_st(name: str) -> bool:
    upper = name.upper().replace(" ", "")
    return any(marker in upper for marker in ST_MARKERS)


def _timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
