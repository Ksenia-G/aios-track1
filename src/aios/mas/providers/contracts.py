"""Нормализованная граница живого LLM-провайдера.

Провайдер получает промпт и возвращает текст. Он ничего не знает про
MagnitudeResolver, численные управления, Guardian, ЧДД и экономический отбор:
это внешний недоверенный компонент, а не участник принятия решений. Разбор,
словарь и авторитетные проверки остаются у специалиста -- провайдер не имеет
права «починить» ответ модели до вида валидного intent.

Секрет живёт только в памяти конфигурации и не попадает ни в один из типов,
которые кто-либо сериализует: ни в ответ, ни в провенанс, ни в текст ошибки.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from aios.evaluation.canonical import JsonValue, digest

#: Верхняя граница промпта. Запрос заведомо больше контекста модели -- это
#: ошибка сборки промпта, и платить за неё сетевым вызовом незачем.
MAX_PROMPT_CHARS = 200_000

#: Верхняя граница ответа. Специалист ждёт компактный JSON; всё, что сильно
#: больше, обрезать нельзя -- обрезанный JSON выглядел бы как поломка модели.
MAX_RESPONSE_CHARS = 200_000


class ProviderErrorKind(StrEnum):
    """Класс отказа. Определяет, имеет ли смысл повтор."""

    TIMEOUT = "TIMEOUT"
    CONNECTION = "CONNECTION"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    AUTH = "AUTH"
    BAD_REQUEST = "BAD_REQUEST"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    OVERSIZED = "OVERSIZED"


#: Отказы, которые имеет смысл повторить: они говорят о состоянии канала, а не
#: о нашем запросе. Повтор 400/401/403 только потратил бы бюджет.
RETRYABLE_KINDS = frozenset(
    {
        ProviderErrorKind.TIMEOUT,
        ProviderErrorKind.CONNECTION,
        ProviderErrorKind.RATE_LIMITED,
        ProviderErrorKind.SERVER_ERROR,
    }
)


class ProviderTransportError(RuntimeError):
    """Живой провайдер не отдал пригодный текст.

    Сообщение намеренно не содержит ни заголовков, ни тела запроса: текст
    ошибки попадает в логи и трассировки, и секрету там не место.
    """

    def __init__(self, kind: ProviderErrorKind, detail: str, *, status: int | None = None) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Потребление запроса. Только счётчики, ничего идентифицирующего."""

    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass(frozen=True, slots=True)
class ProviderCompletion:
    """Нормализованный ответ провайдера.

    Сырой текст здесь есть, потому что его обязан разобрать специалист, но в
    провенанс уходит только его дайджест: бенчмарк не хранит переписку с
    моделью.
    """

    text: str
    provider_id: str
    model_id: str
    latency_ms: int
    attempts: int = 1
    request_id: str | None = None
    usage: ProviderUsage = ProviderUsage()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ProviderTransportError(
                ProviderErrorKind.MALFORMED_RESPONSE, "provider returned no text"
            )
        if len(self.text) > MAX_RESPONSE_CHARS:
            raise ProviderTransportError(
                ProviderErrorKind.OVERSIZED,
                f"response of {len(self.text)} chars exceeds {MAX_RESPONSE_CHARS}",
            )

    @property
    def response_digest(self) -> str:
        return digest(self.text)

    def provenance(self) -> dict[str, JsonValue]:
        """Безопасный след вызова: идентичности и метрики, без текста."""
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "request_id": self.request_id,
            "response_digest": self.response_digest,
            "usage": self.usage.to_json(),
        }


@runtime_checkable
class TextCompletionProvider(Protocol):
    """Единственное, что провайдер умеет: промпт -> текст.

    `response_schema` -- ограничение генерации, необязательное и не меняющее
    смысла границы: возвращается всё тот же недоверенный текст, который обязан
    пройти строгий парсер специалиста.
    """

    def complete(
        self, prompt: str, *, response_schema: Mapping[str, Any] | None = None
    ) -> ProviderCompletion: ...


__all__ = [
    "MAX_PROMPT_CHARS",
    "MAX_RESPONSE_CHARS",
    "RETRYABLE_KINDS",
    "ProviderCompletion",
    "ProviderErrorKind",
    "ProviderTransportError",
    "ProviderUsage",
    "TextCompletionProvider",
]
