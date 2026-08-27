"""HTTP-транспорт живого провайдера на стандартной библиотеке.

Отдельная зависимость сюда не тянется: запрос -- один POST с JSON-телом, и
ради него незачем добавлять в проект HTTP-библиотеку или SDK провайдера.

Транспорт не знает ни про специалистов, ни про их схему. Он делает вызов,
классифицирует отказ и возвращает текст. Решать, годится ли этот текст,
будет строгий парсер специалиста.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from aios.mas.providers.config import ProviderConfig, ProviderKind
from aios.mas.providers.contracts import (
    MAX_PROMPT_CHARS,
    MAX_RESPONSE_CHARS,
    ProviderCompletion,
    ProviderErrorKind,
    ProviderTransportError,
    ProviderUsage,
)

#: Сколько байт тела читаем максимум. Ответ больше этого не усекается в
#: «почти валидный» JSON -- он признаётся негодным целиком.
_READ_LIMIT_BYTES = MAX_RESPONSE_CHARS * 4

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_AUTH_STATUS = frozenset({401, 403})


class HttpTransport(Protocol):
    """Границы сетевого вызова. Подменяется в тестах без сети."""

    def post_json(
        self, url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float
    ) -> tuple[int, bytes]: ...


@dataclass(frozen=True, slots=True)
class UrllibTransport:
    """POST JSON стандартной библиотекой. HTTPS, явный таймаут, лимит чтения."""

    def post_json(
        self, url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float
    ) -> tuple[int, bytes]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read(_READ_LIMIT_BYTES)
        except urllib.error.HTTPError as error:
            return int(error.code), error.read(_READ_LIMIT_BYTES)
        except TimeoutError as error:
            raise ProviderTransportError(ProviderErrorKind.TIMEOUT, "request timed out") from error
        except urllib.error.URLError as error:
            reason = type(error.reason).__name__ if error.reason is not None else "unknown"
            if isinstance(error.reason, TimeoutError):
                raise ProviderTransportError(
                    ProviderErrorKind.TIMEOUT, "request timed out"
                ) from error
            raise ProviderTransportError(
                ProviderErrorKind.CONNECTION, f"connection failed ({reason})"
            ) from error
        except OSError as error:
            raise ProviderTransportError(
                ProviderErrorKind.CONNECTION, f"connection failed ({type(error).__name__})"
            ) from error


def _classify_status(status: int) -> ProviderErrorKind:
    if status in _AUTH_STATUS:
        return ProviderErrorKind.AUTH
    if status == 429:
        return ProviderErrorKind.RATE_LIMITED
    if status in _RETRYABLE_STATUS:
        return ProviderErrorKind.SERVER_ERROR
    if 500 <= status < 600:
        return ProviderErrorKind.SERVER_ERROR
    return ProviderErrorKind.BAD_REQUEST


def _headers(config: ProviderConfig) -> dict[str, str]:
    """Заголовки запроса. Возвращаются только транспорту и не логируются."""
    if config.kind is ProviderKind.ANTHROPIC:
        headers = {"anthropic-version": "2023-06-01"}
        if config.requires_credential:
            headers["x-api-key"] = config.credential()
        return headers
    headers = {}
    if config.requires_credential:
        headers["Authorization"] = f"Bearer {config.credential()}"
    return headers


def _endpoint(config: ProviderConfig) -> str:
    if config.kind is ProviderKind.ANTHROPIC:
        return f"{config.base_url}/v1/messages"
    return f"{config.base_url}/v1/chat/completions"


def _payload(
    config: ProviderConfig, prompt: str, response_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config.model_id,
        "max_tokens": config.max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if config.temperature is not None:
        payload["temperature"] = config.temperature

    if response_schema is None:
        # Обычное текстовое дополнение: запрос остаётся прежним, и провайдеры
        # без структурированного вывода ничего нового не получают.
        return payload

    if not config.structured_output:
        raise ProviderTransportError(
            ProviderErrorKind.BAD_REQUEST,
            "a response schema was requested but structured output is not enabled "
            "for this provider configuration",
        )
    if config.kind is not ProviderKind.OPENAI_COMPATIBLE:  # pragma: no cover - закрыто конфигом
        raise ProviderTransportError(
            ProviderErrorKind.BAD_REQUEST,
            f"structured output is not wired for provider {config.kind}",
        )
    payload["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": "specialist_intents",
            "strict": True,
            "schema": dict(response_schema),
        },
    }
    if config.reasoning_effort is not None:
        payload["reasoning_effort"] = config.reasoning_effort
    return payload


def _extract_text(config: ProviderConfig, document: Any) -> tuple[str, str | None, ProviderUsage]:
    """Достать текст ответа из документа провайдера.

    Отсутствующий или пустой текст -- отказ, а не пустая строка: пустой ответ,
    прошедший дальше, выглядел бы как ответ модели.
    """
    if not isinstance(document, dict):
        raise ProviderTransportError(
            ProviderErrorKind.MALFORMED_RESPONSE, "response body is not a JSON object"
        )
    request_id = document.get("id") if isinstance(document.get("id"), str) else None

    if config.kind is ProviderKind.ANTHROPIC:
        blocks = document.get("content")
        if not isinstance(blocks, list) or not blocks:
            raise ProviderTransportError(
                ProviderErrorKind.MALFORMED_RESPONSE, "response carries no content blocks"
            )
        parts = [
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "".join(parts)
        raw_usage = document.get("usage") or {}
        usage = ProviderUsage(
            input_tokens=raw_usage.get("input_tokens"),
            output_tokens=raw_usage.get("output_tokens"),
        )
    else:
        choices = document.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderTransportError(
                ProviderErrorKind.MALFORMED_RESPONSE, "response carries no choices"
            )
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise ProviderTransportError(
                ProviderErrorKind.MALFORMED_RESPONSE, "first choice carries no message"
            )
        text = message.get("content") or ""
        raw_usage = document.get("usage") or {}
        usage = ProviderUsage(
            input_tokens=raw_usage.get("prompt_tokens"),
            output_tokens=raw_usage.get("completion_tokens"),
        )

    if not isinstance(text, str) or not text.strip():
        raise ProviderTransportError(
            ProviderErrorKind.MALFORMED_RESPONSE, "response carries no text"
        )
    return text, request_id, usage


@dataclass(frozen=True, slots=True)
class LiveTextCompletionProvider:
    """Живой провайдер: промпт -> текст, с ограниченным повтором.

    Повторяются только отказы канала (таймаут, обрыв, 429, 5xx). Ответы вида
    400/401/403 повторять бессмысленно: они говорят о запросе, а не о сети, и
    повтор лишь потратил бы бюджет. Схемный повтор -- отдельная, уже
    существующая ответственность специалиста, и она сюда не дублируется.
    """

    config: ProviderConfig
    transport: HttpTransport = UrllibTransport()

    def complete(
        self, prompt: str, *, response_schema: Mapping[str, Any] | None = None
    ) -> ProviderCompletion:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ProviderTransportError(ProviderErrorKind.BAD_REQUEST, "prompt is empty")
        if len(prompt) > MAX_PROMPT_CHARS:
            # Платить сетевым вызовом за заведомо негодный промпт незачем.
            raise ProviderTransportError(
                ProviderErrorKind.OVERSIZED,
                f"prompt of {len(prompt)} chars exceeds {MAX_PROMPT_CHARS}",
            )

        endpoint = _endpoint(self.config)
        headers = _headers(self.config)
        payload = _payload(self.config, prompt, response_schema)
        started = time.monotonic()
        last: ProviderTransportError | None = None

        for attempt in range(1, self.config.max_attempts + 1):
            try:
                status, raw = self.transport.post_json(
                    endpoint,
                    headers=headers,
                    payload=payload,
                    timeout=self.config.timeout_seconds,
                )
                if status != 200:
                    raise ProviderTransportError(
                        _classify_status(status), f"provider returned HTTP {status}", status=status
                    )
                if len(raw) >= _READ_LIMIT_BYTES:
                    raise ProviderTransportError(
                        ProviderErrorKind.OVERSIZED, "response body exceeded the read limit"
                    )
                try:
                    document = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as error:
                    raise ProviderTransportError(
                        ProviderErrorKind.MALFORMED_RESPONSE, "response body is not valid JSON"
                    ) from error
                text, request_id, usage = _extract_text(self.config, document)
                return ProviderCompletion(
                    text=text,
                    provider_id=self.config.provider_id,
                    model_id=self.config.model_id,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    attempts=attempt,
                    request_id=request_id,
                    usage=usage,
                )
            except ProviderTransportError as error:
                last = error
                if not error.retryable or attempt >= self.config.max_attempts:
                    raise
        raise (
            last
            if last is not None
            else ProviderTransportError(  # pragma: no cover
                ProviderErrorKind.CONNECTION, "provider produced no result"
            )
        )


__all__ = [
    "HttpTransport",
    "LiveTextCompletionProvider",
    "UrllibTransport",
]
