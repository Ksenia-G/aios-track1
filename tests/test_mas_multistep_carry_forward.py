"""HOLD на шаге N -- перенос управлений, ЗАКОММИЧЕННЫХ на шаге N-1.

Инвариант T6 сравнивает HOLD-кандидата с `step_input.previous_committed`.
Движок резолвера это уже соблюдает: HOLD собирается из
`MagnitudeContext.previous_*`, а DETERMINISTIC_BASELINE -- из
`baseline_*`. Ошибка была не в движке и не в оркестраторе, а в проводке
бенчмарка: `_ContextualMagnitudeResolver` держал `previous_*` пустыми всю
дорогу, поэтому `previous_* or baseline_*` на каждом шаге возвращал ИСХОДНЫЙ
baseline. Пока MAS коммитил baseline-эквивалент, это совпадало случайно; на
первом же реальном AGENT_BUNDLE многошаговый прогон падал с
MasInvariantError.

HOLD означает «оставить то, что реально стоит», а не «вернуться к исходному
эксперименту».
"""

from __future__ import annotations

import datetime

from aios.benchmark.mas_variant import _carry_forward, _ContextualMagnitudeResolver
from aios.mas.arbiter import ArbiterDecision, DecisionBundle
from aios.mas.common import CandidateKind
from aios.mas.invariants import (
    MasInvariantError,
    canonical_control_snapshot,
    require_hold_carry_forward,
)
from aios.schedule import InjectorControl, ProducerControl, WellStatus
from tests.support.mas_team_fixtures import synthetic_arbiter_envelope

SCHEMA_VERSION = "aios.mas.contracts/1.0.0"
EFFECTIVE_DATE = datetime.date(2020, 4, 1)


def _producer(well: str, *, rate: float) -> ProducerControl:
    return ProducerControl(well=well, status=WellStatus.OPEN, mode="LRAT", liquid_rate=rate)


def _injector(well: str, *, rate: float) -> InjectorControl:
    return InjectorControl(
        well=well, phase="WATER", status=WellStatus.OPEN, mode="RATE", surface_rate=rate
    )


#: Исходный baseline эксперимента.
BASELINE_PRODUCERS = (_producer("SYN-P1", rate=100.0),)
BASELINE_INJECTORS = (_injector("SYN-I1", rate=200.0),)

#: Заведомо ДРУГОЙ валидный набор -- то, что MAS коммитит на шаге 1.
COMMITTED_PRODUCERS = (_producer("SYN-P1", rate=80.0),)
COMMITTED_INJECTORS = (_injector("SYN-I1", rate=240.0),)


class _StubArbiter:
    """Отдаёт готовый envelope: арбитраж здесь не проверяется."""

    def __init__(self, envelope):
        self.last_envelope = envelope


class _CommittedCandidate:
    """Минимальный носитель закоммиченных управлений (как MagnitudeCandidate)."""

    def __init__(self, producers, injectors):
        self.producers = producers
        self.injectors = injectors


def _resolver(**overrides):
    envelope = synthetic_arbiter_envelope()
    fields = {
        "arbiter": _StubArbiter(envelope),
        "effective_date": EFFECTIVE_DATE,
        "baseline_producers": BASELINE_PRODUCERS,
        "baseline_injectors": BASELINE_INJECTORS,
    }
    fields.update(overrides)
    return _ContextualMagnitudeResolver(**fields), envelope


def _hold_only_decision(envelope) -> ArbiterDecision:
    """HOLD + обязательный DETERMINISTIC_BASELINE: контракт требует пол."""
    return ArbiterDecision(
        schema_version=SCHEMA_VERSION,
        arbiter_envelope_digest=envelope.digest(),
        bundles=(
            DecisionBundle(kind=CandidateKind.HOLD),
            DecisionBundle(kind=CandidateKind.DETERMINISTIC_BASELINE),
        ),
    )


def _hold_candidate(candidates):
    held = [c for c in candidates if CandidateKind(c.candidate_kind) is CandidateKind.HOLD]
    assert len(held) == 1, f"expected exactly one HOLD candidate, got {len(held)}"
    return held[0]


def _snapshot(producers, injectors):
    return canonical_control_snapshot(producers, injectors)


# -- шаг 1: предыдущего коммита нет ------------------------------------------


def test_first_step_hold_carries_the_original_baseline() -> None:
    """Негативная регрессия: без предыдущего коммита HOLD -- это baseline."""
    resolver, envelope = _resolver()
    hold = _hold_candidate(resolver.resolve(_hold_only_decision(envelope)))
    assert _snapshot(hold.producers, hold.injectors) == _snapshot(
        BASELINE_PRODUCERS, BASELINE_INJECTORS
    )
    # И T6 против этого же baseline проходит.
    require_hold_carry_forward(
        previous_producers=BASELINE_PRODUCERS,
        previous_injectors=BASELINE_INJECTORS,
        held_producers=hold.producers,
        held_injectors=hold.injectors,
    )


# -- шаг 2: после коммита НЕ-baseline набора ---------------------------------


def test_second_step_hold_carries_the_committed_controls_not_the_baseline() -> None:
    resolver, envelope = _resolver()
    committed = _CommittedCandidate(COMMITTED_PRODUCERS, COMMITTED_INJECTORS)

    _carry_forward(resolver, committed)

    hold = _hold_candidate(resolver.resolve(_hold_only_decision(envelope)))
    held = _snapshot(hold.producers, hold.injectors)

    assert held == _snapshot(COMMITTED_PRODUCERS, COMMITTED_INJECTORS)
    assert held != _snapshot(BASELINE_PRODUCERS, BASELINE_INJECTORS)


def test_second_step_hold_satisfies_invariant_t6() -> None:
    """Ровно то падение, которое остановило трёхшаговый прогон."""
    resolver, envelope = _resolver()
    committed = _CommittedCandidate(COMMITTED_PRODUCERS, COMMITTED_INJECTORS)
    _carry_forward(resolver, committed)

    hold = _hold_candidate(resolver.resolve(_hold_only_decision(envelope)))
    require_hold_carry_forward(
        previous_producers=committed.producers,
        previous_injectors=committed.injectors,
        held_producers=hold.producers,
        held_injectors=hold.injectors,
    )


def test_unfixed_wiring_would_fail_t6() -> None:
    """Контроль осмысленности: исходный baseline против коммита T6 не проходит."""
    resolver, envelope = _resolver()
    hold = _hold_candidate(resolver.resolve(_hold_only_decision(envelope)))
    try:
        require_hold_carry_forward(
            previous_producers=COMMITTED_PRODUCERS,
            previous_injectors=COMMITTED_INJECTORS,
            held_producers=hold.producers,
            held_injectors=hold.injectors,
        )
    except MasInvariantError:
        return
    raise AssertionError("expected T6 to reject a baseline HOLD against a non-baseline commit")


# -- baseline-семантика остаётся отдельной -----------------------------------


def test_deterministic_baseline_candidate_still_resolves_to_the_original_baseline() -> None:
    """ORIGINAL_BASELINE не имеет права уехать вместе с HOLD."""
    resolver, envelope = _resolver()
    _carry_forward(resolver, _CommittedCandidate(COMMITTED_PRODUCERS, COMMITTED_INJECTORS))

    decision = ArbiterDecision(
        schema_version=SCHEMA_VERSION,
        arbiter_envelope_digest=envelope.digest(),
        bundles=(
            DecisionBundle(kind=CandidateKind.HOLD),
            DecisionBundle(kind=CandidateKind.DETERMINISTIC_BASELINE),
        ),
    )
    candidates = resolver.resolve(decision)
    baseline = [
        c
        for c in candidates
        if CandidateKind(c.candidate_kind) is CandidateKind.DETERMINISTIC_BASELINE
    ]
    assert len(baseline) == 1
    assert _snapshot(baseline[0].producers, baseline[0].injectors) == _snapshot(
        BASELINE_PRODUCERS, BASELINE_INJECTORS
    )
    hold = _hold_candidate(candidates)
    assert _snapshot(hold.producers, hold.injectors) != _snapshot(
        baseline[0].producers, baseline[0].injectors
    )


def test_carry_forward_is_idempotent_and_replaces_rather_than_accumulates() -> None:
    resolver, envelope = _resolver()
    first = _CommittedCandidate(COMMITTED_PRODUCERS, COMMITTED_INJECTORS)
    later_producers = (_producer("SYN-P1", rate=60.0),)
    later_injectors = (_injector("SYN-I1", rate=260.0),)

    _carry_forward(resolver, first)
    _carry_forward(resolver, _CommittedCandidate(later_producers, later_injectors))

    hold = _hold_candidate(resolver.resolve(_hold_only_decision(envelope)))
    assert _snapshot(hold.producers, hold.injectors) == _snapshot(later_producers, later_injectors)


def test_carry_forward_introduces_no_numerical_authority_of_its_own() -> None:
    """Перенос -- это перенос: никаких новых величин мимо MagnitudeResolver."""
    resolver, _envelope = _resolver()
    committed = _CommittedCandidate(COMMITTED_PRODUCERS, COMMITTED_INJECTORS)
    _carry_forward(resolver, committed)
    assert resolver.previous_producers == COMMITTED_PRODUCERS
    assert resolver.previous_injectors == COMMITTED_INJECTORS
    assert resolver.baseline_producers == BASELINE_PRODUCERS
    assert resolver.baseline_injectors == BASELINE_INJECTORS
