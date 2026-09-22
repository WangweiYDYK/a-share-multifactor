"""Small Tushare Pro adapter used by the first data ingestion demo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd

if TYPE_CHECKING:
    from pathlib import Path

SOURCE = "tushare_pro"
SOURCE_VERSION = "demo_v1"


class TushareConfigurationError(ValueError):
    """Raised when required Tushare configuration is missing."""


class DataQualityError(ValueError):
    """Raised when a downloaded dataset violates its basic contract."""


@dataclass(frozen=True)
class FetchResult:
    name: str
    rows: int
    path: "Path"


def create_client(token: str | None = None) -> Any:
    """Create an authenticated Tushare Pro client without persisting the token."""
    resolved_token = token or os.environ.get("TUSHARE_TOKEN") or _read_local_token()
    if not resolved_token:
        raise TushareConfigurationError(
            "TUSHARE_TOKEN is not set. Set it in the environment before running the demo."
        )

    try:
        import tushare as ts
    except ImportError as exc:
        raise TushareConfigurationError(
            "The tushare package is not installed. Run: python -m pip install -e ."
        ) from exc

    return ts.pro_api(resolved_token)


def _read_local_token(path: Path = Path(".env")) -> str | None:
    """Read only TUSHARE_TOKEN from a local ignored dotenv file."""
    if not path.is_file():
        return None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "TUSHARE_TOKEN":
            return value.strip().strip("\"'") or None
    return None


def fetch_trade_calendar(client: Any, start_date: str, end_date: str) -> pd.DataFrame:
    frame = client.trade_cal(
        exchange="SSE",
        start_date=_compact_date(start_date),
        end_date=_compact_date(end_date),
        fields="exchange,cal_date,is_open,pretrade_date",
    )
    return _normalize(
        frame,
        name="trade_calendar",
        primary_key=["exchange", "cal_date"],
        date_columns=["cal_date", "pretrade_date"],
    )


def fetch_stock_basic(client: Any) -> pd.DataFrame:
    frames = []
    for status in ("L", "D", "P"):
        frame = client.stock_basic(
            exchange="",
            list_status=status,
            fields=(
                "ts_code,symbol,name,area,industry,market,exchange,list_status,"
                "list_date,delist_date,is_hs"
            ),
        )
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return _normalize(
        combined,
        name="stock_basic",
        primary_key=["ts_code"],
        date_columns=["list_date", "delist_date"],
    )


def fetch_daily(client: Any, trade_date: str) -> pd.DataFrame:
    frame = client.daily(
        trade_date=_compact_date(trade_date),
        fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    )
    return _normalize(
        frame,
        name="daily",
        primary_key=["ts_code", "trade_date"],
        date_columns=["trade_date"],
        available_at_factory=_daily_available_at,
    )


def _normalize(
    frame: pd.DataFrame,
    *,
    name: str,
    primary_key: list[str],
    date_columns: list[str],
    available_at_factory: Callable[[pd.DataFrame, str], pd.Series] | None = None,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise DataQualityError(f"Tushare returned no rows for {name}.")

    missing = sorted(set(primary_key) - set(frame.columns))
    if missing:
        raise DataQualityError(f"{name} is missing primary-key columns: {missing}")

    normalized = frame.copy()
    for column in date_columns:
        if column in normalized.columns:
            normalized[column] = pd.to_datetime(
                normalized[column], format="%Y%m%d", errors="coerce"
            ).dt.strftime("%Y-%m-%d")

    if normalized.duplicated(primary_key).any():
        duplicates = int(normalized.duplicated(primary_key).sum())
        raise DataQualityError(f"{name} contains {duplicates} duplicate primary keys.")

    retrieved_at = datetime.now(timezone.utc).isoformat()
    normalized["source"] = SOURCE
    normalized["source_version"] = SOURCE_VERSION
    normalized["retrieved_at"] = retrieved_at
    normalized["available_at"] = (
        available_at_factory(normalized, retrieved_at)
        if available_at_factory
        else retrieved_at
    )
    return normalized.sort_values(primary_key).reset_index(drop=True)


def _daily_available_at(frame: pd.DataFrame, fallback: str) -> pd.Series:
    # Conservative research convention for end-of-day prices. This is not a vendor
    # publication timestamp and must not be reused for financial statements.
    available = pd.to_datetime(frame["trade_date"], errors="coerce") + pd.Timedelta(
        hours=15, minutes=30
    )
    values = available.dt.tz_localize("Asia/Shanghai").apply(
        lambda value: value.isoformat() if not pd.isna(value) else fallback
    )
    return values


def _compact_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}; expected YYYY-MM-DD.") from exc
