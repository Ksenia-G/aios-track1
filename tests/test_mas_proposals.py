"""Proposal contracts: grounding, provenance, audit, specialist outputs."""

from __future__ import annotations

import pytest

from aios.mas.common import Direction, Intensity, ProposalSource, UncertaintyLevel
from aios.mas.intents import ProductionAction, ProductionIntent
from aios.mas.proposals import (
    GroundingReport,
    InjectionAgentOutput,
    ProductionAgentOutput,
    ProductionProposal,
    ProposalAudit,
    ProposalError,
    ProposalProvenance,
)


def _intent() -> ProductionIntent:
    return ProductionIntent(
        action=ProductionAction.INCREASE_OFFTAKE,
        direction=Direction.UP,
        intensity=Intensity.SMALL,
        target_wells=("P1",),
        target_regions=(),
        evidence_ids=("ev-1",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="Producer has ample WBHP margin.",
    )


def _proposal(**overrides: object) -> ProductionProposal:
    fields = {
        "schema_version": "aios.mas.contracts/1.0.0",
        "envelope_digest": "d" * 64,
        "source": ProposalSource.LLM_LIVE,
        "intents": (_intent(),),
        "grounding_report": GroundingReport(grounded_evidence_ids=("ev-1",)),
        "provenance": ProposalProvenance(model_id="llm-provider-x"),
        "audit": ProposalAudit(latency_ms=120),
    }
    fields.update(overrides)
    return ProductionProposal(**fields)  # type: ignore[arg-type]


def test_grounding_report_passed_when_no_unknown_evidence() -> None:
    report = GroundingReport(grounded_evidence_ids=("ev-1", "ev-2"))
    assert report.passed is True


def test_grounding_report_fails_with_unknown_evidence() -> None:
    report = GroundingReport(grounded_evidence_ids=("ev-1",), unknown_evidence_ids=("ev-9",))
    assert report.passed is False


def test_proposal_audit_rejects_negative_latency() -> None:
    ProposalAudit(latency_ms=0)
    with pytest.raises(ValueError):
        ProposalAudit(latency_ms=-1)


# ---------------------------------------------------------------------------
# C18: accepted Proposal rejects unknown grounding evidence.
# ---------------------------------------------------------------------------


def test_proposal_rejects_unknown_grounding_evidence() -> None:
    with pytest.raises(ProposalError):
        _proposal(
            grounding_report=GroundingReport(
                grounded_evidence_ids=("ev-1",), unknown_evidence_ids=("ev-9",)
            )
        )


def test_proposal_role_is_fixed_by_type() -> None:
    from aios.mas.common import AgentRole

    assert _proposal().role is AgentRole.PRODUCTION


# ---------------------------------------------------------------------------
# C19: latency_ms (and other audit-only fields) must not change semantic digest.
# ---------------------------------------------------------------------------


def test_proposal_digest_unchanged_by_latency() -> None:
    a = _proposal(audit=ProposalAudit(latency_ms=50))
    b = _proposal(audit=ProposalAudit(latency_ms=50000))
    assert a.digest() == b.digest()


def test_proposal_digest_changes_with_intents() -> None:
    a = _proposal()
    other_intent = ProductionIntent(
        action=ProductionAction.HOLD,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        target_wells=(),
        target_regions=(),
        evidence_ids=(),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="",
    )
    b = _proposal(intents=(other_intent,), grounding_report=GroundingReport())
    assert a.digest() != b.digest()


def test_proposal_to_json_includes_audit_but_digest_excludes_it() -> None:
    proposal = _proposal()
    payload = proposal.to_json()
    assert payload["audit"]["latency_ms"] == 120
    assert "audit" not in _hash_input_keys(proposal)


def _hash_input_keys(proposal: ProductionProposal) -> set[str]:
    # The semantic payload used for digest() must exclude "audit"; probe indirectly
    # by confirming digest stability under audit mutation (see test above) and by
    # checking the private payload helper directly for a clear, explicit signal.
    return set(proposal._semantic_payload().keys())


# ---------------------------------------------------------------------------
# C20 / C21: AgentOutput requires deterministic baseline ProposalSource.
# ---------------------------------------------------------------------------


def test_production_agent_output_requires_deterministic_baseline_source() -> None:
    primary = _proposal(source=ProposalSource.LLM_LIVE)
    bad_baseline = _proposal(source=ProposalSource.LLM_REPLAY)
    with pytest.raises(ProposalError):
        ProductionAgentOutput(primary_proposal=primary, baseline_proposal=bad_baseline)

    good_baseline = _proposal(source=ProposalSource.DETERMINISTIC_BASELINE)
    output = ProductionAgentOutput(primary_proposal=primary, baseline_proposal=good_baseline)
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE


def test_production_agent_output_allows_deterministic_fallback_primary() -> None:
    primary = _proposal(source=ProposalSource.DETERMINISTIC_FALLBACK)
    baseline = _proposal(source=ProposalSource.DETERMINISTIC_BASELINE)
    output = ProductionAgentOutput(primary_proposal=primary, baseline_proposal=baseline)
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK


def test_injection_agent_output_requires_deterministic_baseline_source() -> None:
    from aios.mas.intents import InjectionAction, InjectionIntent
    from aios.mas.proposals import InjectionProposal

    intent = InjectionIntent(
        action=InjectionAction.HOLD,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        donor_wells=(),
        recipient_wells=(),
        source_regions=(),
        target_regions=(),
        evidence_ids=(),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="",
    )
    primary = InjectionProposal(
        schema_version="aios.mas.contracts/1.0.0",
        envelope_digest="e" * 64,
        source=ProposalSource.LLM_LIVE,
        intents=(intent,),
        grounding_report=GroundingReport(),
        provenance=ProposalProvenance(),
        audit=ProposalAudit(),
    )
    bad_baseline = InjectionProposal(
        schema_version="aios.mas.contracts/1.0.0",
        envelope_digest="e" * 64,
        source=ProposalSource.LLM_LOCAL,
        intents=(intent,),
        grounding_report=GroundingReport(),
        provenance=ProposalProvenance(),
        audit=ProposalAudit(),
    )
    with pytest.raises(ProposalError):
        InjectionAgentOutput(primary_proposal=primary, baseline_proposal=bad_baseline)


# ---------------------------------------------------------------------------
# C22: primary and baseline must reference the same envelope_digest.
# ---------------------------------------------------------------------------


def test_agent_output_requires_matching_envelope_digest() -> None:
    primary = _proposal(envelope_digest="a" * 64)
    baseline = _proposal(source=ProposalSource.DETERMINISTIC_BASELINE, envelope_digest="b" * 64)
    with pytest.raises(ProposalError):
        ProductionAgentOutput(primary_proposal=primary, baseline_proposal=baseline)
