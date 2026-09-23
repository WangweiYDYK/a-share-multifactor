"""Focused offline checks for the snapshot-to-universe vertical slice."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from ashare_multifactor.data.repository import DataRepository
from ashare_multifactor.data.universe import (
    UniverseBuildError,
    build_universe,
    build_universe_from_repository,
    write_universe_result,
)
from ashare_multifactor.data.universe_demo import (
    DECISION_DATE,
    DEMO_CONFIG,
    MONTH,
    SNAPSHOT_ID,
    run,
    synthetic_datasets,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class UniverseBuilderTest(unittest.TestCase):
    def test_snapshot_to_result_and_replay(self) -> None:
        run_id = datetime.now(timezone.utc).strftime("verified-%Y%m%dT%H%M%S%fZ")
        output = PROJECT_ROOT / "artifacts" / "universe-demo" / run_id
        manifest_path = run(output)
        summary = json.loads(manifest_path.read_text(encoding="utf-8"))
        eligible = json.loads((manifest_path.parent / "eligible.json").read_text(encoding="utf-8"))
        exclusions = json.loads(
            (manifest_path.parent / "exclusions.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary["candidate_count"], 12)
        self.assertEqual(summary["eligible_count"], 1)
        self.assertEqual(summary["excluded_count"], 11)
        self.assertEqual(summary["decision_date"], DECISION_DATE)
        self.assertEqual(summary["as_of"], "2025-08-29T18:00:00+08:00")
        self.assertTrue(summary["synthetic"])
        self.assertEqual([row["symbol"] for row in eligible], ["DEMO01.SH"])
        self.assertEqual(eligible[0]["industry_code"], "DEMO_BANK")
        self.assertEqual(eligible[0]["status"]["is_st"], 0)
        self.assertIsNone(eligible[0]["security_master"]["delist_date"])
        self.assertEqual(len(summary["liquidity_dates"]), 20)
        for dataset in summary["datasets"].values():
            self.assertEqual(dataset["snapshot_id"], SNAPSHOT_ID)
            self.assertEqual(dataset["source"], "synthetic_universe_demo")
            self.assertEqual(len(dataset["visible_content_sha256"]), 64)

        by_symbol = {row["symbol"]: row for row in exclusions}
        expected = {
            "DEMO02.SH": "st", "DEMO03.SH": "suspended",
            "DEMO04.SH": "delisted_at_decision", "DEMO05.SH": "listing_too_short",
            "DEMO06.SH": "missing_daily_basic", "DEMO07.SH": "low_liquidity",
            "DEMO08.SH": "missing_industry", "DEMO09.SH": "insufficient_price_history",
            "DEMO10.SH": "unknown_security_status", "DEMO11.SH": "not_listed_at_decision",
            "DEMO12.SH": "missing_security_status",
        }
        for symbol, reason in expected.items():
            self.assertIn(reason, by_symbol[symbol]["reasons"], symbol)
        self.assertIsNone(by_symbol["DEMO09.SH"]["average_amount"])
        self.assertEqual(by_symbol["DEMO09.SH"]["missing_amount_dates"], ["2025-08-28"])

        replay = build_universe_from_repository(
            DataRepository(output / "snapshots", snapshot_id=SNAPSHOT_ID),
            MONTH, config=DEMO_CONFIG,
        )
        self.assertEqual(replay.summary, summary)
        self.assertEqual(list(replay.eligible), eligible)
        self.assertEqual(list(replay.exclusions), exclusions)
        with self.assertRaises(FileExistsError):
            write_universe_result(manifest_path.parent, replay)
        print(f"\nSynthetic result: {manifest_path}")

    def test_requires_pinned_snapshot(self) -> None:
        with self.assertRaisesRegex(UniverseBuildError, "snapshot_id"):
            build_universe_from_repository(
                DataRepository(PROJECT_ROOT / "data" / "normalized" / "snapshots"),
                MONTH, config=DEMO_CONFIG,
            )

    def test_blocks_calendar_gap_and_entire_dataset_gaps(self) -> None:
        for name in ("trade_calendar", "security_status", "industry_membership", "daily_basic"):
            with self.subTest(dataset=name):
                datasets = {d.name: d for d in synthetic_datasets()}
                original = datasets[name]
                remaining = (
                    [r for r in original.rows if r["trade_date"] != "2025-08-31"]
                    if name == "trade_calendar" else []
                )
                datasets[name] = replace(original, rows=remaining)
                with self.assertRaises(UniverseBuildError):
                    build_universe(datasets, month=MONTH, config=DEMO_CONFIG)

    def test_duplicate_versions_do_not_silently_pick_one(self) -> None:
        datasets = {d.name: d for d in synthetic_datasets()}
        original = datasets["security_status"]
        datasets["security_status"] = replace(
            original, rows=[*original.rows, dict(original.rows[0])]
        )
        with self.assertRaisesRegex(UniverseBuildError, "duplicate"):
            build_universe(datasets, month=MONTH, config=DEMO_CONFIG)

    def test_latest_visible_industry_revision_and_window_requirement(self) -> None:
        datasets = {d.name: d for d in synthetic_datasets()}
        original = datasets["industry_membership"]
        revision = {
            **original.rows[0], "effective_to": DECISION_DATE,
            "available_at": "2025-08-29T17:00:00+08:00",
        }
        datasets["industry_membership"] = replace(original, rows=[*original.rows, revision])
        result = build_universe(datasets, month=MONTH, config=DEMO_CONFIG)
        excluded = {r["symbol"]: r for r in result.exclusions}
        self.assertIn("missing_industry", excluded["DEMO01.SH"]["reasons"])
        with self.assertRaisesRegex(UniverseBuildError, "window"):
            build_universe(
                datasets, month=MONTH, config=replace(DEMO_CONFIG, liquidity_window=1000)
            )


if __name__ == "__main__":
    unittest.main()
