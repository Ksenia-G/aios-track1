"""Canonical home for deterministic diagnostic (evidence) services.

`PressureDiagnostic` и `SweepDiagnostic` -- deterministic evidence services
поверх StateCard. Они не являются cognitive MAS agents: не создают Intent,
Proposal (в смысле aios.mas), не участвуют в Arbiter как самостоятельные
специалисты и не имеют decision authority. Специалисты-агенты в будущем
используют их typed output как evidence (через будущий EvidenceBuilder
adapter в aios.mas.evidence.EvidenceArtifact), а не как готовое решение.

Исторические имена `aios.agents.pressure_agent.PressureAgent` и
`aios.agents.sweep_agent.SweepAgent` продолжают работать без изменений --
это compatibility layer, не удалённый этой миграцией.

`ProposalType` для pressure и sweep -- два разных, несвязанных enum'а
(легаси-дизайн, унаследованный без изменений). Чтобы не создавать
неоднозначный bare-импорт на уровне пакета, они здесь не ре-экспортируются:
используйте `aios.diagnostics.pressure.ProposalType` или
`aios.diagnostics.sweep.ProposalType` явно.
"""

from aios.diagnostics.pressure import (
    PRESSURE_DIAGNOSTIC_VERSION,
    PressureDiagnostic,
    PressureDiagnosticError,
    PressureProposal,
    PressureProposalReason,
)
from aios.diagnostics.sweep import (
    SWEEP_DIAGNOSTIC_VERSION,
    SweepDiagnostic,
    SweepDiagnosticError,
    SweepProposal,
    SweepProposalReason,
)

__all__ = [
    "PRESSURE_DIAGNOSTIC_VERSION",
    "SWEEP_DIAGNOSTIC_VERSION",
    "PressureDiagnostic",
    "PressureDiagnosticError",
    "PressureProposal",
    "PressureProposalReason",
    "SweepDiagnostic",
    "SweepDiagnosticError",
    "SweepProposal",
    "SweepProposalReason",
]
