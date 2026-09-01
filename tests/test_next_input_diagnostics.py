"""Диагностический контекст следующей эпохи: MAS-NEXT-INPUT-DIAGNOSTICS-001.

`default_next_input` переносил вперёд только состояние, память, допустимые
действия и закоммиченного кандидата. Все диагностические слоты входа
(`pressure_diagnostic`, `sweep_diagnostic`, `geology_card`, `response_map`)
на T1/T2 приходили как None -- хотя на T0 они были собраны. Injection-
специалист на поздних эпохах отклонял активные гипотезы не потому, что
состояние их не поддерживает, а потому что evidence физически не было.

Правило, которое здесь проверяется, важнее самого факта наличия слотов:
СТАТИКА (связность отсеков по колоде) переиспользуется, ДИНАМИКА (давление,
охват, региональный дисбаланс) ПЕРЕСЧИТЫВАЕТСЯ от реализованного StateCard
эпохи, а не копируется с T0. Копия T0-диагностики на T1 была бы такой же
ошибкой, как её отсутствие -- просто менее заметной.
"""

from __future__ import annotations

import dataclasses
import datetime

import pytest

from aios.diagnostics import PressureDiagnostic, SweepDiagnostic
from aios.diagnostics.support import derive_support_connectivity, derive_support_imbalance
from aios.mas.common import ProposalSource
from aios.mas.intents import InjectionAction, ProductionAction
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.orchestration import DecisionStepInput, DecisionStepResult, default_next_input
from aios.mas.selection.models import CandidateEvaluation, EconomicSelectionResult
from aios.state import (
    ConstraintState,
    FieldStateSnapshot,
    GeologyContext,
    StateCard,
    StateMetadata,
    UncertaintyState,
    WellRole,
    WellStateSnapshot,
)
from tests.support.mas_team_fixtures import (
    synthetic_horizon_context,
    synthetic_magnitude_candidate,
    synthetic_memory_slice,
)

T0_DATE = datetime.date(2014, 4, 1)
T1_DATE = datetime.date(2014, 9, 1)
T2_DATE = datetime.date(2015, 4, 1)

# Один отсек, два нагнетателя и два добывающих: минимум, на котором
# донор/получатель вообще определимы (`_classify` требует >= 2 нагнетателей).
MEMBERSHIP = {"NX-I1": "NX-R3", "NX-I2": "NX-R3", "NX-P1": "NX-R3", "NX-P2": "NX-R3"}


def _producer(well_id: str, *, wlpr: float, wopr: float, wct: float) -> WellStateSnapshot:
    return WellStateSnapshot(
        well_id=well_id,
        role=WellRole.PRODUCER,
        status="OPEN",
        current_target=wlpr,
        control_mode="LRAT",
        bhp=95.0,
        wlpr=wlpr,
        wopr=wopr,
        wwpr=wlpr - wopr,
        wwir=0.0,
        wct=wct,
        wopt=1000.0,
        wwpt=400.0,
        wwit=0.0,
        producer_bhp_margin=12.0,
        injector_bhp_margin=None,
        wlpr_margin=100.0,
    )


def _injector(well_id: str, *, wwir: float, margin: float) -> WellStateSnapshot:
    return WellStateSnapshot(
        well_id=well_id,
        role=WellRole.INJECTOR,
        status="OPEN",
        current_target=wwir,
        control_mode="RATE",
        bhp=240.0,
        wlpr=0.0,
        wopr=0.0,
        wwpr=0.0,
        wwir=wwir,
        wct=0.0,
        wopt=0.0,
        wwpt=0.0,
        wwit=5000.0,
        producer_bhp_margin=None,
        injector_bhp_margin=margin,
        wlpr_margin=None,
    )


def _state(
    *,
    date: datetime.date,
    donor_rate: float,
    recipient_rate: float,
    average_pressure: float,
    water_cut: float,
    membership: dict[str, str] | None = None,
) -> StateCard:
    """StateCard одной эпохи. Меняются только динамические величины."""
    liquid = 300.0
    oil = liquid * (1.0 - water_cut)
    return StateCard(
        metadata=StateMetadata(
            date=date,
            case_id="NX-case",
            run_id=f"NX-run-{date.isoformat()}",
            simulator_digest="sim-sha256:" + "a" * 10,
            schedule_hash="b" * 64,
            source_summary_identity="summary-sha256:" + "c" * 10,
            extractor_version="aios.state.extractor/1.0.0",
        ),
        field=FieldStateSnapshot(
            average_pressure=average_pressure,
            pressure_trend=None,
            oil_rate=oil,
            water_rate=liquid - oil,
            liquid_rate=liquid,
            water_injection_rate=donor_rate + recipient_rate,
            voidage_production_rate=liquid,
            voidage_production_total=140000.0,
            oil_total=100000.0,
            water_total=40000.0,
            water_injection_total=120000.0,
            produced_water_available=liquid - oil,
            compensation_value=None,
            compensation_status="UNRESOLVED",
        ),
        wells=(
            _producer("NX-P1", wlpr=liquid / 2, wopr=oil / 2, wct=water_cut),
            _producer("NX-P2", wlpr=liquid / 2, wopr=oil / 2, wct=water_cut),
            _injector("NX-I1", wwir=donor_rate, margin=40.0),
            _injector("NX-I2", wwir=recipient_rate, margin=40.0),
        ),
        constraints=ConstraintState(
            official={"producer_bhp_min": 80.0, "injector_bhp_max": 300.0, "wlpr_max": 500.0},
            search={"producer_lrat_max": 400.0},
        ),
        geology=GeologyContext(
            block_membership=dict(MEMBERSHIP if membership is None else membership)
        ),
        uncertainty=UncertaintyState(items=("compensation definition unknown",)),
    )


def _t0_state() -> StateCard:
    # Донор -- NX-I1: качает заметно больше соседа по отсеку.
    return _state(
        date=T0_DATE,
        donor_rate=400.0,
        recipient_rate=100.0,
        average_pressure=205.0,
        water_cut=0.42,
    )


def _t1_state() -> StateCard:
    # Дисбаланс развернулся: теперь больше качает NX-I2, а давление и
    # обводнённость -- другие. Ни один из этих фактов не выводим из T0.
    return _state(
        date=T1_DATE,
        donor_rate=90.0,
        recipient_rate=430.0,
        average_pressure=188.0,
        water_cut=0.61,
    )


def _t2_state() -> StateCard:
    return _state(
        date=T2_DATE,
        donor_rate=250.0,
        recipient_rate=240.0,
        average_pressure=196.0,
        water_cut=0.55,
    )


def _t0_input(state: StateCard | None = None, **overrides: object) -> DecisionStepInput:
    """Вход T0 -- собранный тем же каноническим строителем, что и в бенчмарке."""
    from aios.mas.orchestration.context import build_decision_context

    card = _t0_state() if state is None else state
    fields: dict[str, object] = {
        "state": card,
        "horizon_context": dataclasses.replace(
            synthetic_horizon_context(), decision_date=card.metadata.date
        ),
        "memory_slice": synthetic_memory_slice(),
        "production_allowed_actions": (ProductionAction.HOLD, ProductionAction.REDUCE_OFFTAKE),
        "injection_allowed_actions": (
            InjectionAction.HOLD,
            InjectionAction.SUPPORT_REGION,
            InjectionAction.REALLOCATE_INJECTION,
        ),
        **build_decision_context(card),
    }
    fields.update(overrides)
    return DecisionStepInput(**fields)  # type: ignore[arg-type]


def _result(next_state: StateCard, candidate: MagnitudeCandidate | None = None):
    """Результат шага, из которого строится вход следующей эпохи."""
    committed = synthetic_magnitude_candidate() if candidate is None else candidate
    return DecisionStepResult(
        round2_triggered=False,
        production_envelope_digest="d" * 64,
        injection_envelope_digest="e" * 64,
        arbiter_decision_digest="f" * 64,
        candidates=(),
        committed_candidate_digest=committed.digest(),
        committed_candidate=committed,
        selection=EconomicSelectionResult(
            selection_rule_version="tests.next_input_diagnostics/1.0.0",
            selected=CandidateEvaluation(candidate=committed),
            eligible=(),
            rejected=(),
        ),
        next_state=next_state,
        production_fallback_used=False,
        injection_fallback_used=False,
    )


@pytest.fixture
def t1_input() -> DecisionStepInput:
    return default_next_input(_t0_input(), _result(_t1_state()))


# ===========================================================================
# N1-N4 -- диагностика вообще доживает до следующей эпохи
# ===========================================================================


def test_n1_pressure_diagnostic_survives_the_next_epoch(t1_input: DecisionStepInput) -> None:
    assert t1_input.pressure_diagnostic is not None
    assert t1_input.pressure_diagnostic.kind == "pressure_diagnostic"


def test_n2_sweep_diagnostic_survives_the_next_epoch(t1_input: DecisionStepInput) -> None:
    assert t1_input.sweep_diagnostic is not None
    assert t1_input.sweep_diagnostic.kind == "sweep_diagnostic"


def test_n3_geology_card_is_available_at_the_next_epoch(t1_input: DecisionStepInput) -> None:
    assert t1_input.geology_card is not None
    assert t1_input.geology_card.kind == "support_connectivity"


def test_n4_response_map_is_available_at_the_next_epoch(t1_input: DecisionStepInput) -> None:
    assert t1_input.response_map is not None
    assert t1_input.response_map.kind == "regional_support_imbalance"


# ===========================================================================
# N5 -- региональная поддержка ПЕРЕСЧИТАНА по состоянию T1, а не скопирована
# ===========================================================================


def test_n5_regional_support_reflects_the_realized_t1_state(t1_input: DecisionStepInput) -> None:
    t1 = _t1_state()
    expected = derive_support_imbalance(wells=t1.wells, geology=t1.geology).to_json()
    stale = derive_support_imbalance(wells=_t0_state().wells, geology=_t0_state().geology).to_json()

    assert t1_input.response_map is not None
    payload = dict(t1_input.response_map.payload)
    assert payload == expected
    assert payload != stale

    scope = next(item for item in payload["scopes"] if item["scope"] == "NX-R3")
    # Донор и получатель поменялись местами относительно T0 -- вход эпохи
    # обязан показывать именно это, а не унаследованную картинку.
    assert scope["donor_like"] == ["NX-I2"]
    assert scope["recipient_like"] == ["NX-I1"]


# ===========================================================================
# N6 -- динамическая диагностика не является копией T0 при смене состояния
# ===========================================================================


def test_n6_dynamic_diagnostics_change_with_the_state(t1_input: DecisionStepInput) -> None:
    t0 = _t0_input()
    t1 = _t1_state()

    assert t1_input.pressure_diagnostic is not None and t0.pressure_diagnostic is not None
    assert t1_input.sweep_diagnostic is not None and t0.sweep_diagnostic is not None

    assert t1_input.pressure_diagnostic.digest() != t0.pressure_diagnostic.digest()
    assert t1_input.sweep_diagnostic.digest() != t0.sweep_diagnostic.digest()

    assert dict(t1_input.pressure_diagnostic.payload) == PressureDiagnostic().propose(t1).to_json()
    assert dict(t1_input.sweep_diagnostic.payload) == SweepDiagnostic().propose(t1).to_json()


# ===========================================================================
# N7 -- статика (связность отсеков) стабильна на той же колоде
# ===========================================================================


def test_n7_static_connectivity_is_stable_across_epochs(t1_input: DecisionStepInput) -> None:
    t0 = _t0_input()
    assert t0.geology_card is not None and t1_input.geology_card is not None
    assert t1_input.geology_card.digest() == t0.geology_card.digest()

    t1 = _t1_state()
    assert dict(t1_input.geology_card.payload) == (
        derive_support_connectivity(wells=t1.wells, geology=t1.geology).to_json()
    )


# ===========================================================================
# N8 -- id динамического evidence принадлежит своей эпохе
# ===========================================================================


def test_n8_dynamic_evidence_ids_are_fresh_per_epoch(t1_input: DecisionStepInput) -> None:
    t0 = _t0_input()
    for slot in ("pressure_diagnostic", "sweep_diagnostic", "response_map"):
        old = getattr(t0, slot)
        new = getattr(t1_input, slot)
        assert old is not None and new is not None, slot
        assert new.evidence_id != old.evidence_id, slot
        assert T1_DATE.isoformat() in new.evidence_id, slot
        assert T0_DATE.isoformat() not in new.evidence_id, slot


def test_n8_static_evidence_id_stays_the_same(t1_input: DecisionStepInput) -> None:
    t0 = _t0_input()
    assert t0.geology_card is not None and t1_input.geology_card is not None
    assert t1_input.geology_card.evidence_id == t0.geology_card.evidence_id


# ===========================================================================
# N9 -- вход эпохи не зависит от того, что случится ПОСЛЕ неё
# ===========================================================================


def test_n9_next_input_carries_no_future_information() -> None:
    """Две траектории, совпадающие до T1 и разные после, дают один вход T1."""
    t0 = _t0_input()
    branch_a = default_next_input(t0, _result(_t1_state()))
    branch_b = default_next_input(t0, _result(_t1_state()))

    # Ветки расходятся строго ПОСЛЕ T1.
    future_a = default_next_input(branch_a, _result(_t2_state()))
    future_b = default_next_input(
        branch_b,
        _result(
            _state(
                date=T2_DATE,
                donor_rate=10.0,
                recipient_rate=480.0,
                average_pressure=150.0,
                water_cut=0.9,
            )
        ),
    )

    def _slots(step: DecisionStepInput) -> tuple:
        return (
            step.state.digest(),
            tuple(
                None if artifact is None else artifact.digest()
                for artifact in (
                    step.pressure_diagnostic,
                    step.sweep_diagnostic,
                    step.geology_card,
                    step.response_map,
                )
            ),
        )

    assert _slots(branch_a) == _slots(branch_b)
    assert _slots(future_a) != _slots(future_b)

    # Ни одна дата, попавшая во вход T1, не позже самой T1.
    assert branch_a.state.metadata.date == T1_DATE
    assert branch_a.horizon_context.decision_date == T1_DATE


def test_n9_diagnostics_are_derived_only_from_the_epoch_state_card() -> None:
    """Тот же StateCard эпохи -- тот же контекст, независимо от истории входа."""
    from_t0 = default_next_input(_t0_input(), _result(_t1_state()))
    from_other = default_next_input(
        _t0_input(
            state=_state(
                date=T0_DATE,
                donor_rate=1.0,
                recipient_rate=2.0,
                average_pressure=260.0,
                water_cut=0.05,
            )
        ),
        _result(_t1_state()),
    )
    for slot in ("pressure_diagnostic", "sweep_diagnostic", "geology_card", "response_map"):
        left, right = getattr(from_t0, slot), getattr(from_other, slot)
        assert left is not None and right is not None, slot
        assert left.digest() == right.digest(), slot


# ===========================================================================
# N10/N11 -- перенос управлений и прежняя семантика входа не деградировали
# ===========================================================================


def test_n10_committed_controls_carry_forward(t1_input: DecisionStepInput) -> None:
    committed = synthetic_magnitude_candidate()
    step = default_next_input(_t0_input(), _result(_t1_state(), candidate=committed))
    assert step.previous_committed is not None
    assert step.previous_committed.digest() == committed.digest()


def test_n11_allowed_actions_memory_and_horizon_are_preserved(
    t1_input: DecisionStepInput,
) -> None:
    t0 = _t0_input()
    assert t1_input.production_allowed_actions == t0.production_allowed_actions
    assert t1_input.injection_allowed_actions == t0.injection_allowed_actions
    assert t1_input.memory_slice == t0.memory_slice
    assert t1_input.state is not t0.state
    assert t1_input.horizon_context.decision_date == T1_DATE
    assert t1_input.horizon_context.horizon_end == t0.horizon_context.horizon_end
    assert t1_input.horizon_context.remaining_steps == t0.horizon_context.remaining_steps - 1


# ===========================================================================
# N12 -- Injection-специалист на T1 видит поддержку и может её процитировать
# ===========================================================================


def test_n12_injection_specialist_sees_support_evidence_at_t1(
    t1_input: DecisionStepInput,
) -> None:
    import json

    from aios.mas.common import DecisionRound
    from aios.mas.envelopes import InjectionEnvelope
    from aios.mas.specialists.injection.controller import InjectionController
    from aios.mas.specialists.injection.evidence_builder import EvidenceBuilder
    from aios.mas.specialists.injection.provider import FakeLlmProvider

    envelope = InjectionEnvelope(
        schema_version="aios.mas.contracts/1.0.0",
        round=DecisionRound.ROUND_1,
        variant_id="NX-variant",
        config_digest="c" * 64,
        state_card=t1_input.state,
        horizon_context=t1_input.horizon_context,
        memory_slice=t1_input.memory_slice,
        allowed_actions=t1_input.injection_allowed_actions,
        pressure_diagnostic=t1_input.pressure_diagnostic,
        sweep_diagnostic=t1_input.sweep_diagnostic,
        geology_card=t1_input.geology_card,
        response_map=t1_input.response_map,
    )

    catalog = EvidenceBuilder().build(envelope)
    kinds = {artifact.kind for artifact in catalog.artifacts}
    assert {
        "pressure_diagnostic",
        "sweep_diagnostic",
        "support_connectivity",
        "regional_support_imbalance",
    } <= kinds

    assert t1_input.response_map is not None and t1_input.geology_card is not None
    cited = [t1_input.response_map.evidence_id, t1_input.geology_card.evidence_id]
    assert all(catalog.contains(evidence_id) for evidence_id in cited)

    # Донор и получатель берутся ИЗ evidence эпохи, а не из константы теста.
    scope = next(
        item
        for item in dict(t1_input.response_map.payload)["scopes"]
        if item["donor_like"] and item["recipient_like"]
    )
    response = {
        "hypotheses": [
            {
                "intents": [
                    {
                        "action": "REALLOCATE_INJECTION",
                        "direction": "NONE",
                        "intensity": "MODERATE",
                        "donor_wells": list(scope["donor_like"]),
                        "recipient_wells": list(scope["recipient_like"]),
                        # REALLOCATE перераспределяет ВНУТРИ отсека, поэтому
                        # источник и цель -- один и тот же scope из evidence.
                        "source_regions": [scope["scope"]],
                        "target_regions": [scope["scope"]],
                        "evidence_ids": cited,
                        "uncertainty": "MEDIUM",
                        "rationale_short": "Compartment support is lopsided at this epoch.",
                    }
                ]
            }
        ],
        "considered_actions": [],
    }
    output = InjectionController(
        llm_enabled=True,
        llm_provider=FakeLlmProvider(json.dumps(response)),
        llm_source=ProposalSource.LLM_LOCAL,
    ).propose(envelope)

    assert output.primary_proposal.provenance.fallback_used is False
    assert output.primary_proposal.source is ProposalSource.LLM_LOCAL
    assert [str(intent.action) for intent in output.primary_proposal.intents] == [
        "REALLOCATE_INJECTION"
    ]
