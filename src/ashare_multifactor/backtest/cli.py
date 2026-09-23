"""Command line entry point for one monthly multifactor backtest run."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ashare_multifactor.backtest.config import BacktestConfig
from ashare_multifactor.backtest.engine import (
    BacktestError,
    run_backtest,
    write_run_artifacts,
)
from ashare_multifactor.backtest.synthetic_market import (
    INDUSTRY_SYSTEM,
    generate_synthetic_market,
    synthetic_data_version,
)
from ashare_multifactor.data.contracts import CanonicalDataset
from ashare_multifactor.data.repository import DataRepository, DataRepositoryError
from ashare_multifactor.data.universe import REQUIRED_DATASETS

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "backtest-run"
DEFAULT_DATA_START = "2023-01-02"
DEFAULT_BACKTEST_START = "2023-07-03"
DEFAULT_END = "2024-12-31"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the monthly multifactor backtest over synthetic or snapshot data."
    )
    parser.add_argument("--start-date", default=DEFAULT_BACKTEST_START)
    parser.add_argument("--end-date", default=DEFAULT_END)
    parser.add_argument(
        "--data-start",
        default=DEFAULT_DATA_START,
        help="First calendar day of the synthetic fixture (ignored for snapshots).",
    )
    parser.add_argument("--symbols", type=int, default=60)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--initial-cash", type=float, default=10_000_000.0)
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--industry-system", default=INDUSTRY_SYSTEM)
    parser.add_argument(
        "--snapshot-root",
        type=Path,
        help="Read a canonical snapshot instead of the synthetic fixture.",
    )
    parser.add_argument("--snapshot-id", help="Pin one snapshot directory name.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Run directory; defaults to artifacts/backtest-run/<utc timestamp>.",
    )
    return parser


def run(args: argparse.Namespace) -> Path:
    """Execute one run and write its artifacts inside the repository."""
    if args.snapshot_root:
        datasets, data_version = _load_snapshot(args)
    else:
        datasets = generate_synthetic_market(
            start_date=args.data_start,
            end_date=args.end_date,
            n_symbols=args.symbols,
            seed=args.seed,
        )
        data_version = synthetic_data_version(n_symbols=args.symbols, seed=args.seed)

    config = BacktestConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        industry_system=args.industry_system,
        data_version=data_version,
        initial_cash=args.initial_cash,
        top_n=args.top_n,
        random_seed=args.seed,
    )
    result = run_backtest(datasets, config)

    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%S%fZ")
    resolved = Path(output_dir)
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    resolved = resolved.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise BacktestError("Run output must stay inside the project repository.") from exc
    if resolved.exists():
        raise BacktestError(f"Run directory already exists: {resolved}")

    manifest = write_run_artifacts(result, resolved)
    LOGGER.info(
        "Run complete: decisions=%s daily=%s trades=%s rank_ic=%s",
        result.manifest["decision_count"],
        result.manifest["daily_observations"],
        result.metrics.get("trade_count"),
        result.metrics.get("rank_ic_mean"),
    )
    return manifest


def _load_snapshot(args: argparse.Namespace) -> tuple[dict[str, CanonicalDataset], str]:
    repository = DataRepository(Path(args.snapshot_root), snapshot_id=args.snapshot_id)
    datasets: dict[str, CanonicalDataset] = {}
    for name in REQUIRED_DATASETS:
        datasets[name] = repository.load(name, as_of=args.end_date)
    try:
        datasets["corporate_actions"] = repository.load(
            "corporate_actions", as_of=args.end_date
        )
    except DataRepositoryError:
        LOGGER.warning("Snapshot has no corporate_actions dataset; none will be applied.")
    return datasets, args.snapshot_id or "latest_snapshot"


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        manifest = run(args)
    except Exception as exc:
        LOGGER.error("Monthly backtest failed: %s", exc)
        return 1
    LOGGER.info("Saved run artifacts: %s", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
