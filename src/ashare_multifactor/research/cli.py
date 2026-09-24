"""Command line entry point for BaoStock price-factor research."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ashare_multifactor.research.price_factors import (
    PriceFactorResearchConfig,
    run_price_factor_research,
)

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BACKFILL_ROOT = PROJECT_ROOT / "data" / "normalized" / "backfills"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "factor-research"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate partitioned BaoStock history and evaluate monthly price factors."
    )
    parser.add_argument("--input-root", type=Path, help="One baostock_YYYYMMDD_YYYYMMDD directory.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--min-listing-observations", type=int, default=120)
    parser.add_argument("--liquidity-window", type=int, default=20)
    parser.add_argument("--min-average-amount", type=float, default=50_000_000.0)
    parser.add_argument("--quantiles", type=int, default=5)
    parser.add_argument("--max-symbols", type=int)
    return parser


def run(args: argparse.Namespace) -> Path:
    if args.quantiles < 2:
        raise ValueError("--quantiles must be at least 2.")
    if args.min_listing_observations < 1 or args.liquidity_window < 1:
        raise ValueError("Listing observations and liquidity window must be positive.")
    if args.min_average_amount < 0:
        raise ValueError("--min-average-amount must be non-negative.")
    input_root = args.input_root or _latest_backfill()
    if not input_root.is_absolute():
        input_root = PROJECT_ROOT / input_root
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / stamp
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir = output_dir.resolve()
    try:
        output_dir.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("Research outputs must stay inside the project repository.") from exc
    config = PriceFactorResearchConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        min_listing_observations=args.min_listing_observations,
        liquidity_window=args.liquidity_window,
        min_average_amount=args.min_average_amount,
        quantiles=args.quantiles,
        max_symbols=args.max_symbols,
    )
    return run_price_factor_research(input_root, output_dir, config)


def _latest_backfill() -> Path:
    candidates = sorted(DEFAULT_BACKFILL_ROOT.glob("baostock_*"))
    if not candidates:
        raise FileNotFoundError(f"No BaoStock backfill found under {DEFAULT_BACKFILL_ROOT}")
    return candidates[-1]


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        manifest = run(build_parser().parse_args(argv))
    except Exception as exc:  # noqa: BLE001 - CLI reports failures without a traceback
        LOGGER.error("Price-factor research failed: %s", exc)
        return 1
    LOGGER.info("Research artifacts saved: %s", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
