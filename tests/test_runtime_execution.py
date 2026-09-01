"""RUNTIME-EXECUTION-001: режимы исполнения, restart-цепочка, reconciliation."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

import aios.execution.execution as execution_module
from aios.execution.execution import (
    DEFAULT_RECONCILIATION_TOLERANCE,
    FullOpmRun,
    ReconciliationVerdict,
    RestartExecutionError,
    normalized_summary_document,
    reconcile_documents,
    reconcile_summaries,
    serialize_trajectory_document,
)
from aios.opm.runner import OpmRunResult
from tests.support.runtime_fixtures import synthetic_summary_data


class TestNormalizedDocument:
    def test_physical_only_document(self) -> None:
        document = normalized_summary_document(synthetic_summary_data())
        assert document["format"] == "aios.execution.opm_summary"
        assert document["physical_only"] is True
        text = serialize_trajectory_document(document).decode("utf-8")
        for forbidden in ("npv", "chdd", "NPV", "ЧДД"):
            assert forbidden not in text
        assert len(document["dates"]) == 3
        field_vectors = document["field_vectors"]
        assert {"FOPR", "FWPR", "FWIR"} <= set(field_vectors)
        well_vectors = document["well_vectors"]
        assert set(well_vectors["WBHP"]) == {"P1", "P2", "I1"}
        assert set(well_vectors["WLPR"]) == {"P1", "P2"}

    def test_serialization_is_deterministic(self) -> None:
        summary = synthetic_summary_data()
        first = serialize_trajectory_document(normalized_summary_document(summary))
        second = serialize_trajectory_document(normalized_summary_document(summary))
        assert first == second


class TestReconciliation:
    def test_identical_runs_pass(self) -> None:
        report = reconcile_summaries(synthetic_summary_data(), synthetic_summary_data())
        assert report.verdict is ReconciliationVerdict.PASS
        assert report.max_abs_deviation == 0.0
        assert report.compared_values > 0
        assert report.missing_overlap == ()

    def test_small_noise_within_tolerance_passes(self) -> None:
        noisy = synthetic_summary_data(
            perturbation={("FOPR", "", 1): DEFAULT_RECONCILIATION_TOLERANCE / 2}
        )
        report = reconcile_summaries(synthetic_summary_data(), noisy)
        assert report.verdict is ReconciliationVerdict.PASS

    def test_deviation_beyond_tolerance_fails(self) -> None:
        drifted = synthetic_summary_data(perturbation={("WBHP", "I1", 2): 0.5})
        report = reconcile_summaries(synthetic_summary_data(), drifted)
        assert report.verdict is ReconciliationVerdict.FAIL
        assert report.worst_vector == "well:WBHP:I1"
        assert report.max_abs_deviation == pytest.approx(0.5)

    def test_missing_vector_fails_loudly(self) -> None:
        full = normalized_summary_document(synthetic_summary_data())
        continuation = normalized_summary_document(synthetic_summary_data())
        del continuation["well_vectors"]["WLPR"]
        report = reconcile_documents(full, continuation)
        assert report.verdict is ReconciliationVerdict.FAIL
        assert any(entry.startswith("well:WLPR:") for entry in report.missing_overlap)

    def test_disjoint_dates_fail(self) -> None:
        full = normalized_summary_document(synthetic_summary_data())
        continuation = normalized_summary_document(synthetic_summary_data())
        continuation["dates"] = ["2021-01-01", "2021-02-01", "2021-03-01"]
        report = reconcile_documents(full, continuation)
        assert report.verdict is ReconciliationVerdict.FAIL
        assert "dates:no-overlap" in report.missing_overlap

    def test_report_json_round_trip(self) -> None:
        report = reconcile_summaries(synthetic_summary_data(), synthetic_summary_data())
        payload = report.to_json()
        assert payload["verdict"] == "PASS"
        assert payload["tolerance"] == DEFAULT_RECONCILIATION_TOLERANCE
        assert isinstance(payload["deviations_by_vector"], dict)


def _stub_run_result(*, exit_code: int = 1) -> OpmRunResult:
    return OpmRunResult(
        exit_code=exit_code,
        wall_seconds=0.1,
        command=("docker", "run"),
        log_path=Path("flow.log"),
        output_files=(),
        report_steps=3,
        final_date="2020-04-01",
        warnings=(),
        warning_count=0,
        errors=("synthetic failure",),
    )


class TestRestartChainWiring:
    # enable_restart_output требует хотя бы одно ключевое слово в тексте.
    MINIMAL_SCHEDULE_TEXT = "DATES\n 01 'FEB' 2020 /\n/\n"

    def test_failed_source_run_is_typed_error(self, monkeypatch, tmp_path, mini_case) -> None:
        def failing_run(*args, **kwargs):
            return FullOpmRun(workspace=None, run_result=_stub_run_result(), summary=None)

        monkeypatch.setattr(execution_module, "run_full_opm", failing_run)
        with pytest.raises(RestartExecutionError, match="restart source run failed"):
            execution_module.run_restart_chain(
                mini_case,
                root=tmp_path,
                history_text="",
                full_schedule_text=self.MINIMAL_SCHEDULE_TEXT,
                restart_date=datetime.date(2020, 3, 1),
            )

    def test_restart_error_wraps_typed(self, monkeypatch, tmp_path, mini_case) -> None:
        monkeypatch.setattr(
            execution_module,
            "run_full_opm",
            lambda *args, **kwargs: FullOpmRun(
                workspace=None, run_result=_stub_run_result(), summary=None
            ),
        )
        with pytest.raises(RestartExecutionError):
            execution_module.run_restart_chain(
                mini_case,
                root=tmp_path,
                history_text="",
                full_schedule_text=self.MINIMAL_SCHEDULE_TEXT,
                restart_date=datetime.date(2020, 3, 1),
            )
