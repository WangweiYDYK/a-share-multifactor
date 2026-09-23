"""Day-line A-share order matching with fees, price limits, and T+1.

The model is deliberately conservative: the execution rule is fixed before the
next day is observed, the reference price is the next trading day's open, and
suspended, limit-locked, or volume-capped orders are recorded as unfilled
instead of being assumed to complete.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ashare_multifactor.execution.account import Account

EXECUTION_VERSION = "execution-simulator-v1"
SIDE_BUY = "buy"
SIDE_SELL = "sell"
SIDES = (SIDE_BUY, SIDE_SELL)


@dataclass(frozen=True)
class ExecutionRules:
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_sell: float = 0.0005
    transfer_fee: float = 0.00001
    slippage_bps: float = 10.0
    lot_size: int = 100
    max_participation_rate: float = 0.05
    rule_version: str = "historical_trade_rule_v1"


@dataclass(frozen=True)
class LimitRules:
    """Historical price-limit percentages, looked up per board and ST status."""

    main_board_pct: float = 0.10
    st_pct: float = 0.05
    growth_board_pct: float = 0.20
    bse_pct: float = 0.30
    rule_version: str = "price_limit_rule_v1"


@dataclass(frozen=True)
class OrderIntent:
    execution_date: str
    signal_time: str
    order_time: str
    symbol: str
    side: str
    requested_volume: int
    requested_price: float
    reason: str

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"Unsupported order side {self.side!r}.")
        if self.requested_volume <= 0:
            raise ValueError("Order volume must be positive.")


@dataclass(frozen=True)
class Fill:
    execution_date: str
    symbol: str
    side: str
    volume: int
    price: float
    gross_amount: float
    fee: float
    slippage_cost: float
    status: str
    reason: str


@dataclass(frozen=True)
class RejectedOrder:
    execution_date: str
    symbol: str
    side: str
    requested_volume: int
    filled_volume: int
    reason: str


@dataclass(frozen=True)
class ExecutionResult:
    fills: tuple[Fill, ...]
    rejects: tuple[RejectedOrder, ...]


def execute_orders(
    orders: Sequence[OrderIntent],
    account: Account,
    bars: Mapping[str, Mapping[str, Any]],
    statuses: Mapping[str, Mapping[str, Any]],
    rules: ExecutionRules,
    limits: LimitRules,
) -> ExecutionResult:
    """Match orders against one trading day of open prices and status."""
    fills: list[Fill] = []
    rejects: list[RejectedOrder] = []

    for order in orders:
        bar = bars.get(order.symbol)
        if bar is None:
            rejects.append(_reject(order, 0, "missing_quote"))
            continue

        status = statuses.get(order.symbol) or {}
        if _is_suspended(bar, status):
            rejects.append(_reject(order, 0, "suspended"))
            continue

        open_price = _number(bar.get("open"))
        if open_price is None or open_price <= 0:
            rejects.append(_reject(order, 0, "missing_open_price"))
            continue

        limit_down, limit_up = price_limits(order.symbol, bar, status, limits)
        if order.side == SIDE_BUY and limit_up is not None and open_price >= limit_up:
            rejects.append(_reject(order, 0, "limit_up"))
            continue
        if order.side == SIDE_SELL and limit_down is not None and open_price <= limit_down:
            rejects.append(_reject(order, 0, "limit_down"))
            continue

        capacity = _participant_capacity(bar, rules)
        if capacity <= 0:
            rejects.append(_reject(order, 0, "no_market_volume"))
            continue

        volume = min(order.requested_volume, capacity)
        reason = "participant_cap" if volume < order.requested_volume else order.reason
        price = _execution_price(open_price, order.side, rules)

        if order.side == SIDE_BUY:
            affordable = max_affordable_shares(account.cash, price, rules)
            if affordable <= 0:
                rejects.append(_reject(order, 0, "insufficient_cash"))
                continue
            if affordable < volume:
                volume = affordable
                reason = "insufficient_cash"
            fee = trade_fee(volume, price, SIDE_BUY, rules)
            account.buy(order.symbol, volume, price, fee)
        else:
            sellable = account.available_to_sell(order.symbol)
            if sellable <= 0:
                rejects.append(_reject(order, 0, "t_plus_one_lock"))
                continue
            if sellable < volume:
                volume = sellable
                reason = "t_plus_one_lock"
            fee = trade_fee(volume, price, SIDE_SELL, rules)
            account.sell(order.symbol, volume, price, fee)

        fills.append(
            Fill(
                execution_date=order.execution_date,
                symbol=order.symbol,
                side=order.side,
                volume=volume,
                price=round(price, 4),
                gross_amount=round(volume * price, 4),
                fee=round(fee, 4),
                slippage_cost=round(abs(price - open_price) * volume, 4),
                status="filled" if volume >= order.requested_volume else "partially_filled",
                reason=reason,
            )
        )
        if volume < order.requested_volume:
            rejects.append(
                RejectedOrder(
                    execution_date=order.execution_date,
                    symbol=order.symbol,
                    side=order.side,
                    requested_volume=order.requested_volume - volume,
                    filled_volume=volume,
                    reason=reason,
                )
            )

    return ExecutionResult(fills=tuple(fills), rejects=tuple(rejects))


def price_limits(
    symbol: str,
    bar: Mapping[str, Any],
    status: Mapping[str, Any],
    limits: LimitRules,
) -> tuple[float | None, float | None]:
    """Return (limit_down, limit_up) from the previous close and board rules.

    Without a previous close or an explicit limit price the band is unknown, and
    an unknown band is reported as ``(None, None)`` instead of being derived
    from the same day's close.
    """
    explicit_down = _number(bar.get("limit_down"))
    explicit_up = _number(bar.get("limit_up"))
    if explicit_down is not None or explicit_up is not None:
        return explicit_down, explicit_up

    previous_close = _number(bar.get("pre_close"))
    if previous_close is None or previous_close <= 0:
        return None, None
    percent = board_limit_pct(symbol, status, limits)
    return (
        round(previous_close * (1.0 - percent), 2),
        round(previous_close * (1.0 + percent), 2),
    )


def board_limit_pct(
    symbol: str,
    status: Mapping[str, Any],
    limits: LimitRules,
) -> float:
    """Resolve the daily price-limit percentage for one security."""
    if status.get("is_st") == 1:
        return limits.st_pct
    _, _, exchange = symbol.partition(".")
    code = symbol.split(".", 1)[0]
    if exchange == "BJ":
        return limits.bse_pct
    if code.startswith(("300", "301", "688")):
        return limits.growth_board_pct
    return limits.main_board_pct


def trade_fee(
    volume: int,
    price: float,
    side: str,
    rules: ExecutionRules,
) -> float:
    """Commission, transfer fee, and (on sells) stamp tax in yuan."""
    gross = volume * price
    if gross <= 0:
        return 0.0
    commission = max(gross * rules.commission_rate, rules.min_commission)
    transfer = gross * rules.transfer_fee
    stamp = gross * rules.stamp_tax_sell if side == SIDE_SELL else 0.0
    return commission + transfer + stamp


def max_affordable_shares(
    cash: float,
    price: float,
    rules: ExecutionRules,
) -> int:
    """Largest lot-aligned buy volume whose shares plus fees fit in cash.

    Order sizing and order matching share this function so a planned buy can
    always be funded, including the minimum commission and transfer fee.
    """
    lot = rules.lot_size
    if cash <= 0 or price <= 0 or lot <= 0:
        return 0
    volume = int(cash / price) // lot * lot
    while volume > 0:
        if volume * price + trade_fee(volume, price, SIDE_BUY, rules) <= cash:
            return volume
        volume -= lot
    return 0


def _execution_price(open_price: float, side: str, rules: ExecutionRules) -> float:
    slippage = rules.slippage_bps / 10000.0
    if side == SIDE_BUY:
        return open_price * (1.0 + slippage)
    return open_price * (1.0 - slippage)


def _participant_capacity(
    bar: Mapping[str, Any],
    rules: ExecutionRules,
) -> int:
    volume = _number(bar.get("volume"))
    if volume is None or volume <= 0:
        return 0
    shares = int(math.floor(volume * rules.max_participation_rate))
    return shares // rules.lot_size * rules.lot_size


def _is_suspended(bar: Mapping[str, Any], status: Mapping[str, Any]) -> bool:
    if bar.get("trade_status") == 0 or status.get("trade_status") == 0:
        return True
    return status.get("is_suspended") == 1


def _reject(order: OrderIntent, filled_volume: int, reason: str) -> RejectedOrder:
    return RejectedOrder(
        execution_date=order.execution_date,
        symbol=order.symbol,
        side=order.side,
        requested_volume=order.requested_volume,
        filled_volume=filled_volume,
        reason=reason,
    )


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
