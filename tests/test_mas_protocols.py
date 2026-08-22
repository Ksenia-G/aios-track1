"""Generic Agent Protocol and MagnitudeResolver interface (no implementation)."""

from __future__ import annotations

from aios.mas.arbiter import ArbiterDecision
from aios.mas.envelopes import ProductionEnvelope
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.proposals import ProductionAgentOutput
from aios.mas.protocols import Agent, MagnitudeResolver


class _DummyProductionAgent:
    """Минимальная реализация Agent[ProductionEnvelope, ProductionAgentOutput]."""

    def propose(self, envelope: ProductionEnvelope) -> ProductionAgentOutput:
        raise NotImplementedError


class _DummyMagnitudeResolver:
    def resolve(self, decision: ArbiterDecision) -> tuple[MagnitudeCandidate, ...]:
        raise NotImplementedError


def test_agent_protocol_is_runtime_checkable_and_role_specific() -> None:
    agent: Agent[ProductionEnvelope, ProductionAgentOutput] = _DummyProductionAgent()
    assert isinstance(agent, Agent)
    assert hasattr(agent, "propose")


def test_magnitude_resolver_protocol_is_runtime_checkable() -> None:
    resolver: MagnitudeResolver = _DummyMagnitudeResolver()
    assert isinstance(resolver, MagnitudeResolver)


def test_non_conforming_object_is_not_an_agent() -> None:
    class NotAnAgent:
        pass

    assert not isinstance(NotAnAgent(), Agent)


def test_protocols_module_does_not_define_a_new_simulator_protocol() -> None:
    import aios.mas.protocols as protocols_module

    assert not hasattr(protocols_module, "Simulator")
