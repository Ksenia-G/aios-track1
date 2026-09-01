"""System invariants T1, T3-T7 for Hybrid MAS v1.1.

These are deterministic guards, not a finished pipeline. Each guard exists so
that a *future* component (PromptBuilder, MagnitudeResolver, PredictiveSafety
Screen, Orchestrator, HOLD carry-forward stage) is REQUIRED to call it -- the
guard itself does not run inside any pipeline yet, because none of those
components exist. T2 (no setpoints cross) authority audit lives in
tests/test_mas_authority_invariant.py.
"""

from __future__ import annotations

import dataclasses
import datetime

import pytest

from aios.mas.common import CandidateKind
from aios.mas.envelopes import InjectionEnvelope, ProductionEnvelope
from aios.mas.invariants import (
    DECISION_LAYER_TYPES,
    MasInvariantError,
    canonical_control_digest,
    canonical_control_snapshot,
    require_ablation_equivalence,
    require_candidate_preservation,
    require_hold_carry_forward,
    require_no_baseline_field,
    require_no_numerical_controls_before_magnitude,
    require_simulation_floor,
)
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.protocols import SpecialistPromptBuilder
from aios.schedule import InjectorControl, ProducerControl, WellStatus


def _producer(well: str = "P1", liquid_rate: float = 150.0) -> ProducerControl:
    return ProducerControl(well=well, status=WellStatus.OPEN, mode="LRAT", liquid_rate=liquid_rate)


def _injector(well: str = "I1", surface_rate: float = 200.0) -> InjectorControl:
    return InjectorControl(
        well=well, phase="WATER", status=WellStatus.OPEN, mode="RATE", surface_rate=surface_rate
    )


def _candidate(kind: CandidateKind, **overrides: object) -> MagnitudeCandidate:
    fields = {
        "candidate_kind": kind,
        "bundle_digest": "b" * 64,
        "effective_date": datetime.date(2020, 4, 1),
        "resolver_version": "aios.mas.magnitude/1.0.0",
    }
    fields.update(overrides)
    return MagnitudeCandidate(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T1 -- anti-anchoring (I1, I2, I3)
# ---------------------------------------------------------------------------


def test_i1_production_envelope_has_no_baseline_field() -> None:
    field_names = {f.name for f in dataclasses.fields(ProductionEnvelope)}
    assert "baseline" not in field_names
    assert "baseline_proposal" not in field_names


def test_i2_injection_envelope_has_no_baseline_field() -> None:
    field_names = {f.name for f in dataclasses.fields(InjectionEnvelope)}
    assert "baseline" not in field_names
    assert "baseline_proposal" not in field_names


def test_i3_specialist_prompt_builder_boundary_takes_only_envelope() -> None:
    import inspect

    signature = inspect.signature(SpecialistPromptBuilder.build)
    params = [name for name in signature.parameters if name != "self"]
    assert params == ["envelope"], "build() must accept exactly one argument: envelope"


def test_require_no_baseline_field_accepts_clean_envelope() -> None:
    require_no_baseline_field(ProductionEnvelope)
    require_no_baseline_field(InjectionEnvelope)


def test_require_no_baseline_field_rejects_extended_type_with_baseline() -> None:
    @dataclasses.dataclass(frozen=True, slots=True)
    class _LeakyEnvelope:
        baseline_proposal: object

    with pytest.raises(MasInvariantError):
        require_no_baseline_field(_LeakyEnvelope)


# ---------------------------------------------------------------------------
# T2 (non-AST half) -- I4, I6
# ---------------------------------------------------------------------------


def test_i4_decision_layer_types_carry_no_numerical_control_fields() -> None:
    require_no_numerical_controls_before_magnitude()
    for cls in DECISION_LAYER_TYPES:
        for f in dataclasses.fields(cls):
            type_str = str(f.type)
            assert "ProducerControl" not in type_str
            assert "InjectorControl" not in type_str


def test_i6_magnitude_candidate_remains_the_allowed_numerical_boundary() -> None:
    field_names = {f.name for f in dataclasses.fields(MagnitudeCandidate)}
    assert "producers" in field_names
    assert "injectors" in field_names


# ---------------------------------------------------------------------------
# T3 -- simulation floor (I7-I10)
# ---------------------------------------------------------------------------


def test_i7_simulation_floor_accepts_hold_and_baseline() -> None:
    require_simulation_floor(
        (
            _candidate(CandidateKind.HOLD),
            _candidate(CandidateKind.DETERMINISTIC_BASELINE),
        )
    )


def test_i8_simulation_floor_rejects_missing_hold() -> None:
    with pytest.raises(MasInvariantError):
        require_simulation_floor((_candidate(CandidateKind.DETERMINISTIC_BASELINE),))


def test_i9_simulation_floor_rejects_missing_deterministic_baseline() -> None:
    with pytest.raises(MasInvariantError):
        require_simulation_floor((_candidate(CandidateKind.HOLD),))


def test_i10_simulation_floor_allows_extra_agent_bundle_candidates() -> None:
    require_simulation_floor(
        (
            _candidate(CandidateKind.HOLD),
            _candidate(CandidateKind.DETERMINISTIC_BASELINE),
            _candidate(CandidateKind.AGENT_BUNDLE),
            _candidate(CandidateKind.AGENT_BUNDLE),
        )
    )


# ---------------------------------------------------------------------------
# T4 -- candidate preservation (I11-I14)
# ---------------------------------------------------------------------------


def test_i11_preservation_accepts_reordered_identities() -> None:
    require_candidate_preservation(before_ids=("a", "b", "c"), after_ids=("c", "a", "b"))


def test_i12_preservation_rejects_dropped_candidate() -> None:
    with pytest.raises(MasInvariantError):
        require_candidate_preservation(before_ids=("a", "b", "c"), after_ids=("a", "b"))


def test_i13_preservation_rejects_added_candidate() -> None:
    with pytest.raises(MasInvariantError):
        require_candidate_preservation(before_ids=("a", "b"), after_ids=("a", "b", "c"))


def test_i14_preservation_rejects_duplicate_identity() -> None:
    with pytest.raises(MasInvariantError):
        require_candidate_preservation(before_ids=("a", "b"), after_ids=("a", "a"))


# ---------------------------------------------------------------------------
# T5 -- fair LLM ablation (I15-I20)
# ---------------------------------------------------------------------------


def _config(**overrides: object) -> dict[str, object]:
    base = {
        "variant_id": "variant-A",
        "llm_enabled": True,
        "simulation_budget": 8,
        "resolver_version": "aios.mas.magnitude/1.0.0",
        "guardian_config_digest": "g" * 64,
        "chdd_config_digest": "c" * 64,
    }
    base.update(overrides)
    return base


def test_i15_ablation_accepts_configs_differing_only_by_llm_enabled() -> None:
    require_ablation_equivalence(_config(llm_enabled=True), _config(llm_enabled=False))


def test_i16_ablation_rejects_simulation_budget_difference() -> None:
    with pytest.raises(MasInvariantError):
        require_ablation_equivalence(
            _config(llm_enabled=True), _config(llm_enabled=False, simulation_budget=16)
        )


def test_i17_ablation_rejects_resolver_version_difference() -> None:
    with pytest.raises(MasInvariantError):
        require_ablation_equivalence(
            _config(llm_enabled=True),
            _config(llm_enabled=False, resolver_version="aios.mas.magnitude/2.0.0"),
        )


def test_i18_ablation_rejects_candidate_budget_style_field_difference() -> None:
    with pytest.raises(MasInvariantError):
        require_ablation_equivalence(
            _config(llm_enabled=True, max_candidates=8),
            _config(llm_enabled=False, max_candidates=16),
        )


def test_i19_ablation_rejects_missing_llm_enabled_key() -> None:
    config_a = _config()
    config_b = _config()
    del config_b["llm_enabled"]
    with pytest.raises(MasInvariantError):
        require_ablation_equivalence(config_a, config_b)


def test_i20_ablation_rejects_any_other_ignore_key_shortcut() -> None:
    # An extra key present only on one side (beyond llm_enabled) must be
    # rejected -- no second ignore-key is permitted implicitly.
    with pytest.raises(MasInvariantError):
        require_ablation_equivalence(
            _config(llm_enabled=True),
            {**_config(llm_enabled=False), "mpc_tier": "TIER_1"},
        )


# ---------------------------------------------------------------------------
# T6 -- HOLD carry-forward (I21-I24)
# ---------------------------------------------------------------------------


def test_i21_hold_carry_forward_accepts_exact_reordered_controls() -> None:
    require_hold_carry_forward(
        previous_producers=(_producer("P1"), _producer("P2")),
        previous_injectors=(_injector("I1"),),
        held_producers=(_producer("P2"), _producer("P1")),
        held_injectors=(_injector("I1"),),
    )


def test_i22_hold_carry_forward_rejects_changed_producer_value() -> None:
    with pytest.raises(MasInvariantError):
        require_hold_carry_forward(
            previous_producers=(_producer("P1", liquid_rate=150.0),),
            previous_injectors=(),
            held_producers=(_producer("P1", liquid_rate=151.0),),
            held_injectors=(),
        )


def test_i23_hold_carry_forward_rejects_changed_injector_value() -> None:
    with pytest.raises(MasInvariantError):
        require_hold_carry_forward(
            previous_producers=(),
            previous_injectors=(_injector("I1", surface_rate=200.0),),
            held_producers=(),
            held_injectors=(_injector("I1", surface_rate=201.0),),
        )


def test_i24_hold_carry_forward_rejects_role_movement() -> None:
    with pytest.raises(MasInvariantError):
        require_hold_carry_forward(
            previous_producers=(_producer("W1"),),
            previous_injectors=(),
            held_producers=(),
            held_injectors=(_injector("W1"),),
        )


def test_canonical_control_snapshot_rejects_ambiguous_duplicate_well() -> None:
    with pytest.raises(MasInvariantError):
        canonical_control_snapshot(
            producers=(_producer("P1", liquid_rate=100.0), _producer("P1", liquid_rate=200.0)),
            injectors=(),
        )


def test_canonical_control_snapshot_dedups_exact_literal_repeats() -> None:
    snapshot_repeated = canonical_control_snapshot(
        producers=(_producer("P1"), _producer("P1")), injectors=()
    )
    snapshot_single = canonical_control_snapshot(producers=(_producer("P1"),), injectors=())
    assert snapshot_repeated == snapshot_single


def test_canonical_control_snapshot_rejects_well_as_both_roles() -> None:
    with pytest.raises(MasInvariantError):
        canonical_control_snapshot(producers=(_producer("W1"),), injectors=(_injector("W1"),))


# ---------------------------------------------------------------------------
# T7 -- canonical identity (I25-I27)
# ---------------------------------------------------------------------------


def test_i25_canonical_digest_ignores_control_ordering() -> None:
    a = canonical_control_digest(
        producers=(_producer("P1"), _producer("P2")), injectors=(_injector("I1"),)
    )
    b = canonical_control_digest(
        producers=(_producer("P2"), _producer("P1")), injectors=(_injector("I1"),)
    )
    assert a == b


def test_i26_canonical_digest_changes_with_control_value() -> None:
    a = canonical_control_digest(producers=(_producer("P1", liquid_rate=150.0),), injectors=())
    b = canonical_control_digest(producers=(_producer("P1", liquid_rate=151.0),), injectors=())
    assert a != b


def test_i27_canonical_snapshot_is_json_compatible() -> None:
    from aios.evaluation.canonical import normalize_json

    snapshot = canonical_control_snapshot(
        producers=(_producer("P1"),), injectors=(_injector("I1"),)
    )
    assert normalize_json(snapshot) == snapshot


# ---------------------------------------------------------------------------
# I28 -- all invariant errors carry a short deterministic explanation.
# ---------------------------------------------------------------------------


def test_i28_invariant_errors_carry_short_deterministic_explanation() -> None:
    with pytest.raises(MasInvariantError) as excinfo:
        require_simulation_floor((_candidate(CandidateKind.HOLD),))
    message = str(excinfo.value)
    assert message
    assert len(message) < 300
    assert "\n" not in message
