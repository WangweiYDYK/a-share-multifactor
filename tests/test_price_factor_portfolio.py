"""Focused checks for the monthly price-factor portfolio comparison."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ashare_multifactor.research.portfolio_backtest import (
    PortfolioResearchConfig,
    run_portfolio_research,
)


class PriceFactorPortfolioTest(unittest.TestCase):
    def test_compares_strategies_costs_and_validation_period(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            snapshot = root / "factor_snapshot.csv"
            output = root / "output"
            rows = []
            decisions = pd.date_range("2022-01-31", periods=36, freq="ME")
            symbols = [f"{index:06d}.SZ" for index in range(1, 61)]
            for month, decision in enumerate(decisions):
                entry = decision + pd.offsets.BDay(1)
                exit_day = decision + pd.offsets.BDay(22)
                for index, symbol in enumerate(symbols):
                    centered = (index - 29.5) / 17.5
                    reversal = centered + 0.05 * math.sin(month + index)
                    low_vol = centered + 0.05 * math.cos(month - index)
                    momentum = -centered
                    dual = (reversal + low_vol) / 2.0
                    rows.append(
                        {
                            "decision_date": decision.date().isoformat(),
                            "entry_date": entry.date().isoformat(),
                            "exit_date": exit_day.date().isoformat(),
                            "symbol": symbol,
                            "forward_return": 0.015 * dual + 0.002 * math.sin(month),
                            "score_reversal_20d": reversal,
                            "score_momentum_60d": momentum,
                            "score_low_vol_60d": low_vol,
                            "composite_score": (reversal + low_vol + momentum) / 3.0,
                        }
                    )
            pd.DataFrame(rows).to_csv(snapshot, index=False)

            manifest = run_portfolio_research(
                snapshot,
                output,
                PortfolioResearchConfig(top_n=10, development_end="2023-12-31"),
            )

            self.assertTrue(manifest.is_file())
            metrics = pd.read_csv(output / "portfolio_metrics.csv")
            self.assertEqual(set(metrics["period"]), {"full", "development", "validation"})
            self.assertEqual(len(metrics), 15)
            validation = metrics.loc[metrics["period"] == "validation"].set_index("strategy")
            self.assertGreater(
                validation.loc["dual_factor", "total_return"],
                validation.loc["eligible_equal_weight", "total_return"],
            )

            sensitivity = pd.read_csv(output / "cost_sensitivity.csv")
            full = sensitivity.loc[sensitivity["period"] == "full"].set_index(
                "cost_multiplier"
            )
            self.assertGreater(full.loc[0.0, "total_return"], full.loc[1.0, "total_return"])
            self.assertGreater(full.loc[1.0, "total_return"], full.loc[2.0, "total_return"])

            report = (output / "portfolio_report.html").read_text(encoding="utf-8")
            self.assertIn("本次结果怎么理解", report)
            self.assertIn("双因子成本敏感性", report)
            self.assertIn("权重级研究回测", report)


if __name__ == "__main__":
    unittest.main()
