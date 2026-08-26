"""All-or-nothing ProductionProposal assembly."""

from __future__ import annotations

from dataclasses import dataclass

from aios.mas.common import ProposalSource
from aios.mas.envelopes import ProductionEnvelope
from aios.mas.intents import ProductionIntent
from aios.mas.proposals import (
    GroundingReport,
    ProductionProposal,
    ProposalAudit,
    ProposalProvenance,
)

from .errors import ProposalCompilationError


@dataclass(frozen=True, slots=True)
class ProposalCompiler:
    """Compile the complete response or reject it; never remove one bad intent."""

    def compile(
        self,
        *,
        envelope: ProductionEnvelope,
        source: ProposalSource,
        intents: tuple[ProductionIntent, ...],
        grounding_report: GroundingReport,
        provenance: ProposalProvenance,
        audit: ProposalAudit | None = None,
    ) -> ProductionProposal:
        if not grounding_report.passed:
            raise ProposalCompilationError(
                f"unknown evidence_ids: {grounding_report.unknown_evidence_ids}"
            )
        return ProductionProposal(
            schema_version=envelope.schema_version,
            envelope_digest=envelope.digest(),
            source=source,
            intents=intents,
            grounding_report=grounding_report,
            provenance=provenance,
            audit=audit or ProposalAudit(),
        )

    def deterministic_fallback(
        self,
        *,
        envelope: ProductionEnvelope,
        baseline: ProductionProposal,
        reason: str,
        prompt_builder_version: str | None = None,
        prompt_digest: str | None = None,
        model_id: str | None = None,
        response_digest: str | None = None,
    ) -> ProductionProposal:
        return self.compile(
            envelope=envelope,
            source=ProposalSource.DETERMINISTIC_FALLBACK,
            intents=baseline.intents,
            grounding_report=baseline.grounding_report,
            provenance=ProposalProvenance(
                prompt_builder_version=prompt_builder_version,
                prompt_digest=prompt_digest,
                model_id=model_id,
                response_digest=response_digest,
                fallback_used=True,
                fallback_reason=reason,
            ),
        )
