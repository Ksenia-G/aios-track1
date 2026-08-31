"""The shared NO-SETPOINTS-CROSS gate, exercised directly.

The rule is about control authority, not about digits, target keys, string
values or numbers. A magnitude encoded as a string is still a magnitude, and
an identifier containing digits is still an identifier.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from aios.diagnostics import PressureDiagnostic, SweepDiagnostic
from aios.mas.evidence import EvidenceArtifact, MemorySlice
from aios.mas.specialists.authority import find_prescriptive_control
from aios.mas.specialists.injection.evidence_builder import (
    EvidenceBuilder as InjectionEvidenceBuilder,
)
from aios.mas.specialists.injection.evidence_builder import (
    EvidenceBuildError as InjectionEvidenceBuildError,
)
from aios.mas.specialists.production.errors import (
    EvidenceBuildError as ProductionEvidenceBuildError,
)
from aios.mas.specialists.production.evidence import EvidenceBuilder as ProductionEvidenceBuilder
from aios.state import ConstraintState, StateCard
from tests.support.mas_team_fixtures import (
    synthetic_injection_envelope,
    synthetic_production_envelope,
    synthetic_state_card,
)

# --- numerical control, however it is encoded -------------------------------

PRESCRIPTIVE_PAYLOADS = [
    {"target": 240},
    {"target": "240"},
    {"target": "240 m3/day"},
    {"target": "BHP 240 bar"},
    {"target": "SYN-I1 at 240 m3/day"},
    {"recommended_rate": "240 m3/day"},
    {"setpoint": "240"},
    {"command": "240 m3/day"},
    {"note": "Set injection to 240 m3/day."},
    {"note": "Use BHP 240 bar."},
    {"note": "Increase injection by 20 percent."},
    {"note": "WCONINJE OPEN 240"},
    # nested and list-borne variants
    {"metadata": {"target": "240 m3/day"}},
    {"items": [{"reason": "Pressure weakening"}, {"note": "Set BHP to 240 bar"}]},
    {"outer": {"inner": [{"setpoint": "300 bar"}]}},
    # AUTH-2: bare numeric magnitude wrapped in a list under a prescriptive key
    {"target": [240]},
    # AUTH-3: unit-bearing numeric string wrapped in a list
    {"target": ["240 m3/day"]},
    # AUTH-4: numeric magnitude nested through a dict inside a list
    {"target": [{"value": 240}]},
    # nested list-of-list magnitude, same shape one level deeper
    {"target": [{"value": [240]}]},
    # AUTH-5: unit-bearing string nested two containers deep
    {"target": [{"nested": ["240 m3/day"]}]},
    # AUTH-9: mixed container -- a legitimate identifier alongside a setpoint
    {"target": ["SYN-I1", 240]},
    # AUTH-10: recursive bypass through several layers of supported nesting
    {"outer": {"target": [{"a": [{"b": [{"value": "300 bar"}]}]}]}},
]

# --- facts, identifiers and measurements ------------------------------------

OBSERVATIONAL_PAYLOADS = [
    {"target": "SYN-I1"},
    {"target": "SYN-P1"},
    {"target": "SYN-B2"},
    {"target": ["SYN-I1", "SYN-I2"]},
    {"well_id": "SYN-I1"},
    {"note": "Observed BHP is 55 bar."},
    {"note": "Pressure declined by 12 bar during the interval."},
    {"confidence": 0.82},
    {"pressure": 240.0},
    {"wlpr": 420.0},
    {"reason": "Pressure support around SYN-I1 is weakening."},
    {"metadata": {"target": "SYN-B2"}},
    {"items": [{"target": "SYN-I1"}, {"reason": "Sweep is poor in SYN-B2"}]},
    # AUTH-6/7: authorized well-ID lists and region/evidence identifiers, unaffected
    {"target": ["SYN-I1", "SYN-I2", "SYN-I3"]},
    {"target_regions": ["BLOCK-12", "BLOCK-7"]},
    {"evidence_ids": ["e1", "e2"]},
    # AUTH-8: observational numerical state, however it is nested
    {"wwir": 1500.0},
    {"bhp": 211.5},
    {"wwct": 0.42},
    {"margins": [0.12, 0.08]},
    {"evidence": {"measured": {"bhp": 211.5, "wlpr": 320.0}}},
    {"observed": {"items": [{"wwir": 1500.0}, {"bhp": 211.5}]}},
]


@pytest.mark.parametrize("payload", PRESCRIPTIVE_PAYLOADS)
def test_numerical_control_is_refused_however_encoded(payload: dict) -> None:
    assert find_prescriptive_control(payload) is not None


@pytest.mark.parametrize("payload", OBSERVATIONAL_PAYLOADS)
def test_facts_and_identifiers_are_allowed(payload: dict) -> None:
    assert find_prescriptive_control(payload) is None


def test_real_diagnostics_carry_no_prescriptive_control() -> None:
    card = synthetic_state_card()
    assert find_prescriptive_control(PressureDiagnostic().propose(card).to_json()) is None
    assert find_prescriptive_control(SweepDiagnostic().propose(card).to_json()) is None


# --- Production StateCard-derived route --------------------------------------


def _card_with_search(search: dict[str, object]) -> StateCard:
    card = synthetic_state_card()
    return replace(
        card,
        constraints=ConstraintState(official=dict(card.constraints.official), search=search),
    )


def test_production_statecard_search_refuses_a_string_encoded_setpoint() -> None:
    envelope = synthetic_production_envelope(state_card=_card_with_search({"target": "240 m3/day"}))
    with pytest.raises(ProductionEvidenceBuildError, match="prescriptive numerical control"):
        ProductionEvidenceBuilder().build(envelope)


def test_production_statecard_search_refuses_a_container_wrapped_setpoint() -> None:
    """AUTH-11: the container bypass is closed on the Production route too."""
    envelope = synthetic_production_envelope(state_card=_card_with_search({"target": [240]}))
    with pytest.raises(ProductionEvidenceBuildError, match="prescriptive numerical control"):
        ProductionEvidenceBuilder().build(envelope)


@pytest.mark.parametrize(
    "search",
    [
        {"target": "SYN-P1"},
        {"note": "Observed BHP is 55 bar"},
        {"producer_lrat_max": 500.0},
    ],
)
def test_production_statecard_search_keeps_facts(search: dict[str, object]) -> None:
    envelope = synthetic_production_envelope(state_card=_card_with_search(search))
    evidence = ProductionEvidenceBuilder().build(envelope)
    assert "production.state.constraints" in evidence.evidence_ids


# --- Injection EvidenceArtifact route ----------------------------------------


def _injection_envelope_with(payload: dict[str, object]) -> object:
    return synthetic_injection_envelope(
        response_map=EvidenceArtifact(evidence_id="SYN-probe", kind="response_map", payload=payload)
    )


def test_injection_evidence_refuses_a_string_encoded_setpoint() -> None:
    with pytest.raises(InjectionEvidenceBuildError):
        InjectionEvidenceBuilder().build(_injection_envelope_with({"target": "240 m3/day"}))


def test_injection_evidence_refuses_a_container_wrapped_setpoint() -> None:
    """AUTH-11: the container bypass is closed on the Injection route too."""
    with pytest.raises(InjectionEvidenceBuildError):
        InjectionEvidenceBuilder().build(_injection_envelope_with({"target": [240]}))


def test_injection_evidence_refuses_a_mixed_identifier_and_setpoint_list() -> None:
    """AUTH-9: a legitimate well-ID list must not smuggle a setpoint alongside it."""
    with pytest.raises(InjectionEvidenceBuildError):
        InjectionEvidenceBuilder().build(
            _injection_envelope_with({"target": ["SYN-I1", "240 m3/day"]})
        )


def test_injection_evidence_keeps_a_target_identifier() -> None:
    context = InjectionEvidenceBuilder().build(_injection_envelope_with({"target": "SYN-I1"}))
    assert "SYN-probe" in context.known_ids


def test_injection_memory_route_is_protected_too() -> None:
    envelope = synthetic_injection_envelope(
        memory_slice=MemorySlice(
            records=(
                EvidenceArtifact(
                    evidence_id="SYN-mem",
                    kind="memory",
                    payload={"target": "240 m3/day"},
                ),
            )
        )
    )
    with pytest.raises(InjectionEvidenceBuildError):
        InjectionEvidenceBuilder().build(envelope)


def test_sanitized_diagnostic_substance_still_reaches_the_prompt() -> None:
    """Closing the leak must not starve the specialist again."""
    import json

    from aios.mas.specialists.injection.prompt import PromptBuilder

    card = synthetic_state_card()
    diagnostic = PressureDiagnostic().propose(card)
    envelope = synthetic_injection_envelope(
        pressure_diagnostic=EvidenceArtifact(
            evidence_id="SYN-real-pressure",
            kind="pressure_diagnostic",
            payload=diagnostic.to_json(),
        )
    )
    payload = json.loads(PromptBuilder().build(envelope))
    body = {item["evidence_id"]: item for item in payload["evidence"]}["SYN-real-pressure"]
    assert body["payload"]["reason"]["pressure_condition"] == diagnostic.reason.pressure_condition
    assert body["payload"]["confidence"] == diagnostic.confidence
