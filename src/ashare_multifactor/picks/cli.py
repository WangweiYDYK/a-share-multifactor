"""Command line entry point for the monthly reference buy list."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ashare_multifactor.backtest.synthetic_market import generate_synthetic_market
from ashare_multifactor.data.contracts import CanonicalDataset
from ashare_multifactor.data.repository import DataRepository, DataRepositoryError
from ashare_multifactor.picks.builder import build_pick_list
from ashare_multifactor.picks.report import format_picks, write_pick_artifacts
from ashare_multifactor.picks.screen import (
    OPTIONAL_DATASETS,
    REQUIRED_DATASETS,
    SHANGHAI_TZ,
    PicksConfig,
    PicksError,
)

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SNAPSHOT_ROOT = PROJECT_ROOT / "data" / "normalized" / "snapshots"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "picks"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a month-end reference buy list from a canonical snapshot."
    )
    parser.add_argument(
        "--snapshot-root",
        type=Path,
        default=DEFAULT_SNAPSHOT_ROOT,
        help="Snapshot root; defaults to data/normalized/snapshots.",
    )
    parser.add_argument("--snapshot-id", help="Pin one snapshot directory name.")
    parser.add_argument("--source", help="Keep rows from one provider only.")
    parser.add_argument(
        "--as-of",
        help="Decision date; defaults to the last open day in the calendar.",
    )
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--min-average-amount", type=float, default=50_000_000.0)
    parser.add_argument("--min-listing-trading-days", type=int, default=120)
    parser.add_argument("--liquidity-window", type=int, default=20)
    parser.add_argument("--max-weight", type=float, default=0.05)
    parser.add_argument("--cash-buffer", type=float, default=0.05)
    parser.add_argument(
        "--industry-system",
        help="Industry classification to use; defaults to the snapshot system.",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Run on the offline synthetic market instead of a snapshot.",
    )
    parser.add_argument("--symbols", type=int, default=60, help="Synthetic fixture size.")
    parser.add_argument("--seed", type=int, default=7, help="Synthetic fixture seed.")
    parser.add_argument("--data-start", default="2023-01-02")
    parser.add_argument("--data-end", default="2024-12-31")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Run directory; defaults to artifacts/picks/<decision date>-<utc stamp>.",
    )
    return parser


def run(args: argparse.Namespace) -> Path:
    """Build the list, print it, and write its artifacts inside the repository."""
    datasets = _load_datasets(args)
    config = PicksConfig(
        as_of=args.as_of,
        top_n=args.top_n,
        min_average_amount=args.min_average_amount,
        min_listing_trading_days=args.min_listing_trading_days,
        liquidity_window=args.liquidity_window,
        max_weight=args.max_weight,
        cash_buffer=args.cash_buffer,
        industry_system=args.industry_system,
    )
    result = build_pick_list(datasets, config)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / "{0}-{1}".format(
        result.decision_date, stamp
    )
    resolved = _resolve_output(output_dir)
    if resolved.exists():
        raise PicksError("Run directory already exists: {0}".format(resolved))

    print(format_picks(result))
    markdown = write_pick_artifacts(result, resolved)
    LOGGER.info(
        "Picks saved: decision=%s candidates=%s picks=%s file=%s",
        result.decision_date,
        result.summary["candidate_count"],
        len(result.picks),
        markdown,
    )
    return markdown


def _load_datasets(args: argparse.Namespace) -> dict[str, CanonicalDataset]:
    if args.synthetic:
        LOGGER.info("Using the synthetic market fixture, not real market data.")
        return generate_synthetic_market(
            start_date=args.data_start,
            end_date=args.data_end,
            n_symbols=args.symbols,
            seed=args.seed,
        )

    as_of = args.as_of or datetime.now(SHANGHAI_TZ).date().isoformat()
    if args.as_of is None:
        LOGGER.info("No --as-of given; loading the snapshot as of %s.", as_of)
    root = Path(args.snapshot_root)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    repository = DataRepository(root, snapshot_id=args.snapshot_id)
    datasets: dict[str, CanonicalDataset] = {}
    for name in REQUIRED_DATASETS:
        try:
            datasets[name] = repository.load(name, as_of=as_of, source=args.source)
        except DataRepositoryError as exc:
            raise PicksError(
                "Snapshot is missing {0!r}, which the pick list requires: {1}".format(
                    name, exc
                )
            ) from exc
    for name in OPTIONAL_DATASETS:
        try:
            datasets[name] = repository.load(name, as_of=as_of, source=args.source)
        except DataRepositoryError:
            LOGGER.info("Snapshot has no %r; that screen will be reported as skipped.", name)
    return datasets


def _resolve_output(output_dir: Path) -> Path:
    resolved = Path(output_dir)
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    resolved = resolved.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise PicksError("Pick artifacts must stay inside the project repository.") from exc
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except Exception as exc:  # noqa: BLE001 - the CLI reports any failure to the user
        LOGGER.error("Pick list failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
