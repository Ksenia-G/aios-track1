"""Наложение переноса закачки и перечень сценариев.

Симулятор здесь не запускается: проверяется то, что должно быть верно
до всякого счёта -- перечень сценариев, форма расписания кандидата и
сохранение плановой суммы пары.
"""

from datetime import date

import pytest

from aios.cases import Case
from aios.experiments import (
    SCREENING_BLOCKS,
    InjectionTransfer,
    Scenario,
    TransferError,
    baseline_pair_targets,
    baseline_scenario,
    build_candidate_schedule,
    first_pass_scenarios,
    fraction_tag,
    make_scenario,
    refinement_scenarios,
    scenario_id,
    validate_scenario,
)
from aios.experiments.blocks import block_of_injector
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule.generator import schedule_sha256
from aios.schedule.validator import GateStatus, validate_schedule

# --- перечень сценариев ----------------------------------------------------


def test_pair_enumeration_matches_the_experiment_design() -> None:
    per_block = {
        block.block_id: len(block.ordered_pairs())
        for block in SCREENING_BLOCKS
        if block.supports_redistribution
    }

    assert per_block == {2: 2, 3: 12, 4: 6, 5: 30}
    assert len(first_pass_scenarios()) == 50


def test_single_injector_block_is_skipped() -> None:
    block_one = next(b for b in SCREENING_BLOCKS if b.block_id == 1)

    assert not block_one.supports_redistribution
    assert block_one.ordered_pairs() == ()
    assert all(scenario.block_id != 1 for scenario in first_pass_scenarios())


def test_scenario_ids_are_deterministic_and_directional() -> None:
    first = first_pass_scenarios()
    second = first_pass_scenarios()

    assert [s.scenario_id for s in first] == [s.scenario_id for s in second]
    assert scenario_id(2, "19", "41", 0.15) == "EXP001_B2_19_TO_41_F15"
    assert scenario_id(2, "41", "19", 0.15) == "EXP001_B2_41_TO_19_F15"
    assert len({s.scenario_id for s in first}) == len(first)


@pytest.mark.parametrize(
    ("fraction", "tag"), [(0.05, "F05"), (0.10, "F10"), (0.15, "F15"), (0.30, "F30")]
)
def test_fraction_tags(fraction: float, tag: str) -> None:
    assert fraction_tag(fraction) == tag


def test_fraction_finer_than_a_hundredth_of_a_percent_is_rejected() -> None:
    # Сетка EXP-003 доходит до сотых долей процента; всё, что мельче,
    # уже не восстанавливается из идентификатора сценария однозначно.
    with pytest.raises(ValueError, match="hundredth of a percent"):
        fraction_tag(0.155555)


def test_refinement_enumeration_is_bounded() -> None:
    pairs = [(2, "19", "41"), (3, "4", "13"), (4, "7", "28"), (5, "2", "15"), (5, "15", "2")]

    extra = refinement_scenarios(pairs)

    assert len(extra) == 20
    assert 0.15 not in {s.fraction for s in extra}


def test_transfer_rejects_bad_parameters() -> None:
    window = {"start_date": date(2014, 1, 1), "end_date": date(2015, 12, 1)}
    with pytest.raises(TransferError, match="different wells"):
        InjectionTransfer("19", "19", 0.15, **window)
    with pytest.raises(TransferError, match=r"\(0, 1\)"):
        InjectionTransfer("19", "41", 0.0, **window)
    with pytest.raises(TransferError, match=r"\(0, 1\)"):
        InjectionTransfer("19", "41", 1.0, **window)
    with pytest.raises(TransferError, match="after its start"):
        InjectionTransfer("19", "41", 0.15, start_date=date(2015, 1, 1), end_date=date(2014, 1, 1))


# --- расписание кандидата --------------------------------------------------


def _plan(case: Case, scenario: Scenario):
    state, baseline, _history = read_baseline_inputs(case)
    schedule, text = build_candidate_schedule(scenario, case, state=state, baseline=baseline)
    return state, baseline, schedule, text


def test_control_run_reproduces_the_accepted_baseline(model_y_case: Case) -> None:
    _state, _baseline, _schedule, text = _plan(model_y_case, baseline_scenario())

    assert (
        schedule_sha256(text) == "7662d10495796ace6b0d20df404ca68932b2894f367de2062c232cbe3e01c399"
    )


def test_candidate_schedule_is_deterministic(model_y_case: Case) -> None:
    scenario = make_scenario(2, "19", "41", 0.15)

    first = _plan(model_y_case, scenario)[3]
    second = _plan(model_y_case, scenario)[3]

    assert schedule_sha256(first) == schedule_sha256(second)


def test_opposite_directions_differ(model_y_case: Case) -> None:
    forward = _plan(model_y_case, make_scenario(2, "19", "41", 0.15))[3]
    backward = _plan(model_y_case, make_scenario(2, "41", "19", 0.15))[3]

    assert schedule_sha256(forward) != schedule_sha256(backward)


def test_pair_total_is_conserved_at_every_control_step(model_y_case: Case) -> None:
    scenario = make_scenario(5, "2", "15", 0.15)
    state, baseline, candidate, _text = _plan(model_y_case, scenario)

    base_targets = baseline_pair_targets(state, baseline, "2", "15")
    cand_targets = baseline_pair_targets(state, candidate, "2", "15")

    assert set(base_targets) == set(cand_targets)
    for day, (base_donor, base_receiver) in base_targets.items():
        cand_donor, cand_receiver = cand_targets[day]
        assert cand_donor + cand_receiver == pytest.approx(base_donor + base_receiver, abs=1e-9)
        assert cand_donor == pytest.approx(base_donor * 0.85, abs=1e-9)
        assert cand_donor >= 0.0


def test_zero_donor_target_transfers_nothing() -> None:
    from aios.experiments.transfer import InjectionTransferPolicy
    from aios.schedule.events import InjectorControl, Schedule, ScheduleStep, WellRole, WellStatus
    from aios.schedule.state import FieldState, WellState

    donor = WellState("D", WellRole.INJECTOR, WellStatus.OPEN, "RATE", 0.0, 300.0, "WATER")
    receiver = WellState("R", WellRole.INJECTOR, WellStatus.OPEN, "RATE", 200.0, 300.0, "WATER")
    state = FieldState(date(2014, 1, 1), frozenset({"D", "R"}), (donor, receiver))
    baseline = Schedule(
        (
            ScheduleStep(
                date(2014, 1, 1),
                injectors=(
                    InjectorControl("D", "WATER", WellStatus.OPEN, "RATE", 0.0, 300.0),
                    InjectorControl("R", "WATER", WellStatus.OPEN, "RATE", 200.0, 300.0),
                ),
            ),
            ScheduleStep(date(2014, 2, 1)),
        )
    )
    policy = InjectionTransferPolicy(
        InjectionTransfer("D", "R", 0.15, date(2014, 1, 1), date(2014, 2, 1)), "T"
    )

    plan = policy.plan(state, baseline)

    rates = {c.well: c.surface_rate for c in plan.steps[0].injectors}
    assert rates == {"D": 0.0, "R": 200.0}


def test_producers_and_other_injectors_are_untouched(model_y_case: Case) -> None:
    scenario = make_scenario(3, "4", "32", 0.15)
    _state, baseline, candidate, _text = _plan(model_y_case, scenario)

    pair = {"4", "32"}
    base_steps = {step.date: step for step in baseline.canonical().steps}
    for step in candidate.canonical().steps:
        base = base_steps[step.date]
        assert step.producers == base.producers
        assert tuple(c for c in step.injectors if c.well not in pair) == tuple(
            c for c in base.injectors if c.well not in pair
        )


def test_transfer_starts_at_the_first_management_step(model_y_case: Case) -> None:
    # Скважина 28 в опорной колоде впервые трогается только 01.06.2014,
    # но воздействие обязано действовать с начала окна.
    scenario = make_scenario(4, "28", "7", 0.15)
    _state, _baseline, candidate, _text = _plan(model_y_case, scenario)

    first = candidate.canonical().steps[0]
    controlled = {c.well for c in first.injectors}

    assert first.date == model_y_case.management_start_date
    assert {"28", "7"} <= controlled


# --- ворота ----------------------------------------------------------------


def _gate(report, gate_id: str):
    return next(g for g in report.experiment if g.gate_id == gate_id)


def test_valid_scenario_passes_every_gate(model_y_case: Case) -> None:
    scenario = make_scenario(2, "19", "41", 0.15)
    state, baseline, candidate, _text = _plan(model_y_case, scenario)

    report = validate_scenario(
        scenario,
        case=model_y_case,
        state=state,
        baseline=baseline,
        candidate=candidate,
        base_report=validate_schedule(candidate, state, model_y_case),
    )

    assert report.is_valid, [g.gate_id for g in report.failed]


def test_cross_block_pair_is_rejected(model_y_case: Case) -> None:
    # 19 из блока 2, 4 из блока 3.
    assert block_of_injector("19").block_id != block_of_injector("4").block_id
    scenario = Scenario("EXP001_CROSS", 2, "19", "4", 0.15)
    state, baseline, candidate, _text = _plan(model_y_case, scenario)

    report = validate_scenario(
        scenario,
        case=model_y_case,
        state=state,
        baseline=baseline,
        candidate=candidate,
        base_report=validate_schedule(candidate, state, model_y_case),
    )

    assert not report.is_valid
    assert _gate(report, "transfer_pair_valid").status is GateStatus.FAIL


def test_producer_as_donor_is_rejected(model_y_case: Case) -> None:
    scenario = Scenario("EXP001_BADROLE", 2, "25", "41", 0.15)
    state, baseline, _candidate, _text = _plan(model_y_case, baseline_scenario())

    report = validate_scenario(
        scenario,
        case=model_y_case,
        state=state,
        baseline=baseline,
        candidate=baseline,
        base_report=validate_schedule(baseline, state, model_y_case),
    )

    assert not report.is_valid
    violations = _gate(report, "transfer_pair_valid").violations
    assert any("not an injector" in v or "screening block" in v for v in violations)


def test_broken_conservation_is_caught(model_y_case: Case) -> None:
    """Кандидат, у которого получатель не добрал перенесённое, обязан упасть."""

    from dataclasses import replace

    from aios.schedule.events import Schedule, ScheduleStep

    scenario = make_scenario(2, "19", "41", 0.15)
    state, baseline, candidate, _text = _plan(model_y_case, scenario)

    steps = []
    for step in candidate.canonical().steps:
        injectors = tuple(
            replace(c, surface_rate=(c.surface_rate or 0.0) - 5.0) if c.well == "41" else c
            for c in step.injectors
        )
        steps.append(ScheduleStep(step.date, step.producers, injectors))
    broken = Schedule(tuple(steps))

    report = validate_scenario(
        scenario,
        case=model_y_case,
        state=state,
        baseline=baseline,
        candidate=broken,
        base_report=validate_schedule(broken, state, model_y_case),
    )

    assert not report.is_valid
    assert _gate(report, "pair_injection_conserved").status is GateStatus.FAIL
