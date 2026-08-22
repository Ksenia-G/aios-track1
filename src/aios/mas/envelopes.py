"""Role-specific envelopes: ProductionEnvelope, InjectionEnvelope, ArbiterEnvelope.

Намеренно НЕТ одного общего "god AgentEnvelope": Production и Injection
специалисты видят разный набор diagnostic evidence и разные allowed_actions,
и смешивание их в одном типе создало бы риск случайной утечки чужого поля.

КРИТИЧЕСКАЯ ГРАНИЦА: ни ProductionEnvelope, ни InjectionEnvelope не несут
поля baseline/baseline_proposal. PromptBuilder следующей задачи получает
именно Envelope, поэтому baseline структурно не может попасть в specialist
prompt через официальный контракт -- это защита от anchoring на
детерминированный baseline при выработке primary-предложения.

Arbiter -- второй эшелон: ArbiterEnvelope собирает уже готовые
ProductionAgentOutput/InjectionAgentOutput, а не запускается параллельно со
специалистами.
"""

from __future__ import annotations

from dataclasses import dataclass

from aios.evaluation.canonical import JsonValue, digest
from aios.mas.common import DecisionRound
from aios.mas.evidence import (
    EvidenceArtifact,
    HorizonContext,
    MemorySlice,
    ProposalReference,
    SimulationBudget,
)
from aios.mas.intents import InjectionAction, ProductionAction
from aios.mas.proposals import InjectionAgentOutput, ProductionAgentOutput
from aios.state import StateCard


class EnvelopeError(ValueError):
    """Envelope contract нарушен."""


def _require_text(value: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EnvelopeError(f"{where}: expected non-empty string")
    return value


def _validate_round_peers(
    round_: DecisionRound, peer_proposals: tuple[ProposalReference, ...], where: str
) -> None:
    DecisionRound(round_)
    if round_ is DecisionRound.ROUND_1 and peer_proposals:
        raise EnvelopeError(f"{where}: ROUND_1 must not carry peer_proposals")
    if round_ is DecisionRound.ROUND_2 and not peer_proposals:
        raise EnvelopeError(f"{where}: ROUND_2 requires non-empty peer_proposals")


def _evidence_digest(artifact: EvidenceArtifact | None) -> str | None:
    return artifact.digest() if artifact is not None else None


def _canonical_peer_proposals(peer_proposals: tuple[ProposalReference, ...]) -> list[JsonValue]:
    return sorted(
        (ref.to_json() for ref in peer_proposals),
        key=lambda item: (str(item["role"]), str(item["proposal_digest"])),
    )


@dataclass(frozen=True, slots=True)
class ProductionEnvelope:
    """Всё, что видит Production-специалист на одном decision step."""

    schema_version: str
    round: DecisionRound
    variant_id: str
    config_digest: str

    state_card: StateCard
    horizon_context: HorizonContext
    memory_slice: MemorySlice

    allowed_actions: tuple[ProductionAction, ...]

    pressure_diagnostic: EvidenceArtifact | None = None
    sweep_diagnostic: EvidenceArtifact | None = None
    geology_card: EvidenceArtifact | None = None
    response_map: EvidenceArtifact | None = None
    compensation_state: EvidenceArtifact | None = None
    economic_context: EvidenceArtifact | None = None

    peer_proposals: tuple[ProposalReference, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.schema_version, "production_envelope.schema_version")
        _require_text(self.variant_id, "production_envelope.variant_id")
        _require_text(self.config_digest, "production_envelope.config_digest")
        _validate_round_peers(self.round, self.peer_proposals, where="production_envelope")

    def _semantic_payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "round": str(self.round),
            "variant_id": self.variant_id,
            "config_digest": self.config_digest,
            "state_card_digest": self.state_card.digest(),
            "horizon_context": self.horizon_context.to_json(),
            "memory_slice": self.memory_slice.to_json(),
            "allowed_actions": sorted(str(action) for action in self.allowed_actions),
            "pressure_diagnostic": _evidence_digest(self.pressure_diagnostic),
            "sweep_diagnostic": _evidence_digest(self.sweep_diagnostic),
            "geology_card": _evidence_digest(self.geology_card),
            "response_map": _evidence_digest(self.response_map),
            "compensation_state": _evidence_digest(self.compensation_state),
            "economic_context": _evidence_digest(self.economic_context),
            "peer_proposals": _canonical_peer_proposals(self.peer_proposals),
        }

    def digest(self) -> str:
        return digest(self._semantic_payload())


@dataclass(frozen=True, slots=True)
class InjectionEnvelope:
    """Всё, что видит Injection-специалист на одном decision step.

    Без economic_context: экономический контекст в v1.1 передаётся только
    Production-специалисту, а Injection работает от pressure/sweep/geology
    evidence.
    """

    schema_version: str
    round: DecisionRound
    variant_id: str
    config_digest: str

    state_card: StateCard
    horizon_context: HorizonContext
    memory_slice: MemorySlice

    allowed_actions: tuple[InjectionAction, ...]

    pressure_diagnostic: EvidenceArtifact | None = None
    sweep_diagnostic: EvidenceArtifact | None = None
    geology_card: EvidenceArtifact | None = None
    response_map: EvidenceArtifact | None = None
    compensation_state: EvidenceArtifact | None = None

    peer_proposals: tuple[ProposalReference, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.schema_version, "injection_envelope.schema_version")
        _require_text(self.variant_id, "injection_envelope.variant_id")
        _require_text(self.config_digest, "injection_envelope.config_digest")
        _validate_round_peers(self.round, self.peer_proposals, where="injection_envelope")

    def _semantic_payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "round": str(self.round),
            "variant_id": self.variant_id,
            "config_digest": self.config_digest,
            "state_card_digest": self.state_card.digest(),
            "horizon_context": self.horizon_context.to_json(),
            "memory_slice": self.memory_slice.to_json(),
            "allowed_actions": sorted(str(action) for action in self.allowed_actions),
            "pressure_diagnostic": _evidence_digest(self.pressure_diagnostic),
            "sweep_diagnostic": _evidence_digest(self.sweep_diagnostic),
            "geology_card": _evidence_digest(self.geology_card),
            "response_map": _evidence_digest(self.response_map),
            "compensation_state": _evidence_digest(self.compensation_state),
            "peer_proposals": _canonical_peer_proposals(self.peer_proposals),
        }

    def digest(self) -> str:
        return digest(self._semantic_payload())


@dataclass(frozen=True, slots=True)
class ArbiterEnvelope:
    """Вход Arbiter: уже готовые specialist outputs, второй эшелон.

    Никаких numerical candidate setpoints и никаких existing Schedule control
    объектов -- Arbiter работает со structural proposals/bundles. Полный
    StateCard сюда не кладётся повторно: достаточно state_card_digest.
    """

    schema_version: str
    variant_id: str
    config_digest: str
    state_card_digest: str

    production_output: ProductionAgentOutput
    injection_output: InjectionAgentOutput

    horizon_context: HorizonContext
    memory_slice: MemorySlice
    simulation_budget: SimulationBudget

    def __post_init__(self) -> None:
        _require_text(self.schema_version, "arbiter_envelope.schema_version")
        _require_text(self.variant_id, "arbiter_envelope.variant_id")
        _require_text(self.config_digest, "arbiter_envelope.config_digest")
        _require_text(self.state_card_digest, "arbiter_envelope.state_card_digest")

    def _semantic_payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "variant_id": self.variant_id,
            "config_digest": self.config_digest,
            "state_card_digest": self.state_card_digest,
            "production_output": {
                "primary": self.production_output.primary_proposal.digest(),
                "baseline": self.production_output.baseline_proposal.digest(),
            },
            "injection_output": {
                "primary": self.injection_output.primary_proposal.digest(),
                "baseline": self.injection_output.baseline_proposal.digest(),
            },
            "horizon_context": self.horizon_context.to_json(),
            "memory_slice": self.memory_slice.to_json(),
            "simulation_budget": self.simulation_budget.to_json(),
        }

    def digest(self) -> str:
        return digest(self._semantic_payload())
