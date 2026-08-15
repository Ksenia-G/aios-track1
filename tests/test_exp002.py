"""Focused checks for EXP-002 scenario construction and safeguards."""

from datetime import UTC, datetime

import pytest

from aios.cases import Case
from aios.experiments.exp002 import (
    CandidateType,
    Exp002Scenario,
    InteractionClass,
    TransferMode,
    TransferSpec,
    assert_non_overlapping,
    build_exp002_schedule,
    build_same_b3_combinations,
    classify_interaction,
    combined_scenario_id,
    decomposition_scenarios,
    run_exp002_scenario,
    saturation_for_well,
    stage_a_scenarios,
    validate_exp002_candidate,
)
from aios.opm.summary import SummaryData, SummaryReadError
from aios.pipeline.validation import read_baseline_inputs


def test_stage_a_upper_fraction_grid_is_exact_and_directional() -> None:
    scenarios = stage_a_scenarios()

    assert len(scenarios) == 42
    assert len({scenario.scenario_id for scenario in scenarios}) == 42
    assert scenarios[0].scenario_id == "EXP002_A_B3_32_TO_4_F40"
    assert scenarios[-1].scenario_id == "EXP002_A_B4_7_TO_42_F90"
    assert {transfer.fraction for scenario in scenarios for transfer in scenario.transfers} == {
        0.40,
        0.50,
        0.60,
        0.70,
        0.80,
        0.90,
    }


def test_combined_scenario_ids_are_deterministic() -> None:
    transfers = (TransferSpec(5, "26", "2", 0.9), TransferSpec(3, "32", "4", 0.7))

    assert combined_scenario_id("D", transfers) == "EXP002_D_B3_32_TO_4_F70__B5_26_TO_2_F90"


def test_overlapping_wells_are_rejected() -> None:
    with pytest.raises(ValueError, match="overlapping"):
        assert_non_overlapping((TransferSpec(3, "32", "4", 0.7), TransferSpec(3, "32", "21", 0.5)))


def test_same_b3_combinations_require_disjoint_wells() -> None:
    combos = build_same_b3_combinations(
        {
            ("32", "4"): TransferSpec(3, "32", "4", 0.7),
            ("13", "21"): TransferSpec(3, "13", "21", 0.6),
            ("32", "21"): TransferSpec(3, "32", "21", 0.8),
            ("13", "4"): TransferSpec(3, "13", "4", 0.5),
        }
    )

    assert [combo.scenario_id for combo in combos] == [
        "EXP002_D_B3_B3_13_TO_21_F60__B3_32_TO_4_F70",
        "EXP002_D_B3_B3_13_TO_4_F50__B3_32_TO_21_F80",
    ]


def test_multi_transfer_schedule_conserves_each_pair(model_y_case: Case) -> None:
    state, baseline, _history = read_baseline_inputs(model_y_case)
    scenario = Exp002Scenario(
        "TEST_COMBO",
        "D",
        CandidateType.ADMISSIBLE,
        (TransferSpec(3, "32", "4", 0.5), TransferSpec(5, "26", "2", 0.4)),
    )

    candidate, _text = build_exp002_schedule(scenario, model_y_case, state=state, baseline=baseline)
    report = validate_exp002_candidate(
        scenario, case=model_y_case, state=state, baseline=baseline, candidate=candidate
    )

    assert report["is_valid"]


def test_diagnostic_decomposition_is_not_rankable_as_admissible() -> None:
    scenarios = decomposition_scenarios()

    assert len(scenarios) == 9
    assert {scenario.candidate_type for scenario in scenarios} == {CandidateType.DIAGNOSTIC_ONLY}
    assert {scenario.transfers[0].mode for scenario in scenarios} == set(TransferMode)


def test_interaction_classification_uses_documented_tolerance() -> None:
    assert classify_interaction(0.30) is InteractionClass.POSITIVE_INTERACTION
    assert classify_interaction(0.10) is InteractionClass.NEAR_ADDITIVE
    assert classify_interaction(-0.30) is InteractionClass.NEGATIVE_INTERACTION


def test_bhp_saturation_metrics_count_limit_states() -> None:
    summary = SummaryData(
        start=datetime(2014, 1, 1, tzinfo=UTC),
        dates=(
            datetime(2013, 12, 1, tzinfo=UTC),
            datetime(2014, 1, 1, tzinfo=UTC),
            datetime(2014, 2, 1, tzinfo=UTC),
            datetime(2014, 3, 1, tzinfo=UTC),
        ),
        wells=("4",),
        units={"WBHP": "BARSA"},
        _well_index={("WBHP", "4"): 0},
        _field_index={},
        _params=((250.0,), (299.95,), (300.0,), (297.0,)),
    )

    sat = saturation_for_well(summary, "4", 300.0, "2014-01-01")

    assert sat.max_wbhp_bar == 300.0
    assert sat.hit_count == 2
    assert sat.first_hit_date == "2014-01-01"
    assert sat.last_hit_date == "2014-02-01"


def test_stale_result_is_replaced_on_failed_rerun(
    tmp_path, monkeypatch, model_y_case: Case
) -> None:
    state, baseline, history = read_baseline_inputs(model_y_case)
    scenario = Exp002Scenario(
        "STALE_TEST",
        "A",
        CandidateType.ADMISSIBLE,
        (TransferSpec(3, "32", "4", 0.4),),
    )
    scenario_dir = tmp_path / scenario.scenario_id
    scenario_dir.mkdir()
    (scenario_dir / "result.json").write_text('{"status":"PASS","npv_mln_rub":999.0}\n')

    def fail_prepare(*_args, **_kwargs):
        raise SummaryReadError("synthetic failure")

    monkeypatch.setattr("aios.experiments.exp002.prepare_workspace", fail_prepare)
    result = run_exp002_scenario(
        scenario,
        model_y_case,
        runtime_root=tmp_path,
        state=state,
        baseline=baseline,
        history_text=history,
    )

    assert not result.is_pass
    text = (scenario_dir / "result.json").read_text()
    assert '"status": "FAIL"' in text
    assert "999.0" not in text
