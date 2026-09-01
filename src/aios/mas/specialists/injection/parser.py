"""Строгий разбор LLM-вывода в InjectionIntent.

Только JSON-объект с ключом intents. Новые поля, setpoint-ключи и неизвестные
метки словаря отклоняют весь ответ. Частичный разбор запрещён.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from aios.mas.common import UncertaintyLevel
from aios.mas.intents import InjectionIntent, IntentError
from aios.mas.specialists.injection.prescriptive import (
    FORBIDDEN_COMMAND_KEYS,
    PrescriptiveTextError,
    reject_prescriptive_intent,
)
from aios.mas.specialists.injection.vocabulary import VocabularyError, VocabularyValidator

_ALLOWED_TOP_LEVEL = frozenset({"intents", "hypotheses", "considered_actions"})

#: Ключи записи «рассмотрено, но не поддержано». Численных величин здесь нет:
#: это структурная видимость выбора, а не управление.
_ALLOWED_CONSIDERED_KEYS = frozenset({"action", "supported", "reason"})
_ALLOWED_INTENT_KEYS = frozenset(
    {
        "action",
        "direction",
        "donor_wells",
        "evidence_ids",
        "intensity",
        "rationale_short",
        "recipient_wells",
        "source_regions",
        "target_regions",
        "uncertainty",
    }
)
_FORBIDDEN_KEYS = FORBIDDEN_COMMAND_KEYS


class ParseError(ValueError):
    """LLM-ответ нельзя превратить в валидные InjectionIntent."""


class StrictOutputParser:
    """Разобрать JSON-ответ. All-or-nothing, без setpoint-полей."""

    def parse(self, text: str) -> tuple[InjectionIntent, ...]:
        """Первая (rank-1) гипотеза. Прежний контракт вызывающих не меняется."""
        beam = self.parse_beam(text)
        return beam.hypotheses[0]

    def parse_beam(self, text: str) -> InjectionBeam:
        """Пучок структурных гипотез одного ответа провайдера.

        Прежняя форма ответа (`{"intents": [...]}`) остаётся валидной и даёт
        пучок из одной гипотезы: контракт расширяется, а не заменяется.
        """
        payload = _load_object(text)
        extra = set(payload) - _ALLOWED_TOP_LEVEL
        if extra:
            raise ParseError(f"unexpected top-level keys {sorted(extra)}")
        validator = VocabularyValidator()

        if "hypotheses" in payload:
            raw_beam = payload["hypotheses"]
            if not isinstance(raw_beam, list) or not raw_beam:
                raise ParseError("hypotheses must be a non-empty list")
            # Негодная ОТДЕЛЬНАЯ гипотеза отбрасывается поодиночке: она не
            # имеет права уносить с собой годных соседей -- ради этого пучок
            # и вводился. Ответ целиком, не давший ни одной годной гипотезы,
            # остаётся fail-closed.
            parsed_beam: list[tuple[InjectionIntent, ...]] = []
            first_error: ParseError | None = None
            for rank, item in enumerate(raw_beam):
                try:
                    parsed_beam.append(_parse_intents(item, f"hypotheses[{rank}].", validator))
                except ParseError as error:
                    first_error = first_error or error
            if not parsed_beam:
                raise first_error or ParseError("no hypothesis in the beam is parsable")
            hypotheses = tuple(parsed_beam)
        else:
            hypotheses = (_parse_intents(payload, "", validator),)

        return InjectionBeam(
            hypotheses=hypotheses,
            considered=_parse_considered(payload.get("considered_actions")),
        )


def _load_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped.startswith("{"):
        raise ParseError("response must be a JSON object")
    try:
        payload = json.loads(
            stripped,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except ParseError:
        raise
    except json.JSONDecodeError as exc:
        raise ParseError("response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ParseError("response must be a JSON object")
    offender = _first_forbidden_key(payload)
    if offender is not None:
        raise ParseError(f"prescriptive key {offender!r} is forbidden")
    return payload


@dataclass(frozen=True, slots=True)
class ConsideredAction:
    """Действие, которое модель рассмотрела явно."""

    action: str
    supported: bool
    reason: str = ""

    def label(self) -> str:
        """Короткая auditable метка для ProposalAudit.declined_alternatives."""
        state = "supported" if self.supported else "not_supported"
        return (
            f"{self.action}:{state}:{self.reason[:80]}"
            if self.reason
            else (f"{self.action}:{state}")
        )


@dataclass(frozen=True, slots=True)
class InjectionBeam:
    """Структурные гипотезы одного ответа провайдера, в порядке ранга."""

    hypotheses: tuple[tuple[InjectionIntent, ...], ...]
    considered: tuple[ConsideredAction, ...] = ()


def _parse_intents(container, where: str, validator) -> tuple[InjectionIntent, ...]:
    if not isinstance(container, dict):
        raise ParseError(f"{where or 'response'} must be an object")
    raw_intents = container.get("intents")
    if not isinstance(raw_intents, list) or not raw_intents:
        raise ParseError(f"{where or 'response'}.intents must be a non-empty list")
    parsed: list[InjectionIntent] = []
    for index, item in enumerate(raw_intents):
        if not isinstance(item, dict):
            raise ParseError(f"{where}intents[{index}] must be an object")
        parsed.append(_parse_intent(item, index, validator))
    return tuple(parsed)


def _parse_considered(raw) -> tuple[ConsideredAction, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ParseError("considered_actions must be a list")
    items: list[ConsideredAction] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ParseError(f"considered_actions[{index}] must be an object")
        unexpected = set(entry) - _ALLOWED_CONSIDERED_KEYS
        if unexpected:
            raise ParseError(f"considered_actions[{index}] unexpected keys {sorted(unexpected)}")
        action = entry.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ParseError(f"considered_actions[{index}].action must be a non-empty string")
        supported = entry.get("supported")
        if not isinstance(supported, bool):
            raise ParseError(f"considered_actions[{index}].supported must be a boolean")
        reason = entry.get("reason") or ""
        if not isinstance(reason, str):
            raise ParseError(f"considered_actions[{index}].reason must be a string")
        items.append(ConsideredAction(action=action, supported=supported, reason=reason))
    return tuple(items)


def _parse_intent(
    item: Mapping[str, Any],
    index: int,
    validator: VocabularyValidator,
) -> InjectionIntent:
    extra = set(item) - _ALLOWED_INTENT_KEYS
    if extra:
        raise ParseError(f"intents[{index}] unexpected keys {sorted(extra)}")
    missing = _ALLOWED_INTENT_KEYS - set(item)
    if missing:
        raise ParseError(f"intents[{index}] missing keys {sorted(missing)}")
    try:
        action, direction, intensity = validator.validate_labels(
            _require_str(item["action"], f"intents[{index}].action"),
            _require_str(item["direction"], f"intents[{index}].direction"),
            _require_str(item["intensity"], f"intents[{index}].intensity"),
        )
        uncertainty = UncertaintyLevel(
            _require_str(item["uncertainty"], f"intents[{index}].uncertainty")
        )
    except (VocabularyError, ValueError) as exc:
        raise ParseError(f"intents[{index}] has an invalid vocabulary label") from exc
    try:
        intent = InjectionIntent(
            action=action,
            direction=direction,
            intensity=intensity,
            donor_wells=_string_tuple(item["donor_wells"], f"intents[{index}].donor_wells"),
            recipient_wells=_string_tuple(
                item["recipient_wells"], f"intents[{index}].recipient_wells"
            ),
            source_regions=_string_tuple(
                item["source_regions"], f"intents[{index}].source_regions"
            ),
            target_regions=_string_tuple(
                item["target_regions"], f"intents[{index}].target_regions"
            ),
            evidence_ids=_string_tuple(item["evidence_ids"], f"intents[{index}].evidence_ids"),
            uncertainty=uncertainty,
            rationale_short=_require_str(
                item["rationale_short"], f"intents[{index}].rationale_short", allow_empty=True
            ),
        )
        reject_prescriptive_intent(intent)
        return intent
    except (IntentError, ParseError, PrescriptiveTextError) as exc:
        raise ParseError(f"intents[{index}] is not a valid InjectionIntent") from exc


def _require_str(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ParseError(f"{where} must be a string")
    if not allow_empty and not value.strip():
        raise ParseError(f"{where} must be a non-empty string")
    return value


def _string_tuple(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ParseError(f"{where} must be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ParseError(f"{where} must be a list of non-empty strings")
        items.append(item)
    return tuple(items)


def _first_forbidden_key(
    payload: Mapping[str, Any] | Sequence[Any] | Any,
) -> str | None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            folded = str(key).casefold()
            if folded in _FORBIDDEN_KEYS:
                return folded
            nested = _first_forbidden_key(value)
            if nested is not None:
                return nested
        return None
    if isinstance(payload, list | tuple):
        for item in payload:
            nested = _first_forbidden_key(item)
            if nested is not None:
                return nested
    return None


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Дубль ключа -- не «последний выигрывает», а отказ.

    Молчаливое last-value-wins позволяет спрятать второе значение того же
    поля от любого, кто читает ответ модели глазами.
    """
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ParseError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> None:
    """NaN/Infinity невыразимы в каноническом JSON и запрещены явно."""
    raise ParseError(f"non-finite JSON constant {value!r} is forbidden")
