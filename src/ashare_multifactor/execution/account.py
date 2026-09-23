"""Cash, position, and valuation accounting for the simulated account."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

ACCOUNT_VERSION = "account-v1"


@dataclass
class Position:
    """A long position; shares bought today stay unsellable until T+1."""

    symbol: str
    shares: int = 0
    avg_cost: float = 0.0
    unavailable_shares: int = 0

    @property
    def available_to_sell(self) -> int:
        return max(self.shares - self.unavailable_shares, 0)


@dataclass
class Account:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    corporate_action_log: list[dict[str, Any]] = field(default_factory=list)

    def start_of_day(self) -> None:
        """Release the T+1 lock acquired on the previous trading day."""
        for position in self.positions.values():
            position.unavailable_shares = 0

    def position(self, symbol: str) -> Position:
        position = self.positions.get(symbol)
        if position is None:
            position = Position(symbol=symbol)
            self.positions[symbol] = position
        return position

    def shares(self, symbol: str) -> int:
        position = self.positions.get(symbol)
        return position.shares if position else 0

    def buy(self, symbol: str, shares: int, price: float, fee: float) -> None:
        """Buy shares; the whole lot becomes sellable on the next trading day."""
        if shares <= 0:
            raise ValueError("Buy volume must be positive.")
        position = self.position(symbol)
        gross = shares * price
        total_cost = gross + fee
        if total_cost > self.cash + 1e-9:
            raise ValueError("Buy exceeds available cash.")
        previous_cost = position.avg_cost * position.shares
        position.shares += shares
        position.avg_cost = (previous_cost + total_cost) / position.shares
        position.unavailable_shares += shares
        self.cash -= total_cost
        self.fees_paid += fee

    def sell(self, symbol: str, shares: int, price: float, fee: float) -> float:
        """Sell shares already released by T+1 and return realized pnl."""
        if shares <= 0:
            raise ValueError("Sell volume must be positive.")
        position = self.positions.get(symbol)
        if position is None or shares > position.available_to_sell:
            raise ValueError("Sell exceeds the T+1 sellable quantity.")
        gross = shares * price
        realized = (price - position.avg_cost) * shares - fee
        position.shares -= shares
        self.cash += gross - fee
        self.fees_paid += fee
        self.realized_pnl += realized
        if position.shares == 0:
            self.positions.pop(symbol)
        return realized

    def apply_split(self, symbol: str, ratio: float, ex_date: str) -> None:
        """Update share count after a bonus issue or share split."""
        position = self.positions.get(symbol)
        if position is None or position.shares == 0:
            return
        new_shares = int(round(position.shares * ratio))
        position.unavailable_shares = int(round(position.unavailable_shares * ratio))
        position.shares = new_shares
        position.avg_cost = position.avg_cost / ratio
        self.corporate_action_log.append(
            {
                "ex_date": ex_date,
                "symbol": symbol,
                "action_type": "split",
                "ratio": ratio,
                "shares_after": new_shares,
            }
        )

    def apply_cash_dividend(
        self, symbol: str, dividend_per_share: float, ex_date: str
    ) -> None:
        """Credit a cash dividend without touching the price-based pnl."""
        position = self.positions.get(symbol)
        if position is None or position.shares == 0:
            return
        amount = position.shares * dividend_per_share
        self.cash += amount
        self.corporate_action_log.append(
            {
                "ex_date": ex_date,
                "symbol": symbol,
                "action_type": "cash_dividend",
                "dividend_per_share": dividend_per_share,
                "cash_amount": amount,
            }
        )

    def market_value(self, prices: Mapping[str, float]) -> float:
        """Mark every held position; missing prices fall back to average cost."""
        value = 0.0
        for symbol, position in self.positions.items():
            price = prices.get(symbol)
            value += position.shares * (price if price is not None else position.avg_cost)
        return value

    def total_equity(self, prices: Mapping[str, float]) -> float:
        return self.cash + self.market_value(prices)
