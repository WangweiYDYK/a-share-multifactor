"""Download a small BaoStock sample through the canonical data middle layer."""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

from ashare_multifactor.data.normalize import CanonicalDataService
from ashare_multifactor.data.providers.baostock import BaoStockProvider
from ashare_multifactor.data.storage import write_datasets

LOGGER = logging.getLogger(__name__)
DEFAULT_OUTPUT = Path("data/normalized/baostock_demo")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch free BaoStock data and normalize it to project schemas."
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
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser


def run(
    start_date: str,
    end_date: str,
    symbols: Sequence[str],
    adjustment: str,
    output_dir: Path,
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
    return write_datasets(output_dir, datasets)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        manifest = run(
            args.start_date,
            args.end_date,
            args.symbols,
            args.adjustment,
            args.output_dir,
        )
    except Exception as exc:
        LOGGER.error("BaoStock demo failed: %s", exc)
        return 1
    LOGGER.info("Saved canonical BaoStock sample; manifest: %s", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
