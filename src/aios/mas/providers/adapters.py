"""Тонкие адаптеры одного живого провайдера под два контракта специалистов.

Production ждёт `ProviderResponse`, Injection -- строку. Общая сетевая логика
живёт в одном месте: дублировать её означало бы позволить двум веткам
незаметно разойтись в поведении при отказе.

Адаптер ничего не исправляет в ответе модели. Если модель вернула численный
setpoint, он дойдёт до строгого парсера специалиста и будет отвергнут там --
провайдер не имеет права привести ответ к «правильному» виду.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aios.mas.common import ProposalSource
from aios.mas.providers.contracts import ProviderCompletion, TextCompletionProvider
from aios.mas.providers.schemas import injection_response_schema, production_response_schema
from aios.mas.specialists.production.provider import ProviderResponse


@dataclass(frozen=True, slots=True)
class ProductionLiveProvider:
    """Адаптер под `ProductionLLMProvider`.

    Схема ограничивает генерацию, но авторитет остаётся у специалиста: текст
    возвращается как есть и разбирается его строгим парсером.
    """

    provider: TextCompletionProvider
    source: ProposalSource = ProposalSource.LLM_LIVE
    structured: bool = False

    def complete(self, prompt: str) -> ProviderResponse:
        schema = production_response_schema() if self.structured else None
        completion = self.provider.complete(prompt, response_schema=schema)
        return ProviderResponse(
            text=completion.text,
            model_id=completion.model_id,
            source=self.source,
            latency_ms=completion.latency_ms,
        )


@dataclass
class InjectionLiveProvider:
    """Адаптер под инъекционный `LlmProvider`.

    Контракт возвращает строку, поэтому провенанс последнего вызова хранится
    рядом: так идентичность модели и задержка остаются доступны, а авторитет
    специалиста не меняется. `source` объявляет, откуда пришла генерация --
    тем же полем, что и у `ProductionLiveProvider` -- потому что bare-строка
    из `complete()` не может нести это сама.
    """

    provider: TextCompletionProvider
    model_id: str = ""
    source: ProposalSource = ProposalSource.LLM_LIVE
    structured: bool = False
    last_completion: ProviderCompletion | None = field(default=None, repr=False)

    def complete(self, prompt: str) -> str:
        schema = injection_response_schema() if self.structured else None
        completion = self.provider.complete(prompt, response_schema=schema)
        self.last_completion = completion
        return completion.text


__all__ = ["InjectionLiveProvider", "ProductionLiveProvider"]
