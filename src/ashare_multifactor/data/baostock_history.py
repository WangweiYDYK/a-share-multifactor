"""Resumable, partitioned BaoStock history downloader."""

from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ashare_multifactor.data.normalize import CanonicalDataService
from ashare_multifactor.data.providers.baostock import BaoStockProvider

LOGGER = logging.getLogger(__name__)
DEFAULT_ROOT = Path("data/normalized/backfills")
ADJUSTMENTS = ("none", "backward")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download a resumable batch of normalized BaoStock daily history."
    )
    parser.add_argument("--start-date", default="2021-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--batch-index", type=int, default=0)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    return parser


def run(
    *,
    start_date: str,
    end_date: str,
    batch_size: int,
    batch_index: int,
    output_root: Path,
) -> Path:
    _validate_args(start_date, end_date, batch_size, batch_index)
    run_dir = output_root / f"baostock_{start_date.replace('-', '')}_{end_date.replace('-', '')}"
    partition_root = run_dir / "daily_prices"
    run_dir.mkdir(parents=True, exist_ok=True)

    normalizer = CanonicalDataService()
    with BaoStockProvider() as provider:
        master = normalizer.normalize(provider.fetch_all_security_master())
        stocks = sorted(
            (row for row in master.rows if _is_a_share(row)),
            key=lambda row: str(row["symbol"]),
        )
        start = batch_index * batch_size
        selected = stocks[start : start + batch_size]
        if not selected:
            raise ValueError(
                f"Batch {batch_index} is empty; A-share universe contains {len(stocks)} rows."
            )

        calendar = normalizer.normalize(provider.fetch_trade_calendar(start_date, end_date))
        _write_csv_once(run_dir / "trade_calendar.csv", calendar.rows)
        _write_csv_once(run_dir / "security_master.csv", master.rows)

        state_path = run_dir / f"batch_{batch_index:04d}_state.json"
        state = _load_state(state_path, selected, start_date, end_date, batch_index)
        completed = set(state["completed"])
        failures: dict[str, str] = dict(state["failures"])

        for offset, master_row in enumerate(selected, start=1):
            symbol = str(master_row["symbol"])
            effective_start, effective_end = _effective_range(
                master_row, start_date, end_date
            )
            if effective_start > effective_end:
                for adjustment in ADJUSTMENTS:
                    completed.add(f"{symbol}:{adjustment}")
                _save_state(state_path, state, completed, failures)
                continue

            one_master = replace(master, rows=[master_row])
            for adjustment in ADJUSTMENTS:
                key = f"{symbol}:{adjustment}"
                partition = partition_root / adjustment / f"{symbol}.csv"
                empty_marker = partition.with_suffix(partition.suffix + ".empty")
                if key in completed and (partition.is_file() or empty_marker.is_file()):
                    continue
                try:
                    raw = provider.fetch_daily(
                        [symbol], effective_start, effective_end, adjustment
                    )
                    if raw.rows:
                        daily = normalizer.enrich_daily_prices(
                            normalizer.normalize(raw), one_master
                        )
                        _write_csv(partition, daily.rows)
                    else:
                        _write_empty_marker(partition)
                    completed.add(key)
                    failures.pop(key, None)
                except Exception as exc:
                    failures[key] = str(exc)
                    LOGGER.warning("Failed %s: %s", key, exc)
                _save_state(state_path, state, completed, failures)

            LOGGER.info(
                "Batch %s progress %s/%s: %s",
                batch_index,
                offset,
                len(selected),
                symbol,
            )

    _save_state(state_path, state, completed, failures)
    if failures:
        LOGGER.warning("Batch finished with %s failed partitions.", len(failures))
    else:
        LOGGER.info("Batch finished successfully with %s partitions.", len(completed))
    return state_path.resolve()


def _is_a_share(row: Mapping[str, Any]) -> bool:
    if row.get("security_type") != "stock":
        return False
    symbol = str(row.get("symbol") or "")
    code, _, exchange = symbol.partition(".")
    if exchange == "SH":
        return not code.startswith("900")
    if exchange == "SZ":
        return not code.startswith("200")
    return exchange == "BJ"


def _effective_range(
    row: Mapping[str, Any], start_date: str, end_date: str
) -> tuple[str, str]:
    listed = str(row.get("list_date") or start_date)
    delisted = str(row.get("delist_date") or end_date)
    return max(start_date, listed), min(end_date, delisted)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fieldnames = list(rows[0])
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _write_csv_once(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not path.exists():
        _write_csv(path, rows)


def _write_empty_marker(path: Path) -> None:
    marker = path.with_suffix(path.suffix + ".empty")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch(exist_ok=True)


def _load_state(
    path: Path,
    selected: Sequence[Mapping[str, Any]],
    start_date: str,
    end_date: str,
    batch_index: int,
) -> dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "source": "baostock",
        "start_date": start_date,
        "end_date": end_date,
        "batch_index": batch_index,
        "symbols": [row["symbol"] for row in selected],
        "adjustments": list(ADJUSTMENTS),
        "completed": [],
        "failures": {},
        "updated_at": None,
    }


def _save_state(
    path: Path,
    state: Mapping[str, Any],
    completed: set[str],
    failures: Mapping[str, str],
) -> None:
    payload = {
        **state,
        "completed": sorted(completed),
        "failures": dict(sorted(failures.items())),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _validate_args(
    start_date: str, end_date: str, batch_size: int, batch_index: int
) -> None:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must not be after end_date.")
    if batch_size < 1 or batch_index < 0:
        raise ValueError("batch_size must be positive and batch_index non-negative.")


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        state_path = run(
            start_date=args.start_date,
            end_date=args.end_date,
            batch_size=args.batch_size,
            batch_index=args.batch_index,
            output_root=args.output_root,
        )
    except Exception as exc:
        LOGGER.error("BaoStock history download failed: %s", exc)
        return 1
    LOGGER.info("Saved batch state: %s", state_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
