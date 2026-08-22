"""Typed evidence/context layer.

EvidenceArtifact -- описательный контекст (WBHP, WLPR, тренды, диагностика
Pressure/Sweep как evidence, не prescriptive control). Контракт этого модуля
не занимается content policing произвольного payload: проверка того, что
конкретный EvidenceArtifact не протаскивает prescriptive setpoint под видом
evidence -- ответственность EvidenceBuilder/валидаторов следующих задач.

MemorySlice -- только immutable input contract; MemoryStore здесь не
реализован. SimulationBudget не содержит MPC Tier0/Tier1/Tier2: та политика
ещё provisional и будет отдельной задачей.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field

from aios.evaluation.canonical import JsonValue, digest, normalize_json
from aios.mas.common import AgentRole


class EvidenceError(ValueError):
    """Evidence/context contract нарушен."""


def _require_text(value: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{where}: expected non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    """Один типизированный, дайджестируемый фрагмент evidence."""

    evidence_id: str
    kind: str
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    source_version: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.evidence_id, "evidence_artifact.evidence_id")
        _require_text(self.kind, "evidence_artifact.kind")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "payload": normalize_json(dict(self.payload)),
            "source_version": self.source_version,
        }

    def digest(self) -> str:
        return digest(self.to_json())


@dataclass(frozen=True, slots=True)
class ProposalReference:
    """Ссылка на digest предложения другого агента (для Round 2)."""

    role: AgentRole
    proposal_digest: str

    def __post_init__(self) -> None:
        AgentRole(self.role)
        _require_text(self.proposal_digest, "proposal_reference.proposal_digest")

    def to_json(self) -> dict[str, JsonValue]:
        return {"role": str(self.role), "proposal_digest": self.proposal_digest}


@dataclass(frozen=True, slots=True)
class HorizonContext:
    """Окно принятия решения на текущем decision step."""

    decision_date: datetime.date
    horizon_end: datetime.date
    remaining_steps: int

    def __post_init__(self) -> None:
        if self.decision_date > self.horizon_end:
            raise EvidenceError("horizon_context: decision_date must be <= horizon_end")
        if self.remaining_steps < 0:
            raise EvidenceError("horizon_context.remaining_steps: must be >= 0")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "decision_date": self.decision_date.isoformat(),
            "horizon_end": self.horizon_end.isoformat(),
            "remaining_steps": self.remaining_steps,
        }


@dataclass(frozen=True, slots=True)
class MemorySlice:
    """Immutable срез памяти, переданный агенту. MemoryStore не реализован."""

    records: tuple[EvidenceArtifact, ...] = ()

    def to_json(self) -> dict[str, JsonValue]:
        return {"records": [record.to_json() for record in self.records]}


@dataclass(frozen=True, slots=True)
class SimulationBudget:
    """Лимит числа кандидатов, допустимых к прогону на этом decision step."""

    max_candidates: int

    def __post_init__(self) -> None:
        if self.max_candidates <= 0:
            raise EvidenceError("simulation_budget.max_candidates: must be > 0")

    def to_json(self) -> dict[str, JsonValue]:
        return {"max_candidates": self.max_candidates}
