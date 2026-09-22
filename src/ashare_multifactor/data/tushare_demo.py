"""Command-line demo for downloading a minimal A-share data slice."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

from ashare_multifactor.data.tushare import (
    FetchResult,
    create_client,
    fetch_daily,
    fetch_stock_basic,
    fetch_trade_calendar,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_OUTPUT = Path("data/raw/tushare_demo")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a minimal, research-only A-share dataset from Tushare Pro."
    )
    parser.add_argument(
        "--trade-date",
        default=date.today().isoformat(),
        help="Trading date for daily prices, in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--calendar-start",
        help="Calendar start date; defaults to 30 calendar days before trade-date.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Ignored local output directory.",
    )
    return parser


def run(trade_date: str, calendar_start: str | None, output_dir: Path) -> list[FetchResult]:
    parsed_trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
    if calendar_start is None:
        from datetime import timedelta

        calendar_start = (parsed_trade_date - timedelta(days=30)).isoformat()

    client = create_client()
    datasets = {
        "trade_calendar": fetch_trade_calendar(client, calendar_start, trade_date),
        "stock_basic": fetch_stock_basic(client),
        "daily": fetch_daily(client, trade_date),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name, frame in datasets.items():
        path = output_dir / f"{name}.csv"
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        results.append(FetchResult(name=name, rows=len(frame), path=path.resolve()))

    manifest = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "trade_date": trade_date,
        "calendar_start": calendar_start,
        "datasets": [
            {"name": item.name, "rows": item.rows, "path": str(item.path)}
            for item in results
        ],
        "warning": (
            "Daily available_at uses the project convention of 15:30 Asia/Shanghai; "
            "it is not a Tushare publication timestamp."
        ),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return results


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        results = run(args.trade_date, args.calendar_start, args.output_dir)
    except Exception as exc:
        LOGGER.error("Tushare demo failed: %s", exc)
        return 1

    for result in results:
        LOGGER.info("Saved %s rows for %s to %s", result.rows, result.name, result.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

