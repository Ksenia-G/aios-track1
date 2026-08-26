"""Детерминированный baseline Injection Controller.

Baseline -- отдельный объект, не primary и не LLM-fallback. Он всегда HOLD:
агент не копирует чужое решение и не ставит численные режимы. Промпт его
не видит -- это собирается здесь, до PromptBuilder.
"""

from __future__ import annotations

from aios.mas.common import (
    MAS_CONTRACT_SCHEMA_VERSION,
    Direction,
    Intensity,
    ProposalSource,
    UncertaintyLevel,
)
from aios.mas.envelopes import InjectionEnvelope
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.mas.proposals import GroundingReport, InjectionProposal, ProposalProvenance


class BaselinePolicyError(ValueError):
    """Baseline нельзя построить из этого envelope."""


class BaselinePolicy:
    """Собрать DETERMINISTIC_BASELINE proposal: один HOLD, без setpoints."""

    def propose(self, envelope: InjectionEnvelope) -> InjectionProposal:
        if InjectionAction.HOLD not in envelope.allowed_actions:
            raise BaselinePolicyError("allowed_actions must include HOLD")
        return InjectionProposal(
            schema_version=MAS_CONTRACT_SCHEMA_VERSION,
            envelope_digest=envelope.digest(),
            source=ProposalSource.DETERMINISTIC_BASELINE,
            intents=(_hold_intent(),),
            grounding_report=GroundingReport(),
            provenance=ProposalProvenance(),
        )


def _hold_intent() -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.HOLD,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        donor_wells=(),
        recipient_wells=(),
        source_regions=(),
        target_regions=(),
        evidence_ids=(),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="",
    )
