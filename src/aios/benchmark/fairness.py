"""Классификация честности сравнения двух вариантов.

Таблица лидеров без этой проверки бесполезна: два числа, полученные на разной
колоде, в разном горизонте или под разной версией ЧДД, выглядят сравнимыми и
не являются таковыми. Поэтому класс сравнения вычисляется из рамок прогонов, а
не назначается автором отчёта.
"""

from __future__ import annotations

from aios.benchmark.contracts import (
    BenchmarkComparison,
    BenchmarkOutcome,
    ComparisonClass,
    PhysicalFrame,
    RunKind,
)

#: Расхождения, которые нельзя нормализовать: число означает другое явление.
_FATAL_KEYS = (
    "case_id",
    "case_digest",
    "deck_reference",
    "effective_start",
    "effective_end",
    "chdd_calculator_version",
    "compensation_policy",
)

#: Расхождения, при которых сравнение ещё осмысленно, но требует оговорки.
_NORMALIZABLE_KEYS = (
    "simulator_identity",
    "initial_state_digest",
    "guardian_version",
    "deck_digest",
)


def frame_differences(left: PhysicalFrame, right: PhysicalFrame) -> tuple[str, ...]:
    """Имена полей рамки, в которых прогоны расходятся."""
    differences: list[str] = []
    for key in (*_FATAL_KEYS, *_NORMALIZABLE_KEYS):
        if getattr(left, key) != getattr(right, key):
            differences.append(key)
    return tuple(differences)


def classify(left: BenchmarkOutcome, right: BenchmarkOutcome) -> BenchmarkComparison:
    """Определить, насколько честно сравнивать два результата.

    Разный род прогона (фиксированное расписание против замкнутого контура) сам
    по себе не делает сравнение недействительным -- именно это мы и хотим
    измерить, -- но и прямым его назвать нельзя: механизмы разные, и об этом
    обязана говорить сама таблица.
    """
    reasons: list[str] = []
    differences = frame_differences(left.manifest.frame, right.manifest.frame)

    fatal = [key for key in differences if key in _FATAL_KEYS]
    normalizable = [key for key in differences if key in _NORMALIZABLE_KEYS]

    if fatal:
        reasons.append("incomparable physical or economic frame: " + ", ".join(sorted(fatal)))
        return BenchmarkComparison(
            left_variant_id=left.variant_id,
            right_variant_id=right.variant_id,
            comparison_class=ComparisonClass.NOT_COMPARABLE,
            reasons=tuple(reasons),
        )

    historical = {RunKind.HISTORICAL_REFERENCE}
    if left.manifest.run_kind in historical or right.manifest.run_kind in historical:
        reasons.append(
            "at least one side is an ingested historical record rather than a run "
            "reproduced under this frame"
        )
        return _with_delta(left, right, ComparisonClass.REFERENCE_ONLY, tuple(reasons))

    if normalizable:
        reasons.append("frame differs in normalisable fields: " + ", ".join(sorted(normalizable)))

    if left.manifest.run_kind is not right.manifest.run_kind:
        reasons.append(
            f"different run kinds ({left.manifest.run_kind!s} vs "
            f"{right.manifest.run_kind!s}): a fixed schedule is evaluated once over the "
            "horizon while a closed loop decides step by step and produces its own"
        )

    comparison_class = (
        ComparisonClass.DIRECT_COMPARABLE if not reasons else ComparisonClass.NORMALIZED_COMPARABLE
    )
    return _with_delta(left, right, comparison_class, tuple(reasons))


def _with_delta(
    left: BenchmarkOutcome,
    right: BenchmarkOutcome,
    comparison_class: ComparisonClass,
    reasons: tuple[str, ...],
) -> BenchmarkComparison:
    delta = left.npv_mln_rub - right.npv_mln_rub
    pct = (delta / abs(right.npv_mln_rub) * 100.0) if right.npv_mln_rub else None
    return BenchmarkComparison(
        left_variant_id=left.variant_id,
        right_variant_id=right.variant_id,
        comparison_class=comparison_class,
        reasons=reasons,
        delta_npv_mln_rub=delta,
        delta_pct=pct,
    )


def may_claim_win(comparison: BenchmarkComparison, left: BenchmarkOutcome) -> bool:
    """Разрешено ли объявлять победу на этом сравнении.

    Требуется и честный класс сравнения, и легальный кандидат: NPV, полученный
    с нарушением официальных ограничений, победой не является.
    """
    return comparison.claimable and left.rankable


__all__ = ["classify", "frame_differences", "may_claim_win"]
