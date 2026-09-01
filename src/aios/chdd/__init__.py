"""Адаптер официального калькулятора ЧДД."""

from aios.chdd.runner import (
    ANNUAL_CUMULATIVE_KEYS,
    ANNUAL_NPV_KEYS,
    AnnualChddRow,
    ChddError,
    ChddResult,
    ChddResultSchemaError,
    parse_chdd_result,
    run_chdd,
)
from aios.chdd.trajectory import (
    CHDD_COLUMNS,
    ChddInput,
    TrajectoryError,
    TrajectoryStats,
    build_chdd_input,
    write_chdd_csv,
)

__all__ = [
    "ANNUAL_CUMULATIVE_KEYS",
    "ANNUAL_NPV_KEYS",
    "CHDD_COLUMNS",
    "AnnualChddRow",
    "ChddError",
    "ChddInput",
    "ChddResult",
    "ChddResultSchemaError",
    "TrajectoryError",
    "TrajectoryStats",
    "build_chdd_input",
    "parse_chdd_result",
    "run_chdd",
    "write_chdd_csv",
]
