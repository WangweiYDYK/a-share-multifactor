"""Translate target weights into order intents against the real account.

Sells are sized first so their proceeds can fund buys, positions are rounded to
the exchange lot size, and everything the account cannot support is reported as
a skipped order instead of being silently trimmed away.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from ashare_multifactor.execution.account import Account
from ashare_multifactor.execution.simulator import (
    SIDE_BUY,
    SIDE_SELL,
    ExecutionRules,
    OrderIntent,
)
from ashare_multifactor.portfolio.constructor import TargetPortfolio

ORDERS_VERSION = "order-intents-v1"
CASH_SAFETY = 1e-9


@dataclass(frozen=True)
class SkippedOrder:
    symbol: str
    side: str
    volume: int
    reason: str


@dataclass(frozen=True)
class OrderPlan:
    orders: tuple[OrderIntent, ...]
    skipped: tuple[SkippedOrder, ...]


def create_order_intents(
    target: TargetPortfolio,
    account: Account,
    decision_prices: Mapping[str, float],
    *,
    signal_time: str,
    order_time: str,
    execution_date: str | None,
    rules: ExecutionRules,
) -> OrderPlan:
    """Size target positions at the decision close and queue them for T+1."""
    if execution_date is None:
        return OrderPlan(orders=(), skipped=())

    lot = rules.lot_size
    equity = account.total_equity(decision_prices)
    if equity <= 0:
        return OrderPlan(orders=(), skipped=())

    target_shares: dict[str, int] = {}
    skipped: list[SkippedOrder] = []
    for symbol, weight in sorted(target.weights.items()):
        price = decision_prices.get(symbol)
        if price is None or price <= 0:
            skipped.append(
                SkippedOrder(symbol=symbol, side=SIDE_BUY, volume=0, reason="missing_decision_price")
            )
            continue
        target_shares[symbol] = int(weight * equity / price) // lot * lot

    symbols = sorted(set(target_shares) | set(account.positions))
    sells: list[OrderIntent] = []
    buys: list[OrderIntent] = []

    for symbol in symbols:
        current = account.shares(symbol)
        desired = target_shares.get(symbol, 0)
        delta = desired - current
        price = decision_prices.get(symbol)
        if price is None or price <= 0:
            if delta != 0:
                skipped.append(
                    SkippedOrder(
                        symbol=symbol,
                        side=SIDE_BUY if delta > 0 else SIDE_SELL,
                        volume=abs(delta),
                        reason="missing_decision_price",
                    )
                )
            continue

        if delta < 0:
            requested = -delta
            sellable = account.position(symbol).available_to_sell
            volume = min(requested, sellable) // lot * lot
            if volume <= 0:
                skipped.append(
                    SkippedOrder(
                        symbol=symbol, side=SIDE_SELL, volume=requested, reason="t_plus_one_lock"
                    )
                )
                continue
            if volume < requested:
                skipped.append(
                    SkippedOrder(
                        symbol=symbol,
                        side=SIDE_SELL,
                        volume=requested - volume,
                        reason="t_plus_one_lock",
                    )
                )
            sells.append(
                OrderIntent(
                    execution_date=execution_date,
                    signal_time=signal_time,
                    order_time=order_time,
                    symbol=symbol,
                    side=SIDE_SELL,
                    requested_volume=volume,
                    requested_price=price,
                    reason="rebalance_exit" if desired == 0 else "target_decrease",
                )
            )
        elif delta > 0:
            volume = delta // lot * lot
            if volume > 0:
                buys.append(
                    OrderIntent(
                        execution_date=execution_date,
                        signal_time=signal_time,
                        order_time=order_time,
                        symbol=symbol,
                        side=SIDE_BUY,
                        requested_volume=volume,
                        requested_price=price,
                        reason="new_position" if current == 0 else "target_increase",
                    )
                )

    available_cash = account.cash + sum(
        order.requested_volume * order.requested_price for order in sells
    )
    approved: list[OrderIntent] = list(sells)
    for order in buys:
        price = order.requested_price
        unit_cost = price * (1.0 + rules.commission_rate + rules.transfer_fee)
        affordable = int(max(available_cash, 0.0) / unit_cost) // lot * lot
        if affordable <= 0:
            skipped.append(
                SkippedOrder(
                    symbol=order.symbol,
                    side=SIDE_BUY,
                    volume=order.requested_volume,
                    reason="insufficient_cash",
                )
            )
            continue
        if affordable < order.requested_volume:
            skipped.append(
                SkippedOrder(
                    symbol=order.symbol,
                    side=SIDE_BUY,
                    volume=order.requested_volume - affordable,
                    reason="insufficient_cash",
                )
            )
            approved.append(replace(order, requested_volume=affordable))
        else:
            approved.append(order)
        available_cash -= affordable * price * (1.0 + rules.commission_rate + rules.transfer_fee)

    return OrderPlan(orders=tuple(approved), skipped=tuple(skipped))
