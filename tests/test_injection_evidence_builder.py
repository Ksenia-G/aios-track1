"""EvidenceBuilder catalogs envelope artifacts and rejects prescriptive setpoints."""

from __future__ import annotations

import pytest

from aios.mas.evidence import EvidenceArtifact
from aios.mas.specialists.injection.evidence_builder import (
    EvidenceBuilder,
    EvidenceBuildError,
)
from tests.support.mas_team_fixtures import (
    synthetic_evidence_artifact,
    synthetic_injection_envelope,
)


def test_collects_present_slots_and_memory_in_stable_order() -> None:
    envelope = synthetic_injection_envelope()
    context = EvidenceBuilder().build(envelope)

    assert [item.evidence_id for item in context.artifacts] == [
        "SYN-pressure-1",
        "SYN-sweep-1",
        "SYN-memory-1",
    ]
    assert context.contains("SYN-pressure-1")
    assert context.contains("SYN-sweep-1")
    assert context.contains("SYN-memory-1")
    assert context.get("SYN-pressure-1") is envelope.pressure_diagnostic


def test_skips_missing_optional_slots() -> None:
    envelope = synthetic_injection_envelope(geology_card=None, response_map=None)
    context = EvidenceBuilder().build(envelope)

    assert "geology_card" not in {item.kind for item in context.artifacts}
    assert context.get("missing") is None


def test_includes_geology_and_compensation_when_present() -> None:
    envelope = synthetic_injection_envelope(
        geology_card=synthetic_evidence_artifact("SYN-geology-1", "geology_card"),
        compensation_state=synthetic_evidence_artifact("SYN-comp-1", "compensation_state"),
    )
    context = EvidenceBuilder().build(envelope)

    assert [item.evidence_id for item in context.artifacts] == [
        "SYN-pressure-1",
        "SYN-sweep-1",
        "SYN-geology-1",
        "SYN-comp-1",
        "SYN-memory-1",
    ]


def test_rejects_duplicate_evidence_ids() -> None:
    envelope = synthetic_injection_envelope(
        geology_card=synthetic_evidence_artifact("SYN-pressure-1", "geology_card"),
    )
    with pytest.raises(EvidenceBuildError, match="duplicate evidence_id"):
        EvidenceBuilder().build(envelope)


def test_rejects_prescriptive_rate_key() -> None:
    envelope = synthetic_injection_envelope(
        response_map=EvidenceArtifact(
            evidence_id="SYN-bad-rate",
            kind="response_map",
            payload={"surface_rate": 240},
        )
    )
    with pytest.raises(EvidenceBuildError, match="surface_rate"):
        EvidenceBuilder().build(envelope)


def test_accepts_real_repository_diagnostics() -> None:
    """PressureDiagnostic/SweepDiagnostic must reach the specialist."""
    from aios.diagnostics import PressureDiagnostic, SweepDiagnostic
    from tests.support.mas_team_fixtures import synthetic_state_card

    card = synthetic_state_card()
    envelope = synthetic_injection_envelope(
        pressure_diagnostic=EvidenceArtifact(
            evidence_id="SYN-real-pressure",
            kind="pressure_diagnostic",
            payload=PressureDiagnostic().propose(card).to_json(),
        ),
        sweep_diagnostic=EvidenceArtifact(
            evidence_id="SYN-real-sweep",
            kind="sweep_diagnostic",
            payload=SweepDiagnostic().propose(card).to_json(),
        ),
    )
    context = EvidenceBuilder().build(envelope)
    assert {"SYN-real-pressure", "SYN-real-sweep"} <= context.known_ids


def test_rejects_unknown_numeric_target_key() -> None:
    envelope = synthetic_injection_envelope(
        response_map=EvidenceArtifact(
            evidence_id="SYN-bad-target",
            kind="response_map",
            payload={"target": 240},
        )
    )
    with pytest.raises(EvidenceBuildError, match="target"):
        EvidenceBuilder().build(envelope)


def test_rejects_setpoint_smuggled_in_note() -> None:
    envelope = synthetic_injection_envelope(
        response_map=EvidenceArtifact(
            evidence_id="SYN-bad-note",
            kind="response_map",
            payload={"note": "set the donor to 240"},
        )
    )
    with pytest.raises(EvidenceBuildError, match="note"):
        EvidenceBuilder().build(envelope)


def test_allows_observational_wbhp_and_wwir() -> None:
    envelope = synthetic_injection_envelope(
        compensation_state=EvidenceArtifact(
            evidence_id="SYN-obs-1",
            kind="compensation_state",
            payload={"wbhp": 180.0, "wwir": 150.0, "status": "UNRESOLVED"},
        )
    )
    context = EvidenceBuilder().build(envelope)

    assert context.contains("SYN-obs-1")
    assert context.get("SYN-obs-1").payload["wbhp"] == 180.0


def test_same_envelope_is_reproducible() -> None:
    envelope = synthetic_injection_envelope()
    builder = EvidenceBuilder()

    first = builder.build(envelope)
    second = builder.build(envelope)

    assert [item.evidence_id for item in first.artifacts] == [
        item.evidence_id for item in second.artifacts
    ]
    assert first.known_ids == second.known_ids
