"""SHUT_IN обязан превращаться в разбираемую запись WCONPROD.

Реальный отказ: MagnitudeResolver материализует SHUT_IN как
`status=SHUT, mode=LRAT, liquid_rate=None` (engine.py), а генератор писал
режим и значение безусловно, из-за чего получалось

    '1' 'SHUT' 'LRAT' 1* 1* 1* 1* 1* 1* 1* 1* /

и OPM отказывался разбирать колоду ещё до физики:

    Internal error: Trying to set CMODE to: LRAT -
    no value has been specified for this control

Проверено прямым dry-run прогоном настоящего OPM на настоящей колоде:
запись с ДЕФОЛТНЫМ полем режима принимается, с 'LRAT' без значения -- нет.
Правило симулятора: режим, управляющий по расходу, обязан иметь своё значение.

Числовое значение при этом не выдумывается: закрытая скважина остаётся
закрытой, а не «открытой с нулевым дебитом» -- иначе численный setpoint
появился бы мимо MagnitudeResolver.
"""

from __future__ import annotations

import pytest

from aios.schedule.events import InjectorControl, ProducerControl, WellStatus
from aios.schedule.generator import _injector_line, _producer_line
from aios.schedule.validator import (
    ControlRepresentationError,
    require_representable_controls,
)

# Форма, принятая настоящим OPM (dry-run PASS).
SHUT_PRODUCER_RECORD = " '1' 'SHUT' 1* 1* 1* 1* 1* 1* 1* 1* 1* /"
# Форма из исходной колоды Model Y, менять её нельзя.
OPEN_PRODUCER_RECORD = " '1' 'OPEN' 'LRAT' 1* 1* 1* 100 1* 50 1* 1* /"


def _producer(**overrides) -> ProducerControl:
    fields = {
        "well": "1",
        "status": WellStatus.OPEN,
        "mode": "LRAT",
        "liquid_rate": 100.0,
        "bhp_limit": 50.0,
    }
    fields.update(overrides)
    return ProducerControl(**fields)


def _injector(**overrides) -> InjectorControl:
    fields = {
        "well": "2",
        "phase": "WATER",
        "status": WellStatus.OPEN,
        "mode": "RATE",
        "surface_rate": 200.0,
        "bhp_limit": 300.0,
    }
    fields.update(overrides)
    return InjectorControl(**fields)


# --- TEST 1: SHUT-продюсер даёт легальную запись ----------------------------


def test_shut_producer_defaults_the_control_mode() -> None:
    control = _producer(status=WellStatus.SHUT, liquid_rate=None, bhp_limit=None)
    assert _producer_line(control) == SHUT_PRODUCER_RECORD


def test_shut_producer_never_names_a_rate_mode_without_a_value() -> None:
    line = _producer_line(_producer(status=WellStatus.SHUT, liquid_rate=None, bhp_limit=None))
    assert "'LRAT'" not in line


@pytest.mark.parametrize("status", [WellStatus.SHUT, WellStatus.STOP])
def test_stopped_and_shut_producers_are_both_representable(status) -> None:
    line = _producer_line(_producer(status=status, liquid_rate=None, bhp_limit=None))
    assert f"'{status}'" in line
    assert "'LRAT'" not in line


# --- TEST 2: обычные продюсеры не меняются ----------------------------------


def test_open_rate_controlled_producer_is_unchanged() -> None:
    assert _producer_line(_producer()) == OPEN_PRODUCER_RECORD


def test_open_producer_keeps_its_mode_when_the_rate_is_present() -> None:
    line = _producer_line(_producer(liquid_rate=80.0))
    assert "'LRAT'" in line
    assert " 80 " in line


def test_shut_producer_that_still_carries_a_rate_keeps_its_mode() -> None:
    """Режим убирается из-за отсутствия значения, а не из-за статуса."""
    line = _producer_line(_producer(status=WellStatus.SHUT, liquid_rate=100.0))
    assert "'LRAT'" in line


# --- TEST 3/4: непредставимые управления отвергаются ДО OPM -----------------


def test_open_producer_without_a_rate_is_rejected_before_opm() -> None:
    bad = _producer(status=WellStatus.OPEN, liquid_rate=None)
    with pytest.raises(ControlRepresentationError):
        require_representable_controls((bad,), ())


def test_open_injector_without_a_rate_is_rejected_before_opm() -> None:
    bad = _injector(status=WellStatus.OPEN, surface_rate=None)
    with pytest.raises(ControlRepresentationError):
        require_representable_controls((), (bad,))


def test_rejection_names_the_well_and_the_missing_control() -> None:
    bad = _producer(status=WellStatus.OPEN, liquid_rate=None)
    with pytest.raises(ControlRepresentationError) as excinfo:
        require_representable_controls((bad,), ())
    message = str(excinfo.value)
    assert "1" in message
    assert "LRAT" in message


# --- TEST 6: легальный SHUT не отвергается ---------------------------------


def test_valid_shut_producer_passes_validation() -> None:
    require_representable_controls(
        (_producer(status=WellStatus.SHUT, liquid_rate=None, bhp_limit=None),), ()
    )


def test_valid_shut_injector_passes_validation() -> None:
    require_representable_controls(
        (), (_injector(status=WellStatus.SHUT, surface_rate=None, bhp_limit=None),)
    )


def test_ordinary_open_controls_pass_validation() -> None:
    require_representable_controls((_producer(),), (_injector(),))


# --- SHUT-инжектор ---------------------------------------------------------


def test_shut_injector_defaults_the_control_mode() -> None:
    line = _injector_line(_injector(status=WellStatus.SHUT, surface_rate=None, bhp_limit=None))
    assert "'RATE'" not in line
    assert "'SHUT'" in line


# --- TEST 5: SHUT_IN проходит компиляцию кандидата целиком ------------------


def test_shut_in_candidate_compiles_into_a_parsable_schedule(mini_case) -> None:
    """Интеграция: SHUT_IN доходит до текста расписания без битой записи."""
    import datetime

    from aios.execution.compiler import compile_candidate_schedule
    from aios.mas.common import CandidateKind
    from aios.mas.magnitude import MagnitudeCandidate
    from aios.pipeline.validation import read_baseline_inputs

    state, baseline, _h, _s = read_baseline_inputs(mini_case)
    candidate = MagnitudeCandidate(
        candidate_kind=CandidateKind.AGENT_BUNDLE,
        bundle_digest="b" * 64,
        effective_date=datetime.date(2020, 3, 1),
        producers=(
            ProducerControl(well="P1", status=WellStatus.SHUT, mode="LRAT", liquid_rate=None),
        ),
        source_intent_digests=("d" * 64,),
        resolver_version="tests.shut/1.0.0",
    )
    compiled = compile_candidate_schedule(candidate, case=mini_case, state=state, baseline=baseline)
    shut_lines = [line for line in compiled.schedule_text.splitlines() if "'SHUT'" in line]
    assert shut_lines, "SHUT record must appear in the compiled schedule"
    for line in shut_lines:
        assert "'LRAT'" not in line


def test_unrepresentable_candidate_is_rejected_before_opm(mini_case) -> None:
    """Тот же класс ошибки больше не тратит прогон симулятора."""
    import datetime

    from aios.execution.compiler import compile_candidate_schedule
    from aios.mas.common import CandidateKind
    from aios.mas.magnitude import MagnitudeCandidate
    from aios.pipeline.validation import read_baseline_inputs

    state, baseline, _h, _s = read_baseline_inputs(mini_case)
    candidate = MagnitudeCandidate(
        candidate_kind=CandidateKind.AGENT_BUNDLE,
        bundle_digest="b" * 64,
        effective_date=datetime.date(2020, 3, 1),
        producers=(
            ProducerControl(well="P1", status=WellStatus.OPEN, mode="LRAT", liquid_rate=None),
        ),
        source_intent_digests=("d" * 64,),
        resolver_version="tests.shut/1.0.0",
    )
    with pytest.raises(ControlRepresentationError):
        compile_candidate_schedule(candidate, case=mini_case, state=state, baseline=baseline)
