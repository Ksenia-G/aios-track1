"""Обнаружение когнитивного провайдера для варианта MAS ON.

Секреты здесь не читаются и не печатаются: проверяется только наличие
объявленной конфигурации. Если одобренного провайдера нет, вариант MAS ON не
запускается и не подделывается -- отсутствие результата честнее выдуманного.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Переменные окружения, объявленные политикой проекта (.env.example).
#: Провайдера LLM среди них нет намеренно: проект его пока не объявляет.
DECLARED_EXTERNAL_VARIABLES = ("MODEL_Y_ROOT", "CHDD_ROOT")

#: Переменная, которой проект объявил бы одобренного провайдера, если бы имел его.
PROVIDER_ENV_VARIABLE = "AIOS_LLM_PROVIDER"
MODEL_ENV_VARIABLE = "AIOS_LLM_MODEL"


@dataclass(frozen=True, slots=True)
class ProviderAvailability:
    """Доступность когнитивной ветки. Значений ключей здесь нет."""

    available: bool
    provider_id: str | None
    model_id: str | None
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "available": self.available,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "reason": self.reason,
        }


def detect_provider() -> ProviderAvailability:
    """Определить, можно ли вообще запустить MAS ON.

    Требуется одновременно объявленная конфигурация и реализация клиента. В
    репозитории сейчас нет ни того, ни другого: `provider.py` специалистов
    содержит только протокол и детерминированный fake, а `.env.example`
    объявляет лишь пути к колоде и пакету ЧДД.
    """
    provider_id = os.environ.get(PROVIDER_ENV_VARIABLE) or None
    model_id = os.environ.get(MODEL_ENV_VARIABLE) or None

    if not provider_id or not model_id:
        return ProviderAvailability(
            available=False,
            provider_id=None,
            model_id=None,
            reason=(
                f"no approved cognitive provider is configured: {PROVIDER_ENV_VARIABLE} "
                f"and {MODEL_ENV_VARIABLE} are unset, and the project's .env.example "
                "declares only MODEL_Y_ROOT and CHDD_ROOT"
            ),
        )

    if not _live_client_available():
        return ProviderAvailability(
            available=False,
            provider_id=provider_id,
            model_id=model_id,
            reason=(
                "a provider is named but no approved live client implementation exists "
                "in aios.mas.specialists.*.provider: only the Protocol and the "
                "deterministic fake are present"
            ),
        )

    return ProviderAvailability(
        available=True,
        provider_id=provider_id,
        model_id=model_id,
        reason="approved provider configuration and client are both present",
    )


def _live_client_available() -> bool:
    """Есть ли в проекте одобренная реализация живого клиента."""
    try:
        from aios.mas.specialists.injection import provider as injection_provider
    except ImportError:  # pragma: no cover - пакет всегда доступен
        return False
    return hasattr(injection_provider, "LiveLlmProvider")


__all__ = [
    "DECLARED_EXTERNAL_VARIABLES",
    "MODEL_ENV_VARIABLE",
    "PROVIDER_ENV_VARIABLE",
    "ProviderAvailability",
    "detect_provider",
]
