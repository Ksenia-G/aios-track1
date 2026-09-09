"""Trajectory-wide Guardian must reject mid-horizon hard-constraint violations."""

from __future__ import annotations

import datetime
from dataclasses import replace

import pytest

from aios.agents.constraint_guardian import ConstraintGuardian
from aios.opm.summary import SummaryData
from aios.state.extractor import StateExtractor
from aios.state.state_card import WellControlSnapshot
from tests.support.mas_team_fixtures import synthetic_state_card


def _t(year: int, month: int, day: int = 1) -> datetime.datetime:
    return datetime.datetime(year, month, day)  # noqa: DTZ001 -- OPM model time


def _control_for(well):
    return WellControlSnapshot(
        role=well.role,
        status=well.status,
        current_target=well.current_target,
        control_mode=well.control_mode,
    )


def _controls(card):
    return {well.well_id: _control_for(well) for well in card.wells}


def _case(card, mini_case):
    return replace(
        mini_case,
        management_start_date=datetime.date(2020, 1, 1),
        management_end_date=datetime.date(2020, 3, 1),
    )


def _summary(
    *,
    producer_well: str = "SYN-P1",
    injector_well: str = "SYN-I1",
    producer_bhp: tuple[float, float, float] = (60.0, 60.0, 55.0),
    injector_bhp: tuple[float, float, float] = (250.0, 250.0, 250.0),
    wlpr: tuple[float, float, float] = (100.0, 100.0, 120.0),
) -> SummaryData:
    dates = (_t(2020, 1), _t(2020, 2), _t(2020, 3))
    wells = (producer_well, injector_well)
    well_data = {
        producer_well: {
            "WBHP": producer_bhp,
            "WLPR": wlpr,
            "WOPR": (80.0, 80.0, 90.0),
            "WWPR": (20.0, 20.0, 30.0),
            "WWIR": (0.0, 0.0, 0.0),
            "WWCT": (0.2, 0.2, 0.25),
            "WOPT": (0.0, 80.0, 170.0),
            "WWPT": (0.0, 20.0, 50.0),
            "WWIT": (0.0, 0.0, 0.0),
        },
        injector_well: {
            "WBHP": injector_bhp,
            "WLPR": (0.0, 0.0, 0.0),
            "WOPR": (0.0, 0.0, 0.0),
            "WWPR": (0.0, 0.0, 0.0),
            "WWIR": (150.0, 150.0, 150.0),
            "WWCT": (0.0, 0.0, 0.0),
            "WOPT": (0.0, 0.0, 0.0),
            "WWPT": (0.0, 0.0, 0.0),
            "WWIT": (0.0, 150.0, 300.0),
        },
    }
    vectors = ("WBHP", "WLPR", "WOPR", "WWPR", "WWIR", "WWCT", "WOPT", "WWPT", "WWIT")
    well_index: dict[tuple[str, str], int] = {}
    field_index = {
        "FPR": 0,
        "FOPR": 1,
        "FWPR": 2,
        "FLPR": 3,
        "FWIR": 4,
        "FVPR": 5,
        "FVPT": 6,
    }
    col = len(field_index)
    for vector in vectors:
        for well in wells:
            well_index[(vector, well)] = col
            col += 1
    rows = []
    for step in range(len(dates)):
        row = [
            210.0,
            well_data[producer_well]["WOPR"][step],
            well_data[producer_well]["WWPR"][step],
            well_data[producer_well]["WLPR"][step],
            well_data[injector_well]["WWIR"][step],
            160.0,
            1000.0 + step,
        ]
        for vector in vectors:
            for well in wells:
                row.append(well_data[well][vector][step])
        rows.append(tuple(row))
    return SummaryData(
        start=dates[0],
        dates=dates,
        wells=wells,
        units={},
        _field_index=field_index,
        _well_index=well_index,
        _params=tuple(rows),
    )


def _final_state(summary: SummaryData, mini_case):
    base = synthetic_state_card()
    return StateExtractor().extract(
        summary=summary,
        case=_case(base, mini_case),
        run_id="SYN-run-final",
        simulator_digest="SYN-simulator",
        schedule_hash="a" * 64,
        well_controls=_controls(base),
        step=-1,
        geology=base.geology,
    )


def test_red_legacy_single_state_guardian_misses_intermediate_producer_bhp_violation(
    mini_case,
) -> None:
    from aios.agents.trajectory_guardian import TrajectoryConstraintGuardian

    summary = _summary(producer_bhp=(60.0, 45.0, 55.0))
    final_report = ConstraintGuardian().evaluate(_final_state(summary, mini_case))
    assert final_report.summary.failed == 0, "legacy final-StateCard Guardian sees only 55 bar"

    report = TrajectoryConstraintGuardian().evaluate_summary(
        summary=summary,
        case=_case(synthetic_state_card(), mini_case),
        run_id="SYN-run-trajectory",
        simulator_digest="SYN-simulator",
        schedule_hash="a" * 64,
        well_controls=_controls(synthetic_state_card()),
        geology=synthetic_state_card().geology,
    )

    assert report.verdict == "FAIL"
    assert report.summary.report_states_checked == 3
    assert report.summary.first_checked_date == "2020-01-01"
    assert report.summary.last_checked_date == "2020-03-01"
    first = report.first_violation
    assert first is not None
    assert first.date == "2020-02-01"
    assert first.well_name == "SYN-P1"
    assert first.constraint == "producer_bhp_min"
    assert first.measured_value == pytest.approx(45.0)
    assert first.limit == pytest.approx(50.0)


@pytest.mark.parametrize(
    ("summary", "constraint", "well", "observed", "limit"),
    [
        (_summary(injector_bhp=(250.0, 301.0, 250.0)), "injector_bhp_max", "SYN-I1", 301.0, 300.0),
        (_summary(wlpr=(100.0, 501.0, 120.0)), "wlpr_max", "SYN-P1", 501.0, 500.0),
    ],
)
def test_red_intermediate_non_final_violations_are_trajectory_failures(
    mini_case, summary, constraint, well, observed, limit
) -> None:
    from aios.agents.trajectory_guardian import TrajectoryConstraintGuardian

    final_report = ConstraintGuardian().evaluate(_final_state(summary, mini_case))
    assert final_report.summary.failed == 0

    report = TrajectoryConstraintGuardian().evaluate_summary(
        summary=summary,
        case=_case(synthetic_state_card(), mini_case),
        run_id="SYN-run-trajectory",
        simulator_digest="SYN-simulator",
        schedule_hash="a" * 64,
        well_controls=_controls(synthetic_state_card()),
        geology=synthetic_state_card().geology,
    )

    assert report.verdict == "FAIL"
    assert report.first_violation is not None
    assert report.first_violation.date == "2020-02-01"
    assert report.first_violation.well_name == well
    assert report.first_violation.constraint == constraint
    assert report.first_violation.measured_value == pytest.approx(observed)
    assert report.first_violation.limit == pytest.approx(limit)


def test_red_clean_trajectory_passes_and_records_endpoint_count(mini_case) -> None:
    from aios.agents.trajectory_guardian import TrajectoryConstraintGuardian

    report = TrajectoryConstraintGuardian().evaluate_summary(
        summary=_summary(producer_well="7A", injector_well="12"),
        case=_case(synthetic_state_card(), mini_case),
        run_id="SYN-run-trajectory",
        simulator_digest="SYN-simulator",
        schedule_hash="a" * 64,
        well_controls={
            "7A": _control_for(synthetic_state_card().wells[0]),
            "12": _control_for(synthetic_state_card().wells[1]),
        },
        geology=synthetic_state_card().geology,
    )

    assert report.verdict == "PASS"
    assert report.summary.report_states_checked == 3
    assert report.summary.total_checks == 9
    assert report.summary.failed_checks == 0
    assert report.summary.first_checked_date == "2020-01-01"
    assert report.summary.last_checked_date == "2020-03-01"
    assert report.first_violation is None
    payload = report.to_json()
    assert "prompt" not in str(payload).lower()
    assert "llm" not in str(payload).lower()


def test_empty_trajectory_fails_closed() -> None:
    from aios.agents.trajectory_guardian import TrajectoryConstraintGuardian

    report = TrajectoryConstraintGuardian().evaluate_states(())

    assert report.verdict == "FAIL"
    assert report.summary.report_states_checked == 0
    assert report.summary.failed_checks == 1
    assert report.summary.first_checked_date is None
    assert report.summary.last_checked_date is None
    assert report.metadata["trajectory_failure_reason"] == "NO_REPORT_STATES_CHECKED"


def test_opm_trajectory_guardian_reads_the_same_summary_locator_as_chdd(mini_case) -> None:
    from aios.evaluation.simulation import SimulationResult, SimulationStatus
    from aios.execution import RuntimeExecution
    from aios.mas.common import CandidateKind
    from aios.mas.magnitude import MagnitudeCandidate
    from aios.mas.orchestration.composition import OpmTrajectoryConstraintGuardian
    from aios.schedule import InjectorControl, ProducerControl, Schedule, ScheduleStep, WellStatus
    from aios.schedule.state import FieldState
    from tests.support.orchestration_doubles import build_provenance, build_request, request_binding

    summary = _summary(producer_bhp=(60.0, 45.0, 55.0))

    class Locator:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def read_summary(self, run_label: str, case_name: str) -> SummaryData:
            self.calls.append((run_label, case_name))
            return summary

    candidate = MagnitudeCandidate(
        candidate_kind=CandidateKind.AGENT_BUNDLE,
        bundle_digest="b" * 64,
        effective_date=datetime.date(2020, 1, 1),
        producers=(
            ProducerControl(well="SYN-P1", status=WellStatus.OPEN, mode="LRAT", liquid_rate=120.0),
        ),
        injectors=(
            InjectorControl(
                well="SYN-I1",
                phase="WATER",
                status=WellStatus.OPEN,
                mode="RATE",
                surface_rate=150.0,
            ),
        ),
        source_intent_digests=("d" * 64,),
        resolver_version="tests.trajectory/1.0.0",
    )
    request = build_request(candidate)
    result = SimulationResult.create(status=SimulationStatus.SUCCESS, **request_binding(request))
    execution = RuntimeExecution(request, result, build_provenance(candidate, request))
    final_state = _final_state(summary, mini_case)
    schedule = Schedule(
        steps=(
            ScheduleStep(
                datetime.date(2020, 1, 1),
                producers=candidate.producers,
                injectors=candidate.injectors,
            ),
        )
    )
    locator = Locator()

    report = OpmTrajectoryConstraintGuardian(
        case=_case(synthetic_state_card(), mini_case),
        locator=locator,  # type: ignore[arg-type]
        initial_state=FieldState(as_of=datetime.date(2020, 1, 1), known_wells=(), wells={}),
        baseline=schedule,
        geology=synthetic_state_card().geology,
    ).evaluate(candidate, execution, final_state=final_state)

    assert locator.calls == [(candidate.digest(), mini_case.history.data_path.stem)]
    assert report.verdict == "FAIL"
    assert report.summary.report_states_checked == 3
    assert report.first_violation is not None
    assert report.first_violation.date == "2020-02-01"
    assert report.first_violation.well_name == "SYN-P1"
