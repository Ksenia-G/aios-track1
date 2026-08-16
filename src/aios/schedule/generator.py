"""Генератор `wells_schedule.inc`.

Файл собирается только отсюда. Ручная правка после генерации запрещена не
по стилю, а по смыслу: сабмишен сопровождается SHA-256, и любая правка мимо
генератора делает манифест ложью.

Детерминизм обеспечивают три правила: шаги идут по возрастанию даты, скважины
внутри шага -- по натуральному ключу, числа печатаются одной функцией. Ни
времени генерации, ни путей, ни версий окружения в файле нет, поэтому
одинаковый вход всегда даёт одинаковый SHA.
"""

import hashlib
from datetime import date

from aios.cases.contract import Case
from aios.schedule.deck import format_deck_date
from aios.schedule.events import (
    DEFAULTED,
    InjectorControl,
    ProducerControl,
    Schedule,
    ScheduleStep,
)

GENERATOR_ID = "aios.schedule.generator"
GENERATOR_VERSION = "1.0.0"

_PRODUCER_HEADER = "-- well status control ORAT WRAT GRAT LRAT RESV BHP THP VFP"
_INJECTOR_HEADER = "-- well phase status control RATE RESV BHP THP VFP"


class ScheduleRenderError(ValueError):
    """Расписание нельзя записать в колоду."""


def format_number(value: float | None) -> str:
    """Печать числа в колоду. `None` -> умолчание `1*`.

    Целые печатаются без дробной части: так короче и совпадает с тем, как
    записана исходная колода. Остальное -- `%.10g`, как в лабораторном
    экспорте, чтобы не плодить второй формат чисел в проекте.
    """

    if value is None:
        return DEFAULTED
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}"


def _producer_line(control: ProducerControl) -> str:
    fields = [
        f"'{control.well}'",
        f"'{control.status}'",
        f"'{control.mode}'",
        DEFAULTED,
        DEFAULTED,
        DEFAULTED,
        format_number(control.liquid_rate),
        DEFAULTED,
        format_number(control.bhp_limit),
        DEFAULTED,
        DEFAULTED,
    ]
    return " " + " ".join(fields) + " /"


def _injector_line(control: InjectorControl) -> str:
    fields = [
        f"'{control.well}'",
        f"'{control.phase}'",
        f"'{control.status}'",
        f"'{control.mode}'",
        format_number(control.surface_rate),
        DEFAULTED,
        format_number(control.bhp_limit),
        DEFAULTED,
        DEFAULTED,
    ]
    return " " + " ".join(fields) + " /"


def _dates_block(day: date) -> list[str]:
    return ["DATES", f" {format_deck_date(day)} /", "/"]


def _step_lines(step: ScheduleStep) -> list[str]:
    lines = _dates_block(step.date)
    if step.producers:
        lines.append("WCONPROD")
        lines.append(_PRODUCER_HEADER)
        lines.extend(_producer_line(control) for control in step.producers)
        lines.append("/")
    if step.injectors:
        lines.append("WCONINJE")
        lines.append(_INJECTOR_HEADER)
        lines.extend(_injector_line(control) for control in step.injectors)
        lines.append("/")
    return lines


def render_schedule(schedule: Schedule, case: Case, *, policy_id: str) -> str:
    """Собрать текст `wells_schedule.inc`."""

    canonical = schedule.canonical()
    if not canonical.steps:
        raise ScheduleRenderError("schedule has no steps")
    if canonical.steps[0].date != case.management_start_date:
        raise ScheduleRenderError(
            f"schedule starts at {canonical.steps[0].date.isoformat()}, "
            f"case management window starts at {case.management_start_date.isoformat()}"
        )
    if canonical.steps[-1].date != case.management_end_date:
        raise ScheduleRenderError(
            f"schedule ends at {canonical.steps[-1].date.isoformat()}, "
            f"case management window ends at {case.management_end_date.isoformat()}"
        )

    lines = [
        "-- AIOS Track 1 -- generated well control schedule.",
        f"-- generator: {GENERATOR_ID} {GENERATOR_VERSION}",
        f"-- case: {case.case_id}",
        f"-- policy: {policy_id}",
        (
            f"-- management window: {case.management_start_date.isoformat()}"
            f" .. {case.management_end_date.isoformat()}"
        ),
        "-- Generated file. Do not edit by hand: the submission manifest carries its SHA-256.",
    ]
    for step in canonical.steps:
        lines.extend(_step_lines(step))
    return "\n".join(lines) + "\n"


def schedule_sha256(text: str) -> str:
    """SHA-256 сгенерированного файла ровно в том виде, в каком он ложится на диск."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
