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


def _control_mode_field(mode: str, value: float | None) -> str:
    """Поле режима WCONPROD/WCONINJE.

    Режим, управляющий по расходу, обязан иметь своё значение: OPM отвергает
    колоду с `CMODE` без величины ещё при разборе. У закрытой скважины
    величины нет и быть не должно, поэтому поле режима остаётся дефолтным --
    численное значение здесь не выдумывается, иначе setpoint появился бы
    мимо MagnitudeResolver.
    """
    return DEFAULTED if value is None else f"'{mode}'"


def _producer_line(control: ProducerControl) -> str:
    fields = [
        f"'{control.well}'",
        f"'{control.status}'",
        _control_mode_field(control.mode, control.liquid_rate),
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
    # У WCONINJE режим управления обязателен даже для закрытой скважины: OPM
    # отвергает колоду с `Unknown control mode string`, тогда как WCONPROD
    # дефолтный режим принимает. Величина у закрытой скважины по-прежнему не
    # печатается, поэтому уставка мимо MagnitudeResolver не появляется.
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


_WELOPEN_HEADER = "-- well status"


def _welopen_lines(step: ScheduleStep) -> list[str]:
    """Доступность скважин на эту дату.

    `WELOPEN` идёт до управляющих ключевых слов: сначала скважина закрывается
    или открывается, и лишь потом ей задаётся режим. Величины здесь нет -- это
    единственный способ выразить обязательный ремонт, не выдумав уставку.
    """
    if not step.shut_wells and not step.opened_wells:
        return []
    lines = ["WELOPEN", _WELOPEN_HEADER]
    lines.extend(f" '{well}' 'OPEN' /" for well in step.opened_wells)
    lines.extend(f" '{well}' 'SHUT' /" for well in step.shut_wells)
    lines.append("/")
    return lines


def _step_lines(step: ScheduleStep) -> list[str]:
    lines = _dates_block(step.date)
    lines.extend(_welopen_lines(step))
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
    """Собрать текст управляемого окна `wells_schedule.inc`."""

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


def render_full_schedule(
    schedule: Schedule,
    case: Case,
    *,
    history_text: str,
    policy_id: str,
    history_start_date: date,
) -> str:
    """Собрать полный `wells_schedule.inc`: история байт-в-байт + новое окно.

    История не перерисовывается генератором: байты официального источника
    являются неизменяемым префиксом. MAS позже сможет менять только часть,
    которая начинается с первого `DATES` окна управления.
    """

    if not history_text:
        raise ScheduleRenderError("source history is empty; refusing to publish partial schedule")
    management_text = render_schedule(schedule, case, policy_id=policy_id)
    start_line = f"-- source history start: {history_start_date.isoformat()}\n"
    separator = "" if history_text.endswith("\n") else "\n"
    return history_text + separator + start_line + management_text


def preserved_history_sha256(schedule_text: str, history_text: str) -> str:
    """SHA-256 сохранённого исторического префикса полного расписания."""

    if not schedule_text.startswith(history_text):
        raise ScheduleRenderError("full schedule does not preserve source history bytes")
    return hashlib.sha256(schedule_text[: len(history_text)].encode("utf-8")).hexdigest()


def schedule_sha256(text: str) -> str:
    """SHA-256 сгенерированного файла ровно в том виде, в каком он ложится на диск."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
