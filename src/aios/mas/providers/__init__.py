"""Живой когнитивный провайдер Hybrid MAS v1.1.

Внешний недоверенный компонент: он отдаёт текст, а решают -- строгий парсер,
словарь и авторитетные границы специалиста.
"""

from aios.mas.providers.adapters import InjectionLiveProvider, ProductionLiveProvider
from aios.mas.providers.config import (
    MODEL_ENV,
    PROVIDER_ENV,
    ProviderConfig,
    ProviderConfigError,
    ProviderKind,
    credential_env_for,
    load_provider_config,
)
from aios.mas.providers.contracts import (
    MAX_PROMPT_CHARS,
    MAX_RESPONSE_CHARS,
    ProviderCompletion,
    ProviderErrorKind,
    ProviderTransportError,
    ProviderUsage,
    TextCompletionProvider,
)
from aios.mas.providers.transport import (
    HttpTransport,
    LiveTextCompletionProvider,
    UrllibTransport,
)

__all__ = [
    "MAX_PROMPT_CHARS",
    "MAX_RESPONSE_CHARS",
    "MODEL_ENV",
    "PROVIDER_ENV",
    "HttpTransport",
    "InjectionLiveProvider",
    "LiveTextCompletionProvider",
    "ProductionLiveProvider",
    "ProviderCompletion",
    "ProviderConfig",
    "ProviderConfigError",
    "ProviderErrorKind",
    "ProviderKind",
    "ProviderTransportError",
    "ProviderUsage",
    "TextCompletionProvider",
    "UrllibTransport",
    "credential_env_for",
    "load_provider_config",
]
