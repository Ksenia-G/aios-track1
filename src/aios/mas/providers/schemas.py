"""JSON-схемы структурированного вывода специалистов.

Схемы ВЫВОДЯТСЯ из тех же констант, по которым работает строгий парсер:
списка допустимых ключей intent и уже замороженных enum. Написанная руками
вторая копия схемы разошлась бы с парсером при первом же изменении словаря, и
расхождение обнаружилось бы на живом вызове, а не в тестах.

Схема -- это ограничение генерации, а не разрешение доверять модели. Ответ всё
равно проходит существующий строгий парсер, проверку словаря и границу
NO SETPOINTS CROSS: ни одно из этих правил здесь не дублируется и не
ослабляется.
"""

from __future__ import annotations

from collections.abc import Mapping

from aios.mas.common import Direction, Intensity, UncertaintyLevel
from aios.mas.intents import InjectionAction, ProductionAction
from aios.mas.specialists.injection.parser import (
    _ALLOWED_INTENT_KEYS as _INJECTION_INTENT_KEYS,
)
from aios.mas.specialists.injection.parser import (
    _ALLOWED_TOP_LEVEL as _INJECTION_TOP_LEVEL,
)
from aios.mas.specialists.production.parser import _INTENT_KEYS as _PRODUCTION_INTENT_KEYS
from aios.mas.specialists.production.parser import _TOP_LEVEL_KEYS as _PRODUCTION_TOP_LEVEL

#: Поля, которых в структурной схеме быть не может ни при каких условиях.
#: Проверяется тестом: схема -- это ещё один канал, по которому численное
#: управление могло бы попроситься наружу раньше MagnitudeResolver.
FORBIDDEN_SCHEMA_FIELDS = frozenset(
    {
        "rate",
        "rates",
        "bhp",
        "bhp_limit",
        "lrat",
        "orat",
        "wrat",
        "grat",
        "setpoint",
        "setpoints",
        "target_rate",
        "surface_rate",
        "liquid_rate",
        "injection_rate",
        "producer_control",
        "injector_control",
        "magnitude",
        "value",
    }
)

#: Поля-списки идентификаторов. Скважины и регионы -- ссылки, а не величины.
_IDENTIFIER_LIST_FIELDS = frozenset(
    {
        "target_wells",
        "target_regions",
        "donor_wells",
        "recipient_wells",
        "source_regions",
        "evidence_ids",
    }
)


def _enum_values(enum_type: type) -> list[str]:
    return [str(member.value) for member in enum_type]


def _intent_schema(intent_keys: frozenset[str], action_enum: type) -> dict[str, object]:
    """Схема одного intent, собранная из допустимых ключей парсера."""
    properties: dict[str, object] = {}
    for key in sorted(intent_keys):
        if key == "action":
            properties[key] = {"type": "string", "enum": _enum_values(action_enum)}
        elif key == "direction":
            properties[key] = {"type": "string", "enum": _enum_values(Direction)}
        elif key == "intensity":
            properties[key] = {"type": "string", "enum": _enum_values(Intensity)}
        elif key == "uncertainty":
            properties[key] = {"type": "string", "enum": _enum_values(UncertaintyLevel)}
        elif key in _IDENTIFIER_LIST_FIELDS:
            properties[key] = {"type": "array", "items": {"type": "string"}}
        elif key == "rationale_short":
            # Короткое качественное объяснение. Числовые величины отсюда
            # по-прежнему выкидывает граница специалиста, а не схема.
            properties[key] = {"type": "string"}
        else:  # pragma: no cover - защита от молчаливого расширения контракта
            raise ValueError(
                f"schema derivation: unknown intent field {key!r}; the schema must be "
                "extended deliberately, never inferred"
            )
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(intent_keys),
        "additionalProperties": False,
    }


def _envelope_schema(
    top_level: frozenset[str], intent_keys: frozenset[str], action_enum: type
) -> dict[str, object]:
    if top_level != {"intents"}:  # pragma: no cover - контракт заморожен
        raise ValueError(f"schema derivation: unexpected top-level keys {sorted(top_level)}")
    return {
        "type": "object",
        "properties": {
            "intents": {
                "type": "array",
                "items": _intent_schema(intent_keys, action_enum),
            }
        },
        "required": ["intents"],
        "additionalProperties": False,
    }


def production_response_schema() -> dict[str, object]:
    """Схема вывода Production, выведенная из его же строгого парсера."""
    return _envelope_schema(
        frozenset(_PRODUCTION_TOP_LEVEL), frozenset(_PRODUCTION_INTENT_KEYS), ProductionAction
    )


def injection_response_schema() -> dict[str, object]:
    """Схема вывода Injection, выведенная из его же строгого парсера."""
    return _envelope_schema(
        frozenset(_INJECTION_TOP_LEVEL), frozenset(_INJECTION_INTENT_KEYS), InjectionAction
    )


def schema_field_names(schema: Mapping[str, object]) -> set[str]:
    """Все имена полей схемы -- для проверки, что запрещённых среди них нет."""
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, Mapping):
            properties = node.get("properties")
            if isinstance(properties, Mapping):
                found.update(str(key) for key in properties)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return found


__all__ = [
    "FORBIDDEN_SCHEMA_FIELDS",
    "injection_response_schema",
    "production_response_schema",
    "schema_field_names",
]
