"""Focused offline checks for the end-to-end monthly backtest slice."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ashare_multifactor.backtest.config import BacktestConfig
from ashare_multifactor.backtest.engine import (
    _visible_closes,
    run_backtest,
    write_run_artifacts,
)
from ashare_multifactor.backtest.synthetic_market import (
    INDUSTRY_SYSTEM,
    generate_synthetic_market,
    synthetic_data_version,
)
from ashare_multifactor.data.contracts import CanonicalDataset
from ashare_multifactor.execution.account import Account
from ashare_multifactor.execution.orders import create_order_intents
from ashare_multifactor.execution.simulator import (
    ExecutionRules,
    LimitRules,
    OrderIntent,
    execute_orders,
    trade_fee,
)
from ashare_multifactor.portfolio.constructor import TargetPortfolio

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_START = "2023-01-02"
DATA_END = "2024-07-31"
BACKTEST_START = "2023-07-03"
SYMBOLS = 40
SEED = 11
SHANGHAI_TZ = timezone(timedelta(hours=8))


class MonthlyBacktestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.datasets = generate_synthetic_market(
            start_date=DATA_START,
            end_date=DATA_END,
            n_symbols=SYMBOLS,
            seed=SEED,
        )
        cls.config = BacktestConfig(
            start_date=BACKTEST_START,
            end_date=DATA_END,
            industry_system=INDUSTRY_SYSTEM,
            data_version=synthetic_data_version(n_symbols=SYMBOLS, seed=SEED),
            top_n=30,
            random_seed=SEED,
        )
        cls.result = run_backtest(cls.datasets, cls.config)

    def test_end_to_end_run_produces_reproducible_artifacts(self) -> None:
        open_days = _open_days(self.datasets, BACKTEST_START, DATA_END)
        expected_decisions = _month_ends(open_days)

        manifest = self.result.manifest
        self.assertEqual(manifest["decision_count"], len(expected_decisions))
        self.assertEqual(manifest["daily_observations"], len(open_days))
        self.assertTrue(manifest["synthetic"])
        self.assertEqual(
            manifest["data_version"],
            synthetic_data_version(n_symbols=SYMBOLS, seed=SEED),
        )
        for key in (
            "config_version",
            "factor_version",
            "preprocess_version",
            "universe_rule_version",
            "execution_rule_version",
        ):
            self.assertTrue(manifest[key], key)

        self.assertEqual(
            [row["decision_date"] for row in self.result.monthly], expected_decisions
        )
        for row in self.result.monthly:
            print(
                "decision={0} eligible={1} target_n={2} scored={3}".format(
                    row["decision_date"],
                    row["eligible_count"],
                    row["target_n"],
                    row["scored_symbols"],
                )
            )

        config = self.config
        list_dates = _list_dates(self.datasets)
        targets_by_decision: dict[str, list[dict[str, object]]] = {}
        for row in self.result.targets:
            targets_by_decision.setdefault(str(row["decision_date"]), []).append(row)
        self.assertEqual(len(targets_by_decision), len(expected_decisions))
        for decision, rows in targets_by_decision.items():
            self.assertLessEqual(len(rows), config.top_n)
            total = sum(float(row["weight"]) for row in rows)
            self.assertLessEqual(total, 1.0 - config.cash_buffer + 1e-6)
            for row in rows:
                symbol = str(row["symbol"])
                self.assertLessEqual(list_dates[symbol], decision)
                self.assertLessEqual(float(row["weight"]), config.max_weight + 1e-9)

        self.assertTrue(self.result.fills, "the run should execute at least one fill")
        for fill in self.result.fills:
            self.assertEqual(int(fill["volume"]) % config.lot_size, 0)
        for symbol in {row["symbol"] for row in self.result.fills}:
            sides = [
                row["side"] for row in self.result.fills if row["symbol"] == symbol
            ]
            self.assertEqual(sides[0], "buy", symbol)

        # Cash has to fund the whole target list, not just its first name.
        self.assertGreater(
            max(int(row["position_count"]) for row in self.result.daily), 10
        )

        self.assertEqual(self.result.daily[0]["nav"], 1.0)
        self.assertGreater(self.result.daily[-1]["total_equity"], 0.0)
        # A label needs a later execution day inside the window, so the last
        # decision and the one before it stay pending when data ends on a
        # month-end trading day.
        self.assertTrue(self.result.monthly[-1]["label_pending"])
        labeled = [row for row in self.result.monthly if not row["label_pending"]]
        self.assertGreaterEqual(len(labeled), len(self.result.monthly) - 2)
        for row in labeled:
            self.assertIsNotNone(row["ic"], row["decision_date"])
            self.assertIsNotNone(row["rank_ic"], row["decision_date"])

        run_id = datetime.now(timezone.utc).strftime("verified-%Y%m%dT%H%M%S%fZ")
        output = PROJECT_ROOT / "artifacts" / "backtest-demo" / run_id
        manifest_path = write_run_artifacts(self.result, output)
        written = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(written["decision_count"], manifest["decision_count"])
        for name in (
            "metrics.json",
            "targets.csv",
            "orders.csv",
            "fills.csv",
            "unfilled.csv",
            "daily_account.csv",
            "factor_snapshot.csv",
            "monthly_evaluation.csv",
        ):
            self.assertTrue((output / name).is_file(), name)
        metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(metrics["trade_count"], len(self.result.fills))
        print(f"\nEnd-to-end run artifacts: {output}")

    def test_order_plan_spends_only_the_approved_volume(self) -> None:
        rules = ExecutionRules()
        account = Account(cash=1_000_000.0)
        prices = {"600000.SH": 10.0, "600001.SH": 10.0, "600002.SH": 10.0}
        target = _target(weights={symbol: 0.30 for symbol in prices})

        plan = create_order_intents(
            target,
            account,
            prices,
            signal_time="2024-03-29T18:00:00+08:00",
            order_time="2024-03-29T18:00:00+08:00",
            execution_date="2024-04-01",
            rules=rules,
        )

        self.assertEqual([order.side for order in plan.orders], ["buy"] * 3)
        self.assertEqual(list(plan.skipped), [])
        spend = sum(
            order.requested_volume * prices[order.symbol] for order in plan.orders
        )
        fees = sum(
            trade_fee(order.requested_volume, prices[order.symbol], "buy", rules)
            for order in plan.orders
        )
        self.assertGreater(spend, 850_000.0)
        self.assertLessEqual(spend + fees, account.cash)

    def test_full_exit_submits_the_whole_odd_lot(self) -> None:
        rules = ExecutionRules()
        account = Account(cash=0.0)
        account.position("600000.SH").shares = 150

        plan = create_order_intents(
            _target(weights={}),
            account,
            {"600000.SH": 10.0},
            signal_time="2024-03-29T18:00:00+08:00",
            order_time="2024-03-29T18:00:00+08:00",
            execution_date="2024-04-01",
            rules=rules,
        )

        self.assertEqual(len(plan.orders), 1)
        self.assertEqual(plan.orders[0].side, "sell")
        self.assertEqual(plan.orders[0].requested_volume, 150)
        self.assertEqual(plan.orders[0].reason, "rebalance_exit")

    def test_factor_inputs_ignore_bars_published_after_the_cutoff(self) -> None:
        dataset = CanonicalDataset(
            name="daily_prices",
            primary_key=("trade_date", "symbol", "adjustment"),
            rows=[
                _price_row("2024-03-27", 9.0, "2024-03-27T18:00:00"),
                _price_row("2024-03-28", 10.0, "2024-03-28T18:00:00+08:00"),
                _price_row("2024-03-29", 11.0, "2024-03-29T20:00:00+08:00"),
            ],
            metadata={},
        )
        cutoff = datetime(2024, 3, 29, 18, 0, tzinfo=SHANGHAI_TZ)

        closes = _visible_closes(dataset, date(2024, 3, 29), cutoff)

        self.assertEqual(closes["600000.SH"], [10.0])

    def test_matching_enforces_a_share_constraints(self) -> None:
        rules = ExecutionRules()
        limits = LimitRules()
        account = Account(cash=1_000_000.0)
        held = account.position("600003.SH")
        held.shares = 5_000
        held.avg_cost = 10.0
        locked = account.position("600000.SH")
        locked.shares = 5_000
        locked.avg_cost = 10.0
        locked.unavailable_shares = 5_000

        bars = {
            "600000.SH": _bar(10.0, 10.0),
            "600001.SH": _bar(10.0, 10.0),
            "600002.SH": _bar(11.0, 10.0),
            "600003.SH": _bar(9.0, 10.0),
            "600004.SH": _bar(10.0, 10.0, volume=20_000),
        }
        statuses = {
            "600001.SH": {"trade_status": 0, "is_suspended": 1, "is_st": 0},
        }
        orders = (
            _order("600001.SH", "buy", 1_000),
            _order("600002.SH", "buy", 1_000),
            _order("600003.SH", "sell", 1_000),
            _order("600004.SH", "buy", 5_000),
            _order("600000.SH", "sell", 1_000),
        )

        result = execute_orders(orders, account, bars, statuses, rules, limits)
        reasons = {reject.reason for reject in result.rejects}
        self.assertIn("suspended", reasons)
        self.assertIn("limit_up", reasons)
        self.assertIn("limit_down", reasons)
        self.assertIn("participant_cap", reasons)
        self.assertIn("t_plus_one_lock", reasons)

        self.assertEqual(len(result.fills), 1)
        fill = result.fills[0]
        self.assertEqual(fill.symbol, "600004.SH")
        self.assertEqual(fill.volume, 1_000)
        self.assertEqual(fill.status, "partially_filled")
        self.assertEqual(account.shares("600004.SH"), 1_000)
        self.assertEqual(account.position("600004.SH").available_to_sell, 0)

        account.start_of_day()
        self.assertEqual(account.position("600004.SH").available_to_sell, 1_000)

    def test_split_updates_shares_without_changing_equity(self) -> None:
        account = Account(cash=0.0)
        position = account.position("600000.SH")
        position.shares = 1_000
        position.avg_cost = 20.0

        account.apply_split("600000.SH", 2.0, "2024-03-15")

        self.assertEqual(account.shares("600000.SH"), 2_000)
        self.assertAlmostEqual(account.total_equity({"600000.SH": 10.0}), 20_000.0)
        self.assertEqual(len(account.corporate_action_log), 1)
        self.assertEqual(account.corporate_action_log[0]["action_type"], "split")


def _target(weights: dict[str, float]) -> TargetPortfolio:
    return TargetPortfolio(
        decision_date="2024-03-29",
        execution_date="2024-04-01",
        weights=weights,
        scores={symbol: 0.0 for symbol in weights},
        ranks={symbol: index for index, symbol in enumerate(weights, start=1)},
        reasons={symbol: "test" for symbol in weights},
        constraint_state={},
    )


def _price_row(trade_date: str, close: float, available_at: str) -> dict:
    return {
        "trade_date": trade_date,
        "symbol": "600000.SH",
        "adjustment": "backward",
        "open": close,
        "close": close,
        "source": "test",
        "source_version": "test-v1",
        "available_at": available_at,
    }


def _open_days(datasets, start: str, end: str) -> list[str]:
    return sorted(
        row["trade_date"]
        for row in datasets["trade_calendar"].rows
        if row["is_open"] == 1 and start <= row["trade_date"] <= end
    )


def _month_ends(open_days: list[str]) -> list[str]:
    last: dict[str, str] = {}
    for day in open_days:
        last[day[:7]] = day
    return sorted(last.values())


def _list_dates(datasets) -> dict[str, str]:
    dates: dict[str, str] = {}
    for row in datasets["security_master"].rows:
        symbol = row["symbol"]
        if symbol not in dates or row["list_date"] < dates[symbol]:
            dates[symbol] = row["list_date"]
    return dates


def _bar(open_price: float, pre_close: float, volume: float = 2_000_000) -> dict:
    return {
        "open": open_price,
        "close": open_price,
        "pre_close": pre_close,
        "volume": volume,
        "trade_status": 1,
    }


def _order(symbol: str, side: str, volume: int) -> OrderIntent:
    return OrderIntent(
        execution_date="2024-03-15",
        signal_time="2024-03-14T18:00:00+08:00",
        order_time="2024-03-14T18:00:00+08:00",
        symbol=symbol,
        side=side,
        requested_volume=volume,
        requested_price=10.0,
        reason="test",
    )


if __name__ == "__main__":
    unittest.main()
