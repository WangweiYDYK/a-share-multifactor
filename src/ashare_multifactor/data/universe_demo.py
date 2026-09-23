"""Offline synthetic universe demo. No real prices, symbols, or exchange calendar."""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from ashare_multifactor.data.contracts import CanonicalDataset
from ashare_multifactor.data.repository import DataRepository, DataRepositoryError
from ashare_multifactor.data.storage import write_snapshot
from ashare_multifactor.data.universe import (
    UniverseConfig,
    build_universe_from_repository,
    write_universe_result,
)
from ashare_multifactor.reports.universe import write_universe_report

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MONTH = "2025-08"
DECISION_DATE = "2025-08-29"
SNAPSHOT_ID = "synthetic-universe-2025-08-v1"
DEMO_CONFIG = UniverseConfig(min_average_amount=5000.0, industry_system="SYNTHETIC")
LOGGER = logging.getLogger(__name__)


def synthetic_datasets() -> list[CanonicalDataset]:
    """Generate known-answer cases; weekdays here are NOT the actual A-share calendar."""
    days = []
    current = date(2025, 2, 3)
    while current <= date(2025, 9, 1):
        days.append(current)
        current += timedelta(days=1)
    price_days = [d.isoformat() for d in days if d.weekday() < 5 and d.isoformat() <= DECISION_DATE][-20:]
    symbols = [f"DEMO{i:02}.SH" for i in range(1, 13)]
    master, prices, basics, statuses, industries = [], [], [], [], []
    for i, symbol in enumerate(symbols, start=1):
        master.append(_row(
            symbol=symbol, security_name=f"Synthetic case {i:02}", security_type="stock",
            list_date="2025-08-27" if i == 5 else "2025-09-01" if i == 11 else "2025-02-03",
            delist_date="2025-08-28" if i == 4 else None,
            list_status="listed", industry="DO_NOT_USE_CURRENT_MASTER_INDUSTRY",
        ))
        for day in price_days:
            if i == 9 and day == "2025-08-28":
                continue
            prices.append(_row(
                available_at=f"{day}T18:00:00+08:00", symbol=symbol, trade_date=day,
                security_name=f"Synthetic case {i:02}",
                open=10.0, high=10.5, low=9.5, close=10.0, pre_close=10.0,
                volume=10.0 if i == 7 else 1000.0,
                amount=100.0 if i == 7 else 10000.0,
                turnover_rate=1.0, pct_change=0.0, pe_ttm=10.0, pb_mrq=1.0,
                ps_ttm=2.0, pcf_ncf_ttm=8.0, trade_status=1, is_st=int(i == 2),
                adjustment="none",
            ))
        basics.append(_row(
            available_at=f"{DECISION_DATE}T18:00:00+08:00", symbol=symbol,
            trade_date="2025-08-28" if i == 6 else DECISION_DATE,
            total_mv=100000000.0, circ_mv=80000000.0,
        ))
        statuses.append(_row(
            available_at="2025-08-30T09:00:00+08:00" if i == 12
            else f"{DECISION_DATE}T18:00:00+08:00",
            symbol=symbol, trade_date=DECISION_DATE,
            list_status="delisted" if i == 4 else "listed",
            trade_status=0 if i == 3 else 1,
            is_st=None if i == 10 else int(i == 2),
            is_suspended=int(i == 3), is_delisting=0,
        ))
        industries.append(_row(
            symbol=symbol, industry_system="SYNTHETIC",
            industry_code="DEMO_BANK", industry_name="Synthetic banking",
            effective_from="2025-02-03", effective_to=DECISION_DATE if i == 8 else None,
        ))

    # A later correction, a later delisting, and a weekend industry update must not leak.
    master.append({**master[0], "list_status": "delisted", "delist_date": "2025-08-30",
                   "available_at": "2025-08-30T09:00:00+08:00"})
    statuses.append({**statuses[0], "is_st": 1, "available_at": "2025-08-30T09:00:00+08:00"})
    industries.append({**industries[0], "industry_code": "DEMO_TECH",
                       "industry_name": "Synthetic technology",
                       "available_at": "2025-08-30T09:00:00+08:00"})
    # An adjusted row cannot replace a missing raw-price observation.
    prices.append({
        **prices[0], "symbol": "DEMO09.SH", "security_name": "Synthetic case 09",
        "trade_date": "2025-08-28", "amount": 90000000.0, "volume": 9000000.0,
        "adjustment": "forward", "available_at": "2025-08-28T18:00:00+08:00",
    })
    calendar_rows = [_row(exchange="SSE", trade_date=d.isoformat(), is_open=int(d.weekday() < 5))
                     for d in days]
    return [
        _dataset("trade_calendar", ("exchange", "trade_date"), calendar_rows),
        _dataset("security_master", ("symbol", "available_at"), master),
        _dataset("daily_prices", ("symbol", "trade_date", "adjustment"), prices),
        _dataset("daily_basic", ("symbol", "trade_date"), basics),
        _dataset("security_status", ("symbol", "trade_date", "available_at"), statuses),
        _dataset("industry_membership", ("symbol", "industry_system", "effective_from",
                                         "available_at"), industries),
    ]


def run(output_dir: Path) -> Path:
    """Write synthetic snapshots and decisions under a new repository-local directory."""
    output_dir = (PROJECT_ROOT / output_dir).resolve()
    try:
        output_dir.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("Demo output must stay inside the project repository.") from exc
    output_dir.mkdir(parents=True, exist_ok=False)
    root = output_dir / "snapshots"
    write_snapshot(root, synthetic_datasets(), snapshot_id=SNAPSHOT_ID, as_of=DECISION_DATE)
    repository = DataRepository(root, snapshot_id=SNAPSHOT_ID)
    result = build_universe_from_repository(repository, MONTH, config=DEMO_CONFIG)
    manifest = write_universe_result(output_dir / "result", result)
    write_universe_report(manifest.parent)
    LOGGER.info(
        "SYNTHETIC ONLY: %s at %s; candidates=%s eligible=%s excluded=%s",
        MONTH, result.decision_as_of, result.summary["candidate_count"],
        len(result.eligible), len(result.exclusions),
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=PROJECT_ROOT / "artifacts" / "universe-demo" /
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        manifest = run(args.output_dir)
    except (ValueError, OSError, DataRepositoryError) as exc:
        LOGGER.error("Synthetic universe demo failed: %s", exc)
        return 1
    LOGGER.info("Saved synthetic universe result: %s", manifest)
    LOGGER.info("Saved visual report: %s", manifest.parent / "report.html")
    return 0


def _row(**values: Any) -> dict[str, Any]:
    return {
        "source": "synthetic_universe_demo", "source_version": "fixture-1",
        "retrieved_at": "2025-09-05T12:00:00+08:00",
        "available_at": "2025-02-01T12:00:00+08:00", **values,
    }


def _dataset(name: str, key: tuple[str, ...], rows: list[dict[str, Any]]) -> CanonicalDataset:
    return CanonicalDataset(name, key, rows, metadata={
        "synthetic": True, "availability_basis": "synthetic_fixture_not_market_data",
    })


if __name__ == "__main__":
    raise SystemExit(main())
