"""Generic interfaces: Agent[TInput, TOutput] и MagnitudeResolver.

Только интерфейсы. Ни один specialist agent, ни один resolver здесь не
реализован -- это отдельные задачи. Новый Simulator protocol здесь не
создаётся: он уже есть в aios.evaluation.simulator.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from aios.mas.arbiter import ArbiterDecision
from aios.mas.magnitude import MagnitudeCandidate

TInput_contra = TypeVar("TInput_contra", contravariant=True)
TOutput_co = TypeVar("TOutput_co", covariant=True)
TEnvelope_contra = TypeVar("TEnvelope_contra", contravariant=True)


@runtime_checkable
class Agent(Protocol[TInput_contra, TOutput_co]):
    """Единый интерфейс specialist-агента, параметризованный по ролям.

    ProductionAgent реализует Agent[ProductionEnvelope, ProductionAgentOutput],
    InjectionAgent -- Agent[InjectionEnvelope, InjectionAgentOutput]. Сама
    реализация (PromptBuilder, LLM-вызов, парсинг, fallback) -- вне этой
    задачи.
    """

    def propose(self, envelope: TInput_contra) -> TOutput_co: ...


@runtime_checkable
class MagnitudeResolver(Protocol):
    """Интерфейс перехода от ArbiterDecision к прогоняемым MagnitudeCandidate.

    Правила SMALL/MODERATE/AGGRESSIVE -> конкретная ставка здесь не заданы:
    это отдельная задача.
    """

    def resolve(self, decision: ArbiterDecision) -> tuple[MagnitudeCandidate, ...]: ...


@runtime_checkable
class SpecialistPromptBuilder(Protocol[TEnvelope_contra]):
    """Граница будущего PromptBuilder (T1, anti-anchoring).

    Сигнатура сама по себе -- часть инварианта: build() принимает ровно один
    аргумент, envelope. Baseline вторым аргументом передать невозможно,
    потому что для него здесь нет параметра. Реализация (сборка текста
    промпта, LLM-вызов) -- вне этой задачи.
    """

    def build(self, envelope: TEnvelope_contra) -> str: ...
