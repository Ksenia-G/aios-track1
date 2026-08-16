"""Демонстрация КТ1: снимок собирается из свидетельств и падает закрыто.

Здесь и только здесь живут ожидаемые числа. Демонстрация обязана показывать
то, что лежит в отслеживаемых артефактах; тест обязан ловить расхождение
между артефактами и проверенным результатом. Если поменять местами эти две
роли, доклад начнёт подтверждать сам себя.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from aios.demo import (
    PRESSURE_FLOOR_STATUS,
    PROVENANCE,
    SOURCE_ARTIFACTS,
    WATER_BALANCE_STATUS,
    EvidenceError,
    build_snapshot,
    serialize,
    snapshot_path,
)
from aios.demo.cli import main, render

REPO_ROOT = Path(__file__).resolve().parents[1]

BASELINE_NPV = 1030.8776958837277
EXP001_BEST_NPV = 1051.6400268038235
EXP002_BEST_NPV = 1166.9843692054437
EXP003_BEST_NPV = 1190.5933863822445
EXP003_BEST_DELTA = 159.71569049851678
EXP003_BEST_DELTA_PCT = 15.493175488834229
BEST_SCHEDULE_SHA = "b53dc873efadcc1f33de49d755f1c97ec8e0992b5d0685bed929e46b1eb58112"
BEST_SCENARIO_ID = "EXP003_D_COMP_ADD_42_100"

CONSERVATIVE_SCENARIO_ID = "EXP003_C_SCALE_F55"
CONSERVATIVE_NPV = 1121.1011349437285
CONSERVATIVE_INJECTION_DELTA = -444.3125
CONSERVATIVE_MEAN_FPR = -6.601696650187175

LOW_PRESSURE_SCENARIO_ID = "EXP003_A_B4_7_TO_42_F92p5"

BASELINE_INJECTION_M3 = 1962961.1875
BEST_INJECTION_M3 = 1972666.53125
BEST_INJECTION_DELTA = 9705.34375
BEST_INJECTION_DEVIATION_PCT = 0.49442361936664625
WELL42_MAX_WBHP = 288.5408020019531
BHP_CAP = 300.0


@pytest.fixture
def snapshot():
    return build_snapshot(REPO_ROOT)


@pytest.fixture
def evidence_copy(tmp_path: Path) -> Path:
    """Изолированная копия свидетельств, которую тест может портить."""

    root = tmp_path / "repo"
    (root / "experiments").mkdir(parents=True)
    for name in ("exp001", "exp002", "exp003"):
        shutil.copytree(REPO_ROOT / "experiments" / name, root / "experiments" / name)
    return root


# --- 1. снимок собирается --------------------------------------------------


def test_canonical_snapshot_builds(snapshot) -> None:
    assert snapshot.provenance == PROVENANCE
    assert len(snapshot.progression) == 4
    assert snapshot.source_artifacts == SOURCE_ARTIFACTS


# --- 2..5. значения прогрессии --------------------------------------------


def test_baseline_value_is_loaded(snapshot) -> None:
    assert snapshot.baseline_npv_mln_rub == pytest.approx(BASELINE_NPV, abs=1e-12)
    assert snapshot.progression[0].npv_mln_rub == pytest.approx(BASELINE_NPV, abs=1e-12)
    assert snapshot.progression[0].delta_npv_mln_rub == pytest.approx(0.0, abs=1e-12)


def test_exp001_value_is_loaded(snapshot) -> None:
    entry = snapshot.progression[1]

    assert entry.stage == "EXP-001"
    assert entry.npv_mln_rub == pytest.approx(EXP001_BEST_NPV, abs=1e-12)
    assert entry.delta_npv_mln_rub == pytest.approx(EXP001_BEST_NPV - BASELINE_NPV, abs=1e-9)
    assert entry.delta_npv_pct == pytest.approx(2.014044052267241, abs=1e-9)
    assert entry.simulation_note == "70 candidate scenarios"


def test_exp002_value_is_loaded(snapshot) -> None:
    entry = snapshot.progression[2]

    assert entry.stage == "EXP-002"
    assert entry.npv_mln_rub == pytest.approx(EXP002_BEST_NPV, abs=1e-12)
    assert entry.delta_npv_mln_rub == pytest.approx(136.10667332171602, abs=1e-9)
    assert entry.delta_npv_pct == pytest.approx(13.202989439502572, abs=1e-9)
    assert entry.simulation_note == "62 candidate scenarios"


def test_exp003_value_is_loaded(snapshot) -> None:
    entry = snapshot.progression[3]

    assert entry.stage == "EXP-003"
    assert entry.npv_mln_rub == pytest.approx(EXP003_BEST_NPV, abs=1e-12)
    assert entry.delta_npv_mln_rub == pytest.approx(EXP003_BEST_DELTA, abs=1e-9)
    assert entry.delta_npv_pct == pytest.approx(EXP003_BEST_DELTA_PCT, abs=1e-9)
    # Счёт помечен точно: 25 кандидатов плюс 2 сверочных прогона.
    assert entry.simulation_note == "25 candidate scenarios (+2 reconciliation)"


# --- 6. SHA лучшего расписания --------------------------------------------


def test_best_schedule_sha_is_consistent(snapshot) -> None:
    assert snapshot.best.scenario_id == BEST_SCENARIO_ID
    assert snapshot.best.schedule_sha256 == BEST_SCHEDULE_SHA
    assert snapshot.best.npv_mln_rub == pytest.approx(EXP003_BEST_NPV, abs=1e-12)
    assert snapshot.best.simulation_status == "PASS"
    assert snapshot.best.constraints_status == "PASS"
    assert snapshot.best.chdd_status == "PASS"


def test_best_controls_match_the_verified_definition(snapshot) -> None:
    controls = {
        (c["block"], c["donor"], c["receiver"]): round(float(c["fraction"]), 6)
        for c in snapshot.best.controls
    }

    assert controls == {
        (2, "41", "19"): 0.35,
        (3, "32", "4"): 0.825,
        (4, "7", "42"): 0.975,
        (5, "26", "2"): 0.975,
    }
    assert snapshot.best.supplements == [{"added_rate_m3_per_day": 100.0, "well": "42"}]


# --- 7, 8. обязательные пометки -------------------------------------------


def test_provenance_is_preserved(snapshot) -> None:
    assert snapshot.provenance == "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"


def test_water_balance_status_is_preserved(snapshot) -> None:
    assert snapshot.water_balance_status == "ORGANIZER_RULE_UNRESOLVED"


def test_pressure_floor_is_declared_absent(snapshot) -> None:
    assert snapshot.pressure_floor_status == PRESSURE_FLOOR_STATUS


def test_limitation_banners_appear_in_the_rendered_output(snapshot) -> None:
    text = render(snapshot)

    assert PROVENANCE in text
    assert WATER_BALANCE_STATUS in text
    assert PRESSURE_FLOOR_STATUS in text


def test_rendering_never_claims_an_official_or_global_result(snapshot) -> None:
    text = render(snapshot).lower()

    assert "not a global optimum" in text
    assert "not an official result" in text
    assert "not an organizer-confirmed npv" in text


# --- 9, 10, 11. закрытый отказ --------------------------------------------


def test_missing_source_artifact_fails_closed(evidence_copy: Path) -> None:
    (evidence_copy / "experiments" / "exp003" / "results.csv").unlink()

    with pytest.raises(EvidenceError, match="missing"):
        build_snapshot(evidence_copy)


def test_malformed_json_fails_closed(evidence_copy: Path) -> None:
    (evidence_copy / "experiments" / "exp003" / "kt1_snapshot.json").write_text(
        "{not json", encoding="utf-8"
    )

    with pytest.raises(EvidenceError, match="not readable JSON"):
        build_snapshot(evidence_copy)


def test_mismatched_source_value_fails_closed(evidence_copy: Path) -> None:
    path = evidence_copy / "experiments" / "exp003" / "kt1_snapshot.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["baseline"]["npv"] = 999.0
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvidenceError, match="sources disagree"):
        build_snapshot(evidence_copy)


def test_wrong_provenance_fails_closed(evidence_copy: Path) -> None:
    path = evidence_copy / "experiments" / "exp003" / "summary.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["provenance"] = "OFFICIAL"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvidenceError, match="provenance"):
        build_snapshot(evidence_copy)


def test_wrong_water_status_fails_closed(evidence_copy: Path) -> None:
    path = evidence_copy / "experiments" / "exp003" / "summary.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["water_balance_status"] = "RESOLVED"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvidenceError, match="water balance status"):
        build_snapshot(evidence_copy)


def test_wrong_scenario_binding_fails_closed(evidence_copy: Path) -> None:
    path = evidence_copy / "experiments" / "exp003" / "kt1_snapshot.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["exp003_max_npv"]["schedule_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvidenceError, match="schedule SHA"):
        build_snapshot(evidence_copy)


def test_failed_candidate_status_fails_closed(evidence_copy: Path) -> None:
    path = evidence_copy / "experiments" / "exp003" / "results.csv"
    lines = path.read_text("utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(BEST_SCENARIO_ID + ","):
            lines[index] = line.replace("PASS,PASS,PASS", "PASS,PASS,FAIL", 1)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(EvidenceError, match="chdd_status"):
        build_snapshot(evidence_copy)


def test_cli_returns_nonzero_when_evidence_is_missing(evidence_copy: Path) -> None:
    shutil.rmtree(evidence_copy / "experiments" / "exp001")

    assert main(["--root", str(evidence_copy)]) == 2


# --- 12, 13. детерминизм ---------------------------------------------------


def test_snapshot_serialization_is_deterministic(snapshot) -> None:
    again = build_snapshot(REPO_ROOT)

    assert serialize(snapshot) == serialize(again)
    assert serialize(snapshot).endswith("\n")


def test_terminal_rendering_is_deterministic(snapshot) -> None:
    assert render(snapshot) == render(build_snapshot(REPO_ROOT))


def test_tracked_snapshot_matches_the_canonical_evidence(snapshot) -> None:
    """Записанный артефакт не должен устареть относительно источников."""

    stored = snapshot_path(REPO_ROOT)

    assert stored.is_file()
    assert stored.read_text(encoding="utf-8") == serialize(snapshot)


# --- 14. без абсолютных путей ---------------------------------------------


def test_no_absolute_paths_in_demo_snapshot() -> None:
    text = snapshot_path(REPO_ROOT).read_text(encoding="utf-8")

    for marker in ("/Users/", "/Volumes/", "/home/", "aios-kt1-demo", "worktrees"):
        assert marker not in text, marker


def test_no_absolute_paths_in_rendered_output(snapshot) -> None:
    text = render(snapshot)

    for marker in ("/Users/", "/Volumes/", "/home/"):
        assert marker not in text, marker


# --- 15. запасные режимы из канонических свидетельств ---------------------


def test_conservative_mode_is_loaded_from_evidence(snapshot) -> None:
    candidate = snapshot.conservative

    assert candidate.scenario_id == CONSERVATIVE_SCENARIO_ID
    assert candidate.npv_mln_rub == pytest.approx(CONSERVATIVE_NPV, abs=1e-12)
    assert candidate.delta_injection_m3 == pytest.approx(CONSERVATIVE_INJECTION_DELTA, abs=1e-9)
    assert candidate.injection_deviation_pct == pytest.approx(0.022634808208606007, abs=1e-12)
    assert candidate.mean_pressure_delta_bar == pytest.approx(CONSERVATIVE_MEAN_FPR, abs=1e-12)
    assert candidate.simulation_status == "PASS"


def test_low_pressure_mode_is_loaded_from_evidence(snapshot) -> None:
    candidate = snapshot.low_pressure

    assert candidate.scenario_id == LOW_PRESSURE_SCENARIO_ID
    assert candidate.npv_mln_rub == pytest.approx(1077.8911815092647, abs=1e-12)
    assert candidate.mean_pressure_delta_bar == pytest.approx(-1.8457228342692058, abs=1e-12)
    # Наименьшее по модулю отклонение давления среди трёх режимов.
    assert abs(candidate.mean_pressure_delta_bar) < abs(snapshot.best.mean_pressure_delta_bar)
    assert abs(candidate.mean_pressure_delta_bar) < abs(
        snapshot.conservative.mean_pressure_delta_bar
    )


# --- робастность и экономика ----------------------------------------------


def test_robustness_metrics_come_from_evidence(snapshot) -> None:
    r = snapshot.robustness

    assert r["baseline_actual_injection_m3"] == pytest.approx(BASELINE_INJECTION_M3, abs=1e-6)
    assert r["candidate_actual_injection_m3"] == pytest.approx(BEST_INJECTION_M3, abs=1e-6)
    assert r["delta_actual_injection_m3"] == pytest.approx(BEST_INJECTION_DELTA, abs=1e-6)
    assert r["injection_deviation_pct"] == pytest.approx(BEST_INJECTION_DEVIATION_PCT, abs=1e-12)
    assert r["supplement_well_max_wbhp_bar"] == pytest.approx(WELL42_MAX_WBHP, abs=1e-9)
    assert r["bhp_cap_bar"] == pytest.approx(BHP_CAP, abs=1e-6)
    assert r["supplement_well_cap_bound_states"] == 0
    assert r["mean_field_pressure_delta_bar"] == pytest.approx(-10.196540832519531, abs=1e-12)
    assert r["final_field_pressure_delta_bar"] == pytest.approx(-14.259201049804688, abs=1e-12)


def test_economic_breakdown_comes_from_evidence(snapshot) -> None:
    e = snapshot.economics

    assert e["revenue_m"] == pytest.approx(790.0432140541989, abs=1e-9)
    assert e["deductions_m"] == pytest.approx(553.0302498379397, abs=1e-9)
    assert e["opex_m"] == pytest.approx(11.185637022514584, abs=1e-9)
    assert e["pump_capex_m"] == pytest.approx(1.85, abs=1e-9)
    assert e["injection_opex_m"] == pytest.approx(0.2911603125, abs=1e-9)
    assert e["npv_m"] == pytest.approx(EXP003_BEST_DELTA, abs=1e-9)


def test_pipeline_stage_list_is_shown(snapshot) -> None:
    text = render(snapshot)

    for stage in (
        "CASE",
        "HISTORY + CONSTRAINTS",
        "CONTROL PLAN",
        "wells_schedule.inc",
        "OPM FLOW",
        "TRAJECTORY",
        "CHDD",
        "CONSTRAINT AUDIT",
        "VERIFIED NPV",
    ):
        assert stage in text
    assert "Validation pipeline: PASS" in text


def test_demo_does_not_claim_live_recomputation(snapshot) -> None:
    text = render(snapshot)

    assert "previously completed full OPM Flow + CHDD runs" in text
    assert "not recomputed here" in text


def test_cli_succeeds_on_the_canonical_repository(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--root", str(REPO_ROOT)])
    captured = capsys.readouterr()

    assert code == 0
    assert "VERIFIED EXPERIMENT SNAPSHOT" in captured.out
    assert captured.err == ""
