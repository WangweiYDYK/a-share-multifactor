"""Canonical dataset schemas used by snapshot storage and readers."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

TRADE_CALENDAR_SCHEMA = {
    "exchange": "str",
    "trade_date": "str",
    "is_open": "int",
    "previous_trade_date": "str",
    "source": "str",
    "source_version": "str",
    "retrieved_at": "str",
    "available_at": "str",
}

SECURITY_MASTER_SCHEMA = {
    "symbol": "str",
    "security_name": "str",
    "list_date": "str",
    "delist_date": "str",
    "security_type": "str",
    "list_status": "str",
    "industry": "str",
    "area": "str",
    "market": "str",
    "exchange": "str",
    "is_hs": "str",
    "source": "str",
    "source_version": "str",
    "retrieved_at": "str",
    "available_at": "str",
}

DAILY_PRICES_SCHEMA = {
    "trade_date": "str",
    "symbol": "str",
    "security_name": "str",
    "open": "float",
    "high": "float",
    "low": "float",
    "close": "float",
    "pre_close": "float",
    "volume": "float",
    "amount": "float",
    "turnover_rate": "float",
    "pct_change": "float",
    "pe_ttm": "float",
    "pb_mrq": "float",
    "ps_ttm": "float",
    "pcf_ncf_ttm": "float",
    "trade_status": "int",
    "is_st": "int",
    "adjustment": "str",
    "source": "str",
    "source_version": "str",
    "retrieved_at": "str",
    "available_at": "str",
}

# Daily amounts and market caps are yuan; share counts are shares, not lots.
DAILY_BASIC_SCHEMA = {
    "trade_date": "str",
    "symbol": "str",
    "total_share": "float",
    "float_share": "float",
    "total_mv": "float",
    "circ_mv": "float",
    "turnover_rate": "float",
    "pe_ttm": "float",
    "pb_mrq": "float",
    "source": "str",
    "source_version": "str",
    "retrieved_at": "str",
    "available_at": "str",
}

SECURITY_STATUS_SCHEMA = {
    "trade_date": "str",
    "symbol": "str",
    "list_status": "str",
    "trade_status": "int",
    "is_st": "int",
    "is_suspended": "int",
    "is_delisting": "int",
    "source": "str",
    "source_version": "str",
    "retrieved_at": "str",
    "available_at": "str",
}

INDUSTRY_MEMBERSHIP_SCHEMA = {
    "symbol": "str",
    "industry_system": "str",
    "industry_code": "str",
    "industry_name": "str",
    "effective_from": "str",
    "effective_to": "str",
    "source": "str",
    "source_version": "str",
    "retrieved_at": "str",
    "available_at": "str",
}

DATASET_SCHEMAS = {
    "trade_calendar": TRADE_CALENDAR_SCHEMA,
    "security_master": SECURITY_MASTER_SCHEMA,
    "daily_prices": DAILY_PRICES_SCHEMA,
    "daily_basic": DAILY_BASIC_SCHEMA,
    "security_status": SECURITY_STATUS_SCHEMA,
    "industry_membership": INDUSTRY_MEMBERSHIP_SCHEMA,
}


def dataset_schema(
    name: str,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Return the persisted schema for a canonical dataset."""
    schema = DATASET_SCHEMAS.get(name)
    if schema is not None:
        return dict(schema)
    return _infer_schema(rows)


def coerce_row(row: Mapping[str, Any], schema: Mapping[str, str]) -> dict[str, Any]:
    """Restore typed values after reading a canonical CSV snapshot."""
    return {
        column: _coerce_value(value, schema.get(column, "str"))
        for column, value in row.items()
    }


def _infer_schema(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    if not rows:
        return {}

    inferred: dict[str, str] = {}
    for column in rows[0]:
        value = next((row.get(column) for row in rows if row.get(column) is not None), None)
        inferred[column] = _infer_type(value)
    return inferred


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def _coerce_value(value: Any, field_type: str) -> Any:
    if value is None or value == "":
        return None

    if field_type == "str":
        return str(value)
    if field_type == "float":
        return float(value)
    if field_type == "int":
        number = float(value)
        if not number.is_integer():
            raise ValueError(f"Cannot coerce {value!r} to int.")
        return int(number)
    if field_type == "bool":
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
        raise ValueError(f"Cannot coerce {value!r} to bool.")
    raise ValueError(f"Unknown schema type {field_type!r}.")
