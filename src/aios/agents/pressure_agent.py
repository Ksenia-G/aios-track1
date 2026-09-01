"""Deterministic pressure/voidage proposal agent over StateCard only."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aios.evaluation.canonical import JsonValue, digest
from aios.state import StateCard, WellRole, WellStateSnapshot
from aios.state.schemas import optional_number, required_text

PRESSURE_AGENT_VERSION = "aios.agents.pressure_agent/1.0.0"

PRESSURE_SUPPORT_NEEDED = "PRESSURE_SUPPORT_NEEDED"
INJECTOR_CAPACITY_LIMITED = "INJECTOR_CAPACITY_LIMITED"
PRESSURE_SUPPORT_STABLE = "PRESSURE_SUPPORT_STABLE"
NO_INJECTOR_AVAILABLE = "NO_INJECTOR_AVAILABLE"


class ProposalType(StrEnum):
    """Pressure/Voidage proposal category."""

    HOLD = "HOLD"
    INJECTION_REALLOCATION = "INJECTION_REALLOCATION"
    INJECTION_INCREASE = "INJECTION_INCREASE"
    INJECTION_DECREASE = "INJECTION_DECREASE"


class PressureAgentError(ValueError):
    """StateCard lacks data required for pressure/voidage proposal generation."""


@dataclass(frozen=True, slots=True)
class PressureProposalReason:
    """Structured pressure-agent explanation."""

    pressure_condition: str
    affected_region: str
    expected_effect: str

    def __post_init__(self) -> None:
        required_text(self.pressure_condition, "pressure_proposal.reason.pressure_condition")
        required_text(self.affected_region, "pressure_proposal.reason.affected_region")
        required_text(self.expected_effect, "pressure_proposal.reason.expected_effect")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "pressure_condition": self.pressure_condition,
            "affected_region": self.affected_region,
            "expected_effect": self.expected_effect,
        }


@dataclass(frozen=True, slots=True)
class PressureProposal:
    """One deterministic proposal from the Pressure/Voidage Agent."""

    metadata: dict[str, str]
    proposal_type: ProposalType
    well_ids: tuple[str, ...]
    """Скважины, О КОТОРЫХ этот факт, -- идентификаторы, а не величины.

    Поле называется `well_ids`, а не `target`, потому что имя обязано нести
    семантику само: `target` -- предписывающее имя, и общая граница
    NO-SETPOINTS-CROSS обязана считать величину под ним setpoint'ом. У колоды
    Model Y скважины названы голыми числами ("2", "13", "21"), и лексически
    `{"target": ["13"]}` неотличимо от `{"target": ["240"]}`. Различить их
    может только тип поля -- то есть вот это имя, а не догадка сканера.
    """
    reason: PressureProposalReason
    confidence: float
    risk_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        ProposalType(self.proposal_type)
        confidence = optional_number(self.confidence, "pressure_proposal.confidence")
        assert confidence is not None
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("pressure_proposal.confidence must be in [0.0, 1.0]")
        for key, value in self.metadata.items():
            required_text(key, "pressure_proposal.metadata")
            required_text(value, f"pressure_proposal.metadata.{key}")
        for index, well in enumerate(self.well_ids):
            required_text(well, f"pressure_proposal.well_ids[{index}]")
        for index, flag in enumerate(self.risk_flags):
            required_text(flag, f"pressure_proposal.risk_flags[{index}]")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "metadata": dict(self.metadata),
            "proposal_type": str(self.proposal_type),
            "well_ids": list(self.well_ids),
            "reason": self.reason.to_json(),
            "confidence": self.confidence,
            "risk_flags": list(self.risk_flags),
        }

    def digest(self) -> str:
        """Deterministic SHA-256 over canonical proposal JSON."""

        return digest(self.to_json())


@dataclass(frozen=True, slots=True)
class PressureAgent:
    """Pressure-management proposal agent.

    The agent reads StateCard only. It identifies pressure-support risks and
    injector capacity limitations, then emits a proposal for later legality
    validation and arbitration. It does not validate official constraints.
    """

    producer_bhp_support_threshold_bar: float = 2.0
    injector_capacity_margin_bar: float = 0.5
    agent_version: str = PRESSURE_AGENT_VERSION

    def __post_init__(self) -> None:
        producer_threshold = optional_number(
            self.producer_bhp_support_threshold_bar,
            "pressure_agent.producer_bhp_support_threshold_bar",
        )
        injector_margin = optional_number(
            self.injector_capacity_margin_bar,
            "pressure_agent.injector_capacity_margin_bar",
        )
        assert producer_threshold is not None and injector_margin is not None
        if producer_threshold < 0:
            raise ValueError(
                "pressure_agent.producer_bhp_support_threshold_bar must be non-negative"
            )
        if injector_margin < 0:
            raise ValueError("pressure_agent.injector_capacity_margin_bar must be non-negative")
        required_text(self.agent_version, "pressure_agent.agent_version")

    def propose(self, state_card: StateCard) -> PressureProposal:
        """Create one deterministic pressure/voidage proposal from StateCard."""

        _require_field_pressure_data(state_card)
        producers = tuple(well for well in state_card.wells if well.role is WellRole.PRODUCER)
        injectors = tuple(well for well in state_card.wells if well.role is WellRole.INJECTOR)

        for well in producers:
            _require_producer_data(well)
        for well in injectors:
            _require_injector_data(well)

        weak_producers = tuple(
            well
            for well in producers
            if well.producer_bhp_margin is not None
            and well.producer_bhp_margin < self.producer_bhp_support_threshold_bar
        )
        capped_injectors = tuple(
            well
            for well in injectors
            if well.injector_bhp_margin is not None
            and well.injector_bhp_margin <= self.injector_capacity_margin_bar
        )
        available_injectors = tuple(well for well in injectors if well not in set(capped_injectors))

        if weak_producers and available_injectors:
            return self._proposal(
                state_card,
                proposal_type=ProposalType.INJECTION_INCREASE,
                well_ids=tuple(well.well_id for well in available_injectors),
                reason=PressureProposalReason(
                    pressure_condition=PRESSURE_SUPPORT_NEEDED,
                    affected_region=_producer_region(weak_producers),
                    expected_effect="Increase pressure support around weak producers.",
                ),
                confidence=0.65,
                risk_flags=(PRESSURE_SUPPORT_NEEDED,),
            )
        if weak_producers and capped_injectors:
            return self._proposal(
                state_card,
                proposal_type=ProposalType.INJECTION_REALLOCATION,
                well_ids=tuple(well.well_id for well in capped_injectors),
                reason=PressureProposalReason(
                    pressure_condition=INJECTOR_CAPACITY_LIMITED,
                    affected_region=_producer_region(weak_producers),
                    expected_effect="Reallocate support away from pressure-limited injectors.",
                ),
                confidence=0.45,
                risk_flags=(PRESSURE_SUPPORT_NEEDED, INJECTOR_CAPACITY_LIMITED),
            )
        if weak_producers:
            return self._proposal(
                state_card,
                proposal_type=ProposalType.HOLD,
                well_ids=(),
                reason=PressureProposalReason(
                    pressure_condition=PRESSURE_SUPPORT_NEEDED,
                    affected_region=_producer_region(weak_producers),
                    expected_effect="No injector-side target is available from the StateCard.",
                ),
                confidence=0.3,
                risk_flags=(PRESSURE_SUPPORT_NEEDED, NO_INJECTOR_AVAILABLE),
            )
        if capped_injectors:
            return self._proposal(
                state_card,
                proposal_type=ProposalType.HOLD,
                well_ids=tuple(well.well_id for well in capped_injectors),
                reason=PressureProposalReason(
                    pressure_condition=INJECTOR_CAPACITY_LIMITED,
                    affected_region=_injector_region(capped_injectors),
                    expected_effect="Avoid injection increase on pressure-limited injectors.",
                ),
                confidence=0.8,
                risk_flags=(INJECTOR_CAPACITY_LIMITED,),
            )
        return self._proposal(
            state_card,
            proposal_type=ProposalType.HOLD,
            well_ids=(),
            reason=PressureProposalReason(
                pressure_condition=PRESSURE_SUPPORT_STABLE,
                affected_region="field",
                expected_effect="Maintain current injection-side controls.",
            ),
            confidence=0.7,
            risk_flags=(),
        )

    def _proposal(
        self,
        state_card: StateCard,
        *,
        proposal_type: ProposalType,
        well_ids: tuple[str, ...],
        reason: PressureProposalReason,
        confidence: float,
        risk_flags: tuple[str, ...],
    ) -> PressureProposal:
        return PressureProposal(
            metadata={
                "state_card_digest": state_card.digest(),
                "agent_version": self.agent_version,
            },
            proposal_type=proposal_type,
            well_ids=well_ids,
            reason=reason,
            confidence=confidence,
            risk_flags=risk_flags,
        )


def _require_field_pressure_data(state_card: StateCard) -> None:
    required = {
        "FPR": state_card.field.average_pressure,
        "FVPR": state_card.field.voidage_production_rate,
        "FVPT": state_card.field.voidage_production_total,
        "FWIR": state_card.field.water_injection_rate,
    }
    for vector, value in required.items():
        if value is None:
            raise PressureAgentError(f"field: missing {vector}")


def _require_producer_data(well: WellStateSnapshot) -> None:
    if well.bhp is None:
        raise PressureAgentError(f"{well.well_id}: missing WBHP")
    if well.producer_bhp_margin is None:
        raise PressureAgentError(f"{well.well_id}: missing producer BHP margin")


def _require_injector_data(well: WellStateSnapshot) -> None:
    if well.bhp is None:
        raise PressureAgentError(f"{well.well_id}: missing WBHP")
    if well.wwir is None:
        raise PressureAgentError(f"{well.well_id}: missing WWIR")
    if well.injector_bhp_margin is None:
        raise PressureAgentError(f"{well.well_id}: missing injector BHP margin")


def _producer_region(wells: tuple[WellStateSnapshot, ...]) -> str:
    return "producers:" + ",".join(well.well_id for well in wells)


def _injector_region(wells: tuple[WellStateSnapshot, ...]) -> str:
    return "injectors:" + ",".join(well.well_id for well in wells)
