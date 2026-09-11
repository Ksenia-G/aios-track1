"""Разметка шагов процесса для графа на странице запуска.

Процесс, запущенный страницей, печатает рядом с обычным выводом служебные
строки `::aios {...}`: какой узел графа начал работу, закончил или упал и что
он сообщил. Страница по ним подсвечивает граф; всё остальное показывает как
есть.

Строки печатаются, только если выставлена `AIOS_UI_EVENTS=1` -- её ставит
сервер страницы своим процессам. В терминале вывод остаётся прежним. На
вычисления разметка не влияет: она только сообщает о вызовах, которые и так
происходят.
"""

from __future__ import annotations

import json
import os

EVENTS_ENV = "AIOS_UI_EVENTS"
PREFIX = "::aios "


def events_enabled() -> bool:
    return os.environ.get(EVENTS_ENV) == "1"


def emit_event(
    node: str | None = None,
    state: str | None = None,
    *,
    text: str | None = None,
    **extra: object,
) -> None:
    """Напечатать отметку для графа, если процесс запустила страница."""
    if not events_enabled():
        return
    payload = {
        key: value
        for key, value in {"node": node, "state": state, "text": text, **extra}.items()
        if value is not None
    }
    print(PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)
