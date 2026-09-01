"""Проверка словаря действий Injection Controller.

Новые enum не придумываются. Action/direction/intensity должны быть frozen
членами, а action ещё и входить в allowed_actions envelope. Невалидный
набор intents отклоняется целиком.
"""

from __future__ import annotations

from collections.abc import Sequence

from aios.mas.common import Direction, Intensity
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.mas.specialists.injection.prescriptive import (
    PrescriptiveTextError,
    reject_prescriptive_intent,
)


class VocabularyError(ValueError):
    """Action/direction/intensity вне frozen словаря или вне allowed_actions."""


class VocabularyValidator:
    """Сверить intents с frozen vocabulary и allowed_actions."""

    def validate_labels(
        self, action: str, direction: str, intensity: str
    ) -> tuple[InjectionAction, Direction, Intensity]:
        resolved_action = _parse_enum(InjectionAction, action, "action")
        resolved_direction = _parse_enum(Direction, direction, "direction")
        resolved_intensity = _parse_enum(Intensity, intensity, "intensity")
        return resolved_action, resolved_direction, resolved_intensity

    def validate_intents(
        self,
        intents: Sequence[InjectionIntent],
        *,
        allowed_actions: Sequence[InjectionAction],
    ) -> tuple[InjectionIntent, ...]:
        if not intents:
            raise VocabularyError("intents must not be empty")
        allowed = frozenset(allowed_actions)
        for intent in intents:
            try:
                self.validate_labels(
                    str(intent.action), str(intent.direction), str(intent.intensity)
                )
            except VocabularyError as exc:
                raise VocabularyError("intents contain an invalid vocabulary label") from exc
            if intent.action not in allowed:
                raise VocabularyError(f"action {intent.action} is not in allowed_actions")
            try:
                reject_prescriptive_intent(intent)
            except PrescriptiveTextError as exc:
                raise VocabularyError(str(exc)) from exc
        return tuple(intents)


def _parse_enum(enum_cls: type, value: str, where: str):
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise VocabularyError(f"{where} {value!r} is not a frozen {enum_cls.__name__}") from exc
