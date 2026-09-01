"""Численное расширение обязано работать и при нескольких Production-намерениях.

PERF-002B ввёл детерминированную окрестность вокруг структурной гипотезы, но
раскрывал только ОДНО масштабирующее намерение. Реальные предложения модели
регулярно содержат несколько: наблюдавшийся живой прогон дал

    T1: REDUCE_OFFTAKE {1} + INCREASE_OFFTAKE {45}
    T2: SHUT_IN {1} + INCREASE_OFFTAKE {45} + INCREASE_OFFTAKE {54}

и ровно на этих эпохах поиск молча схлопывался в один численный кандидат.

Ограничение было осознанной страховкой: механизм подмены заменял ВСЮ
производственную материализацию одной парой (скважины, множитель) и не умел
сказать «этому намерению -- сосед по сетке, остальные -- как обычно». Лучше
прежнее поведение, чем кандидат, потерявший SHUT_IN.

Семантика действий сохраняется: SHUT_IN остаётся дискретным структурным
действием и сеткой величин не становится.
"""

from __future__ import annotations

import pytest

from aios.mas.common import CandidateKind, Direction, Intensity, UncertaintyLevel
from aios.mas.intents import ProductionAction, ProductionIntent
from aios.mas.magnitude_resolution.engine import (
    DeterministicMagnitudeResolver,
    MagnitudeResolutionError,
)
from tests.test_magnitude_resolver_engine import (
    _build_envelope,
    _context,
    _decision_bundle_for,
    _decision_for,
    _producer,
)

_DIRECTION = {
    ProductionAction.INCREASE_OFFTAKE: Direction.UP,
    ProductionAction.REDUCE_OFFTAKE: Direction.DOWN,
    ProductionAction.SHUT_IN: Direction.NONE,
}
_INTENSITY = {
    ProductionAction.SHUT_IN: Intensity.NONE,
}


def _intent(action, wells, intensity=Intensity.MODERATE):
    return ProductionIntent(
        action=action,
        direction=_DIRECTION[action],
        intensity=_INTENSITY.get(action, intensity),
        target_wells=tuple(wells),
        target_regions=(),
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="multi intent fixture",
    )


def _resolve(intents, wells=("A", "B", "C")):
    envelope = _build_envelope(production_primary_intents=tuple(intents))
    previous = tuple(_producer(w) for w in wells)
    context = _context(envelope, previous_producers=previous, baseline_producers=previous)
    resolver = DeterministicMagnitudeResolver(context=context)
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agents = [c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE]
    return candidates, agents, resolver


def _rates(candidate):
    return {c.well: c.liquid_rate for c in candidate.producers}


def _status(candidate, well):
    return next(c.status for c in candidate.producers if c.well == well)


# ===========================================================================
# M1 -- два масштабирующих намерения раскрываются
# ===========================================================================


def test_two_scalable_intents_expand(self=None) -> None:
    _all, agents, _r = _resolve(
        (
            _intent(ProductionAction.INCREASE_OFFTAKE, ("A",)),
            _intent(ProductionAction.INCREASE_OFFTAKE, ("B",)),
        )
    )
    assert len(agents) > 1, "multiple scalable intents must still expand numerically"
    vectors = {tuple(sorted(_rates(c).items())) for c in agents}
    assert len(vectors) == len(agents)


# ===========================================================================
# M2 -- разные направления на непересекающихся скважинах
# ===========================================================================


def test_mixed_directions_on_disjoint_wells_expand() -> None:
    _all, agents, _r = _resolve(
        (
            _intent(ProductionAction.REDUCE_OFFTAKE, ("A",)),
            _intent(ProductionAction.INCREASE_OFFTAKE, ("B",)),
        )
    )
    assert len(agents) > 1
    for candidate in agents:
        rates = _rates(candidate)
        assert rates["A"] <= 100.0, "a reduction intent must never raise its well"
        assert rates["B"] >= 100.0, "an uplift intent must never lower its well"


# ===========================================================================
# M3/M4 -- SHUT_IN не отключает расширение и остаётся дискретным
# ===========================================================================


def test_shut_in_does_not_disable_uplift_expansion() -> None:
    _all, agents, _r = _resolve(
        (
            _intent(ProductionAction.SHUT_IN, ("A",)),
            _intent(ProductionAction.INCREASE_OFFTAKE, ("B", "C")),
        )
    )
    assert len(agents) > 1, "SHUT_IN must not switch numerical search off"
    from aios.schedule.events import WellStatus

    for candidate in agents:
        assert _status(candidate, "A") is WellStatus.SHUT, "the shut well must stay shut"


def test_three_intent_proposal_keeps_expanding() -> None:
    """Форма наблюдавшейся эпохи T2."""
    _all, agents, _r = _resolve(
        (
            _intent(ProductionAction.SHUT_IN, ("A",)),
            _intent(ProductionAction.INCREASE_OFFTAKE, ("B",)),
            _intent(ProductionAction.INCREASE_OFFTAKE, ("C",), Intensity.SMALL),
        )
    )
    assert len(agents) > 1


def test_shut_in_alone_is_not_given_a_magnitude_grid() -> None:
    """Дискретное действие сеткой величин не становится."""
    _all, agents, _r = _resolve((_intent(ProductionAction.SHUT_IN, ("A",)),))
    assert len(agents) == 1


# ===========================================================================
# M5 -- никаких неавторизованных скважин
# ===========================================================================


def test_no_unauthorized_producer_is_ever_modified() -> None:
    _all, agents, _r = _resolve(
        (
            _intent(ProductionAction.INCREASE_OFFTAKE, ("A",)),
            _intent(ProductionAction.REDUCE_OFFTAKE, ("B",)),
        )
    )
    for candidate in agents:
        assert _rates(candidate)["C"] == 100.0, "well C was never authorized"


# ===========================================================================
# M6 -- конфликт закрывается детерминированно
# ===========================================================================


def test_overlapping_contradictory_intents_fail_closed() -> None:
    """Две гипотезы на одну скважину не материализуются молча."""
    envelope = _build_envelope(
        production_primary_intents=(
            _intent(ProductionAction.INCREASE_OFFTAKE, ("A",)),
            _intent(ProductionAction.REDUCE_OFFTAKE, ("A",)),
        )
    )
    previous = tuple(_producer(w) for w in ("A", "B"))
    context = _context(envelope, previous_producers=previous, baseline_producers=previous)
    resolver = DeterministicMagnitudeResolver(context=context)
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    assert not any(c.candidate_kind is CandidateKind.AGENT_BUNDLE for c in candidates)


def test_conflicting_wells_raise_inside_the_resolver() -> None:
    from aios.mas.magnitude_resolution.engine import DeterministicMagnitudeResolver as _R

    resolver = _R.__new__(_R)
    touched: set[str] = set()
    resolver._require_untouched("A", touched, "production")
    with pytest.raises(MagnitudeResolutionError):
        resolver._require_untouched("A", touched, "production")


# ===========================================================================
# M7/M8 -- детерминизм и бюджет
# ===========================================================================


def test_multi_intent_expansion_is_deterministic() -> None:
    intents = (
        _intent(ProductionAction.SHUT_IN, ("A",)),
        _intent(ProductionAction.INCREASE_OFFTAKE, ("B",)),
        _intent(ProductionAction.REDUCE_OFFTAKE, ("C",)),
    )
    first = _resolve(intents)[1]
    second = _resolve(intents)[1]
    assert [c.digest() for c in first] == [c.digest() for c in second]


def test_multi_intent_expansion_respects_the_simulation_budget() -> None:
    wells = tuple(f"W{i}" for i in range(8))
    intents = tuple(
        _intent(ProductionAction.INCREASE_OFFTAKE, (w,), Intensity.AGGRESSIVE) for w in wells
    )
    allc, _agents, _r = _resolve(intents, wells=wells)
    assert len(allc) <= 8


# ===========================================================================
# M9/M10 -- прежнее поведение сохранено
# ===========================================================================


def test_legacy_anchor_combination_is_the_first_candidate() -> None:
    """Кандидат, который дал бы прежний резолвер, остаётся первым."""
    _all, agents, _r = _resolve(
        (
            _intent(ProductionAction.INCREASE_OFFTAKE, ("A",)),
            _intent(ProductionAction.REDUCE_OFFTAKE, ("B",)),
        )
    )
    rates = _rates(agents[0])
    assert rates["A"] == pytest.approx(150.0), "uplift MODERATE anchor is x1.50"
    assert rates["B"] == pytest.approx(80.0), "reduction MODERATE anchor is x0.80"


def test_single_intent_behaviour_is_unchanged() -> None:
    _all, agents, _r = _resolve((_intent(ProductionAction.INCREASE_OFFTAKE, ("A", "B")),))
    assert len(agents) > 1
    assert _rates(agents[0])["A"] == pytest.approx(150.0)
    assert _rates(agents[0])["B"] == pytest.approx(150.0)
