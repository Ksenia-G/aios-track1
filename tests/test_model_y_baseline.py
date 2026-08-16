"""Регрессия по опорному кейсу Model Y.

Тесты работают только там, где доступна лабораторная колода, и пропускаются
в остальных окружениях. Симулятор здесь не запускается: проверяется та часть
опорного прогона, которая детерминирована без Docker, -- восстановленное
состояние и байты сгенерированного расписания.

Полная сверка NPV делается отдельным прогоном `mise run validation-demo`.
"""

from pathlib import Path

import pytest

from aios.cases import CaseError, load_case
from aios.pipeline import build_schedule_for_case
from aios.schedule import schedule_sha256, validate_schedule
from aios.schedule.policy import DEFAULT_POLICY_ID

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = REPO_ROOT / "cases" / "model_y_validation.json"

# Опорный SHA сгенерированного wells_schedule.inc. Изменение этого числа
# означает, что изменились байты сабмишена, и требует объяснения.
BASELINE_SCHEDULE_SHA256 = "7662d10495796ace6b0d20df404ca68932b2894f367de2062c232cbe3e01c399"
BASELINE_NPV_MLN_RUB = 1030.877696


def _case_or_skip():
    if not CASE_PATH.is_file():
        pytest.skip("Model Y case file is not present")
    try:
        case = load_case(CASE_PATH)
    except CaseError as exc:
        # Внешние материалы не настроены -- это заявленное состояние клона
        # без Model Y, а не поломка.
        if "environment variable" not in str(exc):
            raise
        pytest.skip(f"external resources are not configured: {exc}")
    if not case.history.schedule_path.is_file():
        pytest.skip("Model Y deck is not available in this environment")
    return case


def test_baseline_schedule_sha_is_unchanged() -> None:
    case = _case_or_skip()

    _schedule, _state, text, _history = build_schedule_for_case(case, policy_id=DEFAULT_POLICY_ID)

    assert schedule_sha256(text) == BASELINE_SCHEDULE_SHA256


def test_baseline_schedule_passes_every_gate() -> None:
    case = _case_or_skip()

    schedule, state, _text, _history = build_schedule_for_case(case, policy_id=DEFAULT_POLICY_ID)
    report = validate_schedule(schedule, state, case)

    assert report.is_valid, [gate.gate_id for gate in report.failed]


def test_baseline_state_matches_the_known_field() -> None:
    case = _case_or_skip()

    schedule, state, _text, _history = build_schedule_for_case(case, policy_id=DEFAULT_POLICY_ID)

    assert len(state.known_wells) == 49
    assert len(state.producers) == 33
    assert len(state.injectors) == 16
    assert len(schedule.steps) == 24


def test_case_carries_the_baseline_reconciliation_target() -> None:
    case = _case_or_skip()

    assert case.acceptance is not None
    assert case.acceptance.baseline_npv_mln_rub == pytest.approx(BASELINE_NPV_MLN_RUB)
    assert case.acceptance.npv_tolerance_mln_rub == pytest.approx(1e-6)
    assert case.economics.provenance == "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"
