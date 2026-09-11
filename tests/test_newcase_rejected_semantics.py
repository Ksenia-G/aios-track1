"""Семантика, которую наша архитектура принимать не должна.

Эти тесты описывают решения, а не запрещают чужой код: каждый из них ловит
конкретную ошибку, в которую легко скатиться под таймером.
"""

from __future__ import annotations

import pathlib
from datetime import date

import pytest

from aios.newcase.calendar import REPAIRS, WellAvailabilityCalendar
from aios.newcase.capacity import FieldCapacity, project_to_capacity
from aios.newcase.guardian import FieldTrajectoryGuardian
from aios.newcase.policy import build_monthly_schedule
from aios.newcase.signature import ModelSignature, model_signature
from aios.schedule.events import WellStatus

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "aios"


# --- 1. доступность не выражается нулевой уставкой --------------------------


def test_availability_is_never_expressed_as_a_zero_setpoint() -> None:
    """Закрытая скважина не получает управляющей строки с нулём."""
    schedule = build_monthly_schedule(
        producers={"40": 100.0, "1": 100.0},
        injectors={"26": 200.0},
        merit={},
        calendar=WellAvailabilityCalendar(),
    )

    for step in schedule.steps:
        for control in step.producers:
            assert control.status is WellStatus.OPEN
            assert control.liquid_rate not in (0, 0.0)
        for control in step.injectors:
            assert control.status is WellStatus.OPEN
            assert control.surface_rate not in (0, 0.0)


def test_shut_is_expressed_with_welopen_and_carries_no_number() -> None:
    schedule = build_monthly_schedule(
        producers={"40": 100.0}, injectors={}, merit={}, calendar=WellAvailabilityCalendar()
    )
    step = next(s for s in schedule.steps if s.date == date(2013, 4, 1))

    assert step.shut_wells == ("40",)
    assert all(c.well != "40" for c in step.producers)


# --- 2. порог компенсации не выдумывается -----------------------------------


def test_no_compensation_corridor_is_ever_populated_with_a_literal() -> None:
    """Коридор компенсации нигде не заполняется числом.

    Проверяется именно присваивание границ коридора, а не любое появление
    похожих чисел: доли перераспределения в исторических экспериментах к
    компенсации отношения не имеют.
    """
    import ast

    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.keyword)
                and node.arg in ("corridor_min", "corridor_max")
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, (int, float))
            ):
                offenders.append((path.name, node.arg, node.value.value))
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    name = getattr(target, "attr", None) or getattr(target, "id", None)
                    if (
                        name in ("corridor_min", "corridor_max")
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, (int, float))
                    ):
                        offenders.append((path.name, name, node.value.value))

    assert offenders == []


def test_the_new_case_declares_compensation_undefined(newcase_window_case) -> None:
    spec = newcase_window_case.constraints.compensation

    assert spec.corridor_min is None
    assert spec.corridor_max is None


# --- 3. пропорциональное урезание не является политикой ---------------------


def test_scarce_capacity_is_not_spread_proportionally() -> None:
    requested = {"oil": 2000.0, "water": 2000.0}
    merit = {"oil": 0.9, "water": 0.05}

    projected = project_to_capacity(requested, cap=2000.0, merit=merit)

    assert projected["oil"] == 2000.0
    assert projected["water"] == 0.0
    assert projected != pytest.approx({"oil": 1000.0, "water": 1000.0})


# --- 4. предел не переживает 2015 -------------------------------------------


def test_the_pre_2015_cap_does_not_leak_into_2015_operation() -> None:
    report = FieldTrajectoryGuardian().evaluate(
        [(date(2015, 1, 1), 100.0, 100.0), (date(2015, 2, 1), 9000.0, 9000.0)]
    )

    assert report.checks == 0
    assert report.failed == 0


def test_capacity_for_a_2015_control_date_is_unbounded() -> None:
    capacity = FieldCapacity.at(date(2015, 1, 1))

    assert capacity.liquid_max is None
    assert capacity.injection_max is None


# --- 5. старая команда не переживает возврат --------------------------------


@pytest.mark.parametrize("repair", REPAIRS, ids=lambda r: r.well)
def test_no_stale_pre_repair_command_survives_into_the_repair(repair) -> None:
    schedule = build_monthly_schedule(
        producers={r.well: 100.0 for r in REPAIRS},
        injectors={},
        merit={},
        calendar=WellAvailabilityCalendar(),
    )

    for step in schedule.steps:
        if repair.shut <= step.date < repair.back:
            assert all(c.well != repair.well for c in step.producers)


# --- 6. сигнатура модели не становится ограничением -------------------------


#: Прогон обязан сигнатуру ПОКАЗАТЬ -- это его работа. Запрещено другое:
#: чтобы по ней принимались решения.
SIGNATURE_REPORTERS = {"newcase/runner.py"}


def test_the_model_signature_is_not_consumed_by_any_decision_layer() -> None:
    """Сигнатуру можно печатать, но нельзя превращать в ограничение."""
    importers = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "newcase.signature" in path.read_text(encoding="utf-8") and path.name != "signature.py"
    }

    assert importers <= SIGNATURE_REPORTERS
    for layer in ("agents", "chdd", "mas", "evaluation"):
        assert not any(name.startswith(f"{layer}/") for name in importers)


def test_a_mismatching_signature_rejects_nothing() -> None:
    mismatch = model_signature(liquid=3260.0, injection=2740.0)
    report = FieldTrajectoryGuardian().evaluate(
        [(date(2013, 5, 1), 100.0, 100.0), (date(2013, 6, 1), 100.0, 100.0)]
    )

    assert mismatch.verdict is ModelSignature.MISMATCH
    assert report.failed == 0


# --- 7. текст модели не становится числовой властью -------------------------


def test_no_setpoint_is_ever_read_out_of_free_text() -> None:
    """Ни один слой не разбирает числа из rationale/обоснования."""
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("float(rationale", "float(intent.rationale", "parse_rate(rationale"):
            if needle in text:
                offenders.append(path.name)

    assert offenders == []
