"""Реестр ограничений.

Реальные конкурсные ограничения неизвестны, поэтому здесь только механика:
именованные предикаты и отчёт. Ни DSL, ни плагинов, ни иерархии важности --
всё это добавляется тогда, когда появится хотя бы одно настоящее ограничение.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from aios.evaluation.artifacts import ArtifactReader
from aios.evaluation.canonical import JsonValue, normalize_json
from aios.evaluation.simulation import SimulationRequest, SimulationResult


class ConstraintStatus(StrEnum):
    """Состояние проверки ограничений для прогона."""

    NOT_CHECKED = "NOT_CHECKED"
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    """Одно нарушение.

    `is_blocking` означает только одно: на PRE-стадии такое нарушение
    запрещает запускать симулятор. Это про экономию счёта, а не про
    «плохость» варианта.
    """

    constraint_id: str
    message: str
    is_blocking: bool = False
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "constraint_id": self.constraint_id,
            "message": self.message,
            "is_blocking": self.is_blocking,
            "metadata": normalize_json(dict(self.metadata)),
        }


@dataclass(frozen=True, slots=True)
class ConstraintReport:
    """Итог проверки набора предикатов."""

    violations: tuple[ConstraintViolation, ...] = ()

    @property
    def is_feasible(self) -> bool:
        return not self.violations

    @property
    def has_blocking(self) -> bool:
        return any(violation.is_blocking for violation in self.violations)

    def to_json(self) -> list[JsonValue]:
        return [violation.to_json() for violation in self.violations]


PreConstraint = Callable[[SimulationRequest], Sequence[ConstraintViolation]]
# POST-предикат получает читатель артефакта: настоящее ограничение по
# результату обязано смотреть в траекторию, а не только в метаданные.
# Конкретное хранилище в контракт не попадает -- только протокол чтения.
PostConstraint = Callable[[SimulationResult, ArtifactReader], Sequence[ConstraintViolation]]


class ConstraintRegistry:
    """Набор PRE- и POST-предикатов.

    PRE проверяется по запросу до симуляции, POST -- по результату после.
    Разделение нужно, чтобы заведомо недопустимый вариант не оплачивался
    временем счёта.
    """

    def __init__(self) -> None:
        self._pre: dict[str, PreConstraint] = {}
        self._post: dict[str, PostConstraint] = {}

    def register_pre(self, constraint_id: str, predicate: PreConstraint) -> None:
        if constraint_id in self._pre:
            raise ValueError(f"pre constraint already registered: {constraint_id}")
        self._pre[constraint_id] = predicate

    def register_post(self, constraint_id: str, predicate: PostConstraint) -> None:
        if constraint_id in self._post:
            raise ValueError(f"post constraint already registered: {constraint_id}")
        self._post[constraint_id] = predicate

    @property
    def pre_constraint_ids(self) -> tuple[str, ...]:
        return tuple(self._pre)

    @property
    def post_constraint_ids(self) -> tuple[str, ...]:
        return tuple(self._post)

    def check_pre(self, request: SimulationRequest) -> ConstraintReport:
        violations: list[ConstraintViolation] = []
        for predicate in self._pre.values():
            violations.extend(predicate(request))
        return ConstraintReport(tuple(violations))

    def check_post(self, result: SimulationResult, artifacts: ArtifactReader) -> ConstraintReport:
        violations: list[ConstraintViolation] = []
        for predicate in self._post.values():
            violations.extend(predicate(result, artifacts))
        return ConstraintReport(tuple(violations))
