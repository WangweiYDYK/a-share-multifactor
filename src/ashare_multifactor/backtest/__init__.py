"""Monthly multifactor backtest: configuration, engine, and fixtures."""

from ashare_multifactor.backtest.config import CONFIG_VERSION, BacktestConfig
from ashare_multifactor.backtest.engine import (
    ENGINE_VERSION,
    BacktestError,
    BacktestResult,
    run_backtest,
    write_run_artifacts,
)
from ashare_multifactor.backtest.synthetic_market import (
    SYNTHETIC_VERSION,
    generate_synthetic_market,
    synthetic_data_version,
)

__all__ = [
    "CONFIG_VERSION",
    "ENGINE_VERSION",
    "SYNTHETIC_VERSION",
    "BacktestConfig",
    "BacktestError",
    "BacktestResult",
    "generate_synthetic_market",
    "run_backtest",
    "synthetic_data_version",
    "write_run_artifacts",
]
