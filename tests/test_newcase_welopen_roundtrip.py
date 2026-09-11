"""Сгенерированное расписание нового кейса должно читаться обратно.

Артефакт, который нельзя перечитать каноническим парсером, нечем проверить:
Guardian пришлось бы кормить объектом из памяти, а не тем, что реально ушло в
колоду. Поэтому WELOPEN обязан разбираться так же, как управляющие ключевые
слова -- и при этом не приносить ни одной величины.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aios.newcase.calendar import REPAIRS, WellAvailabilityCalendar
from aios.newcase.policy import build_monthly_schedule
from aios.schedule.deck import parse_schedule_file
from aios.schedule.generator import render_full_schedule, render_schedule

HISTORY_START = date(2007, 5, 1)


@pytest.fixture
def case(newcase_window_case):
    """Окно нового кейса на синтетической колоде: внешние материалы не нужны."""
    return newcase_window_case


@pytest.fixture
def schedule():
    return build_monthly_schedule(
        producers={"1": 100.0, "40": 100.0, "30": 100.0},
        injectors={"26": 200.0, "2": 150.0},
        merit={},
        calendar=WellAvailabilityCalendar(),
    )


def _reparse(tmp_path: Path, case, schedule, history_text: str = "-- history\n"):
    text = render_full_schedule(
        schedule,
        case,
        history_text=history_text,
        policy_id="roundtrip",
        history_start_date=HISTORY_START,
    )
    path = tmp_path / "wells_schedule.inc"
    path.write_text(text, encoding="utf-8")
    deck = parse_schedule_file(path, start_date=HISTORY_START)
    return deck.control_schedule(case.management_start_date, case.management_end_date)


# --- WELOPEN разбирается -----------------------------------------------------


def test_welopen_shut_parses(tmp_path: Path, case, schedule) -> None:
    back = _reparse(tmp_path, case, schedule)
    step = next(s for s in back.steps if s.date == date(2013, 4, 1))

    assert "40" in step.shut_wells


def test_welopen_open_parses(tmp_path: Path, case, schedule) -> None:
    back = _reparse(tmp_path, case, schedule)
    step = next(s for s in back.steps if s.date == date(2013, 8, 1))

    assert "40" in step.opened_wells


def test_mixed_wconprod_wconinje_and_welopen_parse_together(tmp_path: Path, case, schedule) -> None:
    back = _reparse(tmp_path, case, schedule)
    step = next(s for s in back.steps if s.date == date(2013, 4, 1))

    assert step.shut_wells == ("40",)
    assert {c.well for c in step.producers} == {"1", "30"}
    assert {c.well for c in step.injectors} == {"2", "26"}


def test_welopen_never_carries_a_setpoint(tmp_path: Path, case, schedule) -> None:
    """WELOPEN выражает доступность; никакой величины из него взяться не может."""
    back = _reparse(tmp_path, case, schedule)

    for step in back.steps:
        for well in (*step.shut_wells, *step.opened_wells):
            assert all(c.well != well or c.liquid_rate is not None for c in step.producers)
            assert all(c.well != well or c.surface_rate is not None for c in step.injectors)


# --- round-trip сохраняет переходы ремонта ----------------------------------


def test_roundtrip_preserves_every_repair_transition(tmp_path: Path, case, schedule) -> None:
    managed = {"1", "40", "30", "26", "2"}
    back = _reparse(tmp_path, case, schedule)
    by_date = {s.date: s for s in back.steps}

    for repair in REPAIRS:
        if repair.well not in managed:
            continue
        assert repair.well in by_date[repair.shut].shut_wells, repair
        assert repair.well in by_date[repair.back].opened_wells, repair


def test_roundtrip_reproduces_the_same_schedule_text(tmp_path: Path, case, schedule) -> None:
    first = render_schedule(schedule, case, policy_id="roundtrip")
    back = _reparse(tmp_path, case, schedule)
    second = render_schedule(back, case, policy_id="roundtrip")

    assert second == first


# --- старые расписания без WELOPEN не меняются -------------------------------


def test_a_schedule_without_welopen_is_unaffected(tmp_path: Path) -> None:
    """Опубликованный артефакт KT3 читается ровно как раньше."""
    source = Path("submission_evidence/wells_schedule.inc")
    deck = parse_schedule_file(source, start_date=HISTORY_START)
    window = deck.control_schedule(date(2014, 1, 1), date(2015, 12, 1))

    assert len(window.steps) == 24
    assert all(step.shut_wells == () and step.opened_wells == () for step in window.steps)
