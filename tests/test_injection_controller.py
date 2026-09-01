"""InjectionController propose(envelope) -> InjectionAgentOutput."""

from __future__ import annotations

import dataclasses
import inspect
import json
from dataclasses import replace

import pytest

from aios.mas.common import AgentRole, DecisionRound, ProposalSource
from aios.mas.envelopes import InjectionEnvelope
from aios.mas.evidence import EvidenceArtifact, ProposalReference
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.mas.invariants import require_no_baseline_field
from aios.mas.protocols import Agent, SpecialistPromptBuilder
from aios.mas.specialists.injection import (
    LLM_SCHEMA_GROUNDING_RETRY_LIMIT,
    EvidenceBuildError,
    FakeLlmProvider,
    InjectionController,
    InputValidationError,
)
from aios.state import WellRole, WellStateSnapshot
from tests.support.mas_team_fixtures import synthetic_injection_envelope, synthetic_state_card


def _injector(well_id: str) -> WellStateSnapshot:
    return WellStateSnapshot(
        well_id=well_id,
        role=WellRole.INJECTOR,
        status="OPEN",
        current_target=150.0,
        control_mode="RATE",
        bhp=250.0,
        wlpr=0.0,
        wopr=0.0,
        wwpr=0.0,
        wwir=150.0,
        wct=0.0,
        wopt=0.0,
        wwpt=0.0,
        wwit=700.0,
        producer_bhp_margin=None,
        injector_bhp_margin=50.0,
        wlpr_margin=None,
    )


def _two_injector_envelope(**overrides: object):
    base = synthetic_state_card()
    wells = (*base.wells, _injector("SYN-I2"))
    membership = {**dict(base.geology.block_membership), "SYN-I2": "SYN-B1"}
    card = replace(base, wells=wells, geology=replace(base.geology, block_membership=membership))
    return synthetic_injection_envelope(state_card=card, **overrides)


def _hold_json() -> str:
    return json.dumps(
        {
            "intents": [
                {
                    "action": "HOLD",
                    "direction": "NONE",
                    "intensity": "NONE",
                    "donor_wells": [],
                    "recipient_wells": [],
                    "source_regions": [],
                    "target_regions": [],
                    "evidence_ids": [],
                    "uncertainty": "LOW",
                    "rationale_short": "",
                }
            ]
        }
    )


def _reallocate_json() -> str:
    return json.dumps(
        {
            "intents": [
                {
                    "action": "REALLOCATE_INJECTION",
                    "direction": "NONE",
                    "intensity": "SMALL",
                    "donor_wells": ["SYN-I1"],
                    "recipient_wells": ["SYN-I2"],
                    "source_regions": ["SYN-B1"],
                    "target_regions": ["SYN-B1"],
                    "evidence_ids": ["SYN-pressure-1"],
                    "uncertainty": "MEDIUM",
                    "rationale_short": "Move support toward the weaker offset producer.",
                }
            ]
        }
    )


def test_llm_retry_contract_is_exactly_one() -> None:
    assert LLM_SCHEMA_GROUNDING_RETRY_LIMIT == 1


def test_valid_envelope_returns_fallback_primary_and_separate_baseline() -> None:
    envelope = synthetic_injection_envelope()
    output = InjectionController(llm_enabled=False).propose(envelope)

    assert output.primary_proposal is not output.baseline_proposal
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE
    assert output.primary_proposal.envelope_digest == envelope.digest()
    assert output.baseline_proposal.envelope_digest == envelope.digest()
    assert output.primary_proposal.digest() != output.baseline_proposal.digest()
    assert output.primary_proposal.grounding_report.passed is True
    assert output.baseline_proposal.grounding_report.passed is True
    assert output.primary_proposal.intents[0].action is InjectionAction.HOLD
    assert output.baseline_proposal.intents[0].action is InjectionAction.HOLD


def test_satisfies_agent_protocol() -> None:
    controller = InjectionController(llm_enabled=False)
    assert isinstance(controller, Agent)


def test_propose_does_not_accept_baseline() -> None:
    parameters = inspect.signature(InjectionController.propose).parameters
    assert "envelope" in parameters
    assert "baseline" not in parameters


def test_prompt_builder_contract_has_no_baseline_input() -> None:
    require_no_baseline_field(InjectionEnvelope)
    require_no_baseline_field(synthetic_injection_envelope())
    parameters = inspect.signature(SpecialistPromptBuilder.build).parameters
    assert "envelope" in parameters
    assert "baseline" not in parameters


def test_round1_has_empty_peers() -> None:
    envelope = synthetic_injection_envelope()
    output = InjectionController(llm_enabled=False).propose(envelope)

    assert envelope.round is DecisionRound.ROUND_1
    assert envelope.peer_proposals == ()
    assert output.primary_proposal.envelope_digest == envelope.digest()


def test_round2_with_peer_proposal_is_accepted() -> None:
    envelope = synthetic_injection_envelope(
        round=DecisionRound.ROUND_2,
        peer_proposals=(ProposalReference(role=AgentRole.PRODUCTION, proposal_digest="d" * 64),),
    )
    output = InjectionController(llm_enabled=False).propose(envelope)

    assert envelope.round is DecisionRound.ROUND_2
    assert len(envelope.peer_proposals) == 1
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE
    assert output.primary_proposal.envelope_digest == envelope.digest()


def test_round2_with_injection_peer_is_rejected() -> None:
    envelope = synthetic_injection_envelope(
        round=DecisionRound.ROUND_2,
        peer_proposals=(ProposalReference(role=AgentRole.INJECTION, proposal_digest="d" * 64),),
    )
    with pytest.raises(InputValidationError, match="must be PRODUCTION"):
        InjectionController(llm_enabled=False).propose(envelope)


def test_fake_provider_is_not_called_when_llm_disabled() -> None:
    provider = FakeLlmProvider(_hold_json())
    InjectionController(llm_enabled=False, llm_provider=provider).propose(
        synthetic_injection_envelope()
    )
    assert provider.prompts == []


def test_llm_enabled_without_provider_keeps_deterministic_floor() -> None:
    output = InjectionController(llm_enabled=True).propose(synthetic_injection_envelope())

    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE
    assert output.primary_proposal is not output.baseline_proposal
    assert output.primary_proposal.provenance.fallback_used is True
    assert output.primary_proposal.intents[0].action is InjectionAction.HOLD


def test_fake_provider_returns_grounded_llm_primary() -> None:
    envelope = _two_injector_envelope()
    provider = FakeLlmProvider(_reallocate_json())
    output = InjectionController(llm_enabled=True, llm_provider=provider).propose(envelope)

    assert len(provider.prompts) == 1
    assert "baseline" not in provider.prompts[0].casefold()
    assert output.primary_proposal is not output.baseline_proposal
    assert output.primary_proposal.source is ProposalSource.LLM_LOCAL
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE
    assert output.primary_proposal.envelope_digest == envelope.digest()
    assert output.primary_proposal.grounding_report.passed is True
    assert output.primary_proposal.intents[0].action is InjectionAction.REALLOCATE_INJECTION
    assert output.primary_proposal.provenance.model_id == "fake-injection-llm"
    assert output.primary_proposal.provenance.fallback_used is False


def test_round2_llm_path_sees_peer_not_baseline() -> None:
    envelope = synthetic_injection_envelope(
        round=DecisionRound.ROUND_2,
        peer_proposals=(ProposalReference(role=AgentRole.PRODUCTION, proposal_digest="d" * 64),),
    )
    provider = FakeLlmProvider(_hold_json())
    output = InjectionController(llm_enabled=True, llm_provider=provider).propose(envelope)

    assert "PRODUCTION" in provider.prompts[0]
    assert "baseline" not in provider.prompts[0].casefold()
    assert output.primary_proposal.source is ProposalSource.LLM_LOCAL
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE
    assert output.primary_proposal.envelope_digest == envelope.digest()


def test_schema_error_retries_once_then_accepts() -> None:
    provider = FakeLlmProvider(("not-json", _hold_json()))
    output = InjectionController(llm_enabled=True, llm_provider=provider).propose(
        synthetic_injection_envelope()
    )

    assert len(provider.prompts) == 2
    assert provider.prompts[0] == provider.prompts[1]
    assert output.primary_proposal.source is ProposalSource.LLM_LOCAL
    assert output.primary_proposal.intents[0].action is InjectionAction.HOLD


def test_two_schema_failures_keep_deterministic_floor() -> None:
    provider = FakeLlmProvider(("not-json", "also-not-json"))
    output = InjectionController(llm_enabled=True, llm_provider=provider).propose(
        synthetic_injection_envelope()
    )

    assert len(provider.prompts) == LLM_SCHEMA_GROUNDING_RETRY_LIMIT + 1
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE
    assert output.primary_proposal is not output.baseline_proposal
    assert output.primary_proposal.envelope_digest == output.baseline_proposal.envelope_digest
    assert output.primary_proposal.provenance.fallback_used is True
    assert output.primary_proposal.intents[0].action is InjectionAction.HOLD


def test_ungrounded_llm_intent_falls_back_to_floor() -> None:
    bad = json.dumps(
        {
            "intents": [
                {
                    "action": "REALLOCATE_INJECTION",
                    "direction": "NONE",
                    "intensity": "SMALL",
                    "donor_wells": ["SYN-I1"],
                    "recipient_wells": ["SYN-I2"],
                    "source_regions": ["SYN-B1"],
                    "target_regions": ["SYN-B1"],
                    "evidence_ids": ["SYN-missing"],
                    "uncertainty": "MEDIUM",
                    "rationale_short": "Move support toward the weaker offset producer.",
                }
            ]
        }
    )
    provider = FakeLlmProvider((bad, bad))
    output = InjectionController(llm_enabled=True, llm_provider=provider).propose(
        _two_injector_envelope()
    )

    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE
    assert output.primary_proposal.provenance.fallback_used is True
    assert output.primary_proposal.intents[0].action is InjectionAction.HOLD


class _ExplodingProvider:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls = 0

    def complete(self, prompt: str) -> str:
        del prompt
        self.calls += 1
        raise self._exc


class _MalformedProvider:
    def __init__(self, response: object) -> None:
        self._response = response
        self.calls = 0

    def complete(self, prompt: str) -> object:
        del prompt
        self.calls += 1
        return self._response


@pytest.mark.parametrize("exc", [RuntimeError("boom"), TimeoutError("deadline")])
def test_generic_provider_exceptions_keep_deterministic_floor(exc: BaseException) -> None:
    provider = _ExplodingProvider(exc)
    output = InjectionController(llm_enabled=True, llm_provider=provider).propose(
        synthetic_injection_envelope()
    )

    assert provider.calls == LLM_SCHEMA_GROUNDING_RETRY_LIMIT + 1
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE
    assert output.primary_proposal is not output.baseline_proposal
    assert output.primary_proposal.provenance.fallback_used is True


@pytest.mark.parametrize("response", [None, {"intents": []}, b"{}"])
def test_malformed_provider_return_keeps_deterministic_floor(response: object) -> None:
    provider = _MalformedProvider(response)
    output = InjectionController(llm_enabled=True, llm_provider=provider).propose(
        synthetic_injection_envelope()
    )

    assert provider.calls == LLM_SCHEMA_GROUNDING_RETRY_LIMIT + 1
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.primary_proposal.provenance.fallback_used is True


def test_same_envelope_is_reproducible() -> None:
    envelope = synthetic_injection_envelope()
    controller = InjectionController(llm_enabled=False)

    first = controller.propose(envelope)
    second = controller.propose(envelope)

    assert first.primary_proposal.digest() == second.primary_proposal.digest()
    assert first.baseline_proposal.digest() == second.baseline_proposal.digest()
    assert first.primary_proposal is not second.primary_proposal
    assert first.baseline_proposal is not second.baseline_proposal


def test_rejects_invalid_envelope() -> None:
    with pytest.raises(InputValidationError):
        InjectionController(llm_enabled=False).propose(object())  # type: ignore[arg-type]


def test_rejects_prescriptive_evidence_all_or_nothing() -> None:
    envelope = synthetic_injection_envelope(
        response_map=EvidenceArtifact(
            evidence_id="SYN-bad-rate",
            kind="response_map",
            payload={"surface_rate": 240},
        )
    )
    with pytest.raises(EvidenceBuildError, match="prescriptive key"):
        InjectionController(llm_enabled=False).propose(envelope)


def test_output_intents_have_no_setpoint_fields() -> None:
    output = InjectionController(llm_enabled=False).propose(synthetic_injection_envelope())
    forbidden = {
        "bhp",
        "lrat",
        "rate",
        "setpoint",
        "surface_rate",
        "wconinje",
        "wconprod",
    }
    for intent in (*output.primary_proposal.intents, *output.baseline_proposal.intents):
        names = {field.name.lower() for field in dataclasses.fields(intent)}
        assert names.isdisjoint(forbidden)
        assert isinstance(intent, InjectionIntent)
