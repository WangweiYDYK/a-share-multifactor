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
            "security_master": self._security_master,
            "daily_prices": self._daily_prices,
        }
        try:
            dataset = handlers[raw.name](raw)
        except KeyError as exc:
            raise ValueError(f"No canonical mapping for dataset {raw.name!r}") from exc
        _validate(dataset)
        return dataset

    def enrich_daily_prices(
        self,
        daily_prices: CanonicalDataset,
        security_master: CanonicalDataset,
    ) -> CanonicalDataset:
        """Add provider-sourced names without leaking provider fields downstream."""
        if daily_prices.name != "daily_prices" or security_master.name != "security_master":
            raise ValueError("Expected daily_prices and security_master datasets.")

        names = {str(row["symbol"]): str(row["security_name"]) for row in security_master.rows}
        missing = sorted({str(row["symbol"]) for row in daily_prices.rows} - names.keys())
        if missing:
            raise DataQualityError(f"Security names are missing for symbols: {missing}")

        rows = []
        for row in daily_prices.rows:
            enriched = {}
            for column, value in row.items():
                enriched[column] = value
                if column == "symbol":
                    enriched["security_name"] = names[str(value)]
            rows.append(enriched)

        dataset = CanonicalDataset(
            name=daily_prices.name,
            primary_key=daily_prices.primary_key,
            rows=rows,
            metadata={
                **daily_prices.metadata,
                "security_master_source": security_master.rows[0]["source"],
                "security_master_source_version": security_master.rows[0]["source_version"],
            },
        )
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

    def _security_master(self, raw: RawDataset) -> CanonicalDataset:
        rows = [
            {
                "symbol": _canonical_symbol(str(row["code"])),
                "security_name": str(row["code_name"]),
                "list_date": _empty_to_none(row.get("ipoDate")),
                "delist_date": _empty_to_none(row.get("outDate")),
                "security_type": _to_int(row.get("type")),
                "list_status": _to_int(row.get("status")),
                "source": raw.source,
                "source_version": raw.source_version,
                "retrieved_at": raw.retrieved_at,
                "available_at": raw.retrieved_at,
            }
            for row in raw.rows
        ]
        return CanonicalDataset(
            name="security_master",
            primary_key=("symbol",),
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
    if dataset.name == "security_master":
        missing_names = [row["symbol"] for row in dataset.rows if not row.get("security_name")]
        if missing_names:
            raise DataQualityError(f"Security names are missing for symbols: {missing_names}")


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


def _empty_to_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
