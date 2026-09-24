"""CLI for the price-factor portfolio comparison."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ashare_multifactor.research.portfolio_backtest import (
    PortfolioResearchConfig,
    run_portfolio_research,
)

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESEARCH_ROOT = PROJECT_ROOT / "artifacts" / "factor-research"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "portfolio-research"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare monthly price-factor portfolios with turnover-based costs."
    )
    parser.add_argument(
        "--research-dir",
        type=Path,
        help="Factor-research run containing factor_snapshot.csv; defaults to latest run.",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--cash-buffer", type=float, default=0.05)
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--stamp-tax-sell", type=float, default=0.0005)
    parser.add_argument("--transfer-fee", type=float, default=0.00001)
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    parser.add_argument("--development-end", default="2023-12-31")
    return parser


def run(args: argparse.Namespace) -> Path:
    research_dir = args.research_dir or _latest_research_run()
    if not research_dir.is_absolute():
        research_dir = PROJECT_ROOT / research_dir
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir = output_dir.resolve()
    try:
        output_dir.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("Portfolio research outputs must stay inside the project.") from exc
    config = PortfolioResearchConfig(
        top_n=args.top_n,
        cash_buffer=args.cash_buffer,
        commission_rate=args.commission_rate,
        stamp_tax_sell=args.stamp_tax_sell,
        transfer_fee=args.transfer_fee,
        slippage_bps=args.slippage_bps,
        development_end=args.development_end,
    )
    return run_portfolio_research(
        research_dir / "factor_snapshot.csv",
        output_dir,
        config,
    )


def _latest_research_run() -> Path:
    candidates = [
        path
        for path in DEFAULT_RESEARCH_ROOT.iterdir()
        if (path / "factor_snapshot.csv").is_file()
    ]
    if not candidates:
        raise FileNotFoundError(f"No completed factor research under {DEFAULT_RESEARCH_ROOT}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        manifest = run(build_parser().parse_args(argv))
    except Exception as exc:  # noqa: BLE001 - CLI reports the failure cleanly
        LOGGER.error("Portfolio research failed: %s", exc)
        return 1
    LOGGER.info("Portfolio research saved: %s", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
