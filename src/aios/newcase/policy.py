"""Материализация месячного расписания нового кейса.

Решение и материализация -- разные вещи. Специалисты и Arbiter работают на
редких эпохах решения, а колоде нужен каждый месяц: между эпохами управление
просто повторяется. Здесь живёт только повторение и приведение к допустимому
множеству -- ремонты и пределы поля. Числа приходят готовыми; ни одно значение
уставки здесь не рождается.
"""

from __future__ import annotations

from collections.abc import Mapping

from aios.newcase.calendar import WellAvailabilityCalendar
from aios.newcase.capacity import FieldCapacity, project_to_capacity
from aios.newcase.grid import management_months
from aios.schedule.events import (
    InjectorControl,
    ProducerControl,
    Schedule,
    ScheduleStep,
    WellStatus,
    natural_key,
)

PRODUCER_BHP_MIN = 50.0
INJECTOR_BHP_MAX = 300.0
PRODUCER_MODE = "LRAT"
INJECTOR_MODE = "RATE"
INJECTOR_PHASE = "WATER"


def build_monthly_schedule(
    *,
    producers: Mapping[str, float],
    injectors: Mapping[str, float],
    merit: Mapping[str, float],
    calendar: WellAvailabilityCalendar,
) -> Schedule:
    """Собрать 84 месячных шага из одного вектора управления.

    На каждый месяц: снять команды со скважин в ремонте, затем уложить остаток
    в действующие на эту дату пределы поля.
    """
    steps: list[ScheduleStep] = []
    previous_shut: set[str] = set()
    for day in management_months():
        capacity = FieldCapacity.at(day)

        allowed_p = calendar.enforce(dict(producers), day)
        allowed_i = calendar.enforce(dict(injectors), day)
        rates_p = project_to_capacity(allowed_p, cap=capacity.liquid_max, merit=merit)
        rates_i = project_to_capacity(allowed_i, cap=capacity.injection_max, merit=merit)

        # Скважина в ремонте вообще не получает управляющей строки: её
        # недоступность выражается WELOPEN, а команда просто не печатается.
        producer_controls = tuple(
            ProducerControl(
                well=well,
                status=WellStatus.OPEN,
                mode=PRODUCER_MODE,
                liquid_rate=rates_p[well],
                bhp_limit=PRODUCER_BHP_MIN,
            )
            for well in sorted(rates_p, key=natural_key)
        )
        injector_controls = tuple(
            InjectorControl(
                well=well,
                phase=INJECTOR_PHASE,
                status=WellStatus.OPEN,
                mode=INJECTOR_MODE,
                surface_rate=rates_i[well],
                bhp_limit=INJECTOR_BHP_MAX,
            )
            for well in sorted(rates_i, key=natural_key)
        )
        managed = set(producers) | set(injectors)
        shut_now = {w for w in managed if not calendar.is_available(w, day)}
        steps.append(
            ScheduleStep(
                date=day,
                producers=producer_controls,
                injectors=injector_controls,
                shut_wells=tuple(sorted(shut_now - previous_shut, key=natural_key)),
                opened_wells=tuple(sorted(previous_shut - shut_now, key=natural_key)),
            )
        )
        previous_shut = shut_now
    return Schedule(steps=tuple(steps))


__all__ = ["INJECTOR_BHP_MAX", "PRODUCER_BHP_MIN", "build_monthly_schedule"]
