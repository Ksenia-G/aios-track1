"""Hybrid MAS v1.1 frozen contract layer.

LLM proposes hypotheses; deterministic core controls numerical setpoints,
physics, legality and final selection. Numeric prescriptive control appears
only at the aios.mas.magnitude boundary, after the Arbiter -- nowhere above
it in this package. This module is a stable import surface for the contract
types; it does not implement agents, prompts, orchestration, or resolvers.
"""

from aios.mas.arbiter import ArbiterDecision, ArbiterError, DecisionBundle, Round2Request
from aios.mas.common import (
    MAS_CONTRACT_SCHEMA_VERSION,
    AgentRole,
    CandidateKind,
    DecisionRound,
    Direction,
    Intensity,
    NetTotalEffect,
    ProposalSource,
    UncertaintyLevel,
)
from aios.mas.envelopes import ArbiterEnvelope, EnvelopeError, InjectionEnvelope, ProductionEnvelope
from aios.mas.evidence import (
    EvidenceArtifact,
    EvidenceError,
    HorizonContext,
    MemorySlice,
    ProposalReference,
    SimulationBudget,
)
from aios.mas.intents import (
    InjectionAction,
    InjectionIntent,
    IntentError,
    ProductionAction,
    ProductionIntent,
    net_total_effect_for,
)
from aios.mas.magnitude import MagnitudeCandidate, MagnitudeCandidateError
from aios.mas.proposals import (
    GroundingReport,
    InjectionAgentOutput,
    InjectionProposal,
    ProductionAgentOutput,
    ProductionProposal,
    ProposalAudit,
    ProposalError,
    ProposalProvenance,
)
from aios.mas.protocols import Agent, MagnitudeResolver

__all__ = [
    "MAS_CONTRACT_SCHEMA_VERSION",
    "Agent",
    "AgentRole",
    "ArbiterDecision",
    "ArbiterEnvelope",
    "ArbiterError",
    "CandidateKind",
    "DecisionBundle",
    "DecisionRound",
    "Direction",
    "EnvelopeError",
    "EvidenceArtifact",
    "EvidenceError",
    "GroundingReport",
    "HorizonContext",
    "InjectionAction",
    "InjectionAgentOutput",
    "InjectionEnvelope",
    "InjectionIntent",
    "InjectionProposal",
    "Intensity",
    "IntentError",
    "MagnitudeCandidate",
    "MagnitudeCandidateError",
    "MagnitudeResolver",
    "MemorySlice",
    "NetTotalEffect",
    "ProductionAction",
    "ProductionAgentOutput",
    "ProductionEnvelope",
    "ProductionIntent",
    "ProductionProposal",
    "ProposalAudit",
    "ProposalError",
    "ProposalProvenance",
    "ProposalReference",
    "ProposalSource",
    "Round2Request",
    "SimulationBudget",
    "UncertaintyLevel",
    "net_total_effect_for",
]
