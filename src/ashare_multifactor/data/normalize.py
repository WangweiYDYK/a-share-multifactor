"""Provider-neutral normalization and basic data quality checks."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from ashare_multifactor.data.contracts import CanonicalDataset, RawDataset

SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
BAOSTOCK_SOURCE = "baostock"
TUSHARE_SOURCE = "tushare_pro"
SUPPORTED_SOURCES = {BAOSTOCK_SOURCE, TUSHARE_SOURCE}
ADJUSTMENT_NAMES = {"1": "backward", "2": "forward", "3": "none"}
DAILY_AVAILABILITY_BASIS = "project_convention_eod_18_00_asia_shanghai"

BAOSTOCK_SECURITY_TYPES = {
    1: "stock",
    2: "index",
    3: "other",
    4: "convertible_bond",
    5: "etf",
}
BAOSTOCK_LIST_STATUSES = {0: "delisted", 1: "listed"}
TUSHARE_LIST_STATUSES = {"D": "delisted", "L": "listed", "P": "paused"}
EXCHANGES = {"SH", "SZ", "BJ"}


class DataQualityError(ValueError):
    """Raised when normalized data violates a minimum quality contract."""


class CanonicalDataService:
    """Translate provider rows into stable schemas used by factors and backtests."""

    def normalize(self, raw: RawDataset) -> CanonicalDataset:
        if raw.source not in SUPPORTED_SOURCES:
            raise ValueError(f"Unsupported data source {raw.source!r}")
        handlers: dict[str, Callable[[RawDataset], CanonicalDataset]] = {
            "trade_calendar": self._trade_calendar,
            "security_master": self._security_master,
            "daily_prices": self._daily_prices,
        }
        try:
            handler = handlers[raw.name]
        except KeyError as exc:
            raise ValueError(f"No canonical mapping for dataset {raw.name!r}") from exc
        dataset = handler(raw)
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
        rows = [_trade_calendar_row(raw, row) for row in raw.rows]
        return CanonicalDataset(
            name="trade_calendar",
            primary_key=("exchange", "trade_date"),
            rows=rows,
            metadata=raw.metadata,
        )

    def _security_master(self, raw: RawDataset) -> CanonicalDataset:
        rows = [_security_master_row(raw, row) for row in raw.rows]
        return CanonicalDataset(
            name="security_master",
            primary_key=("symbol",),
            rows=rows,
            metadata=raw.metadata,
        )

    def _daily_prices(self, raw: RawDataset) -> CanonicalDataset:
        rows = [_daily_prices_row(raw, row) for row in raw.rows]
        return CanonicalDataset(
            name="daily_prices",
            primary_key=("trade_date", "symbol", "adjustment"),
            rows=rows,
            metadata={**raw.metadata, "availability_basis": DAILY_AVAILABILITY_BASIS},
        )


def _trade_calendar_row(raw: RawDataset, row: Mapping[str, Any]) -> dict[str, Any]:
    if raw.source == BAOSTOCK_SOURCE:
        exchange = "CN"
        trade_date = _required_iso_date(row.get("calendar_date"), "trade_calendar.calendar_date")
        is_open = _to_int(row.get("is_trading_day"))
        previous_trade_date = None
    else:
        exchange = str(row.get("exchange") or "SSE")
        trade_date = _required_iso_date(row.get("cal_date"), "trade_calendar.cal_date")
        is_open = _to_int(row.get("is_open"))
        previous_trade_date = _to_iso_date(row.get("pretrade_date"))

    return {
        "exchange": exchange,
        "trade_date": trade_date,
        "is_open": is_open,
        "previous_trade_date": previous_trade_date,
        "source": raw.source,
        "source_version": raw.source_version,
        "retrieved_at": raw.retrieved_at,
        "available_at": raw.retrieved_at,
    }


def _security_master_row(raw: RawDataset, row: Mapping[str, Any]) -> dict[str, Any]:
    if raw.source == BAOSTOCK_SOURCE:
        symbol = _canonical_symbol(str(row["code"]))
        security_name = str(row["code_name"])
        list_date = _to_iso_date(row.get("ipoDate"))
        delist_date = _to_iso_date(row.get("outDate"))
        security_type = BAOSTOCK_SECURITY_TYPES.get(_to_int(row.get("type")), "unknown")
        list_status = BAOSTOCK_LIST_STATUSES.get(_to_int(row.get("status")), "unknown")
        industry = None
        area = None
        market = None
        is_hs = None
    else:
        symbol = _canonical_symbol(str(row["ts_code"]))
        security_name = str(row["name"])
        list_date = _to_iso_date(row.get("list_date"))
        delist_date = _to_iso_date(row.get("delist_date"))
        security_type = "stock"
        status = str(row.get("list_status", "")).upper()
        list_status = TUSHARE_LIST_STATUSES.get(status, "unknown")
        industry = _empty_to_none(row.get("industry"))
        area = _empty_to_none(row.get("area"))
        market = _empty_to_none(row.get("market"))
        is_hs = _empty_to_none(row.get("is_hs"))

    return {
        "symbol": symbol,
        "security_name": security_name,
        "list_date": list_date,
        "delist_date": delist_date,
        "security_type": security_type,
        "list_status": list_status,
        "industry": industry,
        "area": area,
        "market": market,
        "exchange": _exchange_from_symbol(symbol),
        "is_hs": is_hs,
        "source": raw.source,
        "source_version": raw.source_version,
        "retrieved_at": raw.retrieved_at,
        "available_at": raw.retrieved_at,
    }


def _daily_prices_row(raw: RawDataset, row: Mapping[str, Any]) -> dict[str, Any]:
    if raw.source == BAOSTOCK_SOURCE:
        trade_date = _required_iso_date(row.get("date"), "daily_prices.date")
        symbol = _canonical_symbol(str(row["code"]))
        volume = _to_float(row.get("volume"))
        amount = _to_float(row.get("amount"))
        turnover_rate = _to_float(row.get("turn"))
        pct_change = _to_float(row.get("pctChg"))
        pe_ttm = _to_float(row.get("peTTM"))
        pb_mrq = _to_float(row.get("pbMRQ"))
        ps_ttm = _to_float(row.get("psTTM"))
        pcf_ncf_ttm = _to_float(row.get("pcfNcfTTM"))
        trade_status = _to_int(row.get("tradestatus"))
        is_st = _to_int(row.get("isST"))
        adjustment = ADJUSTMENT_NAMES.get(str(row.get("adjustflag")), "unknown")
        pre_close = _to_float(row.get("preclose"))
    else:
        trade_date = _required_iso_date(row.get("trade_date"), "daily_prices.trade_date")
        symbol = _canonical_symbol(str(row["ts_code"]))
        # Tushare returns volume in lots and amount in thousand yuan.
        volume = _scale(_to_float(row.get("vol")), 100)
        amount = _scale(_to_float(row.get("amount")), 1000)
        turnover_rate = None
        pct_change = _to_float(row.get("pct_chg"))
        pe_ttm = None
        pb_mrq = None
        ps_ttm = None
        pcf_ncf_ttm = None
        trade_status = None
        is_st = None
        adjustment = "none"
        pre_close = _to_float(row.get("pre_close"))

    return {
        "trade_date": trade_date,
        "symbol": symbol,
        "open": _to_float(row.get("open")),
        "high": _to_float(row.get("high")),
        "low": _to_float(row.get("low")),
        "close": _to_float(row.get("close")),
        "pre_close": pre_close,
        "volume": volume,
        "amount": amount,
        "turnover_rate": turnover_rate,
        "pct_change": pct_change,
        "pe_ttm": pe_ttm,
        "pb_mrq": pb_mrq,
        "ps_ttm": ps_ttm,
        "pcf_ncf_ttm": pcf_ncf_ttm,
        "trade_status": trade_status,
        "is_st": is_st,
        "adjustment": adjustment,
        "source": raw.source,
        "source_version": raw.source_version,
        "retrieved_at": raw.retrieved_at,
        "available_at": _daily_available_at(trade_date),
    }


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
    parts = value.strip().upper().split(".")
    if len(parts) != 2:
        raise ValueError(f"Invalid stock symbol: {value!r}")

    left, right = parts
    if left in EXCHANGES:
        exchange, code = left, right
    elif right in EXCHANGES:
        exchange, code = right, left
    else:
        raise ValueError(f"Unsupported stock symbol: {value!r}")

    if not code:
        raise ValueError(f"Invalid stock symbol: {value!r}")
    return f"{code}.{exchange}"


def _exchange_from_symbol(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1]


def _daily_available_at(trade_date: str) -> str:
    timestamp = datetime.strptime(trade_date, "%Y-%m-%d").replace(
        hour=18,
        minute=0,
        tzinfo=SHANGHAI_TZ,
    )
    return timestamp.isoformat()


def _to_iso_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for date_format in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, date_format).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Invalid date {value!r}; expected YYYY-MM-DD or YYYYMMDD.")


def _required_iso_date(value: Any, label: str) -> str:
    result = _to_iso_date(value)
    if result is None:
        raise DataQualityError(f"{label} is required.")
    return result


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _scale(value: float | None, multiplier: float) -> float | None:
    return None if value is None else value * multiplier


def _empty_to_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
