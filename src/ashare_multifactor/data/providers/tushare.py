"""Tushare Pro provider adapter.

The adapter only requests provider data and returns it in a RawDataset. Field
mapping, time alignment, and quality checks belong to the normalization layer.
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping, Sequence

from ashare_multifactor.data.contracts import RawDataset

SOURCE = "tushare_pro"
STOCK_STATUSES = ("L", "D", "P")
STOCK_BASIC_REQUEST_INTERVAL_SECONDS = 61.0


class TushareError(RuntimeError):
    """Raised when Tushare is unavailable or a request cannot be completed."""


class TushareProvider:
    source = SOURCE

    def __init__(self, token: str | None = None) -> None:
        self._token = token
        self._client: Any = None

    def __enter__(self) -> "TushareProvider":
        self._client = _create_client(self._token)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._client = None

    @property
    def source_version(self) -> str:
        try:
            return metadata.version("tushare")
        except metadata.PackageNotFoundError:
            return "unknown"

    def fetch_trade_calendar(self, start_date: str, end_date: str) -> RawDataset:
        self._require_client()
        frame = self._client.trade_cal(
            exchange="SSE",
            start_date=_compact_date(start_date),
            end_date=_compact_date(end_date),
            fields="exchange,cal_date,is_open,pretrade_date",
        )
        return self._dataset(
            "trade_calendar",
            frame,
            metadata={
                "exchange": "SSE",
                "start_date": start_date,
                "end_date": end_date,
            },
        )

    def fetch_security_master(self) -> RawDataset:
        self._require_client()
        import pandas as pd

        frames = []
        for index, status in enumerate(STOCK_STATUSES):
            if index:
                # Low-tier Tushare accounts may allow only one stock_basic
                # request per minute. Pace the three historical status calls
                # instead of dropping delisted or paused securities.
                time.sleep(STOCK_BASIC_REQUEST_INTERVAL_SECONDS)
            frame = self._client.stock_basic(
                exchange="",
                list_status=status,
                fields=(
                    "ts_code,symbol,name,area,industry,market,exchange,list_status,"
                    "list_date,delist_date,is_hs"
                ),
            )
            frames.append(frame)

        combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return self._dataset(
            "security_master",
            combined,
            metadata={"list_statuses": list(STOCK_STATUSES)},
        )

    def fetch_daily(self, trade_date: str) -> RawDataset:
        self._require_client()
        frame = self._client.daily(
            trade_date=_compact_date(trade_date),
            fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
        )
        return self._dataset(
            "daily_prices",
            frame,
            metadata={"trade_date": trade_date, "adjustment": "none"},
        )

    def fetch_daily_range(
        self,
        symbols: Sequence[str],
        start_date: str,
        end_date: str,
    ) -> RawDataset:
        """Fetch an unadjusted daily range for a bounded symbol batch."""
        self._require_client()
        if not symbols:
            raise ValueError("At least one symbol is required.")
        frame = self._client.daily(
            ts_code=",".join(symbols),
            start_date=_compact_date(start_date),
            end_date=_compact_date(end_date),
            fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
        )
        return self._dataset(
            "daily_prices",
            frame,
            metadata={
                "symbols": list(symbols),
                "start_date": start_date,
                "end_date": end_date,
                "adjustment": "none",
            },
        )

    def _dataset(self, name: str, frame: Any, metadata: dict[str, Any]) -> RawDataset:
        return RawDataset(
            name=name,
            source=self.source,
            source_version=self.source_version,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            rows=_records(frame, name),
            metadata=metadata,
        )

    def _require_client(self) -> None:
        if self._client is None:
            raise TushareError("TushareProvider must be used as a context manager.")


def _create_client(token: str | None) -> Any:
    resolved_token = token or os.environ.get("TUSHARE_TOKEN") or _read_local_token()
    if not resolved_token:
        raise TushareError(
            "TUSHARE_TOKEN is not set. Set it in the environment before running the demo."
        )

    try:
        import tushare as ts
    except ImportError as exc:
        raise TushareError(
            "The tushare package is not installed. Run: python -m pip install -e ."
        ) from exc

    return ts.pro_api(resolved_token)


def _read_local_token(path: Path = Path(".env")) -> str | None:
    """Read only TUSHARE_TOKEN from a local, ignored dotenv file."""
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


def _records(frame: Any, label: str) -> list[Mapping[str, Any]]:
    if frame is None or frame.empty:
        raise TushareError(f"Tushare returned no rows for {label}.")

    rows = []
    for record in frame.to_dict(orient="records"):
        rows.append({key: _clean_value(value) for key, value in record.items()})
    return rows


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _compact_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}; expected YYYY-MM-DD.") from exc
