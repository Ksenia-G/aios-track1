"""Механика экрана: изоляция прогонов, ранжирование, отсутствие повторного
использования устаревшего результата.

Симулятор не запускается: подменяются вызовы OPM и калькулятора.
"""

import json
from pathlib import Path

import pytest

from aios.cases import Case
from aios.chdd.runner import ChddError, ChddResult
from aios.experiments import (
    ScenarioRow,
    baseline_scenario,
    build_row,
    build_summary,
    make_scenario,
    rank_rows,
    run_scenario,
    write_results_csv,
)
from aios.experiments import runner as runner_module
from aios.experiments.runner import RESULT_FILENAME, ScenarioResult
from aios.experiments.sweep import RESULT_COLUMNS, best_per_block
from aios.opm.runner import OpmRunResult
from aios.pipeline import CLAIMED_NPV_FILENAME, SCHEDULE_FILENAME
from aios.pipeline.validation import read_baseline_inputs

BASELINE_NPV = 1030.8776958837277


def _chdd(npv: float) -> ChddResult:
    return ChddResult(
        calculator_version="7.0.2-negative-row-filter",
        provenance="NOT_OFFICIAL_PENDING_TNAV_REFERENCE",
        npv_mln_rub=npv,
        profitability_index=1.2,
        economic_month_count=23,
        months_by_year={"2014": 12, "2015": 11},
        yearly_profile=(),
        start_date="2014-01-01",
        last_economic_month="2015-11",
        source_max_date="2015-11-01",
        oil_kt_total=213.0,
        liquid_kt_total=1976.0,
        injection_k_total=1962.0,
        active_well_months=1127,
        pump_events=1,
        stop_events=0,
        start_events=0,
        conversion_events=0,
        excluded_negative_rows=0,
        duplicate_well_months=196,
        invalid_dates=0,
        ignored_blank_wells=0,
        warnings=(),
        raw={},
    )


def _result(scenario, npv: float | None) -> ScenarioResult:
    result = ScenarioResult(scenario=scenario)
    result.schedule_sha256 = "a" * 64
    if npv is not None:
        result.simulation_status = "PASS"
        result.constraints_status = "PASS"
        result.chdd_status = "PASS"
        result.chdd_result = _chdd(npv)
    return result


# --- ранжирование ----------------------------------------------------------


def test_ranking_is_by_delta_npv_descending_and_deterministic() -> None:
    baseline = _result(baseline_scenario(), BASELINE_NPV)
    rows = [
        build_row(_result(make_scenario(2, "19", "41", 0.15), BASELINE_NPV + 1.0), baseline),
        build_row(_result(make_scenario(2, "41", "19", 0.15), BASELINE_NPV - 2.0), baseline),
        build_row(_result(make_scenario(3, "4", "13", 0.15), BASELINE_NPV + 3.0), baseline),
    ]

    ranked = rank_rows(rows)

    assert [r.values["scenario_id"] for r in ranked] == [
        "EXP001_B3_4_TO_13_F15",
        "EXP001_B2_19_TO_41_F15",
        "EXP001_B2_41_TO_19_F15",
    ]
    assert rank_rows(list(reversed(rows))) == ranked or [
        r.values["scenario_id"] for r in rank_rows(list(reversed(rows)))
    ] == [r.values["scenario_id"] for r in ranked]


def test_ties_break_on_scenario_id() -> None:
    baseline = _result(baseline_scenario(), BASELINE_NPV)
    rows = [
        build_row(_result(make_scenario(5, "26", "2", 0.15), BASELINE_NPV + 1.0), baseline),
        build_row(_result(make_scenario(5, "2", "26", 0.15), BASELINE_NPV + 1.0), baseline),
    ]

    # При равном ΔЧДД порядок задаёт идентификатор сценария -- лишь бы он был
    # один и тот же при любом порядке входа.
    expected = sorted(r.values["scenario_id"] for r in rows)

    assert [r.values["scenario_id"] for r in rank_rows(rows)] == expected
    assert [r.values["scenario_id"] for r in rank_rows(list(reversed(rows)))] == expected


def test_failed_scenarios_sort_last_and_carry_no_number() -> None:
    baseline = _result(baseline_scenario(), BASELINE_NPV)
    failed = _result(make_scenario(4, "7", "28", 0.15), None)
    failed.notes.append("simulation: flow exited with code 1")
    rows = [
        build_row(failed, baseline),
        build_row(_result(make_scenario(4, "28", "7", 0.15), BASELINE_NPV - 5.0), baseline),
    ]

    ranked = rank_rows(rows)

    assert ranked[-1].values["scenario_id"] == "EXP001_B4_7_TO_28_F15"
    assert ranked[-1].values["npv_mln_rub"] is None
    assert ranked[-1].values["delta_npv_mln_rub"] is None


def test_delta_columns_are_relative_to_the_control_run() -> None:
    baseline = _result(baseline_scenario(), BASELINE_NPV)
    row = build_row(_result(make_scenario(2, "19", "41", 0.15), BASELINE_NPV + 2.5), baseline)

    assert row.values["delta_npv_mln_rub"] == pytest.approx(2.5)
    assert row.values["delta_npv_pct"] == pytest.approx(2.5 / BASELINE_NPV * 100.0)
    assert row.values["delta_oil_t"] == pytest.approx(0.0)


def test_summary_counts_and_best(tmp_path: Path) -> None:
    baseline = _result(baseline_scenario(), BASELINE_NPV)
    rows = [
        build_row(_result(make_scenario(2, "19", "41", 0.15), BASELINE_NPV + 1.0), baseline),
        build_row(_result(make_scenario(3, "4", "13", 0.15), BASELINE_NPV - 1.0), baseline),
        build_row(_result(make_scenario(4, "7", "28", 0.15), None), baseline),
    ]

    summary = build_summary(rows, baseline_npv=BASELINE_NPV, parallelism=4, wall_seconds=1.0)

    assert summary["scenario_count"] == 3
    assert summary["passed_count"] == 2
    assert summary["failed_count"] == 1
    assert summary["positive_npv_count"] == 1
    assert summary["best_scenario"] == "EXP001_B2_19_TO_41_F15"
    assert summary["parallelism"] == 4
    assert set(best_per_block(rows)) == {2, 3}


def test_results_csv_carries_every_required_column(tmp_path: Path) -> None:
    baseline = _result(baseline_scenario(), BASELINE_NPV)
    rows = [build_row(_result(make_scenario(2, "19", "41", 0.15), BASELINE_NPV + 1.0), baseline)]

    path = write_results_csv(rank_rows(rows), tmp_path / "results.csv")

    header = path.read_text("utf-8").splitlines()[0].split(",")
    assert header == list(RESULT_COLUMNS)
    for column in ("scenario_id", "delta_npv_mln_rub", "schedule_sha256", "wall_seconds"):
        assert column in header


# --- изоляция прогонов -----------------------------------------------------


def _stub_opm(tmp_path: Path) -> OpmRunResult:
    log = tmp_path / "flow.log"
    log.write_text("stub\n", encoding="utf-8")
    return OpmRunResult(
        exit_code=0,
        wall_seconds=1.0,
        command=("docker", "run", "stub"),
        log_path=log,
        output_files=(),
        report_steps=108,
        final_date="2015-12-01",
        warnings=(),
        warning_count=0,
        errors=(),
    )


def test_each_scenario_gets_its_own_runtime_directory(
    model_y_case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, baseline, history = read_baseline_inputs(model_y_case)
    monkeypatch.setattr(
        runner_module, "run_simulation", lambda case, workspace: (_stub_opm(tmp_path), object())
    )
    monkeypatch.setattr(runner_module, "build_chdd_input", lambda summary, case: object())
    monkeypatch.setattr(runner_module, "write_chdd_csv", lambda chdd_input, path: Path(path))
    monkeypatch.setattr(
        runner_module, "run_chdd", lambda case, chdd_input, *, source_file: _chdd(1.0)
    )
    monkeypatch.setattr(
        runner_module, "collect_metrics", lambda summary, *, wells, window_start: None
    )
    monkeypatch.setattr(runner_module, "prepare_workspace", lambda *a, **k: object())

    runtime = tmp_path / "runtime"
    for scenario in (make_scenario(2, "19", "41", 0.15), make_scenario(2, "41", "19", 0.15)):
        run_scenario(
            scenario,
            model_y_case,
            runtime_root=runtime,
            state=state,
            baseline=baseline,
            history_text=history,
        )

    directories = sorted(p.name for p in runtime.iterdir())
    assert directories == ["EXP001_B2_19_TO_41_F15", "EXP001_B2_41_TO_19_F15"]
    for name in directories:
        assert (runtime / name / RESULT_FILENAME).is_file()
        assert (runtime / name / SCHEDULE_FILENAME).is_file()


def test_experiment_output_never_looks_like_a_submission(
    model_y_case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, baseline, history = read_baseline_inputs(model_y_case)
    monkeypatch.setattr(
        runner_module, "run_simulation", lambda case, workspace: (_stub_opm(tmp_path), object())
    )
    monkeypatch.setattr(runner_module, "build_chdd_input", lambda summary, case: object())
    monkeypatch.setattr(runner_module, "write_chdd_csv", lambda chdd_input, path: Path(path))
    monkeypatch.setattr(
        runner_module, "run_chdd", lambda case, chdd_input, *, source_file: _chdd(1.0)
    )
    monkeypatch.setattr(
        runner_module, "collect_metrics", lambda summary, *, wells, window_start: None
    )
    monkeypatch.setattr(runner_module, "prepare_workspace", lambda *a, **k: object())

    runtime = tmp_path / "runtime"
    run_scenario(
        make_scenario(2, "19", "41", 0.15),
        model_y_case,
        runtime_root=runtime,
        state=state,
        baseline=baseline,
        history_text=history,
    )

    produced = {p.name for p in (runtime / "EXP001_B2_19_TO_41_F15").iterdir()}
    assert CLAIMED_NPV_FILENAME not in produced
    assert "validation_report.json" not in produced
    assert "run_manifest.json" not in produced


def test_failed_rerun_cannot_reuse_the_previous_number(
    model_y_case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, baseline, history = read_baseline_inputs(model_y_case)
    scenario = make_scenario(2, "19", "41", 0.15)
    runtime = tmp_path / "runtime"

    monkeypatch.setattr(
        runner_module, "run_simulation", lambda case, workspace: (_stub_opm(tmp_path), object())
    )
    monkeypatch.setattr(runner_module, "build_chdd_input", lambda summary, case: object())
    monkeypatch.setattr(runner_module, "write_chdd_csv", lambda chdd_input, path: Path(path))
    monkeypatch.setattr(runner_module, "prepare_workspace", lambda *a, **k: object())
    monkeypatch.setattr(
        runner_module, "collect_metrics", lambda summary, *, wells, window_start: None
    )
    monkeypatch.setattr(
        runner_module,
        "run_chdd",
        lambda case, chdd_input, *, source_file: _chdd(BASELINE_NPV + 42.0),
    )

    first = run_scenario(
        scenario,
        model_y_case,
        runtime_root=runtime,
        state=state,
        baseline=baseline,
        history_text=history,
    )
    assert first.is_pass
    stored = json.loads((runtime / scenario.scenario_id / RESULT_FILENAME).read_text("utf-8"))
    assert stored["economics"]["npv_mln_rub"] == pytest.approx(BASELINE_NPV + 42.0)

    def explode(case, chdd_input, *, source_file):
        raise ChddError("calculator refused the input")

    monkeypatch.setattr(runner_module, "run_chdd", explode)

    second = run_scenario(
        scenario,
        model_y_case,
        runtime_root=runtime,
        state=state,
        baseline=baseline,
        history_text=history,
    )

    assert not second.is_pass
    assert second.npv_mln_rub is None
    reread = json.loads((runtime / scenario.scenario_id / RESULT_FILENAME).read_text("utf-8"))
    assert reread["status"] == "FAIL"
    assert reread["economics"] is None
    assert not (runtime / scenario.scenario_id / "metrics.json").exists()


def test_gate_failure_stops_before_the_simulator(
    model_y_case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        runner_module,
        "run_simulation",
        lambda case, workspace: calls.append("ran") or (_stub_opm(tmp_path), object()),
    )
    state, baseline, history = read_baseline_inputs(model_y_case)
    # 19 из блока 2, 4 из блока 3 -- пара между блоками не проходит ворота.
    from aios.experiments.scenarios import Scenario

    scenario = Scenario("EXP001_CROSS_BLOCK", 2, "19", "4", 0.15)

    result = run_scenario(
        scenario,
        model_y_case,
        runtime_root=tmp_path / "runtime",
        state=state,
        baseline=baseline,
        history_text=history,
    )

    assert calls == []
    assert result.constraints_status == "FAIL"
    assert result.simulation_status == "FAIL"
    assert result.npv_mln_rub is None


def test_scenario_row_is_json_serialisable() -> None:
    baseline = _result(baseline_scenario(), BASELINE_NPV)
    row: ScenarioRow = build_row(
        _result(make_scenario(2, "19", "41", 0.15), BASELINE_NPV), baseline
    )

    assert json.loads(json.dumps(row.values)) == row.values
