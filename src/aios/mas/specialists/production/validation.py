"""Fail-closed validation of the official ProductionEnvelope input."""

from __future__ import annotations

from dataclasses import dataclass

from aios.mas.common import MAS_CONTRACT_SCHEMA_VERSION, AgentRole, DecisionRound
from aios.mas.envelopes import ProductionEnvelope
from aios.mas.evidence import EvidenceArtifact, HorizonContext, MemorySlice, ProposalReference
from aios.mas.intents import ProductionAction
from aios.state import (
    ConstraintState,
    FieldStateSnapshot,
    GeologyContext,
    StateCard,
    StateMetadata,
    UncertaintyState,
    WellRole,
    WellStateSnapshot,
)

from .errors import InputValidationError


@dataclass(frozen=True, slots=True)
class InputValidator:
    """Validate role scope and round semantics beyond frozen dataclass checks."""

    def validate(self, envelope: ProductionEnvelope) -> None:
        if not isinstance(envelope, ProductionEnvelope):
            raise InputValidationError("expected ProductionEnvelope")
        if envelope.schema_version != MAS_CONTRACT_SCHEMA_VERSION:
            raise InputValidationError(f"unsupported schema_version {envelope.schema_version!r}")
        if not isinstance(envelope.round, DecisionRound):
            raise InputValidationError("round must be a DecisionRound value")
        if not isinstance(envelope.state_card, StateCard):
            raise InputValidationError("state_card must be a StateCard")
        card = envelope.state_card
        if not isinstance(card.metadata, StateMetadata):
            raise InputValidationError("state_card.metadata must be StateMetadata")
        if not isinstance(card.field, FieldStateSnapshot):
            raise InputValidationError("state_card.field must be FieldStateSnapshot")
        if not isinstance(card.constraints, ConstraintState):
            raise InputValidationError("state_card.constraints must be ConstraintState")
        if not isinstance(card.geology, GeologyContext):
            raise InputValidationError("state_card.geology must be GeologyContext")
        if not isinstance(card.uncertainty, UncertaintyState):
            raise InputValidationError("state_card.uncertainty must be UncertaintyState")
        if not isinstance(envelope.horizon_context, HorizonContext):
            raise InputValidationError("horizon_context must be a HorizonContext")
        if not isinstance(envelope.memory_slice, MemorySlice):
            raise InputValidationError("memory_slice must be a MemorySlice")
        if not isinstance(envelope.memory_slice.records, tuple) or any(
            not isinstance(record, EvidenceArtifact) for record in envelope.memory_slice.records
        ):
            raise InputValidationError("memory_slice.records must contain EvidenceArtifact values")
        if not isinstance(card.wells, tuple) or any(
            not isinstance(well, WellStateSnapshot) or not isinstance(well.role, WellRole)
            for well in card.wells
        ):
            raise InputValidationError(
                "state_card.wells must contain WellStateSnapshot values with exact WellRole"
            )
        for field_name in (
            "pressure_diagnostic",
            "sweep_diagnostic",
            "geology_card",
            "response_map",
            "compensation_state",
            "economic_context",
        ):
            artifact = getattr(envelope, field_name)
            if artifact is not None and not isinstance(artifact, EvidenceArtifact):
                raise InputValidationError(f"{field_name} must be an EvidenceArtifact or null")
        if not isinstance(envelope.allowed_actions, tuple):
            raise InputValidationError("allowed_actions must be a tuple")
        if not envelope.allowed_actions:
            raise InputValidationError("allowed_actions must not be empty")
        if any(not isinstance(action, ProductionAction) for action in envelope.allowed_actions):
            raise InputValidationError("allowed_actions must contain ProductionAction values")
        if len(set(envelope.allowed_actions)) != len(envelope.allowed_actions):
            raise InputValidationError("allowed_actions must not contain duplicates")
        if ProductionAction.HOLD not in envelope.allowed_actions:
            raise InputValidationError("allowed_actions must include HOLD for safe fallback")

        if not isinstance(envelope.peer_proposals, tuple) or any(
            not isinstance(reference, ProposalReference) for reference in envelope.peer_proposals
        ):
            raise InputValidationError("peer_proposals must contain ProposalReference values")
        if envelope.round is DecisionRound.ROUND_1 and envelope.peer_proposals:
            raise InputValidationError("ROUND_1 must be isolated from peer proposals")
        if envelope.round is DecisionRound.ROUND_2:
            if not envelope.peer_proposals:
                raise InputValidationError("ROUND_2 requires peer proposal context")
            if any(
                not isinstance(ref.role, AgentRole) or ref.role is not AgentRole.INJECTION
                for ref in envelope.peer_proposals
            ):
                raise InputValidationError(
                    "ROUND_2 Production context may reference only INJECTION proposals"
                )
            digests = tuple(ref.proposal_digest for ref in envelope.peer_proposals)
            if len(digests) != len(set(digests)):
                raise InputValidationError("ROUND_2 peer proposal references must be unique")
