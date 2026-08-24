"""RUNTIME-EXECUTION-001: компиляция MagnitudeCandidate в complete schedule."""

from __future__ import annotations

import datetime

import pytest

from aios.execution.compiler import (
    COMPILER_ID,
    ScheduleCompilationError,
    compile_candidate_schedule,
)
from aios.mas.common import CandidateKind
from aios.mas.invariants import MasInvariantError
from aios.mas.magnitude import MagnitudeCandidate
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule import InjectorControl, ProducerControl, WellStatus

pytestmark = pytest.mark.usefixtures("mini_case")


def producer(well: str, liquid_rate: float) -> ProducerControl:
    return ProducerControl(well=well, status=WellStatus.OPEN, mode="LRAT", liquid_rate=liquid_rate)


def injector(well: str, surface_rate: float) -> InjectorControl:
    return InjectorControl(
        well=well,
        phase="WATER",
        status=WellStatus.OPEN,
        mode="RATE",
        surface_rate=surface_rate,
    )


def candidate(
    *,
    kind: CandidateKind = CandidateKind.AGENT_BUNDLE,
    effective_date: datetime.date = datetime.date(2020, 3, 1),
    producers: tuple[ProducerControl, ...] = (producer("P1", 130.0),),
    injectors: tuple[InjectorControl, ...] = (),
) -> MagnitudeCandidate:
    return MagnitudeCandidate(
        candidate_kind=kind,
        bundle_digest="b" * 64,
        effective_date=effective_date,
        producers=producers,
        injectors=injectors,
        source_intent_digests=("d" * 64,),
        resolver_version="tests.runtime/1.0.0",
    )


@pytest.fixture
def compiled_context(mini_case):
    state, baseline, history_text, start_date = read_baseline_inputs(mini_case)
    return mini_case, state, baseline, history_text, start_date


class TestDeterminism:
    def test_same_candidate_same_sha(self, compiled_context) -> None:
        case, state, baseline, _, _ = compiled_context
        first = compile_candidate_schedule(candidate(), case=case, state=state, baseline=baseline)
        second = compile_candidate_schedule(candidate(), case=case, state=state, baseline=baseline)
        assert first.schedule_sha256 == second.schedule_sha256
        assert first.schedule_text == second.schedule_text
        assert first.action_control_digest == second.action_control_digest

    def test_input_order_does_not_change_identity(self, compiled_context) -> None:
        case, state, baseline, _, _ = compiled_context
        reordered = candidate(producers=(producer("P1", 130.0), producer("P2", 90.0)))
        shuffled = candidate(producers=(producer("P2", 90.0), producer("P1", 130.0)))
        first = compile_candidate_schedule(reordered, case=case, state=state, baseline=baseline)
        second = compile_candidate_schedule(shuffled, case=case, state=state, baseline=baseline)
        assert first.schedule_sha256 == second.schedule_sha256
        assert first.candidate_digest == second.candidate_digest


class TestCompleteScheduleSemantics:
    def test_window_boundaries_match_case(self, compiled_context) -> None:
        case, state, baseline, _, _ = compiled_context
        compiled = compile_candidate_schedule(
            candidate(effective_date=datetime.date(2020, 2, 1)),
            case=case,
            state=state,
            baseline=baseline,
        )
        dates = compiled.schedule.dates
        assert dates[0] == case.management_start_date
        assert dates[-1] == case.management_end_date
        # Сетка опорного расписания кейса: февраль-март-апрель.
        assert datetime.date(2020, 2, 1) in dates
        assert datetime.date(2020, 3, 1) in dates

    def test_action_step_carries_whole_fund(self, compiled_context) -> None:
        """Изменённые и неизменённые скважины представлены явно на дату действия."""
        case, state, baseline, _, _ = compiled_context
        compiled = compile_candidate_schedule(
            candidate(producers=(producer("P1", 130.0),)),
            case=case,
            state=state,
            baseline=baseline,
        )
        action_step = next(
            step for step in compiled.schedule.steps if step.date == compiled.action_date
        )
        wells = {control.well for control in (*action_step.producers, *action_step.injectors)}
        assert wells == {"P1", "P2", "I1"}
        changed = next(c for c in action_step.producers if c.well == "P1")
        unchanged = next(c for c in action_step.producers if c.well == "P2")
        assert changed.liquid_rate == 130.0
        state_p2 = next(s for s in state.producers if s.well == "P2")
        assert unchanged == state_p2.as_producer()

    def test_pre_action_step_is_explicit_carry_forward(self, compiled_context) -> None:
        case, state, baseline, _, _ = compiled_context
        compiled = compile_candidate_schedule(
            candidate(), case=case, state=state, baseline=baseline
        )
        opening = compiled.schedule.steps[0]
        assert opening.date == case.management_start_date
        assert compiled.action_date != opening.date
        wells = {control.well for control in (*opening.producers, *opening.injectors)}
        assert wells == {"P1", "P2", "I1"}
        # До действия фонд находится в состоянии среза, а не в состоянии кандидата.
        p1 = next(c for c in opening.producers if c.well == "P1")
        state_p1 = next(s for s in state.producers if s.well == "P1")
        assert p1 == state_p1.as_producer()


class TestHoldExactCarryForward:
    def test_hold_compiles_to_state_snapshot(self, compiled_context) -> None:
        case, state, baseline, _, _ = compiled_context
        compiled = compile_candidate_schedule(
            candidate(kind=CandidateKind.HOLD, producers=(), injectors=()),
            case=case,
            state=state,
            baseline=baseline,
        )
        action_step = next(
            step for step in compiled.schedule.steps if step.date == compiled.action_date
        )
        held_producers = {c.well: c for c in action_step.producers}
        for well_state in state.producers:
            assert held_producers[well_state.well] == well_state.as_producer()

    def test_hold_with_altered_value_is_rejected(self, compiled_context) -> None:
        """HOLD с «чуть другим» значением -- не carry-forward; инвариант роняет."""
        case, state, baseline, _, _ = compiled_context
        state_p1 = next(s for s in state.producers if s.well == "P1")
        drifted = producer("P1", float(state_p1.target) + 1e-9)
        with pytest.raises(MasInvariantError):
            compile_candidate_schedule(
                candidate(kind=CandidateKind.HOLD, producers=(drifted,)),
                case=case,
                state=state,
                baseline=baseline,
            )

    def test_baseline_and_action_digests_differ_for_action(self, compiled_context) -> None:
        case, state, baseline, _, _ = compiled_context
        compiled = compile_candidate_schedule(
            candidate(), case=case, state=state, baseline=baseline
        )
        assert compiled.baseline_control_digest != compiled.action_control_digest


class TestRejections:
    def test_unknown_well_rejected(self, compiled_context) -> None:
        case, state, baseline, _, _ = compiled_context
        with pytest.raises(ScheduleCompilationError, match="unknown well"):
            compile_candidate_schedule(
                candidate(producers=(producer("P9", 100.0),)),
                case=case,
                state=state,
                baseline=baseline,
            )

    def test_role_conversion_rejected(self, compiled_context) -> None:
        case, state, baseline, _, _ = compiled_context
        with pytest.raises(ScheduleCompilationError, match="as a producer"):
            compile_candidate_schedule(
                candidate(producers=(producer("I1", 100.0),)),
                case=case,
                state=state,
                baseline=baseline,
            )

    def test_effective_date_outside_grid_rejected(self, compiled_context) -> None:
        case, state, baseline, _, _ = compiled_context
        with pytest.raises(ScheduleCompilationError, match="not a report step"):
            compile_candidate_schedule(
                candidate(effective_date=datetime.date(2020, 3, 15)),
                case=case,
                state=state,
                baseline=baseline,
            )


class TestProvenance:
    def test_compilation_report_fields(self, compiled_context) -> None:
        case, state, baseline, _, _ = compiled_context
        source = candidate()
        compiled = compile_candidate_schedule(source, case=case, state=state, baseline=baseline)
        report = compiled.compilation_to_json()
        assert report["compiler_id"] == COMPILER_ID
        assert report["candidate_digest"] == source.canonical().digest()
        assert report["schedule_sha256"] == compiled.schedule_sha256
