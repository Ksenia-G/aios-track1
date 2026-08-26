"""Provider-independent interface for the optional Production LLM branch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aios.mas.common import ProposalSource

from .errors import ProductionControllerError


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Raw provider result; semantic validation happens after this boundary."""

    text: str
    model_id: str
    source: ProposalSource = ProposalSource.LLM_LOCAL
    latency_ms: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ProductionControllerError("provider response text must be non-empty")
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ProductionControllerError("provider model_id must be non-empty")
        if not isinstance(self.source, ProposalSource):
            raise ProductionControllerError("provider source must be a ProposalSource value")
        if self.source not in {
            ProposalSource.LLM_LIVE,
            ProposalSource.LLM_LOCAL,
            ProposalSource.LLM_REPLAY,
        }:
            raise ProductionControllerError("provider source must be an LLM source")
        if self.latency_ms is not None:
            if isinstance(self.latency_ms, bool) or not isinstance(self.latency_ms, int):
                raise ProductionControllerError("provider latency_ms must be an integer or null")
            if self.latency_ms < 0:
                raise ProductionControllerError("provider latency_ms must be >= 0")


@runtime_checkable
class ProductionLLMProvider(Protocol):
    """Minimal injectable provider used by live, local, replay, and fake backends."""

    def complete(self, prompt: str) -> ProviderResponse: ...
