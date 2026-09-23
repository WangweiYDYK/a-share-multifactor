"""Download a small Tushare Pro sample through the canonical data layer."""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence

from ashare_multifactor.data.normalize import CanonicalDataService
from ashare_multifactor.data.providers.tushare import TushareProvider
from ashare_multifactor.data.storage import write_snapshot

LOGGER = logging.getLogger(__name__)
DEFAULT_SNAPSHOT_ROOT = Path("data/normalized/snapshots")


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
        "--snapshot-root",
        "--output-dir",
        dest="snapshot_root",
        type=Path,
        default=DEFAULT_SNAPSHOT_ROOT,
        help="Ignored local root directory for immutable snapshots.",
    )
    return parser


def run(trade_date: str, calendar_start: str | None, snapshot_root: Path) -> Path:
    parsed_trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
    if calendar_start is None:
        calendar_start = (parsed_trade_date - timedelta(days=30)).isoformat()
    else:
        datetime.strptime(calendar_start, "%Y-%m-%d")

    normalizer = CanonicalDataService()
    with TushareProvider() as provider:
        raw_calendar = provider.fetch_trade_calendar(calendar_start, trade_date)
        raw_security_master = provider.fetch_security_master()
        raw_daily = provider.fetch_daily(trade_date)

    security_master = normalizer.normalize(raw_security_master)
    daily_prices = normalizer.enrich_daily_prices(
        normalizer.normalize(raw_daily),
        security_master,
    )
    datasets = [normalizer.normalize(raw_calendar), security_master, daily_prices]
    return write_snapshot(snapshot_root, datasets, as_of=trade_date)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        manifest = run(args.trade_date, args.calendar_start, args.snapshot_root)
    except Exception as exc:
        LOGGER.error("Tushare demo failed: %s", exc)
        return 1

    LOGGER.info("Saved canonical Tushare snapshot; manifest: %s", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
