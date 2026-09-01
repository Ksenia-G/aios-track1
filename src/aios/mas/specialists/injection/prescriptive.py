"""Граница prescriptive control для Injection Controller.

Правила живут в общем модуле `aios.mas.specialists.authority`: Production и
Injection обязаны понимать NO SETPOINTS CROSS одинаково. Здесь остаются только
имена, специфичные для Injection, и адаптация ошибок под контракт модуля.

Две прежние ошибки этой границы исправлены именно здесь:

* запрет "любой цифры" отклонял SYN-I1, I10, F3, BLOCK-12 -- то есть любой
  реальный идентификатор в системе. Запрещена величина, а не цифра;
* закрытый allowlist наблюдательных ключей отклонял реальные
  PressureDiagnostic/SweepDiagnostic целиком, потому что их схема
  (metadata/proposal_type/target/reason/confidence/risk_flags) в него не
  входила. Мир открыт: незнакомый ключ -- ещё не команда.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from aios.mas.intents import InjectionIntent
from aios.mas.specialists.authority import (
    PrescriptiveAuthorityError,
    find_prescriptive_control,
)
from aios.mas.specialists.authority import (
    reject_prescriptive_text as _reject_text,
)

# Имена, которые сам Injection-парсер не принимает как поля intent.
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


class PrescriptiveTextError(ValueError):
    """Текст или payload несёт командный setpoint, а не наблюдение."""


def reject_prescriptive_text(text: str, *, where: str = "rationale_short") -> None:
    """Отклонить численную команду, сохранив идентификаторы с цифрами.

    "Move support toward SYN-I1." -- наблюдение и остаётся разрешённым.
    "Inject 240 m3/day into SYN-I1." -- величина управления и отклоняется.
    """
    try:
        _reject_text(text, where=where)
    except PrescriptiveAuthorityError as exc:
        raise PrescriptiveTextError(str(exc)) from exc


def reject_prescriptive_intent(intent: InjectionIntent) -> None:
    reject_prescriptive_text(intent.rationale_short, where="rationale_short")


def first_prescriptive_payload_key(
    payload: Mapping[str, Any] | Sequence[Any] | Any,
) -> str | None:
    """Путь до prescriptive control в payload, либо None.

    Наблюдательные числа (WBHP, WWIR, confidence, тренды) -- факты и проходят.
    """
    return find_prescriptive_control(payload)
