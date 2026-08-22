"""Canonical deterministic pressure/voidage diagnostic service.

`PressureDiagnostic` -- canonical architecture name. Оно deterministic
evidence/diagnostic service, а не cognitive MAS agent: не создаёт Intent,
Proposal (в смысле aios.mas), не участвует в Arbiter как самостоятельный
специалист и не имеет decision authority. Его typed output описывает
состояние (evidence), а не предписывает управление.

Это тонкая compatibility-обёртка над уже существующей реализацией
`aios.agents.pressure_agent.PressureAgent`: ни одна формула, threshold или
физическое допущение здесь не менялись. `PressureAgent` остаётся рабочим
историческим именем -- этот модуль не удаляет и не переопределяет его
поведение, только добавляет canonical alias поверх.
"""

from __future__ import annotations

from aios.agents.pressure_agent import (
    INJECTOR_CAPACITY_LIMITED,
    NO_INJECTOR_AVAILABLE,
    PRESSURE_AGENT_VERSION,
    PRESSURE_SUPPORT_NEEDED,
    PRESSURE_SUPPORT_STABLE,
    PressureAgent,
    PressureAgentError,
    PressureProposal,
    PressureProposalReason,
    ProposalType,
)

# Версия сознательно не переименована в "diagnostic": строка входит в
# metadata каждого PressureProposal и, значит, в его digest. Смена строки
# здесь изменила бы output content, что нарушило бы behavior-preserving
# migration. Это тот же identity string, что и у legacy PressureAgent.
PRESSURE_DIAGNOSTIC_VERSION = PRESSURE_AGENT_VERSION

# Тот же класс ошибки, без новой иерархии исключений.
PressureDiagnosticError = PressureAgentError


class PressureDiagnostic(PressureAgent):
    """Canonical pressure/voidage diagnostic. "legacy compatibility alias PressureAgent".

    Пустой subclass: все поля, defaults, thresholds и propose() унаследованы
    без изменений от PressureAgent. Для одного и того же StateCard
    PressureDiagnostic().propose(card) и PressureAgent().propose(card) дают
    побайтово идентичный PressureProposal (тот же to_json(), тот же digest()).
    """


__all__ = [
    "INJECTOR_CAPACITY_LIMITED",
    "NO_INJECTOR_AVAILABLE",
    "PRESSURE_DIAGNOSTIC_VERSION",
    "PRESSURE_SUPPORT_NEEDED",
    "PRESSURE_SUPPORT_STABLE",
    "PressureDiagnostic",
    "PressureDiagnosticError",
    "PressureProposal",
    "PressureProposalReason",
    "ProposalType",
]
