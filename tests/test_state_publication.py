"""Real SummaryData extraction and StateCard publication."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from aios.cases import Case
from aios.chdd.runner import AnnualChddRow, ChddResult
from aios.chdd.trajectory import ChddInput, TrajectoryStats
from aios.opm.runner import OpmRunResult
from aios.opm.summary import SummaryData
from aios.pipeline import run_validation_pipeline
from aios.pipeline import validation as validation_module
from aios.state import (
    StateCard,
    StateExtractor,
    WellControlSnapshot,
    WellRole,
    publish_state_card,
)


def _model_time(year: int, month: int, day: int) -> datetime.datetime:
    return datetime.datetime(year, month, day)  # noqa: DTZ001


def _multi_well_summary(*, omit: tuple[str, str] | None = None) -> SummaryData:
    field_index = {
        "FPR": 0,
        "FOPR": 1,
        "FWPR": 2,
        "FLPR": 3,
        "FWIR": 4,
        "FVPR": 5,
        "FVPT": 6,
    }
    vectors = ("WBHP", "WLPR", "WOPR", "WWPR", "WWIR", "WWCT", "WOPT", "WWPT", "WWIT")
    wells = ("P1", "P2", "I1")
    well_index: dict[tuple[str, str], int] = {}
    column = len(field_index)
    for well in wells:
        for vector in vectors:
            if omit != (vector, well):
                well_index[(vector, well)] = column
            column += 1

    def row(
        *,
        fpr: float,
        fopr: float,
        fwpr: float,
        flpr: float,
        fwir: float,
        fvpr: float,
        fvpt: float,
        values: dict[tuple[str, str], float],
    ) -> tuple[float, ...]:
        data: list[float] = [fpr, fopr, fwpr, flpr, fwir, fvpr, fvpt]
        for well in wells:
            for vector in vectors:
                data.append(values[(vector, well)])
        return tuple(data)

    previous_values = {
        ("WBHP", "P1"): 58.0,
        ("WLPR", "P1"): 120.0,
        ("WOPR", "P1"): 90.0,
        ("WWPR", "P1"): 30.0,
        ("WWIR", "P1"): 0.0,
        ("WWCT", "P1"): 0.25,
        ("WOPT", "P1"): 900.0,
        ("WWPT", "P1"): 300.0,
        ("WWIT", "P1"): 0.0,
        ("WBHP", "P2"): 54.0,
        ("WLPR", "P2"): 90.0,
        ("WOPR", "P2"): 60.0,
        ("WWPR", "P2"): 30.0,
        ("WWIR", "P2"): 0.0,
        ("WWCT", "P2"): 0.333,
        ("WOPT", "P2"): 500.0,
        ("WWPT", "P2"): 250.0,
        ("WWIT", "P2"): 0.0,
        ("WBHP", "I1"): 270.0,
        ("WLPR", "I1"): 0.0,
        ("WOPR", "I1"): 0.0,
        ("WWPR", "I1"): 0.0,
        ("WWIR", "I1"): 180.0,
        ("WWCT", "I1"): 0.0,
        ("WOPT", "I1"): 0.0,
        ("WWPT", "I1"): 0.0,
        ("WWIT", "I1"): 500.0,
    }
    current_values = {
        **previous_values,
        ("WBHP", "P1"): 54.0,
        ("WLPR", "P1"): 155.0,
        ("WOPR", "P1"): 120.0,
        ("WWPR", "P1"): 35.0,
        ("WWIR", "P1"): 0.0,
        ("WWCT", "P1"): 0.2258,
        ("WOPT", "P1"): 1000.0,
        ("WWPT", "P1"): 400.0,
        ("WBHP", "P2"): 49.0,
        ("WLPR", "P2"): 520.0,
        ("WOPR", "P2"): 250.0,
        ("WWPR", "P2"): 270.0,
        ("WWIR", "P2"): 0.0,
        ("WWCT", "P2"): 0.5192,
        ("WOPT", "P2"): 600.0,
        ("WWPT", "P2"): 350.0,
        ("WBHP", "I1"): 299.0,
        ("WWIR", "I1"): 220.0,
        ("WWIT", "I1"): 700.0,
    }
    return SummaryData(
        start=_model_time(2020, 1, 1),
        dates=(_model_time(2020, 3, 1), _model_time(2020, 4, 1)),
        wells=wells,
        units={},
        _field_index=field_index,
        _well_index=well_index,
        _params=(
            row(
                fpr=200.0,
                fopr=150.0,
                fwpr=60.0,
                flpr=210.0,
                fwir=180.0,
                fvpr=225.0,
                fvpt=1400.0,
                values=previous_values,
            ),
            row(
                fpr=211.5,
                fopr=370.0,
                fwpr=305.0,
                flpr=675.0,
                fwir=220.0,
                fvpr=710.0,
                fvpt=2350.0,
                values=current_values,
            ),
        ),
    )


def _controls() -> dict[str, WellControlSnapshot]:
    return {
        "P1": WellControlSnapshot(
            role=WellRole.PRODUCER,
            status="OPEN",
            current_target=155.0,
            control_mode="LRAT",
        ),
        "P2": WellControlSnapshot(
            role=WellRole.PRODUCER,
            status="OPEN",
            current_target=520.0,
            control_mode="LRAT",
        ),
        "I1": WellControlSnapshot(
            role=WellRole.INJECTOR,
            status="OPEN",
            current_target=220.0,
            control_mode="RATE",
        ),
    }


def _extract(mini_case: Case, summary: SummaryData | None = None) -> StateCard:
    return StateExtractor().extract(
        summary=summary or _multi_well_summary(),
        case=mini_case,
        run_id="run-001",
        simulator_digest="example/flow@sha256:" + "0" * 64,
        schedule_hash="a" * 64,
        well_controls=_controls(),
    )


def test_summary_data_extracts_multiple_wells_roles_and_cumulatives(mini_case: Case) -> None:
    card = _extract(mini_case)

    by_well = {well.well_id: well for well in card.wells}
    assert tuple(by_well) == ("P1", "P2", "I1")
    assert by_well["P1"].role is WellRole.PRODUCER
    assert by_well["I1"].role is WellRole.INJECTOR
    assert card.field.oil_rate == pytest.approx(370.0)
    assert card.field.water_rate == pytest.approx(305.0)
    assert card.field.liquid_rate == pytest.approx(675.0)
    assert card.field.water_injection_rate == pytest.approx(220.0)
    assert card.field.voidage_production_rate == pytest.approx(710.0)
    assert card.field.voidage_production_total == pytest.approx(2350.0)
    assert card.field.oil_total == pytest.approx(1600.0)
    assert card.field.water_total == pytest.approx(750.0)
    assert card.field.water_injection_total == pytest.approx(700.0)
    assert by_well["P2"].wct == pytest.approx(0.5192)
    assert by_well["I1"].wwir == pytest.approx(220.0)
    assert by_well["P2"].producer_bhp_margin == pytest.approx(-1.0)
    assert by_well["P2"].wlpr_margin == pytest.approx(-20.0)
    assert by_well["I1"].injector_bhp_margin == pytest.approx(1.0)
    assert card.constraints.margins["producer_bhp_min:P2"] == pytest.approx(-1.0)
    assert card.constraints.margins["wlpr_max:P2"] == pytest.approx(-20.0)
    assert "producer_bhp_min:P2" in card.constraints.violations
    assert "wlpr_max:P2" in card.constraints.violations


def test_missing_required_well_vector_fails_closed(mini_case: Case) -> None:
    with pytest.raises(ValueError, match="well I1 vector WWIR unavailable"):
        _extract(mini_case, _multi_well_summary(omit=("WWIR", "I1")))


def test_missing_required_field_vector_fails_closed(mini_case: Case) -> None:
    summary = _multi_well_summary()
    summary = SummaryData(
        start=summary.start,
        dates=summary.dates,
        wells=summary.wells,
        units=summary.units,
        _field_index={key: value for key, value in summary._field_index.items() if key != "FVPR"},
        _well_index=summary._well_index,
        _params=summary._params,
    )

    with pytest.raises(ValueError, match="field vector FVPR unavailable"):
        _extract(mini_case, summary)


def test_state_card_publication_is_deterministic(tmp_path: Path, mini_case: Case) -> None:
    card = _extract(mini_case)
    first = publish_state_card(
        card,
        output_dir=tmp_path,
        source_artifacts=("MINI.SMSPEC", "MINI.UNSMRY"),
    )
    first_bytes = first.read_bytes()
    second = publish_state_card(
        card,
        output_dir=tmp_path,
        source_artifacts=("MINI.SMSPEC", "MINI.UNSMRY"),
    )

    assert first == tmp_path / "artifacts" / "state" / "state_card_2020_04_01.json"
    assert first.read_bytes() == first_bytes == second.read_bytes()
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["canonical_digest"] == card.digest()
    assert payload["provenance"]["case_id"] == "mini"
    assert payload["provenance"]["run_id"] == "run-001"
    assert payload["provenance"]["source_summary_identity"] == card.metadata.source_summary_identity
    assert payload["provenance"]["extractor_version"] == card.metadata.extractor_version
    assert payload["provenance"]["source_artifacts"] == ["MINI.SMSPEC", "MINI.UNSMRY"]
    assert StateCard.from_json(payload["state_card"]) == card


def _opm_result(tmp_path: Path) -> OpmRunResult:
    log = tmp_path / "flow.log"
    log.write_text("stub\n", encoding="utf-8")
    return OpmRunResult(
        exit_code=0,
        wall_seconds=1.0,
        command=("docker", "run", "stub"),
        log_path=log,
        output_files=("MINI.SMSPEC", "MINI.UNSMRY"),
        report_steps=2,
        final_date="2020-04-01",
        warnings=(),
        warning_count=0,
        errors=(),
    )


def _chdd_input() -> ChddInput:
    return ChddInput(
        headers=("DATA", "well"),
        rows=(("2020-04-01", "P1"),),
        stats=TrajectoryStats(
            row_count=1,
            well_count=1,
            interval_count=1,
            date_min="2020-04-01",
            date_max="2020-04-01",
            economic_rows=1,
            pre_start_rows=0,
            max_wlpr=120.0,
            rows_over_wlpr_limit=0,
            negative_diff_counts={"WLPT_Diff": 0, "WOMT_Diff": 0, "WWIT_Diff": 0},
        ),
    )


def _chdd_result() -> ChddResult:
    return ChddResult(
        calculator_version="stub",
        provenance="TEST",
        npv_mln_rub=10.0,
        profitability_index=1.0,
        economic_month_count=1,
        months_by_year={"2020": 1},
        yearly_profile=(
            AnnualChddRow(
                year=2020,
                npv_mln_rub=10.0,
                cumulative_mln_rub=10.0,
                months=1,
                active_well_months=1,
                stop_events=0,
                start_events=0,
            ),
        ),
        start_date="2020-04-01",
        last_economic_month="2020-04-01",
        source_max_date="2020-04-01",
        oil_kt_total=1.0,
        liquid_kt_total=1.0,
        injection_k_total=1.0,
        active_well_months=1,
        pump_events=0,
        stop_events=0,
        start_events=0,
        conversion_events=0,
        excluded_negative_rows=0,
        duplicate_well_months=0,
        invalid_dates=0,
        ignored_blank_wells=0,
        warnings=(),
        raw={},
    )


def test_validation_pipeline_publishes_state_card_without_running_opm(
    mini_case_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        validation_module,
        "run_simulation",
        lambda case, workspace: (_opm_result(tmp_path), _multi_well_summary()),
    )
    monkeypatch.setattr(validation_module, "build_chdd_input", lambda summary, case: _chdd_input())
    monkeypatch.setattr(validation_module, "write_chdd_csv", lambda chdd_input, path: Path(path))
    monkeypatch.setattr(
        validation_module, "run_chdd", lambda case, chdd_input, *, source_file: _chdd_result()
    )

    outcome = run_validation_pipeline(
        mini_case_dir / "case.json",
        output_dir=mini_case_dir / "output",
        runtime_dir=mini_case_dir / "runtime",
    )

    state_path = mini_case_dir / "output" / "artifacts" / "state" / "state_card_2020_04_01.json"
    assert outcome.verdict == "READY"
    assert state_path.is_file()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["state_card"]["metadata"]["case_id"] == "mini"
    assert payload["state_card"]["metadata"]["date"] == "2020-04-01"
    assert payload["provenance"]["source_artifacts"] == ["MINI.SMSPEC", "MINI.UNSMRY"]
