"""Минимальная проверка payload против объявленной схемы структурного вывода.

В зависимостях проекта нет валидатора JSON Schema, и тянуть его сюда ради
одного контракта незачем: провайдерская схема намеренно живёт в узком strict
подмножестве, и это подмножество проверяется целиком.

Модуль отвечает ровно на один вопрос: приняла бы объявленная схема этот
ответ. Утверждение «схема принимает» без такой проверки невыразимо, а именно
оно и есть предмет контракта: всё, что схема приняла, обязано собираться в
типизированный intent.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Ключевые слова, которые strict structured output OpenAI-совместимого
#: транспорта действительно исполняет. Схема, вышедшая за этот список, была бы
#: строгой только у нас и молча свободной у провайдера -- то есть ровно тем
#: расхождением, ради закрытия которого этот контракт и вводится.
SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "anyOf",
        "description",
        "enum",
        "items",
        "maxItems",
        "minItems",
        "properties",
        "required",
        "type",
    }
)


class SchemaViolation(AssertionError):
    """Payload не проходит объявленную схему."""


def schema_accepts(instance: object, schema: Mapping[str, object]) -> bool:
    """True, если объявленная схема приняла бы такой ответ."""
    try:
        assert_valid(instance, schema)
    except SchemaViolation:
        return False
    return True


def assert_valid(instance: object, schema: Mapping[str, object], where: str = "$") -> None:
    """Проверить payload; при отказе поднять SchemaViolation с местом отказа."""
    if "anyOf" in schema:
        branches = schema["anyOf"]
        assert isinstance(branches, list)
        reasons: list[str] = []
        for index, branch in enumerate(branches):
            try:
                assert_valid(instance, branch, where)
            except SchemaViolation as exc:
                reasons.append(f"anyOf[{index}]: {exc}")
            else:
                return
        raise SchemaViolation(f"{where}: no anyOf branch matches ({'; '.join(reasons)})")

    declared = schema.get("type")
    if declared == "object":
        _assert_object(instance, schema, where)
    elif declared == "array":
        _assert_array(instance, schema, where)
    elif declared == "string" and not isinstance(instance, str):
        raise SchemaViolation(f"{where}: expected a string")
    elif declared == "boolean" and not isinstance(instance, bool):
        raise SchemaViolation(f"{where}: expected a boolean")

    allowed = schema.get("enum")
    if allowed is not None and instance not in allowed:
        raise SchemaViolation(f"{where}: {instance!r} is outside enum {allowed}")


def _assert_object(instance: object, schema: Mapping[str, object], where: str) -> None:
    if not isinstance(instance, Mapping):
        raise SchemaViolation(f"{where}: expected an object")
    properties = schema.get("properties") or {}
    assert isinstance(properties, Mapping)
    for key in schema.get("required") or ():
        if key not in instance:
            raise SchemaViolation(f"{where}: required property {key!r} is missing")
    if schema.get("additionalProperties") is False:
        extra = sorted(set(instance) - set(properties))
        if extra:
            raise SchemaViolation(f"{where}: additional properties {extra}")
    for key, value in instance.items():
        child = properties.get(key)
        if isinstance(child, Mapping):
            assert_valid(value, child, f"{where}.{key}")


def _assert_array(instance: object, schema: Mapping[str, object], where: str) -> None:
    if not isinstance(instance, list):
        raise SchemaViolation(f"{where}: expected an array")
    minimum = schema.get("minItems")
    if isinstance(minimum, int) and len(instance) < minimum:
        raise SchemaViolation(f"{where}: needs at least {minimum} item(s), got {len(instance)}")
    maximum = schema.get("maxItems")
    if isinstance(maximum, int) and len(instance) > maximum:
        raise SchemaViolation(f"{where}: allows at most {maximum} item(s), got {len(instance)}")
    item_schema = schema.get("items")
    if isinstance(item_schema, Mapping):
        for index, item in enumerate(instance):
            assert_valid(item, item_schema, f"{where}[{index}]")


def schema_keywords(schema: object) -> set[str]:
    """Все ключевые слова схемы -- для проверки совместимости с транспортом."""
    found: set[str] = set()

    def walk(node: object, inside_properties: bool = False) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                if not inside_properties:
                    found.add(str(key))
                walk(value, inside_properties=(not inside_properties and key == "properties"))
        elif isinstance(node, list):
            for item in node:
                walk(item, inside_properties=False)

    walk(schema)
    return found


__all__ = [
    "SUPPORTED_SCHEMA_KEYWORDS",
    "SchemaViolation",
    "assert_valid",
    "schema_accepts",
    "schema_keywords",
]
