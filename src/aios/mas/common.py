"""Общий словарь Hybrid MAS v1.1 contract layer.

Эти enum -- только структурная классификация. Ни один enum здесь не несёт
числового значения: перевод SMALL/MODERATE/AGGRESSIVE в конкретный процент
или ставку -- ответственность MagnitudeResolver, которого в этом пакете нет.
LLM предлагает гипотезы через эти роли и категории; детерминированное ядро
остаётся единственным владельцем физики, легальности и итогового выбора.
"""

from __future__ import annotations

from enum import StrEnum

# Идентичность схемы контрактов. Инкремент обязателен при любом breaking
# изменении состава полей: старые сериализованные контракты после этого
# не считаются валидными без явной миграции.
MAS_CONTRACT_SCHEMA_VERSION = "aios.mas.contracts/1.0.0"


class AgentRole(StrEnum):
    """Роль агента в цепочке specialist -> Arbiter."""

    PRODUCTION = "PRODUCTION"
    INJECTION = "INJECTION"
    ARBITER = "ARBITER"


class DecisionRound(StrEnum):
    """Раунд принятия решения. Round 2 -- опциональный conflict workflow."""

    ROUND_1 = "ROUND_1"
    ROUND_2 = "ROUND_2"


class Direction(StrEnum):
    """Направление предлагаемого изменения, без величины."""

    UP = "UP"
    DOWN = "DOWN"
    NONE = "NONE"


class Intensity(StrEnum):
    """Качественная интенсивность предложения, без числовой величины.

    SMALL/MODERATE/AGGRESSIVE намеренно не связаны ни с каким процентом или
    ставкой в этом слое контрактов: numeric magnitude появляется только на
    границе MagnitudeResolver, которая здесь не реализована.
    """

    NONE = "NONE"
    SMALL = "SMALL"
    MODERATE = "MODERATE"
    AGGRESSIVE = "AGGRESSIVE"


class UncertaintyLevel(StrEnum):
    """Заявленная агентом уверенность в собственном предложении."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class ProposalSource(StrEnum):
    """Источник, породивший proposal.

    DETERMINISTIC_FALLBACK -- primary-предложение, вернувшееся к детерминизму
    (например, llm_enabled=False ablation), но baseline -- это всегда
    отдельный DETERMINISTIC_BASELINE объект, а не тот же самый.
    """

    DETERMINISTIC_BASELINE = "DETERMINISTIC_BASELINE"
    LLM_LIVE = "LLM_LIVE"
    LLM_LOCAL = "LLM_LOCAL"
    LLM_REPLAY = "LLM_REPLAY"
    DETERMINISTIC_FALLBACK = "DETERMINISTIC_FALLBACK"


class CandidateKind(StrEnum):
    """Категория кандидата в ArbiterDecision."""

    HOLD = "HOLD"
    DETERMINISTIC_BASELINE = "DETERMINISTIC_BASELINE"
    AGENT_BUNDLE = "AGENT_BUNDLE"


class NetTotalEffect(StrEnum):
    """Метаданные суммарного эффекта injection action на total закачку.

    Это классификация действия, а не измеренная величина: DIRECTIONAL
    означает "однозначно вверх или вниз в зависимости от direction", а не
    числовой прогноз.
    """

    ZERO = "ZERO"
    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    DIRECTIONAL = "DIRECTIONAL"
