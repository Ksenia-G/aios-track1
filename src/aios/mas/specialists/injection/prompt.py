"""Промпт Injection Controller. Только envelope, без baseline.

Сигнатура build(envelope) -- часть T1: baseline вторым аргументом передать
нельзя. Численные setpoints в промпт как команды не попадают.
"""

from __future__ import annotations

from aios.evaluation.canonical import canonical_json
from aios.mas.common import Direction, Intensity, UncertaintyLevel
from aios.mas.envelopes import InjectionEnvelope
from aios.mas.intents import InjectionAction
from aios.mas.invariants import require_no_baseline_field
from aios.mas.specialists.injection.evidence_builder import EvidenceBuilder

PROMPT_BUILDER_VERSION = "aios.mas.specialists.injection.prompt/1.0.0"


class PromptBuildError(ValueError):
    """Промпт из envelope собрать нельзя."""


class PromptBuilder:
    """SpecialistPromptBuilder[InjectionEnvelope]: один аргумент, без baseline."""

    version = PROMPT_BUILDER_VERSION

    def build(self, envelope: InjectionEnvelope) -> str:
        if not isinstance(envelope, InjectionEnvelope):
            raise PromptBuildError(f"expected InjectionEnvelope, got {type(envelope).__name__}")
        require_no_baseline_field(envelope)
        payload = {
            "prompt_builder_version": self.version,
            "role": "INJECTION",
            "task": (
                "Return a JSON object with key intents. Use only allowed_actions. "
                "Do not emit numerical setpoints."
            ),
            "schema_version": envelope.schema_version,
            "round": str(envelope.round),
            "variant_id": envelope.variant_id,
            "horizon": envelope.horizon_context.to_json(),
            "allowed_actions": sorted(str(action) for action in envelope.allowed_actions),
            "vocabulary": {
                "action": sorted(member.value for member in InjectionAction),
                "direction": sorted(member.value for member in Direction),
                "intensity": sorted(member.value for member in Intensity),
                "uncertainty": sorted(member.value for member in UncertaintyLevel),
            },
            "wells": [
                {
                    "well_id": well.well_id,
                    "role": str(well.role),
                    "status": well.status,
                }
                for well in envelope.state_card.wells
            ],
            "evidence": [
                {
                    "evidence_id": artifact.evidence_id,
                    "kind": artifact.kind,
                    "source_version": artifact.source_version,
                }
                for artifact in EvidenceBuilder().build(envelope).artifacts
            ],
            "peer_proposals": [ref.to_json() for ref in envelope.peer_proposals],
            "output_schema": {
                "intents": [
                    {
                        "action": "InjectionAction",
                        "direction": "Direction",
                        "intensity": "Intensity",
                        "donor_wells": ["well_id"],
                        "recipient_wells": ["well_id"],
                        "source_regions": ["region_id"],
                        "target_regions": ["region_id"],
                        "evidence_ids": ["evidence_id"],
                        "uncertainty": "UncertaintyLevel",
                        "rationale_short": "string",
                    }
                ]
            },
        }
        return canonical_json(payload)
