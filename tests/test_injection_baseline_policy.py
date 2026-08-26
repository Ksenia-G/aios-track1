"""BaselinePolicy emits a separate DETERMINISTIC_BASELINE HOLD proposal."""

from __future__ import annotations

import pytest

from aios.mas.common import AgentRole, Direction, Intensity, ProposalSource
from aios.mas.intents import InjectionAction
from aios.mas.specialists.injection.baseline_policy import BaselinePolicy, BaselinePolicyError
from tests.support.mas_team_fixtures import synthetic_injection_envelope


def test_baseline_is_deterministic_hold() -> None:
    envelope = synthetic_injection_envelope()
    proposal = BaselinePolicy().propose(envelope)

    assert proposal.source is ProposalSource.DETERMINISTIC_BASELINE
    assert proposal.role is AgentRole.INJECTION
    assert proposal.envelope_digest == envelope.digest()
    assert len(proposal.intents) == 1
    intent = proposal.intents[0]
    assert intent.action is InjectionAction.HOLD
    assert intent.direction is Direction.NONE
    assert intent.intensity is Intensity.NONE
    assert intent.donor_wells == ()
    assert intent.recipient_wells == ()
    assert intent.evidence_ids == ()
    assert proposal.grounding_report.passed is True
    assert proposal.provenance.prompt_digest is None
    assert proposal.provenance.fallback_used is False


def test_same_envelope_is_reproducible() -> None:
    envelope = synthetic_injection_envelope()
    policy = BaselinePolicy()

    assert policy.propose(envelope).digest() == policy.propose(envelope).digest()


def test_different_envelopes_produce_different_digests() -> None:
    first = BaselinePolicy().propose(synthetic_injection_envelope(variant_id="SYN-a"))
    second = BaselinePolicy().propose(synthetic_injection_envelope(variant_id="SYN-b"))

    assert first.digest() != second.digest()


def test_rejects_envelope_without_hold() -> None:
    envelope = synthetic_injection_envelope(allowed_actions=(InjectionAction.REALLOCATE_INJECTION,))
    with pytest.raises(BaselinePolicyError, match="must include HOLD"):
        BaselinePolicy().propose(envelope)
