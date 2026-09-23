"""Backtest configuration and version digests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from ashare_multifactor.data.universe import UniverseConfig
from ashare_multifactor.execution.simulator import ExecutionRules, LimitRules
from ashare_multifactor.portfolio.constructor import PortfolioConstraints

CONFIG_VERSION = "backtest-config-v1"


@dataclass(frozen=True)
class BacktestConfig:
    """One frozen configuration for a reproducible monthly backtest."""

    start_date: str
    end_date: str
    industry_system: str
    data_version: str
    min_average_amount: float = 50_000_000.0
    min_listing_trading_days: int = 120
    liquidity_window: int = 20
    initial_cash: float = 10_000_000.0
    top_n: int = 30
    max_weight: float = 0.04
    max_industry_weight: float = 0.20
    cash_buffer: float = 0.05
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_sell: float = 0.0005
    transfer_fee: float = 0.00001
    slippage_bps: float = 10.0
    lot_size: int = 100
    max_participation_rate: float = 0.05
    random_seed: int = 7

    def universe_config(self) -> UniverseConfig:
        return UniverseConfig(
            min_average_amount=self.min_average_amount,
            industry_system=self.industry_system,
            min_listing_trading_days=self.min_listing_trading_days,
            liquidity_window=self.liquidity_window,
        )

    def portfolio_constraints(self) -> PortfolioConstraints:
        return PortfolioConstraints(
            top_n=self.top_n,
            max_weight=self.max_weight,
            max_industry_weight=self.max_industry_weight,
            cash_buffer=self.cash_buffer,
        )

    def execution_rules(self) -> ExecutionRules:
        return ExecutionRules(
            commission_rate=self.commission_rate,
            min_commission=self.min_commission,
            stamp_tax_sell=self.stamp_tax_sell,
            transfer_fee=self.transfer_fee,
            slippage_bps=self.slippage_bps,
            lot_size=self.lot_size,
            max_participation_rate=self.max_participation_rate,
        )

    def limit_rules(self) -> LimitRules:
        return LimitRules()

    def payload(self) -> dict[str, object]:
        return {"config_version": CONFIG_VERSION, **asdict(self)}

    def version(self) -> str:
        encoded = json.dumps(self.payload(), sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
