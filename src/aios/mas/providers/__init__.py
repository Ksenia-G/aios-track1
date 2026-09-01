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
from aios.mas.providers.schemas import (
    FORBIDDEN_SCHEMA_FIELDS,
    injection_response_schema,
    production_response_schema,
    schema_field_names,
)
from aios.mas.providers.transport import (
    HttpTransport,
    LiveTextCompletionProvider,
    UrllibTransport,
)

__all__ = [
    "FORBIDDEN_SCHEMA_FIELDS",
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
    "injection_response_schema",
    "load_provider_config",
    "production_response_schema",
    "schema_field_names",
]
