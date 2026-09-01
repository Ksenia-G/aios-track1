"""Пакет сабмишена: схема файлов и поведение при провале.

Симулятор и официальный калькулятор здесь не запускаются -- проверяется
контракт выходных файлов и то, что провал не оставляет после себя заявку
на результат.
"""

import json
from pathlib import Path

from aios.cases import Case
from aios.chdd.runner import ChddResult
from aios.pipeline import (
    CLAIMED_NPV_FILENAME,
    RUN_MANIFEST_FILENAME,
    SCHEDULE_FILENAME,
    STAGING_DIRNAME,
    STATUS_FAILED,
    VALIDATION_REPORT_FILENAME,
    VERDICT_BLOCKED,
    PipelineReport,
    claimed_npv_payload,
    run_validation_pipeline,
)
from aios.schedule.policy import DEFAULT_POLICY_ID

VALIDATION_REPORT_KEYS = {
    "case_loaded",
    "history_loaded",
    "constraints_loaded",
    "schedule_valid",
    "simulation_pass",
    "chdd_pass",
    "all_hard_constraints_pass",
    "economic_months",
    "npv",
    "warnings",
}

CLAIMED_NPV_KEYS = {
    "case_id",
    "npv_mln_rub",
    "provenance",
    "calculator_version",
    "schedule_sha256",
    "simulation_status",
    "constraints_status",
}


def _stub_chdd_result() -> ChddResult:
    return ChddResult(
        calculator_version="7.0.2-negative-row-filter",
        provenance="NOT_OFFICIAL_PENDING_TNAV_REFERENCE",
        npv_mln_rub=1030.877696,
        profitability_index=1.2188,
        economic_month_count=23,
        months_by_year={"2014": 12, "2015": 11},
        yearly_profile=(),
        start_date="2014-01-01",
        last_economic_month="2015-11",
        source_max_date="2015-11-01",
        oil_kt_total=213.695,
        liquid_kt_total=1976.609,
        injection_k_total=1962.961,
        active_well_months=1127,
        pump_events=1,
        stop_events=0,
        start_events=0,
        conversion_events=0,
        excluded_negative_rows=0,
        duplicate_well_months=196,
        invalid_dates=0,
        ignored_blank_wells=0,
        warnings=("дубли «скважина-месяц»",),
        raw={},
    )


def _passing_report(case: Case) -> PipelineReport:
    return PipelineReport(
        case_path=Path("case.json"),
        policy_id=DEFAULT_POLICY_ID,
        case=case,
        case_loaded=True,
        history_loaded=True,
        constraints_loaded=True,
        schedule_valid=True,
        simulation_pass=True,
        chdd_pass=True,
        baseline_reconciled=True,
        schedule_sha256="a" * 64,
        chdd_result=_stub_chdd_result(),
    )


def test_validation_report_carries_the_required_fields(mini_case: Case) -> None:
    payload = _passing_report(mini_case).to_json()

    assert VALIDATION_REPORT_KEYS <= set(payload)
    assert payload["verdict"] == "READY"
    assert payload["npv_unit"] == "mln RUB"
    assert payload["economic_months"] == 23
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_claimed_npv_matches_the_submission_contract(mini_case: Case) -> None:
    payload = claimed_npv_payload(_passing_report(mini_case))

    assert set(payload) == CLAIMED_NPV_KEYS
    assert payload["case_id"] == "mini"
    assert payload["provenance"] == "NOT_OFFICIAL_PENDING_TNAV_REFERENCE"
    assert payload["simulation_status"] == "PASS"
    assert payload["constraints_status"] == "PASS"


def test_report_stays_blocked_while_any_gate_is_open(mini_case: Case) -> None:
    report = _passing_report(mini_case)
    report.chdd_pass = False

    assert report.verdict == VERDICT_BLOCKED


def test_failed_reconciliation_blocks_the_run(mini_case: Case) -> None:
    report = _passing_report(mini_case)
    report.baseline_reconciled = False

    assert report.verdict == VERDICT_BLOCKED


def test_unreadable_deck_blocks_before_the_simulator(mini_case_dir: Path) -> None:
    (mini_case_dir / "deck" / "INCLUDE" / "SCH.INC").unlink()

    outcome = run_validation_pipeline(
        mini_case_dir / "case.json",
        output_dir=mini_case_dir / "output",
        runtime_dir=mini_case_dir / "runtime",
    )

    assert outcome.verdict == VERDICT_BLOCKED
    report = json.loads((outcome.output_dir / VALIDATION_REPORT_FILENAME).read_text("utf-8"))
    assert report["case_loaded"] is True
    assert report["history_loaded"] is False
    assert report["simulation_pass"] is False
    assert any("schedule" in blocker for blocker in report["blockers"])


def test_blocked_run_writes_no_claim_and_removes_a_stale_one(mini_case_dir: Path) -> None:
    output_dir = mini_case_dir / "output"
    output_dir.mkdir()
    stale = output_dir / CLAIMED_NPV_FILENAME
    stale.write_text('{"npv_mln_rub": 999.0}\n', encoding="utf-8")
    (mini_case_dir / "deck" / "INCLUDE" / "SCH.INC").unlink()

    outcome = run_validation_pipeline(
        mini_case_dir / "case.json",
        output_dir=output_dir,
        runtime_dir=mini_case_dir / "runtime",
    )

    assert outcome.verdict == VERDICT_BLOCKED
    assert not stale.exists()
    assert (output_dir / VALIDATION_REPORT_FILENAME).is_file()
    assert (output_dir / RUN_MANIFEST_FILENAME).is_file()


def test_schedule_gate_failure_stops_before_the_simulator(
    mini_case_dir: Path, case_payload: dict
) -> None:
    # Опорная колода поднимает P1 до 120 м3/сут; кейс с потолком 110 обязан
    # остановить прогон на воротах, не потратив ни секунды счёта.
    case_payload["constraints"]["producer_wlpr_max"] = 110.0
    (mini_case_dir / "case.json").write_text(
        json.dumps(case_payload, ensure_ascii=False), encoding="utf-8"
    )

    outcome = run_validation_pipeline(
        mini_case_dir / "case.json",
        output_dir=mini_case_dir / "output",
        runtime_dir=mini_case_dir / "runtime",
    )

    assert outcome.verdict == VERDICT_BLOCKED
    report = json.loads((outcome.output_dir / VALIDATION_REPORT_FILENAME).read_text("utf-8"))
    assert report["history_loaded"] is True
    assert report["schedule_valid"] is False
    assert report["simulation"] is None
    assert any("rate_limits" in blocker for blocker in report["blockers"])

    manifest = json.loads((outcome.output_dir / RUN_MANIFEST_FILENAME).read_text("utf-8"))
    assert manifest["status"] == STATUS_FAILED
    assert manifest["inputs"]["case"]["sha256"] is not None
    assert manifest["inputs"]["deck_schedule"]["sha256"] is not None
    assert manifest["schedule_sha256"] == outcome.report.schedule_sha256
    # Расписание, не прошедшее ворота, остаётся в staging для разбора и
    # сабмишеном не публикуется.
    assert (mini_case_dir / "runtime" / STAGING_DIRNAME / SCHEDULE_FILENAME).is_file()
    assert not (outcome.output_dir / SCHEDULE_FILENAME).exists()
    assert not (outcome.output_dir / CLAIMED_NPV_FILENAME).exists()
