"""Strict, non-repairing parser for untrusted Production LLM output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from aios.mas.specialists.authority import contains_numeric_magnitude

from .errors import StrictOutputError

_TOP_LEVEL_KEYS = {"intents"}
_INTENT_KEYS = {
    "action",
    "direction",
    "intensity",
    "target_wells",
    "target_regions",
    "evidence_ids",
    "uncertainty",
    "rationale_short",
}


@dataclass(frozen=True, slots=True)
class ParsedProductionIntent:
    action: str
    direction: str
    intensity: str
    target_wells: tuple[str, ...]
    target_regions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    uncertainty: str
    rationale_short: str


@dataclass(frozen=True, slots=True)
class StrictOutputParser:
    """Accept exactly one JSON object and never salvage partial intents."""

    def parse(self, response_text: str) -> tuple[ParsedProductionIntent, ...]:
        try:
            payload = json.loads(
                response_text,
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_non_finite_constant,
            )
        except StrictOutputError:
            raise
        except (TypeError, json.JSONDecodeError) as exc:
            raise StrictOutputError("response must be one valid JSON object") from exc
        if not isinstance(payload, dict):
            raise StrictOutputError("response root must be an object")
        if set(payload) != _TOP_LEVEL_KEYS:
            raise StrictOutputError("response root must contain only 'intents'")
        raw_intents = payload["intents"]
        if not isinstance(raw_intents, list):
            raise StrictOutputError("intents must be a list")

        parsed: list[ParsedProductionIntent] = []
        for index, raw in enumerate(raw_intents):
            where = f"intents[{index}]"
            if not isinstance(raw, dict):
                raise StrictOutputError(f"{where} must be an object")
            if set(raw) != _INTENT_KEYS:
                raise StrictOutputError(f"{where} has missing or unknown fields")
            rationale = _required_string(raw["rationale_short"], f"{where}.rationale_short")
            if raw["action"] == "HOLD" and rationale != "":
                raise StrictOutputError(f"{where}.HOLD rationale_short must be empty")
            if contains_numeric_magnitude(rationale):
                raise StrictOutputError(
                    f"{where}.rationale_short must cite numerical observations by evidence_id"
                )
            parsed.append(
                ParsedProductionIntent(
                    action=_required_string(raw["action"], f"{where}.action"),
                    direction=_required_string(raw["direction"], f"{where}.direction"),
                    intensity=_required_string(raw["intensity"], f"{where}.intensity"),
                    target_wells=_string_tuple(raw["target_wells"], f"{where}.target_wells"),
                    target_regions=_string_tuple(raw["target_regions"], f"{where}.target_regions"),
                    evidence_ids=_string_tuple(raw["evidence_ids"], f"{where}.evidence_ids"),
                    uncertainty=_required_string(raw["uncertainty"], f"{where}.uncertainty"),
                    rationale_short=rationale,
                )
            )
        return tuple(parsed)


def _required_string(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise StrictOutputError(f"{where} must be a string")
    if where.endswith("rationale_short"):
        return value
    if not value.strip():
        raise StrictOutputError(f"{where} must be non-empty")
    return value


def _string_tuple(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise StrictOutputError(f"{where} must be a list of strings")
    result = tuple(_required_string(item, f"{where}[{index}]") for index, item in enumerate(value))
    if len(result) != len(set(result)):
        raise StrictOutputError(f"{where} must not contain duplicates")
    return result


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictOutputError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> None:
    raise StrictOutputError(f"non-finite JSON constant {value!r} is forbidden")
