"""Proposal contracts: grounding, provenance, audit, specialist outputs.

Baseline и primary -- всегда два разных объекта (anti-anchoring): baseline
обязан прийти с source == DETERMINISTIC_BASELINE, primary может быть любым
источником, включая DETERMINISTIC_FALLBACK для llm_enabled=False ablation.

Audit-поля (latency, declined alternatives) -- только для наблюдаемости и
никогда не входят в семантический дайджест предложения: иначе одно и то же
решение по содержанию получало бы разные digest в зависимости от того, как
долго отвечала модель.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aios.evaluation.canonical import JsonValue, digest
from aios.mas.common import AgentRole, ProposalSource
from aios.mas.intents import InjectionIntent, ProductionIntent


class ProposalError(ValueError):
    """Proposal contract нарушен."""


def _require_text(value: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProposalError(f"{where}: expected non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class GroundingReport:
    """Итог проверки, что evidence_ids в intents реально существуют в envelope."""

    grounded_evidence_ids: tuple[str, ...] = ()
    unknown_evidence_ids: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.unknown_evidence_ids == ()

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "grounded_evidence_ids": sorted(self.grounded_evidence_ids),
            "unknown_evidence_ids": sorted(self.unknown_evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class ProposalProvenance:
    """Происхождение предложения: какой промпт/модель/ответ его породили."""

    prompt_builder_version: str | None = None
    prompt_digest: str | None = None
    model_id: str | None = None
    response_digest: str | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "prompt_builder_version": self.prompt_builder_version,
            "prompt_digest": self.prompt_digest,
            "model_id": self.model_id,
            "response_digest": self.response_digest,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True, slots=True)
class ProposalAudit:
    """Audit-only метаданные. Не влияют на downstream candidate selection.

    Никакого private chain-of-thought здесь: только короткие auditable
    метки (latency, отклонённые альтернативы по имени/коду).
    """

    latency_ms: int | None = None
    declined_alternatives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ProposalError("proposal_audit.latency_ms: must be >= 0")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "latency_ms": self.latency_ms,
            "declined_alternatives": list(self.declined_alternatives),
        }


def _validate_proposal(
    *,
    schema_version: str,
    envelope_digest: str,
    grounding_report: GroundingReport,
    where: str,
) -> None:
    _require_text(schema_version, f"{where}.schema_version")
    _require_text(envelope_digest, f"{where}.envelope_digest")
    if not grounding_report.passed:
        raise ProposalError(
            f"{where}: proposal is not grounded, unknown_evidence_ids="
            f"{grounding_report.unknown_evidence_ids}"
        )


@dataclass(frozen=True, slots=True)
class ProductionProposal:
    """Скомпилированное предложение Production-специалиста."""

    schema_version: str
    envelope_digest: str
    source: ProposalSource
    intents: tuple[ProductionIntent, ...]
    grounding_report: GroundingReport
    provenance: ProposalProvenance
    audit: ProposalAudit = field(default_factory=ProposalAudit)

    def __post_init__(self) -> None:
        ProposalSource(self.source)
        _validate_proposal(
            schema_version=self.schema_version,
            envelope_digest=self.envelope_digest,
            grounding_report=self.grounding_report,
            where="production_proposal",
        )

    @property
    def role(self) -> AgentRole:
        return AgentRole.PRODUCTION

    def _semantic_payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "role": str(self.role),
            "envelope_digest": self.envelope_digest,
            "source": str(self.source),
            "intents": [intent.to_json() for intent in self.intents],
            "grounding_report": self.grounding_report.to_json(),
            "provenance": self.provenance.to_json(),
        }

    def to_json(self) -> dict[str, JsonValue]:
        payload = self._semantic_payload()
        payload["audit"] = self.audit.to_json()
        return payload

    def digest(self) -> str:
        return digest(self._semantic_payload())


@dataclass(frozen=True, slots=True)
class InjectionProposal:
    """Скомпилированное предложение Injection-специалиста."""

    schema_version: str
    envelope_digest: str
    source: ProposalSource
    intents: tuple[InjectionIntent, ...]
    grounding_report: GroundingReport
    provenance: ProposalProvenance
    audit: ProposalAudit = field(default_factory=ProposalAudit)

    def __post_init__(self) -> None:
        ProposalSource(self.source)
        _validate_proposal(
            schema_version=self.schema_version,
            envelope_digest=self.envelope_digest,
            grounding_report=self.grounding_report,
            where="injection_proposal",
        )

    @property
    def role(self) -> AgentRole:
        return AgentRole.INJECTION

    def _semantic_payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "role": str(self.role),
            "envelope_digest": self.envelope_digest,
            "source": str(self.source),
            "intents": [intent.to_json() for intent in self.intents],
            "grounding_report": self.grounding_report.to_json(),
            "provenance": self.provenance.to_json(),
        }

    def to_json(self) -> dict[str, JsonValue]:
        payload = self._semantic_payload()
        payload["audit"] = self.audit.to_json()
        return payload

    def digest(self) -> str:
        return digest(self._semantic_payload())


def _validate_injection_beam(
    *,
    primary: InjectionProposal,
    baseline: InjectionProposal,
    alternatives: tuple[InjectionProposal, ...],
) -> None:
    """Пучок обязан состоять из РАЗНЫХ структурных гипотез одного вызова.

    Повтор primary/baseline внутри alternatives не добавил бы поиску ни одной
    новой гипотезы, зато занял бы слот физического бюджета.
    """
    where = "injection_agent_output.alternatives"
    seen = {primary.digest(), baseline.digest()}
    for index, alternative in enumerate(alternatives):
        if alternative.source is ProposalSource.DETERMINISTIC_BASELINE:
            raise ProposalError(
                f"{where}[{index}]: an alternative is a structural hypothesis, "
                "not a second deterministic floor"
            )
        if alternative.envelope_digest != primary.envelope_digest:
            raise ProposalError(
                f"{where}[{index}]: every hypothesis must share the primary envelope_digest"
            )
        digest_ = alternative.digest()
        if digest_ in seen:
            raise ProposalError(
                f"{where}[{index}]: duplicates an existing proposal; alternatives must "
                "carry materially different structural hypotheses"
            )
        seen.add(digest_)


def _validate_agent_output(
    *,
    primary: ProductionProposal | InjectionProposal,
    baseline: ProductionProposal | InjectionProposal,
    where: str,
) -> None:
    if baseline.source is not ProposalSource.DETERMINISTIC_BASELINE:
        raise ProposalError(f"{where}.baseline_proposal: source must be DETERMINISTIC_BASELINE")
    if primary.envelope_digest != baseline.envelope_digest:
        raise ProposalError(f"{where}: primary and baseline must share the same envelope_digest")


@dataclass(frozen=True, slots=True)
class ProductionAgentOutput:
    """Итог одного вызова Production-специалиста: primary + отдельный baseline."""

    primary_proposal: ProductionProposal
    baseline_proposal: ProductionProposal

    def __post_init__(self) -> None:
        _validate_agent_output(
            primary=self.primary_proposal,
            baseline=self.baseline_proposal,
            where="production_agent_output",
        )


@dataclass(frozen=True, slots=True)
class InjectionAgentOutput:
    """Итог ОДНОГО вызова Injection-специалиста: пучок гипотез + baseline.

    `alternatives` -- дополнительные СТРУКТУРНЫЕ гипотезы того же самого
    вызова провайдера, а не отдельные обращения к модели. Одна гипотеза за
    эпоху означала, что стохастический выбор действия мог на целую эпоху
    выключить исправно работающую downstream-способность.

    Ранг задаётся порядком: primary -- ранг 1, alternatives -- ранги 2..N.
    Отдельного поля ранга нет намеренно: порядок кортежа уже детерминирован,
    а второй источник той же правды разошёлся бы с ним.

    Расширение аддитивное: пустой кортеж по умолчанию, и вызывающий, который
    его не передаёт, ведёт себя ровно как раньше. Численных величин в этом
    контракте нет и быть не может -- гипотезы структурные.
    """

    primary_proposal: InjectionProposal
    baseline_proposal: InjectionProposal
    alternatives: tuple[InjectionProposal, ...] = ()

    def hypotheses(self) -> tuple[InjectionProposal, ...]:
        """Структурные гипотезы пучка в порядке ранга. Baseline сюда не входит."""
        return (self.primary_proposal, *self.alternatives)

    def ranked_hypotheses(self) -> tuple[tuple[int, InjectionProposal], ...]:
        """Пары (ранг, гипотеза), ранг начинается с 1."""
        return tuple(enumerate(self.hypotheses(), start=1))

    def __post_init__(self) -> None:
        _validate_injection_beam(
            primary=self.primary_proposal,
            baseline=self.baseline_proposal,
            alternatives=self.alternatives,
        )
        _validate_agent_output(
            primary=self.primary_proposal,
            baseline=self.baseline_proposal,
            where="injection_agent_output",
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "primary_proposal": self.primary_proposal.to_json(),
            "baseline_proposal": self.baseline_proposal.to_json(),
            "alternatives": [item.to_json() for item in self.alternatives],
        }
