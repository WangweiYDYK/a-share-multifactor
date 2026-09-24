"""Focused checks for the partitioned BaoStock price-factor workflow."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ashare_multifactor.research.price_factors import (
    PriceFactorResearchConfig,
    run_price_factor_research,
)


class PriceFactorResearchTest(unittest.TestCase):
    def test_writes_quality_factor_and_html_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary) / "input"
            output = Path(temporary) / "output"
            raw_dir = root / "daily_prices" / "none"
            adjusted_dir = root / "daily_prices" / "backward"
            raw_dir.mkdir(parents=True)
            adjusted_dir.mkdir(parents=True)

            dates = pd.bdate_range("2023-01-02", periods=320)
            pd.DataFrame(
                {
                    "trade_date": dates.strftime("%Y-%m-%d"),
                    "is_open": 1,
                }
            ).to_csv(root / "trade_calendar.csv", index=False)

            symbols = [f"00000{index}.SZ" for index in range(1, 7)]
            for offset, symbol in enumerate(symbols, start=1):
                base = 10.0 + offset
                closes = [
                    base * (1.0 + (0.0002 * offset - 0.0005) * day)
                    * (1.0 + 0.01 * ((day + offset) % 9) / 9.0)
                    for day in range(len(dates))
                ]
                frame = pd.DataFrame(
                    {
                        "trade_date": dates.strftime("%Y-%m-%d"),
                        "open": [value * 0.999 for value in closes],
                        "high": [value * 1.01 for value in closes],
                        "low": [value * 0.99 for value in closes],
                        "close": closes,
                        "volume": 1_000_000 + offset,
                        "amount": 100_000_000 + offset,
                        "trade_status": 1,
                        "is_st": 0,
                        "available_at": [
                            f"{day}T18:00:00+08:00"
                            for day in dates.strftime("%Y-%m-%d")
                        ],
                    }
                )
                frame.to_csv(raw_dir / f"{symbol}.csv", index=False)
                frame.to_csv(adjusted_dir / f"{symbol}.csv", index=False)

            manifest_path = run_price_factor_research(
                root,
                output,
                PriceFactorResearchConfig(
                    min_listing_observations=80,
                    min_average_amount=0,
                    quantiles=3,
                ),
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["quality_summary"]["symbols_with_raw_data"], 6)
            self.assertGreater(manifest["evaluated_months"], 1)
            self.assertTrue((output / "data_quality_report.html").is_file())
            self.assertTrue((output / "factor_report.html").is_file())
            report = (output / "factor_report.html").read_text(encoding="utf-8")
            self.assertIn("如何阅读这份报告", report)
            self.assertIn("本次结果的直白结论", report)
            self.assertIn("Q5-Q1月均收益", report)

            summary = pd.read_csv(output / "factor_summary.csv")
            self.assertEqual(
                set(summary["factor"]),
                {"reversal_20d", "momentum_60d", "low_vol_60d", "composite"},
            )
            quality = pd.read_csv(output / "data_quality.csv")
            self.assertTrue(quality["price_date_match"].all())


if __name__ == "__main__":
    unittest.main()
