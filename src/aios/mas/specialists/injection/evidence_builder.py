"""Каталог evidence для Injection Controller.

Evidence уже лежит в envelope как `EvidenceArtifact`. Этот модуль не вызывает
Pressure/Sweep diagnostic и не считает compensation: он собирает слоты в
детерминированный каталог, отклоняет дубликаты id и prescriptive setpoints
в payload. Числа вроде WBHP/WWIR как факты допустимы.
"""

from __future__ import annotations

from dataclasses import dataclass

from aios.mas.envelopes import InjectionEnvelope
from aios.mas.evidence import EvidenceArtifact
from aios.mas.specialists.injection.prescriptive import first_prescriptive_payload_key

_SLOT_ORDER: tuple[str, ...] = (
    "pressure_diagnostic",
    "sweep_diagnostic",
    "geology_card",
    "response_map",
    "compensation_state",
)


class EvidenceBuildError(ValueError):
    """Каталог evidence собрать нельзя."""


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """Детерминированный срез evidence, на который потом ссылаются intents."""

    artifacts: tuple[EvidenceArtifact, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for artifact in self.artifacts:
            if artifact.evidence_id in seen:
                raise EvidenceBuildError(f"duplicate evidence_id {artifact.evidence_id!r}")
            seen.add(artifact.evidence_id)

    @property
    def known_ids(self) -> frozenset[str]:
        return frozenset(artifact.evidence_id for artifact in self.artifacts)

    def get(self, evidence_id: str) -> EvidenceArtifact | None:
        for artifact in self.artifacts:
            if artifact.evidence_id == evidence_id:
                return artifact
        return None

    def contains(self, evidence_id: str) -> bool:
        return any(artifact.evidence_id == evidence_id for artifact in self.artifacts)


class EvidenceBuilder:
    """Собрать каталог из envelope без setpoint-полей и без baseline."""

    def build(self, envelope: InjectionEnvelope) -> EvidenceContext:
        artifacts: list[EvidenceArtifact] = []
        for slot in _SLOT_ORDER:
            artifact = getattr(envelope, slot)
            if artifact is None:
                continue
            _reject_prescriptive_payload(artifact)
            artifacts.append(artifact)
        for artifact in envelope.memory_slice.records:
            _reject_prescriptive_payload(artifact)
            artifacts.append(artifact)
        return EvidenceContext(tuple(artifacts))


def _reject_prescriptive_payload(artifact: EvidenceArtifact) -> None:
    offender = first_prescriptive_payload_key(artifact.payload)
    if offender is not None:
        raise EvidenceBuildError(
            f"evidence {artifact.evidence_id!r} carries prescriptive key {offender!r}"
        )
