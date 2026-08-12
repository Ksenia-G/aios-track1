"""Контракт экономической оценки.

Официальная формула NPV, её слагаемые и единицы измерения на этом этапе
неизвестны, поэтому здесь нет ни одной экономической константы -- только
контейнер значения и его происхождение.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Protocol, runtime_checkable

from aios.evaluation.artifacts import ArtifactReader
from aios.evaluation.canonical import JsonValue, normalize_json
from aios.evaluation.simulation import SimulationResult


class EvaluatorProvenance(StrEnum):
    """Происхождение оценки.

    Безопасное значение по умолчанию -- NOT_OFFICIAL. OFFICIAL нельзя получить
    случайно: его требуется указать явно, и это единственный способ.
    """

    NOT_OFFICIAL = "NOT_OFFICIAL"
    OFFICIAL = "OFFICIAL"


class NpvStatus(StrEnum):
    """Состояние экономической оценки конкретного прогона."""

    NOT_EVALUATED = "NOT_EVALUATED"
    OK = "OK"
    FAILED = "FAILED"


class NpvEvaluationError(RuntimeError):
    """Отказ эвалюатора. Никогда не заменяется числом."""


@dataclass(frozen=True, slots=True)
class NpvEstimate:
    """Оценка NPV вместе с происхождением.

    Значение не существует отдельно от provenance: голое число легко попадает
    в отчёт или на слайд, где уже невозможно отличить синтетику от официальной
    метрики. Единицы измерения не угадываются -- по умолчанию `unit is None`.
    """

    value: float
    evaluator_id: str
    evaluator_version: str
    provenance: EvaluatorProvenance = EvaluatorProvenance.NOT_OFFICIAL
    unit: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Строка вместо enum отклоняется: иначе опечатка в литерале даёт
        # тихо «официальную» пометку.
        if not isinstance(self.provenance, EvaluatorProvenance):
            raise TypeError("provenance must be an EvaluatorProvenance member")
        if not isinstance(self.value, int | float) or isinstance(self.value, bool):
            raise TypeError("npv value must be a real number")
        if not isfinite(float(self.value)):
            raise ValueError("npv value must be finite")

    @property
    def is_official(self) -> bool:
        return self.provenance is EvaluatorProvenance.OFFICIAL

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "value": float(self.value),
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "provenance": str(self.provenance),
            "unit": self.unit,
            "metadata": normalize_json(dict(self.metadata)),
        }


@runtime_checkable
class NpvEvaluator(Protocol):
    """Контракт эвалюатора: результат симуляции -> оценка."""

    @property
    def evaluator_id(self) -> str: ...

    @property
    def evaluator_version(self) -> str: ...

    def evaluate(self, result: SimulationResult, artifacts: ArtifactReader) -> NpvEstimate:
        """Оценить результат, читая артефакт только через ArtifactReader."""
        ...
