"""Canonical deterministic sweep/water-cut diagnostic service.

`SweepDiagnostic` -- canonical architecture name, symmetричен
`PressureDiagnostic`: deterministic evidence/diagnostic service, не
cognitive MAS agent, не создаёт Intent/Proposal, не имеет decision
authority.

Тонкая compatibility-обёртка над `aios.agents.sweep_agent.SweepAgent`: ни
одна формула, threshold или физическое допущение не менялись. `SweepAgent`
остаётся рабочим историческим именем.
"""

from __future__ import annotations

from aios.agents.sweep_agent import (
    HIGH_WATER_CUT,
    LOW_OIL_EFFICIENCY,
    NEAR_WLPR_LIMIT,
    NEAR_WLPR_LIMIT_WITH_HIGH_WATER_CUT,
    OIL_EFFICIENCY_ACCEPTABLE,
    SWEEP_AGENT_VERSION,
    WATER_CUT_STABLE,
    SweepAgent,
    SweepAgentError,
    SweepProposal,
    SweepProposalReason,
)
from aios.agents.sweep_agent import (
    ProposalType as SweepProposalType,
)

# Тот же identity string, что и у legacy SweepAgent -- смена изменила бы
# output content (metadata входит в digest), что противоречило бы
# behavior-preserving migration.
SWEEP_DIAGNOSTIC_VERSION = SWEEP_AGENT_VERSION

SweepDiagnosticError = SweepAgentError


class SweepDiagnostic(SweepAgent):
    """Canonical sweep/water-cut diagnostic. "legacy compatibility alias SweepAgent".

    Пустой subclass: все поля, defaults, thresholds и propose() унаследованы
    без изменений от SweepAgent. Для одного и того же StateCard
    SweepDiagnostic().propose(card) и SweepAgent().propose(card) дают
    побайтово идентичный SweepProposal.
    """


__all__ = [
    "HIGH_WATER_CUT",
    "LOW_OIL_EFFICIENCY",
    "NEAR_WLPR_LIMIT",
    "NEAR_WLPR_LIMIT_WITH_HIGH_WATER_CUT",
    "OIL_EFFICIENCY_ACCEPTABLE",
    "SWEEP_DIAGNOSTIC_VERSION",
    "WATER_CUT_STABLE",
    "SweepDiagnostic",
    "SweepDiagnosticError",
    "SweepProposal",
    "SweepProposalReason",
    "SweepProposalType",
]
