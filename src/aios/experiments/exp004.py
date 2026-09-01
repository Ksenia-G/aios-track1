"""EXP-004: поиск верхней границы ЧДД по двум осям сразу.

EXP-001..003 двигали только закачку и только перераспределением. Это узкое
семейство: суммарный плановый отбор в нём не менялся вовсе. EXP-004
добавляет вторую ось -- уставки добывающих скважин -- и снимает
искусственное ограничение на суммарный объём закачки.

Ключевые правила, из которых всё следует.

1. **Заданная уставка -- не результат.** Поднятая `LRAT` ничего не значит,
   если скважина упирается в забойное давление. Везде, где отчёт говорит
   о добыче или закачке, берутся фактические векторы прогона, а не то,
   что было заказано. Разница «заказано против получено» -- отдельная
   измеряемая величина.

2. **Потолок 500 м3/сут -- экономический, а не косметический.** При
   `WLPR > 500` официальный калькулятор обрывает расчёт целиком, поэтому
   уставки обрезаются до 500 на этапе построения расписания, а не после.

3. **VRR не ограничение.** Ни 1.0, ни 1.1 не подтверждены организатором.
   Замещение отбора измеряется и печатается, но не запрещает сценарий.
   Запрещают только реализованные ограничения проекта: забойные давления,
   потолок WLPR, запрет перевода добывающих в нагнетательные.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from aios.cases.contract import Case
from aios.experiments.exp002 import TransferMode, TransferSpec, _validate_transfers
from aios.experiments.exp003 import SupplementalInjection, quantize_fraction
from aios.schedule.events import (
    InjectorControl,
    ProducerControl,
    Schedule,
    ScheduleStep,
    natural_key,
)
from aios.schedule.generator import render_schedule
from aios.schedule.state import FieldState

# Потолок ликвидной уставки добывающей скважины. Выше него официальный
# калькулятор ЧДД падает с ошибкой, а не штрафует.
WLPR_HARD_CEILING = 500.0

# Ниже этого множителя воздействие перестаёт быть отличимым от опорного.
MIN_SCALE = 1e-6


class Family(StrEnum):
    """Семейство поиска."""

    BASELINE = "BASELINE"
    INJECTION = "A_INJECTION"
    PRODUCER = "B_PRODUCER"
    JOINT = "C_JOINT"
    AGGRESSIVE = "D_AGGRESSIVE"
    REFERENCE = "R_REFERENCE"


class Exp004PolicyError(ValueError):
    """Воздействие нельзя наложить на это состояние."""


@dataclass(frozen=True, slots=True)
class ProducerUplift:
    """Множитель ликвидной уставки добывающих скважин.

    Обрезание по `WLPR_HARD_CEILING` -- часть воздействия, а не поправка
    после факта: расписание не должно содержать уставку, на которой
    экономический расчёт откажется работать.
    """

    factor: float
    wells: tuple[str, ...] | None = None
    ceiling: float = WLPR_HARD_CEILING

    def __post_init__(self) -> None:
        if not math.isfinite(self.factor) or self.factor <= MIN_SCALE:
            raise Exp004PolicyError(f"producer uplift factor must be positive: {self.factor}")
        if self.ceiling > WLPR_HARD_CEILING:
            raise Exp004PolicyError(
                f"ceiling {self.ceiling} exceeds the economic limit {WLPR_HARD_CEILING}"
            )

    def applies_to(self, well: str) -> bool:
        return self.wells is None or well in self.wells

    def target(self, baseline_rate: float) -> float:
        return min(baseline_rate * self.factor, self.ceiling)

    def to_json(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "wells": None if self.wells is None else list(self.wells),
            "ceiling": self.ceiling,
        }


@dataclass(frozen=True, slots=True)
class InjectionScale:
    """Множитель плановой закачки нагнетательных скважин."""

    factor: float
    wells: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.factor) or self.factor < 0.0:
            raise Exp004PolicyError(f"injection scale must be non-negative: {self.factor}")

    def applies_to(self, well: str) -> bool:
        return self.wells is None or well in self.wells

    def to_json(self) -> dict[str, Any]:
        return {"factor": self.factor, "wells": None if self.wells is None else list(self.wells)}


@dataclass(frozen=True, slots=True)
class Exp004Scenario:
    """Сценарий EXP-004: добыча, закачка, переносы и добавки вместе."""

    scenario_id: str
    family: Family
    label: str = ""
    producer_uplift: ProducerUplift | None = None
    injection_scale: InjectionScale | None = None
    transfers: tuple[TransferSpec, ...] = ()
    supplements: tuple[SupplementalInjection, ...] = ()
    render_policy_id: str | None = None

    @property
    def is_baseline(self) -> bool:
        return (
            self.producer_uplift is None
            and self.injection_scale is None
            and not self.transfers
            and not self.supplements
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "family": self.family.value,
            "label": self.label,
            "producer_uplift": None
            if self.producer_uplift is None
            else self.producer_uplift.to_json(),
            "injection_scale": None
            if self.injection_scale is None
            else self.injection_scale.to_json(),
            "transfers": [t.to_json() for t in self.transfers],
            "supplements": [s.to_json() for s in self.supplements],
        }


@dataclass(frozen=True, slots=True)
class Exp004Policy:
    """Наложение всех воздействий EXP-004 на опорное расписание.

    Порядок применения к закачке фиксирован и содержателен: сначала
    перенос между парами (он сохраняет сумму), затем общий множитель,
    затем адресная добавка. Иной порядок дал бы другое расписание при тех
    же параметрах, а сценарий обязан однозначно определять файл.
    """

    scenario: Exp004Scenario
    policy_id: str

    def plan(self, state: FieldState, baseline: Schedule, case: Case) -> Schedule:
        scenario = self.scenario
        if scenario.is_baseline:
            return baseline.canonical()
        if scenario.transfers:
            _validate_transfers(scenario.transfers, state)

        supplement_by_well = {s.well: s.added_rate_m3_per_day for s in scenario.supplements}
        for well in supplement_by_well:
            well_state = state.by_well.get(well)
            if well_state is None or well_state.role.name != "INJECTOR":
                raise Exp004PolicyError(f"supplemental well {well} is not a known injector")

        injector_wells = {w for t in scenario.transfers for w in t.wells} | set(supplement_by_well)
        if scenario.injection_scale is not None:
            injector_wells |= {
                s.well for s in state.injectors if scenario.injection_scale.applies_to(s.well)
            }

        templates: dict[str, InjectorControl] = {}
        targets: dict[str, float] = {}
        for well in injector_wells:
            well_state = state.by_well.get(well)
            if well_state is None:
                raise Exp004PolicyError(f"unknown injector {well}")
            template = well_state.as_injector()
            templates[well] = template
            targets[well] = float(template.surface_rate or 0.0)

        producer_targets: dict[str, float] = {}
        producer_templates: dict[str, ProducerControl] = {}
        if scenario.producer_uplift is not None:
            for well_state in state.producers:
                control = well_state.as_producer()
                producer_templates[well_state.well] = control
                producer_targets[well_state.well] = float(control.liquid_rate or 0.0)

        steps: list[ScheduleStep] = []
        for index, step in enumerate(baseline.canonical().steps):
            touched_injector = False
            touched_producer = False
            for control in step.injectors:
                if control.well in injector_wells:
                    templates[control.well] = control
                    targets[control.well] = float(control.surface_rate or 0.0)
                    touched_injector = True
            for control in step.producers:
                if control.well in producer_targets:
                    producer_templates[control.well] = control
                    producer_targets[control.well] = float(control.liquid_rate or 0.0)
                    touched_producer = True

            in_window = case.management_start_date <= step.date <= case.management_end_date
            first = index == 0
            if not in_window:
                steps.append(step)
                continue

            injectors = step.injectors
            if injector_wells and (touched_injector or first):
                candidate = dict(targets)
                for transfer in scenario.transfers:
                    donor_target = targets[transfer.donor]
                    delta = transfer.fraction * donor_target if donor_target > 0.0 else 0.0
                    if transfer.mode in (TransferMode.TRANSFER, TransferMode.DONOR_ONLY):
                        candidate[transfer.donor] -= delta
                    if transfer.mode in (TransferMode.TRANSFER, TransferMode.RECEIVER_ONLY):
                        candidate[transfer.receiver] += delta
                if scenario.injection_scale is not None:
                    for well in candidate:
                        if scenario.injection_scale.applies_to(well):
                            candidate[well] *= scenario.injection_scale.factor
                for well, added in supplement_by_well.items():
                    candidate[well] += added
                others = [c for c in step.injectors if c.well not in injector_wells]
                for well in sorted(injector_wells, key=natural_key):
                    rate = candidate[well]
                    if rate < 0.0:
                        raise Exp004PolicyError(f"negative injection target for {well}: {rate}")
                    others.append(replace(templates[well], surface_rate=rate))
                injectors = tuple(others)

            producers = step.producers
            if producer_targets and (touched_producer or first):
                uplift = scenario.producer_uplift
                assert uplift is not None
                changed = [c for c in step.producers if not uplift.applies_to(c.well)]
                for well in sorted(producer_targets, key=natural_key):
                    if not uplift.applies_to(well):
                        continue
                    changed.append(
                        replace(
                            producer_templates[well],
                            liquid_rate=uplift.target(producer_targets[well]),
                        )
                    )
                producers = tuple(changed)

            steps.append(ScheduleStep(step.date, producers, injectors))
        return Schedule(tuple(steps)).canonical()


def build_exp004_schedule(
    scenario: Exp004Scenario, case: Case, *, state: FieldState, baseline: Schedule
) -> tuple[Schedule, str]:
    policy_id = scenario.render_policy_id or f"EXP004::{scenario.scenario_id}"
    policy = Exp004Policy(scenario, policy_id)
    schedule = policy.plan(state, baseline, case)
    return schedule, render_schedule(schedule, case, policy_id=policy_id)


# --- построение сценариев --------------------------------------------------


def _tag(value: float) -> str:
    """Множитель -> метка идентификатора: 1.25 -> `125`, 0.8 -> `080`."""

    hundredths = round(value * 100)
    if abs(value * 100 - hundredths) > 1e-6:
        raise ValueError(f"scale {value} is finer than a hundredth")
    return f"{hundredths:03d}"


def baseline_scenario() -> Exp004Scenario:
    return Exp004Scenario(
        scenario_id="EXP004_R_BASELINE",
        family=Family.BASELINE,
        label="baseline control",
        render_policy_id="BASELINE_CONTINUATION",
    )


def injection_scale_scenarios(factors: Sequence[float]) -> tuple[Exp004Scenario, ...]:
    return tuple(
        Exp004Scenario(
            scenario_id=f"EXP004_A_INJ_{_tag(f)}",
            family=Family.INJECTION,
            label=f"field injection x{f:g}",
            injection_scale=InjectionScale(f),
        )
        for f in factors
    )


def producer_uplift_scenarios(factors: Sequence[float]) -> tuple[Exp004Scenario, ...]:
    return tuple(
        Exp004Scenario(
            scenario_id=f"EXP004_B_PROD_{_tag(f)}",
            family=Family.PRODUCER,
            label=f"producer LRAT x{f:g} (capped at {WLPR_HARD_CEILING:g})",
            producer_uplift=ProducerUplift(f),
        )
        for f in factors
    )


def joint_scenarios(
    producer_factors: Sequence[float],
    injection_factors: Sequence[float],
    *,
    transfers: Sequence[TransferSpec] = (),
    supplements: Sequence[SupplementalInjection] = (),
    family: Family = Family.JOINT,
    prefix: str = "EXP004_C_JOINT",
) -> tuple[Exp004Scenario, ...]:
    scenarios: list[Exp004Scenario] = []
    for p in producer_factors:
        for i in injection_factors:
            suffix = "_T" if transfers or supplements else ""
            scenarios.append(
                Exp004Scenario(
                    scenario_id=f"{prefix}_P{_tag(p)}_I{_tag(i)}{suffix}",
                    family=family,
                    label=f"producer x{p:g}, injection x{i:g}",
                    producer_uplift=ProducerUplift(p),
                    injection_scale=InjectionScale(i),
                    transfers=tuple(transfers),
                    supplements=tuple(supplements),
                )
            )
    return tuple(scenarios)


def exp003_winner_transfers() -> tuple[TransferSpec, ...]:
    """Направления победителя EXP-003 как переиспользуемая структура."""

    return (
        TransferSpec(2, "41", "19", quantize_fraction(0.35)),
        TransferSpec(3, "32", "4", quantize_fraction(0.825)),
        TransferSpec(4, "7", "42", quantize_fraction(0.975)),
        TransferSpec(5, "26", "2", quantize_fraction(0.975)),
    )


def exp003_winner_scenario() -> Exp004Scenario:
    """Победитель EXP-003 в терминах EXP-004 -- опорная точка сравнения."""

    return Exp004Scenario(
        scenario_id="EXP004_R_EXP003_BEST",
        family=Family.REFERENCE,
        label="EXP-003 MAX_NPV re-run",
        transfers=exp003_winner_transfers(),
        supplements=(SupplementalInjection("42", 100.0),),
        render_policy_id="EXP003::EXP003_D_COMP_ADD_42_100",
    )


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def scenario_ids(scenarios: Sequence[Exp004Scenario]) -> list[str]:
    return [s.scenario_id for s in scenarios]


def assert_unique(scenarios: Sequence[Exp004Scenario]) -> None:
    ids = scenario_ids(scenarios)
    if len(set(ids)) != len(ids):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate scenario ids: {', '.join(duplicates)}")


def as_mapping(scenarios: Sequence[Exp004Scenario]) -> Mapping[str, Exp004Scenario]:
    assert_unique(scenarios)
    return {s.scenario_id: s for s in scenarios}
