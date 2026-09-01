"""Deterministic evidence view for Production Controller branches."""

from __future__ import annotations

from dataclasses import dataclass

from aios.evaluation.canonical import JsonValue
from aios.mas.envelopes import ProductionEnvelope
from aios.mas.evidence import EvidenceArtifact
from aios.mas.specialists.authority import (
    PrescriptiveAuthorityError,
    reject_prescriptive_payload,
)
from aios.state import WellRole

from .errors import EvidenceBuildError

EVIDENCE_BUILDER_VERSION = "aios.mas.specialists.production.evidence/1.0.0"

_ENVELOPE_EVIDENCE_FIELDS = (
    "pressure_diagnostic",
    "sweep_diagnostic",
    "geology_card",
    "response_map",
    "compensation_state",
    "economic_context",
)


@dataclass(frozen=True, slots=True)
class ProductionEvidence:
    """Canonical collection of facts visible to baseline/LLM/auditors."""

    artifacts: tuple[EvidenceArtifact, ...]

    def __post_init__(self) -> None:
        ids = tuple(artifact.evidence_id for artifact in self.artifacts)
        if len(ids) != len(set(ids)):
            duplicates = sorted({item for item in ids if ids.count(item) > 1})
            raise EvidenceBuildError(f"duplicate evidence_ids: {tuple(duplicates)}")

    @property
    def evidence_ids(self) -> frozenset[str]:
        return frozenset(artifact.evidence_id for artifact in self.artifacts)

    def to_json(self) -> list[JsonValue]:
        return [artifact.to_json() for artifact in self.artifacts]


@dataclass(frozen=True, slots=True)
class EvidenceBuilder:
    """Build facts only; never infer a control or an official constraint verdict."""

    version: str = EVIDENCE_BUILDER_VERSION

    def build(self, envelope: ProductionEnvelope) -> ProductionEvidence:
        artifacts: list[EvidenceArtifact] = []

        def admit(artifact: EvidenceArtifact) -> None:
            """Единая граница для КАЖДОГО источника evidence, включая StateCard.

            StateCard-производные артефакты проходят ту же проверку, что и
            внешние: `constraints.search` и `geology.fault_relationship` --
            свободные Mapping[str, JsonValue], через которые prescriptive
            control ранее попадал в промпт специалиста незамеченным.
            """
            try:
                reject_prescriptive_payload(artifact.payload, artifact.evidence_id)
            except PrescriptiveAuthorityError as exc:
                raise EvidenceBuildError(str(exc)) from exc
            artifacts.append(artifact)

        for field_name in _ENVELOPE_EVIDENCE_FIELDS:
            artifact = getattr(envelope, field_name)
            if artifact is not None:
                admit(artifact)

        for artifact in envelope.memory_slice.records:
            admit(artifact)

        card = envelope.state_card
        admit(
            EvidenceArtifact(
                evidence_id="production.state.field",
                kind="state_card_field_observation",
                payload=card.field.to_json(),
                source_version=self.version,
            )
        )
        admit(
            EvidenceArtifact(
                evidence_id="production.state.constraints",
                kind="state_card_constraint_context",
                payload=card.constraints.to_json(),
                source_version=self.version,
            )
        )
        admit(
            EvidenceArtifact(
                evidence_id="production.state.geology",
                kind="state_card_geology_context",
                payload=card.geology.to_json(),
                source_version=self.version,
            )
        )
        admit(
            EvidenceArtifact(
                evidence_id="production.state.uncertainty",
                kind="state_card_uncertainty",
                payload=card.uncertainty.to_json(),
                source_version=self.version,
            )
        )
        for well in sorted(
            (item for item in card.wells if item.role is WellRole.PRODUCER),
            key=lambda item: item.well_id,
        ):
            admit(
                EvidenceArtifact(
                    evidence_id=f"production.state.well:{well.well_id}",
                    kind="producer_state_observation",
                    payload=well.to_json(),
                    source_version=self.version,
                )
            )

        return ProductionEvidence(tuple(artifacts))
