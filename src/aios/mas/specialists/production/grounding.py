"""Deterministic evidence grounding audit."""

from __future__ import annotations

from dataclasses import dataclass

from aios.mas.intents import ProductionIntent
from aios.mas.proposals import GroundingReport

from .evidence import ProductionEvidence


@dataclass(frozen=True, slots=True)
class GroundingAuditor:
    """Report every unknown evidence identifier without filtering intents."""

    def audit(
        self,
        intents: tuple[ProductionIntent, ...],
        evidence: ProductionEvidence,
    ) -> GroundingReport:
        cited = {evidence_id for intent in intents for evidence_id in intent.evidence_ids}
        known = evidence.evidence_ids
        return GroundingReport(
            grounded_evidence_ids=tuple(sorted(cited & known)),
            unknown_evidence_ids=tuple(sorted(cited - known)),
        )
