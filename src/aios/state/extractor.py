"""State extraction from existing simulator result objects.

This module consumes already parsed simulator output (`SummaryData`). It does not
open SMSPEC, SUMMARY, UNRST, or raw artifact files, preserving the StateCard
boundary for future MAS agents.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from aios.cases import Case
from aios.evaluation.canonical import digest
from aios.opm.summary import SummaryData
from aios.schedule.events import InjectorControl, ProducerControl, Schedule
from aios.schedule.events import WellRole as ScheduleWellRole
from aios.schedule.state import FieldState, WellState
from aios.state.schemas import StateCardError
from aios.state.state_card import (
    ConstraintState,
    FieldStateSnapshot,
    GeologyContext,
    StateCard,
    StateMetadata,
    UncertaintyState,
    WellControlSnapshot,
    WellRole,
    WellStateSnapshot,
)

FIELD_VECTORS = {
    "average_pressure": "FPR",
    "oil_rate": "FOPR",
    "water_rate": "FWPR",
    "liquid_rate": "FLPR",
    "water_injection_rate": "FWIR",
    "voidage_production_rate": "FVPR",
    "voidage_production_total": "FVPT",
}

WELL_VECTORS = {
    "bhp": "WBHP",
    "wlpr": "WLPR",
    "wopr": "WOPR",
    "wwpr": "WWPR",
    "wwir": "WWIR",
    "wct": "WWCT",
    "wopt": "WOPT",
    "wwpt": "WWPT",
    "wwit": "WWIT",
}

STATE_EXTRACTOR_VERSION = "aios.state.extractor/1.0.0"


def source_summary_identity(summary: SummaryData) -> str:
    """Deterministic identity for the parsed simulator summary consumed here."""

    payload = {
        "start": summary.start.isoformat(),
        "dates": [item.isoformat() for item in summary.dates],
        "wells": list(summary.wells),
        "units": dict(sorted(summary.units.items())),
        "field_index": dict(sorted(summary._field_index.items())),
        "well_index": {
            f"{vector}:{well}": column
            for (vector, well), column in sorted(summary._well_index.items())
        },
        "params": [list(row) for row in summary._params],
    }
    return f"summary-sha256:{digest(payload)}"


def state_run_id(*, case_id: str, schedule_hash: str, simulator_digest: str) -> str:
    """Deterministic run identifier for StateCard publication."""

    payload = {
        "case_id": case_id,
        "schedule_hash": schedule_hash,
        "simulator_digest": simulator_digest,
    }
    return f"state-{digest(payload)[:16]}"


def _control_from_state(state: WellState) -> WellControlSnapshot:
    role = WellRole.PRODUCER if state.role is ScheduleWellRole.PRODUCER else WellRole.INJECTOR
    return WellControlSnapshot(
        role=role,
        status=str(state.status),
        current_target=state.target,
        control_mode=state.mode,
    )


def _control_from_producer(control: ProducerControl) -> WellControlSnapshot:
    return WellControlSnapshot(
        role=WellRole.PRODUCER,
        status=str(control.status),
        current_target=control.liquid_rate,
        control_mode=control.mode,
    )


def _control_from_injector(control: InjectorControl) -> WellControlSnapshot:
    return WellControlSnapshot(
        role=WellRole.INJECTOR,
        status=str(control.status),
        current_target=control.surface_rate,
        control_mode=control.mode,
    )


def well_controls_from_schedule(
    initial_state: FieldState, schedule: Schedule, *, at_date: date
) -> dict[str, WellControlSnapshot]:
    """Replay production schedule controls to the requested state date."""

    controls = {state.well: _control_from_state(state) for state in initial_state.wells}
    for step in schedule.canonical().steps:
        if step.date > at_date:
            break
        for producer in step.producers:
            controls[producer.well] = _control_from_producer(producer)
        for injector in step.injectors:
            controls[injector.well] = _control_from_injector(injector)
    return controls


class StateExtractionError(StateCardError):
    """StateCard cannot be extracted from the supplied parsed simulator output."""


@dataclass(frozen=True, slots=True)
class StateExtractor:
    """Build StateCard v1.0 from existing parsed simulator outputs."""

    field_vectors: Mapping[str, str] = field(default_factory=lambda: dict(FIELD_VECTORS))
    well_vectors: Mapping[str, str] = field(default_factory=lambda: dict(WELL_VECTORS))

    def extract(
        self,
        *,
        summary: SummaryData,
        case: Case,
        run_id: str,
        simulator_digest: str,
        schedule_hash: str,
        well_controls: Mapping[str, WellControlSnapshot],
        step: int = -1,
        geology: GeologyContext | None = None,
    ) -> StateCard:
        """Extract one StateCard from a parsed OPM summary.

        Well roles and command targets are supplied by the schedule/controller
        side. The extractor refuses to infer them from raw simulator artifacts.
        """

        if not summary.dates:
            raise StateExtractionError("summary has no report dates")
        resolved_step = step if step >= 0 else summary.report_steps + step
        if resolved_step < 0 or resolved_step >= summary.report_steps:
            raise StateExtractionError(f"summary step {step} is outside available report range")

        uncertainty: list[str] = []
        field = self._field_state(summary, resolved_step, case=case)
        wells = self._well_states(
            summary,
            resolved_step,
            case=case,
            well_controls=well_controls,
        )
        constraints = self._constraints(case=case, wells=wells)
        if (
            case.constraints.compensation.status == "UNRESOLVED"
            or case.constraints.compensation.status == "NOT_APPLICABLE"
        ):
            uncertainty.append("compensation definition unknown")

        return StateCard(
            metadata=StateMetadata(
                date=summary.dates[resolved_step].date(),
                case_id=case.case_id,
                run_id=run_id,
                simulator_digest=simulator_digest,
                schedule_hash=schedule_hash,
                source_summary_identity=source_summary_identity(summary),
                extractor_version=STATE_EXTRACTOR_VERSION,
            ),
            field=field,
            wells=wells,
            constraints=constraints,
            geology=geology or GeologyContext(),
            uncertainty=UncertaintyState(items=tuple(dict.fromkeys(uncertainty))),
        )

    def _field_value(self, summary: SummaryData, logical_name: str, step: int) -> float:
        vector = self.field_vectors[logical_name]
        if not summary.has_field_vector(vector):
            raise StateExtractionError(f"field vector {vector} unavailable")
        return summary.field_value(vector, step)

    def _well_total(self, summary: SummaryData, logical_name: str, step: int) -> float:
        vector = self.well_vectors[logical_name]
        values: list[float] = []
        for well in summary.wells:
            if not summary.has_well_vector(vector, well):
                raise StateExtractionError(f"well {well} vector {vector} unavailable")
            values.append(summary.well_value(vector, well, step))
        return sum(values)

    def _field_state(self, summary: SummaryData, step: int, *, case: Case) -> FieldStateSnapshot:
        average_pressure = self._field_value(summary, "average_pressure", step)
        previous_pressure = None
        if step > 0 and summary.has_field_vector(self.field_vectors["average_pressure"]):
            previous_pressure = summary.field_value(
                self.field_vectors["average_pressure"], step - 1
            )
        pressure_trend = (
            None
            if average_pressure is None or previous_pressure is None
            else average_pressure - previous_pressure
        )
        water_rate = self._field_value(summary, "water_rate", step)
        return FieldStateSnapshot(
            average_pressure=average_pressure,
            pressure_trend=pressure_trend,
            oil_rate=self._field_value(summary, "oil_rate", step),
            water_rate=water_rate,
            liquid_rate=self._field_value(summary, "liquid_rate", step),
            water_injection_rate=self._field_value(summary, "water_injection_rate", step),
            voidage_production_rate=self._field_value(summary, "voidage_production_rate", step),
            voidage_production_total=self._field_value(summary, "voidage_production_total", step),
            oil_total=self._well_total(summary, "wopt", step),
            water_total=self._well_total(summary, "wwpt", step),
            water_injection_total=self._well_total(summary, "wwit", step),
            produced_water_available=water_rate,
            compensation_value=None,
            compensation_status=case.constraints.compensation.status,
        )

    def _well_value(self, summary: SummaryData, well: str, logical_name: str, step: int) -> float:
        vector = self.well_vectors[logical_name]
        if not summary.has_well_vector(vector, well):
            raise StateExtractionError(f"well {well} vector {vector} unavailable")
        return summary.well_value(vector, well, step)

    def _well_states(
        self,
        summary: SummaryData,
        step: int,
        *,
        case: Case,
        well_controls: Mapping[str, WellControlSnapshot],
    ) -> tuple[WellStateSnapshot, ...]:
        states: list[WellStateSnapshot] = []
        producer_bhp_min = case.constraints.official_realized.producer_bhp_min
        injector_bhp_max = case.constraints.official_realized.injector_bhp_max
        wlpr_max = case.constraints.official_realized.producer_wlpr_max
        for well in summary.wells:
            control = well_controls.get(well)
            if control is None:
                raise StateExtractionError(f"missing control context for well {well!r}")
            role = WellRole(control.role)
            bhp = self._well_value(summary, well, "bhp", step)
            wlpr = self._well_value(summary, well, "wlpr", step)
            producer_bhp_margin = (
                bhp - producer_bhp_min
                if role is WellRole.PRODUCER and producer_bhp_min is not None
                else None
            )
            injector_bhp_margin = (
                injector_bhp_max - bhp
                if role is WellRole.INJECTOR and injector_bhp_max is not None
                else None
            )
            wlpr_margin = (
                wlpr_max - wlpr if role is WellRole.PRODUCER and wlpr_max is not None else None
            )
            states.append(
                WellStateSnapshot(
                    well_id=well,
                    role=role,
                    status=control.status,
                    current_target=control.current_target,
                    control_mode=control.control_mode,
                    bhp=bhp,
                    wlpr=wlpr,
                    wopr=self._well_value(summary, well, "wopr", step),
                    wwpr=self._well_value(summary, well, "wwpr", step),
                    wwir=self._well_value(summary, well, "wwir", step),
                    wct=self._well_value(summary, well, "wct", step),
                    wopt=self._well_value(summary, well, "wopt", step),
                    wwpt=self._well_value(summary, well, "wwpt", step),
                    wwit=self._well_value(summary, well, "wwit", step),
                    producer_bhp_margin=producer_bhp_margin,
                    injector_bhp_margin=injector_bhp_margin,
                    wlpr_margin=wlpr_margin,
                )
            )
        return tuple(states)

    def _constraints(self, *, case: Case, wells: tuple[WellStateSnapshot, ...]) -> ConstraintState:
        official = {
            "producer_bhp_min": case.constraints.official_realized.producer_bhp_min,
            "injector_bhp_max": case.constraints.official_realized.injector_bhp_max,
            "wlpr_max": case.constraints.official_realized.producer_wlpr_max,
        }
        search = {
            "producer_lrat_max": case.constraints.search_bounds.producer_lrat_max,
            "injector_rate_max": case.constraints.search_bounds.injector_rate_max,
            "field_injection_rate_max": case.constraints.search_bounds.field_injection_rate_max,
        }
        margins: dict[str, float | None] = {}
        violations: list[str] = []
        binding: list[str] = []
        producer_bhp_min = official["producer_bhp_min"]
        injector_bhp_max = official["injector_bhp_max"]
        wlpr_max = official["wlpr_max"]
        for well in wells:
            if well.role is WellRole.PRODUCER:
                if producer_bhp_min is not None and well.bhp is not None:
                    key = f"producer_bhp_min:{well.well_id}"
                    margins[key] = well.bhp - producer_bhp_min
                    if margins[key] is not None and margins[key] <= 0:
                        binding.append(key)
                    if margins[key] is not None and margins[key] < 0:
                        violations.append(key)
                if wlpr_max is not None and well.wlpr is not None:
                    key = f"wlpr_max:{well.well_id}"
                    margins[key] = wlpr_max - well.wlpr
                    if margins[key] is not None and margins[key] <= 0:
                        binding.append(key)
                    if margins[key] is not None and margins[key] < 0:
                        violations.append(key)
            elif injector_bhp_max is not None and well.bhp is not None:
                key = f"injector_bhp_max:{well.well_id}"
                margins[key] = injector_bhp_max - well.bhp
                if margins[key] is not None and margins[key] <= 0:
                    binding.append(key)
                if margins[key] is not None and margins[key] < 0:
                    violations.append(key)
        return ConstraintState(
            official=official,
            search=search,
            violations=tuple(violations),
            binding_constraints=tuple(binding),
            margins=margins,
        )
