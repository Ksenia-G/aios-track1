"""Deterministic anti-anchoring prompt for the Production specialist."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from aios.mas.envelopes import ProductionEnvelope

from .evidence import EvidenceBuilder

PROMPT_BUILDER_VERSION = "aios.mas.specialists.production.prompt/1.0.0"


@dataclass(frozen=True, slots=True)
class PromptBuilder:
    """Build from the Envelope only, matching SpecialistPromptBuilder protocol."""

    evidence_builder: EvidenceBuilder = field(default_factory=EvidenceBuilder)
    version: str = PROMPT_BUILDER_VERSION

    def build(self, envelope: ProductionEnvelope) -> str:
        evidence = self.evidence_builder.build(envelope)
        context = {
            "prompt_builder_version": self.version,
            "schema_version": envelope.schema_version,
            "variant_id": envelope.variant_id,
            "config_digest": envelope.config_digest,
            "envelope_digest": envelope.digest(),
            "round": str(envelope.round),
            "state_card_schema_version": envelope.state_card.schema_version,
            "state_card_digest": envelope.state_card.digest(),
            "state_metadata": envelope.state_card.metadata.to_json(),
            "horizon_context": envelope.horizon_context.to_json(),
            "memory_slice": envelope.memory_slice.to_json(),
            "allowed_actions": sorted(str(action) for action in envelope.allowed_actions),
            "evidence": evidence.to_json(),
            "peer_proposals": sorted(
                (reference.to_json() for reference in envelope.peer_proposals),
                key=lambda item: (str(item["role"]), str(item["proposal_digest"])),
            ),
        }
        schema = {
            "intents": [
                {
                    "action": "<allowed ProductionAction>",
                    "direction": "<UP|DOWN|NONE>",
                    "intensity": "<NONE|SMALL|MODERATE|AGGRESSIVE>",
                    "target_wells": ["<producer well id>"],
                    "target_regions": ["<region id>"],
                    "evidence_ids": ["<existing evidence id>"],
                    "uncertainty": "<LOW|MEDIUM|HIGH|UNKNOWN>",
                    "rationale_short": "<at most two short sentences>",
                }
            ]
        }
        action_contracts = {
            "HOLD": "direction NONE; intensity NONE; no targets; empty rationale_short",
            "INCREASE_OFFTAKE": "direction UP; qualitative intensity; producer wells required",
            "REDUCE_OFFTAKE": "direction DOWN; qualitative intensity; producer wells required",
            "SHUT_IN": "direction NONE; intensity NONE; producer wells required",
            "REOPEN": "direction NONE; intensity NONE; producer wells required",
            "REBALANCE_WITHIN_BLOCK": (
                "direction NONE; qualitative intensity; producer wells and their known block required"
            ),
            "ALL_NON_HOLD": "cite at least one existing evidence_id; no numerical output",
        }
        return "\n".join(
            (
                "You are the Production specialist in Hybrid MAS v1.1.",
                "Propose only structural production hypotheses for simulation.",
                "Never emit numerical magnitudes, setpoints, rates, percentages, or controls.",
                "Use only allowed actions and cite only evidence_ids present in the context.",
                "Return one JSON object only, with exactly this schema:",
                json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "Frozen action contracts:",
                json.dumps(
                    action_contracts,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "CONTEXT:",
                json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        )

    def build_schema_retry(self, envelope: ProductionEnvelope) -> str:
        return (
            self.build(envelope)
            + "\nPrevious response failed strict schema validation. Retry JSON only."
        )
