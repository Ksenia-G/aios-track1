"""Запрет прескриптивных чисел и команд в тексте и evidence.

Frozen InjectionIntent не сканирует rationale. Специалист закрывает класс
утечек: количественная величина в NL-формулировке и eclipse/control token.
Evidence в каталоге -- только observational keys; всё остальное -- команда.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from aios.mas.intents import InjectionIntent

# Факты, которые агент может читать. Любой другой ключ -- потенциальный setpoint.
OBSERVATIONAL_EVIDENCE_KEYS = frozenset(
    {
        "note",
        "pressure",
        "status",
        "synthetic",
        "trend",
        "water_cut",
        "wbhp",
        "wlpr",
        "wopr",
        "wwct",
        "wwir",
        "wwit",
        "wwpr",
        "wwpt",
        "wopt",
    }
)

# Имена командных полей и eclipse-записей. Не наблюдения вроде WBHP.
FORBIDDEN_COMMAND_KEYS = frozenset(
    {
        "bhp",
        "injection_rate",
        "injector_control",
        "injector_rate",
        "liquid_rate",
        "lrat",
        "orat",
        "producer_control",
        "producer_rate",
        "rate",
        "setpoint",
        "setpoints",
        "surface_rate",
        "wconinje",
        "wconprod",
        "wrat",
    }
)

_COMMAND_TOKEN = re.compile(
    r"(?i)\b(?:wconinje|wconprod|surface_rate|injection_rate|liquid_rate|"
    r"injector_control|producer_control|setpoints?)\b"
)


class PrescriptiveTextError(ValueError):
    """Текст или payload несёт командный setpoint, а не наблюдение."""


def reject_prescriptive_text(text: str, *, where: str = "rationale_short") -> None:
    """Отклонить класс численных команд, не одну форму RATE=.

    Структурный rationale не содержит величин: цифры и eclipse-токены -- это
    setpoint, даже в формулировке 'set the donor to 240' или 'WCONINJE OPEN'.
    """
    if not text:
        return
    if re.search(r"\d", text):
        raise PrescriptiveTextError(f"{where} contains a numerical setpoint")
    if _COMMAND_TOKEN.search(text):
        raise PrescriptiveTextError(f"{where} contains a prescriptive control token")


def reject_prescriptive_intent(intent: InjectionIntent) -> None:
    reject_prescriptive_text(intent.rationale_short, where="rationale_short")


def first_prescriptive_payload_key(
    payload: Mapping[str, Any] | Sequence[Any] | Any,
) -> str | None:
    """Ключ вне observational allowlist или строка с setpoint внутри значения."""
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            folded = str(key).casefold()
            if folded in FORBIDDEN_COMMAND_KEYS or folded not in OBSERVATIONAL_EVIDENCE_KEYS:
                return folded
            if isinstance(value, str):
                try:
                    reject_prescriptive_text(value, where=folded)
                except PrescriptiveTextError:
                    return folded
            nested = first_prescriptive_payload_key(value)
            if nested is not None:
                return nested
        return None
    if isinstance(payload, list | tuple):
        for item in payload:
            nested = first_prescriptive_payload_key(item)
            if nested is not None:
                return nested
    return None
