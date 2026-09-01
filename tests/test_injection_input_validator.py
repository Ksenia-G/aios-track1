"""InputValidator is the first Injection Controller gate over InjectionEnvelope."""

from __future__ import annotations

import pytest

from aios.mas.common import AgentRole, DecisionRound
from aios.mas.evidence import ProposalReference
from aios.mas.intents import InjectionAction
from aios.mas.specialists.injection import InputValidationError, InputValidator
from tests.support.mas_team_fixtures import synthetic_injection_envelope


def test_valid_round1_envelope_passes() -> None:
    envelope = synthetic_injection_envelope()
    validated = InputValidator().validate(envelope)

    assert validated is envelope
    assert validated.round is DecisionRound.ROUND_1
    assert validated.peer_proposals == ()


def test_valid_round2_envelope_with_peer_passes() -> None:
    envelope = synthetic_injection_envelope(
        round=DecisionRound.ROUND_2,
        peer_proposals=(ProposalReference(role=AgentRole.PRODUCTION, proposal_digest="d" * 64),),
    )
    validated = InputValidator().validate(envelope)

    assert validated is envelope
    assert validated.round is DecisionRound.ROUND_2
    assert len(validated.peer_proposals) == 1


def test_rejects_non_envelope() -> None:
    with pytest.raises(InputValidationError, match="expected InjectionEnvelope"):
        InputValidator().validate(object())


def test_rejects_unknown_schema_version() -> None:
    envelope = synthetic_injection_envelope(schema_version="aios.mas.contracts/0.0.0")
    with pytest.raises(InputValidationError, match="schema_version"):
        InputValidator().validate(envelope)


def test_rejects_empty_allowed_actions() -> None:
    envelope = synthetic_injection_envelope(allowed_actions=())
    with pytest.raises(InputValidationError, match="allowed_actions must not be empty"):
        InputValidator().validate(envelope)


def test_rejects_allowed_actions_without_hold() -> None:
    envelope = synthetic_injection_envelope(allowed_actions=(InjectionAction.REALLOCATE_INJECTION,))
    with pytest.raises(InputValidationError, match="must include HOLD"):
        InputValidator().validate(envelope)


def test_same_envelope_is_reproducible() -> None:
    envelope = synthetic_injection_envelope()
    validator = InputValidator()

    first = validator.validate(envelope)
    second = validator.validate(envelope)

    assert first.digest() == second.digest()


def test_round2_rejects_injection_peer() -> None:
    envelope = synthetic_injection_envelope(
        round=DecisionRound.ROUND_2,
        peer_proposals=(ProposalReference(role=AgentRole.INJECTION, proposal_digest="d" * 64),),
    )
    with pytest.raises(InputValidationError, match="must be PRODUCTION"):
        InputValidator().validate(envelope)


def test_round2_rejects_arbiter_peer() -> None:
    envelope = synthetic_injection_envelope(
        round=DecisionRound.ROUND_2,
        peer_proposals=(ProposalReference(role=AgentRole.ARBITER, proposal_digest="d" * 64),),
    )
    with pytest.raises(InputValidationError, match="must be PRODUCTION"):
        InputValidator().validate(envelope)


# ---------------------------------------------------------------------------
# COMPLETION I4 -- Round2 peer identities must be unambiguous.
# ---------------------------------------------------------------------------


def test_round2_rejects_duplicate_peer_proposal_digests() -> None:
    duplicate = ProposalReference(role=AgentRole.PRODUCTION, proposal_digest="e" * 64)
    envelope = synthetic_injection_envelope(
        round=DecisionRound.ROUND_2,
        peer_proposals=(duplicate, duplicate),
    )
    with pytest.raises(InputValidationError, match="repeats proposal_digest"):
        InputValidator().validate(envelope)


def test_round2_accepts_distinct_production_peers() -> None:
    envelope = synthetic_injection_envelope(
        round=DecisionRound.ROUND_2,
        peer_proposals=(
            ProposalReference(role=AgentRole.PRODUCTION, proposal_digest="a" * 64),
            ProposalReference(role=AgentRole.PRODUCTION, proposal_digest="b" * 64),
        ),
    )
    assert InputValidator().validate(envelope) is envelope
