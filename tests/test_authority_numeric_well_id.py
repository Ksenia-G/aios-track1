"""Numeric well identifiers are identifiers, not setpoints.

The Model Y deck names its wells with bare numbers (`"2"`, `"13"`, `"21"`).
`PressureDiagnostic`/`SweepDiagnostic` emit the wells a fact is *about*, and
the recursive NO-SETPOINTS-CROSS guard read those numeric identifiers as
prescriptive magnitudes because they sat under the ambiguous key `target`.

The fix is semantic, not lexical: an observational diagnostic names its
subject wells under an identifier field (`well_ids`), and the guard stays
exactly as strict as before for genuine `target` control contexts. Nothing
here may make a digit-only string legal *because* it is a string -- see
`test_generic_digit_only_string_under_target_stays_refused`.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from aios.diagnostics import PressureDiagnostic, SweepDiagnostic
from aios.mas.evidence import EvidenceArtifact
from aios.mas.orchestration import build_decision_context
from aios.mas.specialists.authority import find_prescriptive_control
from aios.mas.specialists.injection.controller import InjectionController
from aios.mas.specialists.injection.evidence_builder import (
    EvidenceBuilder as InjectionEvidenceBuilder,
)
from aios.mas.specialists.production.controller import ProductionController
from aios.mas.specialists.production.errors import (
    EvidenceBuildError as ProductionEvidenceBuildError,
)
from aios.mas.specialists.production.evidence import EvidenceBuilder as ProductionEvidenceBuilder
from aios.state import StateCard, WellRole
from tests.support.mas_team_fixtures import (
    synthetic_injection_envelope,
    synthetic_production_envelope,
    synthetic_state_card,
)

#: Имена скважин реальной колоды compat_p002: голые числа, не SYN-*.
MODEL_Y_PRODUCER = "13"
MODEL_Y_INJECTOR = "21"
MODEL_Y_SECOND_INJECTOR = "2"


def _renamed_wells(card: StateCard, mapping: dict[str, str]) -> StateCard:
    """Тот же StateCard с другими именами скважин -- и только с ними."""
    wells = tuple(
        replace(well, well_id=mapping.get(well.well_id, well.well_id)) for well in card.wells
    )
    geology = replace(
        card.geology,
        block_membership={
            mapping.get(well_id, well_id): block
            for well_id, block in card.geology.block_membership.items()
        },
    )
    return replace(card, wells=wells, geology=geology)


def _model_y_card() -> StateCard:
    """Карта состояния с числовыми именами скважин и активной диагностикой.

    Производитель прижат к BHP-пределу, а инжектор -- нет: это ветка
    PRESSURE_SUPPORT_NEEDED, единственная, у которой список скважин непустой.
    Пустой список прошёл бы guard и при исходном баге, поэтому тест обязан
    стоять именно на непустом.
    """
    card = _renamed_wells(
        synthetic_state_card(),
        {"SYN-P1": MODEL_Y_PRODUCER, "SYN-I1": MODEL_Y_INJECTOR},
    )
    wells = tuple(
        replace(well, producer_bhp_margin=1.0, wct=0.95, wlpr_margin=1.0)
        if well.role is WellRole.PRODUCER
        else well
        for well in card.wells
    )
    return replace(card, wells=wells)


def _three_well_model_y_card() -> StateCard:
    """Колода из трёх скважин с именами "13", "21", "2" -- как в compat_p002."""
    card = _model_y_card()
    injector = next(well for well in card.wells if well.role is WellRole.INJECTOR)
    second = replace(injector, well_id=MODEL_Y_SECOND_INJECTOR)
    geology = replace(
        card.geology,
        block_membership={
            **card.geology.block_membership,
            MODEL_Y_SECOND_INJECTOR: card.geology.block_membership[MODEL_Y_INJECTOR],
        },
    )
    return replace(card, wells=(*card.wells, second), geology=geology)


def _symbolic_card() -> StateCard:
    card = synthetic_state_card()
    wells = tuple(
        replace(well, producer_bhp_margin=1.0) if well.role is WellRole.PRODUCER else well
        for well in card.wells
    )
    return replace(card, wells=wells)


def _pressure_artifact(card: StateCard) -> EvidenceArtifact:
    return EvidenceArtifact(
        evidence_id=f"diagnostic.pressure.{card.metadata.date.isoformat()}",
        kind="pressure_diagnostic",
        payload=PressureDiagnostic().propose(card).to_json(),
    )


def _sweep_artifact(card: StateCard) -> EvidenceArtifact:
    return EvidenceArtifact(
        evidence_id=f"diagnostic.sweep.{card.metadata.date.isoformat()}",
        kind="sweep_diagnostic",
        payload=SweepDiagnostic().propose(card).to_json(),
    )


def _production_envelope(card: StateCard):
    return synthetic_production_envelope(
        state_card=card,
        pressure_diagnostic=_pressure_artifact(card),
        sweep_diagnostic=_sweep_artifact(card),
    )


def _injection_envelope(card: StateCard):
    return synthetic_injection_envelope(
        state_card=card,
        pressure_diagnostic=_pressure_artifact(card),
        sweep_diagnostic=_sweep_artifact(card),
    )


# --- A1 / A2: the real diagnostic, admitted through the real builder --------


def test_numeric_well_ids_are_admitted_through_production_evidence_builder() -> None:
    """A1 -- RED before the fix: `target[0] == "13"` read as a setpoint."""
    card = _model_y_card()
    proposal = PressureDiagnostic().propose(card)
    assert proposal.well_ids == (MODEL_Y_INJECTOR,), "fixture must exercise a non-empty subject"

    evidence = ProductionEvidenceBuilder().build(_production_envelope(card))

    assert f"diagnostic.pressure.{card.metadata.date.isoformat()}" in evidence.evidence_ids


def test_symbolic_well_ids_are_still_admitted() -> None:
    """A2 -- the previously working case must not regress."""
    card = _symbolic_card()
    evidence = ProductionEvidenceBuilder().build(_production_envelope(card))

    assert f"diagnostic.pressure.{card.metadata.date.isoformat()}" in evidence.evidence_ids


# --- A3-A8, A14: the guard stays exactly as strict --------------------------


GENUINE_SETPOINT_PAYLOADS = [
    pytest.param({"target": 240}, id="A3-bare-number"),
    pytest.param({"target": [240]}, id="A4-number-in-list"),
    pytest.param({"target": ["240 m3/day"]}, id="A5-unit-bearing-string"),
    pytest.param({"target": ["240"]}, id="A6-ambiguous-digit-only-string"),
    pytest.param({"target": [{"value": [240]}]}, id="A7-nested-numeric-control"),
    pytest.param({"target": ["SYN-I1", 240]}, id="A8-identifier-mixed-with-control"),
    pytest.param({"target": [MODEL_Y_INJECTOR, 240]}, id="A8-numeric-id-mixed-with-control"),
]


@pytest.mark.parametrize("payload", GENUINE_SETPOINT_PAYLOADS)
def test_genuine_prescriptive_control_is_still_refused(payload: dict) -> None:
    assert find_prescriptive_control(payload) is not None


def test_generic_digit_only_string_under_target_stays_refused() -> None:
    """Phase 2 -- the information-theoretic core of this milestone.

    `{"target": ["13"]}` is lexically indistinguishable from `{"target":
    ["240"]}`: a bare digit string carries no evidence of whether it names a
    well or commands a rate. The common authority layer sees no type and no
    section, so it must stay fail-closed for BOTH. The bug is fixed by making
    the diagnostic say what it means, never by teaching this scanner to guess.
    """
    assert find_prescriptive_control({"target": ["13"]}) is not None
    assert find_prescriptive_control({"target": ["240"]}) is not None
    assert find_prescriptive_control({"target": "13"}) is not None


def test_no_global_exemption_for_digit_only_strings() -> None:
    """A14 -- digit-only strings stay illegal under every prescriptive key."""
    for key in ("target", "set", "new", "recommended", "proposed", "assign"):
        assert find_prescriptive_control({key: ["13"]}) is not None, key
        assert find_prescriptive_control({key: "13"}) is not None, key


# --- A9: observational numbers remain legal ---------------------------------


OBSERVATIONAL_PAYLOADS = [
    pytest.param({"bhp": 174.2}, id="bhp"),
    pytest.param({"wwir": 1500.0}, id="wwir"),
    pytest.param({"wwct": 0.91}, id="wwct"),
    pytest.param({"wlpr": 420.0}, id="wlpr"),
    pytest.param({"margins": [0.12, 0.08]}, id="margins"),
    pytest.param({"pressure": 211.5}, id="pressure"),
    pytest.param({"well_ids": [MODEL_Y_PRODUCER, MODEL_Y_INJECTOR]}, id="numeric-well-ids"),
    pytest.param({"well_ids": ["SYN-I1", "SYN-I2"]}, id="symbolic-well-ids"),
]


@pytest.mark.parametrize("payload", OBSERVATIONAL_PAYLOADS)
def test_observational_values_remain_legal(payload: dict) -> None:
    assert find_prescriptive_control(payload) is None


# --- A10: the complete diagnostic -> evidence path --------------------------


def test_full_diagnostic_to_evidence_path_preserves_numeric_identifiers() -> None:
    """A10 -- PressureDiagnostic -> payload -> admit -> evidence catalog."""
    card = _model_y_card()
    artifact = _pressure_artifact(card)

    assert artifact.payload["well_ids"] == [MODEL_Y_INJECTOR]
    assert "target" not in artifact.payload
    assert all(isinstance(item, str) for item in artifact.payload["well_ids"])

    evidence = ProductionEvidenceBuilder().build(_production_envelope(card))
    admitted = {item.evidence_id: item for item in evidence.artifacts}
    payload = admitted[artifact.evidence_id].payload

    assert payload["well_ids"] == [MODEL_Y_INJECTOR]


def test_sweep_diagnostic_carries_numeric_identifiers_too() -> None:
    """The same ambiguous field existed in SweepDiagnostic -- latent, not hit."""
    card = _model_y_card()
    proposal = SweepDiagnostic().propose(card)

    assert proposal.well_ids == (MODEL_Y_PRODUCER,)
    assert find_prescriptive_control(proposal.to_json()) is None


def test_real_numeric_diagnostics_carry_no_prescriptive_control() -> None:
    card = _model_y_card()

    assert find_prescriptive_control(PressureDiagnostic().propose(card).to_json()) is None
    assert find_prescriptive_control(SweepDiagnostic().propose(card).to_json()) is None


# --- A11 / A12: both specialists consume the evidence ------------------------


def test_production_specialist_consumes_numeric_well_id_evidence() -> None:
    """A11 -- deterministic path only; no provider call."""
    output = ProductionController(llm_enabled=False).propose(_production_envelope(_model_y_card()))

    assert output.primary_proposal is not None


def test_injection_specialist_consumes_numeric_well_id_evidence() -> None:
    """A12 -- the injection builder shares the same authority boundary."""
    card = _model_y_card()
    InjectionEvidenceBuilder().build(_injection_envelope(card))

    output = InjectionController(llm_enabled=False).propose(_injection_envelope(card))

    assert output.primary_proposal is not None


# --- real-deck regression: the exact production path that failed -------------


def test_t0_decision_context_admits_a_three_well_numeric_deck() -> None:
    """Тот самый путь, который упал в PERF-004, без OPM и без провайдера.

    `build_decision_context` -- строитель эпохи, из которого приходят слоты
    `pressure_diagnostic`/`sweep_diagnostic`; именно его результат отклонял
    EvidenceBuilder на T0 (2014-01-01). Скважины названы как в compat_p002.
    """
    card = _three_well_model_y_card()
    assert [well.well_id for well in card.wells] == [
        MODEL_Y_PRODUCER,
        MODEL_Y_INJECTOR,
        MODEL_Y_SECOND_INJECTOR,
    ]
    context = build_decision_context(card)

    pressure = context["pressure_diagnostic"]
    assert pressure.payload["well_ids"] == [MODEL_Y_INJECTOR, MODEL_Y_SECOND_INJECTOR]
    assert "target" not in pressure.payload
    assert "target" not in context["sweep_diagnostic"].payload

    # Обе стороны границы принимают контекст, и идентификаторы не тронуты.
    production = ProductionEvidenceBuilder().build(
        synthetic_production_envelope(state_card=card, **context)
    )
    InjectionEvidenceBuilder().build(synthetic_injection_envelope(state_card=card, **context))

    admitted = {item.evidence_id: item for item in production.artifacts}
    surviving = admitted[pressure.evidence_id].payload["well_ids"]
    assert surviving == [MODEL_Y_INJECTOR, MODEL_Y_SECOND_INJECTOR]
    assert all(isinstance(item, str) for item in surviving)


def test_numeric_deck_context_still_refuses_an_injected_setpoint() -> None:
    """Тот же контекст, но с подложенным управлением -- обязан отклоняться."""
    card = _model_y_card()
    context = build_decision_context(card)
    poisoned = EvidenceArtifact(
        evidence_id=context["pressure_diagnostic"].evidence_id,
        kind="pressure_diagnostic",
        payload={**context["pressure_diagnostic"].payload, "target": [240]},
    )

    with pytest.raises(ProductionEvidenceBuildError):
        ProductionEvidenceBuilder().build(
            synthetic_production_envelope(
                state_card=card,
                **{**context, "pressure_diagnostic": poisoned},
            )
        )
