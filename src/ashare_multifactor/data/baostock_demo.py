"""Download a small BaoStock sample through the canonical data middle layer."""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

from ashare_multifactor.data.normalize import CanonicalDataService
from ashare_multifactor.data.providers.baostock import BaoStockProvider
from ashare_multifactor.data.storage import write_snapshot

LOGGER = logging.getLogger(__name__)
DEFAULT_SNAPSHOT_ROOT = Path("data/normalized/snapshots")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch free BaoStock data and normalize it to project snapshots."
    )
    parser.add_argument("--start-date", default=(date.today() - timedelta(days=14)).isoformat())
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["600000.SH", "000001.SZ"],
        help="Canonical stock symbols, for example 600000.SH 000001.SZ.",
    )
    parser.add_argument(
        "--adjustment",
        choices=("none", "forward", "backward"),
        default="none",
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


def run(
    start_date: str,
    end_date: str,
    symbols: Sequence[str],
    adjustment: str,
    snapshot_root: Path,
) -> Path:
    normalizer = CanonicalDataService()
    with BaoStockProvider() as provider:
        raw_calendar = provider.fetch_trade_calendar(start_date, end_date)
        raw_security_master = provider.fetch_security_master(symbols)
        raw_daily = provider.fetch_daily(symbols, start_date, end_date, adjustment)

    security_master = normalizer.normalize(raw_security_master)
    daily_prices = normalizer.enrich_daily_prices(
        normalizer.normalize(raw_daily),
        security_master,
    )
    datasets = [normalizer.normalize(raw_calendar), security_master, daily_prices]
    return write_snapshot(snapshot_root, datasets, as_of=end_date)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        manifest = run(
            args.start_date,
            args.end_date,
            args.symbols,
            args.adjustment,
            args.snapshot_root,
        )
    except Exception as exc:
        LOGGER.error("BaoStock demo failed: %s", exc)
        return 1
    LOGGER.info("Saved canonical BaoStock snapshot; manifest: %s", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
