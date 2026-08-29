"""PERF-003: многопарное перераспределение закачки.

Резолвер поддерживал ровно одну пару донор/получатель -- «единственный
протестированный шаблон переноса». Экономический механизм, который отделяет
текущий MAS от опорного результата EXP-003, устроен иначе: несколько
одновременных переносов при почти неизменной суммарной закачке.

Численную часть по-прежнему целиком владеет резолвер. Специалист выдаёт только
структуру: действие, регионы/скважины, направление, интенсивность.
"""

from __future__ import annotations

import pytest

from aios.mas.common import Direction, Intensity, UncertaintyLevel
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.mas.magnitude_resolution.reallocation import (
    MAX_REALLOCATION_VIRTUAL_CANDIDATES,
    ReallocationError,
    donor_recipient_splits,
    transfer_fraction_grid,
)
from aios.schedule.events import InjectorControl, WellStatus


def _inj(well: str, rate: float) -> InjectorControl:
    return InjectorControl(
        well=well, phase="WATER", status=WellStatus.OPEN, mode="RATE", surface_rate=rate
    )


def _intent(donors, recipients, intensity=Intensity.MODERATE):
    return InjectionIntent(
        action=InjectionAction.REALLOCATE_INJECTION,
        direction=Direction.NONE,
        intensity=intensity,
        donor_wells=tuple(donors),
        recipient_wells=tuple(recipients),
        source_regions=("1",),
        target_regions=("1",),
        evidence_ids=("SYN-pressure-1",),
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="multi pair fixture",
    )


# ===========================================================================
# I7/I8 -- сетка долей переноса
# ===========================================================================


def test_transfer_grid_is_nested_and_keeps_the_legacy_anchor() -> None:
    small = transfer_fraction_grid(Intensity.SMALL)
    moderate = transfer_fraction_grid(Intensity.MODERATE)
    aggressive = transfer_fraction_grid(Intensity.AGGRESSIVE)
    assert set(small) < set(moderate) < set(aggressive)
    assert 0.35 in small
    assert 0.825 in moderate
    assert 0.975 in aggressive
    for grid in (small, moderate, aggressive):
        assert all(0.0 < value <= 1.0 for value in grid)


# ===========================================================================
# I3/I10 -- многопарные разбиения, ограниченная комбинаторика
# ===========================================================================


def test_multi_donor_multi_recipient_split_is_produced() -> None:
    splits = donor_recipient_splits(("D1", "D2"), ("R1", "R2"))
    assert any(len(d) == 2 and len(r) == 2 for d, r in splits), (
        "the full multi-donor/multi-recipient split must be explored"
    )


def test_full_split_is_explored_first() -> None:
    splits = donor_recipient_splits(("D1", "D2"), ("R1", "R2"))
    assert splits[0] == (("D1", "D2"), ("R1", "R2"))


def test_splits_stay_inside_the_authorized_envelope() -> None:
    splits = donor_recipient_splits(("D1", "D2", "D3"), ("R1", "R2"))
    for donors, recipients in splits:
        assert set(donors) <= {"D1", "D2", "D3"}
        assert set(recipients) <= {"R1", "R2"}
        assert donors and recipients
        assert not (set(donors) & set(recipients))


def test_split_combinatorics_are_capped() -> None:
    donors = tuple(f"D{i}" for i in range(10))
    recipients = tuple(f"R{i}" for i in range(10))
    splits = donor_recipient_splits(donors, recipients)
    assert len(splits) <= MAX_REALLOCATION_VIRTUAL_CANDIDATES


def test_splits_are_deterministic() -> None:
    a = donor_recipient_splits(("D2", "D1"), ("R2", "R1"))
    b = donor_recipient_splits(("D2", "D1"), ("R2", "R1"))
    assert a == b


def test_donor_and_recipient_must_both_exist() -> None:
    assert donor_recipient_splits((), ("R1",)) == ()
    assert donor_recipient_splits(("D1",), ()) == ()


# ===========================================================================
# I4/I5 -- сохранение суммарной закачки
# ===========================================================================


def _materialize(donors, recipients, fraction, controls):
    from aios.mas.magnitude_resolution.reallocation import materialize_reallocation

    return materialize_reallocation(
        donors=donors, recipients=recipients, fraction=fraction, controls=controls
    )


def test_total_commanded_injection_is_conserved() -> None:
    controls = {c.well: c for c in (_inj("D1", 200.0), _inj("D2", 100.0), _inj("R1", 50.0))}
    before = sum(c.surface_rate for c in controls.values())
    result = _materialize(("D1", "D2"), ("R1",), 0.5, controls)
    after = sum(c.surface_rate for c in result.values())
    assert after == pytest.approx(before, abs=1e-9)


def test_transfer_is_split_across_recipients() -> None:
    controls = {c.well: c for c in (_inj("D1", 200.0), _inj("R1", 0.0), _inj("R2", 0.0))}
    result = _materialize(("D1",), ("R1", "R2"), 0.5, controls)
    assert result["D1"].surface_rate == pytest.approx(100.0)
    assert result["R1"].surface_rate == pytest.approx(50.0)
    assert result["R2"].surface_rate == pytest.approx(50.0)


def test_donor_never_goes_negative() -> None:
    controls = {c.well: c for c in (_inj("D1", 10.0), _inj("R1", 0.0))}
    result = _materialize(("D1",), ("R1",), 1.0, controls)
    assert result["D1"].surface_rate >= 0.0
    assert result["R1"].surface_rate == pytest.approx(10.0)


def test_zero_transfer_is_rejected_as_non_representable() -> None:
    """Донор без закачки не может ничего отдать: кандидат не представим."""
    controls = {c.well: c for c in (_inj("D1", 0.0), _inj("R1", 100.0))}
    with pytest.raises(ReallocationError):
        _materialize(("D1",), ("R1",), 0.5, controls)


def test_unauthorized_well_is_never_modified() -> None:
    controls = {c.well: c for c in (_inj("D1", 200.0), _inj("R1", 0.0), _inj("X9", 300.0))}
    result = _materialize(("D1",), ("R1",), 0.5, controls)
    assert result["X9"].surface_rate == 300.0


def test_materialization_is_deterministic() -> None:
    controls = {c.well: c for c in (_inj("D1", 200.0), _inj("D2", 100.0), _inj("R1", 0.0))}
    a = _materialize(("D1", "D2"), ("R1",), 0.4, controls)
    b = _materialize(("D1", "D2"), ("R1",), 0.4, controls)
    assert {w: c.surface_rate for w, c in a.items()} == {w: c.surface_rate for w, c in b.items()}


# ===========================================================================
# I1/I2 -- заземляемость действия
# ===========================================================================


def test_reallocate_stays_ungroundable_without_regions() -> None:
    """I1: прежний контракт сохраняется."""
    from dataclasses import replace as dc_replace

    from aios.mas.specialists.injection.scope import groundable_injection_actions
    from aios.state import GeologyContext
    from tests.support.mas_team_fixtures import synthetic_state_card

    card = dc_replace(synthetic_state_card(), geology=GeologyContext())
    offered = groundable_injection_actions(card, tuple(InjectionAction))
    assert InjectionAction.REALLOCATE_INJECTION not in offered


def test_reallocate_becomes_groundable_with_regions_and_two_injectors() -> None:
    """I2: при реальной геологии и двух нагнетателях действие доступно."""
    from dataclasses import replace as dc_replace

    from aios.mas.specialists.injection.scope import groundable_injection_actions
    from aios.state import GeologyContext
    from tests.support.mas_team_fixtures import synthetic_state_card

    base = synthetic_state_card()
    from aios.state import WellRole

    injector = next(w for w in base.wells if w.role is WellRole.INJECTOR)
    wells = (*base.wells, dc_replace(injector, well_id="SYN-I2"))
    card = dc_replace(
        base,
        wells=wells,
        geology=GeologyContext(block_membership={w.well_id: "1" for w in wells}),
    )
    offered = groundable_injection_actions(card, tuple(InjectionAction))
    assert InjectionAction.REALLOCATE_INJECTION in offered


def test_reallocate_not_offered_when_only_one_injector_exists() -> None:
    """Перераспределять нечего: нужен и донор, и получатель."""
    from dataclasses import replace as dc_replace

    from aios.mas.specialists.injection.scope import groundable_injection_actions
    from aios.state import GeologyContext
    from tests.support.mas_team_fixtures import synthetic_state_card

    base = synthetic_state_card()
    card = dc_replace(
        base,
        geology=GeologyContext(block_membership={w.well_id: "1" for w in base.wells}),
    )
    offered = groundable_injection_actions(card, tuple(InjectionAction))
    assert InjectionAction.REALLOCATE_INJECTION not in offered


# ===========================================================================
# I20 -- анти-оверфит
# ===========================================================================


def test_no_experiment_specific_identifiers_in_reallocation_logic() -> None:
    import re
    from pathlib import Path

    source = Path("src/aios/mas/magnitude_resolution/reallocation.py").read_text(encoding="utf-8")
    for banned in ("model_y", "MODEL_Y", "EXP003", "EXP004", "state_digest"):
        assert banned not in source
    assert not re.search(r"[\"'](?:\d{1,2})[\"']\s*,\s*[\"'](?:\d{1,2})[\"']", source)


# ===========================================================================
# I7/I11/I12 -- расширение на уровне резолвера
# ===========================================================================


def _resolve_reallocation(donors, recipients, intensity=Intensity.MODERATE, rates=None):
    from aios.mas.common import CandidateKind
    from aios.mas.magnitude_resolution.engine import DeterministicMagnitudeResolver
    from tests.test_magnitude_resolver_engine import (
        _build_envelope,
        _context,
        _decision_bundle_for,
        _decision_for,
    )

    injectors = tuple(_inj(w, (rates or {}).get(w, 100.0)) for w in (*donors, *recipients))
    envelope = _build_envelope(injection_primary_intents=(_intent(donors, recipients, intensity),))
    context = _context(envelope, previous_injectors=injectors, baseline_injectors=injectors)
    resolver = DeterministicMagnitudeResolver(context=context)
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    return candidates, [c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE]


def test_one_reallocation_intent_expands_to_many_candidates() -> None:
    _all, agents = _resolve_reallocation(("D1", "D2"), ("R1", "R2"))
    assert len(agents) > 1, "one structural reallocation must expand numerically"


def test_expanded_reallocation_candidates_are_numerically_distinct() -> None:
    _all, agents = _resolve_reallocation(("D1", "D2"), ("R1", "R2"))
    vectors = {tuple(sorted((c.well, c.surface_rate) for c in cand.injectors)) for cand in agents}
    assert len(vectors) == len(agents)


def test_every_expanded_reallocation_conserves_total_injection() -> None:
    _all, agents = _resolve_reallocation(("D1", "D2"), ("R1", "R2"))
    before = 4 * 100.0
    for candidate in agents:
        after = sum(c.surface_rate for c in candidate.injectors)
        assert after == pytest.approx(before, abs=1e-9)


def test_expanded_reallocation_respects_the_simulation_budget() -> None:
    donors = tuple(f"D{i}" for i in range(4))
    recipients = tuple(f"R{i}" for i in range(4))
    allc, _agents = _resolve_reallocation(donors, recipients, Intensity.AGGRESSIVE)
    assert len(allc) <= 8


def test_expanded_reallocation_touches_only_authorized_injectors() -> None:
    _all, agents = _resolve_reallocation(("D1", "D2"), ("R1", "R2"))
    for candidate in agents:
        assert {c.well for c in candidate.injectors} == {"D1", "D2", "R1", "R2"}


def test_legacy_anchor_reallocation_is_still_produced() -> None:
    """Прежняя единственная политика остаётся внутри расширенного поиска."""
    _all, agents = _resolve_reallocation(("D1",), ("R1",), Intensity.MODERATE)
    rates = [{c.well: c.surface_rate for c in cand.injectors} for cand in agents]
    legacy = {"D1": 100.0 * (1 - 0.825), "R1": 100.0 + 100.0 * 0.825}
    assert any(all(abs(r.get(w, 0.0) - v) < 1e-9 for w, v in legacy.items()) for r in rates)


def test_expanded_reallocation_is_deterministic() -> None:
    first = _resolve_reallocation(("D1", "D2"), ("R1", "R2"))[1]
    second = _resolve_reallocation(("D1", "D2"), ("R1", "R2"))[1]
    assert [c.digest() for c in first] == [c.digest() for c in second]
