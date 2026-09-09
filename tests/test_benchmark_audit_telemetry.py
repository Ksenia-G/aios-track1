"""Per-epoch audit telemetry, and truthful MAS OFF fallback provenance.

PERF-004 ran the right pipeline but left no way to audit it: the summary kept
economics and digests, and dropped every per-epoch fact a reviewer needs --
which StateCard the epoch reasoned about, which diagnostics it cited, what the
specialists actually returned. Causality was evidenced by behaviour and not
reconstructable from the artifact.

The second defect is narrower: with `llm_enabled=False` the Injection
controller returns `DETERMINISTIC_FALLBACK` but records
`fallback_used=False`, so the MAS OFF ablation under-reported itself (3 of 6).

Nothing here may move a decision. Telemetry is observational: the same
controls, the same candidate ordering, the same schedule.
"""

from __future__ import annotations

import datetime

from aios.benchmark.mas_variant import epoch_audits, fallback_count
from aios.mas.common import ProposalSource
from aios.mas.orchestration import DecisionEpochAudit
from aios.mas.specialists.injection import InjectionController
from aios.mas.specialists.production import ProductionController
from tests.support.mas_team_fixtures import (
    synthetic_injection_envelope,
    synthetic_production_envelope,
)
from tests.test_mas_closed_loop_integration import _diagnostic_step_input, _orchestrator

T0 = datetime.date(2014, 1, 1)
T1 = datetime.date(2014, 9, 1)
T2 = datetime.date(2015, 4, 1)
CANONICAL_EPOCHS = (T0, T1, T2)

#: Снято на 205ad86 ДО этого изменения, тем же детерминированным стендом.
#: HOLD-бандл по контракту не ссылается на proposal-дайджесты, поэтому
#: провенанс на него не влияет -- и именно это здесь и проверяется.
PRE_CHANGE_CANDIDATE_ORDER = ("HOLD", "DETERMINISTIC_BASELINE", "AGENT_BUNDLE")
PRE_CHANGE_HOLD_DIGEST = "b20bea87bd752f5b0b1853771e312a1eebd88b47a5e0b726eae9c983bc3a7dc1"
#: Re-based deliberately at KT3-FINAL-EVIDENCE-PATCH-001. The StateCard now
#: carries pre-action deliverability evidence (WBP9, drawdown_used,
#: drawdown_spare_ratio) and the floor-pinned constraint semantics, so its
#: digest legitimately changed. What this test guards is unchanged and still
#: holds: telemetry must not alter the realized trajectory. The old value was
#: "bd83011d67e812b22eb24ebbadaa0629596b3a5bce1f7687b4717f6b26d66188".
PRE_CHANGE_NEXT_STATE_DIGEST = "1a17b84a3951235870fefcff160af798cb403a32a867b0f29751b22c0b781dc9"


class _StubResult:
    """Ровно те поля DecisionStepResult, которые читает агрегатор."""

    def __init__(self, *, production: bool, injection: bool, audit=None) -> None:
        self.production_fallback_used = production
        self.injection_fallback_used = injection
        self.audit = audit


def _step_result(**overrides):
    return _orchestrator(**overrides).run_decision_step(_diagnostic_step_input())


# --- T1-T4: the epoch knows which state it reasoned about -------------------


def test_audit_retains_decision_and_state_card_dates() -> None:
    """T1 -- the record pairs the epoch with the state it actually read."""
    result = _step_result()

    assert isinstance(result.audit, DecisionEpochAudit)
    assert result.audit.state_card_date == datetime.date(2020, 4, 1)
    assert result.audit.state_card_digest


def test_canonical_epoch_dates_are_persisted_per_epoch() -> None:
    """T2/T3/T4 -- T0, T1 and T2 each carry their own dates into the summary."""
    results = [
        _StubResult(
            production=False,
            injection=False,
            audit=DecisionEpochAudit(state_card_date=epoch, state_card_digest="d" * 64),
        )
        for epoch in CANONICAL_EPOCHS
    ]
    records = epoch_audits(CANONICAL_EPOCHS, results)

    assert [r["epoch_index"] for r in records] == [0, 1, 2]
    assert [r["decision_date"] for r in records] == ["2014-01-01", "2014-09-01", "2015-04-01"]
    assert [r["state_card_date"] for r in records] == ["2014-01-01", "2014-09-01", "2015-04-01"]
    assert all(r["decision_date"] == r["state_card_date"] for r in records)


def test_a_state_dated_after_its_epoch_is_visible_in_the_record() -> None:
    """The gate must be inspectable, so the bad case has to be legible too."""
    stale = [
        _StubResult(
            production=False,
            injection=False,
            audit=DecisionEpochAudit(state_card_date=T2, state_card_digest="d" * 64),
        )
    ]
    record = epoch_audits((T0,), stale)[0]

    assert record["decision_date"] == "2014-01-01"
    assert record["state_card_date"] == "2015-04-01"
    assert record["decision_date"] != record["state_card_date"]


# --- T5-T10: the four evidence identities -----------------------------------


def test_all_four_evidence_identities_are_persisted() -> None:
    """T5-T8 -- pressure, sweep, connectivity, regional imbalance."""
    audit = _step_result().audit

    for slot in (
        "pressure",
        "sweep",
        "support_connectivity",
        "regional_support_imbalance",
    ):
        assert hasattr(audit, slot), slot

    assert audit.pressure.evidence_id == "pressure.diagnostic"
    assert audit.pressure.artifact_digest
    assert audit.sweep.evidence_id == "sweep.diagnostic"
    assert audit.sweep.artifact_digest


def test_absent_diagnostic_slot_is_recorded_as_absent_not_invented() -> None:
    """T9/T10 -- a missing slot must read as missing, never be back-filled."""
    audit = _step_result().audit

    assert audit.support_connectivity.evidence_id is None
    assert audit.support_connectivity.artifact_digest is None
    assert audit.regional_support_imbalance.evidence_id is None


def test_dynamic_evidence_ids_follow_their_epoch_while_static_identity_is_stable() -> None:
    """T9/T10 against the real per-epoch builder, no OPM."""
    from aios.mas.orchestration import build_decision_context
    from tests.support.mas_team_fixtures import synthetic_state_card

    card = synthetic_state_card()
    context = build_decision_context(card)
    stamp = card.metadata.date.isoformat()

    assert context["pressure_diagnostic"].evidence_id.endswith(stamp)
    assert context["sweep_diagnostic"].evidence_id.endswith(stamp)
    if "geology_card" in context:
        assert not context["geology_card"].evidence_id.endswith(stamp)


# --- T11-T15: specialist provenance and beams -------------------------------


def test_production_specialist_provenance_and_beam_are_persisted() -> None:
    """T11/T13/T15."""
    audit = _step_result().audit

    assert audit.production.proposal_source == str(ProposalSource.DETERMINISTIC_FALLBACK)
    assert audit.production.fallback_used is True
    assert audit.production.fallback_reason == "llm_disabled"
    assert audit.production.primary["intents"]
    assert audit.production.baseline is not None


def test_injection_specialist_provenance_and_beam_are_persisted() -> None:
    """T12/T14 -- hypotheses, not only the winner."""
    audit = _step_result().audit

    assert audit.injection.proposal_source == str(ProposalSource.DETERMINISTIC_FALLBACK)
    assert audit.injection.primary["intents"]
    assert isinstance(audit.injection.alternatives, tuple)
    assert audit.injection.baseline is not None


def test_cited_evidence_ids_mirror_the_proposal_exactly() -> None:
    """T15 -- the record repeats what the proposal grounded, and nothing else.

    A deterministic HOLD floor cites nothing, and the audit must say so rather
    than borrow the epoch's evidence catalogue to look better populated.
    """
    from aios.mas.orchestration import SpecialistAudit

    output = InjectionController(llm_enabled=False).propose(synthetic_injection_envelope())
    record = SpecialistAudit.of(output)
    grounded = output.primary_proposal.grounding_report.grounded_evidence_ids

    assert record.cited_evidence_ids == tuple(sorted(grounded))

    production = ProductionController(llm_enabled=False).propose(synthetic_production_envelope())
    production_record = SpecialistAudit.of(production)
    assert production_record.cited_evidence_ids == tuple(
        sorted(production.primary_proposal.grounding_report.grounded_evidence_ids)
    )


def test_beam_records_carry_structural_intent_but_no_setpoints() -> None:
    """NO_SETPOINTS_CROSS: an audit record is evidence, not a control channel."""
    from aios.mas.specialists.authority import find_prescriptive_control

    audit = _step_result().audit

    for payload in (
        audit.production.primary,
        audit.injection.primary,
        *audit.injection.alternatives,
    ):
        assert find_prescriptive_control(payload) is None


# --- T16-T21: truthful fallback accounting ----------------------------------


def test_production_llm_disabled_reports_fallback_used() -> None:
    """T16 -- already true before this milestone; it must stay true."""
    output = ProductionController(llm_enabled=False).propose(synthetic_production_envelope())

    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.primary_proposal.provenance.fallback_used is True


def test_injection_llm_disabled_reports_fallback_used() -> None:
    """T17 -- RED before the fix: DETERMINISTIC_FALLBACK with fallback_used=False."""
    output = InjectionController(llm_enabled=False).propose(synthetic_injection_envelope())

    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.primary_proposal.provenance.fallback_used is True
    assert output.primary_proposal.provenance.fallback_reason == "llm_disabled"


def test_the_live_branch_keeps_its_own_fallback_reason() -> None:
    """The correction must touch the ablation branch only.

    MAS ON reaches the deterministic floor for its own reasons, and those must
    keep their own text -- otherwise a live run that genuinely fell back would
    be indistinguishable from an ablation, and the frozen PERF-004 provenance
    would stop meaning what it meant.
    """
    output = InjectionController(llm_enabled=True, llm_provider=None).propose(
        synthetic_injection_envelope()
    )
    provenance = output.primary_proposal.provenance

    assert provenance.fallback_used is True
    assert provenance.fallback_reason != "llm_disabled"
    assert "llm_provider" in provenance.fallback_reason


def test_one_mas_off_epoch_counts_both_specialists() -> None:
    """T18."""
    assert fallback_count([_StubResult(production=True, injection=True)]) == 2


def test_three_mas_off_epochs_count_six_deterministic_decisions() -> None:
    """T19 -- the PERF-004 number that read 3 and should have read 6."""
    results = [_StubResult(production=True, injection=True) for _ in range(3)]

    assert fallback_count(results) == 6


def test_all_live_mas_on_counts_zero() -> None:
    """T20 -- MAS ON meaning is unchanged by this milestone."""
    results = [_StubResult(production=False, injection=False) for _ in range(3)]

    assert fallback_count(results) == 0


def test_a_real_mas_on_fallback_still_increments() -> None:
    """T21 -- one specialist falling back is +1, not +2 and not 0."""
    results = [
        _StubResult(production=False, injection=False),
        _StubResult(production=False, injection=True),
        _StubResult(production=True, injection=False),
    ]

    assert fallback_count(results) == 2


def test_mas_off_end_to_end_reports_both_specialists() -> None:
    """T18/T19 through the real controllers, not stubs."""
    result = _step_result()

    assert result.production_fallback_used is True
    assert result.injection_fallback_used is True
    assert fallback_count([result]) == 2


# --- T22-T24: non-interference ----------------------------------------------


def test_telemetry_does_not_alter_committed_hold_identity() -> None:
    """T22 -- HOLD carries no proposal reference, so provenance cannot reach it."""
    result = _step_result()
    hold = next(c for c in result.candidates if c.candidate_kind == "HOLD")

    assert hold.candidate_digest == PRE_CHANGE_HOLD_DIGEST


def test_telemetry_does_not_alter_candidate_ordering() -> None:
    """T23."""
    result = _step_result()

    assert tuple(c.candidate_kind for c in result.candidates) == PRE_CHANGE_CANDIDATE_ORDER


def test_telemetry_does_not_alter_the_realized_trajectory() -> None:
    """T24 -- the state the next epoch inherits is byte-identical."""
    result = _step_result()

    assert result.next_state.digest() == PRE_CHANGE_NEXT_STATE_DIGEST


def test_numerical_controls_are_untouched_by_telemetry() -> None:
    """The controls are what reach OPM; they must not move."""
    result = _step_result()
    committed = result.committed_candidate.canonical()

    assert [(c.well, c.liquid_rate) for c in committed.producers] == [("SYN-P1", 180.0)]
    assert [(c.well, c.surface_rate) for c in committed.injectors] == [("SYN-I1", 150.0)]


def test_audit_is_optional_so_existing_callers_are_unaffected() -> None:
    """A caller that never asked for telemetry keeps its old contract."""
    audit = DecisionEpochAudit(state_card_date=T0, state_card_digest="d" * 64)

    assert audit.pressure.evidence_id is None
    assert audit.production is None or audit.production.primary is None
