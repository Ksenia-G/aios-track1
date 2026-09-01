"""Воспроизводимый бенчмарк Hybrid MAS v1.1.

Задача пакета -- измерить экономический эффект MAS против управленческой
базы и исследовательских кандидатов при одинаковой физической и экономической
рамке, и не позволить таблице лидеров скрыть несопоставимость.
"""

from aios.benchmark.contracts import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkComparison,
    BenchmarkError,
    BenchmarkOutcome,
    BenchmarkRunManifest,
    CognitiveConfig,
    ComparisonClass,
    CompensationPolicy,
    EvaluationLineage,
    PhysicalFrame,
    RunKind,
    VariantFamily,
)
from aios.benchmark.fairness import classify, frame_differences, may_claim_win
from aios.benchmark.provider import ProviderAvailability, detect_provider
from aios.benchmark.scoreboard import RepeatStatistics, Scoreboard, aggregate_repeats, delta

__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "BenchmarkComparison",
    "BenchmarkError",
    "BenchmarkOutcome",
    "BenchmarkRunManifest",
    "CognitiveConfig",
    "ComparisonClass",
    "CompensationPolicy",
    "EvaluationLineage",
    "PhysicalFrame",
    "ProviderAvailability",
    "RepeatStatistics",
    "RunKind",
    "Scoreboard",
    "VariantFamily",
    "aggregate_repeats",
    "classify",
    "delta",
    "detect_provider",
    "frame_differences",
    "may_claim_win",
]
