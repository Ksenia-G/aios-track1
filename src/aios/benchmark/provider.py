"""Обнаружение когнитивного провайдера для варианта MAS ON.

Секреты здесь не читаются и не печатаются: проверяется только наличие
объявленной конфигурации. Если одобренного провайдера нет, вариант MAS ON не
запускается и не подделывается -- отсутствие результата честнее выдуманного.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Переменные окружения, объявленные политикой проекта (.env.example).
#: Провайдера LLM среди них нет намеренно: проект его пока не объявляет.
DECLARED_EXTERNAL_VARIABLES = ("MODEL_Y_ROOT", "CHDD_ROOT")

#: Объявленные проектом переменные когнитивного провайдера.
from aios.mas.providers.config import MODEL_ENV as MODEL_ENV_VARIABLE
from aios.mas.providers.config import PROVIDER_ENV as PROVIDER_ENV_VARIABLE


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
    """Определить, можно ли запустить MAS ON.

    Клиент в проекте теперь есть; недостающим звеном остаётся одобренная
    конфигурация. Отсутствие ключа -- это отсутствие варианта MAS ON, а не
    повод его сымитировать.
    """
    from aios.mas.providers.config import (
        ProviderConfigError,
        credential_env_for,
        load_provider_config,
    )

    try:
        config = load_provider_config()
    except ProviderConfigError as error:
        return ProviderAvailability(
            available=False, provider_id=None, model_id=None, reason=str(error)
        )

    if config is None:
        return ProviderAvailability(
            available=False,
            provider_id=None,
            model_id=None,
            reason=(
                f"no cognitive provider is configured: set {PROVIDER_ENV_VARIABLE} and "
                f"{MODEL_ENV_VARIABLE}, plus the provider's documented credential variable"
            ),
        )

    if config.requires_credential and not config._credential:
        return ProviderAvailability(
            available=False,
            provider_id=config.provider_id,
            model_id=config.model_id,
            reason=(
                f"provider {config.provider_id} is configured but its credential variable "
                f"{credential_env_for(config.kind)} is unset"
            ),
        )

    return ProviderAvailability(
        available=True,
        provider_id=config.provider_id,
        model_id=config.model_id,
        reason="approved provider configuration and client are both present",
    )


def build_live_providers():
    """Собрать адаптеры живого провайдера для обоих специалистов."""
    from aios.mas.providers import (
        InjectionLiveProvider,
        LiveTextCompletionProvider,
        ProductionLiveProvider,
    )
    from aios.mas.providers.config import load_provider_config

    config = load_provider_config()
    if config is None:
        return None, None, None
    live = LiveTextCompletionProvider(config=config)
    # Схему просим только там, где провайдер её действительно поддерживает;
    # в остальном путь остаётся прежним текстовым дополнением.
    structured = config.structured_output
    return (
        ProductionLiveProvider(provider=live, structured=structured),
        InjectionLiveProvider(provider=live, model_id=config.model_id, structured=structured),
        config,
    )


__all__ = [
    "DECLARED_EXTERNAL_VARIABLES",
    "MODEL_ENV_VARIABLE",
    "PROVIDER_ENV_VARIABLE",
    "ProviderAvailability",
    "build_live_providers",
    "detect_provider",
]
