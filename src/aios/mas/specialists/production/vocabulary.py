"""Frozen-vocabulary and production-role scope validation."""

from __future__ import annotations

from dataclasses import dataclass

from aios.mas.common import Direction, Intensity, UncertaintyLevel
from aios.mas.envelopes import ProductionEnvelope
from aios.mas.intents import IntentError, ProductionAction, ProductionIntent
from aios.mas.specialists.authority import contains_prescriptive_text
from aios.state import WellRole

from .errors import VocabularyValidationError
from .parser import ParsedProductionIntent


@dataclass(frozen=True, slots=True)
class VocabularyValidator:
    """Compile parsed strings only through the already-frozen contract enums."""

    def validate(
        self,
        parsed: tuple[ParsedProductionIntent, ...],
        envelope: ProductionEnvelope,
    ) -> tuple[ProductionIntent, ...]:
        allowed = set(envelope.allowed_actions)
        producer_ids = {
            well.well_id for well in envelope.state_card.wells if well.role is WellRole.PRODUCER
        }
        block_membership = envelope.state_card.geology.block_membership
        known_regions = set(block_membership.values())
        intents: list[ProductionIntent] = []
        for index, item in enumerate(parsed):
            try:
                action = ProductionAction(item.action)
                if action not in allowed:
                    raise VocabularyValidationError(
                        f"intents[{index}].action is not allowed by the envelope"
                    )
                unknown_wells = sorted(set(item.target_wells) - producer_ids)
                if unknown_wells:
                    raise VocabularyValidationError(
                        f"intents[{index}] targets unknown or non-producer wells: {tuple(unknown_wells)}"
                    )
                if any(contains_prescriptive_text(region) for region in item.target_regions):
                    raise VocabularyValidationError(
                        f"intents[{index}].target_regions contains a control expression"
                    )
                unknown_regions = sorted(set(item.target_regions) - known_regions)
                if unknown_regions:
                    raise VocabularyValidationError(
                        f"intents[{index}] targets unknown regions: {tuple(unknown_regions)}"
                    )
                if action is ProductionAction.REBALANCE_WITHIN_BLOCK:
                    memberships = {block_membership.get(well) for well in item.target_wells}
                    if (
                        len(item.target_regions) != 1
                        or None in memberships
                        or memberships != set(item.target_regions)
                    ):
                        raise VocabularyValidationError(
                            f"intents[{index}] rebalancing targets must match well block membership"
                        )
                intent = ProductionIntent(
                    action=action,
                    direction=Direction(item.direction),
                    intensity=Intensity(item.intensity),
                    target_wells=item.target_wells,
                    target_regions=item.target_regions,
                    evidence_ids=item.evidence_ids,
                    uncertainty=UncertaintyLevel(item.uncertainty),
                    rationale_short=item.rationale_short,
                )
            except VocabularyValidationError:
                raise
            except (IntentError, ValueError) as exc:
                raise VocabularyValidationError(
                    f"intents[{index}] violates frozen production vocabulary"
                ) from exc
            intents.append(intent)
        return tuple(intents)
