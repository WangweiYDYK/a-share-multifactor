"""Factor definitions, cross-sectional preprocessing, and composite scoring."""

from ashare_multifactor.factors.composite import CompositeScore, composite_score
from ashare_multifactor.factors.definitions import (
    FACTOR_VERSION,
    FactorSpec,
    compute_raw_factor,
    compute_raw_factors,
    default_factor_specs,
)
from ashare_multifactor.factors.preprocess import (
    PREPROCESS_VERSION,
    ProcessedFactor,
    process_factor,
)

__all__ = [
    "CompositeScore",
    "FACTOR_VERSION",
    "PREPROCESS_VERSION",
    "FactorSpec",
    "ProcessedFactor",
    "composite_score",
    "compute_raw_factor",
    "compute_raw_factors",
    "default_factor_specs",
    "process_factor",
]
