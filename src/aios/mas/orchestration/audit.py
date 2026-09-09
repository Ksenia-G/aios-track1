"""Наблюдательный след одной эпохи решения.

Здесь не принимается ни одного решения и не выводится ни одного нового факта:
модуль только СОХРАНЯЕТ то, что на шаге уже существовало. PERF-004 прошёл по
правильному конвейеру, но итоговый артефакт не удерживал ни даты StateCard, ни
идентичности диагностик, ни того, что специалисты на самом деле вернули --
причинность приходилось доказывать поведением прогона, а не читать из файла.

Граница та же, что и везде: численного управления в этих записях нет.
Структурный intent -- это ещё не setpoint, а `to_json()` предложения и так
живёт до MagnitudeResolver. NO SETPOINTS CROSS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from aios.evaluation.canonical import JsonValue


@dataclass(frozen=True, slots=True)
class EvidenceAudit:
    """Идентичность одного слота evidence. Отсутствие -- это тоже факт.

    Пустой слот записывается как пустой, а не достраивается: диагностика,
    которой на шаге не было, не имеет права выглядеть в отчёте так, будто была.
    """

    evidence_id: str | None = None
    artifact_digest: str | None = None

    @classmethod
    def of(cls, artifact: object | None) -> EvidenceAudit:
        if artifact is None:
            return cls()
        return cls(
            evidence_id=getattr(artifact, "evidence_id", None),
            artifact_digest=artifact.digest(),  # type: ignore[attr-defined]
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {"evidence_id": self.evidence_id, "artifact_digest": self.artifact_digest}


@dataclass(frozen=True, slots=True)
class SpecialistAudit:
    """Что специалист вернул на этом шаге -- весь пучок, а не только победитель.

    Хранить одну победившую гипотезу значило бы сделать пучок непроверяемым:
    ревьюер не смог бы отличить «модель предложила одно» от «модель предложила
    пять, четыре отсеялись».
    """

    proposal_source: str
    fallback_used: bool
    fallback_reason: str | None = None
    primary: dict[str, JsonValue] | None = None
    baseline: dict[str, JsonValue] | None = None
    alternatives: tuple[dict[str, JsonValue], ...] = ()
    considered_alternatives: tuple[dict[str, JsonValue], ...] = ()
    cited_evidence_ids: tuple[str, ...] = ()

    @classmethod
    def of(cls, output: object) -> SpecialistAudit:
        """Снимок вывода специалиста ровно в том виде, в каком он уже есть."""
        primary = output.primary_proposal  # type: ignore[attr-defined]
        baseline = getattr(output, "baseline_proposal", None)
        alternatives = tuple(getattr(output, "alternatives", ()) or ())
        # Рассмотренная, но не рекомендованная альтернатива обязана быть видна:
        # именно она делает читаемым случай "модель не предпочла направление,
        # покрытие его проверило, экономика показала обратное".
        considered = tuple(getattr(output, "considered_alternatives", ()) or ())
        return cls(
            proposal_source=str(primary.source),
            fallback_used=primary.provenance.fallback_used,
            fallback_reason=primary.provenance.fallback_reason,
            primary=primary.to_json(),
            baseline=None if baseline is None else baseline.to_json(),
            alternatives=tuple(item.to_json() for item in alternatives),
            considered_alternatives=tuple(item.to_json() for item in considered),
            cited_evidence_ids=tuple(sorted(primary.grounding_report.grounded_evidence_ids)),
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "proposal_source": self.proposal_source,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "primary": self.primary,
            "baseline": self.baseline,
            "alternatives": list(self.alternatives),
            "considered_alternatives": list(self.considered_alternatives),
            "cited_evidence_ids": list(self.cited_evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class DecisionEpochAudit:
    """Полный наблюдательный след эпохи.

    Даты эпохи здесь нет намеренно: оркестратор знает, ПО КАКОМУ состоянию он
    рассуждал, а какой это по счёту шаг многошагового прогона -- знает только
    бенчмарк. Свести их в одну строку -- задача `epoch_audits`, и именно там
    видно, совпадает ли дата решения с датой состояния.
    """

    state_card_date: date
    state_card_digest: str
    pressure: EvidenceAudit = field(default_factory=EvidenceAudit)
    sweep: EvidenceAudit = field(default_factory=EvidenceAudit)
    support_connectivity: EvidenceAudit = field(default_factory=EvidenceAudit)
    regional_support_imbalance: EvidenceAudit = field(default_factory=EvidenceAudit)
    production: SpecialistAudit | None = None
    injection: SpecialistAudit | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "state_card_date": self.state_card_date.isoformat(),
            "state_card_digest": self.state_card_digest,
            "diagnostics": {
                "pressure": self.pressure.to_json(),
                "sweep": self.sweep.to_json(),
                "support_connectivity": self.support_connectivity.to_json(),
                "regional_support_imbalance": self.regional_support_imbalance.to_json(),
            },
            "production": None if self.production is None else self.production.to_json(),
            "injection": None if self.injection is None else self.injection.to_json(),
        }


__all__ = ["DecisionEpochAudit", "EvidenceAudit", "SpecialistAudit"]
