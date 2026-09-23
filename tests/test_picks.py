"Focused offline checks for the month-end reference pick list."

from __future__ import annotations

import unittest
from typing import Any, Mapping

from ashare_multifactor.backtest.synthetic_market import generate_synthetic_market
from ashare_multifactor.picks.builder import build_pick_list
from ashare_multifactor.picks.report import format_picks, render_markdown
from ashare_multifactor.picks.screen import PicksConfig, screen_snapshot

DATA_START = "2023-01-02"
DATA_END = "2024-12-31"
AS_OF = "2024-07-31"
SYMBOLS = 40
SEED = 5
OPTIONAL_DATASETS = ("daily_basic", "security_status", "industry_membership")


def _datasets(*, drop_optional: bool = False) -> dict[str, Any]:
    data = generate_synthetic_market(
        start_date=DATA_START,
        end_date=DATA_END,
        n_symbols=SYMBOLS,
        seed=SEED,
    )
    if drop_optional:
        for name in OPTIONAL_DATASETS:
            data.pop(name, None)
    return data


def _backward_closes(
    datasets: Mapping[str, Any],
    symbol: str,
    decision_date: str,
) -> list[float]:
    by_date: dict[str, float] = {}
    for row in datasets["daily_prices"].rows:
        if row.get("symbol") != symbol or row.get("adjustment") != "backward":
            continue
        trade_date = str(row.get("trade_date"))
        if trade_date <= decision_date:
            by_date[trade_date] = float(row["close"])
    return [by_date[day] for day in sorted(by_date)]


class MonthlyPicksTest(unittest.TestCase):
    def test_synthetic_list_is_ranked_and_reports_held_return(self) -> None:
        datasets = _datasets()
        config = PicksConfig(as_of=AS_OF, top_n=10)
        result = build_pick_list(datasets, config)

        self.assertTrue(result.picks)
        ranks = [pick["rank"] for pick in result.picks]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(ranks[0], 1)
        for pick in result.picks:
            self.assertLessEqual(pick["weight"], config.max_weight + 1e-9)

        top = result.picks[0]
        self.assertEqual(top["last_quote_date"], result.decision_date)
        closes = _backward_closes(datasets, top["symbol"], result.decision_date)
        expected_return = closes[-1] / closes[-21] - 1.0
        self.assertAlmostEqual(top["return_20d"], expected_return, places=3)

        self.assertIn(top["symbol"], render_markdown(result))
        self.assertIn(top["security_name"], format_picks(result))

    def test_missing_optional_datasets_are_reported_as_skipped(self) -> None:
        datasets = _datasets(drop_optional=True)
        config = PicksConfig(as_of=AS_OF, top_n=10)
        screen = screen_snapshot(datasets, config)

        self.assertTrue(screen.candidates)
        for name in ("market_cap", "industry", "industry_neutralization"):
            status = screen.screens[name]
            self.assertTrue(status.startswith("skipped"), status)
        # Suspension and ST still come from the quote table, so both stay applied.
        self.assertTrue(screen.screens["suspension"].startswith("applied"))
        self.assertTrue(screen.screens["st"].startswith("applied"))
        counts = screen.summary["exclusion_counts"]
        self.assertGreaterEqual(counts.get("st_flag", 0), 1)
        self.assertGreaterEqual(counts.get("suspended", 0), 1)

        result = build_pick_list(datasets, config)
        self.assertTrue(result.picks)

    def test_industry_system_mismatch_is_reported(self) -> None:
        datasets = _datasets()
        config = PicksConfig(as_of=AS_OF, top_n=10, industry_system="SW")
        result = build_pick_list(datasets, config)

        self.assertTrue(result.picks)
        self.assertIn("skipped", result.screens["industry"])
        self.assertTrue(
            any(note.startswith("industry") for note in result.manifest["limitations"]),
            result.manifest["limitations"],
        )

    def test_declared_industry_system_is_used_when_it_matches(self) -> None:
        datasets = _datasets()
        config = PicksConfig(as_of=AS_OF, top_n=10, industry_system="SYNTHETIC_SW")
        result = build_pick_list(datasets, config)

        self.assertTrue(result.picks)
        self.assertIn("SYNTHETIC_SW", result.screens["industry"])
        self.assertTrue(result.screens["industry_neutralization"].startswith("applied"))
        self.assertTrue(all(pick["industry_code"] for pick in result.picks))

    def test_markdown_report_keeps_table_and_disclaimer(self) -> None:
        result = build_pick_list(_datasets(), PicksConfig(as_of=AS_OF, top_n=5))
        markdown = render_markdown(result)

        self.assertTrue(markdown.startswith("# "))
        self.assertIn("| --- |", markdown)
        self.assertIn(result.manifest["disclaimer"], markdown)
        self.assertIn(result.picks[0]["symbol"], format_picks(result, limit=1))

    def test_short_history_is_excluded_instead_of_scored(self) -> None:
        datasets = _datasets()
        config = PicksConfig(as_of="2023-02-28", top_n=10)
        screen = screen_snapshot(datasets, config)

        self.assertFalse(screen.candidates)
        self.assertGreaterEqual(
            screen.summary["exclusion_counts"].get("insufficient_price_history", 0), 1
        )


if __name__ == "__main__":
    unittest.main()
