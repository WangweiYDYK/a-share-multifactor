"""Simulated account, order intents, and A-share day-line matching."""

from ashare_multifactor.execution.account import ACCOUNT_VERSION, Account, Position
from ashare_multifactor.execution.orders import (
    ORDERS_VERSION,
    OrderPlan,
    SkippedOrder,
    create_order_intents,
)
from ashare_multifactor.execution.simulator import (
    EXECUTION_VERSION,
    ExecutionResult,
    ExecutionRules,
    Fill,
    LimitRules,
    OrderIntent,
    RejectedOrder,
    board_limit_pct,
    execute_orders,
    max_affordable_shares,
    price_limits,
    trade_fee,
)

__all__ = [
    "ACCOUNT_VERSION",
    "EXECUTION_VERSION",
    "ORDERS_VERSION",
    "Account",
    "ExecutionResult",
    "ExecutionRules",
    "Fill",
    "LimitRules",
    "OrderIntent",
    "OrderPlan",
    "Position",
    "RejectedOrder",
    "SkippedOrder",
    "board_limit_pct",
    "create_order_intents",
    "execute_orders",
    "max_affordable_shares",
    "price_limits",
    "trade_fee",
]
