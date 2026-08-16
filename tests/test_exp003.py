"""EXP-003: сетки уточнения, масштабирование, компенсация, отбор кандидатов.

Симулятор и официальный калькулятор здесь не запускаются. Проверяется то,
что обязано быть верным до всякого счёта: состав сценариев, форма
расписания кандидата, точность ворот и правила отбора.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aios.cases import Case
from aios.experiments.exp002 import CandidateType
from aios.experiments.exp003 import (
    BASELINE_SCHEDULE_SHA256,
    EXP002_WINNER_SCHEDULE_SHA256,
    NEAR_BASELINE_INJECTION_PCT,
    SCALING_ALPHAS,
    STAGE_A_GRID,
    SUPPLEMENT_RATES,
    Exp003Policy,
    Exp003PolicyError,
    Exp003Scenario,
    KeyCandidate,
    RefinementClass,
    SupplementalInjection,
    build_exp003_schedule,
    classify_refinement,
    compensation_scenarios,
    dominates,
    exp002_winner_transfers,
    headroom_ranking,
    injection_deviation_pct,
    kt1_snapshot,
    nearest_by_injection,
    pareto_frontier,
    quantize_fraction,
    reconciliation_scenario,
    refined_quad_scenario,
    scaled_quad_scenarios,
    select_key_candidates,
    split_compensation_scenarios,
    stage_a_scenarios,
    transfer,
    water_accounting,
)
from aios.experiments.exp003_cli import _control_scenario
from aios.experiments.scenarios import fraction_tag
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule.generator import schedule_sha256

BASELINE_NPV = 1030.8776958837277
BASELINE_INJECTION = 1962961.1875


# --- сетки уточнения -------------------------------------------------------


def test_stage_a_grid_covers_the_four_winning_directions() -> None:
    scenarios = stage_a_scenarios()

    assert len(scenarios) == 15
    directions = {
        (s.transfers[0].block_id, s.transfers[0].donor, s.transfers[0].receiver) for s in scenarios
    }
    assert directions == set(STAGE_A_GRID)
    for key, fractions in STAGE_A_GRID.items():
        got = sorted(
            s.transfers[0].fraction
            for s in scenarios
            if (s.transfers[0].block_id, s.transfers[0].donor, s.transfers[0].receiver) == key
        )
        assert got == sorted(quantize_fraction(f) for f in fractions)


def test_stage_a_ids_are_unique_and_deterministic() -> None:
    first = [s.scenario_id for s in stage_a_scenarios()]
    second = [s.scenario_id for s in stage_a_scenarios()]

    assert first == second
    assert len(set(first)) == len(first)
    assert "EXP003_A_B3_32_TO_4_F82p5" in first


def test_stage_a_does_not_repeat_fractions_verified_in_exp002() -> None:
    verified = {(3, "32", "4"): {0.70, 0.80, 0.90}, (4, "7", "42"): {0.90}, (5, "26", "2"): {0.90}}
    for key, already in verified.items():
        assert already.isdisjoint(set(STAGE_A_GRID[key]))


@pytest.mark.parametrize(
    ("fraction", "tag"),
    [(0.825, "F82p5"), (0.875, "F87p5"), (0.925, "F92p5"), (0.4675, "F46p75"), (0.80, "F80")],
)
def test_half_percent_tags(fraction: float, tag: str) -> None:
    assert fraction_tag(fraction) == tag


# --- масштабирование -------------------------------------------------------


def test_scaled_quad_multiplies_every_fraction_by_alpha() -> None:
    base = refined_quad_scenario(
        {(2, "41", "19"): 0.45, (3, "32", "4"): 0.85, (4, "7", "42"): 0.95, (5, "26", "2"): 0.95}
    ).transfers

    scaled = scaled_quad_scenarios(base)

    assert len(scaled) == len(SCALING_ALPHAS)
    for alpha, scenario in zip(SCALING_ALPHAS, scaled, strict=True):
        assert len(scenario.transfers) == len(base)
        for original, item in zip(base, scenario.transfers, strict=True):
            assert item.fraction == pytest.approx(quantize_fraction(alpha * original.fraction))
            assert (item.block_id, item.donor, item.receiver) == (
                original.block_id,
                original.donor,
                original.receiver,
            )


def test_scaled_quad_ids_encode_alpha() -> None:
    base = exp002_winner_transfers()

    ids = [s.scenario_id for s in scaled_quad_scenarios(base)]

    assert ids == [
        "EXP003_C_SCALE_F40",
        "EXP003_C_SCALE_F55",
        "EXP003_C_SCALE_F70",
        "EXP003_C_SCALE_F85",
    ]
    assert len(set(ids)) == len(ids)


def test_scaling_that_leaves_the_open_interval_is_rejected() -> None:
    base = (transfer(3, "32", "4", 0.9),)

    with pytest.raises(ValueError, match="outside"):
        scaled_quad_scenarios(base, alphas=(1.5,))


# --- компенсация -----------------------------------------------------------


def test_compensation_scenario_ids_and_rates() -> None:
    base = exp002_winner_transfers()

    scenarios = compensation_scenarios(base)

    assert [s.scenario_id for s in scenarios] == [
        "EXP003_D_COMP_ADD_42_20",
        "EXP003_D_COMP_ADD_42_40",
        "EXP003_D_COMP_ADD_42_60",
        "EXP003_D_COMP_ADD_42_80",
        "EXP003_D_COMP_ADD_42_100",
    ]
    assert [s.supplements[0].added_rate_m3_per_day for s in scenarios] == list(SUPPLEMENT_RATES)
    assert all(s.transfers == base for s in scenarios)


def test_split_compensation_ids_are_order_independent() -> None:
    base = exp002_winner_transfers()

    first = split_compensation_scenarios(base, allocations=[(("42", 100.0), ("13", 50.0))])
    second = split_compensation_scenarios(base, allocations=[(("13", 50.0), ("42", 100.0))])

    assert first[0].scenario_id == second[0].scenario_id


def test_non_positive_supplement_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        SupplementalInjection("42", 0.0)
    with pytest.raises(ValueError, match="positive"):
        SupplementalInjection("42", -10.0)


# --- наложение на расписание ----------------------------------------------


def _plan(case: Case, scenario: Exp003Scenario):
    state, baseline, _history = read_baseline_inputs(case)
    schedule, text = build_exp003_schedule(scenario, case, state=state, baseline=baseline)
    return state, baseline, schedule, text


def test_reconciliation_scenarios_reproduce_the_accepted_files(model_y_case: Case) -> None:
    control_sha = schedule_sha256(_plan(model_y_case, _control_scenario())[3])
    winner_sha = schedule_sha256(_plan(model_y_case, reconciliation_scenario())[3])

    assert control_sha == BASELINE_SCHEDULE_SHA256
    assert winner_sha == EXP002_WINNER_SCHEDULE_SHA256


def test_supplement_adds_exactly_the_declared_rate(model_y_case: Case) -> None:
    base = exp002_winner_transfers()
    quad = Exp003Scenario("Q", "B", CandidateType.ADMISSIBLE, base)
    comp = Exp003Scenario(
        "C", "D", CandidateType.ADMISSIBLE, base, (SupplementalInjection("42", 60.0),)
    )

    _s, _b, quad_schedule, _t = _plan(model_y_case, quad)
    _s2, _b2, comp_schedule, _t2 = _plan(model_y_case, comp)

    quad_by_step = {
        step.date: {c.well: c.surface_rate for c in step.injectors}
        for step in quad_schedule.canonical().steps
    }
    for step in comp_schedule.canonical().steps:
        for control in step.injectors:
            expected = quad_by_step[step.date].get(control.well)
            if expected is None:
                continue
            bump = 60.0 if control.well == "42" else 0.0
            assert control.surface_rate == pytest.approx(expected + bump, abs=1e-9)


def test_supplement_never_touches_steps_before_the_window(model_y_case: Case) -> None:
    comp = Exp003Scenario(
        "C",
        "D",
        CandidateType.ADMISSIBLE,
        exp002_winner_transfers(),
        (SupplementalInjection("42", 100.0),),
    )

    _state, baseline, candidate, _text = _plan(model_y_case, comp)

    base_steps = {s.date: s for s in baseline.canonical().steps}
    for step in candidate.canonical().steps:
        assert step.date >= model_y_case.management_start_date
        assert step.producers == base_steps[step.date].producers


def test_supplement_on_an_unrelated_well_leaves_the_quad_alone(model_y_case: Case) -> None:
    base = exp002_winner_transfers()
    comp = Exp003Scenario(
        "C", "E", CandidateType.ADMISSIBLE, base, (SupplementalInjection("15", 50.0),)
    )

    state, baseline, candidate, _text = _plan(model_y_case, comp)

    # Сравниваются действующие уставки, а не записи шага: опорная колода
    # трогает скважину 15 не на каждом шаге, а добавка действует всё окно.
    from aios.experiments.exp002 import _effective_targets

    base_targets = _effective_targets(state, baseline, ["15"])
    cand_targets = _effective_targets(state, candidate, ["15"])

    assert set(base_targets) == set(cand_targets)
    for day, targets in base_targets.items():
        assert cand_targets[day]["15"] == pytest.approx(targets["15"] + 50.0, abs=1e-9)


def test_supplement_on_a_producer_is_rejected(model_y_case: Case) -> None:
    scenario = Exp003Scenario(
        "C",
        "D",
        CandidateType.ADMISSIBLE,
        exp002_winner_transfers(),
        (SupplementalInjection("6", 20.0),),
    )
    state, baseline, _s, _t = _plan(model_y_case, reconciliation_scenario())
    policy = Exp003Policy(scenario.transfers, scenario.supplements, "T")

    with pytest.raises(Exp003PolicyError, match="not an injector"):
        policy.plan(state, baseline, model_y_case)


def test_control_run_plans_the_untouched_baseline(model_y_case: Case) -> None:
    _state, baseline, candidate, _text = _plan(model_y_case, _control_scenario())

    assert candidate == baseline.canonical()


# --- классификация уточнения ----------------------------------------------


def test_bracketed_peak_is_recognised() -> None:
    points = [(0.75, 5.0, 0), (0.80, 9.0, 0), (0.85, 7.0, 0)]

    assert classify_refinement(points) is RefinementClass.PEAK_REGION_BRACKETED


def test_boundary_best_is_recognised() -> None:
    points = [(0.75, 5.0, 0), (0.85, 9.0, 0)]

    assert classify_refinement(points) is RefinementClass.BOUNDARY_BEST


def test_saturated_boundary_is_reported_as_constraint_saturated() -> None:
    points = [(0.75, 5.0, 0), (0.85, 9.0, 3)]

    assert classify_refinement(points) is RefinementClass.CONSTRAINT_SATURATED


def test_a_bracketed_peak_stays_bracketed_even_if_the_receiver_is_saturated() -> None:
    # Насыщение получателя -- контекст, а не приговор: максимум найден внутри
    # сетки, и это более сильное утверждение, чем «упёрлись».
    points = [(0.30, 5.0, 10), (0.35, 9.0, 15), (0.45, 8.0, 20)]

    assert classify_refinement(points) is RefinementClass.PEAK_REGION_BRACKETED


def test_decreasing_sequence_reports_the_first_point_as_local_best() -> None:
    points = [(0.30, 9.0, 0), (0.35, 5.0, 0), (0.45, 1.0, 0)]

    assert classify_refinement(points) is RefinementClass.LOCAL_BEST_TESTED


def test_single_point_is_insufficient() -> None:
    assert classify_refinement([(0.8, 1.0, 0)]) is RefinementClass.INSUFFICIENT_DATA


# --- отклонение по закачке -------------------------------------------------


def test_injection_deviation_is_relative_and_absolute() -> None:
    assert injection_deviation_pct(BASELINE_INJECTION, BASELINE_INJECTION) == pytest.approx(0.0)
    assert injection_deviation_pct(BASELINE_INJECTION * 1.01, BASELINE_INJECTION) == pytest.approx(
        1.0
    )
    # Знак не имеет значения: отклонение вниз так же далеко, как вверх.
    assert injection_deviation_pct(BASELINE_INJECTION * 0.99, BASELINE_INJECTION) == pytest.approx(
        1.0
    )


def test_injection_deviation_without_a_baseline_is_undefined() -> None:
    assert injection_deviation_pct(100.0, 0.0) is None


# --- Парето ----------------------------------------------------------------


def _row(scenario_id: str, npv: float, injection: float, pressure: float) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "npv_mln_rub": npv,
        "delta_injection_m3": injection,
        "field_mean_pressure_delta_bar": pressure,
        "injection_m3": BASELINE_INJECTION + injection,
    }


def test_dominance_requires_not_worse_everywhere_and_better_somewhere() -> None:
    assert dominates((10.0, 1.0, 1.0), (9.0, 1.0, 1.0))
    assert dominates((10.0, 1.0, 1.0), (10.0, 2.0, 1.0))
    assert not dominates((10.0, 1.0, 1.0), (10.0, 1.0, 1.0))
    assert not dominates((10.0, 3.0, 1.0), (9.0, 1.0, 1.0))


def test_pareto_frontier_drops_dominated_candidates() -> None:
    rows = [
        _row("best_npv", 1200.0, -50000.0, -13.0),
        _row("dominated", 1100.0, -60000.0, -14.0),
        _row("near_injection", 1050.0, -500.0, -1.0),
    ]

    frontier = {r["scenario_id"] for r in pareto_frontier(rows)}

    assert "dominated" not in frontier
    assert frontier == {"best_npv", "near_injection"}


def test_pareto_skips_rows_missing_an_objective() -> None:
    rows = [_row("full", 1200.0, -1.0, -1.0), {"scenario_id": "partial", "npv_mln_rub": 9999.0}]

    frontier = [r["scenario_id"] for r in pareto_frontier(rows)]

    assert frontier == ["full"]


# --- отбор ключевых кандидатов --------------------------------------------


def test_key_candidates_are_selected_on_their_own_axis() -> None:
    rows = [
        _row("max", 1200.0, -50000.0, -13.0),
        _row("near", 1100.0, BASELINE_INJECTION * 0.005, -6.0),
        _row("calm", 1050.0, -20000.0, -0.2),
    ]

    keys = select_key_candidates(rows, baseline_injection_m3=BASELINE_INJECTION)

    assert keys[KeyCandidate.MAX_NPV.value]["scenario_id"] == "max"
    assert keys[KeyCandidate.NEAR_BASELINE_INJECTION.value]["scenario_id"] == "near"
    assert keys[KeyCandidate.LOW_PRESSURE_DEVIATION.value]["scenario_id"] == "calm"


def test_near_baseline_injection_is_none_when_nothing_is_close() -> None:
    rows = [_row("far", 1200.0, -BASELINE_INJECTION * 0.5, -13.0)]

    keys = select_key_candidates(rows, baseline_injection_m3=BASELINE_INJECTION)

    assert keys[KeyCandidate.NEAR_BASELINE_INJECTION.value] is None
    assert NEAR_BASELINE_INJECTION_PCT == 1.0


def test_nearest_by_injection_is_reported_even_when_none_qualifies() -> None:
    rows = [
        _row("far", 1200.0, -BASELINE_INJECTION * 0.5, -13.0),
        _row("closer", 1100.0, -BASELINE_INJECTION * 0.05, -9.0),
    ]

    assert (
        nearest_by_injection(rows, baseline_injection_m3=BASELINE_INJECTION)["scenario_id"]
        == "closer"
    )


def test_headroom_ranking_excludes_saturated_and_used_wells() -> None:
    table = {
        "13": {"hit_count": 0, "headroom_bar": 90.0},
        "15": {"hit_count": 0, "headroom_bar": 120.0},
        "21": {"hit_count": 4, "headroom_bar": 0.0},
        "42": {"hit_count": 0, "headroom_bar": 58.0},
    }

    ranked = headroom_ranking(table, exclude=["42"])

    assert [well for well, _ in ranked] == ["15", "13"]


# --- снимок КТ1 ------------------------------------------------------------


def test_kt1_snapshot_schema() -> None:
    max_row = _row("max", 1200.0, -50000.0, -13.0)
    max_row["schedule_sha256"] = "a" * 64
    near_row = _row("near", 1100.0, -100.0, -6.0)
    near_row["schedule_sha256"] = "b" * 64

    snapshot = kt1_snapshot(
        baseline_npv=BASELINE_NPV,
        exp001_best_npv=1051.6400268038235,
        exp002_best_npv=1166.9843692054437,
        max_npv_row=max_row,
        near_baseline_row=near_row,
        simulation_count_total=31,
        all_full_opm=True,
        all_chdd_verified=True,
    )

    assert set(snapshot) >= {
        "baseline",
        "exp001_best",
        "exp002_best",
        "exp003_max_npv",
        "near_baseline_injection",
        "simulation_count_total",
        "all_full_opm",
        "all_chdd_verified",
        "provenance",
    }
    assert snapshot["provenance"] == "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"
    assert snapshot["baseline"]["npv"] == pytest.approx(BASELINE_NPV)
    assert snapshot["exp003_max_npv"]["delta"] == pytest.approx(1200.0 - BASELINE_NPV)
    assert snapshot["exp003_max_npv"]["delta_pct"] == pytest.approx(
        (1200.0 - BASELINE_NPV) / BASELINE_NPV * 100.0
    )
    assert snapshot["near_baseline_injection"]["injection_delta"] == pytest.approx(-100.0)
    assert json.loads(json.dumps(snapshot)) == snapshot


def test_kt1_snapshot_tolerates_a_missing_near_baseline_candidate() -> None:
    snapshot = kt1_snapshot(
        baseline_npv=BASELINE_NPV,
        exp001_best_npv=1051.0,
        exp002_best_npv=1166.0,
        max_npv_row=None,
        near_baseline_row=None,
        simulation_count_total=3,
        all_full_opm=False,
        all_chdd_verified=False,
    )

    assert snapshot["exp003_max_npv"] is None
    assert snapshot["near_baseline_injection"] is None
    assert snapshot["all_full_opm"] is False


# --- изоляция прогонов -----------------------------------------------------


def test_stale_scenario_directory_is_wiped_before_a_rerun(
    model_y_case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aios.experiments import exp003_runner

    runtime = tmp_path / "runtime"
    scenario = Exp003Scenario(
        "EXP003_STALE", "A", CandidateType.ADMISSIBLE, (transfer(3, "32", "4", 0.85),)
    )
    stale_dir = runtime / scenario.scenario_id
    stale_dir.mkdir(parents=True)
    (stale_dir / "result.json").write_text('{"npv": 9999}', encoding="utf-8")
    (stale_dir / "metrics.json").write_text("{}", encoding="utf-8")

    def explode(case, workspace):
        raise RuntimeError("simulator unavailable")

    monkeypatch.setattr(exp003_runner, "prepare_workspace", lambda *a, **k: object())
    monkeypatch.setattr(exp003_runner, "run_simulation", explode)
    state, baseline, history = read_baseline_inputs(model_y_case)

    result = exp003_runner.run_exp003_scenario(
        scenario,
        model_y_case,
        runtime_root=runtime,
        state=state,
        baseline=baseline,
        history_text=history,
    )

    assert not result.is_pass
    assert result.npv_mln_rub is None
    assert not (stale_dir / "metrics.json").exists()
    stored = json.loads((stale_dir / "result.json").read_text("utf-8"))
    assert stored["status"] == "FAIL"
    assert stored["economics"] is None


def test_water_accounting_is_labelled_as_surface_only() -> None:
    class FakeSummary:
        report_steps = 3

        def field_series(self, vector: str):
            return {"FWPT": (10.0, 30.0, 60.0), "FWIT": (20.0, 50.0, 90.0)}[vector]

        @property
        def dates(self):
            import datetime

            # Модельное время колоды -- календарь без часового пояса.
            start = datetime.datetime(2014, 1, 1)  # noqa: DTZ001
            return tuple(start + datetime.timedelta(days=30 * i) for i in range(3))

    accounting = water_accounting(FakeSummary(), "2014-01-01")

    assert accounting.produced_water_m3 == pytest.approx(50.0)
    assert accounting.injected_water_m3 == pytest.approx(70.0)
    assert accounting.injection_minus_production_m3 == pytest.approx(20.0)
    assert accounting.monthly_produced_water_m3 == (20.0, 30.0)
    assert "NOT voidage replacement" in accounting.to_json()["basis"]
