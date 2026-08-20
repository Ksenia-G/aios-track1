"""Закрытый отказ: провал прогона не оставляет действующего сабмишена.

Каждый тест начинается с уже лежащего на диске успешного пакета от прошлого
прогона -- именно та ситуация, в которой устаревшая заявка выглядит как
свежий результат. После провала ни один файл этого пакета не должен остаться
действующим.

Симулятор и официальный калькулятор здесь не запускаются: отказы вносятся
подменой соответствующих вызовов.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from aios.chdd.runner import ChddError
from aios.chdd.trajectory import ChddInput, TrajectoryStats
from aios.opm.runner import OpmRunResult
from aios.opm.summary import SummaryReadError
from aios.opm.workspace import prepare_workspace
from aios.pipeline import (
    CLAIMED_NPV_FILENAME,
    CONSTRAINT_REPORT_FILENAME,
    FINAL_ARTIFACTS,
    RUN_MANIFEST_FILENAME,
    SCHEDULE_FILENAME,
    STATUS_FAILED,
    VALIDATION_REPORT_FILENAME,
    VERDICT_BLOCKED,
    YEARLY_NPV_PROFILE_FILENAME,
    invalidate_package,
    run_validation_pipeline,
)
from aios.pipeline import validation as validation_module

STALE_NPV = 999.999999


def seed_previous_success(output_dir: Path) -> dict[str, Path]:
    """Разложить пакет «прошлого удачного прогона»."""

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {name: output_dir / name for name in FINAL_ARTIFACTS}
    paths[SCHEDULE_FILENAME].write_text("-- stale schedule\nDATES\n 01 'FEB' 2020 /\n/\n", "utf-8")
    paths[CLAIMED_NPV_FILENAME].write_text(
        json.dumps({"case_id": "mini", "npv_mln_rub": STALE_NPV, "simulation_status": "PASS"}),
        "utf-8",
    )
    paths[YEARLY_NPV_PROFILE_FILENAME].write_text(
        json.dumps({"status": "PASSED", "total_npv_mln_rub": STALE_NPV}), "utf-8"
    )
    paths[CONSTRAINT_REPORT_FILENAME].write_text(
        json.dumps({"status": "PASS", "realized_constraints_status": "PASS"}), "utf-8"
    )
    paths[VALIDATION_REPORT_FILENAME].write_text(
        json.dumps({"status": "PASSED", "verdict": "READY", "npv": STALE_NPV}), "utf-8"
    )
    paths[RUN_MANIFEST_FILENAME].write_text(
        json.dumps({"status": "PASSED", "verdict": "READY"}), "utf-8"
    )
    return paths


def assert_no_surviving_success(output_dir: Path) -> dict[str, Any]:
    """Убедиться, что действующего успешного пакета на диске нет."""

    assert not (output_dir / CLAIMED_NPV_FILENAME).exists()
    assert not (output_dir / SCHEDULE_FILENAME).exists()

    report = json.loads((output_dir / VALIDATION_REPORT_FILENAME).read_text("utf-8"))
    manifest = json.loads((output_dir / RUN_MANIFEST_FILENAME).read_text("utf-8"))
    assert report["status"] == STATUS_FAILED
    assert report["verdict"] == VERDICT_BLOCKED
    assert report["npv"] != STALE_NPV
    assert manifest["status"] == STATUS_FAILED
    return report


def _passing_opm_result(tmp_path: Path) -> OpmRunResult:
    log = tmp_path / "flow.log"
    log.write_text("stub run\n", encoding="utf-8")
    return OpmRunResult(
        exit_code=0,
        wall_seconds=1.0,
        command=("docker", "run", "stub"),
        log_path=log,
        output_files=("STUB.UNSMRY",),
        report_steps=3,
        final_date="2020-04-01",
        warnings=(),
        warning_count=0,
        errors=(),
    )


def _stub_chdd_input() -> ChddInput:
    return ChddInput(
        headers=("DATA", "well"),
        rows=(("2020-02-01", "P1"),),
        stats=TrajectoryStats(
            row_count=1,
            well_count=1,
            interval_count=1,
            date_min="2020-02-01",
            date_max="2020-02-01",
            economic_rows=1,
            pre_start_rows=0,
            max_wlpr=100.0,
            rows_over_wlpr_limit=0,
            negative_diff_counts={"WLPT_Diff": 0, "WOMT_Diff": 0, "WWIT_Diff": 0},
        ),
    )


def _run(mini_case_dir: Path):
    return run_validation_pipeline(
        mini_case_dir / "case.json",
        output_dir=mini_case_dir / "output",
        runtime_dir=mini_case_dir / "runtime",
    )


def _reach_chdd_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Довести пайплайн до экономической стадии без OPM и без калькулятора."""

    monkeypatch.setattr(
        validation_module,
        "run_simulation",
        lambda case, workspace: (_passing_opm_result(tmp_path), object()),
    )
    monkeypatch.setattr(
        validation_module, "build_chdd_input", lambda summary, case: _stub_chdd_input()
    )
    monkeypatch.setattr(validation_module, "write_chdd_csv", lambda chdd_input, path: Path(path))


def test_invalidate_package_removes_only_the_final_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    seed_previous_success(output_dir)
    bystander = output_dir / "notes.txt"
    bystander.write_text("keep me\n", encoding="utf-8")

    removed = invalidate_package(output_dir)

    assert set(removed) == set(FINAL_ARTIFACTS)
    assert bystander.is_file()
    for name in FINAL_ARTIFACTS:
        assert not (output_dir / name).exists()


def test_summary_read_error_invalidates_a_previous_success(
    mini_case_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_previous_success(mini_case_dir / "output")

    def explode(case, workspace):
        raise SummaryReadError("cannot read summary file MODEL_Y.UNSMRY: truncated")

    monkeypatch.setattr(validation_module, "run_simulation", explode)

    outcome = _run(mini_case_dir)

    assert outcome.verdict == VERDICT_BLOCKED
    report = assert_no_surviving_success(outcome.output_dir)
    assert any("truncated" in blocker for blocker in report["blockers"])
    assert set(report["invalidated_previous_artifacts"]) == set(FINAL_ARTIFACTS)


def test_chdd_execution_failure_invalidates_a_previous_success(
    mini_case_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed_previous_success(mini_case_dir / "output")
    _reach_chdd_stage(monkeypatch, tmp_path)

    def explode(case, chdd_input, *, source_file):
        raise ChddError("official CHDD calculator failed: boom")

    monkeypatch.setattr(validation_module, "run_chdd", explode)

    outcome = _run(mini_case_dir)

    assert outcome.verdict == VERDICT_BLOCKED
    report = assert_no_surviving_success(outcome.output_dir)
    assert any("boom" in blocker for blocker in report["blockers"])


def test_unexpected_chdd_exception_still_fails_closed(
    mini_case_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed_previous_success(mini_case_dir / "output")
    _reach_chdd_stage(monkeypatch, tmp_path)

    def explode(case, chdd_input, *, source_file):
        raise KeyError("totalChddM")

    monkeypatch.setattr(validation_module, "run_chdd", explode)

    outcome = _run(mini_case_dir)

    assert outcome.verdict == VERDICT_BLOCKED
    report = assert_no_surviving_success(outcome.output_dir)
    assert any("unhandled KeyError" in blocker for blocker in report["blockers"])


def test_unexpected_exception_in_an_early_stage_fails_closed(
    mini_case_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_previous_success(mini_case_dir / "output")

    def explode(case, *, policy_id):
        raise TypeError("policy returned something unexpected")

    monkeypatch.setattr(validation_module, "build_schedule_for_case", explode)

    outcome = _run(mini_case_dir)

    assert outcome.verdict == VERDICT_BLOCKED
    report = assert_no_surviving_success(outcome.output_dir)
    assert any("unhandled TypeError" in blocker for blocker in report["blockers"])


def test_keyboard_interrupt_is_not_swallowed(
    mini_case_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_previous_success(mini_case_dir / "output")

    def interrupt(case, *, policy_id):
        raise KeyboardInterrupt

    monkeypatch.setattr(validation_module, "build_schedule_for_case", interrupt)

    with pytest.raises(KeyboardInterrupt):
        _run(mini_case_dir)

    # Прерывание -- не результат, но пакет прошлого прогона уже снят: он не
    # может быть выдан за итог начатого расчёта.
    assert not (mini_case_dir / "output" / CLAIMED_NPV_FILENAME).exists()
    assert not (mini_case_dir / "output" / SCHEDULE_FILENAME).exists()


def test_workspace_files_are_copies_not_hardlinks(mini_case, tmp_path: Path) -> None:
    workspace = prepare_workspace(
        mini_case,
        root=tmp_path / "workspace",
        history_text="-- history\n",
        schedule_text="-- schedule\n",
    )

    source = mini_case.history.deck_root / "INCLUDE" / "PHYSICS.INC"
    mirrored = workspace.root / "INCLUDE" / "PHYSICS.INC"

    assert mirrored.is_file()
    assert mirrored.read_text("utf-8") == source.read_text("utf-8")
    assert mirrored.stat().st_ino != source.stat().st_ino
    assert source.stat().st_nlink == 1
    assert mirrored.stat().st_nlink == 1

    # Запись в рабочую копию не может задеть исходную колоду.
    mirrored.write_text("-- overwritten in the workspace\n", encoding="utf-8")
    assert source.read_text("utf-8") == "-- synthetic physics placeholder\n"
