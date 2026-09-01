"""PERF-002B: детерминированный численный поиск вокруг структурной гипотезы.

До этого одна структурная гипотеза Production превращалась ровно в ОДИН
численный кандидат: интенсивность отображалась в единственный anchor. На
эпохах T1/T2, где и появляется весь наблюдаемый прирост, это означало ровно
одну проверяемую политику против HOLD/DETERMINISTIC_BASELINE, при бюджете 8 и
трёх незанятых слотах.

Численная власть не переезжает: LLM по-прежнему выдаёт только структуру
(действие, направление, интенсивность, целевые скважины), а весь численный
перебор остаётся детерминированным и внутри MagnitudeResolver.
"""

from __future__ import annotations

from aios.mas.common import CandidateKind, Direction, Intensity, UncertaintyLevel
from aios.mas.intents import ProductionAction, ProductionIntent
from aios.mas.magnitude_resolution.engine import (
    DeterministicMagnitudeResolver,
)
from aios.mas.magnitude_resolution.neighbourhood import (
    MAX_TARGET_SUBSETS,
    producer_factor_grid,
    target_subsets,
)
from tests.test_magnitude_resolver_engine import (  # reuse the authoritative harness
    _build_envelope,
    _context,
    _decision_bundle_for,
    _decision_for,
    _producer,
)


def _intent(action, intensity, wells, direction):
    return ProductionIntent(
        action=action,
        direction=direction,
        intensity=intensity,
        target_wells=tuple(wells),
        target_regions=(),
        evidence_ids=("SYN-pressure-1",),
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="guided search fixture",
    )


def _resolve(action, intensity, wells, direction=Direction.UP, *, previous=None, budget_wells=None):
    envelope = _build_envelope(
        production_primary_intents=(_intent(action, intensity, wells, direction),)
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    prev = previous or tuple(_producer(w) for w in (budget_wells or wells))
    context = _context(envelope, previous_producers=prev, baseline_producers=prev)
    resolver = DeterministicMagnitudeResolver(context=context)
    return resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))


def _agents(candidates):
    return [c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE]


def _rates(candidate):
    return {c.well: c.liquid_rate for c in candidate.producers}


# ===========================================================================
# TEST 1 -- одна структурная гипотеза даёт НЕСКОЛЬКО численных кандидатов
# ===========================================================================


def test_one_structural_hypothesis_expands_to_many_numerical_candidates() -> None:
    candidates = _resolve(ProductionAction.INCREASE_OFFTAKE, Intensity.MODERATE, ("P1", "P2"))
    agents = _agents(candidates)
    assert len(agents) > 1, "a single structural hypothesis must expand numerically"
    vectors = {tuple(sorted(_rates(c).items())) for c in agents}
    assert len(vectors) == len(agents), "expanded candidates must be numerically distinct"


# ===========================================================================
# TEST 2 -- поиск не выдумывает новых целевых скважин
# ===========================================================================


def test_search_never_touches_a_well_the_llm_did_not_authorize() -> None:
    prev = (_producer("P1"), _producer("P2"), _producer("P3"))
    candidates = _resolve(
        ProductionAction.INCREASE_OFFTAKE, Intensity.AGGRESSIVE, ("P1", "P2"), previous=prev
    )
    for candidate in _agents(candidates):
        for control in candidate.producers:
            if control.well not in {"P1", "P2"}:
                assert control.liquid_rate == 100.0, f"unauthorized change on {control.well}"


# ===========================================================================
# TEST 4 -- сетка магнитуд закреплена и вложена
# ===========================================================================


def test_increase_grid_is_pinned_and_nested() -> None:
    small = producer_factor_grid(ProductionAction.INCREASE_OFFTAKE, Intensity.SMALL)
    moderate = producer_factor_grid(ProductionAction.INCREASE_OFFTAKE, Intensity.MODERATE)
    aggressive = producer_factor_grid(ProductionAction.INCREASE_OFFTAKE, Intensity.AGGRESSIVE)
    assert small == (1.10, 1.25)
    assert moderate == (1.10, 1.25, 1.50, 2.00)
    assert aggressive == (1.10, 1.25, 1.50, 2.00, 3.00, 5.00)
    assert set(small) < set(moderate) < set(aggressive)


def test_reduce_grid_is_pinned_and_nested() -> None:
    small = producer_factor_grid(ProductionAction.REDUCE_OFFTAKE, Intensity.SMALL)
    moderate = producer_factor_grid(ProductionAction.REDUCE_OFFTAKE, Intensity.MODERATE)
    aggressive = producer_factor_grid(ProductionAction.REDUCE_OFFTAKE, Intensity.AGGRESSIVE)
    assert small == (0.90, 0.85)
    assert moderate == (0.90, 0.85, 0.80, 0.70)
    assert aggressive == (0.90, 0.85, 0.80, 0.70, 0.60)
    assert set(small) < set(moderate) < set(aggressive)


def test_legacy_anchor_is_a_member_of_every_grid() -> None:
    """Прежний единственный кандидат обязан остаться внутри нового поиска."""
    assert 1.10 in producer_factor_grid(ProductionAction.INCREASE_OFFTAKE, Intensity.SMALL)
    assert 1.50 in producer_factor_grid(ProductionAction.INCREASE_OFFTAKE, Intensity.MODERATE)
    assert 5.00 in producer_factor_grid(ProductionAction.INCREASE_OFFTAKE, Intensity.AGGRESSIVE)
    assert 0.90 in producer_factor_grid(ProductionAction.REDUCE_OFFTAKE, Intensity.SMALL)
    assert 0.80 in producer_factor_grid(ProductionAction.REDUCE_OFFTAKE, Intensity.MODERATE)
    assert 0.60 in producer_factor_grid(ProductionAction.REDUCE_OFFTAKE, Intensity.AGGRESSIVE)


# ===========================================================================
# TEST 5 -- клиппинг по существующему потолку
# ===========================================================================


def test_uplift_never_exceeds_the_producer_ceiling() -> None:
    from aios.cases.contract import TRACK1_WLPR_HARD_MAX

    candidates = _resolve(ProductionAction.INCREASE_OFFTAKE, Intensity.AGGRESSIVE, ("P1", "P2"))
    for candidate in _agents(candidates):
        for control in candidate.producers:
            assert control.liquid_rate <= TRACK1_WLPR_HARD_MAX


def test_reduction_stays_positive_and_below_baseline() -> None:
    candidates = _resolve(
        ProductionAction.REDUCE_OFFTAKE,
        Intensity.AGGRESSIVE,
        ("P1", "P2"),
        direction=Direction.DOWN,
    )
    for candidate in _agents(candidates):
        for control in candidate.producers:
            assert 0.0 < control.liquid_rate <= 100.0


# ===========================================================================
# TEST 6/7 -- подмножества целей и их ограничение
# ===========================================================================


def test_target_subsets_include_full_set_first_and_smaller_subsets() -> None:
    subsets = target_subsets(("P1", "P2", "P3"))
    assert subsets[0] == ("P1", "P2", "P3"), "full authorized set must be explored first"
    assert ("P1",) in subsets
    assert len(subsets) > 1


def test_target_subsets_are_capped_for_large_target_sets() -> None:
    wells = tuple(f"W{i}" for i in range(20))
    subsets = target_subsets(wells)
    assert len(subsets) <= MAX_TARGET_SUBSETS
    for subset in subsets:
        assert set(subset) <= set(wells)


def test_single_well_target_yields_one_subset() -> None:
    assert target_subsets(("P1",)) == (("P1",),)


# ===========================================================================
# TEST 8 -- дедупликация по каноническому вектору управлений
# ===========================================================================


def test_identical_control_vectors_collapse_to_one_candidate() -> None:
    """Дедупликация касается расширенных кандидатов.

    Пол (HOLD/DETERMINISTIC_BASELINE) -- это две обязательные контрактные
    идентичности, и их управления совпадают законно, пока не было коммита.
    """
    candidates = _resolve(ProductionAction.INCREASE_OFFTAKE, Intensity.AGGRESSIVE, ("P1", "P2"))
    vectors = [tuple(sorted(_rates(c).items())) for c in _agents(candidates)]
    assert len(vectors) == len(set(vectors)), "no two agent candidates may carry identical controls"


def test_agent_candidates_never_duplicate_the_floor_controls() -> None:
    candidates = _resolve(ProductionAction.INCREASE_OFFTAKE, Intensity.AGGRESSIVE, ("P1", "P2"))
    floor = {
        tuple(sorted(_rates(c).items()))
        for c in candidates
        if c.candidate_kind is not CandidateKind.AGENT_BUNDLE
    }
    for candidate in _agents(candidates):
        assert tuple(sorted(_rates(candidate).items())) not in floor


# ===========================================================================
# TEST 9 -- физический бюджет соблюдён
# ===========================================================================


def test_total_candidates_never_exceed_the_simulation_budget() -> None:
    candidates = _resolve(
        ProductionAction.INCREASE_OFFTAKE,
        Intensity.AGGRESSIVE,
        ("P1", "P2", "P3"),
        previous=tuple(_producer(w) for w in ("P1", "P2", "P3")),
    )
    assert len(candidates) <= 8


def test_floor_candidates_are_always_preserved() -> None:
    candidates = _resolve(
        ProductionAction.INCREASE_OFFTAKE,
        Intensity.AGGRESSIVE,
        ("P1", "P2", "P3"),
        previous=tuple(_producer(w) for w in ("P1", "P2", "P3")),
    )
    kinds = {c.candidate_kind for c in candidates}
    assert CandidateKind.HOLD in kinds
    assert CandidateKind.DETERMINISTIC_BASELINE in kinds


# ===========================================================================
# TEST 10 -- детерминизм
# ===========================================================================


def test_expansion_is_deterministic() -> None:
    first = _resolve(ProductionAction.INCREASE_OFFTAKE, Intensity.AGGRESSIVE, ("P1", "P2"))
    second = _resolve(ProductionAction.INCREASE_OFFTAKE, Intensity.AGGRESSIVE, ("P1", "P2"))
    assert [c.digest() for c in first] == [c.digest() for c in second]


# ===========================================================================
# TEST 11 -- разнообразие финалистов
# ===========================================================================


def test_finalists_span_more_than_one_magnitude_level() -> None:
    candidates = _resolve(ProductionAction.INCREASE_OFFTAKE, Intensity.AGGRESSIVE, ("P1", "P2"))
    rates = {
        control.liquid_rate
        for candidate in _agents(candidates)
        for control in candidate.producers
        if control.liquid_rate != 100.0
    }
    assert len(rates) > 1, "finalists must not all sit on one magnitude"


def test_finalists_span_more_than_one_target_subset() -> None:
    candidates = _resolve(
        ProductionAction.INCREASE_OFFTAKE,
        Intensity.AGGRESSIVE,
        ("P1", "P2", "P3"),
        previous=tuple(_producer(w) for w in ("P1", "P2", "P3")),
    )
    sizes = {
        len([c for c in candidate.producers if c.liquid_rate != 100.0])
        for candidate in _agents(candidates)
    }
    assert len(sizes) > 1, "finalists must not all touch the same number of wells"


# ===========================================================================
# TEST 12 -- прежний победитель остаётся достижимым
# ===========================================================================


def test_legacy_full_set_anchor_candidate_is_still_produced() -> None:
    """×1.50 на всём авторизованном наборе -- прежняя выигравшая политика."""
    candidates = _resolve(ProductionAction.INCREASE_OFFTAKE, Intensity.MODERATE, ("P1", "P2"))
    legacy = {"P1": 150.0, "P2": 150.0}
    assert any(_rates(c) == legacy for c in _agents(candidates)), (
        "the pre-PERF-002B anchor candidate must survive inside the expanded search"
    )


# ===========================================================================
# TEST 13 -- инъекция не затронута
# ===========================================================================


def test_injection_controls_are_untouched_by_producer_search() -> None:
    candidates = _resolve(ProductionAction.INCREASE_OFFTAKE, Intensity.AGGRESSIVE, ("P1", "P2"))
    for candidate in _agents(candidates):
        assert {c.well: c.surface_rate for c in candidate.injectors} == {"I1": 200.0}


# ===========================================================================
# Анти-оверфит
# ===========================================================================


def test_no_benchmark_specific_identifiers_in_the_search_implementation() -> None:
    """Ни списка скважин Model Y, ни дайджеста состояния в коде поиска."""
    import re
    from pathlib import Path

    source = Path("src/aios/mas/magnitude_resolution/neighbourhood.py").read_text(encoding="utf-8")
    for banned in ("model_y", "MODEL_Y", "state_digest", "EXP003", "EXP004"):
        assert banned not in source
    # Списков конкретных скважин быть не должно.
    assert not re.search(r"[\"'](?:\d{1,2})[\"']\s*,\s*[\"'](?:\d{1,2})[\"']", source)
