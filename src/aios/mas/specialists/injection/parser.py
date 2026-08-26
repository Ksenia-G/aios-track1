"""Строгий разбор LLM-вывода в InjectionIntent.

Только JSON-объект с ключом intents. Новые поля, setpoint-ключи и неизвестные
метки словаря отклоняют весь ответ. Частичный разбор запрещён.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from aios.mas.common import UncertaintyLevel
from aios.mas.intents import InjectionIntent, IntentError
from aios.mas.specialists.injection.prescriptive import (
    FORBIDDEN_COMMAND_KEYS,
    PrescriptiveTextError,
    reject_prescriptive_intent,
)
from aios.mas.specialists.injection.vocabulary import VocabularyError, VocabularyValidator

_ALLOWED_TOP_LEVEL = frozenset({"intents"})
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
        payload = _load_object(text)
        extra = set(payload) - _ALLOWED_TOP_LEVEL
        if extra:
            raise ParseError(f"unexpected top-level keys {sorted(extra)}")
        raw_intents = payload.get("intents")
        if not isinstance(raw_intents, list) or not raw_intents:
            raise ParseError("intents must be a non-empty list")
        validator = VocabularyValidator()
        parsed: list[InjectionIntent] = []
        for index, item in enumerate(raw_intents):
            if not isinstance(item, dict):
                raise ParseError(f"intents[{index}] must be an object")
            parsed.append(_parse_intent(item, index, validator))
        return tuple(parsed)


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
