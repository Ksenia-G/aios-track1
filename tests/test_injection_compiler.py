"""ProposalCompiler builds a primary InjectionProposal, never a baseline."""

from __future__ import annotations

import inspect

import pytest

from aios.mas.common import AgentRole, Direction, Intensity, ProposalSource, UncertaintyLevel
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.mas.proposals import GroundingReport, ProposalProvenance
from aios.mas.specialists.injection.compiler import CompileError, ProposalCompiler
from tests.support.mas_team_fixtures import synthetic_injection_envelope


def _hold() -> InjectionIntent:
    return InjectionIntent(
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


def _reallocate(*evidence_ids: str) -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.REALLOCATE_INJECTION,
        direction=Direction.NONE,
        intensity=Intensity.SMALL,
        donor_wells=("I3",),
        recipient_wells=("I1",),
        source_regions=("B1",),
        target_regions=("B1",),
        evidence_ids=evidence_ids,
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="Move support toward the weaker offset producer.",
    )


def test_compile_deterministic_fallback_hold() -> None:
    envelope = synthetic_injection_envelope()
    proposal = ProposalCompiler().compile(
        envelope,
        intents=(_hold(),),
        grounding_report=GroundingReport(),
        source=ProposalSource.DETERMINISTIC_FALLBACK,
    )

    assert proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert proposal.role is AgentRole.INJECTION
    assert proposal.envelope_digest == envelope.digest()
    assert proposal.intents == (_hold(),)
    assert proposal.grounding_report.passed is True
    assert proposal.provenance.prompt_digest is None


def test_compile_grounded_reallocate() -> None:
    envelope = synthetic_injection_envelope()
    report = GroundingReport(grounded_evidence_ids=("SYN-pressure-1",))
    proposal = ProposalCompiler().compile(
        envelope,
        intents=(_reallocate("SYN-pressure-1"),),
        grounding_report=report,
        source=ProposalSource.LLM_LIVE,
        provenance=ProposalProvenance(model_id="fake-llm"),
    )

    assert proposal.source is ProposalSource.LLM_LIVE
    assert proposal.intents[0].action is InjectionAction.REALLOCATE_INJECTION
    assert proposal.grounding_report.grounded_evidence_ids == ("SYN-pressure-1",)
    assert proposal.provenance.model_id == "fake-llm"


def test_compile_does_not_accept_baseline_argument() -> None:
    assert "baseline" not in inspect.signature(ProposalCompiler.compile).parameters


def test_rejects_deterministic_baseline_source() -> None:
    with pytest.raises(CompileError, match="DETERMINISTIC_BASELINE"):
        ProposalCompiler().compile(
            synthetic_injection_envelope(),
            intents=(_hold(),),
            grounding_report=GroundingReport(),
            source=ProposalSource.DETERMINISTIC_BASELINE,
        )


def test_rejects_empty_intents() -> None:
    with pytest.raises(CompileError, match="must not be empty"):
        ProposalCompiler().compile(
            synthetic_injection_envelope(),
            intents=(),
            grounding_report=GroundingReport(),
            source=ProposalSource.DETERMINISTIC_FALLBACK,
        )


def test_rejects_ungrounded_report_entirely() -> None:
    with pytest.raises(CompileError, match="not grounded"):
        ProposalCompiler().compile(
            synthetic_injection_envelope(),
            intents=(_reallocate("SYN-pressure-1"),),
            grounding_report=GroundingReport(
                grounded_evidence_ids=("SYN-pressure-1",),
                unknown_evidence_ids=("SYN-ghost",),
            ),
            source=ProposalSource.DETERMINISTIC_FALLBACK,
        )


def test_rejects_intent_evidence_missing_from_passed_report() -> None:
    with pytest.raises(CompileError, match="ungrounded evidence_id"):
        ProposalCompiler().compile(
            synthetic_injection_envelope(),
            intents=(_reallocate("SYN-pressure-1"),),
            grounding_report=GroundingReport(),
            source=ProposalSource.DETERMINISTIC_FALLBACK,
        )


def test_same_inputs_are_reproducible() -> None:
    envelope = synthetic_injection_envelope()
    compiler = ProposalCompiler()
    kwargs = {
        "intents": (_hold(),),
        "grounding_report": GroundingReport(),
        "source": ProposalSource.DETERMINISTIC_FALLBACK,
    }

    first = compiler.compile(envelope, **kwargs)
    second = compiler.compile(envelope, **kwargs)

    assert first.digest() == second.digest()
    assert first is not second
