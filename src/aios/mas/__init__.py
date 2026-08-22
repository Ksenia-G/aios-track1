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
from aios.mas.invariants import (
    DECISION_LAYER_TYPES,
    NUMERICAL_CONTROL_BOUNDARY_MODULES,
    MasInvariantError,
    audit_numerical_control_imports,
    canonical_control_digest,
    canonical_control_snapshot,
    require_ablation_equivalence,
    require_candidate_preservation,
    require_hold_carry_forward,
    require_no_baseline_field,
    require_no_numerical_controls_before_magnitude,
    require_numerical_control_import_boundary,
    require_simulation_floor,
)
from aios.mas.magnitude import (
    MagnitudeCandidate,
    MagnitudeCandidateError,
    injector_control_to_json,
    producer_control_to_json,
)
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
from aios.mas.protocols import Agent, MagnitudeResolver, SpecialistPromptBuilder

__all__ = [
    "DECISION_LAYER_TYPES",
    "MAS_CONTRACT_SCHEMA_VERSION",
    "NUMERICAL_CONTROL_BOUNDARY_MODULES",
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
    "MasInvariantError",
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
    "SpecialistPromptBuilder",
    "UncertaintyLevel",
    "audit_numerical_control_imports",
    "canonical_control_digest",
    "canonical_control_snapshot",
    "injector_control_to_json",
    "net_total_effect_for",
    "producer_control_to_json",
    "require_ablation_equivalence",
    "require_candidate_preservation",
    "require_hold_carry_forward",
    "require_no_baseline_field",
    "require_no_numerical_controls_before_magnitude",
    "require_numerical_control_import_boundary",
    "require_simulation_floor",
]
