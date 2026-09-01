"""Typed evidence/context layer: descriptive-only, no prescriptive setpoints."""

from __future__ import annotations

import datetime

import pytest

from aios.mas.common import AgentRole
from aios.mas.evidence import (
    EvidenceArtifact,
    HorizonContext,
    MemorySlice,
    ProposalReference,
    SimulationBudget,
)


def _artifact(evidence_id: str = "pressure-1") -> EvidenceArtifact:
    return EvidenceArtifact(
        evidence_id=evidence_id,
        kind="pressure_diagnostic",
        payload={"average_pressure": 211.5, "trend": "declining"},
        source_version="aios.agents.pressure_agent/1.0.0",
    )


def test_evidence_artifact_to_json_is_json_compatible() -> None:
    payload = _artifact().to_json()
    assert payload["evidence_id"] == "pressure-1"
    assert payload["kind"] == "pressure_diagnostic"
    assert payload["payload"]["average_pressure"] == 211.5
    assert payload["source_version"] == "aios.agents.pressure_agent/1.0.0"


def test_evidence_artifact_digest_is_deterministic() -> None:
    assert _artifact().digest() == _artifact().digest()


def test_evidence_artifact_digest_changes_with_payload() -> None:
    other = EvidenceArtifact(
        evidence_id="pressure-1",
        kind="pressure_diagnostic",
        payload={"average_pressure": 999.0},
    )
    assert _artifact().digest() != other.digest()


def test_evidence_artifact_requires_non_empty_id_and_kind() -> None:
    with pytest.raises(ValueError):
        EvidenceArtifact(evidence_id="", kind="pressure_diagnostic", payload={})
    with pytest.raises(ValueError):
        EvidenceArtifact(evidence_id="x", kind="", payload={})


def test_proposal_reference_round_trips() -> None:
    ref = ProposalReference(role=AgentRole.PRODUCTION, proposal_digest="a" * 64)
    assert ref.to_json() == {"role": "PRODUCTION", "proposal_digest": "a" * 64}


def test_proposal_reference_requires_digest() -> None:
    with pytest.raises(ValueError):
        ProposalReference(role=AgentRole.PRODUCTION, proposal_digest="")


def test_horizon_context_accepts_valid_window() -> None:
    ctx = HorizonContext(
        decision_date=datetime.date(2020, 4, 1),
        horizon_end=datetime.date(2020, 4, 1),
        remaining_steps=0,
    )
    assert ctx.remaining_steps == 0


def test_horizon_context_rejects_decision_after_horizon_end() -> None:
    with pytest.raises(ValueError):
        HorizonContext(
            decision_date=datetime.date(2020, 5, 1),
            horizon_end=datetime.date(2020, 4, 1),
            remaining_steps=1,
        )


def test_horizon_context_rejects_negative_remaining_steps() -> None:
    with pytest.raises(ValueError):
        HorizonContext(
            decision_date=datetime.date(2020, 4, 1),
            horizon_end=datetime.date(2020, 5, 1),
            remaining_steps=-1,
        )


def test_memory_slice_is_immutable_tuple_of_evidence() -> None:
    slice_ = MemorySlice(records=(_artifact("a"), _artifact("b")))
    assert len(slice_.records) == 2
    assert slice_.to_json()["records"][0]["evidence_id"] == "a"


def test_simulation_budget_requires_positive_max_candidates() -> None:
    SimulationBudget(max_candidates=1)
    with pytest.raises(ValueError):
        SimulationBudget(max_candidates=0)
    with pytest.raises(ValueError):
        SimulationBudget(max_candidates=-3)
