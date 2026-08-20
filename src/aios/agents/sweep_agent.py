"""Deterministic sweep/water-cut proposal agent over StateCard only."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aios.evaluation.canonical import JsonValue, digest
from aios.state import StateCard, WellRole, WellStateSnapshot
from aios.state.schemas import optional_number, required_text

SWEEP_AGENT_VERSION = "aios.agents.sweep_agent/1.0.0"

WATER_CUT_STABLE = "WATER_CUT_STABLE"
HIGH_WATER_CUT = "HIGH_WATER_CUT"
LOW_OIL_EFFICIENCY = "LOW_OIL_EFFICIENCY"
OIL_EFFICIENCY_ACCEPTABLE = "OIL_EFFICIENCY_ACCEPTABLE"
NEAR_WLPR_LIMIT = "NEAR_WLPR_LIMIT"
NEAR_WLPR_LIMIT_WITH_HIGH_WATER_CUT = "NEAR_WLPR_LIMIT_WITH_HIGH_WATER_CUT"


class ProposalType(StrEnum):
    """Sweep/Water-Cut proposal category."""

    HOLD = "HOLD"
    REDUCE_WATER_RISK = "REDUCE_WATER_RISK"
    PRODUCER_PRIORITY_CHANGE = "PRODUCER_PRIORITY_CHANGE"
    WATER_SWEEP_WARNING = "WATER_SWEEP_WARNING"


class SweepAgentError(ValueError):
    """StateCard lacks data required for sweep proposal generation."""


@dataclass(frozen=True, slots=True)
class SweepProposalReason:
    """Structured sweep-agent explanation."""

    water_condition: str
    oil_efficiency_condition: str
    expected_effect: str

    def __post_init__(self) -> None:
        required_text(self.water_condition, "sweep_proposal.reason.water_condition")
        required_text(
            self.oil_efficiency_condition,
            "sweep_proposal.reason.oil_efficiency_condition",
        )
        required_text(self.expected_effect, "sweep_proposal.reason.expected_effect")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "water_condition": self.water_condition,
            "oil_efficiency_condition": self.oil_efficiency_condition,
            "expected_effect": self.expected_effect,
        }


@dataclass(frozen=True, slots=True)
class SweepProposal:
    """One deterministic proposal from the Sweep/Water-Cut Agent."""

    metadata: dict[str, str]
    proposal_type: ProposalType
    target: tuple[str, ...]
    reason: SweepProposalReason
    confidence: float
    risk_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        ProposalType(self.proposal_type)
        confidence = optional_number(self.confidence, "sweep_proposal.confidence")
        assert confidence is not None
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("sweep_proposal.confidence must be in [0.0, 1.0]")
        for key, value in self.metadata.items():
            required_text(key, "sweep_proposal.metadata")
            required_text(value, f"sweep_proposal.metadata.{key}")
        for index, well in enumerate(self.target):
            required_text(well, f"sweep_proposal.target[{index}]")
        for index, flag in enumerate(self.risk_flags):
            required_text(flag, f"sweep_proposal.risk_flags[{index}]")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "metadata": dict(self.metadata),
            "proposal_type": str(self.proposal_type),
            "target": list(self.target),
            "reason": self.reason.to_json(),
            "confidence": self.confidence,
            "risk_flags": list(self.risk_flags),
        }

    def digest(self) -> str:
        """Deterministic SHA-256 over canonical proposal JSON."""

        return digest(self.to_json())


@dataclass(frozen=True, slots=True)
class SweepAgent:
    """Water-management and sweep-efficiency proposal agent.

    The agent reads StateCard only. It emits advisory proposals and risk flags
    for later legality validation and arbitration; it never reports official
    constraint violations.
    """

    water_cut_threshold: float = 0.6
    low_oil_efficiency_threshold: float = 0.35
    wlpr_near_limit_margin_m3_day: float = 5.0
    agent_version: str = SWEEP_AGENT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "water_cut_threshold",
            "low_oil_efficiency_threshold",
            "wlpr_near_limit_margin_m3_day",
        ):
            value = optional_number(getattr(self, name), f"sweep_agent.{name}")
            assert value is not None
            if value < 0:
                raise ValueError(f"sweep_agent.{name} must be non-negative")
        if self.water_cut_threshold > 1.0:
            raise ValueError("sweep_agent.water_cut_threshold must be <= 1.0")
        if self.low_oil_efficiency_threshold > 1.0:
            raise ValueError("sweep_agent.low_oil_efficiency_threshold must be <= 1.0")
        required_text(self.agent_version, "sweep_agent.agent_version")

    def propose(self, state_card: StateCard) -> SweepProposal:
        """Create one deterministic sweep/water-cut proposal from StateCard."""

        producers = tuple(well for well in state_card.wells if well.role is WellRole.PRODUCER)
        for well in producers:
            _require_producer_data(well)

        high_water = tuple(well for well in producers if well.wct > self.water_cut_threshold)
        low_efficiency = tuple(
            well for well in high_water if _oil_efficiency(well) < self.low_oil_efficiency_threshold
        )
        near_wlpr = tuple(
            well
            for well in high_water
            if well.wlpr_margin is not None
            and well.wlpr_margin <= self.wlpr_near_limit_margin_m3_day
        )

        if near_wlpr:
            return self._proposal(
                state_card,
                proposal_type=ProposalType.WATER_SWEEP_WARNING,
                target=tuple(well.well_id for well in near_wlpr),
                reason=SweepProposalReason(
                    water_condition=NEAR_WLPR_LIMIT_WITH_HIGH_WATER_CUT,
                    oil_efficiency_condition=_efficiency_condition(near_wlpr),
                    expected_effect="Warn about high produced water near liquid-rate capacity.",
                ),
                confidence=0.75,
                risk_flags=(HIGH_WATER_CUT, NEAR_WLPR_LIMIT),
            )
        if low_efficiency:
            return self._proposal(
                state_card,
                proposal_type=ProposalType.REDUCE_WATER_RISK,
                target=tuple(well.well_id for well in low_efficiency),
                reason=SweepProposalReason(
                    water_condition=HIGH_WATER_CUT,
                    oil_efficiency_condition=LOW_OIL_EFFICIENCY,
                    expected_effect="Reduce produced-water risk from inefficient producers.",
                ),
                confidence=0.65,
                risk_flags=(HIGH_WATER_CUT, LOW_OIL_EFFICIENCY),
            )
        return self._proposal(
            state_card,
            proposal_type=ProposalType.HOLD,
            target=(),
            reason=SweepProposalReason(
                water_condition=WATER_CUT_STABLE,
                oil_efficiency_condition=OIL_EFFICIENCY_ACCEPTABLE,
                expected_effect="Maintain current producer-side priorities.",
            ),
            confidence=0.7,
            risk_flags=(),
        )

    def _proposal(
        self,
        state_card: StateCard,
        *,
        proposal_type: ProposalType,
        target: tuple[str, ...],
        reason: SweepProposalReason,
        confidence: float,
        risk_flags: tuple[str, ...],
    ) -> SweepProposal:
        return SweepProposal(
            metadata={
                "state_card_digest": state_card.digest(),
                "agent_version": self.agent_version,
            },
            proposal_type=proposal_type,
            target=target,
            reason=reason,
            confidence=confidence,
            risk_flags=risk_flags,
        )


def _require_producer_data(well: WellStateSnapshot) -> None:
    required = {
        "WLPR": well.wlpr,
        "WOPR": well.wopr,
        "WWPR": well.wwpr,
        "WWCT": well.wct,
    }
    for vector, value in required.items():
        if value is None:
            raise SweepAgentError(f"{well.well_id}: missing {vector}")
    if well.wlpr_margin is None:
        raise SweepAgentError(f"{well.well_id}: missing WLPR margin")


def _oil_efficiency(well: WellStateSnapshot) -> float:
    assert well.wlpr is not None and well.wopr is not None
    if well.wlpr <= 0:
        raise SweepAgentError(f"{well.well_id}: WLPR must be positive")
    return well.wopr / well.wlpr


def _efficiency_condition(wells: tuple[WellStateSnapshot, ...]) -> str:
    return (
        LOW_OIL_EFFICIENCY
        if any(_oil_efficiency(well) < 0.35 for well in wells)
        else OIL_EFFICIENCY_ACCEPTABLE
    )
