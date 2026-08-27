"""Конфигурация живого провайдера. Секрет не сериализуется никогда.

Ключ хранится в поле, которое исключено из `to_json()`, `repr` и провенанса:
единственный способ его увидеть -- запросить явно, и это делает только
транспорт при сборке заголовка.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum

from aios.evaluation.canonical import JsonValue, digest

#: Имена переменных окружения проекта.
PROVIDER_ENV = "MAS_LLM_PROVIDER"
MODEL_ENV = "MAS_LLM_MODEL"
TIMEOUT_ENV = "MAS_LLM_TIMEOUT_SECONDS"
TEMPERATURE_ENV = "MAS_LLM_TEMPERATURE"
MAX_TOKENS_ENV = "MAS_LLM_MAX_TOKENS"
BASE_URL_ENV = "MAS_LLM_BASE_URL"

MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 300.0
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_TOKENS = 2048


class ProviderConfigError(ValueError):
    """Конфигурация провайдера непригодна."""


class ProviderKind(StrEnum):
    """Поддерживаемые провайдеры.

    Список намеренно короткий: спекулятивные клиенты под провайдеров, которых
    здесь никто не настраивал, пришлось бы поддерживать вслепую.
    """

    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


#: Переменная с ключом для каждого провайдера, и документированный базовый URL.
_PROVIDER_CREDENTIAL_ENV = {
    ProviderKind.ANTHROPIC: "ANTHROPIC_API_KEY",
    ProviderKind.OPENAI_COMPATIBLE: "OPENAI_API_KEY",
}
_PROVIDER_DEFAULT_BASE_URL = {
    ProviderKind.ANTHROPIC: "https://api.anthropic.com",
    ProviderKind.OPENAI_COMPATIBLE: "https://api.openai.com",
}


@dataclass(frozen=True)
class ProviderConfig:
    """Всё, что нужно для вызова, и ровно одно поле, которое нельзя печатать."""

    kind: ProviderKind
    model_id: str
    base_url: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    temperature: float | None = None
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_attempts: int = 2
    _credential: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        ProviderKind(self.kind)
        if not self.model_id.strip():
            raise ProviderConfigError("provider config: model_id must not be empty")
        # Локальный провайдер без секрета -- единственное исключение, и оно
        # обязано быть явным, а не следствием опечатки в базовом URL.
        local = self.base_url.startswith(("http://127.0.0.1", "http://localhost"))
        if not self.base_url.startswith("https://") and not local:
            raise ProviderConfigError(
                "provider config: base_url must be https, or an explicit local address"
            )
        if not MIN_TIMEOUT_SECONDS <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ProviderConfigError(
                f"provider config: timeout_seconds must be within "
                f"[{MIN_TIMEOUT_SECONDS}, {MAX_TIMEOUT_SECONDS}]"
            )
        if self.max_tokens <= 0:
            raise ProviderConfigError("provider config: max_tokens must be > 0")
        if not 1 <= self.max_attempts <= 3:
            raise ProviderConfigError("provider config: max_attempts must be within [1, 3]")
        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ProviderConfigError("provider config: temperature must be within [0.0, 2.0]")

    @property
    def provider_id(self) -> str:
        return str(self.kind)

    @property
    def requires_credential(self) -> bool:
        """Локальный провайдер без сети наружу ключа не требует."""
        return self.base_url.startswith("https://")

    def credential(self) -> str:
        """Секрет отдаётся только транспорту и только по явному запросу."""
        if not self._credential and self.requires_credential:
            raise ProviderConfigError(
                f"provider config: {self.kind} requires a credential and none is configured"
            )
        return self._credential

    def to_json(self) -> dict[str, JsonValue]:
        """Публичное описание конфигурации. Секрета здесь нет и быть не может."""
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "base_url": self.base_url,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_attempts": self.max_attempts,
        }

    def digest(self) -> str:
        return digest(self.to_json())

    def __str__(self) -> str:  # pragma: no cover - тривиально
        return f"ProviderConfig({self.provider_id}, {self.model_id})"


def credential_env_for(kind: ProviderKind) -> str:
    return _PROVIDER_CREDENTIAL_ENV[ProviderKind(kind)]


def default_base_url_for(kind: ProviderKind) -> str:
    return _PROVIDER_DEFAULT_BASE_URL[ProviderKind(kind)]


def _float_env(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        raise ProviderConfigError(f"provider config: {name} must be a number") from None


def load_provider_config(environ: dict[str, str] | None = None) -> ProviderConfig | None:
    """Собрать конфигурацию из окружения, либо вернуть None.

    None означает «провайдер не настроен» и приводит к отсутствию варианта
    MAS ON, а не к его имитации.
    """
    env = dict(os.environ if environ is None else environ)
    raw_kind = (env.get(PROVIDER_ENV) or "").strip()
    model_id = (env.get(MODEL_ENV) or "").strip()
    if not raw_kind or not model_id:
        return None
    try:
        kind = ProviderKind(raw_kind)
    except ValueError:
        raise ProviderConfigError(
            f"provider config: unsupported provider {raw_kind!r}; "
            f"supported: {sorted(str(item) for item in ProviderKind)}"
        ) from None

    base_url = (env.get(BASE_URL_ENV) or default_base_url_for(kind)).rstrip("/")
    credential = env.get(credential_env_for(kind), "")
    timeout = _float_env(TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)
    temperature = _float_env(TEMPERATURE_ENV, None)
    raw_tokens = (env.get(MAX_TOKENS_ENV) or "").strip()
    max_tokens = int(raw_tokens) if raw_tokens else DEFAULT_MAX_TOKENS

    return ProviderConfig(
        kind=kind,
        model_id=model_id,
        base_url=base_url,
        timeout_seconds=timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS,
        temperature=temperature,
        max_tokens=max_tokens,
        _credential=credential,
    )


__all__ = [
    "BASE_URL_ENV",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "MAX_TOKENS_ENV",
    "MIN_TIMEOUT_SECONDS",
    "MODEL_ENV",
    "PROVIDER_ENV",
    "TEMPERATURE_ENV",
    "TIMEOUT_ENV",
    "ProviderConfig",
    "ProviderConfigError",
    "ProviderKind",
    "credential_env_for",
    "default_base_url_for",
    "load_provider_config",
]
