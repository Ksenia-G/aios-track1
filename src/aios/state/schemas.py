"""StateCard v1.0 schema constants and validation helpers."""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

STATE_CARD_SCHEMA_VERSION = "1.0"

REALIZED_VECTOR_FIELDS = (
    "BHP",
    "WLPR",
    "WOPR",
    "WWPR",
    "WCT",
    "WOPT",
    "WWPT",
    "WWIT",
)


class StateCardError(ValueError):
    """StateCard payload is missing required state or cannot be trusted."""


def require_mapping(payload: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise StateCardError(f"{where}: expected object")
    return payload


def require_key(payload: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in payload:
        raise StateCardError(f"{where}.{key}: missing required field")
    return payload[key]


def optional_number(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StateCardError(f"{where}: expected number or null")
    number = float(value)
    if not isfinite(number):
        raise StateCardError(f"{where}: number must be finite")
    return number


def required_text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateCardError(f"{where}: expected non-empty string")
    return value.strip()


def required_date(value: Any, where: str) -> datetime.date:
    if not isinstance(value, str):
        raise StateCardError(f"{where}: expected ISO date string")
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise StateCardError(f"{where}: bad ISO date {value!r}") from exc


def text_tuple(value: Any, where: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise StateCardError(f"{where}: expected list of strings")
    return tuple(required_text(item, f"{where}[{index}]") for index, item in enumerate(value))


def number_mapping(value: Any, where: str) -> dict[str, float | None]:
    payload = require_mapping(value, where)
    return {
        required_text(key, where): optional_number(item, f"{where}.{key}")
        for key, item in payload.items()
    }


def text_mapping(value: Any, where: str) -> dict[str, str]:
    payload = require_mapping(value, where)
    return {
        required_text(key, where): required_text(item, f"{where}.{key}")
        for key, item in payload.items()
    }
