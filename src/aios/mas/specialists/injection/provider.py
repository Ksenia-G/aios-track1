"""Injected LLM boundary. Live API здесь нет -- только протокол и fake."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


class LlmProviderError(ValueError):
    """Провайдер не смог вернуть текст ответа."""


@runtime_checkable
class LlmProvider(Protocol):
    """provider-independent complete(prompt) -> str."""

    def complete(self, prompt: str) -> str: ...


def read_provider_text(provider: LlmProvider, prompt: str) -> str:
    """Любой сбой или не-str ответ -- LlmProviderError, не пробой floor."""
    try:
        response = provider.complete(prompt)
    except Exception as exc:
        raise LlmProviderError(f"provider failed: {type(exc).__name__}") from exc
    if not isinstance(response, str):
        raise LlmProviderError(f"malformed provider return: {type(response).__name__}")
    return response


class FakeLlmProvider:
    """Детерминированный fake: отдаёт заранее заданные ответы по порядку."""

    def __init__(
        self,
        responses: str | Sequence[str],
        *,
        model_id: str = "fake-injection-llm",
    ) -> None:
        self._responses = [responses] if isinstance(responses, str) else list(responses)
        self._index = 0
        self.model_id = model_id
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self._index >= len(self._responses):
            raise LlmProviderError("fake provider has no remaining responses")
        text = self._responses[self._index]
        self._index += 1
        return text
