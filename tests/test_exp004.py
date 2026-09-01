"""EXP-004: воздействия на добычу и закачку, метрики сценария, манифесты.

Симулятор не запускается. Проверяется то, что обязано быть верным до счёта:
уставка не может превысить экономический потолок, множитель применяется к
опорным значениям, порядок наложения воздействий фиксирован, а заказанное
не путается с полученным.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aios.cases import Case
from aios.experiments.exp002 import TransferSpec
from aios.experiments.exp003 import SupplementalInjection
from aios.experiments.exp004 import (
    WLPR_HARD_CEILING,
    Exp004PolicyError,
    Exp004Scenario,
    Family,
    InjectionScale,
    ProducerUplift,
    assert_unique,
    baseline_scenario,
    build_exp004_schedule,
    exp003_winner_scenario,
    exp003_winner_transfers,
    injection_scale_scenarios,
    joint_scenarios,
    producer_uplift_scenarios,
)
from aios.experiments.exp004_runner import RUNTIME_PLACEHOLDER, _portable
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule.generator import schedule_sha256
from aios.schedule.validator import validate_schedule

BASELINE_SHA = "7662d10495796ace6b0d20df404ca68932b2894f367de2062c232cbe3e01c399"
EXP003_BEST_SHA = "b53dc873efadcc1f33de49d755f1c97ec8e0992b5d0685bed929e46b1eb58112"


def _plan(case: Case, scenario: Exp004Scenario):
    state, baseline, _history = read_baseline_inputs(case)
    schedule, text = build_exp004_schedule(scenario, case, state=state, baseline=baseline)
    return state, baseline, schedule, text


# --- потолок уставки -------------------------------------------------------


def test_producer_uplift_clips_at_the_economic_ceiling() -> None:
    uplift = ProducerUplift(10.0)

    assert uplift.target(100.0) == pytest.approx(WLPR_HARD_CEILING)
    assert uplift.target(20.0) == pytest.approx(200.0)


def test_ceiling_above_the_economic_limit_is_rejected() -> None:
    with pytest.raises(Exp004PolicyError, match="exceeds the economic limit"):
        ProducerUplift(1.5, ceiling=600.0)


def test_non_positive_uplift_is_rejected() -> None:
    with pytest.raises(Exp004PolicyError, match="positive"):
        ProducerUplift(0.0)


def test_negative_injection_scale_is_rejected() -> None:
    with pytest.raises(Exp004PolicyError, match="non-negative"):
        InjectionScale(-0.5)


def test_schedule_never_exceeds_the_ceiling(model_y_case: Case) -> None:
    scenario = Exp004Scenario("T", Family.PRODUCER, producer_uplift=ProducerUplift(50.0))

    _state, _baseline, schedule, _text = _plan(model_y_case, scenario)

    for step in schedule.canonical().steps:
        for control in step.producers:
            assert control.liquid_rate is not None
            assert control.liquid_rate <= WLPR_HARD_CEILING


def test_ceiling_schedule_still_passes_track1_gates(model_y_case: Case) -> None:
    scenario = Exp004Scenario("T", Family.PRODUCER, producer_uplift=ProducerUplift(5.0))
    state, _baseline, schedule, _text = _plan(model_y_case, scenario)

    report = validate_schedule(schedule, state, model_y_case)

    assert report.is_valid, [g.gate_id for g in report.failed]


# --- применение воздействий ------------------------------------------------


def test_baseline_scenario_reproduces_the_accepted_file(model_y_case: Case) -> None:
    _s, _b, _sched, text = _plan(model_y_case, baseline_scenario())

    assert schedule_sha256(text) == BASELINE_SHA


def test_exp003_winner_reproduces_its_accepted_file(model_y_case: Case) -> None:
    _s, _b, _sched, text = _plan(model_y_case, exp003_winner_scenario())

    assert schedule_sha256(text) == EXP003_BEST_SHA


def test_producer_uplift_multiplies_the_baseline_target(model_y_case: Case) -> None:
    scenario = Exp004Scenario("T", Family.PRODUCER, producer_uplift=ProducerUplift(2.0))
    _state, baseline, schedule, _text = _plan(model_y_case, scenario)

    base_first = {c.well: c.liquid_rate for c in baseline.canonical().steps[0].producers}
    cand_first = {c.well: c.liquid_rate for c in schedule.canonical().steps[0].producers}

    assert cand_first
    for well, rate in cand_first.items():
        if base_first.get(well):
            assert rate == pytest.approx(min(base_first[well] * 2.0, WLPR_HARD_CEILING))


def test_injection_scale_multiplies_every_injector(model_y_case: Case) -> None:
    scenario = Exp004Scenario("T", Family.INJECTION, injection_scale=InjectionScale(1.5))
    _state, baseline, schedule, _text = _plan(model_y_case, scenario)

    base_first = {c.well: c.surface_rate for c in baseline.canonical().steps[0].injectors}
    cand_first = {c.well: c.surface_rate for c in schedule.canonical().steps[0].injectors}

    for well, rate in base_first.items():
        assert cand_first[well] == pytest.approx(rate * 1.5)


def test_producer_uplift_leaves_injectors_alone(model_y_case: Case) -> None:
    scenario = Exp004Scenario("T", Family.PRODUCER, producer_uplift=ProducerUplift(3.0))
    _state, baseline, schedule, _text = _plan(model_y_case, scenario)

    base = {s.date: s.injectors for s in baseline.canonical().steps}
    for step in schedule.canonical().steps:
        assert step.injectors == base[step.date]


def test_injection_scale_leaves_producers_alone(model_y_case: Case) -> None:
    scenario = Exp004Scenario("T", Family.INJECTION, injection_scale=InjectionScale(0.8))
    _state, baseline, schedule, _text = _plan(model_y_case, scenario)

    base = {s.date: s.producers for s in baseline.canonical().steps}
    for step in schedule.canonical().steps:
        assert step.producers == base[step.date]


def test_transfer_then_scale_then_supplement_order_is_fixed(model_y_case: Case) -> None:
    """Порядок наложения задан и воспроизводим."""

    scenario = Exp004Scenario(
        "T",
        Family.JOINT,
        injection_scale=InjectionScale(2.0),
        transfers=(TransferSpec(2, "41", "19", 0.5),),
        supplements=(SupplementalInjection("42", 50.0),),
    )
    _state, baseline, schedule, _text = _plan(model_y_case, scenario)

    base_first = {c.well: c.surface_rate for c in baseline.canonical().steps[0].injectors}
    cand_first = {c.well: c.surface_rate for c in schedule.canonical().steps[0].injectors}
    donor, receiver = base_first["41"], base_first["19"]
    # перенос -> масштаб -> добавка
    assert cand_first["41"] == pytest.approx((donor - 0.5 * donor) * 2.0)
    assert cand_first["19"] == pytest.approx((receiver + 0.5 * donor) * 2.0)
    assert cand_first["42"] == pytest.approx(base_first["42"] * 2.0 + 50.0)


def test_supplement_on_a_producer_is_rejected(model_y_case: Case) -> None:
    scenario = Exp004Scenario("T", Family.JOINT, supplements=(SupplementalInjection("6", 20.0),))

    with pytest.raises(Exp004PolicyError, match="not a known injector"):
        _plan(model_y_case, scenario)


def test_zero_injection_scale_shuts_nothing_but_zeroes_targets(model_y_case: Case) -> None:
    scenario = Exp004Scenario("T", Family.INJECTION, injection_scale=InjectionScale(0.0))
    _state, _baseline, schedule, _text = _plan(model_y_case, scenario)

    for control in schedule.canonical().steps[0].injectors:
        assert control.surface_rate == pytest.approx(0.0)
        assert control.status.value == "OPEN"


# --- перечни сценариев -----------------------------------------------------


def test_scenario_ids_are_deterministic_and_unique() -> None:
    first = injection_scale_scenarios((0.6, 0.9, 1.5))
    second = injection_scale_scenarios((0.6, 0.9, 1.5))

    assert [s.scenario_id for s in first] == [s.scenario_id for s in second]
    assert [s.scenario_id for s in first] == [
        "EXP004_A_INJ_060",
        "EXP004_A_INJ_090",
        "EXP004_A_INJ_150",
    ]
    assert_unique(first)


def test_producer_scenario_ids() -> None:
    ids = [s.scenario_id for s in producer_uplift_scenarios((1.25, 2.0, 5.0))]

    assert ids == ["EXP004_B_PROD_125", "EXP004_B_PROD_200", "EXP004_B_PROD_500"]


def test_joint_grid_is_the_cross_product() -> None:
    scenarios = joint_scenarios((2.0, 3.0), (1.2, 1.5))

    assert len(scenarios) == 4
    assert [s.scenario_id for s in scenarios] == [
        "EXP004_C_JOINT_P200_I120",
        "EXP004_C_JOINT_P200_I150",
        "EXP004_C_JOINT_P300_I120",
        "EXP004_C_JOINT_P300_I150",
    ]
    assert_unique(scenarios)


def test_overlay_scenarios_get_a_distinct_suffix() -> None:
    plain = joint_scenarios((2.0,), (1.5,))
    overlay = joint_scenarios(
        (2.0,), (1.5,), transfers=exp003_winner_transfers(), prefix="EXP004_C_OVL"
    )

    assert plain[0].scenario_id != overlay[0].scenario_id
    assert overlay[0].scenario_id.endswith("_T")


def test_duplicate_scenario_ids_are_rejected() -> None:
    duplicated = (*joint_scenarios((2.0,), (1.5,)), *joint_scenarios((2.0,), (1.5,)))

    with pytest.raises(ValueError, match="duplicate scenario ids"):
        assert_unique(duplicated)


def test_finer_than_a_hundredth_scale_is_rejected() -> None:
    with pytest.raises(ValueError, match="finer than a hundredth"):
        injection_scale_scenarios((1.2345,))


def test_baseline_scenario_is_recognised_as_untouched() -> None:
    assert baseline_scenario().is_baseline
    assert not exp003_winner_scenario().is_baseline


# --- переносимость записанного результата ----------------------------------


def test_recorded_result_carries_no_host_path(tmp_path) -> None:
    """Абсолютный путь машины, где считали, не должен попадать в артефакт."""

    payload = {
        "command": ["docker", "run", "-v", f"{tmp_path}/S1/workspace:/data"],
        "nested": [{"log": str(tmp_path / "S1" / "flow.log")}],
        "exit_code": 0,
    }

    scrubbed = _portable(payload, tmp_path)

    assert str(tmp_path) not in json.dumps(scrubbed)
    assert scrubbed["command"][3] == f"{RUNTIME_PLACEHOLDER}/S1/workspace:/data"
    assert scrubbed["nested"][0]["log"] == f"{RUNTIME_PLACEHOLDER}/S1/flow.log"
    assert scrubbed["exit_code"] == 0


def test_portability_scrub_without_a_root_is_a_no_op() -> None:
    payload = {"command": ["flow"]}

    assert _portable(payload, None) == payload


def test_published_artifacts_carry_no_host_path() -> None:
    published = sorted(Path("experiments/exp004").glob("*.json"))
    published += sorted(Path("experiments/exp004/comparisons").glob("*.json"))

    assert published
    for path in published:
        assert "/Users/" not in path.read_text(encoding="utf-8"), path
