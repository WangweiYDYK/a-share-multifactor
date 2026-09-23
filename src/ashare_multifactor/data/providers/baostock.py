"""BaoStock provider adapter.

The adapter preserves provider field names. Canonical field mapping belongs to
the normalization layer so the rest of the project is independent of BaoStock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from importlib import metadata
from typing import Any, Sequence

from ashare_multifactor.data.contracts import RawDataset

DAILY_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,"
    "tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST"
)
ADJUST_FLAGS = {"none": "3", "forward": "2", "backward": "1"}


class BaoStockError(RuntimeError):
    """Raised when BaoStock cannot log in or return a requested dataset."""


class BaoStockProvider:
    source = "baostock"

    def __init__(self) -> None:
        self._client: Any = None
        self._logged_in = False

    def __enter__(self) -> "BaoStockProvider":
        try:
            import baostock as bs
        except ImportError as exc:
            missing = getattr(exc, "name", None) or "an unknown dependency"
            raise BaoStockError(
                f"BaoStock or its dependency {missing!r} is not installed correctly. "
                "Run: python -m pip install -e ."
            ) from exc

        result = bs.login()
        if result.error_code != "0":
            raise BaoStockError(f"BaoStock login failed: {result.error_msg}")
        self._client = bs
        self._logged_in = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._logged_in:
            self._client.logout()
        self._logged_in = False

    @property
    def source_version(self) -> str:
        try:
            return metadata.version("baostock")
        except metadata.PackageNotFoundError:
            return "unknown"

    def fetch_trade_calendar(self, start_date: str, end_date: str) -> RawDataset:
        self._require_login()
        result = self._client.query_trade_dates(
            start_date=start_date,
            end_date=end_date,
        )
        return self._dataset(
            "trade_calendar",
            result,
            metadata={"start_date": start_date, "end_date": end_date},
        )

    def fetch_security_master(self, symbols: Sequence[str]) -> RawDataset:
        self._require_login()
        rows = []
        for symbol in symbols:
            result = self._client.query_stock_basic(code=_to_baostock_symbol(symbol))
            rows.extend(_read_result(result, f"security master for {symbol}"))
        return RawDataset(
            name="security_master",
            source=self.source,
            source_version=self.source_version,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            rows=rows,
            metadata={"symbols": list(symbols)},
        )

    def fetch_all_security_master(self) -> RawDataset:
        """Fetch the complete BaoStock security master in one request."""
        self._require_login()
        result = self._client.query_stock_basic()
        return self._dataset(
            "security_master",
            result,
            metadata={"scope": "all_securities"},
        )

    def fetch_daily(
        self,
        symbols: Sequence[str],
        start_date: str,
        end_date: str,
        adjustment: str = "none",
    ) -> RawDataset:
        self._require_login()
        if adjustment not in ADJUST_FLAGS:
            expected = ", ".join(sorted(ADJUST_FLAGS))
            raise ValueError(f"Unknown adjustment {adjustment!r}; expected one of: {expected}")

        rows = []
        for symbol in symbols:
            result = self._client.query_history_k_data_plus(
                _to_baostock_symbol(symbol),
                DAILY_FIELDS,
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag=ADJUST_FLAGS[adjustment],
            )
            rows.extend(_read_result(result, f"daily prices for {symbol}"))

        return RawDataset(
            name="daily_prices",
            source=self.source,
            source_version=self.source_version,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            rows=rows,
            metadata={
                "symbols": list(symbols),
                "start_date": start_date,
                "end_date": end_date,
                "adjustment": adjustment,
            },
        )

    def _dataset(self, name: str, result: Any, metadata: dict[str, Any]) -> RawDataset:
        return RawDataset(
            name=name,
            source=self.source,
            source_version=self.source_version,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            rows=_read_result(result, name),
            metadata=metadata,
        )

    def _require_login(self) -> None:
        if not self._logged_in:
            raise BaoStockError("BaoStockProvider must be used as a context manager.")


def _read_result(result: Any, label: str) -> list[dict[str, str]]:
    if result.error_code != "0":
        raise BaoStockError(f"BaoStock query failed for {label}: {result.error_msg}")

    rows = []
    while result.next():
        rows.append(dict(zip(result.fields, result.get_row_data())))
    return rows


def _to_baostock_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized.startswith(("SH.", "SZ.", "BJ.")):
        return normalized.lower()
    if "." in normalized:
        code, exchange = normalized.split(".", 1)
        exchange_map = {"SH": "sh", "SZ": "sz", "BJ": "bj"}
        if exchange in exchange_map:
            return f"{exchange_map[exchange]}.{code}"
    if len(normalized) == 6 and normalized.isdigit():
        exchange = "sh" if normalized.startswith(("5", "6", "9")) else "sz"
        return f"{exchange}.{normalized}"
    raise ValueError(f"Unsupported stock symbol: {symbol!r}")
