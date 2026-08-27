"""Сводка бенчмарка: агрегация повторов, дельты, таблица честности.

Медиана, а не лучший прогон, служит основным числом для когнитивной ветки:
показывать только удачный запуск LLM -- это отчёт о везении, а не о качестве.
Лучший прогон приводится отдельно и подписан как лучший.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from aios.benchmark.contracts import (
    BenchmarkError,
    BenchmarkOutcome,
    ComparisonClass,
    VariantFamily,
)
from aios.benchmark.fairness import classify, may_claim_win
from aios.evaluation.canonical import JsonValue


@dataclass(frozen=True, slots=True)
class RepeatStatistics:
    """Разброс повторов одного варианта.

    Три-пять прогонов не дают статистической значимости; это свидетельство
    повторяемости, и называется оно именно так.
    """

    variant_id: str
    count: int
    median_npv: float
    mean_npv: float
    min_npv: float
    max_npv: float
    stdev_npv: float | None
    best_run_id: str | None
    worst_run_id: str | None
    guardian_failures: int
    evidence_strength: str = "repeatability_evidence_not_significance"

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "variant_id": self.variant_id,
            "count": self.count,
            "median_npv_mln_rub": self.median_npv,
            "mean_npv_mln_rub": self.mean_npv,
            "min_npv_mln_rub": self.min_npv,
            "max_npv_mln_rub": self.max_npv,
            "stdev_npv_mln_rub": self.stdev_npv,
            "best_run_id": self.best_run_id,
            "worst_run_id": self.worst_run_id,
            "guardian_failures": self.guardian_failures,
            "evidence_strength": self.evidence_strength,
        }


def aggregate_repeats(outcomes: Sequence[BenchmarkOutcome]) -> RepeatStatistics:
    """Свести повторы одного варианта в описательную статистику."""
    if not outcomes:
        raise BenchmarkError("aggregate_repeats: at least one outcome is required")
    variant_ids = {outcome.variant_id for outcome in outcomes}
    if len(variant_ids) != 1:
        raise BenchmarkError(
            f"aggregate_repeats: outcomes belong to different variants {sorted(variant_ids)}"
        )
    values = [outcome.npv_mln_rub for outcome in outcomes]
    ordered = sorted(outcomes, key=lambda item: (item.npv_mln_rub, item.variant_id))
    return RepeatStatistics(
        variant_id=next(iter(variant_ids)),
        count=len(outcomes),
        median_npv=statistics.median(values),
        mean_npv=statistics.fmean(values),
        min_npv=min(values),
        max_npv=max(values),
        stdev_npv=statistics.stdev(values) if len(values) > 1 else None,
        best_run_id=ordered[-1].manifest.cognitive.run_id,
        worst_run_id=ordered[0].manifest.cognitive.run_id,
        guardian_failures=sum(0 if outcome.guardian_pass else 1 for outcome in outcomes),
    )


@dataclass(frozen=True, slots=True)
class Scoreboard:
    """Итог одного прогона бенчмарка."""

    outcomes: tuple[BenchmarkOutcome, ...]
    reference_variant_id: str

    def _by_id(self) -> dict[str, BenchmarkOutcome]:
        return {outcome.variant_id: outcome for outcome in self.outcomes}

    @property
    def reference(self) -> BenchmarkOutcome:
        try:
            return self._by_id()[self.reference_variant_id]
        except KeyError:
            raise BenchmarkError(
                f"scoreboard: reference variant {self.reference_variant_id!r} is absent"
            ) from None

    def fairness_matrix(self) -> dict[str, dict[str, str]]:
        """Класс сравнения каждого варианта с каждым."""
        matrix: dict[str, dict[str, str]] = {}
        for left in self.outcomes:
            row: dict[str, str] = {}
            for right in self.outcomes:
                if left.variant_id == right.variant_id:
                    row[right.variant_id] = "SELF"
                    continue
                row[right.variant_id] = str(classify(left, right).comparison_class)
            matrix[left.variant_id] = row
        return matrix

    def comparisons_against_reference(self) -> tuple[dict[str, JsonValue], ...]:
        reference = self.reference
        rows: list[dict[str, JsonValue]] = []
        for outcome in self.outcomes:
            if outcome.variant_id == reference.variant_id:
                continue
            comparison = classify(outcome, reference)
            rows.append(
                {
                    **comparison.to_json(),
                    "win_claimable": may_claim_win(comparison, outcome),
                }
            )
        return tuple(rows)

    def headline_candidate(self) -> BenchmarkOutcome | None:
        """Сильнейший кандидат, которого вообще можно предъявлять.

        Требуется прошедший Guardian результат и честный класс сравнения с
        опорным вариантом: нарушивший ограничения или несопоставимый прогон
        заголовком не становится, каким бы ни был его NPV.
        """
        reference = self.reference
        eligible = [
            outcome
            for outcome in self.outcomes
            if outcome.rankable
            and (
                outcome.variant_id == reference.variant_id
                or may_claim_win(classify(outcome, reference), outcome)
            )
        ]
        if not eligible:
            return None
        return max(eligible, key=lambda item: (item.npv_mln_rub, item.variant_id))

    def to_json(self) -> dict[str, JsonValue]:
        reference = self.reference
        rows: list[JsonValue] = []
        for outcome in sorted(self.outcomes, key=lambda item: (-item.npv_mln_rub, item.variant_id)):
            comparison = (
                None if outcome.variant_id == reference.variant_id else classify(outcome, reference)
            )
            rows.append(
                {
                    "variant_id": outcome.variant_id,
                    "variant_family": str(outcome.family),
                    "run_kind": str(outcome.manifest.run_kind),
                    "npv_mln_rub": outcome.npv_mln_rub,
                    "delta_vs_reference_mln_rub": (
                        None if comparison is None else comparison.delta_npv_mln_rub
                    ),
                    "delta_vs_reference_pct": (
                        None if comparison is None else comparison.delta_pct
                    ),
                    "comparison_class": (
                        "SELF" if comparison is None else str(comparison.comparison_class)
                    ),
                    "win_claimable": (
                        None if comparison is None else may_claim_win(comparison, outcome)
                    ),
                    "guardian_pass": outcome.guardian_pass,
                    "guardian_failed_count": outcome.guardian_failed_count,
                    "simulation_count": outcome.simulation_count,
                    "control_change_count": outcome.control_change_count,
                    "decision_steps": outcome.decision_steps,
                    "fallback_count": outcome.fallback_count,
                    "llm_enabled": outcome.manifest.cognitive.llm_enabled,
                    "model_id": outcome.manifest.cognitive.model_id,
                    "notes": list(outcome.manifest.notes),
                }
            )
        headline = self.headline_candidate()
        return {
            "reference_variant_id": reference.variant_id,
            "rows": rows,
            "fairness_matrix": self.fairness_matrix(),
            "comparisons_vs_reference": list(self.comparisons_against_reference()),
            "headline_candidate": None if headline is None else headline.variant_id,
        }


def family_of(
    outcomes: Sequence[BenchmarkOutcome], family: VariantFamily
) -> tuple[BenchmarkOutcome, ...]:
    return tuple(outcome for outcome in outcomes if outcome.family is family)


def delta(left: BenchmarkOutcome, right: BenchmarkOutcome) -> dict[str, JsonValue]:
    """Дельта с сохранённым классом честности."""
    comparison = classify(left, right)
    return {
        **comparison.to_json(),
        "win_claimable": may_claim_win(comparison, left),
    }


__all__ = [
    "ComparisonClass",
    "RepeatStatistics",
    "Scoreboard",
    "aggregate_repeats",
    "delta",
    "family_of",
]
