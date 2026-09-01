"""Проверка, что intents ссылаются только на существующий evidence.

GroundingAuditor не считает diagnostic и не ставит setpoints: он сверяет
evidence_ids с каталогом EvidenceContext. Неизвестный id отклоняет весь
набор intents. HOLD может не ссылаться на evidence.
"""

from __future__ import annotations

from collections.abc import Sequence

from aios.mas.intents import InjectionIntent
from aios.mas.proposals import GroundingReport
from aios.mas.specialists.injection.evidence_builder import EvidenceContext


class GroundingError(ValueError):
    """Intents ссылаются на evidence, которого нет в каталоге."""

    def __init__(self, message: str, report: GroundingReport | None = None) -> None:
        super().__init__(message)
        self.report = report


class GroundingAuditor:
    """Собрать GroundingReport. Неизвестный id -- отказ всего набора."""

    def audit(
        self,
        intents: Sequence[InjectionIntent],
        evidence: EvidenceContext,
    ) -> GroundingReport:
        if not intents:
            raise GroundingError("intents must not be empty")

        grounded: list[str] = []
        unknown: list[str] = []
        seen: set[str] = set()
        for intent in intents:
            for evidence_id in intent.evidence_ids:
                if evidence_id in seen:
                    continue
                seen.add(evidence_id)
                if evidence.contains(evidence_id):
                    grounded.append(evidence_id)
                else:
                    unknown.append(evidence_id)

        report = GroundingReport(
            grounded_evidence_ids=tuple(grounded),
            unknown_evidence_ids=tuple(unknown),
        )
        if not report.passed:
            raise GroundingError(
                f"unknown evidence_ids={report.unknown_evidence_ids}",
                report=report,
            )
        return report
