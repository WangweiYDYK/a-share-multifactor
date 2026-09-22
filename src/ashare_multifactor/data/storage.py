"""Local storage for canonical research datasets."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ashare_multifactor.data.contracts import CanonicalDataset

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


def write_datasets(output_dir: Path, datasets: Sequence[CanonicalDataset]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_datasets = []

    for dataset in datasets:
        path = output_dir / f"{dataset.name}.csv"
        fieldnames = list(dataset.rows[0].keys())
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(dataset.rows)
        manifest_entry = {
            "name": dataset.name,
            "rows": len(dataset.rows),
            "primary_key": list(dataset.primary_key),
            "path": str(path.resolve()),
            "metadata": dict(dataset.metadata),
        }
        if dataset.name == "daily_prices":
            review_path = output_dir / "daily_prices_review.csv"
            _write_review_csv(review_path, dataset, DAILY_PRICES_LABELS_ZH)
            manifest_entry["review_path"] = str(review_path.resolve())
            manifest_entry["review_note"] = "Human review only; row 2 contains Chinese labels."
        manifest_datasets.append(manifest_entry)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "run_at": datetime.now(timezone.utc).isoformat(),
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
