"""Deterministic orchestration test stubs -- TEST SUPPORT ONLY.

These let Doцenko (Closed-Loop Orchestrator) start writing orchestration
tests before the real specialist agents / Arbiter / MagnitudeResolver exist.
Stubs return valid frozen-contract objects, are fully deterministic, never
call an LLM, never simulate a reservoir, and never compute CHDD -- they must
not hide real production behavior, only stand in for a component that does
not exist yet.

No stub lives under src/aios (production packages); this module is not
imported by any aios.* module (see tests/test_team_foundation.py,
TF14/TF15).
"""

from __future__ import annotations

from aios.mas.arbiter import ArbiterDecision
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.proposals import InjectionAgentOutput, ProductionAgentOutput
from tests.support.mas_team_fixtures import (
    synthetic_arbiter_decision,
    synthetic_candidate_set_with_floor,
    synthetic_injection_agent_output,
    synthetic_production_agent_output,
)


class StubProductionAgent:
    """Deterministic stand-in for Agent[ProductionEnvelope, ProductionAgentOutput].

    Ignores the envelope content and always returns the same synthetic
    HOLD-only output with a proper DETERMINISTIC_BASELINE -- enough to
    exercise orchestration wiring, not enough to claim any real reasoning.
    """

    def propose(self, envelope: object) -> ProductionAgentOutput:
        return synthetic_production_agent_output()


class StubInjectionAgent:
    """Deterministic stand-in for Agent[InjectionEnvelope, InjectionAgentOutput]."""

    def propose(self, envelope: object) -> InjectionAgentOutput:
        return synthetic_injection_agent_output()


class StubArbiter:
    """Deterministic orchestration test support, not a frozen production Protocol.

    aios.mas has no public Arbiter Protocol (only ArbiterEnvelope/
    ArbiterDecision data contracts and a MagnitudeResolver Protocol) -- this
    stub is a plain callable, not a new production API surface.
    """

    def decide(self, envelope: object) -> ArbiterDecision:
        return synthetic_arbiter_decision()


class StubMagnitudeResolver:
    """Deterministic stand-in for aios.mas.protocols.MagnitudeResolver."""

    def resolve(self, decision: ArbiterDecision) -> tuple[MagnitudeCandidate, ...]:
        return synthetic_candidate_set_with_floor()


__all__ = [
    "StubArbiter",
    "StubInjectionAgent",
    "StubMagnitudeResolver",
    "StubProductionAgent",
]
