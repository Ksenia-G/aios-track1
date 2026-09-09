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
from aios.mas.intents import (
    INJECTION_LIST_FIELDS,
    InjectionAction,
    InjectionFieldContract,
    ProductionAction,
    injection_field_contract,
)
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


def _injection_intent_schema(intent_keys: frozenset[str]) -> dict[str, object]:
    """Схема одного Injection-intent как разбор по действию.

    Одного широкого объекта, в котором все поля независимо необязательны,
    здесь мало: он объявлял допустимым то, что домен отвергает, и провайдер,
    честно соблюдавший схему, получал `intents[0] is not a valid
    InjectionIntent`. Ветви выводятся из INJECTION_ACTION_CONTRACT -- той же
    таблицы, по которой проверяет себя `InjectionIntent.__post_init__`.

    Схема остаётся НЕ строже домена: поле, которое действие оставляет
    свободным, остаётся свободным и здесь. Строгая схема запретила бы
    провайдеру комбинацию, которую система на самом деле принимает, -- это
    та же ошибка, только с другой стороны.
    """
    unexpected = intent_keys - {
        "action",
        "direction",
        "intensity",
        "uncertainty",
        "rationale_short",
        *INJECTION_LIST_FIELDS,
    }
    if unexpected:  # pragma: no cover - защита от молчаливого расширения контракта
        raise ValueError(
            f"schema derivation: unknown injection intent field(s) {sorted(unexpected)}; "
            "the action contract must be extended deliberately, never inferred"
        )
    branches: list[dict[str, object]] = []
    for action in InjectionAction:
        contract = injection_field_contract(action)
        for choice in _either_choices(contract):
            branches.append(_injection_branch(intent_keys, action, contract, choice))
    return {"anyOf": branches}


def _either_choices(contract: InjectionFieldContract) -> list[frozenset[str]]:
    """Каждый способ удовлетворить правила вида «одно из полей непусто».

    JSON Schema не умеет сказать «хотя бы один из двух списков непуст» внутри
    одного объекта, зато умеет перечислить варианты: `anyOf` из ветвей, в
    каждой из которых непустым объявлен свой список.
    """
    choices: list[frozenset[str]] = [frozenset()]
    for group in contract.either_lists:
        choices = [choice | {field} for choice in choices for field in group]
    return choices


def _injection_branch(
    intent_keys: frozenset[str],
    action: InjectionAction,
    contract: InjectionFieldContract,
    non_empty: frozenset[str],
) -> dict[str, object]:
    required_lists = frozenset(contract.required_lists) | non_empty
    empty_lists = frozenset(contract.empty_lists)
    properties: dict[str, object] = {}
    for key in sorted(intent_keys):
        if key == "action":
            properties[key] = {"type": "string", "enum": [str(action)]}
        elif key == "direction":
            properties[key] = {
                "type": "string",
                "enum": [str(item) for item in contract.directions],
            }
        elif key == "intensity":
            properties[key] = {
                "type": "string",
                "enum": [str(item) for item in contract.intensities],
            }
        elif key == "uncertainty":
            properties[key] = {"type": "string", "enum": _enum_values(UncertaintyLevel)}
        elif key in INJECTION_LIST_FIELDS:
            field: dict[str, object] = {"type": "array", "items": {"type": "string"}}
            if key in empty_lists:
                field["maxItems"] = 0
            elif key in required_lists:
                field["minItems"] = 1
            properties[key] = field
        elif key == "rationale_short":
            # Длина и число предложений -- текстовое качество, которого strict
            # structured output не исполняет: они остаются доменной проверкой.
            properties[key] = {"type": "string"}
        else:  # pragma: no cover - недостижимо после проверки intent_keys выше
            raise ValueError(f"schema derivation: unknown intent field {key!r}")
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(intent_keys),
        "additionalProperties": False,
    }


def _envelope_schema(
    top_level: frozenset[str], intent_object: dict[str, object], action_enum: type
) -> dict[str, object]:
    """Схема ответа, выведенная из тех же ключей, что принимает парсер.

    Форма с одним набором intents остаётся валидной. Пучок структурных
    гипотез -- это массив таких же наборов: у него нет ни одного нового
    поля намерения, поэтому запрет численных величин распространяется на
    него автоматически, а не отдельным правилом.
    """
    intents_array = {"type": "array", "items": intent_object}
    properties: dict[str, object] = {"intents": intents_array}
    required = ["intents"]

    if "hypotheses" in top_level:
        properties["hypotheses"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"intents": intents_array},
                "required": ["intents"],
                "additionalProperties": False,
            },
        }
        required = ["hypotheses"]
        properties.pop("intents")
    if "considered_alternatives" in top_level:
        # Отдельная коллекция, а не флаг внутри hypotheses: структурный
        # контрфакт, который специалист рассмотрел и НЕ рекомендует, не должен
        # иметь возможности случайно попасть в ранжирование предпочтений.
        properties["considered_alternatives"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"intents": intents_array},
                "required": ["intents"],
                "additionalProperties": False,
            },
        }
        required.append("considered_alternatives")
    if "coverage_declarations" in top_level:
        # Слой полноты: на каждый заданный структурный вопрос ровно один ответ.
        # Ни одного численного поля здесь нет -- только действие, класс охвата
        # и распоряжение.
        properties["coverage_declarations"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": _enum_values(action_enum)},
                    "scope_class": {"type": "string", "enum": ["SELECTIVE", "BROAD"]},
                    "disposition": {
                        "type": "string",
                        "enum": ["PREFERRED", "CONSIDERED_NOT_PREFERRED", "DECLINED"],
                    },
                    "decline_reason": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": [
                    "action",
                    "scope_class",
                    "disposition",
                    "decline_reason",
                    "explanation",
                ],
                "additionalProperties": False,
            },
        }
        required.append("coverage_declarations")
    if "considered_actions" in top_level:
        properties["considered_actions"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": _enum_values(action_enum)},
                    "supported": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["action", "supported", "reason"],
                "additionalProperties": False,
            },
        }
        required.append("considered_actions")

    unknown = top_level - {
        "intents",
        "hypotheses",
        "considered_alternatives",
        "coverage_declarations",
        "considered_actions",
    }
    if unknown:  # pragma: no cover - защита от молчаливого расширения
        raise ValueError(f"schema derivation: unexpected top-level keys {sorted(unknown)}")
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(required),
        "additionalProperties": False,
    }


def production_response_schema() -> dict[str, object]:
    """Схема вывода Production, выведенная из его же строгого парсера."""
    return _envelope_schema(
        frozenset(_PRODUCTION_TOP_LEVEL),
        _intent_schema(frozenset(_PRODUCTION_INTENT_KEYS), ProductionAction),
        ProductionAction,
    )


def injection_response_schema() -> dict[str, object]:
    """Схема вывода Injection, выведенная из его же строгого парсера."""
    return _envelope_schema(
        frozenset(_INJECTION_TOP_LEVEL),
        _injection_intent_schema(frozenset(_INJECTION_INTENT_KEYS)),
        InjectionAction,
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
