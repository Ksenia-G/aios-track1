"""Каноническая сериализация и детерминированное хеширование.

Единственный источник правды по вычислению воспроизводимых ключей.
Любой другой модуль обязан хешировать только через этот модуль: две разные
канонизации означают два разных кеша и молчаливую потерю воспроизводимости.
"""

import hashlib
import json
import math
from collections.abc import Mapping

# Версия схемы ключа. Инкремент обязателен при любом изменении правил
# канонизации или состава ключа: старые ключи после этого невалидны.
KEY_SCHEMA_VERSION = 1

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


class CanonicalizationError(ValueError):
    """Значение нельзя канонизировать детерминированно."""


def _normalize(value: object, path: str) -> JsonValue:
    # bool проверяется до int: в Python bool -- подкласс int.
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            # NaN/Inf невыразимы в JSON и делают ключ невоспроизводимым.
            raise CanonicalizationError(f"{path}: non-finite float is not allowed")
        # -0.0 и 0.0 физически неразличимы, но дают разные строки JSON.
        return 0.0 if value == 0.0 else value
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"{path}: mapping key must be a string")
            normalized[key] = _normalize(item, f"{path}.{key}")
        return normalized
    if isinstance(value, list):
        return [_normalize(item, f"{path}[{index}]") for index, item in enumerate(value)]
    # Произвольные Python-объекты (в том числе tuple и set) отклоняются намеренно:
    # неявная сериализация прячет изменение смысла ключа за успешным хешем.
    raise CanonicalizationError(f"{path}: unsupported type {type(value).__name__}")


def normalize_json(value: object) -> JsonValue:
    """Проверить JSON-совместимость значения и нормализовать его."""
    return _normalize(value, "$")


def canonical_json(value: object) -> str:
    """Каноническое JSON-представление: одно значение -- ровно одна строка."""
    return json.dumps(
        normalize_json(value),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: object) -> str:
    """SHA-256 канонического представления значения (hex)."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_bytes(data: bytes) -> str:
    """SHA-256 сырых байтов (hex). Для содержимого артефактов и входных файлов."""
    return hashlib.sha256(data).hexdigest()
