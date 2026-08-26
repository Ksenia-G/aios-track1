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
    assert "surface_rate" not in json.dumps(payload)
    for item in payload["evidence"]:
        assert set(item) == {"evidence_id", "kind", "source_version", "payload"}


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


def test_prompt_carries_observational_evidence_content() -> None:
    """An evidence_id without content is not evidence.

    The specialist has to be able to reason about what a diagnostic says, not
    merely that a diagnostic exists.
    """
    from aios.mas.evidence import EvidenceArtifact

    envelope = synthetic_injection_envelope(
        compensation_state=EvidenceArtifact(
            evidence_id="SYN-obs-1",
            kind="compensation_state",
            payload={"wbhp": 180.0, "wwir": 150.0, "status": "UNRESOLVED"},
        )
    )
    payload = json.loads(PromptBuilder().build(envelope))
    evidence = {item["evidence_id"]: item for item in payload["evidence"]}

    assert evidence["SYN-obs-1"]["payload"]["wbhp"] == 180.0
    assert evidence["SYN-obs-1"]["payload"]["wwir"] == 150.0
    assert evidence["SYN-obs-1"]["payload"]["status"] == "UNRESOLVED"


def test_materially_different_diagnostics_produce_different_prompts() -> None:
    """Two different physical states must not look identical to the model."""
    from aios.mas.evidence import EvidenceArtifact

    def envelope_with(condition: str, trend: float) -> object:
        return synthetic_injection_envelope(
            pressure_diagnostic=EvidenceArtifact(
                evidence_id="SYN-pressure-1",
                kind="pressure_diagnostic",
                payload={"pressure_condition": condition, "pressure_trend": trend},
            )
        )

    stable = PromptBuilder().build(envelope_with("PRESSURE_SUPPORT_STABLE", 0.1))
    collapsing = PromptBuilder().build(envelope_with("PRESSURE_SUPPORT_NEEDED", -12.4))

    assert stable != collapsing
    assert "PRESSURE_SUPPORT_STABLE" in stable
    assert "PRESSURE_SUPPORT_NEEDED" in collapsing


def test_real_diagnostic_substance_reaches_the_prompt() -> None:
    from aios.diagnostics import PressureDiagnostic
    from aios.mas.evidence import EvidenceArtifact
    from tests.support.mas_team_fixtures import synthetic_state_card

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
    evidence = {item["evidence_id"]: item for item in payload["evidence"]}
    body = evidence["SYN-real-pressure"]["payload"]

    assert body["proposal_type"] == str(diagnostic.proposal_type)
    assert body["reason"]["pressure_condition"] == diagnostic.reason.pressure_condition
    assert body["confidence"] == diagnostic.confidence


def test_prompt_still_refuses_prescriptive_evidence_content() -> None:
    """Restoring content must not restore the anti-anchoring leak."""
    from aios.mas.evidence import EvidenceArtifact
    from aios.mas.specialists.injection.evidence_builder import EvidenceBuildError

    envelope = synthetic_injection_envelope(
        compensation_state=EvidenceArtifact(
            evidence_id="SYN-bad",
            kind="compensation_state",
            payload={"note": "Set injection to 240 m3/day"},
        )
    )
    with pytest.raises(EvidenceBuildError):
        PromptBuilder().build(envelope)
