"""Deterministic synthetic market fixture for end-to-end pipeline runs.

This is explicitly not market data. It exists so the monthly decision, order,
matching, valuation, and reporting code can be exercised offline while the real
providers for market cap, status, industry, and corporate actions are still
missing. Every row carries ``synthetic`` metadata and a synthetic source label.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from ashare_multifactor.data.contracts import CanonicalDataset

SYNTHETIC_SOURCE = "synthetic_market_demo"
SYNTHETIC_VERSION = "synthetic-market-v1"
SYNTHETIC_BASIS = "synthetic_fixture_not_market_data"
INDUSTRY_SYSTEM = "SYNTHETIC_SW"
INDUSTRIES: tuple[tuple[str, str], ...] = (
    ("BANK", "Synthetic banks"),
    ("TECH", "Synthetic technology"),
    ("CONSUMER", "Synthetic consumer"),
    ("INDUSTRIAL", "Synthetic industrial"),
    ("HEALTH", "Synthetic health care"),
    ("ENERGY", "Synthetic energy"),
)
SHANGHAI_TZ = timezone(timedelta(hours=8))
# Liquid names must clear the 50,000,000 CNY turnover floor in universe.py:
# 10,000,000 shares at the ~10 CNY starting price is about 100,000,000 CNY.
DAILY_VOLUME = 10_000_000
ILLIQUID_VOLUME = 20_000
TOTAL_SHARES = 1_000_000_000.0
FLOAT_SHARES = 800_000_000.0
SPLIT_INDEX = 7
SPLIT_RATIO = 2.0
SPLIT_POSITION = 300
CALENDAR_PUBLICATION_LEAD_DAYS = 30
ST_COUNT = 3
SUSPENDED_COUNT = 2
ILLIQUID_COUNT = 2
LATE_LISTED_COUNT = 3
DELISTED_COUNT = 2


def generate_synthetic_market(
    *,
    start_date: str = "2023-01-02",
    end_date: str = "2024-12-31",
    n_symbols: int = 60,
    seed: int = 7,
) -> dict[str, CanonicalDataset]:
    """Return canonical datasets for one deterministic synthetic market."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    calendar_days = _days(start, end)
    open_days = [day for day in calendar_days if day.weekday() < 5]
    if len(open_days) < SPLIT_POSITION + 20:
        raise ValueError("Synthetic market needs at least 320 trading days.")

    instruments = _instruments(start, open_days, n_symbols)
    split_day = open_days[SPLIT_POSITION]
    split_symbol = instruments[SPLIT_INDEX]["symbol"]
    base_prices, share_factors = _price_paths(
        instruments, open_days, seed, split_day
    )

    price_rows: list[dict[str, Any]] = []
    basic_rows: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []

    for position, day in enumerate(open_days):
        for instrument in instruments:
            symbol = instrument["symbol"]
            base = base_prices[symbol].get(position)
            if base is None:
                continue
            factor = share_factors[symbol][position]
            raw = round(base / factor, 2)
            previous_base = _previous_price(base_prices[symbol], position, base)
            # Exchanges publish an ex-rights adjusted previous close, so the
            # reference price uses the current day's share factor.
            previous_raw = round(previous_base / factor, 2)
            noise = _daily_noise(seed, symbol, position)
            open_price = round(raw * (1.0 + noise["open"]), 2)
            high = round(max(open_price, raw) * (1.0 + noise["high"]), 2)
            low = round(min(open_price, raw) * (1.0 - noise["low"]), 2)
            volume = (
                0
                if instrument["suspended"]
                else ILLIQUID_VOLUME
                if instrument["illiquid"]
                else DAILY_VOLUME
            )
            timestamp = _timestamp(day)

            for adjustment, scale in (("none", 1.0), ("backward", factor)):
                price_rows.append(
                    _row(
                        trade_date=day.isoformat(),
                        symbol=symbol,
                        open=round(open_price * scale, 2),
                        high=round(high * scale, 2),
                        low=round(low * scale, 2),
                        close=round(raw * scale, 2),
                        pre_close=round(previous_raw * scale, 2),
                        volume=float(volume),
                        amount=round(volume * raw, 2),
                        turnover_rate=1.0,
                        pct_change=round((raw / previous_raw - 1.0) * 100.0, 4),
                        pe_ttm=12.0 + (position % 11) * 0.5,
                        pb_mrq=1.2 + (position % 7) * 0.1,
                        ps_ttm=None,
                        pcf_ncf_ttm=None,
                        trade_status=0 if instrument["suspended"] else 1,
                        is_st=instrument["is_st"],
                        adjustment=adjustment,
                        available_at=timestamp,
                    )
                )

            shares = TOTAL_SHARES * factor
            float_shares = FLOAT_SHARES * factor
            basic_rows.append(
                _row(
                    trade_date=day.isoformat(),
                    symbol=symbol,
                    total_share=shares,
                    float_share=float_shares,
                    total_mv=round(raw * shares, 2),
                    circ_mv=round(raw * float_shares, 2),
                    turnover_rate=1.0,
                    pe_ttm=12.0,
                    pb_mrq=1.2,
                    available_at=timestamp,
                )
            )
            status_rows.append(
                _row(
                    trade_date=day.isoformat(),
                    symbol=symbol,
                    list_status="listed",
                    trade_status=0 if instrument["suspended"] else 1,
                    is_st=instrument["is_st"],
                    is_suspended=int(instrument["suspended"]),
                    is_delisting=0,
                    available_at=timestamp,
                )
            )

    for instrument in instruments:
        delist_date = instrument["delist_date"]
        if delist_date is None:
            continue
        status_rows.append(
            _row(
                trade_date=delist_date.isoformat(),
                symbol=instrument["symbol"],
                list_status="delisted",
                trade_status=0,
                is_st=instrument["is_st"],
                is_suspended=1,
                is_delisting=1,
                available_at=_timestamp(delist_date),
            )
        )

    return {
        "trade_calendar": _dataset(
            "trade_calendar",
            ("exchange", "trade_date"),
            _calendar_rows(calendar_days),
        ),
        "security_master": _dataset(
            "security_master",
            ("symbol",),
            _master_rows(instruments),
        ),
        "daily_prices": _dataset(
            "daily_prices",
            ("trade_date", "symbol", "adjustment"),
            price_rows,
        ),
        "daily_basic": _dataset(
            "daily_basic",
            ("trade_date", "symbol"),
            basic_rows,
        ),
        "security_status": _dataset(
            "security_status",
            ("trade_date", "symbol"),
            status_rows,
        ),
        "industry_membership": _dataset(
            "industry_membership",
            ("symbol", "industry_system", "effective_from"),
            _industry_rows(instruments, start),
        ),
        "corporate_actions": _dataset(
            "corporate_actions",
            ("symbol", "ex_date", "action_type"),
            [
                _row(
                    symbol=split_symbol,
                    ex_date=split_day.isoformat(),
                    action_type="split",
                    ratio=SPLIT_RATIO,
                    dividend_per_share=None,
                    available_at=_timestamp(split_day - timedelta(days=10)),
                )
            ],
        ),
    }


def synthetic_data_version(*, n_symbols: int, seed: int) -> str:
    return f"{SYNTHETIC_VERSION}:symbols={n_symbols}:seed={seed}"


def _instruments(
    start: date,
    open_days: Sequence[date],
    n_symbols: int,
) -> list[dict[str, Any]]:
    instruments = []
    for index in range(n_symbols):
        instruments.append(
            {
                "symbol": _symbol(index),
                "list_date": start,
                "delist_date": None,
                "industry": INDUSTRIES[index % len(INDUSTRIES)][0],
                "is_st": 0,
                "suspended": False,
                "illiquid": False,
                "profile": "rise"
                if index == 0
                else "fall"
                if index == 1
                else "random",
            }
        )

    next_index = n_symbols
    for _ in range(ST_COUNT):
        instruments.append(_extra(next_index, start, is_st=1))
        next_index += 1
    for _ in range(SUSPENDED_COUNT):
        instruments.append(_extra(next_index, start, suspended=True))
        next_index += 1
    for _ in range(ILLIQUID_COUNT):
        instruments.append(_extra(next_index, start, illiquid=True))
        next_index += 1
    for _ in range(LATE_LISTED_COUNT):
        instruments.append(
            _extra(next_index, start, list_date=start + timedelta(days=150))
        )
        next_index += 1
    for offset in range(DELISTED_COUNT):
        instruments.append(
            _extra(
                next_index,
                start,
                delist_date=open_days[SPLIT_POSITION + offset],
            )
        )
        next_index += 1
    return instruments


def _extra(
    index: int,
    start: date,
    *,
    is_st: int = 0,
    suspended: bool = False,
    illiquid: bool = False,
    list_date: date | None = None,
    delist_date: date | None = None,
) -> dict[str, Any]:
    return {
        "symbol": _symbol(index),
        "list_date": list_date or start,
        "delist_date": delist_date,
        "industry": INDUSTRIES[index % len(INDUSTRIES)][0],
        "is_st": is_st,
        "suspended": suspended,
        "illiquid": illiquid,
        "profile": "random",
    }


def _symbol(index: int) -> str:
    if index % 2 == 0:
        return f"{600000 + index:06d}.SH"
    return f"{index + 1:06d}.SZ"


def _price_paths(
    instruments: Sequence[Mapping[str, Any]],
    open_days: Sequence[date],
    seed: int,
    split_day: date,
) -> tuple[dict[str, dict[int, float]], dict[str, list[float]]]:
    prices: dict[str, dict[int, float]] = {}
    factors: dict[str, list[float]] = {}
    split_symbol = instruments[SPLIT_INDEX]["symbol"]

    for index, instrument in enumerate(instruments):
        symbol = str(instrument["symbol"])
        rng = random.Random(seed * 1000 + index)
        profile = instrument["profile"]
        volatility = (
            0.004 if profile == "rise" else 0.014 if profile == "fall" else 0.012
        )
        phase = index * 6.0
        price = 10.0
        series: dict[int, float] = {}
        symbol_factors: list[float] = []
        listed = instrument["list_date"]
        delisted = instrument["delist_date"]

        for position, day in enumerate(open_days):
            factor = (
                SPLIT_RATIO if symbol == split_symbol and day >= split_day else 1.0
            )
            symbol_factors.append(factor)
            if day < listed or (delisted is not None and day >= delisted):
                continue
            if profile == "rise":
                drift = 0.0024
            elif profile == "fall":
                drift = -0.0016
            else:
                drift = 0.0002
            drift += 0.0018 * math.sin((position + phase) / 42.0)
            shock = rng.gauss(0.0, volatility)
            price = max(1.0, price * (1.0 + drift + shock))
            series[position] = round(price, 2)

        prices[symbol] = series
        factors[symbol] = symbol_factors
    return prices, factors


def _previous_price(
    series: Mapping[int, float],
    position: int,
    fallback: float,
) -> float:
    for candidate in range(position - 1, -1, -1):
        if candidate in series:
            return series[candidate]
    return fallback


def _daily_noise(seed: int, symbol: str, position: int) -> dict[str, float]:
    rng = random.Random(f"{seed}:{symbol}:{position}")
    return {
        "open": rng.uniform(-0.006, 0.006),
        "high": rng.uniform(0.001, 0.006),
        "low": rng.uniform(0.001, 0.006),
    }


def _calendar_rows(days: Sequence[date]) -> list[dict[str, Any]]:
    # Exchange calendars are published in advance, so every row is visible well
    # before the first decision of the run.
    published_at = _timestamp(days[0] - timedelta(days=CALENDAR_PUBLICATION_LEAD_DAYS))
    rows = []
    previous_open: date | None = None
    for day in days:
        is_open = day.weekday() < 5
        rows.append(
            _row(
                exchange="SSE",
                trade_date=day.isoformat(),
                is_open=int(is_open),
                previous_trade_date=(
                    previous_open.isoformat() if previous_open else None
                ),
                available_at=published_at,
            )
        )
        if is_open:
            previous_open = day
    return rows


def _master_rows(instruments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for instrument in instruments:
        symbol = str(instrument["symbol"])
        list_date = instrument["list_date"]
        name = f"SYN {symbol.split('.')[0]}"
        rows.append(
            _row(
                symbol=symbol,
                security_name=name,
                list_date=list_date.isoformat(),
                delist_date=None,
                security_type="stock",
                list_status="listed",
                industry=None,
                area=None,
                market="synthetic",
                exchange=symbol.split(".")[1],
                is_hs="N",
                available_at=_timestamp(list_date - timedelta(days=5)),
            )
        )
        delist_date = instrument["delist_date"]
        if delist_date is not None:
            rows.append(
                _row(
                    symbol=symbol,
                    security_name=name,
                    list_date=list_date.isoformat(),
                    delist_date=delist_date.isoformat(),
                    security_type="stock",
                    list_status="delisted",
                    industry=None,
                    area=None,
                    market="synthetic",
                    exchange=symbol.split(".")[1],
                    is_hs="N",
                    available_at=_timestamp(delist_date),
                )
            )
    return rows


def _industry_rows(
    instruments: Sequence[Mapping[str, Any]],
    start: date,
) -> list[dict[str, Any]]:
    names = dict(INDUSTRIES)
    return [
        _row(
            symbol=instrument["symbol"],
            industry_system=INDUSTRY_SYSTEM,
            industry_code=instrument["industry"],
            industry_name=names[instrument["industry"]],
            effective_from=start.isoformat(),
            effective_to=None,
            available_at=_timestamp(start),
        )
        for instrument in instruments
    ]


def _row(**values: Any) -> dict[str, Any]:
    return {
        "source": SYNTHETIC_SOURCE,
        "source_version": SYNTHETIC_VERSION,
        "retrieved_at": "2024-12-31T20:00:00+08:00",
        **values,
    }


def _timestamp(day: date) -> str:
    return datetime(
        day.year, day.month, day.day, 18, 0, tzinfo=SHANGHAI_TZ
    ).isoformat()


def _days(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _dataset(
    name: str,
    primary_key: tuple[str, ...],
    rows: Sequence[Mapping[str, Any]],
) -> CanonicalDataset:
    return CanonicalDataset(
        name=name,
        primary_key=primary_key,
        rows=list(rows),
        metadata={
            "synthetic": True,
            "availability_basis": SYNTHETIC_BASIS,
            "generator": SYNTHETIC_VERSION,
        },
    )
