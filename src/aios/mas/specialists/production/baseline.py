"""Independent deterministic engineering floor for Production Controller."""

from __future__ import annotations

from dataclasses import dataclass

from aios.mas.common import Direction, Intensity, ProposalSource, UncertaintyLevel
from aios.mas.envelopes import ProductionEnvelope
from aios.mas.intents import ProductionAction, ProductionIntent
from aios.mas.proposals import (
    GroundingReport,
    ProductionProposal,
    ProposalProvenance,
)

BASELINE_POLICY_VERSION = "aios.mas.specialists.production.baseline/1.0.0"


@dataclass(frozen=True, slots=True)
class BaselinePolicy:
    """A conservative deterministic baseline with no guessed magnitudes."""

    version: str = BASELINE_POLICY_VERSION

    def build(self, envelope: ProductionEnvelope) -> ProductionProposal:
        hold = ProductionIntent(
            action=ProductionAction.HOLD,
            direction=Direction.NONE,
            intensity=Intensity.NONE,
            target_wells=(),
            target_regions=(),
            evidence_ids=(),
            uncertainty=UncertaintyLevel.LOW,
            rationale_short="",
        )
        return ProductionProposal(
            schema_version=envelope.schema_version,
            envelope_digest=envelope.digest(),
            source=ProposalSource.DETERMINISTIC_BASELINE,
            intents=(hold,),
            grounding_report=GroundingReport(),
            provenance=ProposalProvenance(),
        )
