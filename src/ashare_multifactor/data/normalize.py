"""Provider-neutral normalization and basic data quality checks."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ashare_multifactor.data.contracts import CanonicalDataset, RawDataset

SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
ADJUSTMENT_NAMES = {"1": "backward", "2": "forward", "3": "none"}
DAILY_AVAILABILITY_BASIS = "project_convention_eod_18_00_asia_shanghai"


class DataQualityError(ValueError):
    """Raised when normalized data violates a minimum quality contract."""


class CanonicalDataService:
    """Translate provider rows into stable schemas used by factors and backtests."""

    def normalize(self, raw: RawDataset) -> CanonicalDataset:
        handlers: dict[str, Callable[[RawDataset], CanonicalDataset]] = {
            "trade_calendar": self._trade_calendar,
            "daily_prices": self._daily_prices,
        }
        try:
            dataset = handlers[raw.name](raw)
        except KeyError as exc:
            raise ValueError(f"No canonical mapping for dataset {raw.name!r}") from exc
        _validate(dataset)
        return dataset

    def _trade_calendar(self, raw: RawDataset) -> CanonicalDataset:
        rows = [
            {
                "trade_date": str(row["calendar_date"]),
                "is_open": _to_int(row.get("is_trading_day")),
                "source": raw.source,
                "source_version": raw.source_version,
                "retrieved_at": raw.retrieved_at,
                "available_at": raw.retrieved_at,
            }
            for row in raw.rows
        ]
        return CanonicalDataset(
            name="trade_calendar",
            primary_key=("trade_date",),
            rows=rows,
            metadata=raw.metadata,
        )

    def _daily_prices(self, raw: RawDataset) -> CanonicalDataset:
        rows = []
        for row in raw.rows:
            trade_date = str(row["date"])
            rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": _canonical_symbol(str(row["code"])),
                    "open": _to_float(row.get("open")),
                    "high": _to_float(row.get("high")),
                    "low": _to_float(row.get("low")),
                    "close": _to_float(row.get("close")),
                    "pre_close": _to_float(row.get("preclose")),
                    "volume": _to_float(row.get("volume")),
                    "amount": _to_float(row.get("amount")),
                    "turnover_rate": _to_float(row.get("turn")),
                    "pct_change": _to_float(row.get("pctChg")),
                    "pe_ttm": _to_float(row.get("peTTM")),
                    "pb_mrq": _to_float(row.get("pbMRQ")),
                    "ps_ttm": _to_float(row.get("psTTM")),
                    "pcf_ncf_ttm": _to_float(row.get("pcfNcfTTM")),
                    "trade_status": _to_int(row.get("tradestatus")),
                    "is_st": _to_int(row.get("isST")),
                    "adjustment": ADJUSTMENT_NAMES.get(str(row.get("adjustflag")), "unknown"),
                    "source": raw.source,
                    "source_version": raw.source_version,
                    "retrieved_at": raw.retrieved_at,
                    "available_at": _daily_available_at(trade_date),
                }
            )
        return CanonicalDataset(
            name="daily_prices",
            primary_key=("trade_date", "symbol", "adjustment"),
            rows=rows,
            metadata={**raw.metadata, "availability_basis": DAILY_AVAILABILITY_BASIS},
        )


def _validate(dataset: CanonicalDataset) -> None:
    if not dataset.rows:
        raise DataQualityError(f"{dataset.name} contains no rows.")

    keys = []
    for index, row in enumerate(dataset.rows):
        missing = [column for column in dataset.primary_key if row.get(column) in (None, "")]
        if missing:
            raise DataQualityError(
                f"{dataset.name} row {index} is missing primary-key fields: {missing}"
            )
        keys.append(tuple(row[column] for column in dataset.primary_key))
    if len(keys) != len(set(keys)):
        raise DataQualityError(f"{dataset.name} contains duplicate primary keys.")

    if dataset.name == "daily_prices":
        for row in dataset.rows:
            for column in ("open", "high", "low", "close", "volume", "amount"):
                value = row[column]
                if value is not None and (not math.isfinite(value) or value < 0):
                    raise DataQualityError(
                        f"daily_prices has invalid {column}={value!r} for {row['symbol']}"
                    )


def _canonical_symbol(value: str) -> str:
    exchange, code = value.upper().split(".", 1)
    return f"{code}.{exchange}"


def _daily_available_at(trade_date: str) -> str:
    timestamp = datetime.strptime(trade_date, "%Y-%m-%d").replace(
        hour=18,
        minute=0,
        tzinfo=SHANGHAI_TZ,
    )
    return timestamp.isoformat()


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)
