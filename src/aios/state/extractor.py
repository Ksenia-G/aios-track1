"""State extraction from existing simulator result objects.

This module consumes already parsed simulator output (`SummaryData`). It does not
open SMSPEC, SUMMARY, UNRST, or raw artifact files, preserving the StateCard
boundary for future MAS agents.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from aios.cases import Case
from aios.opm.summary import SummaryData
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
}

WELL_VECTORS = {
    "bhp": "WBHP",
    "wlpr": "WLPR",
    "wopr": "WOPR",
    "wwpr": "WWPR",
    "wct": "WWCT",
    "wopt": "WOPT",
    "wwpt": "WWPT",
    "wwit": "WWIT",
}


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
        field = self._field_state(summary, resolved_step, case=case, uncertainty=uncertainty)
        wells = self._well_states(
            summary, resolved_step, well_controls=well_controls, uncertainty=uncertainty
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
            ),
            field=field,
            wells=wells,
            constraints=constraints,
            geology=geology or GeologyContext(),
            uncertainty=UncertaintyState(items=tuple(dict.fromkeys(uncertainty))),
        )

    def _field_value(
        self, summary: SummaryData, logical_name: str, step: int, uncertainty: list[str]
    ) -> float | None:
        vector = self.field_vectors[logical_name]
        if not summary.has_field_vector(vector):
            uncertainty.append(f"field vector {vector} unavailable")
            return None
        return summary.field_value(vector, step)

    def _field_state(
        self, summary: SummaryData, step: int, *, case: Case, uncertainty: list[str]
    ) -> FieldStateSnapshot:
        average_pressure = self._field_value(summary, "average_pressure", step, uncertainty)
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
        water_rate = self._field_value(summary, "water_rate", step, uncertainty)
        return FieldStateSnapshot(
            average_pressure=average_pressure,
            pressure_trend=pressure_trend,
            oil_rate=self._field_value(summary, "oil_rate", step, uncertainty),
            water_rate=water_rate,
            liquid_rate=self._field_value(summary, "liquid_rate", step, uncertainty),
            water_injection_rate=self._field_value(
                summary, "water_injection_rate", step, uncertainty
            ),
            produced_water_available=water_rate,
            compensation_value=None,
            compensation_status=case.constraints.compensation.status,
        )

    def _well_value(
        self, summary: SummaryData, well: str, logical_name: str, step: int, uncertainty: list[str]
    ) -> float | None:
        vector = self.well_vectors[logical_name]
        if not summary.has_well_vector(vector, well):
            uncertainty.append(f"well {well} vector {vector} unavailable")
            return None
        return summary.well_value(vector, well, step)

    def _well_states(
        self,
        summary: SummaryData,
        step: int,
        *,
        well_controls: Mapping[str, WellControlSnapshot],
        uncertainty: list[str],
    ) -> tuple[WellStateSnapshot, ...]:
        states: list[WellStateSnapshot] = []
        for well in summary.wells:
            control = well_controls.get(well)
            if control is None:
                raise StateExtractionError(f"missing control context for well {well!r}")
            states.append(
                WellStateSnapshot(
                    well_id=well,
                    role=WellRole(control.role),
                    status=control.status,
                    current_target=control.current_target,
                    control_mode=control.control_mode,
                    bhp=self._well_value(summary, well, "bhp", step, uncertainty),
                    wlpr=self._well_value(summary, well, "wlpr", step, uncertainty),
                    wopr=self._well_value(summary, well, "wopr", step, uncertainty),
                    wwpr=self._well_value(summary, well, "wwpr", step, uncertainty),
                    wct=self._well_value(summary, well, "wct", step, uncertainty),
                    wopt=self._well_value(summary, well, "wopt", step, uncertainty),
                    wwpt=self._well_value(summary, well, "wwpt", step, uncertainty),
                    wwit=self._well_value(summary, well, "wwit", step, uncertainty),
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
