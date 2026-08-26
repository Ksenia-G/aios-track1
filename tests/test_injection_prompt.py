"""PromptBuilder.build(envelope) never receives baseline."""

from __future__ import annotations

import inspect
import json

import pytest

from aios.mas.common import AgentRole, DecisionRound
from aios.mas.evidence import ProposalReference
from aios.mas.invariants import require_no_baseline_field
from aios.mas.protocols import SpecialistPromptBuilder
from aios.mas.specialists.injection.prompt import (
    PROMPT_BUILDER_VERSION,
    PromptBuilder,
    PromptBuildError,
)
from tests.support.mas_team_fixtures import synthetic_injection_envelope


def test_build_signature_is_envelope_only() -> None:
    parameters = inspect.signature(PromptBuilder.build).parameters
    assert [name for name in parameters if name != "self"] == ["envelope"]
    assert isinstance(PromptBuilder(), SpecialistPromptBuilder)


def test_calls_anti_anchoring_guard() -> None:
    envelope = synthetic_injection_envelope()
    require_no_baseline_field(envelope)
    prompt = PromptBuilder().build(envelope)
    assert "baseline" not in prompt.casefold()
    assert "deterministic_baseline" not in prompt.casefold()


def test_includes_allowed_actions_and_evidence_ids() -> None:
    envelope = synthetic_injection_envelope()
    payload = json.loads(PromptBuilder().build(envelope))

    assert payload["prompt_builder_version"] == PROMPT_BUILDER_VERSION
    assert payload["role"] == "INJECTION"
    assert "HOLD" in payload["allowed_actions"]
    assert "REALLOCATE_INJECTION" in payload["allowed_actions"]
    evidence_ids = [item["evidence_id"] for item in payload["evidence"]]
    assert evidence_ids == ["SYN-pressure-1", "SYN-sweep-1", "SYN-memory-1"]
    assert payload["peer_proposals"] == []
    well_ids = [item["well_id"] for item in payload["wells"]]
    assert "SYN-P1" in well_ids
    assert "current_target" not in payload["wells"][0]
    assert "surface_rate" not in json.dumps(payload)
    for item in payload["evidence"]:
        assert set(item) <= {"evidence_id", "kind", "source_version"}
        assert "payload" not in item


def test_round2_includes_peer_digest_not_baseline() -> None:
    envelope = synthetic_injection_envelope(
        round=DecisionRound.ROUND_2,
        peer_proposals=(ProposalReference(role=AgentRole.PRODUCTION, proposal_digest="d" * 64),),
    )
    payload = json.loads(PromptBuilder().build(envelope))

    assert payload["round"] == "ROUND_2"
    assert payload["peer_proposals"] == [{"role": "PRODUCTION", "proposal_digest": "d" * 64}]
    assert "baseline" not in json.dumps(payload).casefold()


def test_same_envelope_is_reproducible() -> None:
    envelope = synthetic_injection_envelope()
    builder = PromptBuilder()

    assert builder.build(envelope) == builder.build(envelope)


def test_rejects_non_envelope() -> None:
    with pytest.raises(PromptBuildError, match="expected InjectionEnvelope"):
        PromptBuilder().build(object())  # type: ignore[arg-type]


def test_prompt_evidence_omits_payload_even_when_observational() -> None:
    from aios.mas.evidence import EvidenceArtifact

    envelope = synthetic_injection_envelope(
        compensation_state=EvidenceArtifact(
            evidence_id="SYN-obs-1",
            kind="compensation_state",
            payload={"wbhp": 180.0, "wwir": 150.0, "status": "UNRESOLVED"},
        )
    )
    payload = json.loads(PromptBuilder().build(envelope))
    dumped = json.dumps(payload)

    assert "180" not in dumped
    assert "wwir" not in dumped
    assert "payload" not in dumped
    assert any(item["evidence_id"] == "SYN-obs-1" for item in payload["evidence"])
