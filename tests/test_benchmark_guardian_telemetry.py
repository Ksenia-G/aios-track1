"""Агрегация Guardian в бенчмарке обязана отражать реально выполненные проверки.

Наблюдавшийся дефект: кандидаты действительно проходили Guardian
(total_checks=82, passed=81, failed=1) и корректно отклонялись как
GUARDIAN_FAILED, но итог варианта сообщал guardian_check_count=0 и
guardian_failed_count=0.

Две независимые причины, обе в `run_mas_variant`:

* счётчик проверок спрашивал `isinstance(record.guardian, dict)`, тогда как
  `record.guardian` -- это `GuardianVerdict` (dict лежит внутри, в `.report`).
  Условие ложно всегда, поэтому сумма всегда 0;
* счётчик отказов считал только кандидатов, совпавших с ЗАКОММИЧЕННЫМ, а
  проваливший Guardian кандидат закоммичен быть не может -- значит, 0 всегда.

Семантика счётчиков сохранена по существующему контракту опорных вариантов
(`adapters.py`: guardian_checks = report.summary.total_checks): они описывают
ПРИНЯТУЮ политику. Поиск-широкая телеметрия живёт в отдельных полях, чтобы
одно поле не значило двух вещей.
"""

from __future__ import annotations

import pytest

from aios.benchmark.mas_variant import guardian_telemetry
from aios.mas.orchestration.boundaries import GuardianVerdict
from aios.mas.orchestration.orchestrator import CandidateRecord


def _report(total: int, failed: int) -> dict:
    return {
        "summary": {
            "total_checks": total,
            "passed": total - failed,
            "failed": failed,
            "near_binding": 0,
        }
    }


def _record(digest: str, *, total: int | None, failed: int = 0, kind: str = "AGENT_BUNDLE"):
    verdict = None
    if total is not None:
        verdict = GuardianVerdict(
            passed=failed == 0,
            violations=tuple(f"w{i}:c" for i in range(failed)),
            report=_report(total, failed),
        )
    return CandidateRecord(
        candidate_digest=digest,
        candidate_kind=kind,
        simulation_status="SUCCESS" if total is not None else "CRASHED",
        guardian=verdict,
        npv_mln_rub=1000.0 if (total is not None and failed == 0) else None,
        ineligibility_reason=None if failed == 0 else "GUARDIAN_FAILED",
    )


class _Step:
    """Минимальный носитель того, что читает агрегация."""

    def __init__(self, candidates, committed):
        self.candidates = tuple(candidates)
        self.committed_candidate_digest = committed


# --- TEST 1: успешные проверки агрегируются --------------------------------


def test_passing_candidates_contribute_their_checks() -> None:
    step = _Step([_record("A", total=82), _record("B", total=82)], committed="A")
    t = guardian_telemetry((step,))
    assert t.checks_total == 164
    assert t.failed_total == 0
    assert t.candidates_evaluated == 2
    assert t.candidates_rejected == 0
    # Контрактные поля описывают ПРИНЯТУЮ политику.
    assert t.committed_checks == 82
    assert t.committed_failed == 0


# --- TEST 2: провалившие проверки агрегируются -----------------------------


def test_failed_checks_are_counted_across_all_candidates() -> None:
    step = _Step([_record("A", total=82), _record("B", total=82, failed=1)], committed="A")
    t = guardian_telemetry((step,))
    assert t.checks_total == 164
    assert t.failed_total == 1
    assert t.candidates_rejected == 1
    assert t.committed_checks == 82
    assert t.committed_failed == 0


# --- TEST 3: отклонённый кандидат не исчезает из телеметрии ----------------


def test_guardian_rejected_candidate_still_contributes_its_checks() -> None:
    """Ровно наблюдавшийся случай: отказ был, а телеметрия молчала."""
    step = _Step([_record("A", total=82), _record("B", total=82, failed=1)], committed="A")
    t = guardian_telemetry((step,))
    assert t.checks_total == 164, "rejected candidate's executed checks must be visible"
    assert t.failed_total == 1


# --- TEST 4: не дошедший до Guardian кандидат не выдумывает проверок -------


def test_candidate_that_never_reached_guardian_contributes_nothing() -> None:
    step = _Step([_record("A", total=82), _record("B", total=None)], committed="A")
    t = guardian_telemetry((step,))
    assert t.checks_total == 82
    assert t.candidates_evaluated == 1
    assert t.failed_total == 0


# --- TEST 5: многошаговая агрегация без задвоения --------------------------


def test_multi_step_aggregation_sums_each_step_once() -> None:
    s1 = _Step([_record("A", total=82), _record("B", total=82, failed=1)], committed="A")
    s2 = _Step([_record("C", total=82), _record("D", total=82)], committed="C")
    s3 = _Step([_record("E", total=82)], committed="E")
    t = guardian_telemetry((s1, s2, s3))
    assert t.checks_total == 82 * 5
    assert t.failed_total == 1
    assert t.candidates_evaluated == 5
    assert t.candidates_rejected == 1
    # Принятая политика: по одному закоммиченному кандидату на шаг.
    assert t.committed_checks == 82 * 3
    assert t.committed_failed == 0


def test_empty_run_reports_zero_without_crashing() -> None:
    t = guardian_telemetry(())
    assert (t.checks_total, t.failed_total, t.candidates_evaluated) == (0, 0, 0)


# --- TEST 6: значения переживают сериализацию ------------------------------


def _benchmark_outcome(**counters):
    from aios.benchmark.contracts import (
        BENCHMARK_SCHEMA_VERSION,
        BenchmarkOutcome,
        BenchmarkRunManifest,
        CognitiveConfig,
        CompensationPolicy,
        EvaluationLineage,
        PhysicalFrame,
        RunKind,
        VariantFamily,
    )

    frame = PhysicalFrame(
        case_id="c",
        case_digest="d",
        deck_reference="r",
        deck_digest=None,
        simulator_identity="img",
        effective_start="2014-01-01",
        effective_end="2015-12-01",
        chdd_calculator_version="v",
        chdd_month_count=None,
        guardian_version="g",
        compensation_policy=CompensationPolicy.OFFICIAL_UNKNOWN_PORT,
        initial_state_digest="s",
        runtime_mode="FULL",
    )
    manifest = BenchmarkRunManifest(
        benchmark_schema_version=BENCHMARK_SCHEMA_VERSION,
        variant_id="B4_MAS_ON",
        variant_family=VariantFamily.MAS_ON,
        run_kind=RunKind.CLOSED_LOOP_POLICY,
        code_sha="deadbeef",
        frame=frame,
        cognitive=CognitiveConfig(llm_enabled=True, model_id="m", provider_id="p"),
        lineage=(EvaluationLineage(schedule_digest="sched"),),
    )
    return BenchmarkOutcome(manifest=manifest, npv_mln_rub=1000.0, guardian_pass=True, **counters)


def test_counters_survive_serialization_into_the_summary() -> None:
    outcome = _benchmark_outcome(
        guardian_check_count=246,
        guardian_checks_total=410,
        guardian_failed_total=1,
        guardian_candidates_evaluated=5,
        guardian_candidates_rejected=1,
    )
    payload = outcome.to_json()
    assert payload["guardian_check_count"] == 246
    assert payload["guardian_checks_total"] == 410
    assert payload["guardian_failed_total"] == 1
    assert payload["guardian_candidates_evaluated"] == 5
    assert payload["guardian_candidates_rejected"] == 1


def test_outcome_contract_still_rejects_passing_verdict_with_failed_committed_checks() -> None:
    """Контракт, из-за которого контрактные счётчики описывают принятую политику.

    Всекандидатная семантика для `guardian_failed_count` несовместима с ним:
    отклонённый Guardian кандидат дал бы failed>0 при законной принятой
    политике, и сборка результата упала бы вместо того, чтобы отчитаться.
    """
    from aios.benchmark.contracts import BenchmarkError

    with pytest.raises(BenchmarkError):
        _benchmark_outcome(guardian_failed_count=3)


def test_search_wide_failures_do_not_break_a_passing_committed_policy() -> None:
    """Отказы среди ОТКЛОНЁННЫХ кандидатов законны при принятой политике."""
    outcome = _benchmark_outcome(guardian_failed_total=4, guardian_candidates_rejected=4)
    assert outcome.guardian_pass is True
    assert outcome.guardian_failed_count == 0
    assert outcome.guardian_failed_total == 4
