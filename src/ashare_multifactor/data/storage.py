"""Local storage for canonical research datasets and immutable snapshots."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ashare_multifactor.data.contracts import CanonicalDataset
from ashare_multifactor.data.schemas import dataset_schema

SNAPSHOT_FORMAT_VERSION = 1
DAILY_PRICES_LABELS_ZH = {
    "trade_date": "交易日期",
    "symbol": "股票代码",
    "security_name": "股票名称",
    "open": "开盘价",
    "high": "最高价",
    "low": "最低价",
    "close": "收盘价",
    "pre_close": "前收盘价",
    "volume": "成交量(股)",
    "amount": "成交额(元)",
    "turnover_rate": "换手率(%)",
    "pct_change": "涨跌幅(%)",
    "pe_ttm": "市盈率TTM",
    "pb_mrq": "市净率MRQ",
    "ps_ttm": "市销率TTM",
    "pcf_ncf_ttm": "市现率TTM",
    "trade_status": "交易状态(1正常/0停牌)",
    "is_st": "是否ST(1是/0否)",
    "adjustment": "复权方式",
    "source": "数据来源",
    "source_version": "数据源版本",
    "retrieved_at": "拉取时间",
    "available_at": "可用时间",
}


def write_snapshot(
    snapshot_root: Path,
    datasets: Sequence[CanonicalDataset],
    *,
    snapshot_id: str | None = None,
    as_of: str | None = None,
) -> Path:
    """Write one immutable canonical data snapshot and return its manifest path."""
    if not datasets:
        raise ValueError("At least one dataset is required to write a snapshot.")

    resolved_id = snapshot_id or _default_snapshot_id(datasets)
    _validate_snapshot_id(resolved_id)
    snapshot_dir = snapshot_root / resolved_id
    if snapshot_dir.exists():
        raise FileExistsError(f"Snapshot already exists: {snapshot_dir}")

    return write_datasets(
        snapshot_dir,
        datasets,
        snapshot_id=resolved_id,
        as_of=as_of,
    )


def write_datasets(
    output_dir: Path,
    datasets: Sequence[CanonicalDataset],
    *,
    snapshot_id: str | None = None,
    as_of: str | None = None,
) -> Path:
    """Write canonical CSV files plus a manifest for one snapshot directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_datasets = []

    for dataset in datasets:
        path = output_dir / f"{dataset.name}.csv"
        fieldnames = list(dataset.rows[0].keys())
        schema = dataset_schema(dataset.name, dataset.rows)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(dataset.rows)

        retrieved_at_start, retrieved_at_end = _timestamp_range(dataset, "retrieved_at")
        available_at_start, available_at_end = _timestamp_range(dataset, "available_at")
        manifest_entry = {
            "name": dataset.name,
            "rows": len(dataset.rows),
            "primary_key": list(dataset.primary_key),
            "path": path.relative_to(output_dir).as_posix(),
            "source": _single_dataset_value(dataset, "source"),
            "source_version": _single_dataset_value(dataset, "source_version"),
            "retrieved_at_start": retrieved_at_start,
            "retrieved_at_end": retrieved_at_end,
            "available_at_start": available_at_start,
            "available_at_end": available_at_end,
            "schema": schema,
            "metadata": dict(dataset.metadata),
        }
        if dataset.name == "daily_prices":
            review_path = output_dir / "daily_prices_review.csv"
            _write_review_csv(review_path, dataset, DAILY_PRICES_LABELS_ZH)
            manifest_entry["review_path"] = review_path.relative_to(output_dir).as_posix()
            manifest_entry["review_note"] = "Human review only; row 2 contains Chinese labels."
        manifest_datasets.append(manifest_entry)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format_version": SNAPSHOT_FORMAT_VERSION,
                "snapshot_id": snapshot_id or output_dir.name,
                "run_at": datetime.now(timezone.utc).isoformat(),
                "as_of": as_of,
                "datasets": manifest_datasets,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest_path.resolve()


def _write_review_csv(
    path: Path,
    dataset: CanonicalDataset,
    labels: dict[str, str],
) -> None:
    fieldnames = list(dataset.rows[0].keys())
    missing_labels = [field for field in fieldnames if field not in labels]
    if missing_labels:
        raise ValueError(f"Missing Chinese review labels for fields: {missing_labels}")

    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({field: labels[field] for field in fieldnames})
        writer.writerows(dataset.rows)


def _default_snapshot_id(datasets: Sequence[CanonicalDataset]) -> str:
    sources = sorted({_single_dataset_value(dataset, "source") for dataset in datasets})
    source_prefix = "_".join(sources)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{source_prefix}_{timestamp}"


def _validate_snapshot_id(snapshot_id: str) -> None:
    if not snapshot_id or Path(snapshot_id).name != snapshot_id:
        raise ValueError(f"snapshot_id must be a single directory name: {snapshot_id!r}")


def _single_dataset_value(dataset: CanonicalDataset, column: str) -> str:
    values = sorted(
        {str(row[column]) for row in dataset.rows if row.get(column) not in (None, "")}
    )
    if len(values) != 1:
        raise ValueError(
            f"{dataset.name} must contain exactly one {column} per snapshot; got {values}"
        )
    return values[0]


def _timestamp_range(
    dataset: CanonicalDataset,
    column: str,
) -> tuple[str | None, str | None]:
    values = []
    for row in dataset.rows:
        value = row.get(column)
        if value in (None, ""):
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"{dataset.name} has invalid {column} timestamp: {value!r}"
            ) from exc
        if parsed.tzinfo is None:
            raise ValueError(f"{dataset.name} {column} must include a timezone: {value!r}")
        values.append(parsed)

    if not values:
        return None, None
    return min(values).isoformat(), max(values).isoformat()
