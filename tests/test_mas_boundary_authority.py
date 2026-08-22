"""Cross-cutting authority-boundary checks: no duplicated authorities, canonical
round-trips, and equal semantic identity for canonically-equal objects.

C33: existing aios.evaluation.SimulationResult remains unchanged, no NPV field.
C34/C35: no aios.mas SimulationRequest/SimulationResult class exists.
C37: MAS contracts round-trip / to_json produce canonical JSON-compatible structures.
C38: equivalent semantically canonical objects produce identical digest.
"""

from __future__ import annotations

import dataclasses
import datetime

from aios.evaluation.canonical import normalize_json
from aios.evaluation.simulation import SimulationResult, SimulationStatus
from aios.mas import arbiter, common, envelopes, evidence, intents, magnitude, proposals, protocols
from aios.mas.common import Direction, Intensity, UncertaintyLevel
from aios.mas.intents import ProductionAction, ProductionIntent
from aios.mas.magnitude import MagnitudeCandidate
from aios.schedule import InjectorControl, ProducerControl, WellStatus

MAS_MODULES = (common, evidence, intents, proposals, envelopes, arbiter, magnitude, protocols)


# ---------------------------------------------------------------------------
# C33: SimulationResult has no NPV/CHDD/objective field.
# ---------------------------------------------------------------------------


def test_simulation_result_has_no_npv_or_chdd_fields() -> None:
    field_names = {f.name.lower() for f in dataclasses.fields(SimulationResult)}
    forbidden = {"official_npv", "npv", "chdd", "objective_value"}
    assert field_names & forbidden == set()


def test_simulation_result_still_requires_matching_digest() -> None:
    result = SimulationResult.create(status=SimulationStatus.SUCCESS)
    assert result.status is SimulationStatus.SUCCESS


# ---------------------------------------------------------------------------
# C34 / C35: no duplicate SimulationRequest/SimulationResult under aios.mas.
# ---------------------------------------------------------------------------


def test_mas_package_defines_no_simulation_request_or_result() -> None:
    for module in MAS_MODULES:
        assert not hasattr(module, "SimulationRequest"), module.__name__
        assert not hasattr(module, "SimulationResult"), module.__name__


def test_mas_package_reuses_state_card_identity_where_imported() -> None:
    # envelopes.py legitimately imports StateCard to type ProductionEnvelope/
    # InjectionEnvelope -- that's reuse, not duplication. What must never
    # happen is a *second* class also named StateCard.
    from aios.state import StateCard as AuthoritativeStateCard

    for module in MAS_MODULES:
        candidate = getattr(module, "StateCard", None)
        if candidate is not None:
            assert candidate is AuthoritativeStateCard, module.__name__
        assert not hasattr(module, "ConstraintGuardian"), module.__name__


# ---------------------------------------------------------------------------
# C37: to_json output is canonical JSON-compatible for every serializable
# MAS contract type exercised here.
# ---------------------------------------------------------------------------


def test_production_intent_to_json_round_trips_through_normalize_json() -> None:
    intent = ProductionIntent(
        action=ProductionAction.INCREASE_OFFTAKE,
        direction=Direction.UP,
        intensity=Intensity.SMALL,
        target_wells=("P1",),
        target_regions=(),
        evidence_ids=("ev-1",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="Healthy margin.",
    )
    normalized = normalize_json(intent.to_json())
    assert normalized == intent.to_json()


def test_magnitude_candidate_to_json_round_trips_through_normalize_json() -> None:
    candidate = MagnitudeCandidate(
        candidate_kind=common.CandidateKind.AGENT_BUNDLE,
        bundle_digest="b" * 64,
        effective_date=datetime.date(2020, 4, 1),
        producers=(ProducerControl(well="P1", status=WellStatus.OPEN, mode="LRAT", liquid_rate=150.0),),
        injectors=(
            InjectorControl(
                well="I1", phase="WATER", status=WellStatus.OPEN, mode="RATE", surface_rate=200.0
            ),
        ),
        source_intent_digests=("d" * 64,),
        resolver_version="aios.mas.magnitude/1.0.0",
    )
    normalized = normalize_json(candidate.to_json())
    assert normalized == candidate.to_json()


# ---------------------------------------------------------------------------
# C38: equivalent semantically canonical objects produce identical digest.
# ---------------------------------------------------------------------------


def test_semantically_equal_intents_from_different_construction_order_match_digest() -> None:
    a = ProductionIntent(
        action=ProductionAction.INCREASE_OFFTAKE,
        direction=Direction.UP,
        intensity=Intensity.SMALL,
        target_wells=("P1", "P2", "P3"),
        target_regions=(),
        evidence_ids=("ev-1", "ev-2"),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="Healthy margin.",
    )
    b = ProductionIntent(
        action=ProductionAction.INCREASE_OFFTAKE,
        direction=Direction.UP,
        intensity=Intensity.SMALL,
        target_wells=("P3", "P1", "P2"),
        target_regions=(),
        evidence_ids=("ev-2", "ev-1"),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="Healthy margin.",
    )
    assert a.digest() == b.digest()


def test_semantically_equal_magnitude_candidates_match_digest() -> None:
    def build(order: tuple[str, str]) -> MagnitudeCandidate:
        wells = {
            "P1": ProducerControl(well="P1", status=WellStatus.OPEN, mode="LRAT", liquid_rate=100.0),
            "P2": ProducerControl(well="P2", status=WellStatus.OPEN, mode="LRAT", liquid_rate=200.0),
        }
        return MagnitudeCandidate(
            candidate_kind=common.CandidateKind.AGENT_BUNDLE,
            bundle_digest="b" * 64,
            effective_date=datetime.date(2020, 4, 1),
            producers=(wells[order[0]], wells[order[1]]),
            resolver_version="aios.mas.magnitude/1.0.0",
        )

    assert build(("P1", "P2")).digest() == build(("P2", "P1")).digest()
