"""Композитное расписание: неизменяемая история + хвост кандидата.

Кандидат в эпохе T_i компилируется в ОДНО полное расписание окна:
закоммиченная история на [T0, T_i) плюс управления кандидата на [T_i, H].
Физическое состояние в T_i внутри любого прогноза поэтому порождено ровно
теми управлениями, которые действительно были закоммичены.
"""

from __future__ import annotations

import datetime

import pytest

from aios.execution.compiler import compile_candidate_schedule
from aios.execution.history import (
    CommittedHistory,
    CommittedHistoryError,
    CommittedInterval,
)
from aios.mas.common import CandidateKind
from aios.mas.invariants import MasInvariantError, canonical_control_snapshot
from aios.mas.magnitude import MagnitudeCandidate
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule import InjectorControl, ProducerControl, WellStatus

pytestmark = pytest.mark.usefixtures("mini_case")

# mini_case: окно 2020-02-01 .. 2020-04-01, сетка февраль/март/апрель.
T0 = datetime.date(2020, 2, 1)
T1 = datetime.date(2020, 3, 1)
H = datetime.date(2020, 4, 1)


def _producer(well: str, rate: float) -> ProducerControl:
    return ProducerControl(well=well, status=WellStatus.OPEN, mode="LRAT", liquid_rate=rate)


def _injector(well: str, rate: float) -> InjectorControl:
    return InjectorControl(
        well=well, phase="WATER", status=WellStatus.OPEN, mode="RATE", surface_rate=rate
    )


def _candidate(
    kind: CandidateKind,
    effective_date: datetime.date,
    producers: tuple[ProducerControl, ...] = (),
    injectors: tuple[InjectorControl, ...] = (),
) -> MagnitudeCandidate:
    return MagnitudeCandidate(
        candidate_kind=kind,
        bundle_digest="b" * 64,
        effective_date=effective_date,
        producers=producers,
        injectors=injectors,
        source_intent_digests=("d" * 64,),
        resolver_version="tests.multistep/1.0.0",
    )


def _snapshot(producers, injectors):
    return canonical_control_snapshot(producers, injectors)


@pytest.fixture
def ctx(mini_case):
    state, baseline, _history_text, _start = read_baseline_inputs(mini_case)
    return mini_case, state, baseline


def _controls_at(compiled, moment):
    """Управления, явно выписанные в расписании на дату ``moment``."""
    for step in compiled.schedule.canonical().steps:
        if step.date == moment:
            return step.producers, step.injectors
    raise AssertionError(f"no schedule step at {moment}")


# Политика A, закоммиченная в T0 (отличается от baseline колоды).
A_P = (_producer("P1", 80.0),)
A_I = (_injector("I1", 240.0),)


def _history_A(digest: str = "cand-A") -> CommittedHistory:
    return CommittedHistory().commit(
        CommittedInterval(effective_date=T0, producers=A_P, injectors=A_I, candidate_digest=digest)
    )


# ===========================================================================
# TEST 1 -- HOLD проходит компилятор как перенос закоммиченных управлений
# ===========================================================================


def test_hold_at_t1_carries_the_committed_policy_through_the_compiler(ctx) -> None:
    """Ровно то падение, что остановило трёхшаговый прогон."""
    case, state, baseline = ctx
    hold = _candidate(CandidateKind.HOLD, T1, producers=A_P, injectors=A_I)
    compiled = compile_candidate_schedule(
        hold, case=case, state=state, baseline=baseline, committed_history=_history_A()
    )
    p, i = _controls_at(compiled, T1)
    assert _snapshot(p, i) == _snapshot(A_P, A_I)


def test_hold_at_t1_that_reverts_to_the_window_baseline_fails_closed(ctx) -> None:
    """HOLD не имеет права означать «вернуться к исходному baseline»."""
    case, state, baseline = ctx
    stale = _candidate(CandidateKind.HOLD, T1, producers=(_producer("P1", 100.0),), injectors=())
    with pytest.raises(MasInvariantError):
        compile_candidate_schedule(
            stale, case=case, state=state, baseline=baseline, committed_history=_history_A()
        )


def test_first_epoch_hold_still_carries_the_window_baseline(ctx) -> None:
    """Негативная регрессия: без коммитов HOLD -- это baseline окна."""
    case, state, baseline = ctx
    hold = _candidate(CandidateKind.HOLD, T0)
    compiled = compile_candidate_schedule(
        hold, case=case, state=state, baseline=baseline, committed_history=CommittedHistory()
    )
    assert compiled.schedule_sha256


# ===========================================================================
# TEST 2 -- физическая история продвигается через закоммиченный префикс
# ===========================================================================


def test_step2_schedule_carries_committed_policy_before_t1_not_the_baseline(ctx) -> None:
    case, state, baseline = ctx
    tail = _candidate(CandidateKind.AGENT_BUNDLE, T1, producers=(_producer("P1", 60.0),))
    compiled = compile_candidate_schedule(
        tail, case=case, state=state, baseline=baseline, committed_history=_history_A()
    )
    prefix_p, prefix_i = _controls_at(compiled, T0)
    # A на [T0, T1)
    assert _snapshot(prefix_p, prefix_i) == _snapshot(A_P, A_I)
    # и НЕ исходный baseline колоды
    assert _snapshot(prefix_p, prefix_i) != _snapshot(*_validated_baseline(state))


def _validated_baseline(state):
    from aios.execution.compiler import _validated_baseline_controls

    return _validated_baseline_controls(state)


def test_candidate_cannot_modify_the_interval_before_its_epoch(ctx) -> None:
    """Кандидат владеет только [T_i, H]; префикс неизменяем."""
    case, state, baseline = ctx
    aggressive = _candidate(CandidateKind.AGENT_BUNDLE, T1, producers=(_producer("P1", 10.0),))
    compiled = compile_candidate_schedule(
        aggressive, case=case, state=state, baseline=baseline, committed_history=_history_A()
    )
    prefix_p, _prefix_i = _controls_at(compiled, T0)
    rates = {c.well: c.liquid_rate for c in prefix_p}
    assert rates["P1"] == 80.0, "префикс обязан остаться закоммиченной историей"


# ===========================================================================
# TEST 10 -- устаревший baseline-префикс отвергается
# ===========================================================================


def test_stale_baseline_prefix_after_a_non_baseline_commit_fails_closed(ctx) -> None:
    """Шаг N>0 с пустой историей после коммита A -- отказ, а не тихий baseline."""
    case, state, baseline = ctx
    tail = _candidate(CandidateKind.AGENT_BUNDLE, T1, producers=(_producer("P1", 60.0),))
    with pytest.raises((CommittedHistoryError, MasInvariantError)):
        compile_candidate_schedule(
            tail,
            case=case,
            state=state,
            baseline=baseline,
            committed_history=CommittedHistory(),
            require_history_before=T1,
        )


# ===========================================================================
# TEST 5 -- HOLD и DETERMINISTIC_BASELINE остаются различны
# ===========================================================================


def test_hold_and_deterministic_baseline_differ_after_a_non_baseline_commit(ctx) -> None:
    case, state, baseline = ctx
    history = _history_A()
    hold = compile_candidate_schedule(
        _candidate(CandidateKind.HOLD, T1, producers=A_P, injectors=A_I),
        case=case,
        state=state,
        baseline=baseline,
        committed_history=history,
    )
    det = compile_candidate_schedule(
        _candidate(CandidateKind.DETERMINISTIC_BASELINE, T1),
        case=case,
        state=state,
        baseline=baseline,
        committed_history=history,
    )
    assert hold.schedule_sha256 != det.schedule_sha256
    # Но история до T1 у обоих одна и та же -- она неизменяема.
    assert _snapshot(*_controls_at(hold, T0)) == _snapshot(*_controls_at(det, T0))


# ===========================================================================
# TEST 7 -- прогноз кандидата всегда заканчивается на H
# ===========================================================================


@pytest.mark.parametrize("epoch", [T0, T1])
def test_forecast_end_is_always_the_fixed_horizon(ctx, epoch) -> None:
    case, state, baseline = ctx
    history = CommittedHistory() if epoch == T0 else _history_A()
    compiled = compile_candidate_schedule(
        _candidate(CandidateKind.AGENT_BUNDLE, epoch, producers=(_producer("P1", 70.0),)),
        case=case,
        state=state,
        baseline=baseline,
        committed_history=history,
    )
    dates = compiled.schedule.canonical().dates
    assert dates[0] == T0
    assert dates[-1] == H
