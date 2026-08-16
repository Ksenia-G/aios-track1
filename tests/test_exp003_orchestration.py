"""Сквозная проверка оркестровки EXP-003 без симулятора.

Стадии соединены между собой руками: лучшая доля из A уходит в квад B,
квад -- в масштабирование C и компенсацию D, а всё вместе -- в отбор
кандидатов и артефакты. Ошибка в этой склейке стоит получаса счёта, поэтому
она проверяется на подставных результатах, а не на живых прогонах.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from aios.chdd.runner import ChddResult
from aios.experiments import exp003_cli
from aios.experiments.exp003 import (
    BASELINE_NPV_MLN_RUB,
    BASELINE_SCHEDULE_SHA256,
    EXP002_WINNER_NPV_MLN_RUB,
    EXP002_WINNER_SCHEDULE_SHA256,
    Exp003Scenario,
    WaterAccounting,
)
from aios.experiments.exp003_runner import Exp003Result
from aios.opm.runner import OpmRunResult

BASE_OIL_KT = 213.69528122680654
BASE_LIQUID_KT = 1976.6091847774
BASE_INJECTION_K = 1962.9611875


def _chdd(npv: float, *, oil_kt: float, liquid_kt: float, injection_k: float) -> ChddResult:
    return ChddResult(
        calculator_version="7.0.2-negative-row-filter",
        provenance="NOT_OFFICIAL_PENDING_TNAV_REFERENCE",
        npv_mln_rub=npv,
        profitability_index=1.2,
        economic_month_count=23,
        months_by_year={"2014": 12, "2015": 11},
        start_date="2014-01-01",
        last_economic_month="2015-11",
        source_max_date="2015-11-01",
        oil_kt_total=oil_kt,
        liquid_kt_total=liquid_kt,
        injection_k_total=injection_k,
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
        raw={
            "annual": [
                {"year": 2014, "mainRevenueM": 3000.0, "opexM": 200.0, "fcfM": 600.0},
                {"year": 2015, "mainRevenueM": 2600.0, "opexM": 170.0, "fcfM": 470.0},
            ]
        },
    )


def _opm(tmp_path: Path) -> OpmRunResult:
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


def _fake_result(scenario: Exp003Scenario, tmp_path: Path, index: int) -> Exp003Result:
    """Правдоподобный результат: сильнее воздействие -- выше ЧДД и ниже закачка."""

    if scenario.scenario_id == "EXP003_R_BASELINE":
        npv, oil, injection, sha = (
            BASELINE_NPV_MLN_RUB,
            BASE_OIL_KT,
            BASE_INJECTION_K,
            BASELINE_SCHEDULE_SHA256,
        )
        pressure = {"start_bar": 250.0, "mean_bar": 240.0, "final_bar": 230.0, "min_bar": 229.0}
    elif scenario.scenario_id == "EXP003_R_EXP002_WINNER":
        npv, oil, injection, sha = (
            EXP002_WINNER_NPV_MLN_RUB,
            BASE_OIL_KT + 23.6,
            BASE_INJECTION_K - 54.7,
            EXP002_WINNER_SCHEDULE_SHA256,
        )
        pressure = {"start_bar": 250.0, "mean_bar": 226.5, "final_bar": 210.4, "min_bar": 209.0}
    else:
        npv = BASELINE_NPV_MLN_RUB + 40.0 + index
        oil = BASE_OIL_KT + 5.0 + index * 0.1
        injection = BASE_INJECTION_K - 10.0 - index
        sha = f"{index:064x}"
        pressure = {
            "start_bar": 250.0,
            "mean_bar": 240.0 - index * 0.5,
            "final_bar": 230.0 - index * 0.7,
            "min_bar": 228.0 - index * 0.7,
        }

    result = Exp003Result(scenario=scenario)
    result.simulation_status = "PASS"
    result.constraints_status = "PASS"
    result.chdd_status = "PASS"
    result.schedule_sha256 = sha
    result.opm_result = _opm(tmp_path)
    result.chdd_result = _chdd(npv, oil_kt=oil, liquid_kt=BASE_LIQUID_KT, injection_k=injection)
    result.pressure = pressure
    result.water = WaterAccounting(
        window_start="2014-01-01",
        produced_water_m3=1_700_000.0 + index,
        injected_water_m3=1_900_000.0 - index * 10.0,
        monthly_produced_water_m3=(1.0, 2.0),
        monthly_injected_water_m3=(3.0, 4.0),
        monthly_dates=("2014-02-01", "2014-03-01"),
    )
    result.decomposition = {"revenue_m": 5600.0, "opex_m": 370.0, "npv_m": npv}
    result.saturation = {
        "42": {"hit_count": 0, "max_wbhp_bar": 241.2, "headroom_bar": 58.8},
        "13": {"hit_count": 0, "max_wbhp_bar": 200.0, "headroom_bar": 100.0},
        "15": {"hit_count": 0, "max_wbhp_bar": 190.0, "headroom_bar": 110.0},
        "21": {"hit_count": 5, "max_wbhp_bar": 300.0, "headroom_bar": 0.0},
    }
    result.wall_seconds = 1.0
    return result


@pytest.fixture
def stubbed_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Подменить оба входа в симулятор и запомнить порядок сценариев."""

    seen: list[str] = []
    counter = {"n": 0}

    def one(scenario, case, **kwargs):
        counter["n"] += 1
        seen.append(scenario.scenario_id)
        return _fake_result(scenario, tmp_path, counter["n"])

    def batch(scenarios, case, *, on_result=None, **kwargs):
        results = []
        for scenario in scenarios:
            result = one(scenario, case)
            if on_result is not None:
                on_result(result)
            results.append(result)
        return results

    monkeypatch.setattr(exp003_cli, "run_exp003_scenario", one)
    monkeypatch.setattr(exp003_cli, "run_exp003_batch", batch)
    return seen


def _run(tmp_path: Path, model_y_case, extra: list[str] | None = None) -> tuple[int, Path]:
    output = tmp_path / "out"
    code = exp003_cli.main(
        [
            "--case",
            str(model_y_case_path()),
            "--output",
            str(output),
            "--runtime",
            str(tmp_path / "rt"),
            *(extra or []),
        ]
    )
    return code, output


def model_y_case_path() -> Path:
    return Path(__file__).resolve().parents[1] / "cases" / "model_y_validation.json"


def test_all_artifacts_are_written(tmp_path: Path, model_y_case, stubbed_run: list[str]) -> None:
    code, output = _run(tmp_path, model_y_case)

    assert code == 0
    for name in exp003_cli.OUTPUT_FILES:
        assert (output / name).is_file(), name


def test_stage_order_and_counts(tmp_path: Path, model_y_case, stubbed_run: list[str]) -> None:
    _code, _output = _run(tmp_path, model_y_case)

    assert stubbed_run[0] == "EXP003_R_BASELINE"
    assert stubbed_run[1] == "EXP003_R_EXP002_WINNER"
    stage_a = [s for s in stubbed_run if s.startswith("EXP003_A_")]
    scaled = [s for s in stubbed_run if s.startswith("EXP003_C_SCALE_")]
    comp = [s for s in stubbed_run if s.startswith("EXP003_D_COMP_")]
    quad = [s for s in stubbed_run if s.startswith("EXP003_B_REFINED_QUAD")]
    assert len(stage_a) == 15
    assert len(quad) == 1
    assert len(scaled) == 4
    assert len(comp) == 5
    # Квад строится после уточнения и раньше масштабирования.
    assert stubbed_run.index(quad[0]) > stubbed_run.index(stage_a[-1])
    assert stubbed_run.index(quad[0]) < stubbed_run.index(scaled[0])


def test_new_simulation_count_respects_the_budget(
    tmp_path: Path, model_y_case, stubbed_run: list[str]
) -> None:
    _code, output = _run(tmp_path, model_y_case)

    summary = json.loads((output / "summary.json").read_text("utf-8"))
    counts = summary["counts"]
    assert counts["reconciliation_simulations"] == 2
    assert counts["new_simulations"] <= exp003_cli.MAX_NEW_SIMULATIONS
    assert counts["total_simulations"] == counts["new_simulations"] + 2


def test_reconciliation_mismatch_blocks_before_any_stage(
    tmp_path: Path, model_y_case, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def wrong(scenario, case, **kwargs):
        seen.append(scenario.scenario_id)
        result = _fake_result(scenario, tmp_path, 1)
        result.chdd_result = _chdd(
            999.0, oil_kt=BASE_OIL_KT, liquid_kt=BASE_LIQUID_KT, injection_k=BASE_INJECTION_K
        )
        return result

    monkeypatch.setattr(exp003_cli, "run_exp003_scenario", wrong)

    code, output = _run(tmp_path, model_y_case)

    assert code == 2
    assert seen == ["EXP003_R_BASELINE"]
    assert not (output / "results.csv").exists()


def test_kt1_snapshot_is_machine_readable(
    tmp_path: Path, model_y_case, stubbed_run: list[str]
) -> None:
    _code, output = _run(tmp_path, model_y_case)

    snapshot = json.loads((output / "kt1_snapshot.json").read_text("utf-8"))
    assert snapshot["baseline"]["npv"] == pytest.approx(BASELINE_NPV_MLN_RUB)
    assert snapshot["exp002_best"]["npv"] == pytest.approx(EXP002_WINNER_NPV_MLN_RUB)
    assert snapshot["provenance"] == "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"
    assert snapshot["all_full_opm"] is True
    assert snapshot["simulation_count_total"] == len(stubbed_run)


def test_results_csv_is_ranked_by_npv(tmp_path: Path, model_y_case, stubbed_run: list[str]) -> None:
    _code, output = _run(tmp_path, model_y_case)

    with open(output / "results.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    values = [float(r["npv_mln_rub"]) for r in rows if r["npv_mln_rub"]]
    assert values == sorted(values, reverse=True)
    assert all(r["schedule_sha256"] for r in rows)


def test_pareto_csv_carries_the_three_objectives(
    tmp_path: Path, model_y_case, stubbed_run: list[str]
) -> None:
    _code, output = _run(tmp_path, model_y_case)

    with open(output / "pareto.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    for row in rows:
        assert row["npv_mln_rub"]
        assert row["delta_injection_m3"]
        assert row["field_mean_pressure_delta_bar"]


def test_experiment_output_is_not_a_submission_package(
    tmp_path: Path, model_y_case, stubbed_run: list[str]
) -> None:
    _code, output = _run(tmp_path, model_y_case)

    produced = {p.name for p in output.iterdir()}
    assert "claimed_npv.json" not in produced
    assert "validation_report.json" not in produced
    assert "run_manifest.json" not in produced
    assert "wells_schedule.inc" not in produced


def test_stage_a_csv_carries_refinement_classes(
    tmp_path: Path, model_y_case, stubbed_run: list[str]
) -> None:
    _code, output = _run(tmp_path, model_y_case)

    with open(output / "stage_a.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 15
    assert all(row["refinement_class"] for row in rows)
    assert {row["donor"] for row in rows} == {"41", "32", "7", "26"}


def test_compensation_csv_reports_saturation_status(
    tmp_path: Path, model_y_case, stubbed_run: list[str]
) -> None:
    _code, output = _run(tmp_path, model_y_case)

    with open(output / "compensation.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    for row in rows:
        assert row["saturation_status"] in {"HEADROOM_REMAINS", "BHP_SATURATED"}
        assert float(row["supplement_total_m3_per_day"]) > 0.0
        assert float(row["scheduled_added_injection_m3"]) > 0.0


def test_scaled_quad_csv_includes_the_unscaled_reference(
    tmp_path: Path, model_y_case, stubbed_run: list[str]
) -> None:
    _code, output = _run(tmp_path, model_y_case)

    with open(output / "scaled_quad.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    alphas = sorted(float(r["alpha"]) for r in rows)
    assert alphas == [0.40, 0.55, 0.70, 0.85, 1.00]


def test_summary_records_water_basis_and_unresolved_rule(
    tmp_path: Path, model_y_case, stubbed_run: list[str]
) -> None:
    _code, output = _run(tmp_path, model_y_case)

    summary = json.loads((output / "summary.json").read_text("utf-8"))
    assert summary["water_balance_status"] == "ORGANIZER_RULE_UNRESOLVED"
    assert "NOT voidage replacement" in summary["water_accounting"]["basis"]
    assert summary["provenance"] == "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"


def test_stale_artifacts_are_cleared_before_a_new_run(
    tmp_path: Path, model_y_case, stubbed_run: list[str]
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    stale = output / "kt1_snapshot.json"
    stale.write_text('{"stale": true}', encoding="utf-8")

    _code, _output = _run(tmp_path, model_y_case)

    payload: dict[str, Any] = json.loads(stale.read_text("utf-8"))
    assert "stale" not in payload
